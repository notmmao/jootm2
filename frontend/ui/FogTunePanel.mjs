/**
 * 游戏内光照调参面板（画面亮度）。
 * 挂在 body 上（不在 #stage 内），因此不受 stage 缩放影响，滑块始终是原生清晰渲染。
 *
 * 入口：系统菜单「光照调节」或 Ctrl+F7。
 * 三档 alpha/radius 各自拖动即时生效（MapActor 每帧读配置），自动写 localStorage。
 */

import { getFogLevels, getFogLevel, setFogLevel, resetFog, DEFAULT_FOG, ORIGINAL_FOG } from '../core/fogConfig.mjs';
import { setUiScale, getUiScalePreset, stageScale } from '../core/viewport.mjs';

const PANEL_ID = 'jootm2-fog-tune';
const LEVEL_NAMES = {
    1: '地下 / 深夜（最暗）',
    2: '傍晚 / 半暗',
    3: '正午 / 微暗',
};

let panelEl = null;
let tickTimer = null;

/** 面板是否打开 */
export function isFogTuneOpen() {
    return !!document.getElementById(PANEL_ID);
}

/** 打开 / 关闭（返回打开后的状态） */
export function toggleFogTunePanel() {
    if (isFogTuneOpen()) {
        closeFogTunePanel();
        return false;
    }
    openFogTunePanel();
    return true;
}

export function openFogTunePanel() {
    if (isFogTuneOpen()) return;
    const el = document.createElement('div');
    el.id = PANEL_ID;
    el.style.cssText = 'position:fixed;right:14px;top:14px;z-index:2147483000;'
        + 'width:322px;padding:10px 12px 12px;box-sizing:border-box;'
        + 'background:rgba(14,12,9,.94);border:1px solid #6b5636;border-radius:6px;'
        + 'color:#e8d9b0;font:12px/1.5 "Microsoft YaHei",SimSun,sans-serif;'
        + 'box-shadow:0 6px 26px rgba(0,0,0,.65);user-select:none;';

    const title = document.createElement('div');
    title.style.cssText = 'font-size:13px;color:#ffd76a;margin-bottom:2px;';
    title.textContent = '光照调节（画面亮度）';
    el.appendChild(title);

    const hint = document.createElement('div');
    hint.style.cssText = 'font-size:11px;color:#9fb08a;margin-bottom:8px;line-height:1.45;';
    hint.textContent = '拖动即时生效并自动保存。alpha=黑纱浓度（越小越亮）；radius=兜底光圈（仅光照贴图未就绪时生效）。';
    el.appendChild(hint);

    // ---- 界面缩放：自动 / 不缩放 / 固定倍率 ----
    const scaleBox = document.createElement('div');
    scaleBox.style.cssText = 'padding:6px 0 8px;margin-bottom:8px;border-bottom:1px solid #38301f;';
    const scaleTitle = document.createElement('div');
    scaleTitle.style.cssText = 'color:#ffe9a8;margin-bottom:4px;';
    scaleTitle.textContent = '界面缩放（当前实际 ' + stageScale().toFixed(2) + '×）';
    scaleBox.appendChild(scaleTitle);
    const scaleRow = document.createElement('div');
    scaleRow.style.cssText = 'display:flex;gap:4px;flex-wrap:wrap;';
    const SCALE_PRESETS = [
        ['auto', '自动'], [1, '不缩放 1×'], [1.25, '1.25×'], [1.5, '1.5×'], [2, '2×'],
    ];
    const scaleBtns = [];
    for (const [val, label] of SCALE_PRESETS) {
        const b = makeBtn(label, () => {
            setUiScale(val);
            refreshScaleBtns();
            scaleTitle.textContent = '界面缩放（当前实际 ' + stageScale().toFixed(2) + '×）';
        });
        b._val = val;
        b.style.flex = 'none';
        b.style.minWidth = '52px';
        b.style.padding = '4px 6px';
        scaleRow.appendChild(b);
        scaleBtns.push(b);
    }
    const refreshScaleBtns = () => {
        const preset = getUiScalePreset();
        for (const b of scaleBtns) {
            const active = String(b._val) === String(preset);
            b.style.background = active ? '#5a4826' : '#241d12';
            b.style.color = active ? '#8ef58e' : '#ffe9a8';
            b.style.borderColor = active ? '#9fe08a' : '#5c4d30';
        }
    };
    refreshScaleBtns();
    scaleBox.appendChild(scaleRow);
    const scaleNote = document.createElement('div');
    scaleNote.style.cssText = 'font-size:10px;color:#7a8a6a;margin-top:4px;line-height:1.4;';
    scaleNote.textContent = '不缩放 = 800×600 原始像素，画面最锐利但四周留黑边（窗口越大黑边越多）。';
    scaleBox.appendChild(scaleNote);
    el.appendChild(scaleBox);

    const current = document.createElement('div');
    current.style.cssText = 'font-size:11px;color:#8ef58e;margin-bottom:8px;';
    el.appendChild(current);

    const cfg = getFogLevels();
    for (const lv of [1, 2, 3]) {
        const row = document.createElement('div');
        row.style.cssText = 'margin-bottom:7px;padding-bottom:5px;border-bottom:1px solid #38301f;';
        const label = document.createElement('div');
        label.style.cssText = 'color:#ffe9a8;margin-bottom:3px;';
        label.textContent = `档位 ${lv} · ${LEVEL_NAMES[lv]}`;
        row.appendChild(label);
        row.appendChild(makeSlider(lv, 'alpha', '黑纱浓度', cfg[lv].alpha, 0, 0.95, 0.01,
            (v) => v.toFixed(2)));
        row.appendChild(makeSlider(lv, 'radius', '兜底光圈', cfg[lv].radius, 60, 360, 5,
            (v) => String(Math.round(v)) + 'px'));
        el.appendChild(row);
    }

    const btns = document.createElement('div');
    btns.style.cssText = 'display:flex;gap:6px;margin-top:4px;';
    btns.appendChild(makeBtn('推荐默认', () => { resetFog('default'); refreshAll(); }));
    btns.appendChild(makeBtn('原版数值', () => { resetFog('original'); refreshAll(); }));
    btns.appendChild(makeBtn('关闭 (Ctrl+F7)', () => closeFogTunePanel()));
    el.appendChild(btns);

    const note = document.createElement('div');
    note.style.cssText = 'font-size:10px;color:#7a8a6a;margin-top:6px;line-height:1.4;';
    note.textContent = `推荐默认 地下 alpha=${DEFAULT_FOG[1].alpha} / 原版 ${ORIGINAL_FOG[1].alpha}。找不到最佳值时可随时在两档间 A/B 对比。`;
    el.appendChild(note);

    // 面板内键盘事件不要冒泡到游戏（方向键调滑块会误触发移动）
    el.addEventListener('keydown', (e) => e.stopPropagation());
    el.addEventListener('mousedown', (e) => e.stopPropagation());
    document.body.appendChild(el);
    panelEl = el;

    refreshAll();
    tickTimer = setInterval(() => {
        if (!isFogTuneOpen()) { clearInterval(tickTimer); return; }
        const scene = window.__sceneManager && window.__sceneManager.current;
        const ma = scene && scene.mapActor;
        const lv = ma ? (ma.darkLevel | 0) : 0;
        current.textContent = ma
            ? `当前场景：档位 ${lv || 0}${lv ? ' · ' + LEVEL_NAMES[lv] : '（全亮，无黑暗）'}`
            : '当前不在游戏场景';
    }, 250);
}

export function closeFogTunePanel() {
    const el = document.getElementById(PANEL_ID);
    if (el) el.remove();
    panelEl = null;
    if (tickTimer) { clearInterval(tickTimer); tickTimer = null; }
}

/** 安装 Ctrl+F7 快捷键（捕获阶段，避免和原版按键冲突） */
export function installFogTuneHotkey() {
    window.addEventListener('keydown', (e) => {
        if (!e.ctrlKey || e.altKey || e.metaKey || e.code !== 'F7') return;
        e.preventDefault();
        e.stopPropagation();
        toggleFogTunePanel();
    }, true);
}

// ---------------------------------------------------------------- 内部

function makeSlider(lv, key, name, value, min, max, step, fmt) {
    const wrap = document.createElement('div');
    wrap.style.cssText = 'display:flex;align-items:center;gap:7px;height:19px;';
    const tag = document.createElement('span');
    tag.style.cssText = 'width:56px;color:#c8b98e;flex:none;';
    tag.textContent = name;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(min); input.max = String(max); input.step = String(step);
    input.value = String(value);
    input.style.cssText = 'flex:1;accent-color:#c9a24a;cursor:pointer;';
    input.dataset.lv = String(lv);
    input.dataset.key = key;
    const val = document.createElement('span');
    val.style.cssText = 'width:42px;text-align:right;color:#8ef58e;flex:none;font-variant-numeric:tabular-nums;';
    val.textContent = fmt(value);
    input.addEventListener('input', () => {
        const v = Number(input.value);
        setFogLevel(lv, key, v);
        val.textContent = fmt(v);
    });
    wrap.appendChild(tag);
    wrap.appendChild(input);
    wrap.appendChild(val);
    return wrap;
}

function makeBtn(text, fn) {
    const b = document.createElement('div');
    b.style.cssText = 'flex:1;text-align:center;padding:4px 2px;cursor:pointer;'
        + 'border:1px solid #5c4d30;background:#241d12;color:#ffe9a8;font-size:11px;';
    b.textContent = text;
    b.addEventListener('mouseenter', () => { b.style.background = '#3a2f1c'; });
    b.addEventListener('mouseleave', () => { b.style.background = '#241d12'; });
    b.addEventListener('mousedown', (e) => e.stopPropagation());
    b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
    return b;
}

function refreshAll() {
    if (!panelEl) return;
    const cfg = getFogLevels();
    for (const input of panelEl.querySelectorAll('input[type=range]')) {
        const lv = Number(input.dataset.lv);
        const key = input.dataset.key;
        input.value = String(cfg[lv][key]);
        const val = input.parentElement.querySelector('span:last-child');
        if (val) val.textContent = key === 'alpha' ? cfg[lv][key].toFixed(2) : Math.round(cfg[lv][key]) + 'px';
    }
}
