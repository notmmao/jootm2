// WIL 图片库加载 + 纹理缓存（对应旧 WIL.pas）
// 8 位索引色 + 256 色调色板 → RGBA ImageData → 离屏 canvas 缓存
// 加载：经全局调度器（5 并发 + 去重）拉取 512K 片段，一次性解包范围内所有图缓存。

import { PALETTE_RGBA, widthBytes, skipBytes } from './palette.mjs';
import { scheduler } from './scheduler.mjs';
import { assetCache } from './assetCache.mjs';

// 单个请求的字节范围不再固定 512KB，而是「这张图自己的字节」+ 向后合并相邻图。
// 原因（2026-11-12 实测）：一屏地面约 231 张图，真实字节只有 1MB 上下，
// 但按 512KB 分片对齐会拉下 20MB 量级（实测慢网下每张图摊到 75KB，约 12 倍过量）。
// 200KB/s 的公网上，那意味着「进图 20 秒，地面还是一片兜底暗色」——用户反馈的
// 黑条/灰条就是它。改成精确区间并把间隔 ≤ GAP 的图并成一段后，
// 一屏地面只需 1MB 左右、几个请求。
const MERGE_GAP = 8 * 1024;     // 相邻两张图之间空隙小于这个值就合并进同一个请求
const RANGE_SLACK = 512;        // 每段末尾多要一点字节：个别 WIL 图数据比「下一张偏移」长出几字节

// 创建纹理画布。
// 主线程一律用 HTMLCanvasElement：OffscreenCanvas 的 2D 上下文在部分 Chromium 环境
// （无头 / GPU 读回异常）里 convertToBlob() 会抛 NotReadableError，且该失败不可重试，
// 表现为 UI 按钮贴图整片空白。HTML canvas 的 toDataURL() 读回稳定，同样可作为
// drawImage 的源，性能等价；仅在没有 document 的 worker 里退回 OffscreenCanvas。
function createCanvas(w, h) {
    if (typeof document !== 'undefined') {
        const c = document.createElement('canvas');
        c.width = w;
        c.height = h;
        return c;
    }
    return new OffscreenCanvas(w, h);
}

// ---- 网点阴影 → 半透明阴影（画质增强，不改资源） -------------------------------
// 1.80 客户端是 8 位色，没有半透明：人物/怪物/NPC/建筑的阴影用调色板 18 号 (16,8,8)
// 或 52 号 (8,0,0) 与透明像素交替的「棋盘格网点」模拟（2026-11-19 抽样统计：Hum/Mon/npc
// 全是 18，Objects* 全是 52）。800×600 CRT 上网点会被视觉混合，放大 1.5~2.5 倍后就成了
// 一片清晰的网格。解码时把「严格棋盘格」像素连同中间的透明孔替换成同等平均暗度的
// 半透明黑（覆盖率 50% → alpha≈50%）。
// 魔法/特效库按 lighter 叠加绘制，近黑像素本来就不显示，不处理。
// 关闭：localStorage.setItem('jootm2.smoothShadow','0') 后刷新。
const SHADOW_CI_A = 18;
const SHADOW_CI_B = 52;
const SHADOW_ALPHA = 128;
const SHADOW_LIB_RE = /(^|\/)(hum\d*|mon\d*|npc\d*|objects\d*|weapon\d*|hair\d*|tiles\d*|smtiles\d*|dnitems)\.wil$/i;
let smoothShadowOn = true;
try { smoothShadowOn = localStorage.getItem('jootm2.smoothShadow') !== '0'; } catch { /* worker/隐身 */ }

/** ci: 调色板索引（行优先，已是正向行序）；就地改写 rgba 中的网点阴影。 */
function smoothDitherShadow(ci, rgba, w, h) {
    const n = w * h;
    let mark = null;
    // 1) 严格棋盘格像素：阴影色，四邻全透明，且对角至少一个同色
    for (let y = 0; y < h; y++) {
        const row = y * w;
        for (let x = 0; x < w; x++) {
            const i = row + x;
            const c = ci[i];
            if (c !== SHADOW_CI_A && c !== SHADOW_CI_B) continue;
            if (x > 0 && ci[i - 1]) continue;
            if (x < w - 1 && ci[i + 1]) continue;
            if (y > 0 && ci[i - w]) continue;
            if (y < h - 1 && ci[i + w]) continue;
            const diag = (y > 0 && x > 0 && ci[i - w - 1] === c)
                || (y > 0 && x < w - 1 && ci[i - w + 1] === c)
                || (y < h - 1 && x > 0 && ci[i + w - 1] === c)
                || (y < h - 1 && x < w - 1 && ci[i + w + 1] === c);
            if (!diag) continue;
            if (!mark) mark = new Uint8Array(n);
            mark[i] = 1;
        }
    }
    if (!mark) return;
    // 1b) 贴着物体本体的网点边缘（四邻有一侧是实体像素，严格判定漏掉）：
    //     同为阴影色、与已确认网点对角相连、且四邻至少两个透明 → 也算网点。
    //     迭代两轮，沿栅栏/墙脚这类长边逐步扩散。
    for (let pass = 0; pass < 2; pass++) {
        let grew = false;
        for (let y = 0; y < h; y++) {
            const row = y * w;
            for (let x = 0; x < w; x++) {
                const i = row + x;
                if (mark[i]) continue;
                const c = ci[i];
                if (c !== SHADOW_CI_A && c !== SHADOW_CI_B) continue;
                const diag = (y > 0 && x > 0 && mark[i - w - 1] === 1)
                    || (y > 0 && x < w - 1 && mark[i - w + 1] === 1)
                    || (y < h - 1 && x > 0 && mark[i + w - 1] === 1)
                    || (y < h - 1 && x < w - 1 && mark[i + w + 1] === 1);
                if (!diag) continue;
                let t = 0;
                if (x === 0 || !ci[i - 1]) t++;
                if (x === w - 1 || !ci[i + 1]) t++;
                if (y === 0 || !ci[i - w]) t++;
                if (y === h - 1 || !ci[i + w]) t++;
                if (t >= 2) { mark[i] = 1; grew = true; }
            }
        }
        if (!grew) break;
    }
    // 2) 网点中间的透明孔：四邻里至少 3 个是网点（边缘 2 个）→ 同属阴影
    for (let y = 0; y < h; y++) {
        const row = y * w;
        for (let x = 0; x < w; x++) {
            const i = row + x;
            if (ci[i] || mark[i]) continue;
            let k = 0, m = 0;
            if (x > 0) { m++; if (mark[i - 1] === 1) k++; }
            if (x < w - 1) { m++; if (mark[i + 1] === 1) k++; }
            if (y > 0) { m++; if (mark[i - w] === 1) k++; }
            if (y < h - 1) { m++; if (mark[i + w] === 1) k++; }
            if (k >= 3 || (m < 4 && k >= 2)) mark[i] = 2;
        }
    }
    for (let i = 0; i < n; i++) {
        if (!mark[i]) continue;
        const o = i << 2;
        rgba[o] = 0; rgba[o + 1] = 0; rgba[o + 2] = 0; rgba[o + 3] = SHADOW_ALPHA;
    }
}

export class WIL {
    constructor(wilUrl, wixUrl) {
        this.wilUrl = wilUrl;
        this.wixUrl = wixUrl;
        this.smoothShadow = smoothShadowOn && SHADOW_LIB_RE.test(String(wilUrl).split('?')[0]);
        this.imageCount = 0;
        this.offsetList = [];        // 每图在 wil 的偏移（0=空图）
        this.sortedOffsets = [];     // 非空图偏移（递增），用于 chunk 边界对齐 + 二分查找
        this.fileSize = 0;           // wil 文件总大小（首个 fetch 的 Content-Range 获取）
        this.indexLoaded = false;
        this._indexLoading = null;   // 防重复加载索引
        this.textures = new Map();   // index -> { canvas, px, py, width, height }
        this.lastUse = new Map();    // index -> 最后使用时间戳
        this.memCheckTime = Date.now();
        // 偏移表按偏移升序（[{off,index}]）：分片边界对齐与末张图的位深判定都要用它。
        // 注意不要假设 WIX 偏移表本身有序，加载时显式排序后派生 sortedOffsets。
        this.byOff = [];
    }

    // 加载 WIX 索引（防并发重复）
    loadIndex() {
        if (this.indexLoaded) return Promise.resolve();
        if (this._indexLoading) return this._indexLoading;
        this._indexLoading = (async () => {
            // WIX 索引走持久缓存：虽然浏览器能缓存 200 响应，但公网上仍要一个
            // RTT 做校验；索引只有几 KB，命中后连这个 RTT 都省掉。
            const key = assetCache.key('WIX', this.wixUrl);
            const hit = assetCache.ready ? await assetCache.get(key) : null;
            let buf;
            if (hit && hit.buf) {
                buf = hit.buf;
            } else {
                const resp = await fetch(this.wixUrl);
                if (!resp.ok) throw new Error(`加载 ${this.wixUrl} 失败`);
                buf = await resp.arrayBuffer();
                assetCache.set(key, { buf }, buf.byteLength);
            }
            const view = new DataView(buf);
            this.imageCount = view.getUint32(44, true);
            for (let i = 0; i < this.imageCount; i++) {
                const off = view.getUint32(48 + i * 4, true);
                this.offsetList[i] = off < 1080 ? 0 : off;
                if (off >= 1080) this.byOff.push({ off, index: i });
            }
            // WIX 的偏移表通常是升序，但不要假设：显式排序后再派生 sortedOffsets
            this.byOff.sort((a, b) => a.off - b.off);
            this.sortedOffsets = this.byOff.map(e => e.off);
            this.indexLoaded = true;
        })();
        return this._indexLoading;
    }

    // 释放超时缓存（>5 分钟未用）
    freeOldTextures() {
        const now = Date.now();
        for (const [index, last] of this.lastUse) {
            if (now - last > 5 * 60 * 1000) {
                this.textures.delete(index);
                this.lastUse.delete(index);
            }
        }
    }

    // 懒加载获取纹理（返回 {canvas, px, py, width, height} 或 null）
    async getTexture(index) {
        if (this.textures.has(index)) {
            this.lastUse.set(index, Date.now());
            return this.textures.get(index);
        }
        await this.loadIndex();
        if (index < 0 || index >= this.imageCount) return null;
        const off = this.offsetList[index];
        if (off === 0) return null;   // 空图

        // 定时释放缓存（对应旧 memchecktime / FreeOldMemorys）
        const now = Date.now();
        if (now - this.memCheckTime > 10 * 1000) {
            this.memCheckTime = now;
            this.freeOldTextures();
        }

        // 只拉这张图自己的字节（[off, nextOff)）。旧实现按 512KB 分片对齐，
        // 为了解一张 6KB 的地砖会下载 512KB——慢网下一屏地面要 20MB，正是
        // 「进入游戏后地面长时间是暗色条带」的直接原因。
        const end = this._nextOff(off) + RANGE_SLACK;
        let packed;
        try {
            packed = await scheduler.schedule(this.wilUrl, off, end);
        } catch (e) {
            // 请求被掐断（进游戏时 abortPending）/ 网络失败：返回 null 由调用方兜底。
            // 不要把异常抛给一堆 `getTexture().then()` 调用点——那里多半没有 catch，
            // 会变成 "Uncaught (in promise)" 噪音，且让该格永远停在加载中。
            return null;
        }
        const { start, buffer, total } = packed;
        if (total > this.fileSize) this.fileSize = total;

        // **只解当前需要的那一张**（单张 0.1~1ms）。旧实现在每个分片到达时同步解包
        // 块内全部图（一个 512KB 分片常含上百张），20 个并发分片同时到达就是几十到
        // 几百毫秒的主线程停顿——进图/走路时的掉帧、以及「本地也卡」的根因。
        // 现在不预解无关图：既省主线程也省 canvas 内存与 GC（绝大多数图永远不会被用到）。
        const view = new DataView(buffer);
        const tex = this._unpackOne(view, off - start, index);
        if (tex) {
            this.textures.set(index, tex);
            this.lastUse.set(index, Date.now());
        }
        return this.textures.get(index) || null;
    }

    /** 严格意义上的「下一张非空图起点」：本图的字节范围 = [off, _nextOff(off))。 */
    _nextOff(off) {
        const arr = this.sortedOffsets;
        let lo = 0, hi = arr.length;
        while (lo < hi) {
            const mid = (lo + hi) >> 1;
            if (arr[mid] <= off) lo = mid + 1;
            else hi = mid;
        }
        if (lo < arr.length) return arr[lo];
        return this.fileSize > 0 ? this.fileSize : off + 8 + 48 * 32;
    }

    /** 批量预取：按字节相邻关系合并成尽量少的请求，到一段解一段。 */
    async prefetch(indices, onTex = null, priority = false) {
        if (!indices || !indices.length) return 0;
        await this.loadIndex();
        const want = [];
        const seen = new Set();
        for (const i of indices) {
            if (i == null || i < 0 || i >= this.imageCount) continue;
            if (seen.has(i)) continue;
            seen.add(i);
            // 已经解过的图也要回调：调用方（MapActor._texCache）可能刚建了空占位，
            // 若这里静默跳过，那一格就会永远停在「加载中」——用户看到的就是
            // 「走一段路后整片地面停在借来的像素上不再更新」。
            if (this.textures.has(i)) {
                if (onTex) onTex(i, this.textures.get(i));
                continue;
            }
            const off = this.offsetList[i];
            if (!off) continue;
            want.push(i);
        }
        if (!want.length) return 0;
        want.sort((a, b) => this.offsetList[a] - this.offsetList[b]);

        // 合并相邻图：gap ≤ MERGE_GAP 视为同一段（地图瓦片在 WIL 里通常连续存放）
        const runs = [];
        for (const i of want) {
            const start = this.offsetList[i];
            const end = this._nextOff(start) + RANGE_SLACK;
            const last = runs[runs.length - 1];
            if (last && start - last.end <= MERGE_GAP) {
                last.items.push(i);
                if (end > last.end) last.end = end;
            } else {
                runs.push({ items: [i], start, end: Math.max(end, start + 1) });
            }
        }

        let loaded = 0;
        // 并发交给 scheduler 控制；每段内部逐张解包，解 8 张让一次主线程
        await Promise.all(runs.map(async (run) => {
            try {
                const { start, buffer, total } = await scheduler.schedule(this.wilUrl, run.start, run.end, priority);
                // 服务端会把超出文件末尾的区间钳到 EOF：这里按钳后的终点校验覆盖，
                // 否则末尾那一段会被整段丢弃，那几张图永远停在「加载中」。
                const effEnd = total > 0 ? Math.min(run.end, total) : run.end;
                if (start + buffer.byteLength < effEnd) return;
                const view = new DataView(buffer);
                let sliceAt = performance.now();
                for (let k = 0; k < run.items.length; k++) {
                    const i = run.items[k];
                    if (!this.textures.has(i)) {
                        const tex = this._unpackOne(view, this.offsetList[i] - start, i);
                        if (tex) {
                            this.textures.set(i, tex);
                            this.lastUse.set(i, Date.now());
                            loaded++;
                            if (onTex) onTex(i, tex);
                        }
                    }
                    // 按「时间片」让出主线程，而不是每 8 张：
                    // 解一张 800×600 的对象图要 3~5ms（480K 像素逐个查调色板），
                    // 连续解 8 张就是 30ms 的掉帧——走路时「一顿一顿」的来源。
                    if (performance.now() - sliceAt > 6) {
                        await new Promise(r => setTimeout(r, 0));
                        sliceAt = performance.now();
                    }
                }
            } catch (e) {
                /* 单段失败不影响其余段；缺的图后续 getTexture 会按需重试 */
            }
        }));
        return loaded;
    }

    // 解包单张图（图头在 buffer 的 localOff 处）
    _unpackOne(view, localOff, index) {
        let p = localOff;
        if (p + 8 > view.byteLength) return null;
        const width = view.getUint16(p, true); p += 2;
        const height = view.getUint16(p, true); p += 2;
        const px = view.getInt16(p, true); p += 2;
        const py = view.getInt16(p, true); p += 2;

        if (width <= 0 || height <= 0) return null;
        if (height === 1) return null;   // 1x1 占位图，旧代码跳过（视为空）

        // 位深：下一图偏移 - 本图偏移 判定（8 位或 16 位 RGB565）
        const fileOff = this.offsetList[index];
        // 末张图无 nextOff，必须用文件真实剩余字节（this.fileSize）判定位深；
        // 若退回 "fileOff + 8 + width*height" 会永远判 8 位，末张若是 16 位 RGB565 将只读半截而花屏。
        // 下一张非空图的起点：不能用 offsetList[index+1]（可能是 0=空图），
        // 否则会退化到「文件末尾」，把 8 位图误判成 16 位 RGB565（花屏/错色）。
        const nextOff = this._nextOff(fileOff);
        const bitCount = (nextOff - fileOff >= 8 + width * height * 2) ? 16 : 8;

        // 数据不足则跳过
        const dataLen = bitCount === 8 ? width * height : width * height * 2;
        if (p + dataLen > view.byteLength) return null;

        const rgba = new Uint8Array(width * height * 4);

        if (bitCount === 8) {
            const pixelLength = nextOff - fileOff - 8;
            const padding = pixelLength !== width * height && pixelLength === widthBytes(8 * width) * height;
            const ciBuf = this.smoothShadow ? new Uint8Array(width * height) : null;
            for (let h = height - 1; h >= 0; h--) {
                for (let w = 0; w < width; w++) {
                    if (padding && w === 0) p += skipBytes(8, width);
                    const ci = view.getUint8(p++);
                    const base = ci << 2;
                    if (ciBuf) ciBuf[w + h * width] = ci;
                    const idx = (w + h * width) << 2;
                    rgba[idx]     = PALETTE_RGBA[base];
                    rgba[idx + 1] = PALETTE_RGBA[base + 1];
                    rgba[idx + 2] = PALETTE_RGBA[base + 2];
                    rgba[idx + 3] = PALETTE_RGBA[base + 3];
                }
            }
            if (ciBuf) smoothDitherShadow(ciBuf, rgba, width, height);
        } else {
            // 16 位 RGB565
            for (let h = height - 1; h >= 0; h--) {
                for (let w = 0; w < width; w++, p += 2) {
                    const pdata = view.getUint16(p, true);
                    const r = (pdata & 0xf800) >> 8;
                    const g = (pdata & 0x7e0) >> 3;
                    const b = (pdata & 0x1f) << 3;
                    const idx = (w + h * width) << 2;
                    rgba[idx]     = r;
                    rgba[idx + 1] = g;
                    rgba[idx + 2] = b;
                    rgba[idx + 3] = (r === 0 && g === 0 && b === 0) ? 0 : 255;
                }
            }
        }

        const canvas = createCanvas(width, height);
        const ctx = canvas.getContext('2d');
        ctx.putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
        return { canvas, px, py, width, height };
    }
}
