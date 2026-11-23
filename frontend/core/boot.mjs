// 启动加载层：Loading 界面控制 + 分阶段资源预加载
//
// 目标：公网首次打开时不要黑屏干等，把「必须的等待」显式化成进度条；
// 已缓存的素材全部从 IndexedDB 读，二次打开几乎瞬间完成。
//
// 分层：
//  阻塞阶段  —— 资源清单 / 素材版本 / 缓存初始化 / WS / 音频索引 / 登录+选角首屏纹理
//  后台阶段  —— 登录后延迟低优先级预热全部 WIX 索引 + 出生地图字节（不解析、不抢交互）

import { getLib } from '../image/Images.mjs';
import { imageKeys, mapUrl } from '../gfx/resource.mjs';
import { assetCache } from '../gfx/assetCache.mjs';
import { loadWholeBytes } from '../gfx/fileCache.mjs';
import { preloadMapTextures, waitIdle, onPreloadProgress } from './mapPreload.mjs';

const ID_ROOT = 'boot-layer';
const ID_FILL = 'boot-fill';
const ID_STATUS = 'boot-status';
const ID_TIP = 'boot-tip';
const ID_PCT = 'boot-pct';

// 登录首屏（TLoginScene）+ 选角首屏（TSelectChrScene）立刻要用的帧。
// 这些帧本来就会在场景渲染时懒加载，这里只是把等待提前到有进度条的阶段。
const LOGIN_FRAMES = [22, 50, 52, 53, 60, 61, 62, 63, 64];       // chrsel 背景 + prguse 窗口/按钮
const DOOR_FRAMES = Array.from({ length: 10 }, (_, i) => 23 + i);  // 开门 10 帧
const SELECT_BUTTONS = [65, 66, 67, 68, 69, 70, 71, 72, 73];      // 选角背景 + 按钮
// 武士男/女各一组站立帧（建角/无角色时的默认立绘）。其他职业由
// SelectCharScene._ensurePortraits 在 characterList 到达后按需补。
const PORTRAITS = [];
for (const sex of [0, 1]) {
    const base = 40 + sex * 120;
    for (let f = 0; f < 10; f++) PORTRAITS.push(base + f);
    PORTRAITS.push(60 + sex * 120);
}

// 阻塞阶段只等「登录界面第一帧真正要画的东西」：ChrSel[22] 背景 +
// Prguse 窗口/按钮。开门动画的 10 张 800×600 大图每张都独占一个 512KB
// 分片，放在启动阶段会让冷启动多下 4MB（模拟公网实测 9s）；它们只在
// 登录成功后才用，交给后台最优先预热即可，输账号的几秒完全够。
const ESSENTIAL = [
    ['prguse', LOGIN_FRAMES.filter(n => n >= 50)],
    ['chrsel', [22]],
];
const BACKGROUND_TEXTURES = [
    ['chrsel', DOOR_FRAMES],      // ① 开门动画（登录成功后立刻要用）
    ['prguse', SELECT_BUTTONS],  // ② 选角按钮
    ['chrsel', PORTRAITS],       // ③ 默认立绘
];

function $(id) { return document.getElementById(id); }

/** 后台预取的完成信号（调试 / 自动化验证用）：window.__warmDone */
export const warmState = { done: Promise.resolve(), started: false, cancelled: false };

/** 取消后台预热（进游戏时调用，把带宽让给首屏地面/对象纹理）。 */
export function cancelWarm() {
    warmState.cancelled = true;
    return warmState.cancelled;
}

// 地图纹理预载进度（调试接口 window.__mapPreload，选角界面也用它显示等待原因）
onPreloadProgress((done, total, label) => {
    if (typeof window !== 'undefined') window.__mapPreload = { done, total, label };
});

export const boot = {
    shown: false,

    begin() {
        this.shown = true;
        this.progress('正在连接服务器…', 0, 1);
    },

    progress(label, done, total) {
        if (!this.shown) this.begin();
        const pct = total > 0 ? Math.max(0, Math.min(100, Math.round(done / total * 100))) : 0;
        const fill = $(ID_FILL), status = $(ID_STATUS), num = $(ID_PCT);
        if (fill) fill.style.width = pct + '%';
        if (status) status.textContent = label || '正在载入…';
        if (num) num.textContent = pct + '%';
    },

    step(label) {
        const fill = $(ID_FILL), status = $(ID_STATUS);
        if (status) status.textContent = label;
        if (fill && parseInt(fill.style.width, 10) < 10) fill.style.width = '10%';
    },

    finish() {
        this.progress('准备就绪', 1, 1);
        const root = $(ID_ROOT);
        if (!root) return;
        root.classList.add('done');
        setTimeout(() => root.remove(), 500);
    },

    fail(message) {
        const root = $(ID_ROOT), status = $(ID_STATUS), tip = $(ID_TIP);
        if (root) root.classList.add('error');
        if (status) status.textContent = message;
        if (tip) tip.textContent = '请检查网络后刷新页面重试（Ctrl+F5 可强制刷新）';
    },
};

/** 服务端素材指纹 + 默认出生点；Java 版或接口不可用时退回固定命名空间。 */
export const bootInfo = { version: 'default', start: null };

export function getBootInfo() { return bootInfo; }

export async function fetchAssetVersion() {
    try {
        const resp = await fetch('/api/asset-version', { cache: 'no-store' });
        if (!resp.ok) return 'default';
        const data = await resp.json();
        bootInfo.version = data && data.version ? String(data.version) : 'default';
        if (data && data.start && data.start.map != null) bootInfo.start = data.start;
        return bootInfo.version;
    } catch {
        return 'default';
    }
}

/**
 * 阻塞阶段的必要纹理预取。逐项回报进度，但 DOM 更新做节流，
 * 避免几十个纹理同时完成时反复触发布局。
 * @param {(done:number,total:number,label:string)=>void} onProgress
 */
export async function preloadEssentials(onProgress) {
    const tasks = [];
    for (const [lib, ids] of ESSENTIAL) {
        for (const id of new Set(ids)) tasks.push({ lib, id });
    }
    const total = tasks.length;
    let done = 0;
    const label = '正在载入界面素材…';
    onProgress(0, total, label);
    await Promise.all(tasks.map(async ({ lib, id }) => {
        try {
            const image = getLib(lib);
            if (image) await image.getTexture(id);
        } catch { /* 单张纹理失败不阻塞启动，场景里还有帧回退兜底 */ }
        done++;
        if (done === total || done % 4 === 0) onProgress(done, total, label);
    }));
    onProgress(total, total, label);
    return total;
}

/** 并发受限的小任务池（后台预热用，避免占满浏览器 6 条 HTTP/1.1 连接）。 */
async function pool(items, limit, worker) {
    let cursor = 0;
    const runners = Array.from({ length: Math.min(limit, items.length) }, async () => {
        while (cursor < items.length) {
            const i = cursor++;
            try { await worker(items[i]); } catch { /* 单项失败忽略 */ }
        }
    });
    await Promise.all(runners);
}

/** 只下载并缓存 .map 字节，绝不解析：700×700 的 tile 对象会卡住登录界面主线程。 */
async function warmMap(mapId) {
    const url = mapUrl(mapId);
    if (!url) return;
    await loadWholeBytes('MAP', url);   // 缓存链命中则零网络；失败上抛由调用方静默
}

let mapBytesPromise = Promise.resolve();

/**
 * Loading 阶段就发起 .map 字节下载（不 await，不拖慢首屏）。
 * 公网上 0.map gzip 后约 600KB，却常因与几十个 WIL 分片抢带宽拖到 6~14 秒；
 * 进游戏时 loadMap() 若现下这份字节，就会白等这么久。
 */
export function warmMapBytes(mapId) {
    if (mapId == null) return mapBytesPromise;
    mapBytesPromise = warmMap(String(mapId)).catch(() => {});
    return mapBytesPromise;
}

/**
 * 登录界面显示后延迟执行：58 个 WIX 索引合计不到 1MB，预热后进游戏可省 50+ 个 RTT；
 * 出生地图（0.map ~5.9MB，gzip 后小得多）只暖字节不解析。
 * 用 localStorage['jootm2.preload'] = '0' 可关闭。
 */
export function warmInBackground(delayMs = 400) {
    let disabled = false;
    try { disabled = localStorage.getItem('jootm2.preload') === '0'; } catch { /* 隐私模式 */ }
    if (disabled) return;
    warmState.started = true;
    warmState.done = new Promise((resolve) => {
        setTimeout(async () => {
            try {
                // ① 开门 / 选角界面纹理（登录成功后 3 秒动画立刻要用，先抢完）
                for (const [lib, ids] of BACKGROUND_TEXTURES) {
                    if (warmState.cancelled) return;
                    const image = getLib(lib);
                    if (!image) continue;
                    await pool(ids, 6, async (id) => { await image.getTexture(id); });
                }
                if (warmState.cancelled) return;
                // ② 首屏地图纹理：进游戏最大的一块下载（~16 个 512KB 分片）。
                //    必须紧跟在开门帧之后，不能排在全量 WIX 之后——公网上那 58 个
                //    请求足以把窗口期吃光，导致进游戏时纹理一张都没预热。
                //    .map 字节在 Loading 阶段已并行开下，这里等它落缓存再解析。
                const start = getBootInfo().start;
                if (start && !warmState.cancelled) {
                    await mapBytesPromise;
                    await preloadMapTextures(start.map, start.x, start.y,
                        { miniMapIndex: start.miniMapIndex });
                } else {
                    await warmMapBytes('0');   // 老服务端：至少把 0.map 字节暖进缓存
                }
                // ③ 补齐全量 WIX 索引（合计 <1MB），换了地图也不必再等 RTT
                if (warmState.cancelled) return;
                await pool(imageKeys(), 2, async (key) => {
                    const lib = getLib(key);
                    if (lib) await lib.loadIndex();
                });
                await waitIdle();   // 角色位置触发的精确预载也跑完
                try { await assetCache.flush(); } catch { /* 落盘失败下次再下 */ }
            } catch (e) {
                console.warn('[boot] 后台预热失败', e);
            } finally {
                resolve(true);
            }
        }, delayMs);
    });
}
