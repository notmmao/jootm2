// 高清呈现层：把 800×600 逻辑画面以「设备物理像素」分辨率呈现到可见 canvas。
//
// 背景：旧实现可见 canvas 位图恒为 800×600，再由 CSS transform 整体放大
// （k × devicePixelRatio，常见 1.5~2.7 倍）。导致两类发糊：
//   1. 场景里的文字（名字/喊话/伤害飘字/地面物品名）先按 11~14px 栅格化进
//      800×600，再被拉伸——字形信息已经丢了，CSS 怎么调都救不回来；
//   2. 精灵像素做非整数倍 nearest 拉伸（如 1.8 倍），像素列一宽一窄，发虚发抖。
//
// 做法：
//   - 场景照旧画在 800×600 的 back buffer（所有绘制代码/坐标零改动）；
//   - 可见 canvas 位图 = 舞台在屏幕上的物理像素尺寸（viewport.mjs 已把它对齐成整数）；
//   - 呈现时「整数倍最近邻放大 → 一次高质量线性缩放」（sharp-bilinear），
//     像素块边缘最多 1 物理像素过渡，内部锐利且块大小一致；
//   - back buffer 上的 fillText/strokeText 被拦截排队，放大后按物理分辨率重画，
//     字形直接在目标字号上栅格化（等价于原生分辨率渲染文字）。
//
// 呈现模式（localStorage 'jootm2.scaleFilter' 或 window.__setScaleFilter）：
//   'sharp'  默认，sharp-bilinear；
//   'pixel'  纯最近邻（非整数倍时像素大小不均，但最「硬」）；
//   'smooth' 纯线性（最柔和，接近旧版 CSS 默认插值）。
// 文字高清可用 localStorage 'jootm2.hiText' = '0' 关闭（回到旧版低分辨率文字）。

import { SCREEN_WIDTH, SCREEN_HEIGHT } from './config.mjs';

const FILTERS = ['sharp', 'pixel', 'smooth'];
const FONT_PX_RE = /(\d+(?:\.\d+)?)px/;

function readFilter() {
    try {
        const v = localStorage.getItem('jootm2.scaleFilter');
        return FILTERS.includes(v) ? v : 'sharp';
    } catch { return 'sharp'; }
}

function readHiText() {
    try { return localStorage.getItem('jootm2.hiText') !== '0'; } catch { return true; }
}

export class Presenter {
    /**
     * @param {HTMLCanvasElement} canvas 可见 canvas（CSS 尺寸固定 800×600 逻辑像素）
     * @param {HTMLCanvasElement} back   800×600 离屏 back buffer
     */
    constructor(canvas, back) {
        this.canvas = canvas;
        this.back = back;
        this.backCtx = back.getContext('2d', { alpha: false });
        this.ctx = canvas.getContext('2d', { alpha: false });
        this.mid = document.createElement('canvas');   // 整数倍中间层
        this.midCtx = this.mid.getContext('2d', { alpha: false });
        this.filter = readFilter();
        this.hiText = readHiText();
        this.scale = 1;          // 物理像素 / 逻辑像素
        this._texts = [];
        this._fontCache = new Map();
        this._hookText();
        this.resize(SCREEN_WIDTH, SCREEN_HEIGHT);
    }

    /** 按物理像素尺寸重设可见 canvas 位图（viewport.fitStage 回调）。 */
    resize(devW, devH) {
        devW = Math.max(1, Math.round(devW));
        devH = Math.max(1, Math.round(devH));
        if (this.canvas.width !== devW) this.canvas.width = devW;
        if (this.canvas.height !== devH) this.canvas.height = devH;
        this.scale = devW / SCREEN_WIDTH;
        const n = Math.max(1, Math.ceil(this.scale - 1e-3));
        const mw = SCREEN_WIDTH * n, mh = SCREEN_HEIGHT * n;
        if (this.mid.width !== mw) this.mid.width = mw;
        if (this.mid.height !== mh) this.mid.height = mh;
    }

    setFilter(f) {
        if (!FILTERS.includes(f)) return this.filter;
        this.filter = f;
        try { localStorage.setItem('jootm2.scaleFilter', f); } catch { /* 隐身模式 */ }
        return f;
    }

    setHiText(on) {
        this.hiText = !!on;
        try { localStorage.setItem('jootm2.hiText', on ? '1' : '0'); } catch { /* ignore */ }
        return this.hiText;
    }

    /** 拦截 back buffer 上的文字绘制：记录完整绘制状态，呈现时按物理分辨率重画。 */
    _hookText() {
        const bc = this.backCtx;
        const origFill = bc.fillText.bind(bc);
        const origStroke = bc.strokeText.bind(bc);
        const self = this;
        const capture = (op, text, x, y, maxWidth) => {
            const t = bc.getTransform();
            self._texts.push({
                op, text: String(text), x, y, maxWidth,
                font: bc.font,
                style: op === 'f' ? bc.fillStyle : bc.strokeStyle,
                lineWidth: bc.lineWidth,
                lineJoin: bc.lineJoin,
                align: bc.textAlign,
                baseline: bc.textBaseline,
                alpha: bc.globalAlpha,
                comp: bc.globalCompositeOperation,
                t: [t.a, t.b, t.c, t.d, t.e, t.f],
            });
        };
        bc.fillText = function (text, x, y, maxWidth) {
            if (!self.hiText || self.scale <= 1.001) return origFill(text, x, y, maxWidth);
            capture('f', text, x, y, maxWidth);
        };
        bc.strokeText = function (text, x, y, maxWidth) {
            if (!self.hiText || self.scale <= 1.001) return origStroke(text, x, y, maxWidth);
            capture('s', text, x, y, maxWidth);
        };
    }

    _scaledFont(font, k) {
        const key = font + '@' + k;
        let f = this._fontCache.get(key);
        if (f == null) {
            f = font.replace(FONT_PX_RE, (_, n) => (Number(n) * k).toFixed(2) + 'px');
            if (this._fontCache.size > 256) this._fontCache.clear();
            this._fontCache.set(key, f);
        }
        return f;
    }

    /** 一帧结束：back buffer → 可见 canvas（放大）+ 高清文字层。 */
    present() {
        const ctx = this.ctx;
        const W = this.canvas.width, H = this.canvas.height;
        const s = this.scale;
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'copy';
        if (Math.abs(s - 1) < 1e-3) {
            ctx.imageSmoothingEnabled = false;
            ctx.drawImage(this.back, 0, 0);
        } else if (this.filter === 'smooth' || s < 1) {
            ctx.imageSmoothingEnabled = true;
            ctx.imageSmoothingQuality = 'high';
            ctx.drawImage(this.back, 0, 0, W, H);
        } else {
            const n = Math.max(1, Math.ceil(s - 1e-3));
            if (this.filter === 'pixel' || Math.abs(s - n) < 1e-3) {
                ctx.imageSmoothingEnabled = false;
                ctx.drawImage(this.back, 0, 0, W, H);
            } else {
                // sharp-bilinear：最近邻放大到不小于目标的整数倍，再线性缩到目标
                const m = this.midCtx;
                m.globalCompositeOperation = 'copy';
                m.imageSmoothingEnabled = false;
                m.drawImage(this.back, 0, 0, this.mid.width, this.mid.height);
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(this.mid, 0, 0, W, H);
            }
        }
        ctx.globalCompositeOperation = 'source-over';
        if (this._texts.length) this._drawTexts(ctx, s);
    }

    _drawTexts(ctx, s) {
        const list = this._texts;
        this._texts = [];
        for (const c of list) {
            const [a, b, cc, d, e, f] = c.t;
            const plain = b === 0 && cc === 0 && a > 0 && d > 0;
            let px, py, mw;
            if (plain) {
                // 常见情况：只有平移（可能有整体缩放）→ 换算到物理坐标、字号直接放大，
                // 用单位矩阵画，字形按目标字号栅格化（含 hinting），最清晰。
                const k = s * a;
                ctx.setTransform(1, 0, 0, 1, 0, 0);
                ctx.font = this._scaledFont(c.font, k);
                ctx.lineWidth = c.lineWidth * k;
                px = (a * c.x + e) * s;
                py = (d * c.y + f) * s;
                mw = c.maxWidth != null ? c.maxWidth * k : undefined;
            } else {
                // 旋转/斜切：退回矩阵变换（仍是物理分辨率）
                ctx.setTransform(a * s, b * s, cc * s, d * s, e * s, f * s);
                ctx.font = c.font;
                ctx.lineWidth = c.lineWidth;
                px = c.x; py = c.y; mw = c.maxWidth;
            }
            ctx.textAlign = c.align;
            ctx.textBaseline = c.baseline;
            ctx.globalAlpha = c.alpha;
            ctx.globalCompositeOperation = c.comp === 'copy' ? 'source-over' : c.comp;
            ctx.lineJoin = c.lineJoin;
            if (c.op === 'f') {
                ctx.fillStyle = c.style;
                if (mw !== undefined) ctx.fillText(c.text, px, py, mw);
                else ctx.fillText(c.text, px, py);
            } else {
                ctx.strokeStyle = c.style;
                if (mw !== undefined) ctx.strokeText(c.text, px, py, mw);
                else ctx.strokeText(c.text, px, py);
            }
        }
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.globalAlpha = 1;
        ctx.globalCompositeOperation = 'source-over';
    }
}
