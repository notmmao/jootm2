// 移动端触屏控制：浮动虚拟摇杆 + 动作按钮。
// 设计要点：
//   1) 只有「移动触屏设备」才创建 UI（桌面端零 DOM、零监听，不干扰键鼠）；
//   2) 摇杆只采集输入，输出 8 方向 + 走/跑，由 PlayScene 复用既有 move 发送节流；
//   3) 所有触点坐标经 viewport.toLocal() 换算，兼容 #stage 的 CSS scale。
// 参考：幸存者分支 frontend/survivors/touch.mjs（同一套 Pointer Events 方案）。

import { toLocal } from '../core/viewport.mjs';

const STICK_R = 48;        // 摇杆判定半径（800×600 逻辑像素）
const AXIS_DEAD = 0.24;    // 死区：轻微抖动不算移动
const RUN_THRESHOLD = 0.72; // 推到 72% 以上自动跑（原版右键跑）

/**
 * 是否移动触屏设备。
 * - URL `?touch=1` / `?touch=0` 可强制覆盖（触屏笔记本手动开、自动化回归用）；
 * - 主判据：`pointer:coarse` + `maxTouchPoints>0`，避免带触摸屏的桌面误触发；
 * - 兜底：移动端 UA + 触点/ontouchstart（覆盖少数不报 pointer:coarse 的浏览器）。
 */
export function isTouchDevice() {
    try {
        const q = new URLSearchParams(location.search).get('touch');
        if (q === '1') return true;
        if (q === '0') return false;
    } catch (_) { /* ignore */ }
    try {
        const coarse = !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
        const points = navigator.maxTouchPoints || 0;
        if (coarse && points > 0) return true;
        const ua = navigator.userAgent || '';
        const mobileUA = /Android|iPhone|iPad|iPod|Mobile|Windows Phone|BlackBerry/i.test(ua);
        if (mobileUA && (points > 0 || 'ontouchstart' in window)) return true;
    } catch (_) { /* ignore */ }
    return false;
}

const BTN = 'position:absolute;border-radius:50%;background:rgba(18,28,42,.52);'
    + 'border:2px solid rgba(180,210,235,.55);color:#fff;font-family:SimSun,serif;'
    + 'text-shadow:0 1px 2px #000;pointer-events:auto;touch-action:manipulation;'
    + 'user-select:none;-webkit-user-select:none;-webkit-tap-highlight-color:transparent;'
    + 'box-shadow:0 2px 10px rgba(0,0,0,.45);';

export class TouchControls {
    /**
     * @param {HTMLElement} stage 父容器（800×600 逻辑坐标，外层 CSS scale）
     * @param {Object} handlers onAttack/onSkill(n)/onPotion/onPick/onBag/onMap/onMenu
     */
    constructor(stage, handlers = {}) {
        this.enabled = isTouchDevice();
        this.handlers = handlers;
        this.active = false;
        this.dx = 0; this.dy = 0;
        this.baseX = 0; this.baseY = 0;
        this.pointerId = null;
        this.root = null;
        if (!this.enabled) return;   // 桌面端：不建 DOM、不装监听

        document.body.classList.add('touch');
        this.stage = stage;
        this._build();
    }

    _build() {
        const root = document.createElement('div');
        root.id = 'touch-ui';
        root.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:6;';
        root.innerHTML = `
          <div id="touch-move-zone" style="position:absolute;left:8px;top:370px;width:224px;height:214px;
               pointer-events:auto;touch-action:none"></div>
          <div id="touch-stick" style="position:absolute;left:48px;top:426px;width:112px;height:112px;
               border-radius:50%;background:rgba(20,30,45,.42);border:2px solid rgba(150,200,235,.45);
               box-shadow:0 2px 12px rgba(0,0,0,.4);display:none">
            <div id="touch-knob" style="position:absolute;left:26px;top:26px;width:56px;height:56px;
                 border-radius:50%;background:rgba(150,205,245,.55);border:2px solid rgba(220,240,255,.8);
                 box-shadow:0 1px 6px rgba(0,0,0,.4)"></div>
          </div>
          <button id="tc-attack" style="${BTN}right:24px;top:352px;width:68px;height:68px;font-size:15px">攻击</button>
          <button id="tc-pick"   style="${BTN}right:104px;bottom:16px;width:50px;height:50px;font-size:13px">拾取</button>
          <button id="tc-potion" style="${BTN}right:162px;bottom:16px;width:50px;height:50px;font-size:13px">药</button>
          <button id="tc-s1" style="${BTN}right:220px;bottom:16px;width:50px;height:50px;font-size:13px">技1</button>
          <button id="tc-s2" style="${BTN}right:276px;bottom:16px;width:50px;height:50px;font-size:13px">技2</button>
          <button id="tc-s3" style="${BTN}right:332px;bottom:16px;width:50px;height:50px;font-size:13px">技3</button>
          <button id="tc-bag" style="${BTN}right:24px;top:274px;width:46px;height:46px;font-size:12px">包</button>
          <button id="tc-map" style="${BTN}right:24px;top:222px;width:46px;height:46px;font-size:12px">图</button>
          <button id="tc-menu" style="${BTN}right:78px;top:274px;width:46px;height:46px;font-size:12px">单</button>
          <button id="tc-chat" style="${BTN}right:78px;top:222px;width:46px;height:46px;font-size:12px">说</button>`;
        this.stage.appendChild(root);
        this.root = root;
        this.zone = root.querySelector('#touch-move-zone');
        this.stick = root.querySelector('#touch-stick');
        this.knob = root.querySelector('#touch-knob');

        const zone = this.zone;
        zone.addEventListener('pointerdown', (e) => {
            if (this.pointerId !== null) return;   // 只跟踪第一根手指
            this.pointerId = e.pointerId;
            try { zone.setPointerCapture(e.pointerId); } catch (_) { /* ignore */ }
            const p = toLocal(e.clientX, e.clientY);
            // 浮动摇杆：底座出现在手指按下处
            this.baseX = p.x; this.baseY = p.y;
            this.stick.style.left = (p.x - 56) + 'px';
            this.stick.style.top = (p.y - 56) + 'px';
            this.stick.style.display = 'block';
            this.active = true;
            this._updateAxis(p);
            e.preventDefault();
        });
        zone.addEventListener('pointermove', (e) => {
            if (e.pointerId !== this.pointerId) return;
            this._updateAxis(toLocal(e.clientX, e.clientY));
            e.preventDefault();
        });
        const end = (e) => {
            if (e.pointerId !== this.pointerId) return;
            this.pointerId = null;
            this.reset();
            e.preventDefault();
        };
        zone.addEventListener('pointerup', end);
        zone.addEventListener('pointercancel', end);

        const bind = (id, fn) => {
            const el = root.querySelector(id);
            el.addEventListener('pointerdown', (e) => e.stopPropagation());
            el.addEventListener('click', (e) => {
                e.preventDefault(); e.stopPropagation();
                if (typeof fn === 'function') fn();
            });
        };
        bind('#tc-attack', () => this.handlers.onAttack && this.handlers.onAttack());
        bind('#tc-pick', () => this.handlers.onPick && this.handlers.onPick());
        bind('#tc-potion', () => this.handlers.onPotion && this.handlers.onPotion());
        bind('#tc-bag', () => this.handlers.onBag && this.handlers.onBag());
        bind('#tc-map', () => this.handlers.onMap && this.handlers.onMap());
        bind('#tc-menu', () => this.handlers.onMenu && this.handlers.onMenu());
        bind('#tc-chat', () => this.handlers.onChat && this.handlers.onChat());
        for (let i = 1; i <= 3; i++) {
            bind('#tc-s' + i, () => this.handlers.onSkill && this.handlers.onSkill(i));
        }
    }

    _updateAxis(p) {
        let vx = p.x - this.baseX, vy = p.y - this.baseY;
        const len = Math.hypot(vx, vy);
        if (len > STICK_R) { vx = vx / len * STICK_R; vy = vy / len * STICK_R; }
        this.dx = vx / STICK_R;
        this.dy = vy / STICK_R;
        this.knob.style.transform = `translate(${vx}px,${vy}px)`;
    }

    /** 摇杆模拟量：{dx, dy, mag}，mag ∈ [0,1]。 */
    getAxis() {
        return { dx: this.dx, dy: this.dy, mag: Math.hypot(this.dx, this.dy) };
    }

    /** 映射为服务端 8 方向（0上 1右上 2右 3右下 4下 5左下 6左 7左上）；未过死区返回 -1。 */
    direction() {
        const a = this.getAxis();
        if (a.mag < AXIS_DEAD) return -1;
        const idx = ((Math.round(Math.atan2(a.dy, a.dx) / (Math.PI / 4)) % 8) + 8) % 8;
        return [2, 3, 4, 5, 6, 7, 0, 1][idx];
    }

    /** 推力足够大时跑（等同原版右键），否则走。 */
    running() { return this.getAxis().mag >= RUN_THRESHOLD; }

    reset() {
        this.active = false; this.dx = 0; this.dy = 0; this.pointerId = null;
        this.knob && (this.knob.style.transform = 'translate(0,0)');
        this.stick && (this.stick.style.display = 'none');
    }

    destroy() {
        this.reset();
        if (this.root) this.root.remove();
        this.root = null;
        document.body.classList.remove('touch');
    }
}
