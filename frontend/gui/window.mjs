// DWindow 可拖动窗口（对应旧 TDWindow）
// 支持拖动、点击置顶、模态

import { DControl } from './control.mjs';
import { toLocal } from '../core/viewport.mjs';

export class DWindow extends DControl {
    constructor(manager, parent = null) {
        super(manager, parent);
        this.floating = false;      // 浮动窗口
        this.modal = false;         // 模态窗口
        this.dragging = false;
        this._dragOffsetX = 0;
        this._dragOffsetY = 0;

        // 点击置顶 + 拖动
        this.el.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            this.bringToFront();
            // 记录拖动偏移：换算成 #gui 的 800×600 逻辑坐标，界面放大后拖拽才跟手
            const p = toLocal(e.clientX, e.clientY, this.manager.container);
            this._dragOffsetX = p.x - this.left;
            this._dragOffsetY = p.y - this.top;
            this.dragging = true;
        });
    }

    // 由 manager 在全局 mousemove 时调用
    onGlobalMouseMove(clientX, clientY) {
        if (!this.dragging) return;
        this.setBounds(
            clientX - this._dragOffsetX,
            clientY - this._dragOffsetY,
            this.width, this.height
        );
    }

    onGlobalMouseUp() {
        this.dragging = false;
    }

    // 显示模态（创建遮罩层）
    showModal() {
        this.modal = true;
        this.manager.setModal(this);
        this.show();
        this.bringToFront();
    }

    closeModal() {
        this.modal = false;
        this.manager.clearModal();
        this.hide();
    }
}
