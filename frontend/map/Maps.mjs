// .map 地图解析（对应旧 Maps.mjs）
// 解析出每个 tile 的地砖/小地砖/对象层索引 + 可行走标记。

import { mapUrl } from '../gfx/resource.mjs';
import { assetCache } from '../gfx/assetCache.mjs';

const loadedMaps = new Map();   // mapId -> {width,height,tiles}

export async function loadMap(mapId) {
    if (loadedMaps.has(mapId)) return loadedMaps.get(mapId);
    const url = mapUrl(mapId);
    if (!url) throw new Error(`地图 ${mapId} 未在 resources.json 中`);

    // .map 单文件常有几 MB，公网重下代价高；按资源版本持久缓存整份字节。
    const key = assetCache.key('MAP', url);
    const hit = assetCache.ready ? await assetCache.get(key) : null;
    let buf;
    if (hit && hit.buf) {
        buf = hit.buf;
    } else {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`加载 ${url} 失败`);
        buf = await resp.arrayBuffer();
        assetCache.set(key, { buf }, buf.byteLength);
    }
    const view = new DataView(buf);

    const width = view.getUint16(0, true);
    const height = view.getUint16(2, true);
    const rawTileSize = (buf.byteLength - 52) / width / height;
    // 标准 tile 只有 12 / 14 字节。1.80 客户端部分活动模板图（T118/EM001…）
    // 在 tile 数据后还带附加数据块，长度不整除 w*h；此时按 12 字节读前
    // w*h 个 tile，与服务端 read_map 的推断一致，避免非整数步进让 DataView 抛错。
    const tileByteSize =
        (rawTileSize === 14 || (rawTileSize > 14 && Number.isInteger(rawTileSize)))
            ? rawTileSize : 12;

    let off = 52;
    const tiles = [];
    for (let x = 0; x < width; x++) {
        tiles[x] = [];
        for (let y = 0; y < height; y++) {
            const bng = view.getInt16(off, true); off += 2;
            const mid = view.getInt16(off, true); off += 2;
            const obj = view.getInt16(off, true); off += 2;
            const doorIdx = view.getInt8(off); off++;
            const doorOffset = view.getInt8(off); off++;
            const ani = view.getInt8(off); off++;
            const aniTick = view.getInt8(off); off++;
            const objFileIdx = view.getInt8(off); off++;
            const light = view.getInt8(off); off++;

            let bngFileIdx = 0, midFileIdx = 0;
            if (tileByteSize >= 14) {
                bngFileIdx = view.getInt8(off); off++;
                midFileIdx = view.getInt8(off); off++;
                if (tileByteSize > 14) off += tileByteSize - 14;
            }

            tiles[x][y] = {
                bng: (bng & 0x7fff) > 0 ? (bng & 0x7fff) - 1 : -1,
                mid: (mid & 0x7fff) > 0 ? (mid & 0x7fff) - 1 : -1,
                obj: (obj & 0x7fff) > 0 ? (obj & 0x7fff) - 1 : -1,
                canStand: (bng & 0x8000) === 0 && (mid & 0x8000) === 0 && (obj & 0x8000) === 0,
                // 高墙（FrImg 0x8000）：阻挡飞行/视线（灵魂火符等投射魔法撞墙中断，对齐 Envir.CanFireFly）
                highWall: (obj & 0x8000) !== 0,
                bngFile: bngFileIdx !== 0 ? bngFileIdx + 1 : 0,
                midFile: midFileIdx !== 0 ? midFileIdx + 1 : 0,
                objFile: objFileIdx !== 0 ? objFileIdx + 1 : 0,
                hasAni: (ani & 0x7f) > 0,
                aniFrame: ani & 0x7f,
                blend: (ani & 0x80) !== 0,
                // 原版 TMapUnitInfo.btLight（Envir.pas:33「0..1..4 光源强度」）。
                // PlayScn.pas:1335-1338 对视口内 btLight>0 的格调用 AddLight，
                // 这是地图自带火把/灯柱的静态光源，之前解析出来却直接丢弃了。
                light: light & 0xff,
            };
        }
    }

    const map = { width, height, tiles };
    loadedMaps.set(mapId, map);
    return map;
}
