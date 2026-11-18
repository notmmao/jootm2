// 地图渲染器（canvas 2d，对应旧 MapActor / PlayScn.pas 渲染语义）
// 等距瓦片 48×32，图层：大地砖 tiles → 小地砖 smtiles → 实体/对象（按行遮挡）。
// 绘制语义照原版 PlayScn.pas：
//   - 普通对象：格子左上对齐，底对齐（y = 格顶 + 32 - h）
//   - 混合光效（blend）：x = 格左 + px - 2，y = 格顶 + py - 68（GetObjsEx 的 ax/ay = WIL px/py）
//   - 名字/血条：贴 sprite 顶部（取各层最小 py）
// 平滑卷动：render 接收 shiftX/shiftY（玩家移动插值），坐标收口在 screenX/screenY。

import { loadMap } from './Maps.mjs';
import { LightMap, loadLightMasks } from './LightMap.mjs';
import { getLib } from '../image/Images.mjs';
import { PALETTE_RGBA } from '../gfx/palette.mjs';
import { perf } from '../core/perf.mjs';
import { getFogLevel } from '../core/fogConfig.mjs';

const TILE_W = 48;
const TILE_H = 32;
/** 头顶 HUD 显示偏好：由 PlayScene 从 localStorage 读入后整体注入，切换开关即时生效。
 *  - enhanced：名字按类别/关系色、血条按敌友描边；关闭时维持原版全红观感。
 *  - showHpLabel：对应原版 boShowRedHPLable（ClMain.pas:7005，缺省 False）；
 *    这里默认开以保持现有观感，关掉后只有心灵启示(28)等临时血条。
 *  - hoverNamesOnly：对应原版只画 g_FocusCret / 悬停自己的行为。
 */
const HUD_DEFAULTS = Object.freeze({
    enhanced: false, showHpLabel: true, hoverNamesOnly: false,
});

/** 调色板索引 → CSS 颜色（原版 GetRGB 用 DefColorTable 同一张表）。 */
function paletteFill(idx) {
    if (idx == null) return null;
    const i = Number(idx) * 4;
    if (!Number.isInteger(i) || i < 0 || i + 2 >= PALETTE_RGBA.length) return null;
    return `rgb(${PALETTE_RGBA[i]},${PALETTE_RGBA[i + 1]},${PALETTE_RGBA[i + 2]})`;
}
// 地面缓存未就绪/加载失败时的兜底底色（暗土色）。纯黑/近黑会被看成「一条一条的黑条」，
// 这里用明显亮于纯黑的地色，并且优先借用已绘制格像素，保证任何时刻都不会露出黑条带。
export const FLOOR_FALLBACK = '#3a3222';
// 兜底借像素用的真实地砖样本数量：太少会把同一格重复铺满整行（横条纹），
// 太多则每格快照的拷贝开销变大。24 种地砖足以让整屏看起来是「正常地面」。
const SAMPLE_POOL_MAX = 24;
// 地面窗口原点对齐粒度（格）。偶数保证 tiles 2×2 象限划分稳定；取 4 让「窗口搬家」
// 从每 2 格一次降到每 4 格一次（每次要搬 1760×957 像素，是移动时的主要固定开销）。
const FLOOR_ALIGN = 4;
// 诊断阈值：地面层格平均亮度（0~255）低于 DARK 视为「黑块」，低于 DIM 视为「偏暗」。
const DARK_LUM = 18;
const DIM_LUM = 48;

/** cells 语义（三态 + 空态）：0=未画 1=真实地砖 2=借像素 3=确定没内容（索引 -1 / 地图外） */
const CELL_EMPTY = 3;

// 原版黑暗视野：cliUtil.pas:594-599 DrawFog 的 DarkLevel 分级
//   1 = HeavyDarkColorLevel（深夜/地下）、2 = LightDarkColorLevel（傍晚）、
//   3 = DengunColorLevel（正午微暗）；PlayScn.pas:745 ApplyLightMap 会先按
//   m_LightMap 把人物及光源周围的 fogmask 点亮，所以画面中心是一个圆形亮区。
// 这里用「整屏暗色 + destination-out 挖洞」等价实现，alpha/radius 已改为
// 运行时可调（core/fogConfig.mjs，游戏内系统菜单可实时改），默认值见该文件。

/** 与 render 完全一致的可见格范围（导出供启动阶段预加载复用，保证集合一模一样）。 */
export function visibleRange(m, cx, cy, viewW, viewH) {
    const halfW = Math.ceil(viewW / 2 / TILE_W) + 2;
    const halfH = Math.ceil(viewH / 2 / TILE_H) + 2;
    return {
        left: Math.max(0, Math.floor(cx - halfW)),
        top: Math.max(0, Math.floor(cy - halfH)),
        right: Math.min(m.width - 1, Math.ceil(cx + halfW)),
        bottom: Math.min(m.height - 1, Math.ceil(cy + halfH + 30)),
    };
}

/**
 * 地面层离屏缓存的窗口矩形（与 _ensureFloor 完全一致）。
 *
 * 为什么要单独一个函数：窗口必须**完整覆盖屏幕**（含 ±47px 平滑卷动偏移和
 * 4 格对齐余量），但也不能比屏幕大太多——窗口里的每一格都会被绘制，也会被
 * 预取。早期实现用了 ±10 格的冗余（38×31=1178 格，画布 1760×957），
 * 而可视区只有 17×14：多出来的 15 列每格都单独发一个纹理请求（实测进图时
 * 95 个请求排队，地面等 15 秒还下不完）。收到 26×23 后同一场景降到一个量级。
 */
export function floorWindowRect(cx, cy, viewW, viewH) {
    const WX_BACK = 15, WX_FWD = 9;    // 左/右需要覆盖的格数（含 shift 与对齐余量）
    const WY_BACK = 13, WY_FWD = 8;
    const wx0 = Math.floor((cx - 12) / FLOOR_ALIGN) * FLOOR_ALIGN;
    const wy0 = Math.floor((cy - 10) / FLOOR_ALIGN) * FLOOR_ALIGN;
    const gw = Math.max(Math.ceil(viewW / TILE_W) + 8, WX_BACK + WX_FWD + 2);
    const gh = Math.max(Math.ceil(viewH / TILE_H) + 6, WY_BACK + WY_FWD + 2);
    return { wx0, wy0, gw, gh, left: wx0, top: wy0, right: wx0 + gw - 1, bottom: wy0 + gh - 1 };
}

/**
 * 首屏可见格里「地面层」需要的纹理（大地砖 bng + 小地砖 mid，**不含对象**）。
 * 进图放行的判据只看这个集合：地面是整屏连续色块，缺一块就是一条横带；
 * 对象是零星贴图，晚到只是单个贴图冒出来，观感伤害小得多。
 */
export function collectFloorTextures(m, cx, cy, viewW = 800, viewH = 445) {
    const w = floorWindowRect(cx, cy, viewW, viewH);
    const r = {
        left: Math.max(0, w.left),
        top: Math.max(0, w.top),
        right: Math.min(m.width - 1, w.right),
        bottom: Math.min(m.height - 1, w.bottom),
    };
    const seen = new Set();
    const jobs = [];
    const add = (base, fileIdx, index) => {
        if (index == null || index < 0) return;
        const lib = fileIdx ? base + fileIdx : base;
        const key = lib + '/' + index;
        if (seen.has(key)) return;
        seen.add(key);
        jobs.push({ lib, index, key });
    };
    for (let gx = r.left; gx <= r.right; gx++) {
        for (let gy = r.top; gy <= r.bottom; gy++) {
            const t = m.tiles[gx] && m.tiles[gx][gy];
            if (!t) continue;
            if (t.bng >= 0 && gx % 2 === 0 && gy % 2 === 0) add('tiles', t.bngFile, t.bng);
            if (t.mid >= 0) add('smtiles', t.midFile, t.mid);
        }
    }
    return jobs;
}

/**
 * 算出首屏可见格需要的全部纹理（大地砖 / 小地砖 / 对象含动画帧），去重后返回。
 * 进游戏时 MapActor._prefetchVisible 与后台 mapPreload 共用这个函数，
 * 避免「预加载了一堆用不上的帧、真正要用的却没下」。
 * @returns {{lib:string,index:number,key:string}[]}
 */
export function collectVisibleTextures(m, cx, cy, viewW = 800, viewH = 445) {
    const r = visibleRange(m, cx, cy, viewW, viewH);
    const jobs = [];
    const seen = new Set();
    const add = (base, fileIdx, index) => {
        if (index == null || index < 0) return;
        const lib = fileIdx ? base + fileIdx : base;
        const key = lib + '/' + index;
        if (seen.has(key)) return;
        seen.add(key);
        jobs.push({ lib, index, key });
    };
    for (let gx = r.left; gx <= r.right; gx++) {
        for (let gy = r.top; gy <= r.bottom; gy++) {
            const t = m.tiles[gx] && m.tiles[gx][gy];
            if (!t) continue;
            if (t.bng >= 0 && gx % 2 === 0 && gy % 2 === 0) add('tiles', t.bngFile, t.bng);
            if (t.mid >= 0) add('smtiles', t.midFile, t.mid);
            if (t.obj >= 0) {
                const frames = t.hasAni ? Math.max(1, Math.min(32, t.aniFrame)) : 1;
                for (let f = 0; f < frames; f++) add('objects', t.objFile, t.obj + f);
            }
        }
    }
    return jobs;
}

export class MapActor {
    constructor(viewW, viewH) {
        this.viewW = viewW;   // 可视画布宽
        this.viewH = viewH;   // 可视画布高
        this.map = null;
        this.mapId = null;
        this._texCache = new Map();   // key -> { canvas, px, py }
        this.hudMode = { ...HUD_DEFAULTS };  // 头顶名字/血条显示偏好，PlayScene 可热更新
        this._nameW = new Map();      // 名字 -> 像素宽（measureText 每帧每实体一次太浪费）
        this._tintCache = new Map();  // 原始 canvas -> (color -> 染色后 canvas)，中毒变色用
        this._texVersion = 0;        // 纹理就绪计数（供地面层缓存判失效，预留）
        // 地面层（tiles + smtiles）离屏滚动缓存：按世界格原子生成，
        // 移动时滚动复用，避免新格子的「先画亮地砖、后叠暗阴影」两次变色。
        this._floor = null;
        this._prefetchAt = { x: -99999, y: -99999 };
        this._prefetching = false;
        // render 时缓存（供 screenX/screenY 使用）
        this._cx = 0;
        this._cy = 0;
        this._shiftX = 0;
        this._shiftY = 0;
        // 视野暗度（0 = 全亮）。由 PlayScene 按服务端 SM_NEWMAP/SM_CHANGEMAP
        // 的 cdir = DayBright() 设置；对应原版 PlayScn.pas:2388 的 DarkLevel。
        this.darkLevel = 0;
        this._fogMask = null;
        this._fogHoleR = 0;
        this._fogHoleCanvas = null;
        // ---- 原版 m_LightMap 多光源（PlayScn.pas:1327-1412 / 1471） ----
        // g_boForceNotViewFog：True = 免蜡烛，直接跳过 DrawFog（全亮）。
        this.forceNotViewFog = false;
        // g_boNoDarkness or g_MySelf.m_boDeath → g_boViewFog := FALSE：
        // 死亡时不再压暗，改由 PlayScene 叠 ceGrayScale 黑白效果。
        this.selfDead = false;
        // 每帧由 PlayScene 注入的技能/场景特效光源（火墙、火堆、魔法弹）。
        this.extraLights = [];
        this._lightMap = new LightMap();
        this._lightMasks = null;      // lig0a..f 解码后的 6 张 alpha 贴图
        this._fogBright = null;       // 每帧累加的 fogmask 缓冲
        loadLightMasks().then((masks) => { this._lightMasks = masks; });
    }

    /**
     * 载入地图。先把首屏可见区域需要的纹理一次性预取完成，再挂上 this.map，
     * 这样玩家看到的是「完整一帧」，而不是每帧清屏后瓦片一块块冒出来（严重闪屏）。
     * @param cx/cy 玩家所在格（决定预取范围）；onProgress(done,total) 加载进度回调
     */
    async enter(mapId, cx = 0, cy = 0, onProgress = null) {
        if (mapId === this.mapId && this.map) return;
        this.mapId = mapId;
        this.map = null;              // 加载期间 PlayScene.render 保留上一帧
        this._floor = null;          // 换图必须丢弃旧地面缓存
        this._prefetchAt = { x: -99999, y: -99999 };
        const m = await loadMap(mapId);
        try {
            await this._prefetchVisible(m, cx, cy, onProgress);
        } catch (e) {
            console.warn('[map] 首屏纹理预取失败，直接显示地图', e);
        } finally {
            this.map = m;            // 无论预取成败都必须放行，避免卡在加载界面
        }
    }

    /** 与 render 完全一致的可见格范围。 */
    _visibleRange(m, cx, cy) {
        return visibleRange(m, cx, cy, this.viewW, this.viewH);
    }

    /**
     * 预取首屏纹理，分两段放行：
     *   1) 地面层（tiles bng + smtiles mid）——**必须等齐**，最多 15 秒；地面缺格就是
     *      一条横带，是用户反复反馈的「灰色条纹」的唯一可见来源，不能靠兜底色蒙过去。
     *   2) 对象/动画帧——尽力而为，最多 6 秒；晚到只是单个贴图冒出来。
     * 超时只放行渲染，**绝不把仍在飞的请求标成 failed**（那会引发重复请求风暴，
     * 整片格子长期拿不到图，反而把条纹固化）。
     */
    async _prefetchVisible(m, cx, cy, onProgress) {
        // 地面层按「窗口矩形」收集（窗口里每一格都会被画），对象层按「可视矩形」收集
        // （对象只在屏幕内可见，多取就是白白多下几十 MB）。
        const floorList = collectFloorTextures(m, cx, cy, this.viewW, this.viewH);
        const floorKeys = new Set(floorList.map(j => j.key));
        const all = [];
        const seenKeys = new Set();
        for (const j of floorList) { if (!seenKeys.has(j.key)) { seenKeys.add(j.key); all.push(j); } }
        for (const j of collectVisibleTextures(m, cx, cy, this.viewW, this.viewH)) {
            if (!seenKeys.has(j.key)) { seenKeys.add(j.key); all.push(j); }
        }

        // 按图库分组再批量取：WIL.prefetch 会把同一图库里字节相邻的图合并成一次
        // Range 请求。逐张 getTexture 会让「一屏 231 张地砖」变成 231 个请求、
        // 而且每张都被 512KB 分片放大（现已改成精确区间）。
        const groups = new Map();     // libKey -> { lib, floor:[idx], obj:[idx], keys:Map(idx->key) }
        for (const { lib: libKey, index, key } of all) {
            if (this._texCache.has(key)) continue;
            const lib = getLib(libKey);
            if (!lib) continue;
            this._texCache.set(key, { canvas: null, px: 0, py: 0 });  // 占位，避免 render 重复发起
            let g = groups.get(libKey);
            if (!g) { g = { lib, floor: [], obj: [], keys: new Map() }; groups.set(libKey, g); }
            (floorKeys.has(key) ? g.floor : g.obj).push(index);
            g.keys.set(index, key);
        }

        const total = [...groups.values()].reduce((a, g) => a + g.floor.length + g.obj.length, 0);
        if (!total) return;
        let done = 0;
        const apply = (g) => (index, tex) => {
            const key = g.keys.get(index);
            const e = key ? this._texCache.get(key) : null;
            if (e) {
                e.canvas = tex.canvas; e.px = tex.px; e.py = tex.py;
                this._texVersion++;
            }
            if (onProgress && (++done % 8 === 0 || done >= total)) onProgress(done, total);
        };
        // 1) 地面层先独占带宽：地面缺块就是一片暗屏，对象晚到只是单个贴图冒出来。
        // 两者同时发会互相抢带宽（慢网下一屏对象常常比地面还大），实测把对象推迟到
        // 地面之后能把「进图第一帧就有真实地面」的比例从 0% 拉到 95%+。
        const floorJobs = [];
        for (const g of groups.values()) {
            if (g.floor.length) floorJobs.push(g.lib.prefetch(g.floor, apply(g), true).catch(() => {}));
        }
        await Promise.race([
            Promise.allSettled(floorJobs),
            new Promise(resolve => setTimeout(resolve, 15000)),
        ]);
        // 2) 对象层：地面放行后才开始，尽力而为，6 秒后不再等
        const objJobs = [];
        for (const g of groups.values()) {
            if (g.obj.length) objJobs.push(g.lib.prefetch(g.obj, apply(g)).catch(() => {}));
        }
        await Promise.race([
            Promise.allSettled(objJobs),
            new Promise(resolve => setTimeout(resolve, 6000)),
        ]);
        // 只记录「还在飞」的时间戳用于诊断，不改状态：请求完成时 apply() 会写入同一占位。
        for (const e of this._texCache.values()) {
            if (!e.canvas && !e.failed && !e.pendingSince) e.pendingSince = Date.now();
        }
    }

    // 取纹理（含 px/py；未加载返回 null，并触发异步加载）
    _getTex(key, index) {
        const k = key + '/' + index;
        const hit = this._texCache.get(k);
        if (hit) {
            // 上次失败（空图 / Range 出错）5 秒后允许重试，避免该格永久黑块
            if (hit.failed && Date.now() - (hit.failedAt || 0) > 5000) {
                this._texCache.delete(k);
            } else {
                return hit.canvas ? hit : null;   // 加载中返回 null
            }
        }
        this._texCache.set(k, { canvas: null, px: 0, py: 0 });
        const lib = getLib(key);
        if (!lib) {
            // 图库不存在：明确标记失败，避免占位永远空着又不重试（每帧白查一次）
            const e = this._texCache.get(k);
            if (e) { e.failed = true; e.failedAt = Date.now(); }
            return null;
        }
        lib.getTexture(index).then(tex => {
            const e = this._texCache.get(k);
            if (!e) return;
            if (tex) {
                e.canvas = tex.canvas; e.px = tex.px; e.py = tex.py;
                this._texVersion++;
            } else { e.failed = true; e.failedAt = Date.now(); }
        }).catch(() => {
            const e = this._texCache.get(k);
            if (e) { e.failed = true; e.failedAt = Date.now(); }
        });
        return null;
    }

    _libKey(base, fileIdx) {
        return fileIdx ? base + fileIdx : base;
    }

    // ---- 地面层滚动缓存（tiles + smtiles） ---------------------------------
    // 缓存画布以「世界格窗口」为坐标系，画布 (0,0) = 世界格 (wx0,wy0) 左上角。
    // 每格只在 bng（所属 2×2 大图的对应象限）与 mid 全部就绪时一次性画入，
    // 之后 cells[i]=1 永久复用；视口滚动时把旧内容按格差平移搬运，只补新露出的行列。
    // 这样屏幕上永远看不到「地砖先亮、阴影后叠暗」的中间态（移动时阴影颜色跳变主因）。

    _ensureFloor(cx, cy) {
        // 窗口几何与预取共用 floorWindowRect：凡是会被画到的格，必须也在预取范围内，
        // 否则那一格会退化成「渲染时单独发一个纹理请求」（早期 95 个请求排队的来源）。
        const rect = floorWindowRect(cx, cy, this.viewW, this.viewH);
        const { gw, gh, wx0, wy0 } = rect;
        const CW = gw * TILE_W;
        const CH = gh * TILE_H;
        const f = this._floor;
        if (f && f.gw === gw && f.gh === gh && f.wx0 === wx0 && f.wy0 === wy0) return f;

        if (f && f.gw === gw && f.gh === gh) {
            // 原地平移：旧内容按格差搬一次（1760×957 的一次 self-blit），
            // 不再每 4 格新建一块 6.7MB 画布 + 全量底色填充——那是移动时的隐性卡顿来源
            // （频繁分配大画布触发 GC，表现为「本地也偶尔一顿」）。
            this._shiftFloor(f, f.wx0 - wx0, f.wy0 - wy0);
            f.wx0 = wx0;
            f.wy0 = wy0;
            return f;
        }

        const canvas = document.createElement('canvas');
        canvas.width = CW;
        canvas.height = CH;
        const fctx = canvas.getContext('2d', { alpha: false });
        fctx.fillStyle = FLOOR_FALLBACK;
        fctx.fillRect(0, 0, CW, CH);
        // cells 语义：0=未画（含地图外） 1=已画真实地砖 2=已借像素 3=确定没内容
        // 旧实现把「未就绪」和「已完成」都记 1，或干脆不记，结果每个未就绪格**每帧**
        // 都要重新借像素重绘（一次整屏 200+ 格 × 多次大画布自拷贝 = 单帧数百毫秒），
        // 这正是用户反馈的「本地也卡」。现在借用即打标，纹理到达后才升级为 1。
        // f.open 只统计「还可能变化」的格（0 和 2）：3 态不再参与每帧扫描，
        // 否则 f.open 永远 > 0，_paintFloorCells 的快速返回失效。
        this._floor = {
            canvas, ctx: fctx, wx0, wy0, gw, gh,
            cells: new Uint8Array(gw * gh), open: gw * gh, empty: 0,
            lastPainted: (f && f.lastPainted) || null,
            samples: (f && f.samples) || [],
            sampleCanvas: (f && f.sampleCanvas) || null,
        };
        return this._floor;
    }

    /**
     * 原地搬移地面层缓存（自身为源、自身为目标）。规范要求实现先把源快照，
     * 所以重叠区域是安全的；整数像素偏移不参与插值，画面不会发糊。
     */
    _shiftFloor(f, dgx, dgy) {
        if (!dgx && !dgy) return;
        f.ctx.drawImage(f.canvas, dgx * TILE_W, dgy * TILE_H);
        const gw = f.gw, gh = f.gh, old = f.cells;
        const cells = new Uint8Array(gw * gh);
        let open = 0, empty = 0;
        for (let iy = 0; iy < gh; iy++) {
            const sy = iy - dgy;
            for (let ix = 0; ix < gw; ix++) {
                const sx = ix - dgx;
                const v = (sx >= 0 && sy >= 0 && sx < gw && sy < gh) ? old[sy * gw + sx] : 0;
                cells[iy * gw + ix] = v;
                if (v === 1) continue;
                if (v === CELL_EMPTY) empty++;
                else open++;
            }
        }
        f.cells = cells;
        f.open = open;
        f.empty = empty;
    }

    /**
     * 单张地面纹理的就绪状态。
     * 注意：failed 不等于「这一格没有图」——首屏预取超时 / Range 请求抖动都会置 failed，
     * 旧实现直接当成完成并把整格涂黑 + cells=1 永久锁定，正是「一条一条带间隔的黑条」根因。
     * 现在 failed 超过 5 秒就删占位重新加载，期间该格保持未完成（借用邻格像素）。
     */
    _floorTex(key, index) {
        if (index == null || index < 0) return { ready: true, tex: null };
        const k = key + '/' + index;
        const hit = this._texCache.get(k);
        if (hit && hit.canvas) return { ready: true, tex: hit };
        if (hit && hit.failed) {
            if (Date.now() - (hit.failedAt || 0) > 5000) {
                this._texCache.delete(k);
                this._getTex(key, index);   // 重新发起（内部会再放占位）
            }
            return { ready: false, tex: null };
        }
        if (!hit) this._getTex(key, index);
        return { ready: false, tex: null };   // 加载中：整格等待，不画半成品
    }

    /**
     * 未就绪的格子先铺一块「真实地砖」的样本，避免纯黑条带。
     *
     * **不要用大画布自拷贝**：`f.ctx.drawImage(f.canvas, ...)` 会强制实现先快照
     * 1760×957 的整张画布，单次 ~4ms；滚动时新进窗口的几十格各来一次就是
     * 100~200ms 的顿挫，刷新时 500 多格更是 2 秒以上（实测）。
     * 现在只从 48×32 的样本池里贴：单次约 1~3µs，且与画布大小无关。
     *
     * 样本池按世界格坐标散列取不同样本，保证同一格每帧固定（不闪），相邻格又不同。
     * 池子为空（极冷启动、连一块地砖都没到）时才用兜底底色。
     */
    _borrowFloorCell(f, ix, iy, px, py) {
        const pool = f.samples;
        if (pool && pool.length) {
            const h = (((f.wx0 + ix) * 73856093) ^ ((f.wy0 + iy) * 19349663)) >>> 0;
            f.ctx.drawImage(pool[h % pool.length], px, py);
            return;
        }
        f.ctx.fillStyle = FLOOR_FALLBACK;
        f.ctx.fillRect(px, py, TILE_W, TILE_H);
    }

    /**
     * 样本播种：从当前窗口里已就绪的地砖合成 48×32 样本，让「整屏未就绪」时也有
     * 真实地砖可铺（否则整屏是一块平坦的兜底暗色——就是用户说的黑条/黑块）。
     * 每 500ms 最多尝试一次，最多 24 种。
     */
    _seedSamples(m) {
        const f = this._floor;
        if (!f || f.samples.length >= SAMPLE_POOL_MAX) return 0;
        const now = Date.now();
        if (f._seedAt && now - f._seedAt < 500) return 0;
        f._seedAt = now;
        const seen = new Set();
        let added = 0;
        for (let iy = 0; iy < f.gh && f.samples.length < SAMPLE_POOL_MAX; iy += 1) {
            for (let ix = 0; ix < f.gw && f.samples.length < SAMPLE_POOL_MAX; ix += 3) {
                const gx = f.wx0 + ix, gy = f.wy0 + iy;
                const t = m.tiles[gx] && m.tiles[gx][gy];
                if (!t) continue;
                const bgx = gx & ~1, bgy = gy & ~1;
                const bt = m.tiles[bgx] && m.tiles[bgx][bgy];
                const bk = 't' + (bt ? bt.bngFile + ':' + bt.bng : '-');
                const mk = 's' + (t.midFile + ':' + t.mid);
                const sig = bk + '|' + mk;
                if (seen.has(sig)) continue;
                seen.add(sig);
                const bng = this._floorTex(this._libKey('tiles', bt ? bt.bngFile : 0), bt ? bt.bng : -1);
                const mid = this._floorTex(this._libKey('smtiles', t.midFile), t.mid);
                if (!bng.ready || !mid.ready || (!bng.tex && !mid.tex)) continue;
                const snap = document.createElement('canvas');
                snap.width = TILE_W;
                snap.height = TILE_H;
                const c = snap.getContext('2d');
                c.fillStyle = FLOOR_FALLBACK;
                c.fillRect(0, 0, TILE_W, TILE_H);
                if (bng.tex) {
                    const src = bng.tex.canvas;
                    const qx = (gx % 2) * TILE_W, qy = (gy % 2) * TILE_H;
                    if (src.width > qx && src.height > qy) {
                        c.drawImage(src, qx, qy, Math.min(TILE_W, src.width - qx),
                                    Math.min(TILE_H, src.height - qy), 0, 0, TILE_W, TILE_H);
                    } else if (src.width > 0) {
                        c.drawImage(src, 0, 0, src.width, src.height, 0, 0, TILE_W, TILE_H);
                    }
                }
                if (mid.tex) c.drawImage(mid.tex.canvas, 0, 0);
                f.samples.push(snap);
                added++;
            }
        }
        return added;
    }

    /** 状态收敛：只有落到 1（真实）或 3（空）才从 open 里摘除。 */
    _settleCell(f, idx, st, v) {
        if (st === v) return;
        f.cells[idx] = v;
        if (v === CELL_EMPTY) { f.open--; f.empty++; }
        else if (st === CELL_EMPTY) { f.open++; f.empty--; }   // 3→2 理论上不发生
    }

    _paintFloorCells(m, f) {
        // 全部格子都已画上真实地砖：本帧无事可做（最常见的情况，省掉 1000+ 次查表）
        if (f.open === 0) return;
        if (f.samples.length === 0) this._seedSamples(m);
        for (let iy = 0; iy < f.gh; iy++) {
            for (let ix = 0; ix < f.gw; ix++) {
                const idx = iy * f.gw + ix;
                const st = f.cells[idx];
                if (st === 1) continue;
                const gx = f.wx0 + ix, gy = f.wy0 + iy;
                const px = ix * TILE_W, py = iy * TILE_H;
                if (gx < 0 || gy < 0 || gx >= m.width || gy >= m.height) {
                    // 地图外不再涂纯黑：靠边界走动时纯黑格会被看成一条条黑带，
                    // 统一用与未就绪格相同的土地兜底色，并标 3（永不重试）。
                    f.ctx.fillStyle = FLOOR_FALLBACK;
                    f.ctx.fillRect(px, py, TILE_W, TILE_H);
                    this._settleCell(f, idx, st, CELL_EMPTY);
                    continue;
                }
                const col = m.tiles[gx];
                const t = col && col[gy];
                if (!t) {
                    // 地图数据里没有这一格：借一次像素后标 3，避免每帧重复借
                    if (st === 0) this._borrowFloorCell(f, ix, iy, px, py);
                    this._settleCell(f, idx, st, CELL_EMPTY);
                    continue;
                }
                const bgx = gx & ~1, bgy = gy & ~1;
                const bt = m.tiles[bgx] && m.tiles[bgx][bgy];
                const bng = this._floorTex(this._libKey('tiles', bt ? bt.bngFile : 0),
                                           bt ? bt.bng : -1);
                const mid = this._floorTex(this._libKey('smtiles', t.midFile), t.mid);
                if (!bng.ready || !mid.ready) {
                    // 原子：整格一起出现；借用过就停手，避免每帧重绘整屏（卡顿根因）
                    if (st === 0) this._borrowFloorCell(f, ix, iy, px, py);
                    this._settleCell(f, idx, st, 2);
                    continue;
                }
                if (!bng.tex && !mid.tex) {
                    // bng/mid 索引都是 -1（或图库缺书）：这一格永远拿不到地砖。
                    // 旧实现直接 fillRect(兜底色) 并标 1 —— 那就是「一格纯黑方块」，
                    // 成排出现就是用户看到的黑条。现在借邻格真实地砖，借不到用样本池。
                    if (st === 0) this._borrowFloorCell(f, ix, iy, px, py);
                    this._settleCell(f, idx, st, CELL_EMPTY);
                    continue;
                }

                // 先铺兜底底色：任一图层缺失/尺寸不足时也不会露出纯黑
                f.ctx.fillStyle = FLOOR_FALLBACK;
                f.ctx.fillRect(px, py, TILE_W, TILE_H);
                if (bng.tex) {
                    const src = bng.tex.canvas;
                    const qx = (gx % 2) * TILE_W, qy = (gy % 2) * TILE_H;
                    if (src.width > qx && src.height > qy) {
                        f.ctx.drawImage(src, qx, qy,
                            Math.min(TILE_W, src.width - qx), Math.min(TILE_H, src.height - qy),
                            px, py, TILE_W, TILE_H);
                    } else if (src.width > 0 && src.height > 0) {
                        // 少数 tiles 图只有单格尺寸（不足 96×64），象限越界时整图铺满当前格
                        f.ctx.drawImage(src, 0, 0, src.width, src.height, px, py, TILE_W, TILE_H);
                    }
                }
                if (mid.tex) f.ctx.drawImage(mid.tex.canvas, px, py);
                f.cells[idx] = 1; f.open--;
                // 供 _borrowFloorCell 在「整行都未就绪」时借像素用：维护一个多样本池，
                // 让兜底画面是多种地砖交错，而不是同一格重复铺满（横条纹观感来源）。
                if ((bng.tex || mid.tex) && f.samples.length < SAMPLE_POOL_MAX) {
                    f.lastPainted = { ix, iy };
                    if (!f.sampleCanvas) {
                        f.sampleCanvas = document.createElement('canvas');
                        f.sampleCanvas.width = TILE_W;
                        f.sampleCanvas.height = TILE_H;
                    }
                    const sctx = f.sampleCanvas.getContext('2d');
                    sctx.clearRect(0, 0, TILE_W, TILE_H);
                    sctx.drawImage(f.canvas, px, py, TILE_W, TILE_H, 0, 0, TILE_W, TILE_H);
                    const snap = document.createElement('canvas');
                    snap.width = TILE_W;
                    snap.height = TILE_H;
                    snap.getContext('2d').drawImage(f.sampleCanvas, 0, 0);
                    f.samples.push(snap);
                }
            }
        }
    }

    _syncFloor(ctx, m, cx, cy, shiftX, shiftY) {
        const f = this._ensureFloor(cx, cy);
        this._paintFloorCells(m, f);
        const ox = Math.floor((this.viewW - TILE_W) / 2);
        const oy = Math.floor((this.viewH - TILE_H) / 2);
        const dx = ox + (f.wx0 - cx) * TILE_W - shiftX;
        const dy = oy + (f.wy0 - cy) * TILE_H - shiftY;
        // 只贴「屏幕上真正可见的那一块」（9 参数 drawImage）。
        // 整张 1248×736 贴进 800×445 在 GPU 合成下几乎免费，但在软件渲染
        // （远程桌面 / 无 GPU 虚拟机 / 关掉硬件加速的浏览器）里是全尺寸逐像素，
        // 每帧几十毫秒——这类机器上表现为「一进游戏就卡」。
        const sx0 = Math.max(0, -dx);
        const sy0 = Math.max(0, -dy);
        const sw = Math.min(f.canvas.width - sx0, this.viewW - Math.max(0, dx));
        const sh = Math.min(f.canvas.height - sy0, this.viewH - Math.max(0, dy));
        if (sw <= 0 || sh <= 0) return;
        ctx.drawImage(f.canvas, sx0, sy0, sw, sh, dx + sx0, dy + sy0, sw, sh);
    }

    // ---- 诊断 / 自救（core/diag.mjs 的 Ctrl+F9 / Ctrl+F10 / Ctrl+F11） --------

    /** 地面层状态计数（HUD 与导出共用）。 */
    floorStats() {
        const f = this._floor;
        const out = { ok: 0, borrow: 0, empty: 0, open: 0,
                      tex: this._texCache.size, pending: 0, failed: 0,
                      gw: 0, gh: 0, open2: 0 };
        if (f) {
            out.gw = f.gw; out.gh = f.gh; out.open2 = f.open;
            for (let i = 0; i < f.cells.length; i++) {
                const v = f.cells[i];
                if (v === 1) out.ok++;
                else if (v === 2) out.borrow++;
                else if (v === CELL_EMPTY) out.empty++;
                else out.open++;
            }
        }
        for (const v of this._texCache.values()) {
            if (v.canvas) continue;
            if (v.failed) out.failed++; else out.pending++;
        }
        return out;
    }

    /** 单张纹理状态（诊断用，无副作用）：ok / pending / failed / none / absent */
    _texState(key, index) {
        if (index == null || index < 0) return 'none';
        const e = this._texCache.get(key + '/' + index);
        if (!e) return 'absent';
        if (e.canvas) return 'ok';
        if (e.failed) return 'failed';
        return 'pending';
    }

    /**
     * 刷新地面层（Ctrl+F10 局部 / Ctrl+F11 深度）。
     * 只重建离屏缓存，不动 WIL 已解好的纹理；顺带清掉失败/卡住的纹理占位，
     * 让下一帧重新发起请求——用来验证「黑条是不是等一会儿/刷一下就好」。
     */
    refreshFloor({ dropFailed = true, dropPending = false, dropAll = false } = {}) {
        let dropped = 0;
        if (dropAll) {
            dropped = this._texCache.size;
            this._texCache.clear();
        } else {
            for (const [k, v] of this._texCache) {
                if (v.canvas) continue;
                if (v.failed || dropPending) { this._texCache.delete(k); dropped++; }
            }
        }
        const f = this._floor;
        const gw = f ? f.gw : 0, gh = f ? f.gh : 0;
        this._floor = null;                                  // 下一帧整块重建
        this._prefetchAt = { x: -99999, y: -99999 };         // 强制重新增量预取
        this._texVersion++;
        return { dropped, gw, gh, tex: this._texCache.size };
    }

    /**
     * 现场快照：视口内逐格亮度 + 黑块游程 + 该格的索引/纹理状态。
     * 这是「黑条」定位的核心证据——只截图分不清「没画」还是「画黑了」。
     */
    debugSnapshot() {
        const m = this.map, f = this._floor;
        const out = {
            mapId: this.mapId,
            map: m ? [m.width, m.height] : null,
            view: [this.viewW, this.viewH],
            cell: [this._cx, this._cy],
            shift: [this._shiftX, this._shiftY],
            texVersion: this._texVersion,
            prefetch: { busy: this._prefetching, at: this._prefetchAt },
            floor: this.floorStats(),
        };
        if (!m || !f) { out.ready = false; return out; }
        out.ready = true;
        out.window = { wx0: f.wx0, wy0: f.wy0, gw: f.gw, gh: f.gh,
                       canvas: [f.canvas.width, f.canvas.height],
                       samples: f.samples.length };

        // 异常纹理清单（截断，避免 JSON 过大）
        const failed = [], pending = [];
        const now = Date.now();
        for (const [k, v] of this._texCache) {
            if (v.canvas) continue;
            if (v.failed) { if (failed.length < 80) failed.push(`${k} age=${now - (v.failedAt || 0)}ms`); }
            else if (pending.length < 80) {
                pending.push(k + (v.pendingSince ? ` age=${now - v.pendingSince}ms` : ''));
            }
        }
        out.texFailed = failed;
        out.texPending = pending;

        // 屏幕 → 地面层画布偏移（与 _syncFloor 完全一致）
        const ox = Math.floor((this.viewW - TILE_W) / 2);
        const oy = Math.floor((this.viewH - TILE_H) / 2);
        const dx = ox + (f.wx0 - this._cx) * TILE_W - this._shiftX;
        const dy = oy + (f.wy0 - this._cy) * TILE_H - this._shiftY;
        out.origin = { dx, dy };

        // 屏幕内可见格范围。注意：-dx/TILE_W 得到的是**窗口内索引**，加 f.wx0 才是地图格；
        // 早先漏了这一步，导致所有格都算成「窗口外」而被判成黑（诊断自证其误）。
        const gx0 = Math.max(0, f.wx0 + Math.floor(-dx / TILE_W));
        const gx1 = Math.min(m.width - 1, f.wx0 + Math.ceil((this.viewW - dx) / TILE_W) - 1);
        const gy0 = Math.max(0, f.wy0 + Math.floor(-dy / TILE_H));
        const gy1 = Math.min(m.height - 1, f.wy0 + Math.ceil((this.viewH - dy) / TILE_H) - 1);
        out.visible = { gx0, gx1, gy0, gy1 };

        let img = null;
        try { img = f.ctx.getImageData(0, 0, f.canvas.width, f.canvas.height); }
        catch (e) { out.imgErr = String(e && e.message || e); }
        if (!img) return out;

        const W = f.canvas.width;
        const lumOf = (ix, iy) => {
            let sum = 0, n = 0;
            for (let sy = 0; sy < 4; sy++) {
                const py = Math.min(f.canvas.height - 1, (iy * TILE_H + ((sy * 2 + 1) * TILE_H / 8)) | 0);
                for (let sx = 0; sx < 6; sx++) {
                    const px = Math.min(W - 1, (ix * TILE_W + ((sx * 2 + 1) * TILE_W / 12)) | 0);
                    const o = (py * W + px) * 4;
                    sum += (img.data[o] * 299 + img.data[o + 1] * 587 + img.data[o + 2] * 114) / 1000;
                    n++;
                }
            }
            return sum / n;
        };

        const dimCells = [];       // 偏暗格明细（附带该格的索引与状态）
        const darkRuns = [];       // 连续黑块游程（按行）
        const rows = [];
        for (let gy = gy0; gy <= gy1; gy++) {
            let runStart = -1;
            let darkInRow = 0;
            for (let gx = gx0; gx <= gx1; gx++) {
                const ix = gx - f.wx0, iy = gy - f.wy0;
                const inWin = ix >= 0 && iy >= 0 && ix < f.gw && iy < f.gh;
                const lum = inWin ? lumOf(ix, iy) : 0;
                const st = inWin ? f.cells[iy * f.gw + ix] : -1;
                if (lum < DARK_LUM) {
                    darkInRow++;
                    if (runStart < 0) runStart = gx;
                } else if (runStart >= 0) {
                    darkRuns.push({ gy, from: runStart, to: gx - 1, len: gx - runStart });
                    runStart = -1;
                }
                if (lum < DIM_LUM && dimCells.length < 240) {
                    const t = m.tiles[gx] && m.tiles[gx][gy];
                    const bgx = gx & ~1, bgy = gy & ~1;
                    const bt = m.tiles[bgx] && m.tiles[bgx][bgy];
                    dimCells.push({
                        gx, gy, lum: +lum.toFixed(1), st,
                        bng: t ? t.bng : null, mid: t ? t.mid : null, obj: t ? t.obj : null,
                        bngFile: t ? t.bngFile : null, midFile: t ? t.midFile : null,
                        blockBng: bt ? bt.bng : null,
                        bngState: this._texState(this._libKey('tiles', bt ? bt.bngFile : 0), bt ? bt.bng : -1),
                        midState: this._texState(this._libKey('smtiles', t ? t.midFile : 0), t ? t.mid : -1),
                    });
                }
            }
            if (runStart >= 0) darkRuns.push({ gy, from: runStart, to: gx1, len: gx1 - runStart + 1 });
            if (darkInRow) rows.push({ gy, dark: darkInRow, of: gx1 - gx0 + 1 });
        }
        out.darkRuns = darkRuns;
        out.darkRows = rows;
        out.dimCells = dimCells;
        out.darkCells = rows.reduce((a, r) => a + r.dark, 0);
        out.scanned = (gx1 - gx0 + 1) * (gy1 - gy0 + 1);
        return out;
    }

    // ---- 移动时增量预取（按格原子提交） ------------------------------------
    _maybePrefetch(m, cx, cy) {
        if (this._prefetching) return;
        if (Math.abs(cx - this._prefetchAt.x) < 4 && Math.abs(cy - this._prefetchAt.y) < 4) return;
        this._prefetchAt = { x: cx, y: cy };
        this._prefetching = true;
        this._prefetchAround(m, cx, cy)
            .catch(e => console.warn('[map] 增量预取失败', e))
            .finally(() => { this._prefetching = false; });
    }

    /** 预取视口外冗余一圈的全部纹理；一格的 bng/mid/obj 帧一次性写入缓存。
     *  分批执行 + 每批让出主线程，避免上千个 Range 请求/解包把渲染卡住。 */
    async _prefetchAround(m, cx, cy) {
        // 覆盖「地面窗口 + 2 格外扩」：窗口内的格子一定会被绘制，窗口外的留给下一次
        // 移动时再取。按可视区取会比窗口小，导致窗口边缘的格子每帧反复发单张请求。
        const w = floorWindowRect(cx, cy, this.viewW, this.viewH);
        const r = {
            left: Math.max(0, w.left - 2),
            top: Math.max(0, w.top - 2),
            right: Math.min(m.width - 1, w.right + 2),
            bottom: Math.min(m.height - 1, w.bottom + 2),
        };
        const cells = [];
        // 行优先：每 24 格一批覆盖「一整行的连续列」，而不是一列里的 24 行。
        // 旧顺序会让屏幕上连续多行同时处于未就绪状态，兜底像素连成横条纹。
        for (let gy = r.top; gy <= r.bottom; gy++) {
            for (let gx = r.left; gx <= r.right; gx++) {
                const t = m.tiles[gx] && m.tiles[gx][gy];
                if (!t) continue;
                const loads = [];
                const add = (base, fileIdx, index) => {
                    if (index == null || index < 0) return;
                    const key = this._libKey(base, fileIdx);
                    const k = key + '/' + index;
                    const hit = this._texCache.get(k);
                    if (hit && (hit.canvas || hit.failed)) return;
                    const lib = getLib(key);
                    if (!lib) return;
                    // 先占位：渲染侧看到「加载中」会整格等待，而不是逐层冒出
                    if (!hit) this._texCache.set(k, { canvas: null, px: 0, py: 0 });
                    loads.push({ k, libName: key, lib, index });
                };
                if (t.bng >= 0 && gx % 2 === 0 && gy % 2 === 0) add('tiles', t.bngFile, t.bng);
                if (t.mid >= 0) add('smtiles', t.midFile, t.mid);
                if (t.obj >= 0) {
                    const frames = t.hasAni ? Math.max(1, Math.min(32, t.aniFrame)) : 1;
                    for (let f = 0; f < frames; f++) add('objects', t.objFile, t.obj + f);
                }
                if (loads.length) cells.push(loads);
            }
        }
        for (let i = 0; i < cells.length; i += 24) {
            const batch = cells.slice(i, i + 24);
            // 批内按图库归组：一次 Range 覆盖多张相邻图，慢网下省掉大量 RTT
            const byLib = new Map();   // libKey -> { lib, keys: Map(index->k) }
            for (const loads of batch) {
                for (const { k, libName, lib, index } of loads) {
                    let g = byLib.get(libName);
                    if (!g) { g = { lib, keys: new Map() }; byLib.set(libName, g); }
                    g.keys.set(index, k);
                }
            }
            const jobs = [];
            for (const g of byLib.values()) {
                const idx = [...g.keys.keys()];
                const run = g.lib.prefetch(idx, (index, tex) => {
                    const e = this._texCache.get(g.keys.get(index));
                    if (e) {
                        e.canvas = tex.canvas; e.px = tex.px; e.py = tex.py;
                        this._texVersion++;
                    }
                }).catch(() => {});
                // 8 秒兜底，避免某一段挂住阻塞后续批次；不把仍在飞的请求判成 failed
                jobs.push(Promise.race([run, new Promise(res => setTimeout(() => res(null), 8000))]));
            }
            await Promise.all(jobs);
            // 让出一帧给渲染，避免连续解包长时间占着主线程
            await new Promise(res => setTimeout(res, 0));
        }
    }

    /** 动画对象当前帧未就绪时回退到该对象已加载的最近帧，避免整棵树/建筑消失一帧。 */
    _getObjFrame(t, frame) {
        const key = this._libKey('objects', t.objFile);
        const want = this._getTex(key, t.obj + frame);
        if (want) return { tex: want, blend: t.blend };
        if (!t.hasAni || t.aniFrame <= 1) return null;
        const n = Math.max(1, Math.min(32, t.aniFrame));
        for (let d = 1; d < n; d++) {
            const e = this._texCache.get(key + '/' + (t.obj + ((frame - d + n) % n)));
            if (e && e.canvas) return { tex: e, blend: t.blend };
        }
        return null;
    }

    /** 中毒变色：在 sprite 自己的离屏画布上叠加颜色（source-atop 的 destination 只有 sprite 像素，
     *  精确跟随实体轮廓）。结果按（原始 canvas, 颜色）缓存，避免每帧重建。 */
    _tint(canvas, color) {
        let byColor = this._tintCache.get(canvas);
        if (!byColor) {
            byColor = new Map();
            this._tintCache.set(canvas, byColor);
        }
        let tinted = byColor.get(color);
        if (!tinted) {
            const c = document.createElement('canvas');
            c.width = canvas.width;
            c.height = canvas.height;
            const cc = c.getContext('2d');
            cc.drawImage(canvas, 0, 0);
            cc.globalCompositeOperation = 'source-atop';
            cc.fillStyle = color;
            cc.fillRect(0, 0, c.width, c.height);
            tinted = c;
            byColor.set(color, c);
        }
        return tinted;
    }

    // 屏幕坐标（收口：所有叠加物——血条/名字/地面物品/高亮——都用这两个换算）
    screenX(gx) {
        const ox = Math.floor((this.viewW - TILE_W) / 2);
        return ox + (gx - this._cx) * TILE_W - this._shiftX;
    }

    screenY(gy) {
        const oy = Math.floor((this.viewH - TILE_H) / 2);
        return oy + (gy - this._cy) * TILE_H - this._shiftY;
    }

    /**
     * 绘制地图 + 实体（含对象层遮挡）。
     * @param shiftX/shiftY 平滑卷动偏移（玩家从旧格滑向新格的插值，像素）
     * @param entities [{x,y,name,isSelf,hp,maxHp,isFocus,layers:[{canvas,px,py}]}]
     */
    /** 对象层当前动画帧的纹理（动画帧未就绪时回退最近已加载帧，可能为 null）。 */
    _objTex(t) {
        let frame = 0;
        if (t.hasAni) {
            frame = Math.floor(Date.now() / 200) % Math.max(1, Math.min(32, t.aniFrame));
        }
        const got = this._getObjFrame(t, frame);
        return got ? got.tex : null;
    }

    /** 是否「与地面同层」的小对象（图正好 48×32）。
     *
     * 原版 `PlayScn.pas` 分两遍画 obj 层：
     * - 第一遍（行循环之前、和地砖一起）**只画 48×32 的对象**；
     * - 第二遍（和 actor 按行交错）`if (Width <> 48) or (Height <> 32)` 才画。
     *
     * 桥面正好是 48×32：所以人应该永远踩在桥上，而不是被桥盖住。旧实现把两遍并成一遍，
     * 桥面就被当成「大树」按行遮挡，从下往上走（人被画在桥所在行的前一行）时桥压在身上。
     */
    static _isSmallObj(tex) {
        return !!tex && tex.canvas.width === 48 && tex.canvas.height === 32;
    }

    render(ctx, cx, cy, entities, shiftX = 0, shiftY = 0, drawDrop = null, groundItems = null,
           drawEvent = null, groundEvents = null) {
        if (!this.map) return;
        const m = this.map;
        this._cx = cx;
        this._cy = cy;
        this._shiftX = shiftX;
        this._shiftY = shiftY;

        const sx = gx => this.screenX(gx);
        const sy = gy => this.screenY(gy);

        // 可见区域（游戏坐标，含冗余）
        const halfW = Math.ceil(this.viewW / 2 / TILE_W) + 2;
        const halfH = Math.ceil(this.viewH / 2 / TILE_H) + 2;
        const left = Math.max(0, Math.floor(cx - halfW));
        const top = Math.max(0, Math.floor(cy - halfH));
        const right = Math.min(m.width - 1, Math.ceil(cx + halfW));
        const bottom = Math.min(m.height - 1, Math.ceil(cy + halfH + 30));

        // 1+2. 地面层（tiles + smtiles）：离屏滚动缓存，整帧一次贴图。
        // 每格按世界坐标原子生成，移动时复用旧内容，杜绝逐层异步到位造成的颜色跳变。
        let tPhase = performance.now();
        this._syncFloor(ctx, m, cx, cy, shiftX, shiftY);
        perf.add('floor', performance.now() - tPhase);
        // 视口移出首屏预取范围前，提前把前方格子的全部纹理按格原子载入
        this._maybePrefetch(m, cx, cy);

        // 3. 小对象（48×32）与地面同层：原版 PlayScn.pas 第一遍 obj 循环
        // `if (DSurface.Width = 48) and (DSurface.Height = 32)` 在行循环之前就画完，
        // 所以桥面/地毯这类 1 格见方的对象永远在所有 actor 之下（人踩在桥上）。
        tPhase = performance.now();
        for (let gy = top; gy <= bottom; gy++) {
            for (let gx = left; gx <= right; gx++) {
                const t = m.tiles[gx][gy];
                if (t.obj < 0) continue;
                const tex = this._objTex(t);
                if (!tex || !MapActor._isSmallObj(tex)) continue;
                // 与原版一致：48×32 的小对象按格对齐（m + UNITY - Height），不做 blend
                ctx.drawImage(tex.canvas, sx(gx), sy(gy) + TILE_H - tex.canvas.height);
            }
        }
        perf.add('obj', performance.now() - tPhase);

        // 4. 大对象 + 实体按行交错（y 越大越靠屏幕下方，遮挡在前）
        // Map 每帧 clear 复用：实体多时每帧新建两个 Map 都是可观的 GC 抖动来源。
        const byRow = this._byRow || (this._byRow = new Map());
        byRow.clear();
        for (const e of entities) {
            // drawRow（原版 m_nDownDrawLevel）：沃玛系尸体分到 y-1 行参与排序，
            // 屏幕锚点仍用 e.y —— 效果是同 y 行的掉落物/实体画在尸体之上。
            const rowY = (e.drawRow != null && e.drawRow >= 0) ? e.drawRow : e.y;
            if (!byRow.has(rowY)) byRow.set(rowY, []);
            byRow.get(rowY).push(e);
        }
        // 地面掉落物也按行调度（原版 PlayScn.pas 在 `for j` 行循环里画
        // DropedItemList：`if j = DropItem.y - BlockTop`）。以前在整帧末尾统一画，
        // 会盖在同格角色/树木/建筑之上，看起来像浮在半空、位置不对。
        const dropsByRow = this._dropsByRow || (this._dropsByRow = new Map());
        dropsByRow.clear();
        if (drawDrop && groundItems) {
            // 允许直接传 Map（省掉每帧 [...values()] 的数组分配；场上几百个掉落物时可观）
            const it = Array.isArray(groundItems) ? groundItems : groundItems.values();
            for (const g of it) {
                if (g.y < top || g.y > bottom) continue;
                if (!dropsByRow.has(g.y)) dropsByRow.set(g.y, []);
                dropsByRow.get(g.y).push(g);
            }
        }
        // 场景事件（原版 TClEvent：僵尸出土的洞口、尸块、圣言术帘幕等）也按行调度。
        // PlayScn.pas:1259-1266 在**行循环内**、同行地砖/大对象画完之后、掉落物与
        // 角色之前 `if j = evn.m_nY - BlockTop then evn.DrawEvent(...)`——所以洞口
        // 属于地面层，站在上面的角色/怪会盖住它，后一行的树/城墙也会盖住它。
        // 以前放在整帧末尾的特效层（_renderEffects）里画，洞口会盖在人身上。
        const eventsByRow = this._eventsByRow || (this._eventsByRow = new Map());
        eventsByRow.clear();
        if (drawEvent && groundEvents) {
            for (const ev of groundEvents) {
                if (ev.y < top || ev.y > bottom) continue;
                if (!eventsByRow.has(ev.y)) eventsByRow.set(ev.y, []);
                eventsByRow.get(ev.y).push(ev);
            }
        }
        // 名字/血条排队（每帧复用，避免分配）：[snap, anchorX, anchorY] 三元组平铺
        const hudQueue = this._hudQueue || (this._hudQueue = []);
        hudQueue.length = 0;
        let selfSnap = null;
        let focusSnap = null;
        for (let gy = top; gy <= bottom; gy++) {
            tPhase = performance.now();
            // 大对象层（照 PlayScn.pas 第二遍：`Width<>48 or Height<>32` 才在这里画；
            // 普通对象左上+底对齐；blend 光效 x=格左+px-2, y=格顶+py-68）
            for (let gx = left; gx <= right; gx++) {
                const t = m.tiles[gx][gy];
                if (t.obj < 0) continue;
                // 加色光效（blend，0x80 标志）与是否动画无关：静态火/光类贴图也必须用 lighter 混合，
                // 否则会丢失辉光（原版 DrawScrn 对 boBlend 一律加色，不论 ani&0x80）。
                const blend = !!t.blend;
                // 当前动画帧未就绪时回退该对象已加载的最近帧，绝不整棵树/建筑消失
                const tex = this._objTex(t);
                // 48×32 的已经在地面那一遍画过了（原版两遍互斥），这里必须跳过
                if (!tex || MapActor._isSmallObj(tex)) continue;
                let px, py;
                if (blend) {
                    px = sx(gx) + (tex.px || 0) - 2;
                    py = sy(gy) + (tex.py || 0) - 68;
                } else {
                    px = sx(gx);
                    py = sy(gy) + TILE_H - tex.canvas.height;
                }
                if (blend) {
                    // save/restore 保证 lighter 不泄漏到后续实体/HUD 绘制
                    ctx.save();
                    ctx.globalCompositeOperation = 'lighter';
                    ctx.drawImage(tex.canvas, px, py);
                    ctx.restore();
                } else {
                    ctx.drawImage(tex.canvas, px, py);
                }
            }
            perf.add('obj', performance.now() - tPhase);
            tPhase = performance.now();
            // 本行场景事件（原版 PlayScn.pas:1259-1266）：在同行地砖/大对象之上、
            // 掉落物与角色之下。锚点 = 格子左上角 + 图自带 px/py（DrawEvent 里
            // `Draw(ax + m_nPx, ay + m_nPy)`，不做格中心换算、不做加色混合）。
            const evs = eventsByRow.get(gy);
            if (evs) {
                for (const ev of evs) drawEvent(ctx, ev, sx(ev.x), sy(ev.y));
            }
            // 本行地面物品：先于实体绘制，后续行的对象/角色才能正确遮挡它
            const drops = dropsByRow.get(gy);
            if (drops) {
                // 原版同格物品都叠在格中心（一格最多 5 件，见 Envir.pas:230）。
                // 把「同格第几件」传给绘制回调，让前端做几像素的确定性错开，
                // 否则 BOSS「爆了」时一格里的几件完全重叠、看不出也点不到。
                const cells = this._dropCells || (this._dropCells = new Map());
                cells.clear();
                for (const g of drops) {
                    const key = g.x + ':' + g.y;
                    const slot = cells.get(key) || 0;
                    cells.set(key, slot + 1);
                    drawDrop(ctx, g, sx(g.x), sy(g.y), slot);
                }
            }
            // 实体（照原版：锚点 = 格子左上角，图左上 = 格左+px, 格顶+py）
            const row = byRow.get(gy);
            if (row) {
                for (const e of row) {
                    // 锚点 = 格子左上 + 走动画偏移（self 用 _viewX 过渡，offsetX/Y 为像素差）
                    const anchorX = sx(e.x) + (e.offsetX || 0);
                    const anchorY = sy(e.y) + (e.offsetY || 0);
                    this._drawEntity(ctx, e, anchorX, anchorY);
                    // 名字/血条不在这里画：原版 DrawScrn.pas:296-321 是在所有实体画完之后
                    // 单独一遍 → 后一行的树/怪不可能盖住前一行的血条。这里先排队。
                    if (e.maxHp || e.name) hudQueue.push(e, anchorX, anchorY);
                    // 记住自己：原版 PlayScn.pas:1361 画完行循环后要把自己再画一遍
                    if (e.isSelf) selfSnap = [e, anchorX, anchorY];
                    // 选中目标（g_FocusCret）同样在行循环后重画一遍（PlayScn.pas:1369-1376）
                    if (e.isFocus && !e.isSelf) focusSnap = [e, anchorX, anchorY];
                }
            }
            perf.add('ent', performance.now() - tPhase);
        }

        // 原版 PlayScn.pas:1361-1383：按行画完之后把「自己」（以及选中目标/施法目标）
        // **再画一遍**。以前只靠「尸体 unshift 到列表最前」，但按行绘制会重新按 y 分桶
        // → 同一/下一行的尸体照样盖住人（面朝上时最明显）。
        // 关键细节：这一遍 DrawChr 的 blend 参数是 **TRUE**（Actor.pas:1983 DrawEffSurface
        // → DrawBlend(..., 0) = Color256Mix 半透明）——所以原版的实际效果是：
        //   人被树/城墙/城门挡住时，人物以约 50% 透明度「透」在最上面，而不是实心盖在树上。
        // 旧实现画成实心 → 「人物一直站在树/城墙上」。 FocusCret/MagicTarget 同为 TRUE。
        if (selfSnap) {
            ctx.save();
            ctx.globalAlpha = 0.5;
            this._drawEntity(ctx, selfSnap[0], selfSnap[1], selfSnap[2]);
            ctx.restore();
        }
        if (focusSnap) {
            ctx.save();
            ctx.globalAlpha = 0.5;
            this._drawEntity(ctx, focusSnap[0], focusSnap[1], focusSnap[2]);
            ctx.restore();
        }

        // 原版 PlayScn.pas:1471-1474：DrawFog 画在 ObjSurface 上、MSurface.Draw
        // 之前，也就是压住地面与所有精灵；血条/名字属于之后的 DrawScrn，不受影响。
        this._drawFog(ctx, sx(cx), sy(cy), cx, cy, entities);

        // 原版 DrawScrn.pas:296-321：血条 + 名字单独一遍，压在所有精灵之上；
        // DrawScrn.pas:306 明确要求 `not actor.m_boDeath` → 尸体不画血条。
        for (let i = 0; i < hudQueue.length; i += 3) {
            this._drawHeadHud(ctx, hudQueue[i], hudQueue[i + 1], hudQueue[i + 2]);
        }
        hudQueue.length = 0;

        // 原版 PlayScn.pas:1417-1438「地面物品闪亮」：每 g_dwDropItemFlashTime(5s) 把
        // BoFlash 置 TRUE，之后每 20ms 走一帧 FLASHBASE(410)+step，step 0..9 共 10 帧，
        // DrawBlend(..., 1)（Color256Anti 加色提亮）叠在最上层。1.80 的 Prguse.wil
        // 410-418 正是这组「由小变大再变小」的星光（8×7 → 32×30 → 8×7），419 为 1×1 收尾。
        if (groundItems) {
            const now = Date.now();
            const it = Array.isArray(groundItems) ? groundItems : groundItems.values();
            for (const g of it) {
                if (g.y < top || g.y > bottom) continue;
                const st = g._flash || (g._flash = {});
                if (now - (st.last || 0) > 5000) {
                    st.last = now; st.on = true; st.step = 0; st.stepAt = now;
                }
                if (!st.on) continue;
                if (now - st.stepAt >= 20) { st.stepAt = now; st.step++; }
                if (st.step >= 10) { st.on = false; continue; }
                const tex = this._getTex('prguse', 410 + st.step);
                if (!tex) continue;
                ctx.save();
                ctx.globalCompositeOperation = 'lighter';
                ctx.drawImage(tex.canvas, sx(g.x) + tex.px, sy(g.y) + tex.py);
                ctx.restore();
            }
        }
    }

    /**
     * 黑暗视野：原版 ClearLightMap → AddLight → ApplyLightMap(FogCopy) → DrawFog。
     *
     * cliUtil.pas:160-224 的 ColorLevel 表按 fogmask n∈[0,30] 把像素乘上
     *   HeavyDark (n+1)/31 / LightDark (3n+47)/140 / Dengun (3n+120)/214，
     * 也就是「n 越大越亮、n=0 最暗」。canvas 2d 没有 8 位调色板，这里等价地：
     *   1) 把每张 lig0*.dat 预解码成「黑 + alpha=n/30」的画布，多光源用
     *      'lighter' 累加 alpha（= FogCopy 的 paddd 累加，饱和 255 即 n>=30）；
     *   2) 整屏铺 cfg.alpha 的暗色，再用 'destination-out' 贴累加缓冲挖掉暗度。
     * 保留项目已有的 cfg.alpha 作为「全暗」基准，避免把昼夜观感整体改暗。
     *
     * 光源本身严格走原版亮度网格（LightMap.mjs）：同格取更亮等级、
     * CheckOverLight 去重、UpdateBright 邻域抑制，所以一排火把不会无限叠亮。
     * lig0*.dat 尚未加载完时退回旧的单中心径向孔，保证不会黑屏。
     */
    _drawFog(ctx, px, py, cx, cy, entities) {
        const lv = this.darkLevel | 0;
        const cfg = getFogLevel(lv);   // 运行时配置：系统菜单可调，每帧读缓存
        // PlayScn.pas:1469/1471：免蜡（g_boForceNotViewFog）与死亡都不画雾；
        // 死亡分支在 1476 改画 ceGrayScale（由 PlayScene 处理）。
        if (!cfg || this.forceNotViewFog || this.selfDead) return;
        const w = this.viewW, h = this.viewH;
        const mask = this._fogMask || (this._fogMask = document.createElement('canvas'));
        if (mask.width !== w || mask.height !== h) {
            mask.width = w; mask.height = h;
        }
        const mc = mask.getContext('2d');
        mc.globalCompositeOperation = 'source-over';
        mc.clearRect(0, 0, w, h);
        mc.fillStyle = `rgba(0,0,0,${cfg.alpha})`;
        mc.fillRect(0, 0, w, h);

        // 原 bug：只判数组引用，6 张 lig0 全加载失败时数组仍为真 → 挖洞全空、全屏黑纱、
        // 径向兜底永不执行。改为判「至少有一张有效贴图」才走 mask 分支。
        const hasMasks = !!(this._lightMasks && this._lightMasks.some(m => m && m.canvas));
        if (hasMasks) {
            const bright = this._fogBright || (this._fogBright = document.createElement('canvas'));
            if (bright.width !== w || bright.height !== h) {
                bright.width = w; bright.height = h;
            }
            const bc = bright.getContext('2d');
            bc.globalCompositeOperation = 'source-over';
            bc.clearRect(0, 0, w, h);
            bc.globalCompositeOperation = 'lighter';
            for (const L of this._collectLights(cx, cy, entities)) {
                const m = this._lightMasks[L.level];
                if (!m) continue;
                // PlayScn.pas:758-768：贴到 (lxx-(W-48)/2, lyy-(H-32)/2-5)：
                // 相当于以格中心为基准，水平 +14、垂直 -5 的偏移。
                const dx = Math.round(this.screenX(L.x) + TILE_W / 2 - m.width / 2
                                      + 14 + (L.shiftX || 0));
                const dy = Math.round(this.screenY(L.y) + TILE_H / 2 - m.height / 2
                                      - 5 + (L.shiftY || 0));
                if (dx >= w || dy >= h || dx + m.width <= 0 || dy + m.height <= 0) continue;
                bc.drawImage(m.canvas, dx, dy);
            }
            bc.globalCompositeOperation = 'source-over';
            mc.globalCompositeOperation = 'destination-out';
            mc.drawImage(bright, 0, 0);
            mc.globalCompositeOperation = 'source-over';
        } else {
            const hole = this._fogHole(cfg.radius);
            mc.globalCompositeOperation = 'destination-out';
            mc.drawImage(hole, Math.round(px - cfg.radius), Math.round(py - cfg.radius));
            mc.globalCompositeOperation = 'source-over';
        }
        ctx.drawImage(mask, 0, 0);
    }

    /** PlayScn.pas:1327-1412 的 AddLight 收集顺序，返回登记成功的光源。 */
    _collectLights(cx, cy, entities) {
        const lm = this._lightMap;
        lm.clear();
        const m = this.map;
        // 1) 地图自带静态光源（TMapUnitInfo.btLight，Envir.pas:33）：
        //    视口 +5/-4 格，nocheck=FALSE。灯柱/火盆/城内灯笼都来自这里。
        const halfW = Math.ceil(this.viewW / 2 / TILE_W) + 5;
        const halfH = Math.ceil(this.viewH / 2 / TILE_H) + 4;
        for (let gy = Math.round(cy) - halfH; gy <= Math.round(cy) + halfH; gy++) {
            for (let gx = Math.round(cx) - halfW; gx <= Math.round(cx) + halfW; gx++) {
                const col = m.tiles[gx];
                const t = col && col[gy];
                if (t && t.light > 0) {
                    lm.addLight(gx, gy, 0, 0, Math.min(5, t.light), false, cx, cy);
                }
            }
        }
        // 2) actor：原版 `if (actor=g_MySelf) or (actor.Light>0) then
        //    AddLight(..., actor.Light, actor=g_MySelf)`，自己 nocheck=TRUE。
        //    服务端按 U_RIGHTHAND（蜡烛/火把，StdMode 30）下发 light=3，
        //    没带灯时给 1 的基础微光（原版 m_nChrLight，SM_LOGON 的 Hibyte(cdir)）。
        for (const e of entities || []) {
            if (!e.isSelf && !(e.light > 0)) continue;
            const level = e.isSelf ? (e.light != null ? e.light : 1) : e.light;
            lm.addLight(e.x, e.y, e.shiftX || 0, e.shiftY || 0,
                        level, !!e.isSelf, cx, cy);
        }
        // 3+4) TMagicEff.Light / TClEvent.m_nLight（火墙、火堆、神圣战甲术）
        for (const L of this.extraLights) {
            if (L.level > 0) {
                lm.addLight(L.x, L.y, L.shiftX || 0, L.shiftY || 0,
                            L.level, false, cx, cy);
            }
        }
        return lm.entries(cx, cy);
    }

    /** 预生成「挖洞」用的径向 alpha 渐变（中心全挖空 → 边缘不挖）。 */
    _fogHole(r) {
        if (this._fogHoleR === r && this._fogHoleCanvas) return this._fogHoleCanvas;
        const cv = document.createElement('canvas');
        cv.width = cv.height = r * 2;
        const c = cv.getContext('2d');
        const g = c.createRadialGradient(r, r, 0, r, r, r);
        g.addColorStop(0, 'rgba(0,0,0,1)');
        g.addColorStop(0.35, 'rgba(0,0,0,0.98)');
        g.addColorStop(0.7, 'rgba(0,0,0,0.55)');
        g.addColorStop(1, 'rgba(0,0,0,0)');
        c.fillStyle = g;
        c.fillRect(0, 0, r * 2, r * 2);
        this._fogHoleR = r;
        this._fogHoleCanvas = cv;
        return cv;
    }

    /** 画一个实体的精灵层（隐身半透明 / 悬停提亮 / 中毒染色）。 */
    _drawEntity(ctx, e, anchorX, anchorY) {
        if (e.layers) {
            ctx.save();
            // 隐身术：半透明
            if (e.alpha != null && e.alpha < 1) ctx.globalAlpha = e.alpha;
            // 悬停/施法目标高亮：原版 Actor.pas:2044 的 ceBright（DrawEffSurface 调色），
            // g_FocusCret / g_MagicTarget 只让 sprite 变亮，不画任何边框。
            if (e.isFocus) ctx.filter = 'brightness(1.28)';
            // 红毒（掉血）绿色调 / 绿毒（减防）红色调：在每个 sprite 自己的离屏画布上染色
            // （source-atop 的 destination 只有 sprite 像素 → 颜色精确跟随实体轮廓，不会盖到地图上）
            const tint = e.poison ? 'rgba(60,255,60,0.35)' : (e.poisonArmor ? 'rgba(255,60,60,0.35)' : null);
            for (const layer of e.layers) {
                if (layer.canvas) {
                    const img = tint ? this._tint(layer.canvas, tint) : layer.canvas;
                    if (layer.blend === 'lighter') {
                        // 死亡火焰等叠加特效（原版 DrawBlend(..., 1)）：加色混合，不覆盖身体轮廓
                        ctx.save();
                        ctx.globalCompositeOperation = 'lighter';
                        ctx.drawImage(img, anchorX + layer.px, anchorY + layer.py);
                        ctx.restore();
                    } else {
                        ctx.drawImage(img, anchorX + layer.px, anchorY + layer.py);
                    }
                }
            }
            ctx.restore();
        } else if (e.tex) {
            ctx.drawImage(e.tex, anchorX - e.tex.width / 2, anchorY - e.tex.height);
        }
    }

    /** 头顶血条 + 名字（原版 DrawScrn.pas:296-344）。 */
    _drawHeadHud(ctx, e, anchorX, anchorY) {
        const hud = this.hudMode || HUD_DEFAULTS;
        const enhanced = !!hud.enhanced;
        const pet = e.masterId != null;

        // 血条（原版 DrawScrn：SayY - 10 = 格顶 - 57，头顶上方）
        // 原版只有一种 HEALTHBAR_RED，人物也不换色；以前给「自己」用了天蓝色，
        // 看起来像魔法条，跟左下红血球对不上（用户反馈的「两边不一致」）。
        // 因此增强模式也只换描边、不改红色填充。
        // DrawScrn.pas:306 的 `not actor.m_boDeath` → 死亡/尸体一根都不画。
        // 显示条件（DrawScrn.pas:305-307）：
        //   if g_boShowRedHPLable then actor.m_boOpenHealth := True;
        //   if (actor.m_boOpenHealth or actor.m_noInstanceOpenHealth) and not m_boDeath
        // 即「客户端设置里勾了显示血条」→ 全部显示；没勾 → 只有被心灵启示(28)
        // 打到 openHealth 的目标显示。g_boShowRedHPLable 原版缺省 False
        // （ClMain.pas:7005），这里默认开以保持现有观感；系统菜单可切回原版时机。
        const showHp = (hud.showHpLabel !== false) || e.isSelf
            || (e.openHealth > 0 && e.openHealth > Date.now());
        const dead = e.dead || e.hp <= 0;
        // NPC 不参与战斗，增强模式下不画头顶血条（避免商人头顶一根红条）。
        if (showHp && e.maxHp > 0 && !dead && !(enhanced && e.type === 'npc')) {
            const hp = Math.max(0, e.hp != null ? e.hp : e.maxHp);
            // 与血球同一取整口径：先算百分比整数，再换算像素，避免两边四舍五入不一致
            const pct = Math.max(0, Math.min(100, Math.round(hp * 100 / e.maxHp)));
            const bx0 = anchorX + TILE_W / 2 - 20;
            const by0 = anchorY - 57;
            ctx.fillStyle = '#000';
            ctx.fillRect(bx0 - 1, by0 - 1, 42, 6);
            if (enhanced) {
                const border = e.isSelf ? '#ffffff' : (pet ? '#7bd88f' : null);
                if (border) {
                    ctx.strokeStyle = border;
                    ctx.lineWidth = 1;
                    ctx.strokeRect(bx0 - 1.5, by0 - 1.5, 43, 7);
                }
            }
            ctx.fillStyle = '#ef4444';
            if (pct > 0) ctx.fillRect(bx0, by0, 40 * pct / 100, 4);
        }

        // 名字（血条上方：格顶 - 71，与血条 -57 一起都在头顶上方）
        // hoverNamesOnly 打开时对齐原版：只画悬停目标 + 悬停/移动中的自己。
        // 怪物名字后缀 Lv 等级（用户 2026-11-18；玩家/NPC 不加，保持原版）。
        let dispName = e.name;
        if (e.name && e.type === 'monster' && e.level > 0) {
            dispName = `${e.name} Lv${e.level}`;
        }
        if (dispName && !(hud.hoverNamesOnly && !e.isFocus && !e.isSelf)) {
            ctx.font = '11px SimSun,serif';
            ctx.textAlign = 'center';
            let tw = this._nameW.get(dispName);
            if (tw == null) {
                tw = ctx.measureText(dispName).width;
                if (this._nameW.size > 400) this._nameW.clear();   // 防无敌名字撑爆
                this._nameW.set(dispName, tw);
            }
            const nx = anchorX + TILE_W / 2;
            const ny = anchorY - 71;
            ctx.fillStyle = 'rgba(0,0,0,0.6)';
            ctx.fillRect(nx - tw / 2 - 3, ny - 11, tw + 6, 13);
            // 宠物名字颜色随等级（调色板索引取色，原版 GetThisCharColor/SlaveColors）
            // 这一条不受增强开关影响，保持原版行为。
            let nameFill = e.isSelf ? '#ffe9b0' : '#e0d0a0';
            // 名字颜色是原版核心信息（白/黄/红/灰名 + 关系色），对所有玩家可见，
            // 不受「增强模式」开关影响。旧实现只在 enhanced=true 时着色，默认
            // 关闭时黄名/红名全显示成同一米色——PK 状态完全看不见。
            if ((pet || e.type === 'player') && e.nameColor != null) {
                const idx = Number(e.nameColor);
                // 增强模式下自己用金黄（251），但红名/黄名/灰名等 PK 色优先，
                // 不能把危险状态盖掉（原版自己非 PK 时也是默认色，这里沿用美术习惯）。
                if (e.isSelf && !enhanced && idx === 255) {
                    nameFill = '#ffe9b0';
                } else {
                    nameFill = paletteFill(idx) || nameFill;
                }
            }
            ctx.fillStyle = nameFill;
            ctx.fillText(dispName, nx, ny);
        }

        // 头顶说话气泡（原版 DrawScrn.pas:331-352 / Actor.pas:2365-2420 TActor.Say）：
        // 白字 + 黑描边（BoldTextOut(..., clWhite, clBlack, ...)），死亡灰字；
        // 最多 5 行（MAXSAY=5）、行距 14px、单行最大宽 150px（MAXWIDTH），
        // 显示在名字上方；SayTime 4 秒内有效，超时清掉。无贴图框、无尾巴。
        if (e.say) {
            const lines = this._wrapSay(e.say, 150);
            const baseY = anchorY - 71 - 16;   // 名字(ny=-71)再往上 16px 起
            ctx.font = '12px SimSun,serif';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'top';
            for (let i = 0; i < lines.length; i++) {
                const ly = baseY - (lines.length - 1 - i) * 14;
                ctx.lineWidth = 2;
                ctx.strokeStyle = '#000';
                ctx.strokeText(lines[i], anchorX + TILE_W / 2, ly);
                ctx.fillStyle = dead ? '#9a9a9a' : '#ffffff';
                ctx.fillText(lines[i], anchorX + TILE_W / 2, ly);
            }
            ctx.textBaseline = 'alphabetic';   // 复原，避免影响后续绘制
        }
    }

    /** 把一句话按最大宽度折成最多 5 行（超长末尾截略，对齐原版 MAXSAY）。 */
    _wrapSay(text, maxWidth) {
        const chars = [...String(text)];
        const lines = [];
        let cur = '';
        for (const ch of chars) {
            const next = cur + ch;
            if (ctxWidth(next) > maxWidth && cur) {
                lines.push(cur);
                cur = ch;
                if (lines.length === 5) break;
            } else {
                cur = next;
            }
        }
        if (lines.length < 5 && cur) lines.push(cur);
        return lines;
    }
}

/** 中文字符宽度按 12px 全角估算（气泡折行只求近似，原版用 TextWidth）。 */
function ctxWidth(s) {
    let w = 0;
    for (const ch of s) w += /[\u2e80-\uffff]/.test(ch) ? 12 : 6;
    return w;
}
