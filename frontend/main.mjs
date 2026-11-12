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
import { getLib } from './image/Images.mjs';
import { sound } from './audio/SoundManager.mjs';
import { fitStage } from './core/viewport.mjs';
import { boot, fetchAssetVersion, preloadEssentials, warmInBackground, warmState,
         warmMapBytes, getBootInfo } from './core/boot.mjs';
import { perf } from './core/perf.mjs';
import { installDiag } from './core/diag.mjs';

// 整个 800×600 界面等比放大到当前窗口（4:3 居中，不改变内部逻辑坐标）。
// 想固定倍率：localStorage.setItem('jootm2.uiScale','1.5') 后刷新；
// 调试：window.__setUiScale(1.25) / __setUiScale('auto')。
fitStage();
window.addEventListener('resize', fitStage);
window.__setUiScale = (k) => {
    if (k === 'auto') localStorage.removeItem('jootm2.uiScale');
    else localStorage.setItem('jootm2.uiScale', String(k));
    return fitStage();
};

const canvas = document.getElementById('screen');
canvas.width = SCREEN_WIDTH;
canvas.height = SCREEN_HEIGHT;
const ctx = canvas.getContext('2d');

// 离屏双缓冲：整帧先画在 back buffer 上，全部完成后一次性贴到可见 canvas。
// 直接在可见 canvas 上「清屏 → 逐块重绘」时，GPU 合成可能把中间态呈现出来，
// 表现为整屏发黑/抖动（尤其在每帧要画 200+ 瓦片 + 动画帧的游戏场景）。
const backCanvas = document.createElement('canvas');
backCanvas.width = SCREEN_WIDTH;
backCanvas.height = SCREEN_HEIGHT;
const backCtx = backCanvas.getContext('2d', { alpha: false });

// 全局屏蔽浏览器右键菜单（右键用于游戏跑动，任何界面包括 NPC 对话框都不弹菜单）
document.addEventListener('contextmenu', (e) => e.preventDefault());
// 全局禁用文本/元素框选与图片拖拽（按住移动时避免误选内容）
document.addEventListener('selectstart', (e) => e.preventDefault());
document.addEventListener('dragstart', (e) => e.preventDefault());

const guiEl = document.getElementById('gui');
const winManager = new DWinManager(guiEl);

async function init() {
    boot.begin();
    // 1) 资源清单 + 素材版本指纹并行取；版本决定 IndexedDB 缓存命名空间
    boot.step('正在读取资源清单…');
    const [version] = await Promise.all([fetchAssetVersion(), loadResources()]);

    // 2) 打开持久缓存（版本变化时自动清空旧素材）
    boot.step('正在检查本地缓存…');
    await assetCache.init(version);

    // 2.5) 立刻并行开下出生地图的 .map 字节（不 await）：进游戏 loadMap()
    // 若现下这份文件，公网上常要等 6~14 秒。此时 Loading 还在，进度条可见。
    const start = getBootInfo().start;
    if (start) warmMapBytes(start.map);

    // 3) WebSocket 与音频索引并行就绪
    const conn = new Connection('/ws');
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
        const nc = new Connection('/ws');
        sound.attach(nc);               // 新连接重新挂消息钩子（全局手势/点击监听只装一次）
        sceneManager.bind(nc);          // 消息路由指向新连接
        try {
            await nc.connect();
        } catch (e) {
            console.warn('[exit] 重连失败，刷新页面重试', e);
        }
        sceneManager.switchTo(new LoginScene(sceneManager));
        window.__conn = nc;
    };

    // 诊断（Ctrl+F9 导出 / Ctrl+F10 局部刷新 / Ctrl+F11 深度刷新 / Ctrl+F8 性能 HUD）
    installDiag({ getScene: () => sceneManager.current });

    const loop = new Loop(() => {
        perf.beginFrame();
        const a = performance.now();
        sceneManager.update(a);
        const b = performance.now();
        sceneManager.render(backCtx);
        const c = performance.now();
        // 一次 blit = 一帧，浏览器永远只看到完整画面
        ctx.drawImage(backCanvas, 0, 0);
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
    window.__perf = perf;               // 调试：__perf.frames / __perf.stats(['total','floor'])
}

init().catch(e => {
    console.error('[init]', e);
    boot.fail('初始化失败：' + (e && e.message ? e.message : String(e)));
});
