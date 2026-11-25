// 入口（对应 mir2.dpr + ClMain.InitializeClient）
// 连接 WebSocket → 场景管理器 → 登录场景 → 选角 → 进游戏。

import { Connection } from './net/Connection.mjs';
import { SceneManager } from './scene/SceneManager.mjs';
import { LoginScene } from './ui/LoginScene.mjs';
import { Loop } from './core/loop.mjs';
import { SCREEN_WIDTH, SCREEN_HEIGHT } from './core/config.mjs';
import { DWinManager } from './gui/manager.mjs';
import { loadResources } from './gfx/resource.mjs';
import { scheduler } from './gfx/scheduler.mjs';
import { assetCache } from './gfx/assetCache.mjs';
import { opfsInit, opfsGetStats } from './gfx/opfsStore.mjs';
import { getLib } from './image/Images.mjs';
import { sound } from './audio/SoundManager.mjs';
import { fitStage, setUiScale, onStageFit } from './core/viewport.mjs';
import { Presenter } from './core/present.mjs';
import { boot, fetchAssetVersion, preloadEssentials, warmInBackground, warmState,
         warmMapBytes, getBootInfo } from './core/boot.mjs';
import { perf } from './core/perf.mjs';
import { installDiag } from './core/diag.mjs';
import { installFogTuneHotkey } from './ui/FogTunePanel.mjs';

// 整个 800×600 界面等比放大到当前窗口（4:3 居中，不改变内部逻辑坐标）。
// 想固定倍率：localStorage.setItem('jootm2.uiScale','1.5') 后刷新；
// 调试：window.__setUiScale(1.25) / __setUiScale('auto')。
fitStage();
window.addEventListener('resize', fitStage);
window.__setUiScale = (k) => setUiScale(k);

const canvas = document.getElementById('screen');
canvas.width = SCREEN_WIDTH;
canvas.height = SCREEN_HEIGHT;

// 离屏双缓冲：整帧先画在 back buffer 上，全部完成后一次性贴到可见 canvas。
// 直接在可见 canvas 上「清屏 → 逐块重绘」时，GPU 合成可能把中间态呈现出来，
// 表现为整屏发黑/抖动（尤其在每帧要画 200+ 瓦片 + 动画帧的游戏场景）。
// back buffer 保持 800×600 逻辑分辨率；可见 canvas 位图按物理像素设定，
// 由 Presenter 负责清晰放大 + 文字按物理分辨率重画（core/present.mjs）。
const backCanvas = document.createElement('canvas');
backCanvas.width = SCREEN_WIDTH;
backCanvas.height = SCREEN_HEIGHT;
const presenter = new Presenter(canvas, backCanvas);
const backCtx = presenter.backCtx;
onStageFit(({ devW, devH }) => presenter.resize(devW, devH));
window.__presenter = presenter;
// 调试/设置：__setScaleFilter('sharp'|'pixel'|'smooth')、__setHiText(true|false)
window.__setScaleFilter = (f) => presenter.setFilter(f);
window.__setHiText = (on) => presenter.setHiText(on);

// 全局屏蔽浏览器右键菜单（右键用于游戏跑动，任何界面包括 NPC 对话框都不弹菜单）
document.addEventListener('contextmenu', (e) => e.preventDefault());
// 全局禁用文本/元素框选与图片拖拽（按住移动时避免误选内容）
document.addEventListener('selectstart', (e) => e.preventDefault());
document.addEventListener('dragstart', (e) => e.preventDefault());

const guiEl = document.getElementById('gui');
const winManager = new DWinManager(guiEl);

// ---- 掉线处理 ----------------------------------------------------------
// 原版 CSocketDisconnect（ClMain.pas:3326）只是把 g_boServerConnected 置 FALSE，
// 界面就地停住不动（并且音频模块会切回登录 BGM）。浏览器端照搬会让玩家对着一个
// 「怪不动、按什么都没反应」的画面听着登录音乐——所以这里给明确提示 + 自动回登录界面，
// 顺带把重连也做掉（onReturnLogin 会新建 WebSocket）。
let dropping = false;
let netErrorEl = null;

function showNetError(text) {
    if (netErrorEl) { netErrorEl.firstChild.nextSibling.textContent = text; return; }
    const stage = document.getElementById('stage');
    if (!stage) return;
    const box = document.createElement('div');
    box.style.cssText = 'position:absolute;left:50%;top:46%;transform:translate(-50%,-50%);'
        + 'min-width:320px;padding:18px 22px;background:rgba(8,8,10,.92);'
        + 'border:1px solid #6b5a33;box-shadow:0 0 0 1px #000,0 6px 24px rgba(0,0,0,.6);'
        + 'color:#ffd76a;font-family:SimSun,serif;font-size:14px;line-height:22px;'
        + 'text-align:center;z-index:9999;pointer-events:none;';
    const title = document.createElement('div');
    title.textContent = '连接已断开';
    title.style.cssText = 'font-size:16px;margin-bottom:6px;';
    const body = document.createElement('div');
    body.textContent = text;
    body.style.color = '#e8d9b0';
    box.append(title, body);
    stage.appendChild(box);
    netErrorEl = box;
}

function hideNetError() {
    if (netErrorEl) { netErrorEl.remove(); netErrorEl = null; }
}

function watchDisconnect(c, sm) {
    const prev = c.onClose;
    c.onClose = () => {
        if (prev) prev();
        if (dropping || c._intentional) return;   // 主动 close（退出/回登录流程）不触发重连
        if (!sm.current) return;
        dropping = true;
        // 登录界面也会掉线（例如服务端重启）：连接已死，不重连的话用户
        // 输密码时 send 静默失败、界面无任何反馈。重连逻辑本身有失败重试
        // 上限（见 onReturnLogin），不会变成无限循环。
        const atLogin = sm.current instanceof LoginScene;
        showNetError(atLogin
            ? '与服务器断开连接，正在尝试重连…'
            : '与服务器断开连接，即将返回登录界面…');
        setTimeout(async () => {
            try { await sm.onReturnLogin(); }
            catch (e) {
                console.warn('[net] 回登录失败', e);
                showNetError('无法重新连接服务器，请刷新页面重试');
            } finally {
                dropping = false;
            }
        }, 1600);
    };
}

async function init() {
    boot.begin();
    // 1) 资源清单 + 素材版本指纹并行取；版本决定 IndexedDB 缓存命名空间
    boot.step('正在读取资源清单…');
    const [version] = await Promise.all([fetchAssetVersion(), loadResources()]);

    // 2) 打开持久缓存（版本变化时自动清空旧素材）。
    // OPFS 整文件缓存与 IndexedDB 分片缓存并行开；OPFS 限 1 秒，卡住不拖启动。
    boot.step('正在检查本地缓存…');
    await Promise.all([
        assetCache.init(version),
        Promise.race([
            opfsInit(version),
            new Promise(r => setTimeout(() => r(false), 1000)),
        ]),
    ]);

    // 2.5) 立刻并行开下出生地图的 .map 字节（不 await）：进游戏 loadMap()
    // 若现下这份文件，公网上常要等 6~14 秒。此时 Loading 还在，进度条可见。
    const start = getBootInfo().start;
    if (start) warmMapBytes(start.map);

    // 3) WebSocket 与音频索引并行就绪
    const conn = new Connection();   // 地址按 wsUrl.mjs 规则解析（CF 域名页→直连源站 IP）
    // 音频模块：'*' 通配监听 WS 消息 + 按钮点击 + 首次手势解锁（服务端零改动）
    sound.install(conn);
    try {
        await Promise.all([conn.connect(), sound.ready]);
    } catch (e) {
        console.error('[init] WebSocket 连接失败', e);
        boot.fail('无法连接服务器');
        return;
    }
    console.log('[init] WebSocket 已连接');

    // 4) 登录/选角首屏纹理预取（有进度条；命中缓存时几乎瞬间完成）
    await preloadEssentials((done, total, label) => boot.progress(label, done, total));
    await assetCache.flush();
    boot.finish();

    const sceneManager = new SceneManager(conn, backCtx, winManager);
    sceneManager.switchTo(new LoginScene(sceneManager));

    // 选角界面「退出」：断开旧连接（服务端随之销毁会话）→ 新建 WebSocket → 切回登录界面。
    // 原版 SelChrExitClick 是 FrmMain.Close 直接关客户端；浏览器不能关标签页，回登录最接近。
    sceneManager.onReturnLogin = async () => {
        try { conn.close(); } catch { /* 连接已断开 */ }
        // 服务端重启期间 TCP 直接拒接，要重试到它回来（约 6 次 × 3s ≈ 20s 窗口）。
        // 全部失败则抛给调用方提示刷新；绝不带着打不开的连接进登录界面，
        // 否则输密码 send 静默失败，又回到「无反应」的状态。
        let nc = null, lastErr = null;
        for (let i = 0; i < 6; i++) {
            nc = new Connection();
            try {
                await nc.connect();
                break;
            } catch (e) {
                lastErr = e;
                nc = null;
                await new Promise(r => setTimeout(r, 3000));
            }
        }
        if (!nc) throw lastErr || new Error('无法连接服务器');
        sound.attach(nc);               // 新连接重新挂消息钩子（全局手势/点击监听只装一次）
        sceneManager.bind(nc);          // 消息路由指向新连接
        watchDisconnect(nc, sceneManager);   // 新连接同样要监听掉线
        hideNetError();                 // 重连成功，撤掉「连接已断开」提示
        sceneManager.switchTo(new LoginScene(sceneManager));
        window.__conn = nc;
    };

    watchDisconnect(conn, sceneManager);

    // 诊断（Ctrl+F9 导出 / Ctrl+F10 局部刷新 / Ctrl+F11 深度刷新 / Ctrl+F8 性能 HUD）
    installDiag({ getScene: () => sceneManager.current });
    // 光照调参（Ctrl+F7 开关面板，实时改黑纱浓度/光圈）
    installFogTuneHotkey();

    const loop = new Loop(() => {
        perf.beginFrame();
        const a = performance.now();
        sceneManager.update(a);
        const b = performance.now();
        sceneManager.render(backCtx);
        const c = performance.now();
        // 一次呈现 = 一帧，浏览器永远只看到完整画面（放大 + 高清文字层）
        presenter.present();
        const d = performance.now();
        perf.add('update', b - a);
        perf.add('render', c - b);
        perf.add('blit', d - c);
        perf.endFrame();
    });
    loop.start();

    // 5) 登录界面空闲后低优先级预热：开门/选角纹理 + 全部 WIX + 出生地图字节
    warmInBackground();
    window.__warmDone = warmState.done;   // 调试：await __warmDone 等后台预取结束

    // 调试接口
    window.__conn = conn;
    window.__sceneManager = sceneManager;
    window.__winManager = winManager;
    window.__scheduler = scheduler;
    window.__getLib = getLib;
    window.__sound = sound;   // 调试：__sound.play(103) / status() / toggle()
    window.__assetCache = assetCache;   // 调试：__assetCache.stats() / await __assetCache.clear()
    window.__opfs = opfsGetStats;       // 调试：__opfs() OPFS 整文件缓存状态
    window.__perf = perf;               // 调试：__perf.frames / __perf.stats(['total','floor'])
}

init().catch(e => {
    console.error('[init]', e);
    boot.fail('初始化失败：' + (e && e.message ? e.message : String(e)));
});
