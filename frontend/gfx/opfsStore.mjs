// 浏览器文件系统（OPFS）整文件素材缓存
//
// 为什么在 IndexedDB 之外再加一层（2026-11-23 用户反馈「载入慢/重复载入」根因）：
// - IndexedDB 持久层上限 384MB（assetCache.mjs），而 resources/Data 全量 1.2GB——
//   一个版本的 WIL 分片就放不满，触发淘汰后下次进游戏又要走网络，这就是
//   「重复载入」的直接来源；
// - OPFS（Origin Private File System）走浏览器私有磁盘目录，配额 = 浏览器
//   可用磁盘的固定比例（Chrome 通常给数十 GB），没有 IndexedDB 那种小配额上限，
//   且 createSyncAccessHandle / Blob.slice 读取比 IndexedDB 事务快一个量级。
//
// 设计：
// - 缓存单位是**整文件**（一个 .wil/.wix/.map 一个文件），不是分片。文件进
//   缓存后，scheduler 的任意 Range 请求直接 blob.slice() 本地取，零网络；
// - 版本隔离：目录名 = 服务端素材指纹（/api/asset-version）。换资源后旧目录
//   整体删除，不会读到旧字节；
// - 首次访问某 URL 时照常走分片请求（保证首帧快），空闲后**后台整文件预取**
//   写入 OPFS——本次会话可能用不上，下次打开页就是纯本地读；
// - 任何一步不支持/失败（Firefox<121、Safari 私有模式、配额满）都静默降级，
//   回到 IndexedDB 分片缓存 + 网络，绝不阻塞游戏。

const ROOT_DIR = 'assets';
let enabled = false;
let root = null;            // 当前版本的 assets/<ver>/ 目录句柄
let version = 'default';
const fileIndex = new Map();   // mangleKey(url) -> { name, size, file: Promise<File> }
const prefetching = new Set(); // url（去重后台预取）

export const opfsStats = { hits: 0, misses: 0, stored: 0, storedBytes: 0, prefetches: 0, errors: 0 };

// OPFS 文件名不允许 %2F 之外的奇怪字符也安全起见统一转义；长度截断 + 尾挂 url
// 哈希防碰撞。
function mangleKey(url) {
    const clean = String(url).split('?')[0];
    let h = 5381;
    for (let i = 0; i < clean.length; i++) h = ((h << 5) + h + clean.charCodeAt(i)) | 0;
    return encodeURIComponent(clean.replace(/^https?:\/\/[^/]+/, '')).slice(0, 150)
        + '.' + (h >>> 0).toString(36);
}

/** 启动阶段调用一次；无论成败都 resolve，不抛。 */
export async function opfsInit(ver) {
    version = String(ver || 'default');
    if (typeof navigator === 'undefined' || !navigator.storage || !navigator.storage.getDirectory) {
        return false;
    }
    try {
        const base = await navigator.storage.getDirectory();
        const assets = await base.getDirectoryHandle(ROOT_DIR, { create: true });
        // 清掉其他版本的目录（换资源包 = 全量失效，语义与 assetCache 的 version 一致）
        const want = version.replace(/[^a-zA-Z0-9._-]/g, '_').slice(0, 60) || 'default';
        const names = [];
        for await (const [name, handle] of assets.entries()) {
            if (handle.kind === 'directory' && name !== want) names.push(name);
        }
        for (const name of names) {
            try { await assets.removeEntry(name, { recursive: true }); } catch { /* 占用中 */ }
        }
        root = await assets.getDirectoryHandle(want, { create: true });
        enabled = true;
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
        opfsStats.stored = fileIndex.size;
        for (const v of fileIndex.values()) opfsStats.storedBytes += v.size;
        // 请求持久存储（不主动清），失败也无妨
        try { navigator.storage.persist?.().catch(() => {}); } catch { /* noop */ }
        return true;
    } catch (e) {
        console.warn('[opfs] 初始化失败，降级 IndexedDB', e);
        enabled = false;
        return false;
    }
}

export function opfsSupported() {
    return typeof navigator !== 'undefined' && !!navigator.storage?.getDirectory;
}

/** 取整文件（Promise<File>）。未缓存返回 null。 */
export function opfsGet(url) {
    if (!enabled) return null;
    const hit = fileIndex.get(mangleKey(url));
    if (!hit) { opfsStats.misses++; return null; }
    opfsStats.hits++;
    return hit.file;
}

/** 用整 Blob 填充缓存（幂等：已有则覆盖）。 */
export async function opfsPut(url, blob) {
    if (!enabled || !blob) return false;
    const key = mangleKey(url);
    try {
        const old = fileIndex.get(key);
        if (old) { try { await root.removeEntry(old.name); } catch { /* 已没了 */ } }
        const handle = await root.getFileHandle(key, { create: true });
        const w = await handle.createWritable();
        await w.write(blob);
        await w.close();
        const entry = { name: key, size: blob.size, file: handle.getFile().catch(() => null) };
        fileIndex.set(key, entry);
        opfsStats.stored = fileIndex.size;
        for (const v of fileIndex.values()) opfsStats.storedBytes += v.size;
        return true;
    } catch (e) {
        opfsStats.errors++;
        // 配额满：按「最久未预取」粗略清一半，下次再试
        try { await opfsEvict(Math.ceil(blob.size * 1.5)); } catch { /* 尽力 */ }
        return false;
    }
}

async function opfsEvict(needBytes) {
    let freed = 0;
    const entries = [...fileIndex.entries()];
    for (const [key, v] of entries) {
        try { await root.removeEntry(v.name); } catch { /* 占用中 */ }
        fileIndex.delete(key);
        freed += v.size;
        if (freed >= needBytes) break;
    }
    opfsStats.stored = fileIndex.size;
    opfsStats.storedBytes = 0;
    for (const v of fileIndex.values()) opfsStats.storedBytes += v.size;
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

export function opfsGetStats() {
    return {
        enabled, stored: opfsStats.stored, storedBytes: opfsStats.storedBytes,
        hits: opfsStats.hits, misses: opfsStats.misses,
        prefetches: opfsStats.prefetches, errors: opfsStats.errors,
        pending: prefetching.size,
    };
}
