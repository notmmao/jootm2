// 原版 PlayScn.pas 的 m_LightMap 多光源系统（火把 / 魔法 / 地图光源）。
//
// 依据（本地 Delphi 源码 GameOfMir/MirClient/PlayScn.pas）：
//   LMX=30 / LMY=26 / MAXLIGHT=5                         (:23-26)
//   LightFiles = Data\lig0a.dat .. lig0f.dat              (:27-34)
//   LightMask0..5                                        (:36-128)
//   ClearLightMap / UpdateBright / CheckOverLight / AddLight / ApplyLightMap
//                                                        (:658-775)
//   LoadFog：文件头两个 int32 = 宽高，之后 w*h 字节 fogmask (:607-635)
//
// 每帧流程（PlayScn.pas:1103-1110、1327-1412、1471）：
//   ZeroMemory(m_PFogScreen) → ClearLightMap → 收集 AddLight
//   → ApplyLightMap 用 FogCopy 把 m_Lights[light] **累加**进 fogmask
//   → DrawFog 按 fogmask 值取 ColorLevel 表压暗像素。
//
// canvas 2d 没有 8 位调色板，这里用等价的两步合成：
//   1) 把每张 lig0*.dat 预渲染成「黑色 + alpha=fog/30」的画布，
//      多光源用 globalCompositeOperation='lighter' 累加 alpha（= FogCopy 的 paddd）；
//   2) 整屏铺暗色，再用 'destination-out' 贴累加后的 fog 画布挖掉暗度
//      （= DrawFog 的 pColorLevel[fogmask, srcPixel]）。
// 光源本身仍严格走原版的亮度网格：同格取更亮等级、CheckOverLight 去重、
// UpdateBright 邻域抑制，这样「一堆火把挨着」不会无限叠亮。

export const LMX = 30;
export const LMY = 26;
export const MAXLIGHT = 5;

// PlayScn.pas:676-681 / 700-706：每个等级对应 (r+1)×(r+1) 的亮度核
const LIGHT_RADIUS = [2, 4, 8, 10, 14, 16];
const CHECK_LIMIT = [0, 4, 8, 18, 30, 40];

// PlayScn.pas:36-128，按行优先展开成一维，索引 = i*(r+1)+j
const LIGHT_MASKS = [
    // level 0, r=2
    [0, 1, 0,
     1, 3, 1,
     0, 1, 0],
    // level 1, r=4
    [0, 1, 1, 1, 0,
     1, 1, 3, 1, 1,
     1, 3, 4, 3, 1,
     1, 1, 3, 1, 1,
     0, 1, 2, 1, 0],
    // level 2, r=8
    [0, 0, 0, 1, 1, 1, 0, 0, 0,
     0, 0, 1, 2, 3, 2, 1, 0, 0,
     0, 1, 2, 3, 4, 3, 2, 1, 0,
     1, 2, 3, 4, 4, 4, 3, 2, 1,
     1, 3, 4, 4, 4, 4, 4, 3, 1,
     1, 2, 3, 4, 4, 4, 3, 2, 1,
     0, 1, 2, 3, 4, 3, 2, 1, 0,
     0, 0, 1, 2, 3, 2, 1, 0, 0,
     0, 0, 0, 1, 1, 1, 0, 0, 0],
    // level 3, r=10
    [0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0,
     0, 0, 0, 1, 2, 2, 2, 1, 0, 0, 0,
     0, 0, 1, 2, 3, 3, 3, 2, 1, 0, 0,
     0, 1, 2, 3, 4, 4, 4, 3, 2, 1, 0,
     1, 2, 3, 4, 4, 4, 4, 4, 3, 2, 1,
     2, 3, 4, 4, 4, 4, 4, 4, 4, 3, 2,
     1, 2, 3, 4, 4, 4, 4, 4, 3, 2, 1,
     0, 1, 2, 3, 4, 4, 4, 3, 2, 1, 0,
     0, 0, 1, 2, 3, 3, 3, 2, 1, 0, 0,
     0, 0, 0, 1, 2, 2, 2, 1, 0, 0, 0,
     0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0],
    // level 4, r=14
    [0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 1, 1, 2, 2, 2, 1, 1, 0, 0, 0, 0,
     0, 0, 0, 1, 1, 2, 3, 3, 3, 2, 1, 1, 0, 0, 0,
     0, 0, 1, 1, 2, 3, 4, 4, 4, 3, 2, 1, 1, 0, 0,
     0, 1, 1, 2, 3, 4, 4, 4, 4, 4, 3, 2, 1, 1, 0,
     1, 1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 3, 2, 1, 1,
     1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 4, 4, 3, 2, 1,
     1, 1, 2, 3, 4, 4, 4, 4, 4, 4, 4, 3, 2, 1, 1,
     0, 1, 1, 2, 3, 4, 4, 4, 4, 4, 3, 2, 1, 1, 0,
     0, 0, 1, 1, 2, 3, 4, 4, 4, 3, 2, 1, 1, 0, 0,
     0, 0, 0, 1, 1, 2, 3, 3, 3, 2, 1, 1, 0, 0, 0,
     0, 0, 0, 0, 1, 1, 2, 2, 2, 1, 1, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0],
    // level 5, r=16
    [0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 1, 2, 2, 2, 1, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 1, 2, 4, 4, 4, 2, 1, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 1, 2, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0, 0,
     0, 0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0,
     0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0,
     0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0,
     1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1,
     1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1,
     1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1,
     0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0,
     0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0,
     0, 0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0,
     0, 0, 0, 0, 1, 2, 4, 4, 4, 4, 4, 2, 1, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 1, 2, 4, 4, 4, 2, 1, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 1, 2, 2, 2, 1, 0, 0, 0, 0, 0, 0,
     0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0],
];

const LIGHT_FILES = ['a', 'b', 'c', 'd', 'e', 'f'];
let _maskPromise = null;

/**
 * 载入 6 张原版光源贴图（PlayScn.pas:607-635 LoadFog）。
 * 返回 [{width,height,canvas}]，失败项为 null；只发一次请求。
 */
export function loadLightMasks() {
    if (_maskPromise) return _maskPromise;
    _maskPromise = Promise.all(LIGHT_FILES.map(async (ch) => {
        try {
            const resp = await fetch(`/resources/Data/lig0${ch}.dat`);
            if (!resp.ok) return null;
            const buf = await resp.arrayBuffer();
            if (buf.byteLength < 8) return null;
            const dv = new DataView(buf);
            const w = dv.getInt32(0, true);
            const h = dv.getInt32(4, true);
            if (w <= 0 || h <= 0 || w > 4096 || h > 4096 || 8 + w * h > buf.byteLength) return null;
            const src = new Uint8Array(buf, 8, w * h);
            // 主线程一律 HTMLCanvasElement（见项目纹理约定，OffscreenCanvas 的
            // convertToBlob 在部分 Chromium 会抛 NotReadableError）。
            const cv = document.createElement('canvas');
            cv.width = w; cv.height = h;
            const c = cv.getContext('2d');
            const img = c.createImageData(w, h);
            const d = img.data;
            for (let k = 0, p = 0; k < src.length; k++, p += 4) {
                const v = src[k];
                d[p] = d[p + 1] = d[p + 2] = 0;     // 黑色：lighter 只累加 alpha
                d[p + 3] = v >= 30 ? 255 : Math.round(v * 255 / 30);
            }
            c.putImageData(img, 0, 0);
            return { width: w, height: h, canvas: cv };
        } catch (e) {
            console.warn('[fog] 光源贴图加载失败 lig0' + ch, e);
            return null;
        }
    }));
    return _maskPromise;
}

/** m_LightMap：31×27 的亮度网格，坐标相对自己（rx,ry）。 */
export class LightMap {
    constructor() {
        const n = (LMX + 1) * (LMY + 1);
        this.light = new Int8Array(n);       // -1 = 无光源（ClearLightMap）
        this.bright = new Int16Array(n);     // UpdateBright 累积亮度
        this.shiftX = new Int16Array(n);
        this.shiftY = new Int16Array(n);
        this.clear();
    }

    _idx(lx, ly) { return ly * (LMX + 1) + lx; }

    /** PlayScn.pas:658-666：light 全部置 -1，其余清零。 */
    clear() {
        this.light.fill(-1);
        this.bright.fill(0);
        this.shiftX.fill(0);
        this.shiftY.fill(0);
    }

    /** PlayScn.pas:668-690：把亮度核累加到邻域。 */
    updateBright(lx, ly, level) {
        const r = LIGHT_RADIUS[level];
        const mask = LIGHT_MASKS[level];
        const half = r >> 1;
        for (let i = 0; i <= r; i++) {
            const bx = lx - half + i;
            if (bx < 0 || bx > LMX) continue;
            for (let j = 0; j <= r; j++) {
                const by = ly - half + j;
                if (by < 0 || by > LMY) continue;
                this.bright[this._idx(bx, by)] += mask[i * (r + 1) + j];
            }
        }
    }

    /**
     * PlayScn.pas:692-725：TRUE = 邻域已经够亮，这盏灯可以省掉。
     * 原版返回 FALSE 时 AddLight 才真正加灯（`not CheckOverLight(...) or nocheck`）。
     */
    checkOverLight(lx, ly, level) {
        const r = LIGHT_RADIUS[level];
        const mask = LIGHT_MASKS[level];
        const check = CHECK_LIMIT[level];
        const half = r >> 1;
        let count = 0;
        for (let i = 0; i <= r; i++) {
            const bx = lx - half + i;
            if (bx < 0 || bx > LMX) continue;
            for (let j = 0; j <= r; j++) {
                const by = ly - half + j;
                if (by < 0 || by > LMY) continue;
                const mlight = mask[i * (r + 1) + j];
                const cur = this.bright[this._idx(bx, by)];
                if (cur < mlight) {
                    count += mlight - cur;
                    if (count >= check) return false;   // 有足够暗区 → 需要这盏灯
                }
            }
        }
        return true;                                     // 已够亮 → 跳过
    }

    /** PlayScn.pas:727-743：AddLight（rx/ry = 自己的地图格坐标）。 */
    addLight(x, y, shiftX, shiftY, level, noCheck, rx, ry) {
        const lx = Math.round(x) - Math.round(rx) + (LMX >> 1);
        const ly = Math.round(y) - Math.round(ry) + (LMY >> 1);
        if (lx < 1 || lx >= LMX || ly < 1 || ly >= LMY) return;
        const lv = Math.max(0, Math.min(MAXLIGHT, level | 0));
        const i = this._idx(lx, ly);
        if (this.light[i] >= lv) return;
        if (!noCheck && this.checkOverLight(lx, ly, lv)) return;
        this.updateBright(lx, ly, lv);
        this.light[i] = lv;
        this.shiftX[i] = Math.round(shiftX || 0);
        this.shiftY[i] = Math.round(shiftY || 0);
    }

    /** PlayScn.pas:752-774：把网格里登记的光源还原成世界坐标。 */
    entries(rx, ry) {
        const out = [];
        for (let i = 1; i < LMX; i++) {
            for (let j = 1; j < LMY; j++) {
                const k = this._idx(i, j);
                const lv = this.light[k];
                if (lv < 0) continue;
                out.push({
                    x: i + Math.round(rx) - (LMX >> 1),
                    y: j + Math.round(ry) - (LMY >> 1),
                    level: lv,
                    shiftX: this.shiftX[k],
                    shiftY: this.shiftY[k],
                });
            }
        }
        return out;
    }
}
