/**
 * 黑暗视野（DrawFog）的运行时配置。
 *
 * 背景：原版 cliUtil.pas 的 DarkLevel 分级在浏览器 canvas 上用「整屏黑纱 +
 * lig0 光源贴图挖洞」近似。原版 alpha（尤其地下 0.86）在浏览器里会把暗区
 * 细节几乎全压没——实测游戏画面 70% 像素亮度 < 60/255，观感发糊/廉价。
 * 这里把三档的 alpha/radius 暴露成可调参数，让玩家自己找舒服的亮度。
 *
 * 存储：localStorage['jootm2.fog']（JSON）。读失败一律回退 DEFAULT_FOG。
 * 每帧读取的是模块缓存对象，拖动滑块只改缓存 + 落盘，无逐帧 JSON 解析。
 */

const KEY = 'jootm2.fog';
const LEVELS = [1, 2, 3];

/** 原版基准值（MapActor.mjs 历史常量），面板「恢复原版」用，便于 A/B 对比。 */
export const ORIGINAL_FOG = Object.freeze({
    1: Object.freeze({ alpha: 0.86, radius: 132 }),  // 深夜 / 地下：HeavyDark
    2: Object.freeze({ alpha: 0.56, radius: 190 }),  // 傍晚：LightDark
    3: Object.freeze({ alpha: 0.26, radius: 250 }),  // 正午微暗：Dengun
});

/**
 * 推荐默认：降低黑纱、放大兜底光圈，让暗区保留层次，仍维持洞穴黑暗感。
 * 数值经真实渲染 A/B 选定（地下可见地面区平均亮度 50.9→63.4，石纹更清晰且未过曝）；
 * 玩家可在游戏内系统菜单/ctrl+F7 继续微调，找到自己舒服的值后告诉我固化。
 */
export const DEFAULT_FOG = Object.freeze({
    1: Object.freeze({ alpha: 0.45, radius: 200 }),
    2: Object.freeze({ alpha: 0.32, radius: 230 }),
    3: Object.freeze({ alpha: 0.15, radius: 280 }),
});

const RANGE = {
    alpha: [0, 0.95],
    radius: [60, 360],
};

let cache = null;

function clamp(v, lo, hi, fallback) {
    return Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : fallback;
}

function sanitize(raw) {
    const out = {};
    for (const lv of LEVELS) {
        const src = raw && raw[lv];
        const base = DEFAULT_FOG[lv];
        out[lv] = {
            alpha: clamp(src && Number(src.alpha), RANGE.alpha[0], RANGE.alpha[1], base.alpha),
            radius: clamp(src && Number(src.radius), RANGE.radius[0], RANGE.radius[1], base.radius),
        };
    }
    return out;
}

/** 当前配置（模块缓存，每帧调用无额外开销）。 */
export function getFogLevels() {
    if (cache) return cache;
    let raw = null;
    try {
        const s = localStorage.getItem(KEY);
        if (s) raw = JSON.parse(s);
    } catch { /* 隐私模式 / 坏 JSON：回退默认 */ }
    cache = sanitize(raw);
    return cache;
}

/** 取单档配置，供 MapActor._drawFog 每帧调用。 */
export function getFogLevel(lv) {
    const all = getFogLevels();
    return all[lv] || DEFAULT_FOG[lv] || DEFAULT_FOG[1];
}

/** 改单个参数（alpha 或 radius），即时生效 + 落盘。 */
export function setFogLevel(lv, key, value) {
    const cur = getFogLevels();
    const next = {};
    for (const k of LEVELS) next[k] = { ...cur[k] };
    if (!next[lv] || !(key in RANGE)) return cur;
    const [lo, hi] = RANGE[key];
    next[lv][key] = clamp(Number(value), lo, hi, cur[lv][key]);
    cache = sanitize(next);
    save();
    return cache;
}

/** 批量恢复：kind='original' 恢复原版值，其它恢复推荐默认。 */
export function resetFog(kind) {
    cache = sanitize(kind === 'original' ? ORIGINAL_FOG : DEFAULT_FOG);
    save();
    return cache;
}

/** 供诊断导出读当前生效值。 */
export function fogSnapshot() {
    return JSON.parse(JSON.stringify(getFogLevels()));
}

function save() {
    try { localStorage.setItem(KEY, JSON.stringify(cache)); } catch { /* 隐私模式 */ }
}
