// 客户端素材持久缓存（IndexedDB）
//
// 为什么需要它：WIL 纹理走 HTTP Range 请求返回 206 Partial Content，
// Chromium 不会把 206 写进 HTTP 缓存——即使服务端给了 immutable 一年，
// 每次打开页面仍会重新下载几百个 512KB 分片。实测二次加载 .wil 命中 0/3。
// 这里把「已下载的字节块」按资源版本持久化，二次打开完全不走网络。
//
// 设计要点：
// - 版本命名空间：/api/asset-version 的指纹变化时清空旧缓存，换资源不会用到旧字节。
// - 只缓存不可变素材（WIL 分片 / WIX 索引 / .map），不缓存任何游戏状态或账号数据。
// - 容量上限 + 按时间淘汰，避免无限占用用户磁盘。
// - 任何异常（隐私模式、配额、IndexedDB 被禁）都静默降级为内存缓存 / 纯网络，
//   绝不阻塞游戏启动。

const DB_NAME = 'jootm2-assets';
const DB_VERSION = 1;
const STORE = 'chunks';
const META = 'meta';
const VERSION_KEY = 'assetVersion';
const BYTES_KEY = 'bytes';
const MAX_BYTES = 384 << 20;      // 384MB；超出后淘汰到 80%
const FLUSH_MS = 150;             // 合并短时间内的写，减少事务数
const MEM_MAX = 24 << 20;         // 降级/会话内内存缓存上限

class AssetCache {
    constructor() {
        this.db = null;
        this.version = 'default';
        this.disabled = false;
        this.ready = false;
        this.mem = new Map();      // 会话内一级缓存（同步命中，跨请求复用）
        this.memBytes = 0;
        this.bytes = 0;
        this.hits = 0;
        this.misses = 0;
        this.writes = 0;
        this.savedBytes = 0;      // 缓存命中累计省下的字节（调试用）
        this._queue = [];
        this._flushTimer = null;
        this._flushing = null;
        this._opening = null;
    }

    /** 启动阶段调用一次。version 为服务端素材指纹；失败也不影响游戏。 */
    async init(version = 'default') {
        this.version = String(version || 'default');
        if (typeof indexedDB === 'undefined') {
            this.disabled = true;
            this.ready = true;
            return this;
        }
        try {
            const db = await this._open();
            this.db = db;
            const saved = await this._metaGet(VERSION_KEY);
            if (saved !== this.version) {
                await this._clearStore();
                await this._metaSet(VERSION_KEY, this.version);
            }
            this.bytes = Number(await this._metaGet(BYTES_KEY)) || 0;
            // 页面关闭前尽量把排队中的写入落盘
            if (typeof addEventListener === 'function') {
                addEventListener('pagehide', () => { this.flush().catch(() => {}); });
            }
        } catch (e) {
            console.warn('[cache] IndexedDB 不可用，降级为内存缓存', e);
            this.disabled = true;
            this.db = null;
        }
        this.ready = true;
        return this;
    }

    _open() {
        if (this._opening) return this._opening;
        this._opening = new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains(STORE)) {
                    const s = db.createObjectStore(STORE, { keyPath: 'k' });
                    s.createIndex('byTs', 'ts');
                }
                if (!db.objectStoreNames.contains(META)) {
                    db.createObjectStore(META, { keyPath: 'k' });
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
            req.onblocked = () => reject(new Error('indexedDB blocked'));
        });
        return this._opening;
    }

    _tx(store, mode) {
        return this.db.transaction(store, mode).objectStore(store);
    }

    _metaGet(key) {
        return new Promise((resolve) => {
            if (!this.db) { resolve(null); return; }
            try {
                const r = this._tx(META, 'readonly').get(key);
                r.onsuccess = () => resolve(r.result ? r.result.v : null);
                r.onerror = () => resolve(null);
            } catch { resolve(null); }
        });
    }

    _metaSet(key, value) {
        return new Promise((resolve) => {
            if (!this.db) { resolve(); return; }
            try {
                const tx = this.db.transaction(META, 'readwrite');
                tx.objectStore(META).put({ k: key, v: value });
                tx.oncomplete = () => resolve();
                tx.onerror = () => resolve();
            } catch { resolve(); }
        });
    }

    _clearStore() {
        return new Promise((resolve) => {
            if (!this.db) { resolve(); return; }
            try {
                const tx = this.db.transaction(STORE, 'readwrite');
                tx.objectStore(STORE).clear();
                tx.oncomplete = () => { this.bytes = 0; resolve(); };
                tx.onerror = () => resolve();
            } catch { resolve(); }
        });
    }

    /** 生成带版本前缀的 key，调用方只需给稳定的资源标识。 */
    key(...parts) {
        return this.version + '|' + parts.join('|');
    }

    /** 取缓存值（ArrayBuffer / 普通对象）。未命中返回 null。 */
    async get(key) {
        if (this.mem.has(key)) {
            this.hits++;
            const v = this.mem.get(key);
            this.savedBytes += v && v.buf ? v.buf.byteLength : 0;
            return v;
        }
        if (this.disabled || !this.db) { this.misses++; return null; }
        // 不等待排队的写入：set() 已同步写进一级内存缓存，同 key 会先命中；
        // 若这里 flush，进图时几百个分片会退化成几百次小事务，反而拖慢首帧。
        return new Promise((resolve) => {
            try {
                const r = this._tx(STORE, 'readonly').get(key);
                r.onsuccess = () => {
                    const v = r.result ? r.result.v : null;
                    if (v) {
                        this.hits++;
                        this.savedBytes += v.buf ? v.buf.byteLength : 0;
                        this._memPut(key, v);
                    } else {
                        this.misses++;
                    }
                    resolve(v);
                };
                r.onerror = () => { this.misses++; resolve(null); };
            } catch { this.misses++; resolve(null); }
        });
    }

    _memPut(key, value) {
        const size = value && value.buf ? value.buf.byteLength : 0;
        this.mem.set(key, value);
        this.memBytes += size;
        while (this.memBytes > MEM_MAX && this.mem.size > 1) {
            const oldest = this.mem.keys().next().value;
            const old = this.mem.get(oldest);
            this.memBytes -= old && old.buf ? old.buf.byteLength : 0;
            this.mem.delete(oldest);
        }
    }

    /** 写入缓存（异步批量，不阻塞渲染线程）。 */
    set(key, value) {
        this._memPut(key, value);
        if (this.disabled || !this.db) return;
        const size = value && value.buf ? value.buf.byteLength : 0;
        this._queue.push({ k: key, v: value, ts: Date.now(), size });
        if (this._queue.length >= 16) this.flush().catch(() => {});
        else if (!this._flushTimer) {
            this._flushTimer = setTimeout(() => {
                this._flushTimer = null;
                this.flush().catch(() => {});
            }, FLUSH_MS);
        }
    }

    /** 串行化写入：并发 get/set 都等同一批落盘，避免刚写就查不到。 */
    flush() {
        if (!this._flushing) {
            this._flushing = this._doFlush().finally(() => { this._flushing = null; });
        }
        return this._flushing;
    }

    async _doFlush() {
        if (this._flushTimer) { clearTimeout(this._flushTimer); this._flushTimer = null; }
        if (!this.db || !this._queue.length) return;
        const batch = this._queue.splice(0, this._queue.length);
        await new Promise((resolve) => {
            try {
                const tx = this.db.transaction([STORE, META], 'readwrite');
                const store = tx.objectStore(STORE);
                for (const item of batch) store.put(item);
                let added = 0;
                for (const item of batch) added += item.size;
                this.bytes += added;
                this.writes += batch.length;
                tx.objectStore(META).put({ k: BYTES_KEY, v: this.bytes });
                tx.oncomplete = () => resolve();
                tx.onerror = () => resolve();
                tx.onabort = () => resolve();
            } catch { resolve(); }
        });
        if (this.bytes > MAX_BYTES) await this._evict();
    }

    async _evict() {
        if (!this.db) return;
        const target = this.bytes - Math.floor(MAX_BYTES * 0.8);
        let freed = 0;
        await new Promise((resolve) => {
            try {
                const tx = this.db.transaction([STORE, META], 'readwrite');
                const req = tx.objectStore(STORE).index('byTs').openCursor();
                req.onsuccess = () => {
                    const cur = req.result;
                    if (!cur || freed >= target) { resolve(); return; }
                    freed += cur.value.size || 0;
                    cur.delete();
                    cur.continue();
                };
                req.onerror = () => resolve();
            } catch { resolve(); }
        });
        this.bytes = Math.max(0, this.bytes - freed);
        await this._metaSet(BYTES_KEY, this.bytes);
    }

    /** 手动清空（调试 / 换资源包后强制重下）。 */
    async clear() {
        this.mem.clear();
        this.memBytes = 0;
        this._queue.length = 0;
        await this._clearStore();
        await this._metaSet(VERSION_KEY, this.version);
        await this._metaSet(BYTES_KEY, 0);
        return this.stats();
    }

    stats() {
        return {
            version: this.version, disabled: this.disabled, ready: this.ready,
            bytes: this.bytes, hits: this.hits, misses: this.misses,
            writes: this.writes, savedBytes: this.savedBytes,
            mem: this.mem.size, pending: this._queue.length,
        };
    }
}

export const assetCache = new AssetCache();
