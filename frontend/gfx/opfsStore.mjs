// 整文件素材缓存（两级后端：OPFS → IndexedDB Blob）
//
// 为什么在 IndexedDB 分片缓存（assetCache.mjs）之外再加一层整文件缓存：
// - 分片缓存的 key 是精确 Range 区间，换个坐标就是新 key，跨会话命中率很低
//   （实测换图一次仅命中 5 个分片，其余全走网络）；
// - 整文件缓存命中后，任意 Range 直接 blob.slice() 本地取，零网络；
// - IndexedDB 分片层上限 384MB 是刻意保守的（分片小、可淘汰），整文件层
//   交给浏览器配额管理（桌面 Chrome 通常给数十 GB），装得下全量素材。
//
// 后端选择（2026-11-23 修正，重要教训）：
// - **OPFS（navigator.storage.getDirectory）是「安全上下文」专属 API**——
//   https 或 http://127.0.0.1 / localhost 才存在。线上是 http://域名:8080，
//   getDirectory 直接 undefined，首版只实现了 OPFS 后端，导致整层在线上
//   静默失效（__opfs().enabled=false），「载入一遍后直接读」根本没发生过。
//   回归测试跑在 127.0.0.1 恰好是唯一免安全位的地址，所以测试全绿没暴露。
// - 现在 OPFS 不可用/初始化失败 → 自动降级 IndexedDB 存整文件 Blob。
//   Blob 与 File 一样有 .size / .slice() / .arrayBuffer()，调用方零改动。
//
// 设计：
// - 缓存单位是**整文件**（一个 .wil/.wix/.map 一个条目），不是分片；
// - 版本隔离：OPFS 目录名 / IDB 库版本号后缀 = 服务端素材指纹
//   （/api/asset-version）。换资源后旧数据整体删除，不会读到旧字节；
// - 首次访问某 URL 时照常走分片请求（保证首帧快），空闲后**后台整文件预取**
//   写入缓存——本次会话可能用不上，下次打开页就是纯本地读；
// - 两个后端都失败（隐私模式、配额耗尽）才静默降级为纯网络，绝不阻塞游戏。

const ROOT_DIR = 'assets';
let enabled = false;
let mode = null;             // 'opfs' | 'idb' | null
let root = null;             // 当前版本的 assets/<ver>/ 目录句柄（opfs 模式）
let idb = null;              // IDB 数据库连接（idb 模式）
let version = 'default';
const fileIndex = new Map();   // mangleKey(url) -> { name, size, file?: Promise<File|Blob>, dirty }
const prefetching = new Set(); // url（去重后台预取）

export const opfsStats = { hits: 0, misses: 0, stored: 0, storedBytes: 0, prefetches: 0, errors: 0 };

// OPFS/IDB 条目名不允许奇怪字符，统一转义；长度截断 + 尾挂 url 哈希防碰撞。
function mangleKey(url) {
    const clean = String(url).split('?')[0];
    let h = 5381;
    for (let i = 0; i < clean.length; i++) h = ((h << 5) + h + clean.charCodeAt(i)) | 0;
    return encodeURIComponent(clean.replace(/^https?:\/\/[^/]+/, '')).slice(0, 150)
        + '.' + (h >>> 0).toString(36);
}

function versionDir(ver) {
    return String(ver || 'default').replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 60) || 'default';
}

function recount() {
    opfsStats.stored = fileIndex.size;
    let bytes = 0;
    for (const v of fileIndex.values()) bytes += v.size || 0;
    opfsStats.storedBytes = bytes;
}

// ---- 启动：先试 OPFS，失败降级 IDB。无论成败都 resolve，不抛。 ----
export async function opfsInit(ver) {
    version = versionDir(ver);
    if (await opfsTryInit(ver)) { mode = 'opfs'; enabled = true; return true; }
    if (await idbTryInit()) { mode = 'idb'; enabled = true; return true; }
    console.warn('[wholeCache] OPFS 与 IndexedDB 整文件层均不可用，降级纯网络');
    enabled = false;
    mode = null;
    return false;
}

async function opfsTryInit(ver) {
    if (typeof navigator === 'undefined' || !navigator.storage || !navigator.storage.getDirectory) {
        return false;   // 非安全上下文（http://域名）——最常见路径，直接走 IDB
    }
    try {
        const base = await navigator.storage.getDirectory();
        const assets = await base.getDirectoryHandle(ROOT_DIR, { create: true });
        // 清掉其他版本的目录（换资源包 = 全量失效，语义与 assetCache 的 version 一致）
        const names = [];
        for await (const [name, handle] of assets.entries()) {
            if (handle.kind === 'directory' && name !== version) names.push(name);
        }
        for (const name of names) {
            try { await assets.removeEntry(name, { recursive: true }); } catch { /* 占用中 */ }
        }
        root = await assets.getDirectoryHandle(version, { create: true });
        // 恢复已有索引（同版本重开页面直接命中）。文件名即 mangleKey 产物，
        // 反查时也用同名 key，无需还原 url。
        // 注意：FileSystemFileHandle 没有 getSize()（那是同步访问句柄的方法），
        // 必须先 getFile() 再读 .size——曾在这里抛 TypeError 导致整层静默降级。
        for await (const [name, handle] of root.entries()) {
            if (handle.kind !== 'file') continue;
            const f = await handle.getFile().catch(() => null);
            if (!f) continue;
            fileIndex.set(name, { name, size: f.size,
                file: handle.getFile().catch(() => null) });
        }
        recount();
        // 请求持久存储（不主动清），失败也无妨
        try { navigator.storage.persist?.().catch(() => {}); } catch { /* noop */ }
        return true;
    } catch (e) {
        console.warn('[wholeCache] OPFS 初始化失败，尝试 IndexedDB', e);
        root = null;
        return false;
    }
}

// ---- IndexedDB 整文件后端（HTTP 域名等非安全上下文下的主路径） ----
const IDB_STORE = 'whole';

function idbOpen() {
    return new Promise((resolve, reject) => {
        if (typeof indexedDB === 'undefined') { reject(new Error('no indexedDB')); return; }
        // 库名带素材版本：换资源包 = 整库失效（与 OPFS 目录版本隔离同语义）。
        // 其他版本库稍后异步删除，不阻塞启动。
        const req = indexedDB.open('jootm2-whole-' + version, 1);
        req.onupgradeneeded = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
        };
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
        req.onblocked = () => reject(new Error('indexedDB blocked'));
    });
}

async function idbTryInit() {
    try {
        idb = await idbOpen();
        // 清掉其他版本的库（异步、失败无所谓）。databases() 仅 Chrome 有，
        // 没有就只漏一个旧库，不影响正确性（当前库名已按版本隔离）。
        try {
            const all = await indexedDB.databases?.();
            for (const d of all || []) {
                if (d.name && d.name.startsWith('jootm2-whole-') && d.name !== 'jootm2-whole-' + version) {
                    indexedDB.deleteDatabase(d.name);
                }
            }
        } catch { /* noop */ }
        // 只读 key 列表恢复索引；size 在记录里存一份 {b, size} 包装，
        // 避免 init 时把每个 Blob 都拉进内存。
        const tx = idb.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE);
        await new Promise((resolve) => {
            const cur = tx.openCursor();
            cur.onsuccess = () => {
                const c = cur.result;
                if (!c) { resolve(); return; }
                const v = c.value;
                if (v && v.b) {
                    fileIndex.set(String(c.key), {
                        name: String(c.key), size: v.size || (v.b && v.b.size) || 0,
                        file: Promise.resolve(v.b),
                    });
                }
                c.continue();
            };
            cur.onerror = () => resolve();
        });
        recount();
        try { navigator.storage?.persist?.().catch(() => {}); } catch { /* noop */ }
        return true;
    } catch (e) {
        console.warn('[wholeCache] IndexedDB 整文件层不可用', e);
        idb = null;
        return false;
    }
}

function idbGetBlob(key) {
    return new Promise((resolve) => {
        try {
            const r = idb.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).get(key);
            r.onsuccess = () => resolve(r.result ? r.result.b : null);
            r.onerror = () => resolve(null);
        } catch { resolve(null); }
    });
}

function idbPutBlob(key, blob) {
    return new Promise((resolve) => {
        try {
            const tx = idb.transaction(IDB_STORE, 'readwrite');
            tx.objectStore(IDB_STORE).put({ b: blob, size: blob.size }, key);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => resolve(false);
            tx.onabort = () => resolve(false);
        } catch { resolve(false); }
    });
}

function idbDeleteKeys(keys) {
    return new Promise((resolve) => {
        try {
            const tx = idb.transaction(IDB_STORE, 'readwrite');
            for (const k of keys) tx.objectStore(IDB_STORE).delete(k);
            tx.oncomplete = () => resolve(true);
            tx.onerror = () => resolve(false);
            tx.onabort = () => resolve(false);
        } catch { resolve(false); }
    });
}

function idbAllKeys() {
    return new Promise((resolve) => {
        try {
            const r = idb.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).getAllKeys();
            r.onsuccess = () => resolve(r.result.map(String));
            r.onerror = () => resolve([]);
        } catch { resolve([]); }
    });
}

export function opfsSupported() {
    if (typeof navigator !== 'undefined' && navigator.storage?.getDirectory) return true;
    return typeof indexedDB !== 'undefined';
}

/** 取整文件（Promise<File|Blob>）。未缓存返回 null。 */
export function opfsGet(url) {
    if (!enabled) return null;
    const hit = fileIndex.get(mangleKey(url));
    if (!hit) { opfsStats.misses++; return null; }
    opfsStats.hits++;
    if (!hit.file) hit.file = idbGetBlob(hit.name);   // IDB 惰性读（同一 key 只读一次）
    return hit.file;
}

/** 用整 Blob 填充缓存（幂等：已有则覆盖）。 */
export async function opfsPut(url, blob) {
    if (!enabled || !blob) return false;
    const key = mangleKey(url);
    if (mode === 'opfs') {
        try {
            const old = fileIndex.get(key);
            if (old) { try { await root.removeEntry(old.name); } catch { /* 已没了 */ } }
            const handle = await root.getFileHandle(key, { create: true });
            const w = await handle.createWritable();
            await w.write(blob);
            await w.close();
            fileIndex.set(key, { name: key, size: blob.size,
                file: handle.getFile().catch(() => null) });
            recount();
            return true;
        } catch (e) {
            opfsStats.errors++;
            // 配额满：按插入序清一半，下次再试
            try { await evictSome(Math.ceil(blob.size * 1.5)); } catch { /* 尽力 */ }
            return false;
        }
    }
    // IDB 后端
    if (await idbPutBlob(key, blob)) {
        fileIndex.set(key, { name: key, size: blob.size, file: Promise.resolve(blob) });
        recount();
        return true;
    }
    opfsStats.errors++;
    try { await evictSome(Math.ceil(blob.size * 1.5)); } catch { /* 尽力 */ }
    // 淘汰后重试一次（写失败常见原因就剩配额）
    if (await idbPutBlob(key, blob)) {
        fileIndex.set(key, { name: key, size: blob.size, file: Promise.resolve(blob) });
        recount();
        return true;
    }
    return false;
}

/** 释放约 needBytes（按 Map 插入序 ≈ 最早预取优先）。 */
async function evictSome(needBytes) {
    let freed = 0;
    const doomed = [];
    for (const [key, v] of fileIndex) {
        doomed.push(key);
        freed += v.size || 0;
        if (freed >= needBytes) break;
    }
    if (mode === 'opfs') {
        for (const k of doomed) {
            try { await root.removeEntry(k); } catch { /* 占用中 */ }
            fileIndex.delete(k);
        }
    } else {
        await idbDeleteKeys(doomed);
        for (const k of doomed) fileIndex.delete(k);
    }
    recount();
}

/**
 * 空闲时整文件预取：只在下过某 URL 的分片（说明文件确实要用）且尚未入缓存时
 * 发起，延迟 + 串行，绝不和进游戏首屏的地面分片抢带宽。
 */
let prefetchChain = Promise.resolve();
export function opfsPrefetch(url) {
    if (!enabled || prefetching.has(url) || fileIndex.has(mangleKey(url))) return;
    prefetching.add(url);
    prefetchChain = prefetchChain.then(async () => {
        try {
            await new Promise(r => setTimeout(r, 2500));   // 让首屏分片先走完
            const resp = await fetch(url);
            if (!resp.ok) return;
            const blob = await resp.blob();
            if (await opfsPut(url, blob)) opfsStats.prefetches++;
        } catch { /* 断网/掐断：下次再试 */ }
        finally { prefetching.delete(url); }
    }).catch(() => {});
}

/** 手动清空当前版本缓存（调试 / 用户点「清除资源缓存」）。 */
export async function opfsClear() {
    if (!enabled) return false;
    try {
        if (mode === 'opfs') {
            const names = [];
            for await (const [name, handle] of root.entries()) {
                if (handle.kind === 'file') names.push(name);
            }
            for (const n of names) { try { await root.removeEntry(n); } catch { /* 占用中 */ } }
        } else {
            const keys = await idbAllKeys();
            await idbDeleteKeys(keys);
        }
        fileIndex.clear();
        recount();
        return true;
    } catch { return false; }
}

export function opfsGetStats() {
    return {
        enabled, mode, version,
        stored: opfsStats.stored, storedBytes: opfsStats.storedBytes,
        hits: opfsStats.hits, misses: opfsStats.misses,
        prefetches: opfsStats.prefetches, errors: opfsStats.errors,
        pending: prefetching.size,
    };
}
