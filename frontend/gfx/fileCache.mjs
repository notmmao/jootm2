// 整文件字节加载（.map / .wix 这类一次读全量的资源）
//
// 命中链：OPFS 整文件 → IndexedDB → 网络（失败自动重试）。
// 网络成功后**同时**写两级缓存：IndexedDB 立即可用；OPFS 让下次打开页零网络。
// 分片（WIL Range）不走这里，由 scheduler 内部处理。

import { assetCache } from './assetCache.mjs';
import { opfsGet, opfsPut } from './opfsStore.mjs';

const RETRY_BASE_MS = 400;

async function fetchWithRetry(url) {
    let lastErr = null;
    for (let attempt = 0; attempt <= 2; attempt++) {
        try {
            const resp = await fetch(url);
            if (resp.ok) return resp;
            lastErr = new Error(`HTTP ${resp.status}`);
        } catch (e) {
            lastErr = e;
        }
        if (attempt < 2) await new Promise(r => setTimeout(r, RETRY_BASE_MS * (1 << attempt)));
    }
    throw lastErr || new Error('fetch failed');
}

/** 下载整份 url 的字节；OPFS/IndexedDB 任一命中则不发网络。 */
export async function loadWholeBytes(kind, url) {
    const fileP = opfsGet(url);
    if (fileP) {
        const file = await fileP;
        if (file) return file.arrayBuffer();
    }
    const key = assetCache.key(kind, url);
    const hit = assetCache.ready ? await assetCache.get(key) : null;
    if (hit && hit.buf) {
        // IDB 有、OPFS 没有 → 顺手补写 OPFS（异步，不等）
        opfsPut(url, new Blob([hit.buf])).catch(() => {});
        return hit.buf;
    }
    const resp = await fetchWithRetry(url);
    const buf = await resp.arrayBuffer();
    assetCache.set(key, { buf }, buf.byteLength);
    opfsPut(url, new Blob([buf])).catch(() => {});
    return buf;
}
