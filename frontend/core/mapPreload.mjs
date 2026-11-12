// 首屏地图资源预加载（在登录 / 选角阶段后台执行）
//
// 问题：进游戏时 MapActor.enter 会按可见格一次性预取 300+ 张地面/对象纹理，
// 对应 ~16 个 512KB 的 WIL 分片（约 7.7MB）。公网上这套请求在 6 秒超时兜底
// 之前根本下不完，于是「正在加载地图资源」卡住，放行后地图还一块块冒出来。
//
// 解法：把完全相同的纹理集合提前到「打开网页 → 登录 → 选角」这段时间下载。
// 帧集合直接复用 MapActor.collectVisibleTextures，保证预加载的就是真正要用的；
// 纹理落在 WIL.textures / IndexedDB 里，进游戏时 getTexture 立即命中内存，
// _prefetchVisible 瞬间返回，不再需要 6 秒超时。
//
// 附带预热 HUD 底板/按钮与 mmap 小地图底图——它们同样在进游戏时才懒加载。

import { loadMap } from '../map/Maps.mjs';
import { collectVisibleTextures } from '../map/MapActor.mjs';
import { getLib } from '../image/Images.mjs';
import { assetCache } from '../gfx/assetCache.mjs';
import { SCREEN_WIDTH, MAPSURFACEHEIGHT } from './config.mjs';

// TFrmDlg 底板与常驻控件（PlayScene._libUrl('prguse', idx) 直接用 idx 作帧号）
const HUD_FRAMES = {
    prguse: [
        1,                                  // DBottom 800×251 底板
        4, 5, 6, 7,                        // 血/魔球、经验/负重条
        8, 9, 10, 11, 12, 13, 14, 15,     // 右上功能按钮、昼夜图标
        ...Array.from({ length: 10 }, (_, i) => 30 + i),   // 位图数字
        ...Array.from({ length: 15 }, (_, i) => 128 + i),  // 中部/右下按钮
        370, 371,                          // 状态窗口小图
    ],
    prguse3: [29, 30, 32, 33, 207],       // 装备/属性/技能页底图
};

const CONCURRENCY = 6;    // 与 HTTP/1.1 每域名 6 连接量级相当，避免和 UI 帧抢带宽
const IDLE_LISTENERS = new Set();

export const preloadState = {
    active: null,          // 当前任务
    completed: new Set(),  // 已完成的 "map:x:y"
    progress: { done: 0, total: 0, label: '' },
    onProgress: null,      // (done,total,label)=>void
};

function emit(done, total, label) {
    preloadState.progress = { done, total, label };
    const cb = preloadState.onProgress;
    if (cb) { try { cb(done, total, label); } catch { /* UI 回调不许影响加载 */ } }
}

function notifyIdle() {
    if (preloadState.active) return;
    for (const fn of IDLE_LISTENERS) { try { fn(); } catch { /* noop */ } }
    IDLE_LISTENERS.clear();
}

/** 所有预加载任务结束（调试 / 自动化验证用）。 */
export function waitIdle() {
    if (!preloadState.active) return Promise.resolve();
    return new Promise(resolve => IDLE_LISTENERS.add(resolve));
}

function onPreloadProgress(cb) { preloadState.onProgress = cb; }

/** 并发受限的任务池；task.cancel 为真时立刻停止领新任务。 */
async function pool(items, task, worker) {
    let cursor = 0;
    const runners = Array.from({ length: Math.min(CONCURRENCY, items.length) }, async () => {
        while (!task.cancel && cursor < items.length) {
            const i = cursor++;
            try { await worker(items[i]); } catch { /* 单帧失败不影响其余 */ }
        }
    });
    await Promise.all(runners);
}

async function runTask(task) {
    const { mapId, cx, cy, miniMapIndex } = task;
    try {
        // ① 解析 .map（字节优先读 IndexedDB；700×700 解析约 70ms，结果模块内缓存复用）
        let m;
        try { m = await loadMap(mapId); }
        catch (e) { console.warn('[preload] 地图不可用', mapId, e); return; }
        if (task.cancel) return;

        const jobs = collectVisibleTextures(m, cx, cy, SCREEN_WIDTH, MAPSURFACEHEIGHT);
        // ② 先补齐这些图库的 WIX 索引（几 KB，是 getTexture 的前提）
        const libs = [...new Set(jobs.map(j => j.lib))];
        await Promise.all(libs.map(async lib => {
            const image = getLib(lib);
            if (image) { try { await image.loadIndex(); } catch { /* 缺库跳过 */ } }
        }));
        if (task.cancel) return;

        // ③ 全量预取纹理（无 6 秒超时：后台跑，不阻塞任何界面）
        let done = 0;
        const total = jobs.length;
        emit(0, total, '正在预载地图资源…');
        await pool(jobs, task, async ({ lib, index }) => {
            const image = getLib(lib);
            if (image) await image.getTexture(index);
            done++;
            if (done === total || done % 8 === 0) emit(done, total, '正在预载地图资源…');
        });
        if (task.cancel) return;
        emit(total, total, '正在预载地图资源…');

        // ④ HUD 常驻贴图 + 小地图底图（同一批 WIL，通常已在③的分片里顺带解包）
        const extras = [];
        for (const [lib, ids] of Object.entries(HUD_FRAMES)) {
            for (const id of ids) extras.push({ lib, index: id });
        }
        if (miniMapIndex > 0) extras.push({ lib: 'mmap', index: miniMapIndex - 1 });
        await pool(extras, task, async ({ lib, index }) => {
            const image = getLib(lib);
            if (image) await image.getTexture(index);
        });
        if (task.cancel) return;

        preloadState.completed.add(task.key);
        try { await assetCache.flush(); } catch { /* 落盘失败下次再下 */ }
    } finally {
        if (preloadState.active === task) {
            preloadState.active = null;
            emit(preloadState.progress.done, preloadState.progress.total, '地图资源就绪');
            notifyIdle();
        }
    }
}

/**
 * 预加载某地图某坐标的首屏纹理。可重复调用：
 * - 相同 key 直接复用正在跑的任务；
 * - 不同 key（例如启动时的默认出生点 → 选角时拿到的角色真实位置）
 *   会让旧任务让位，避免把带宽浪费在玩家根本不会去的地图上。
 * @returns {Promise<string>} 'done' | 'cancelled' | 'cached'
 */
export function preloadMapTextures(mapId, cx = 0, cy = 0, opts = {}) {
    try {
        if (localStorage.getItem('jootm2.preload') === '0') return Promise.resolve('disabled');
    } catch { /* 隐私模式：照常预载 */ }
    if (mapId == null) return Promise.resolve('skip');
    const key = `${mapId}:${cx}:${cy}`;
    if (preloadState.completed.has(key)) return Promise.resolve('cached');
    const cur = preloadState.active;
    if (cur && cur.key === key && !cur.cancel) return cur.promise;
    if (cur && !cur.cancel) cur.cancel = true;   // 精确位置优先于默认出生点

    const task = {
        key, mapId: String(mapId), cx: Number(cx) || 0, cy: Number(cy) || 0,
        miniMapIndex: Number(opts.miniMapIndex) || 0,
        cancel: false, promise: null,
    };
    preloadState.active = task;
    task.promise = runTask(task);
    return task.promise;
}

/**
 * 取消当前后台预载任务。进游戏时必须调用：
 * 后台预载（地图纹理 / HUD / 全量 WIX 索引）与进图首屏抢的是同一批 6 条连接，
 * 慢网下实测会把地面纹理压到「进图 20 秒还没解出一张」。
 */
export function cancelPreload() {
    const cur = preloadState.active;
    if (cur) cur.cancel = true;
    return !!cur;
}

export { onPreloadProgress };
