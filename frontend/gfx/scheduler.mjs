// 全局 HTTP Range 请求调度器（单例）
// 所有 WIL 库共享，最多 MAX_CONCURRENT 个并发 fetch（先到先得）。
// 去重：若某请求的字节范围已被「进行中」的请求完全覆盖，则复用其数据（不占并发额度），
//       空出的额度可以让给后续请求。
// 持久缓存：三级——OPFS 整文件（无体积上限，装得下 1.2GB 全量素材）→ IndexedDB
//       分片（384MB，OPFS 不可用时的兜底）→ 网络。206 响应不进浏览器 HTTP 缓存，
//       没有这几层的话，每次打开页面都会把几百个 512KB 分片重新下载一遍。

import { assetCache } from './assetCache.mjs';
import { opfsGet, opfsPrefetch } from './opfsStore.mjs';

// 并发上限跟着「浏览器每域名 6 条 HTTP/1.1 连接」走：
// 大于 6 只是把请求塞进浏览器自己的队列，调度器就失去了插队能力——进游戏时
// 优先下载的地面纹理排在一堆登录阶段的大图（开门动画 10×480KB）后面，
// 实测 60KB/s 下地面 15 秒一张都没解出来。
const MAX_CONCURRENT = 8;

// 网络失败自动重试（用户 2026-11-23「载入失败地图/动作显示不出来」）：
// 瞬时抖动（隧道断连、服务器重启、弱网丢包）重试 2 次、指数退避；
// abortPending 掐断的不重试（那是要给地面让路，调用方按 null 兜底、之后自然重发）。
const MAX_RETRY = 2;
const RETRY_BASE_MS = 400;

/**
 * 应用层协商的分片解压。WIL 虽是二进制，但 8bit 调色板 + 大片透明区让
 * 512KB 分片能压到 250~320KB（公网实测 1.7~2.1x），这是慢网下最划算的优化。
 * 服务端只在收到 X-Jootm2-Gzip:1 时才压缩并在响应里回同一个头；旧服务端
 * 不回这个头，这里就原样使用，自动兼容。
 */
async function inflateRange(buf) {
    if (typeof DecompressionStream === 'undefined') return buf;
    const stream = new Blob([buf]).stream().pipeThrough(new DecompressionStream('gzip'));
    return new Response(stream).arrayBuffer();
}

class RangeScheduler {
    constructor() {
        this.active = [];   // { url, start, end, consumers: [{start,end,resolve,reject}] }
        this.queue = [];    // { url, start, end, resolve, reject }
        this.totalRequests = 0;    // 实际发起的 fetch 数（调试统计）
        this.dedupedRequests = 0;  // 被去重复用的请求数（调试统计）
        this.cacheHits = 0;        // 命中 IndexedDB 的分片数
        this.opfsHits = 0;         // 命中 OPFS 整文件的分片数（零网络）
        this.retries = 0;          // 网络失败自动重试次数
        this.compressedResponses = 0;  // 走应用层 gzip 的分片数
        this.savedBytes = 0;      // gzip 省下的传输字节
        this.bytesDown = 0;       // 实际传输字节（含 gzip 压缩后）
        this.bytesPlain = 0;      // 解压后字节（= 真正进内存的素材量）
    }

    /**
     * 调度一个字节范围请求。
     * @returns {Promise<{start:number, buffer:ArrayBuffer, total:number}>} start 为实际数据起始偏移
     */
    schedule(url, start, end, priority = false) {
        // 1. 去重：若图片偏移 start 已落在「进行中」请求的 chunk 内，复用其数据
        //    （该 chunk 覆盖了 start，图片可直接从其中解包，无需再发请求）
        for (const a of this.active) {
            // 必须**完整覆盖** [start, end) 才能复用：调用方按返回的 start 换算
            // 每张图在 buffer 里的位置，缺尾巴会解出半张图（花屏）。
            if (a.url === url && a.start <= start && end <= a.end) {
                this.dedupedRequests++;
                return new Promise((resolve, reject) => {
                    a.consumers.push({ start, end, resolve, reject });
                });
            }
        }
        // 2. 入队等待。priority=true 插到队首：进图首屏的地面纹理必须压过
        //    后台预载/UI 图标的请求，否则慢网下「地面」会排到最后才下。
        return new Promise((resolve, reject) => {
            const item = { url, start, end, resolve, reject };
            if (priority) this.queue.unshift(item);
            else this.queue.push(item);
            this._pump();
        });
    }

    _pump() {
        while (this.active.length < MAX_CONCURRENT && this.queue.length > 0) {
            const req = this.queue.shift();
            this._start(req);
        }
    }

    _start(req) {
        const item = {
            url: req.url,
            start: req.start,
            end: req.end,
            ctrl: typeof AbortController !== 'undefined' ? new AbortController() : null,
            consumers: [{ start: req.start, end: req.end, resolve: req.resolve, reject: req.reject }],
        };
        // 必须在任何 await 之前登记，否则同范围的并发请求会重复发起
        this.active.push(item);
        this._run(item);
    }

    async _run(item) {
        try {
            // 一级：OPFS 整文件命中 → 本地 slice，零网络（二次打开页的主路径）。
            // Range 语义 [start, end] 闭区间 → Blob.slice 要 end+1。
            const fileP = opfsGet(item.url);
            if (fileP) {
                const file = await fileP;
                if (file && item.start < file.size) {
                    const end = Math.min(item.end, file.size - 1);
                    const buffer = await file.slice(item.start, end + 1).arrayBuffer();
                    this.opfsHits++;
                    this._resolve(item, { start: item.start, buffer, total: file.size });
                    return;
                }
            }
            // 二级：IndexedDB 分片命中
            const key = assetCache.key('R', item.url, item.start, item.end);
            const hit = assetCache.ready ? await assetCache.get(key) : null;
            if (hit && hit.buf) {
                this.cacheHits++;
                this._resolve(item, { start: item.start, buffer: hit.buf, total: hit.total || 0 });
                return;
            }

            this.totalRequests++;
            const headers = { Range: `bytes=${item.start}-${item.end}` };
            if (typeof DecompressionStream !== 'undefined') headers['X-Jootm2-Gzip'] = '1';
            let resp = null;
            let lastErr = null;
            // 三级：网络（瞬时失败自动重试；abortPending 掐断不重试——那是主动
            // 让路，重发只会再被掐，消费方按 null 兜底后自然会再次请求）。
            for (let attempt = 0; attempt <= MAX_RETRY; attempt++) {
                try {
                    resp = await fetch(item.url,
                        item.ctrl ? { headers, signal: item.ctrl.signal } : { headers });
                    if (resp.ok || resp.status === 206) break;
                    lastErr = new Error(`HTTP ${resp.status}`);
                    resp = null;
                } catch (e) {
                    if (item.ctrl && item.ctrl.signal.aborted) throw e;  // 掐断：直接上抛
                    lastErr = e;
                    resp = null;
                }
                if (attempt < MAX_RETRY) {
                    this.retries = (this.retries || 0) + 1;
                    await new Promise(r => setTimeout(r, RETRY_BASE_MS * (1 << attempt)));
                }
            }
            if (!resp) throw lastErr || new Error('fetch failed');
            // 从 Content-Range 提取文件总大小（"bytes 0-1023/267372" -> 267372）
            let total = 0;
            const cr = resp.headers.get('content-range');
            if (cr) {
                const m = cr.match(/\/(\d+)\s*$/);
                if (m) total = parseInt(m[1], 10);
            }
            let buffer = await resp.arrayBuffer();
            this.bytesDown += Number(resp.headers.get('content-length')) || buffer.byteLength;
            if (resp.headers.get('x-jootm2-gzip') === '1') {
                const wire = Number(resp.headers.get('content-length')) || buffer.byteLength;
                buffer = await inflateRange(buffer);
                this.compressedResponses = (this.compressedResponses || 0) + 1;
                this.savedBytes = (this.savedBytes || 0)
                    + Math.max(0, buffer.byteLength - wire);
            }
            this.bytesPlain += buffer.byteLength;
            assetCache.set(key, { buf: buffer, total }, buffer.byteLength);
            // 后台排队把整个文件抓进 OPFS（串行 + 延迟，不抢首屏带宽）。
            // 首帧仍走这里的分片结果，预取只服务后续与下次会话。
            opfsPrefetch(item.url);
            this._resolve(item, { start: item.start, buffer, total });
        } catch (err) {
            for (const c of item.consumers) c.reject(err);
        } finally {
            const i = this.active.indexOf(item);
            if (i >= 0) this.active.splice(i, 1);
            this._pump();
        }
    }

    _resolve(item, value) {
        for (const c of item.consumers) c.resolve(value);
    }

    /**
     * 掐掉所有在飞 + 排队中的请求。
     *
     * 进游戏时调用：此刻管道里塞的是登录/选角的大图（开门动画 10 帧 × 800×600，
     * 约 4.8MB），它们会把「进图首屏要用的地面纹理」堵在后面——实测 60KB/s 下
     * 地面 15 秒一张都没解出来，玩家看到的是一片兜底暗色（黑条/黑块）。
     * 这些图已经下完的留在 IndexedDB 里，掐掉的将来再从缓存/网络取，不会丢东西。
     */
    abortPending(reason = 'aborted') {
        const n = this.active.length + this.queue.length;
        if (!n) return 0;
        for (const a of this.active) {
            try { a.ctrl && a.ctrl.abort(); } catch { /* 已结束 */ }
            for (const c of a.consumers) c.reject(new Error(reason));
        }
        this.active.length = 0;
        for (const q of this.queue) q.reject(new Error(reason));
        this.queue.length = 0;
        this.aborted = (this.aborted || 0) + n;
        return n;
    }

    stats() {
        return {
            active: this.active.length, queue: this.queue.length,
            total: this.totalRequests, totalRequests: this.totalRequests,
            deduped: this.dedupedRequests, cacheHits: this.cacheHits,
            opfsHits: this.opfsHits, retries: this.retries || 0,
            bytesDown: this.bytesDown, bytesPlain: this.bytesPlain,
            gzip: this.compressedResponses, gzipSaved: this.savedBytes,
        };
    }
}

export const scheduler = new RangeScheduler();
