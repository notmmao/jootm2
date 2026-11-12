// 现场诊断与自救（对应「按一下键，把现场存下来」的需求）
//
// 快捷键（全部带 Ctrl，避免和原版按键冲突：原版 F9 背包 / F10 状态 / F11 技能）：
//   Ctrl+F9   导出诊断：JSON（帧耗时分布 + 地面层逐格状态 + 黑块游程 + 纹理/缓存统计
//             + 两条 PNG：可见画面、地面层离屏画布）
//   Ctrl+F10  局部刷新：丢弃地面层离屏缓存、清掉失败纹理标记后重新请求，只重建当前视口
//   Ctrl+F11  深度刷新：在局部刷新基础上清空全部纹理占位（含卡住不返回的）
//   Ctrl+F8   开关性能 HUD（fps / 各阶段 p50 / 未画格数 / 纹理失败数）
//
// 为什么要「逐格 + 黑块游程」：用户反复反馈的地图黑条（灰条）只有两种可能——
//   ① 地面层某些格子根本没画上真实地砖（纹理没到 / 索引为 -1 / 绘制分支漏了）
//   ② 画上了，但借用了同样黑的邻格像素（黑块自我复制成整条）
// 只截图看不出是哪种，所以导出时直接读地面层画布像素，按 48×32 逐格求平均亮度，
// 把「连续黑色格」合并成游程，并附上该格的 bng/mid/obj 索引、纹理状态、cells 状态。

import { perf, PERF_KEYS } from './perf.mjs';
import { assetCache } from '../gfx/assetCache.mjs';
import { scheduler } from '../gfx/scheduler.mjs';
import { getBootInfo } from './boot.mjs';
import { libStats } from '../image/Images.mjs';

const HUD_ID = 'jootm2-perf-hud';
const TOAST_ID = 'jootm2-toast';

/** 控制台日志环（导出时带上，能看出刷新前后有没有静默异常） */
const logs = [];
function pushLog(level, args) {
    try {
        const text = args.map(a => {
            if (a instanceof Error) return a.message;
            if (typeof a === 'string') return a;
            try { return JSON.stringify(a); } catch { return String(a); }
        }).join(' ');
        logs.push({ t: Date.now(), level, text: text.slice(0, 300) });
        if (logs.length > 80) logs.shift();
    } catch { /* 日志本身不能抛 */ }
}

function hookConsole() {
    for (const level of ['error', 'warn']) {
        const orig = console[level].bind(console);
        console[level] = (...args) => { pushLog(level, args); orig(...args); };
    }
    window.addEventListener('error', (e) => pushLog('pageerror', [e.message]));
    window.addEventListener('unhandledrejection', (e) => pushLog('rejection', [String(e.reason && e.reason.message || e.reason)]));
}

function toast(text, ms = 1800) {
    let el = document.getElementById(TOAST_ID);
    if (!el) {
        el = document.createElement('div');
        el.id = TOAST_ID;
        el.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);'
            + 'z-index:2147483647;padding:7px 14px;border-radius:6px;'
            + 'background:rgba(12,12,12,.86);color:#ffd76a;border:1px solid rgba(255,215,106,.45);'
            + 'font:13px/1.5 SimSun,serif;pointer-events:none;white-space:pre;text-align:center;'
            + 'box-shadow:0 4px 18px rgba(0,0,0,.5);transition:opacity .25s;';
        document.body.appendChild(el);
    }
    el.textContent = text;
    el.style.opacity = '1';
    clearTimeout(el._timer);
    el._timer = setTimeout(() => { el.style.opacity = '0'; }, ms);
}

function download(name, text) {
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 20000);
}

function fmtStat(s) {
    return `${s.p50}/${s.p95}/${s.max}`;
}

export function installDiag(deps = {}) {
    hookConsole();
    const getScene = deps.getScene || (() => window.__sceneManager && window.__sceneManager.current);
    const getCanvas = deps.getCanvas || (() => document.getElementById('screen'));
    const getStage = deps.getStage || (() => document.getElementById('stage'));

    const api = {
        last: null,
        hud: false,
        report: () => buildReport(getScene, getCanvas),
        dump: () => doDump(getScene, getCanvas),
        softRefresh: () => doRefresh(getScene, 'soft'),
        hardRefresh: () => doRefresh(getScene, 'hard'),
        toggleHud: () => toggleHud(),
        logs: () => logs.slice(),
    };

    // ---- 快捷键（捕获阶段拦下，避免同时触发原版 F9/F10/F11） ----
    window.addEventListener('keydown', (e) => {
        if (!e.ctrlKey || e.altKey || e.metaKey) return;
        const code = e.code;
        if (code !== 'F8' && code !== 'F9' && code !== 'F10' && code !== 'F11') return;
        e.preventDefault();
        e.stopPropagation();
        if (code === 'F9') api.dump();
        else if (code === 'F10') api.softRefresh();
        else if (code === 'F11') api.hardRefresh();
        else api.toggleHud();
    }, true);

    window.__diag = api;
    console.info('[diag] Ctrl+F9 导出诊断 / Ctrl+F10 局部刷新 / Ctrl+F11 深度刷新 / Ctrl+F8 性能 HUD');
    return api;
}

// ---------------------------------------------------------------- 刷新

function doRefresh(getScene, mode) {
    const s = getScene();
    const ma = s && s.mapActor;
    if (!ma || !ma.map) {
        toast('当前不在游戏场景，无法刷新');
        return null;
    }
    const before = perf.overBudget(20, perf.recent(4000));
    const r = ma.refreshFloor({ dropFailed: true, dropPending: mode === 'hard', dropAll: false });
    perf.reset();   // 刷新后的帧才算数，便于前后对比
    const msg = `${mode === 'hard' ? '深度刷新' : '局部刷新'}：清 ${r.dropped} 个异常纹理，`
        + `重建地面层 ${r.gw}×${r.gh}\n刷新前 4s 内 ${before.over}/${before.frames} 帧超 20ms，最差 ${before.worst}ms`;
    toast(msg, 3200);
    console.info('[diag] ' + msg.replace('\n', ' | '));
    return r;
}

// ---------------------------------------------------------------- HUD

function toggleHud() {
    const old = document.getElementById(HUD_ID);
    if (old) { old.remove(); return false; }
    const el = document.createElement('div');
    el.id = HUD_ID;
    el.style.cssText = 'position:fixed;right:8px;top:8px;z-index:2147483646;'
        + 'padding:6px 9px;border-radius:6px;background:rgba(0,0,0,.62);'
        + 'color:#8ef58e;font:11px/1.55 Consolas,monospace;pointer-events:none;'
        + 'white-space:pre;text-shadow:0 1px 0 #000;';
    document.body.appendChild(el);
    const tick = () => {
        if (!document.getElementById(HUD_ID)) return;
        el.textContent = hudText();
        setTimeout(tick, 250);
    };
    tick();
    return true;
}

function hudText() {
    const s = window.__sceneManager && window.__sceneManager.current;
    const ma = s && s.mapActor;
    const st = perf.stats(['dt', 'total', 'floor', 'obj', 'ent', 'map', 'hud'], perf.recent(1500));
    const dt = st.dt ? st.dt.p50 : 0;
    const fps = dt > 0 ? (1000 / dt).toFixed(0) : '--';
    const ob = perf.overBudget(20, perf.recent(1500));
    const lines = [
        `fps ${fps}  frame p50/p95 ${st.total ? fmtStat(st.total) : '--'}`,
        `>20ms ${ob.over}/${ob.frames} worst ${ob.worst}`,
        `floor ${st.floor ? fmtStat(st.floor) : '--'}  obj ${st.obj ? fmtStat(st.obj) : '--'}`,
        `ent ${st.ent ? fmtStat(st.ent) : '--'}  map ${st.map ? fmtStat(st.map) : '--'}`,
    ];
    if (ma && ma.map) {
        const d = ma.floorStats();
        lines.push(`floor cells  ok ${d.ok} borrow ${d.borrow} empty ${d.empty} open ${d.open}`);
        lines.push(`tex ${d.tex} pending ${d.pending} failed ${d.failed}`);
        lines.push(`cell ${ma._cx},${ma._cy} shift ${ma._shiftX},${ma._shiftY} pref ${ma._prefetching ? 'Y' : 'n'}`);
    }
    if (s && s.entities) lines.push(`entities ${s.entities.size} items ${s.groundItems ? s.groundItems.size : 0}`);
    return lines.join('\n');
}

// ---------------------------------------------------------------- 导出

function doDump(getScene, getCanvas) {
    let rep;
    try {
        rep = buildReport(getScene, getCanvas);
    } catch (e) {
        rep = { at: new Date().toISOString(), error: String(e && e.stack || e) };
    }
    api_last(rep);
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const name = `jootm2-diag-${ts}.json`;
    download(name, JSON.stringify(rep));
    const m = rep.map || {};
    const bars = (m.darkRuns || []);
    toast(`已导出 ${name}\n黑块行 ${bars.length} 段，未画格 ${m.cells && m.cells.open != null ? m.cells.open : '?'}`
        + `\n（文件在浏览器下载目录）`, 4200);
    console.info('[diag] 导出', name, 'dark runs:', bars.length);
    return rep;
}

function api_last(rep) {
    if (window.__diag) window.__diag.last = rep;
}

function buildReport(getScene, getCanvas) {
    const s = getScene();
    const canvas = getCanvas();
    const sceneName = s && s.constructor ? s.constructor.name : null;
    const stage = document.getElementById('stage');
    const rep = {
        at: new Date().toISOString(),
        scene: sceneName,
        page: {
            href: location.href,
            ua: navigator.userAgent,
            dpr: window.devicePixelRatio,
            innerW: window.innerWidth, innerH: window.innerHeight,
            uiScale: readUiScale(),
            stageRect: stage ? rectOf(stage) : null,
            canvasSize: canvas ? [canvas.width, canvas.height] : null,
        },
        boot: safe(() => getBootInfo()),
        perf: {
            frames: perf.frames.length,
            totalFrames: perf.n,
            all: perf.stats(PERF_KEYS),
            last4s: perf.stats(PERF_KEYS, perf.recent(4000)),
            overBudget: perf.overBudget(20, perf.recent(4000)),
            raw: perf.frames.slice(-90).map(f => ({
                dt: r1(f.dt), total: r1(f.total),
                floor: r1(f.floor), obj: r1(f.obj), ent: r1(f.ent),
                map: r1(f.map), hud: r1(f.hud), build: r1(f.build), fx: r1(f.fx),
            })),
        },
        cache: safe(() => assetCache.stats()),
        range: safe(() => ({
            requests: scheduler.totalRequests,
            deduped: scheduler.dedupedRequests,
            cacheHits: scheduler.cacheHits,
            compressed: scheduler.compressedResponses,
            savedBytes: scheduler.savedBytes,
            bytesDown: scheduler.bytesDown,
            bytesPlain: scheduler.bytesPlain,
            aborted: scheduler.aborted || 0,
            active: scheduler.active.length,
            queued: scheduler.queue.length,
        })),
        libs: safe(() => libStats()),
        mem: performance.memory ? {
            usedMB: +(performance.memory.usedJSHeapSize / 1048576).toFixed(1),
            totalMB: +(performance.memory.totalJSHeapSize / 1048576).toFixed(1),
        } : null,
        map: null,
        images: null,
        logs: logs.slice(-40),
    };
    if (s && s.mapActor) rep.map = safe(() => s.mapActor.debugSnapshot());
    if (s && s.entities) {
        rep.entities = {
            count: s.entities.size,
            items: s.groundItems ? s.groundItems.size : 0,
            effects: s.effects ? s.effects.length : 0,
            list: [...s.entities.values()].slice(0, 60).map(e => ({
                id: e.id, name: e.name, x: e.x, y: e.y, hp: e.hp, maxHp: e.maxHp,
            })),
        };
    }
    if (canvas) {
        try { rep.images = { screen: canvas.toDataURL('image/png') }; } catch (e) { rep.images = { screenErr: String(e) }; }
    }
    const ma = s && s.mapActor;
    if (ma && ma._floor) {
        try { rep.images.floor = ma._floor.canvas.toDataURL('image/png'); } catch (e) { rep.images.floorErr = String(e); }
    }
    return rep;
}

function safe(fn) {
    try { return fn(); } catch (e) { return { error: String(e && e.message || e) }; }
}
function r1(v) { return v == null ? 0 : Math.round(v * 10) / 10; }
function rectOf(el) {
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
}
function readUiScale() {
    try {
        const t = getComputedStyle(document.getElementById('stage')).transform;
        const m = /matrix\(([^)]+)\)/.exec(t);
        return m ? +m[1].split(',')[0] : 1;
    } catch { return null; }
}
