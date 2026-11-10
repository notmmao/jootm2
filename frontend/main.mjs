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
import { getLib } from './image/Images.mjs';
import { sound } from './audio/SoundManager.mjs';
import { fitStage } from './core/viewport.mjs';

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
    // 先加载资源索引（图片/地图/音频 URL）
    await loadResources();

    const conn = new Connection('/ws');
    // 音频模块：'*' 通配监听 WS 消息 + 按钮点击 + 首次手势解锁（服务端零改动）
    sound.install(conn);
    try {
        await conn.connect();
    } catch (e) {
        console.error('[init] WebSocket 连接失败', e);
        const el = document.createElement('div');
        el.textContent = '无法连接服务器';
        el.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;'
            + 'color:#ff6b5e;font-size:16px;font-family:SimSun,serif;';
        guiEl.appendChild(el);
        return;
    }
    console.log('[init] WebSocket 已连接');

    const sceneManager = new SceneManager(conn, backCtx, winManager);
    sceneManager.switchTo(new LoginScene(sceneManager));

    const loop = new Loop(() => {
        sceneManager.update(performance.now());
        sceneManager.render(backCtx);
        // 一次 blit = 一帧，浏览器永远只看到完整画面
        ctx.drawImage(backCanvas, 0, 0);
    });
    loop.start();

    // 调试接口
    window.__conn = conn;
    window.__sceneManager = sceneManager;
    window.__winManager = winManager;
    window.__scheduler = scheduler;
    window.__getLib = getLib;
    window.__sound = sound;   // 调试：__sound.play(103) / status() / toggle()
}

init().catch(e => console.error('[init]', e));
