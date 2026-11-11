// 地图渲染器（canvas 2d，对应旧 MapActor / PlayScn.pas 渲染语义）
// 等距瓦片 48×32，图层：大地砖 tiles → 小地砖 smtiles → 实体/对象（按行遮挡）。
// 绘制语义照原版 PlayScn.pas：
//   - 普通对象：格子左上对齐，底对齐（y = 格顶 + 32 - h）
//   - 混合光效（blend）：x = 格左 + px - 2，y = 格顶 + py - 68（GetObjsEx 的 ax/ay = WIL px/py）
//   - 名字/血条：贴 sprite 顶部（取各层最小 py）
// 平滑卷动：render 接收 shiftX/shiftY（玩家移动插值），坐标收口在 screenX/screenY。

import { loadMap } from './Maps.mjs';
import { getLib } from '../image/Images.mjs';
import { PALETTE_RGBA } from '../gfx/palette.mjs';

const TILE_W = 48;
const TILE_H = 32;
// 地面缓存未就绪/加载失败时的兜底底色（暗土色）。纯黑/近黑会被看成「一条一条的黑条」，
// 这里用明显亮于纯黑的地色，并且优先借用已绘制格像素，保证任何时刻都不会露出黑条带。
export const FLOOR_FALLBACK = '#3a3222';

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

    /** 预取首屏地面 / 对象（含动画对象的全部帧）纹理；6 秒超时兜底。 */
    async _prefetchVisible(m, cx, cy, onProgress) {
        const jobs = [];
        const seen = new Set();
        for (const { lib: libKey, index, key } of
             collectVisibleTextures(m, cx, cy, this.viewW, this.viewH)) {
            if (this._texCache.has(key)) continue;
            seen.add(key);
            const lib = getLib(libKey);
            if (!lib) continue;
            this._texCache.set(key, { canvas: null, px: 0, py: 0 });  // 占位，避免 render 重复发起
            jobs.push(lib.getTexture(index).then(tex => {
                const e = this._texCache.get(key);
                if (e && tex) {
                    e.canvas = tex.canvas; e.px = tex.px; e.py = tex.py;
                    this._texVersion++;
                } else if (e) {
                    e.failed = true; e.failedAt = Date.now();
                }
            }).catch(() => {
                const e = this._texCache.get(key);
                if (e) { e.failed = true; e.failedAt = Date.now(); }
            }));
        }

        const total = jobs.length;
        if (!total) return;
        let done = 0;
        const tracked = jobs.map(p => p.then(
            () => { if (onProgress && (++done % 8 === 0 || done === total)) onProgress(done, total); },
            () => { if (onProgress && (++done % 8 === 0 || done === total)) onProgress(done, total); },
        ));
        const result = await Promise.race([
            Promise.allSettled(tracked).then(() => 'done'),
            new Promise(resolve => setTimeout(() => resolve('timeout'), 6000)),
        ]);
        if (result === 'timeout') {
            // 冷缓存（首次登录）时首屏纹理常常超过 6 秒：这里只放行显示地图，
            // **绝不把未完成的占位标成 failed**。旧实现标 failed 会让 _floorTex 立刻
            // 删占位并重发请求，与仍在飞的原请求叠加成重复请求风暴，整片格子长时间
            // 拿不到图，兜底色连成横条纹；重登时 HTTP 缓存已热才「恢复正常」。
            // 原请求完成时上面 jobs 的 then 会直接写入同一个占位，无需重试。
            for (const k of seen) {
                const e = this._texCache.get(k);
                if (e && !e.canvas) e.pendingSince = Date.now();
            }
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
        // 冗余必须让窗口左上角落在屏幕外（DX/DY ≤ 0），否则屏幕左/上缘会露出黑底。
        // ox = floor((800-48)/2) = 376 → 左侧至少 8 格；oy = floor((445-32)/2) = 206 → 上侧至少 7 格。
        const PX = 10, PY = 8;
        const CW = this.viewW + PX * 2 * TILE_W;
        const CH = this.viewH + PY * 2 * TILE_H;
        const gw = Math.ceil(CW / TILE_W) + 1;
        const gh = Math.ceil(CH / TILE_H) + 1;
        // 对齐偶数格：tiles 是 96×64 的 2×2 大图，象限划分必须随窗口稳定
        const wx0 = (cx - PX - 2) & ~1;
        const wy0 = (cy - PY - 2) & ~1;
        const f = this._floor;
        if (f && f.gw === gw && f.gh === gh && f.wx0 === wx0 && f.wy0 === wy0) return f;

        const canvas = document.createElement('canvas');
        canvas.width = CW;
        canvas.height = CH;
        const fctx = canvas.getContext('2d', { alpha: false });
        fctx.fillStyle = FLOOR_FALLBACK;
        fctx.fillRect(0, 0, CW, CH);
        const cells = new Uint8Array(gw * gh);
        if (f && f.gw === gw && f.gh === gh) {
            const dgx = f.wx0 - wx0, dgy = f.wy0 - wy0;
            fctx.drawImage(f.canvas, dgx * TILE_W, dgy * TILE_H);
            for (let iy = 0; iy < f.gh; iy++) {
                for (let ix = 0; ix < f.gw; ix++) {
                    const nx = ix + dgx, ny = iy + dgy;
                    if (nx >= 0 && ny >= 0 && nx < gw && ny < gh) {
                        cells[ny * gw + nx] = f.cells[iy * f.gw + ix];
                    }
                }
            }
        }
        // sample 是「最近一格真实地砖」的 48×32 像素快照，与格子坐标解耦：
        // 滚动搬运后 lastPainted 的 ix/iy 会失效，sample 不会。整行未就绪时用它铺满，
        // 而不是退化成纯色（那正是用户截图里一条一条的横条纹）。
        this._floor = {
            canvas, ctx: fctx, wx0, wy0, gw, gh, cells,
            lastPainted: f && f.lastPainted,
            sample: (f && f.sample) || null,
        };
        return this._floor;
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
     * 未就绪的格子先借用相邻已绘制格的像素，避免纯黑条带；不置 cells=1，
     * 纹理到达后下一帧会用真实内容覆盖。canvas 允许以自身为源 drawImage。
     *
     * 增量预取按 24 格分批提交，会出现「连续一整行都还没就绪」的窗口：这时四个
     * 邻格也都没画，旧实现全部退化成纯色填充，连成一条近黑色横带（用户看到的
     * 「黑色条纹」）。因此邻格全失败时再从缓存里任意已绘制格借像素。
     */
    _borrowFloorCell(f, ix, iy, px, py) {
        const copy = (nx, ny) => {
            try {
                f.ctx.drawImage(f.canvas, nx * TILE_W, ny * TILE_H, TILE_W, TILE_H,
                                px, py, TILE_W, TILE_H);
                return true;
            } catch (e) { return false; }
        };
        const neighbours = [[ix - 1, iy], [ix, iy - 1], [ix + 1, iy], [ix, iy + 1]];
        for (const [nx, ny] of neighbours) {
            if (nx < 0 || ny < 0 || nx >= f.gw || ny >= f.gh) continue;
            if (!f.cells[ny * f.gw + nx]) continue;
            if (copy(nx, ny)) return;
            break;
        }
        // 整带未就绪：先试最近一次真正画过地砖的格子。
        const lp = f.lastPainted;
        if (lp && f.cells[lp.iy * f.gw + lp.ix] && copy(lp.ix, lp.iy)) return;
        // 坐标失效（滚动搬运过）或整窗口都还没画出来时，用与坐标无关的真实地砖快照。
        // 这样整行未就绪也只是重复铺一格地砖，不会连成纯色横条纹。
        if (f.sample) {
            f.ctx.drawImage(f.sample, px, py);
            return;
        }
        // 首帧连一格都没画出来才用底色，且颜色也明显亮于纯黑。
        f.ctx.fillStyle = FLOOR_FALLBACK;
        f.ctx.fillRect(px, py, TILE_W, TILE_H);
    }

    _paintFloorCells(m, f) {
        for (let iy = 0; iy < f.gh; iy++) {
            for (let ix = 0; ix < f.gw; ix++) {
                const idx = iy * f.gw + ix;
                if (f.cells[idx]) continue;
                const gx = f.wx0 + ix, gy = f.wy0 + iy;
                const px = ix * TILE_W, py = iy * TILE_H;
                if (gx < 0 || gy < 0 || gx >= m.width || gy >= m.height) {
                    // 地图外不再涂纯黑：靠边界走动时纯黑格会被看成一条条黑带，
                    // 统一用与未就绪格相同的土地兜底色。
                    f.ctx.fillStyle = FLOOR_FALLBACK;
                    f.ctx.fillRect(px, py, TILE_W, TILE_H);
                    f.cells[idx] = 1;
                    continue;
                }
                const col = m.tiles[gx];
                const t = col && col[gy];
                if (!t) { this._borrowFloorCell(f, ix, iy, px, py); continue; }
                const bgx = gx & ~1, bgy = gy & ~1;
                const bt = m.tiles[bgx] && m.tiles[bgx][bgy];
                const bng = this._floorTex(this._libKey('tiles', bt ? bt.bngFile : 0),
                                           bt ? bt.bng : -1);
                const mid = this._floorTex(this._libKey('smtiles', t.midFile), t.mid);
                if (!bng.ready || !mid.ready) {
                    this._borrowFloorCell(f, ix, iy, px, py);   // 原子：整格一起出现，但不留黑
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
                f.cells[idx] = 1;
                // 供 _borrowFloorCell 在「整行都未就绪」时借像素用
                if (bng.tex || mid.tex) {
                    f.lastPainted = { ix, iy };
                    if (!f.sample) {
                        f.sample = document.createElement('canvas');
                        f.sample.width = TILE_W;
                        f.sample.height = TILE_H;
                    }
                    f.sample.getContext('2d').drawImage(
                        f.canvas, px, py, TILE_W, TILE_H, 0, 0, TILE_W, TILE_H);
                }
            }
        }
    }

    _syncFloor(ctx, m, cx, cy, shiftX, shiftY) {
        const f = this._ensureFloor(cx, cy);
        this._paintFloorCells(m, f);
        const ox = Math.floor((this.viewW - TILE_W) / 2);
        const oy = Math.floor((this.viewH - TILE_H) / 2);
        ctx.drawImage(f.canvas,
            ox + (f.wx0 - cx) * TILE_W - shiftX,
            oy + (f.wy0 - cy) * TILE_H - shiftY);
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
        const halfW = Math.ceil(this.viewW / 2 / TILE_W) + 6;
        const halfH = Math.ceil(this.viewH / 2 / TILE_H) + 8;
        const r = {
            left: Math.max(0, Math.floor(cx - halfW)),
            top: Math.max(0, Math.floor(cy - halfH)),
            right: Math.min(m.width - 1, Math.ceil(cx + halfW)),
            bottom: Math.min(m.height - 1, Math.ceil(cy + halfH + 12)),
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
                    loads.push({ k, lib, index });
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
            await Promise.all(cells.slice(i, i + 24).map(loads =>
                Promise.all(loads.map(({ k, lib, index }) => {
                    // 写入放在原 promise 内部：8 秒超时只用于「不阻塞后续批次」，
                    // 绝不把仍在飞的请求判成 failed（否则会重发请求、与原请求互相
                    // 覆盖，冷缓存时整片格子长期拿不到图 → 横条纹）。原子性由
                    // _paintFloorCells 的「bng/mid 同时 ready 才画整格」保证。
                    const p = lib.getTexture(index).then(tex => {
                        const e = this._texCache.get(k);
                        if (e && tex) {
                            e.canvas = tex.canvas; e.px = tex.px; e.py = tex.py;
                            this._texVersion++;
                        } else if (e) {
                            e.failed = true; e.failedAt = Date.now();
                        }
                        return tex;
                    }).catch(() => {
                        const e = this._texCache.get(k);
                        if (e) { e.failed = true; e.failedAt = Date.now(); }
                        return null;
                    });
                    // 单个请求 8 秒兜底，避免某个挂住的 fetch 阻塞整批 / 后续批次
                    return Promise.race([
                        p,
                        new Promise(res => setTimeout(() => res(null), 8000)),
                    ]);
                })).then(() => { /* 缓存已由各 promise 自行写入 */ })
            ));
            // 让出一帧给渲染，避免解包 512K chunk 时长时间占着主线程
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
    render(ctx, cx, cy, entities, shiftX = 0, shiftY = 0, drawDrop = null, groundItems = null) {
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
        this._syncFloor(ctx, m, cx, cy, shiftX, shiftY);
        // 视口移出首屏预取范围前，提前把前方格子的全部纹理按格原子载入
        this._maybePrefetch(m, cx, cy);

        // 3. 实体 + 对象层按行交错（y 越大越靠屏幕下方，遮挡在前）
        const byRow = new Map();
        for (const e of entities) {
            if (!byRow.has(e.y)) byRow.set(e.y, []);
            byRow.get(e.y).push(e);
        }
        // 地面掉落物也按行调度（原版 PlayScn.pas 在 `for j` 行循环里画
        // DropedItemList：`if j = DropItem.y - BlockTop`）。以前在整帧末尾统一画，
        // 会盖在同格角色/树木/建筑之上，看起来像浮在半空、位置不对。
        const dropsByRow = new Map();
        if (drawDrop && groundItems) {
            for (const g of groundItems) {
                if (g.y < top || g.y > bottom) continue;
                if (!dropsByRow.has(g.y)) dropsByRow.set(g.y, []);
                dropsByRow.get(g.y).push(g);
            }
        }
        for (let gy = top; gy <= bottom; gy++) {
            // 对象层（照 PlayScn.pas：普通对象左上+底对齐；blend 光效 x=格左+px-2, y=格顶+py-68）
            for (let gx = left; gx <= right; gx++) {
                const t = m.tiles[gx][gy];
                if (t.obj < 0) continue;
                // 加色光效（blend，0x80 标志）与是否动画无关：静态火/光类贴图也必须用 lighter 混合，
                // 否则会丢失辉光（原版 DrawScrn 对 boBlend 一律加色，不论 ani&0x80）。
                let frame = 0;
                const blend = !!t.blend;
                if (t.hasAni) {
                    frame = Math.floor(Date.now() / 200) % Math.max(1, Math.min(32, t.aniFrame));
                }
                // 当前动画帧未就绪时回退该对象已加载的最近帧，绝不整棵树/建筑消失
                const got = this._getObjFrame(t, frame);
                const tex = got ? got.tex : null;
                if (tex) {
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
            }
            // 本行地面物品：先于实体绘制，后续行的对象/角色才能正确遮挡它
            const drops = dropsByRow.get(gy);
            if (drops) {
                for (const g of drops) drawDrop(ctx, g, sx(g.x), sy(g.y));
            }
            // 实体（照原版：锚点 = 格子左上角，图左上 = 格左+px, 格顶+py）
            const row = byRow.get(gy);
            if (row) {
                for (const e of row) {
                    // 锚点 = 格子左上 + 走动画偏移（self 用 _viewX 过渡，offsetX/Y 为像素差）
                    const anchorX = sx(e.x) + (e.offsetX || 0);
                    const anchorY = sy(e.y) + (e.offsetY || 0);
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
                                ctx.drawImage(img, anchorX + layer.px, anchorY + layer.py);
                            }
                        }
                        ctx.restore();
                    } else if (e.tex) {
                        ctx.drawImage(e.tex, anchorX - e.tex.width / 2, anchorY - e.tex.height);
                    }

                    // 血条（原版 DrawScrn：SayY - 10 = 格顶 - 57，头顶上方）
                    if (e.maxHp) {
                        const hp = Math.max(0, e.hp != null ? e.hp : e.maxHp);
                        const bx0 = anchorX + TILE_W / 2 - 20;
                        const by0 = anchorY - 57;
                        ctx.fillStyle = '#000';
                        ctx.fillRect(bx0 - 1, by0 - 1, 42, 6);
                        ctx.fillStyle = e.isSelf ? '#0ea5e9' : '#ef4444';
                        ctx.fillRect(bx0, by0, 40 * hp / e.maxHp, 4);
                    }

                    // 名字（血条上方：格顶 - 71，与血条 -57 一起都在头顶上方）
                    if (e.name) {
                        ctx.font = '11px SimSun,serif';
                        ctx.textAlign = 'center';
                        const tw = ctx.measureText(e.name).width;
                        const nx = anchorX + TILE_W / 2;
                        const ny = anchorY - 71;
                        ctx.fillStyle = 'rgba(0,0,0,0.6)';
                        ctx.fillRect(nx - tw / 2 - 3, ny - 11, tw + 6, 13);
                        // 宠物名字颜色随等级（调色板索引取色，原版 GetThisCharColor/SlaveColors）
                        let nameFill = e.isSelf ? '#ffe9b0' : '#e0d0a0';
                        if (e.nameColor != null) {
                            const i = e.nameColor * 4;
                            nameFill = `rgb(${PALETTE_RGBA[i]},${PALETTE_RGBA[i + 1]},${PALETTE_RGBA[i + 2]})`;
                        }
                        ctx.fillStyle = nameFill;
                        ctx.fillText(e.name, nx, ny);
                    }
                }
            }
        }
    }
}
