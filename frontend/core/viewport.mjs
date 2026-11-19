// 界面等比缩放：#stage 内部永远是 800×600 逻辑坐标，外层用 CSS transform 整体放大/缩小。
// 所有鼠标事件必须经这里换算，否则缩放后点击、移动方向、窗口拖拽都会偏移。
import { SCREEN_WIDTH, SCREEN_HEIGHT } from './config.mjs';

/** 当前缩放系数（读 #stage 的真实布局尺寸，不依赖缓存，resize 后立即生效）。 */
export function stageScale() {
    const stage = document.getElementById('stage');
    if (!stage) return 1;
    const r = stage.getBoundingClientRect();
    return r.width > 0 ? r.width / SCREEN_WIDTH : 1;
}

/** 浏览器事件坐标 → 相对 el 的 800×600 逻辑坐标。 */
export function toLocal(clientX, clientY, el) {
    const r = (el || document.getElementById('stage')).getBoundingClientRect();
    const k = stageScale();
    return { x: (clientX - r.left) / k, y: (clientY - r.top) / k };
}

/** 相对 el 的逻辑坐标 → 浏览器事件坐标（elementFromPoint 用）。 */
export function toClient(x, y, el) {
    const r = (el || document.getElementById('stage')).getBoundingClientRect();
    const k = stageScale();
    return { x: r.left + x * k, y: r.top + y * k };
}

/**
 * 按窗口尺寸等比适配：scale = min(vw/800, vh/600)。
 * 可用 localStorage.setItem('jootm2.uiScale', '1.5') 固定倍率，或 window.__setUiScale(k)。
 * 返回本次实际应用的倍率。
 */
const fitListeners = [];
let lastFit = null;

/**
 * 订阅舞台尺寸变化：fn({ k, dpr, devW, devH })，devW/devH 为舞台在屏幕上的物理像素尺寸
 * （present.mjs 据此设可见 canvas 位图）。订阅时若已适配过会立即回调一次。
 */
export function onStageFit(fn) {
    fitListeners.push(fn);
    if (lastFit) fn(lastFit);
}

export function fitStage() {
    const stage = document.getElementById('stage');
    if (!stage) return 1;
    const dpr = window.devicePixelRatio || 1;
    const saved = Number(localStorage.getItem('jootm2.uiScale'));
    const auto = Math.min(window.innerWidth / SCREEN_WIDTH,
        window.innerHeight / SCREEN_HEIGHT);
    let k = saved > 0 ? saved : Math.max(0.5, Math.min(4, auto));
    // 对齐物理像素（清晰度关键）：
    //  1) 让 800·k·dpr 落在整数上——可见 canvas 位图与屏幕像素 1:1，浏览器不再二次插值；
    //  2) 左上角落在整数物理像素上——旧版 flex 居中 + transform-origin:center 常把
    //     整个舞台放在 .5 像素处，GPU 合成时整层（含 DOM 文字）被半像素重采样。
    // 自动倍率向下取整，保证不超出窗口。
    // 宽取 4 的倍数 → 4:3 的高（devW·3/4）也必为整数。
    const q = SCREEN_WIDTH * k * dpr / 4;
    const devW = 4 * Math.max(1, saved > 0 ? Math.round(q) : Math.floor(q + 1e-6));
    k = devW / (SCREEN_WIDTH * dpr);
    const devH = Math.round(SCREEN_HEIGHT * k * dpr);
    const left = Math.round((window.innerWidth * dpr - devW) / 2) / dpr;
    const top = Math.round((window.innerHeight * dpr - devH) / 2) / dpr;
    stage.style.position = 'fixed';
    stage.style.left = left + 'px';
    stage.style.top = top + 'px';
    stage.style.transformOrigin = '0 0';
    stage.style.transform = `scale(${k})`;
    lastFit = { k, dpr, devW, devH };
    for (const fn of fitListeners) {
        try { fn(lastFit); } catch (e) { console.warn('[viewport] fit listener', e); }
    }
    return k;
}

// 跨显示器拖动 / 浏览器缩放（Ctrl ±）会改 devicePixelRatio；部分浏览器此时不发 resize。
function watchDpr() {
    if (!window.matchMedia) return;
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    const onChange = () => { fitStage(); watchDpr(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange, { once: true });
}
if (typeof window !== 'undefined') watchDpr();

/**
 * 设置界面缩放预设。k='auto' 清除固定倍率（回到随窗口等比）；
 * k 为数字则固定该倍率（1 = 完全不缩放，800×600 原尺寸）。
 * 写 localStorage 后立即重排并返回实际倍率。
 */
export function setUiScale(k) {
    if (k === 'auto' || k == null) localStorage.removeItem('jootm2.uiScale');
    else localStorage.setItem('jootm2.uiScale', String(k));
    return fitStage();
}

/** 当前缩放预设：'auto' 或数字倍率。 */
export function getUiScalePreset() {
    const saved = Number(localStorage.getItem('jootm2.uiScale'));
    return saved > 0 ? saved : 'auto';
}

/** 当前「物理像素 / 逻辑像素」倍率（= 舞台缩放 k × devicePixelRatio，已对齐整数像素）。 */
export function deviceScale() {
    if (lastFit) return lastFit.devW / SCREEN_WIDTH;
    return (typeof window !== 'undefined' && window.devicePixelRatio) || 1;
}

/**
 * 让 #stage 内的 DOM canvas 按物理像素分辨率绘制（大地图/小地图等）：
 * 位图设为 cssW·s × cssH·s（CSS 尺寸仍是逻辑 cssW×cssH），返回 s。
 * 调用方随后 ctx.setTransform(s,0,0,s,0,0)，绘制代码继续用逻辑坐标。
 * 尺寸不变时不重设位图（重设会清空画布并重置上下文状态）。
 */
export function fitCanvasToDevice(cv, cssW, cssH) {
    const s = deviceScale();
    const w = Math.max(1, Math.round(cssW * s));
    const h = Math.max(1, Math.round(cssH * s));
    if (cv.width !== w) cv.width = w;
    if (cv.height !== h) cv.height = h;
    return w / cssW;
}
