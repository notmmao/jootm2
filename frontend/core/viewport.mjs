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
export function fitStage() {
    const stage = document.getElementById('stage');
    if (!stage) return 1;
    const saved = Number(localStorage.getItem('jootm2.uiScale'));
    const auto = Math.min(window.innerWidth / SCREEN_WIDTH,
        window.innerHeight / SCREEN_HEIGHT);
    const k = saved > 0 ? saved : Math.max(0.5, Math.min(4, auto));
    stage.style.transform = `scale(${k})`;
    return k;
}

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
