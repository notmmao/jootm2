// 资源清单：从服务端 resources.json 加载图片库 / 地图 URL 映射
// 对应旧代码 WIL 文件路径（Data\*.wil / Map\*.map）

let _images = null;   // { key: { x: wix路径, l: wil路径 } }
let _maps = null;     // { mapId: map路径 }
let _wavs = null;     // { soundId: wav路径 }
let _mapsLower = null;  // 小写 key 索引（服务端 _base() 把 D401.map 归一成 d401）

export async function loadResources() {
    const resp = await fetch('/resources/resources.json');
    if (!resp.ok) throw new Error('加载 resources.json 失败');
    const data = await resp.json();
    _images = data.images || {};
    _maps = data.maps || {};
    _wavs = data.wavs || {};
    // 服务端 gamedata._base() 把文件名统一小写登记（D401.map → "d401"），
    // 而 MapInfo 里地图 id 是原样（D401）。矿洞/地下城这类大写 id 之前直接查表
    // 落空 → 「地图 D401 未在 resources.json 中」卡死在加载界面。
    _mapsLower = {};
    for (const k of Object.keys(_maps)) _mapsLower[k.toLowerCase()] = _maps[k];
    return data;
}

/** 全部图库 key（启动阶段后台预热 WIX 索引用）。 */
export function imageKeys() {
    return _images ? Object.keys(_images) : [];
}

// 取图片库 URL（key 如 'tiles'/'hum'/'mon1'）
export function imageUrls(key) {
    const img = _images?.[key];
    if (!img) return null;
    return { wil: `/resources/${img.l}`, wix: `/resources/${img.x}` };
}

// 取地图文件 URL（先精确匹配，再小写兜底）
export function mapUrl(mapId) {
    const m = _maps?.[mapId] ?? _mapsLower?.[String(mapId).toLowerCase()];
    return m ? `/resources/${m}` : null;
}

// 取音频 URL（soundId 如 '001'/'1'/'100'，对应旧 SoundUtil 的音效索引）
export function wavUrl(soundId) {
    const w = _wavs?.[String(soundId)];
    return w ? `/resources/${w}` : null;
}

// 音频总数
export function wavCount() {
    return _wavs ? Object.keys(_wavs).length : 0;
}
