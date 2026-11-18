/**
 * 面对面交易 + 组队 UI（对齐原版 DDealDlg / DGroupDlg 行为，视觉用 DOM 近似）。
 *
 * 设计：
 * - 挂在 #stage 的 GUI 容器（800×600 逻辑坐标），随界面缩放；不挂 body。
 * - 服务端协议见 pyserver/handlers_social.py：tradeInvite/tradeStart/tradeState/
 *   tradeEnd、partyInvite/partyInfo/partyChat/partySys。
 * - 交易双方各一个窗口：左=自己（可放/取物品、设金币、锁定确认），右=对方（只读）。
 * - 组队面板：队员列表（名字/等级/血条/地图）、邀请输入、离线/退队、队伍聊天。
 *
 * 用法：new TradePartyUI({ container, conn, libUrl, libTexture, onLog })，PlayScene.onMessage
 * 把上述 type 转给 handleMessage(type,msg)，onExit 调 destroy()。
 */
import { PALETTE_RGBA } from '../gfx/palette.mjs';

/** 调色板索引 → CSS 颜色（与 MapActor 同表，原版 GetRGB/DefColorTable）。 */
function paletteFill(idx) {
    if (idx == null) return null;
    const i = Number(idx) * 4;
    if (!Number.isInteger(i) || i < 0 || i + 2 >= PALETTE_RGBA.length) return null;
    return `rgb(${PALETTE_RGBA[i]},${PALETTE_RGBA[i + 1]},${PALETTE_RGBA[i + 2]})`;
}

// 查看他人装备窗的 13 槽坐标 = FState.pas:1272-1325 的 DSWxxx（与 F10 的 EQUIP_SLOTS 同口径）
// [slot, x, y, w, h]，相对窗口左上角；衣服/武器/头盔叠在人物身上，此处仅作悬停热区。
const DSW_SLOTS = [
    [0,   96, 122, 53, 112],
    [1,   47,  80, 47,  87],
    [2,  168, 125, 34,  31],
    [3,  168,  87, 34,  31],
    [4,  115,  93, 18,  18],
    [5,  168, 176, 34,  31],
    [6,   42, 176, 34,  31],
    [7,  168, 215, 34,  31],
    [8,   42, 215, 34,  31],
    [9,   42, 254, 34,  31],
    [10,  84, 254, 34,  31],
    [11, 126, 254, 34,  31],
    [12, 168, 254, 34,  31],
];

export class TradePartyUI {
    constructor({ container, conn, libUrl, libTexture, onLog }) {
        this.container = container;
        this.conn = conn;
        this.libUrl = libUrl;
        // WIL 纹理 + 像素偏移（画查看装备窗时把衣服/武器/头盔叠在人物身上要用到）
        this.libTexture = libTexture || (async () => null);
        this.onLog = onLog || (() => {});
        this.trade = null;          // { selfName, otherName, state }
        this.party = null;          // { leaderId, members }
        this._layers = [];          // 本模块创建的 DOM 根节点（destroy 时统一移除）
        this._goldDlg = null;
    }

    _send(type, payload = {}) {
        this.conn.send(type, payload);
    }

    _mk(css = '', parent = null) {
        const el = document.createElement('div');
        el.style.cssText = css;
        (parent || this.container).appendChild(el);
        return el;
    }

    _track(el) {
        this._layers.push(el);
        return el;
    }

    /**
     * 绑定弹窗输入框：拦住键盘事件别冒泡到 PlayScene 的全局快捷键
     * （尤其数字键 1..6 会被当快捷用药水 preventDefault，导致 "t1" 里的 1 打不进去），
     * 并支持回车提交 / Esc 失焦。
     */
    _bindInput(input, onSubmit) {
        input.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') { e.preventDefault(); onSubmit(); }
            else if (e.key === 'Escape') { e.preventDefault(); input.blur(); }
        });
        return input;
    }

    /** 统一窗口外壳：可拖动，背景贴图 + 标题 + 关闭按钮。 */
    _window(title, left, top, width, height, bgIdx, onClose) {
        const win = this._track(this._mk(
            'position:absolute;pointer-events:auto;font-family:SimSun,serif;color:#fff;'
            + `left:${left}px;top:${top}px;width:${width}px;height:${height}px;z-index:30;`
            + 'background:rgba(20,16,10,.96);border:2px solid #6b5636;box-shadow:0 4px 18px rgba(0,0,0,.7);'));
        const bg = document.createElement('img');
        bg.draggable = false;
        bg.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;opacity:.25;';
        win.appendChild(bg);
        this.libUrl('prguse', bgIdx).then((url) => {
            if (url && win.isConnected) { bg.src = url; bg.style.width = width + 'px'; bg.style.height = height + 'px'; }
        });
        const bar = document.createElement('div');
        bar.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:20px;cursor:move;'
            + 'background:rgba(0,0,0,.35);font-size:12px;line-height:20px;padding-left:8px;'
            + 'box-sizing:border-box;color:#ffd76a;user-select:none;';
        bar.textContent = title;
        win.appendChild(bar);
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;right:4px;top:2px;width:16px;height:16px;'
            + 'text-align:center;line-height:16px;cursor:pointer;color:#ff9a8a;font-size:14px;';
        close.textContent = '×';
        close.onclick = onClose;
        win.appendChild(close);
        this._drag(win, bar);
        return win;
    }

    _drag(win, handle) {
        let ox = 0, oy = 0, dragging = false;
        handle.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            const stage = win.closest('#stage') || this.container;
            const r = stage.getBoundingClientRect();
            const scale = r.width / 800 || 1;
            ox = e.clientX - (r.left + parseFloat(win.style.left) * scale);
            oy = e.clientY - (r.top + parseFloat(win.style.top) * scale);
            dragging = true;
            win.style.zIndex = String(++this._z || (this._z = 31));
            e.preventDefault();
        });
        document.addEventListener('mousemove', (e) => {
            if (!dragging || !win.isConnected) return;
            const stage = win.closest('#stage') || this.container;
            const r = stage.getBoundingClientRect();
            const scale = r.width / 800 || 1;
            win.style.left = Math.max(0, Math.min(800 - parseFloat(win.style.width),
                (e.clientX - r.left - ox) / scale)) + 'px';
            win.style.top = Math.max(0, Math.min(600 - parseFloat(win.style.height),
                (e.clientY - r.top - oy) / scale)) + 'px';
        });
        document.addEventListener('mouseup', () => { dragging = false; });
    }

    // ------------------------------------------------------------ 交易

    requestTrade(name) {
        if (!name) return;
        this._send('tradeRequest', { name });
    }

    /** 交易按钮：优先用当前最近的同图玩家，否则弹输入框让玩家填名字。 */
    promptTrade(nearbyName = '') {
        if (nearbyName) { this.requestTrade(nearbyName); return; }
        if (this._promptWin) return;
        const win = this._window('发起交易', 280, 220, 240, 118, 385,
            () => { this._promptWin.remove(); this._promptWin = null; });
        this._promptWin = win;
        const tip = document.createElement('div');
        tip.style.cssText = 'position:absolute;left:12px;top:30px;width:216px;'
            + 'text-align:center;font-size:12px;color:#c9b98f;';
        tip.textContent = '输入附近玩家的名字发起交易';
        win.appendChild(tip);
        const input = document.createElement('input');
        input.type = 'text';
        input.style.cssText = 'position:absolute;left:12px;top:52px;width:150px;height:22px;'
            + 'font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        const submit = () => {
            this.requestTrade(input.value.trim());
            win.remove(); this._promptWin = null;
        };
        this._bindInput(input, submit);
        win.appendChild(input);
        const ok = document.createElement('div');
        ok.style.cssText = 'position:absolute;left:170px;top:52px;width:58px;height:22px;'
            + 'cursor:pointer;text-align:center;line-height:22px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
        ok.textContent = '邀请';
        ok.onclick = submit;
        win.appendChild(ok);
    }

    respondTrade(accept) {
        this._send(accept ? 'tradeAccept' : 'tradeReject');
        if (this._inviteWin) { this._inviteWin.remove(); this._inviteWin = null; }
    }

    _itemGrid(parent, items, side) {
        // 原版 DDGrid / DDRGrid：5×2，36×33，起点 (21,56)（FState.pas:1586-1597）
        const grid = document.createElement('div');
        grid.style.cssText = 'position:absolute;left:21px;top:56px;width:180px;height:66px;'
            + 'display:grid;grid-template-columns:repeat(5,36px);grid-auto-rows:33px;';
        for (let i = 0; i < 10; i++) {
            const cell = document.createElement('div');
            cell.style.cssText = 'width:36px;height:33px;box-sizing:border-box;'
                + 'display:grid;place-items:center;overflow:hidden;cursor:'
                + (side === 'self' && items[i] ? 'pointer' : 'default') + ';';
            const it = items[i];
            if (it) {
                cell.title = it.name;
                this.libUrl('items', it.looks).then((url) => {
                    if (url && cell.isConnected) {
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'max-width:32px;max-height:30px;';
                        cell.appendChild(im);
                    }
                });
                if (side === 'self') {
                    cell.onclick = () => this._send('tradeRemoveItem', { makeIndex: it.makeIndex });
                }
            }
            grid.appendChild(cell);
        }
        parent.appendChild(grid);
    }

    _renderTradeSide(win, side, data) {
        // 原版布局：买进方(prguse390)在左、卖出方(prguse389)在右，贴屏幕顶。
        const x = side === 'self' ? 220 : 0;
        const box = document.createElement('div');
        box.style.cssText = `position:absolute;left:${x}px;top:0;width:220px;height:175px;`;
        const name = document.createElement('div');
        name.style.cssText = 'position:absolute;left:0;top:32px;width:100%;text-align:center;'
            + 'font-size:12px;color:#e8d9b0;text-shadow:1px 1px 0 #000;';
        name.textContent = data.name || '';
        box.appendChild(name);
        this._itemGrid(box, data.items || [], side);
        const sub = document.createElement('div');
        sub.style.cssText = 'position:absolute;left:40px;top:140px;width:170px;'
            + 'font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;';
        sub.textContent = (data.gold || 0).toLocaleString() + ' 金币';
        box.appendChild(sub);
        win.appendChild(box);
        return sub;
    }

    _openTradeWindow() {
        if (this._tradeWin) return this._tradeWin;
        // 原版 DDealRemoteDlg(Prguse390 220×175) + DDealDlg(Prguse389 236×175)，
        // 分别贴屏幕左上/右上（FState.pas:1581-1604），不用自绘窗口外壳。
        const win = this._track(this._mk(
            'position:absolute;left:344px;top:0;width:456px;height:175px;'
            + 'pointer-events:none;z-index:30;font-family:SimSun,serif;color:#fff;'));
        this._tradeWin = win;
        for (const [idx, x] of [[390, 0], [389, 220]]) {
            this.libUrl('prguse', idx).then((url) => {
                if (url && win.isConnected) {
                    const im = document.createElement('img');
                    im.src = url; im.draggable = false;
                    im.style.cssText = `position:absolute;left:${x}px;top:0;`
                        + 'pointer-events:none;';
                    win.insertBefore(im, win.firstChild);
                }
            });
        }
        // DDGold(Prguse28) 金币图标 + 点击热区（原版 :1597-1599）
        this.libUrl('prguse', 28).then((url) => {
            if (url && win.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:231px;top:137px;pointer-events:none;';
                win.insertBefore(im, win.firstChild);
            }
        });
        const goldBtn = document.createElement('div');
        goldBtn.style.cssText = 'position:absolute;left:231px;top:137px;width:36px;height:21px;'
            + 'cursor:pointer;pointer-events:auto;';
        goldBtn.title = '设置交易金币';
        goldBtn.onclick = () => this._showGoldDialog();
        win.appendChild(goldBtn);
        // DDealOk(Prguse391)：图片自带「锁定」字样，不另画按钮
        const lockBtn = document.createElement('div');
        lockBtn.style.cssText = 'position:absolute;left:375px;top:128px;width:44px;height:16px;'
            + 'cursor:pointer;pointer-events:auto;';
        lockBtn.title = '锁定 / 确认';
        lockBtn.onclick = () => this._send('tradeConfirm');
        this._tradeLockBtn = lockBtn;
        this.libUrl('prguse', 391).then((url) => {
            if (url && lockBtn.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                lockBtn.appendChild(im);
            }
        });
        win.appendChild(lockBtn);
        // DDealClose(Prguse64)：原版在卖出方窗口 (220,42)
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:440px;top:42px;width:16px;height:23px;'
            + 'cursor:pointer;pointer-events:auto;';
        close.title = '取消交易';
        close.onclick = () => this._send('tradeCancel');
        this.libUrl('prguse', 64).then((url) => {
            if (url && close.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                close.appendChild(im);
            }
        });
        win.appendChild(close);
        return win;
    }

    _renderTrade(state) {
        if (!state) return;
        const win = this._openTradeWindow();
        win.querySelectorAll('[data-tp-part]').forEach((e) => e.remove());
        // 用标记包裹两侧内容，便于刷新时只替换这部分
        const parts = [];
        for (const side of ['self', 'other']) {
            const holder = document.createElement('div');
            holder.dataset.tpPart = '1';
            holder.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;pointer-events:none;';
            this._renderTradeSide(holder, side, state[side]);
            holder.querySelectorAll('*').forEach((c) => {
                if (c.tagName === 'IMG' || c.style.cursor === 'pointer') c.style.pointerEvents = 'auto';
            });
            win.appendChild(holder);
            parts.push(holder);
        }
        if (this._tradeLockBtn) {
            // 原版 DDealOk 图片自带文字，只改提示与轻微着色，不覆盖子节点
            this._tradeLockBtn.title = state.self.confirm ? '已锁定' : '锁定 / 确认';
            this._tradeLockBtn.style.filter = state.self.confirm
                ? 'brightness(1.35) saturate(.6)' : '';
        }
    }

    _showGoldDialog() {
        if (this._goldDlg) return;
        const dlg = this._window('放入金币', 250, 250, 220, 110, 385,
            () => { this._goldDlg.remove(); this._goldDlg = null; });
        // 交易窗口的格子层在每次 tradeState 刷新时都会 re-insert，弹窗必须
        // 显式高于它，否则输入框会被交易面板盖住点不到（用户 2026-11-18）。
        dlg.style.zIndex = '60';
        this._goldDlg = dlg;
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '金币总额';
        input.style.cssText = 'position:absolute;left:16px;top:36px;width:120px;height:20px;'
            + 'z-index:1;font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        const submit = () => {
            const n = parseInt(input.value.replace(/[^0-9]/g, ''), 10);
            if (Number.isFinite(n) && n >= 0) this._send('tradeSetGold', { amount: n });
            dlg.remove(); this._goldDlg = null;
        };
        this._bindInput(input, submit);
        dlg.appendChild(input);
        input.focus();
        const ok = document.createElement('div');
        ok.style.cssText = 'position:absolute;left:146px;top:36px;width:60px;height:20px;'
            + 'cursor:pointer;text-align:center;line-height:20px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
        ok.textContent = '确定';
        ok.onclick = submit;
        dlg.appendChild(ok);
        const tip = document.createElement('div');
        tip.style.cssText = 'position:absolute;left:16px;top:66px;width:190px;font-size:11px;color:#c9b98f;';
        tip.textContent = '输入交易栏中的金币总数（可下调取回）';
        dlg.appendChild(tip);
    }

    _showTradeInvite(fromName) {
        if (this._inviteWin) this._inviteWin.remove();
        const win = this._window('交易请求', 280, 220, 240, 120, 385,
            () => this.respondTrade(false));
        this._inviteWin = win;
        const t = document.createElement('div');
        t.style.cssText = 'position:absolute;left:12px;top:34px;width:216px;'
            + 'text-align:center;font-size:12px;line-height:1.6;';
        t.textContent = `${fromName} 想与你交易。`;
        win.appendChild(t);
        for (const [label, accept, x] of [['拒绝', false, 24], ['接受', true, 132]]) {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:${x}px;top:76px;width:80px;height:24px;`
                + 'cursor:pointer;text-align:center;line-height:24px;font-size:12px;'
                + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
            b.textContent = label;
            b.onclick = () => this.respondTrade(accept);
            win.appendChild(b);
        }
    }

    // ------------------------------------------------------------ 组队

    inviteParty(name) {
        if (!name) return;
        this._send('partyInvite', { name });
    }

    respondParty(accept) {
        this._send(accept ? 'partyAccept' : 'partyReject');
        if (this._partyInviteWin) { this._partyInviteWin.remove(); this._partyInviteWin = null; }
    }

    toggleParty() {
        if (this._partyWin && this._partyWin.isConnected) {
            this._partyWin.remove(); this._partyWin = null;
        } else {
            this._openPartyWindow();
            this._send('partyInfo');
        }
    }

    _openPartyWindow() {
        if (this._partyWin && this._partyWin.isConnected) return this._partyWin;
        // 原版 DGroupDlg：Prguse[120] 276×242，居中（FState.pas:1553-1561）
        const win = this._track(this._mk(
            'position:absolute;left:262px;top:179px;width:276px;height:242px;'
            + 'pointer-events:none;z-index:30;font-family:SimSun,serif;color:#e8d9b0;'));
        this._partyWin = win;
        this.libUrl('prguse', 120).then((url) => {
            if (url && win.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                win.insertBefore(im, win.firstChild);
            }
        });
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:260px;top:0;width:16px;height:23px;'
            + 'cursor:pointer;pointer-events:auto;';
        close.title = '关闭组队窗口';
        close.onclick = () => { win.remove(); this._partyWin = null; this._partyList = null; };
        this.libUrl('prguse', 64).then((url) => {
            if (url && close.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                close.appendChild(im);
            }
        });
        win.appendChild(close);

        const list = document.createElement('div');
        list.style.cssText = 'position:absolute;left:16px;top:42px;width:244px;height:88px;'
            + 'overflow:auto;pointer-events:auto;';
        win.appendChild(list);
        this._partyList = list;

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '输入玩家名邀请入队';
        input.style.cssText = 'position:absolute;left:20px;top:136px;width:150px;height:20px;'
            + 'pointer-events:auto;font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        const submit = () => { this.inviteParty(input.value.trim()); input.value = ''; };
        this._bindInput(input, submit);
        win.appendChild(input);
        // 原版 DGrpAddMem(Prguse124)：邀请按钮热区 (97,203)
        this._partyButton(win, 124, 97, 203, submit, '邀请入队');

        // 原版 DGrpDelMem(Prguse125)：退出队伍热区 (172,203)
        this._partyButton(win, 125, 172, 203,
                          () => this._send('partyLeave'), '退出队伍');

        const chat = document.createElement('input');
        chat.type = 'text';
        chat.placeholder = '队伍聊天 (回车发送)';
        chat.style.cssText = 'position:absolute;left:20px;top:160px;width:236px;height:20px;'
            + 'pointer-events:auto;font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        this._bindInput(chat, () => {
            if (chat.value.trim()) this._send('partyChat', { text: chat.value.trim() });
            chat.value = ''; chat.blur();
        });
        win.appendChild(chat);
        return win;
    }

    /** 原版组队按钮：贴图 + 透明热区（不另画边框）。 */
    _partyButton(win, idx, x, y, onclick, title) {
        const b = document.createElement('div');
        b.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:72px;height:19px;`
            + 'cursor:pointer;pointer-events:auto;';
        b.title = title;
        b.onclick = onclick;
        this.libUrl('prguse', idx).then((url) => {
            if (url && b.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                b.appendChild(im);
            }
        });
        win.appendChild(b);
        return b;
    }

    _renderParty(data) {
        this.party = data;
        if (!this._partyList || !this._partyList.isConnected) return;
        this._partyList.innerHTML = '';
        const members = data.members || [];
        if (!members.length) {
            const empty = document.createElement('div');
            empty.style.cssText = 'padding:10px;font-size:12px;color:#9fb08a;';
            empty.textContent = '你还没有队伍。邀请附近玩家开始组队。';
            this._partyList.appendChild(empty);
            return;
        }
        for (const m of members) {
            const row = document.createElement('div');
            row.style.cssText = 'position:relative;height:26px;padding:2px 6px;'
                + 'border-bottom:1px solid #3a3120;font-size:12px;';
            const label = document.createElement('div');
            label.style.cssText = 'position:absolute;left:6px;top:2px;color:'
                + (m.id === data.leaderId ? '#ffd76a' : '#e8d9b0') + ';';
            label.textContent = (m.id === data.leaderId ? '★' : '　')
                + `${m.name} Lv.${m.level}` + (m.online ? '' : ' [离线]');
            row.appendChild(label);
            const barBg = document.createElement('div');
            barBg.style.cssText = 'position:absolute;left:6px;top:17px;width:180px;height:6px;'
                + 'background:#3a1010;border:1px solid #6b5636;';
            const ratio = m.maxHp > 0 ? Math.max(0, Math.min(1, m.hp / m.maxHp)) : 0;
            const bar = document.createElement('div');
            bar.style.cssText = `width:${(ratio * 100).toFixed(1)}%;height:100%;background:#c8342a;`;
            barBg.appendChild(bar);
            row.appendChild(barBg);
            this._partyList.appendChild(row);
        }
    }

    _showPartyInvite(fromName) {
        if (this._partyInviteWin) this._partyInviteWin.remove();
        const win = this._window('组队邀请', 280, 220, 240, 120, 385,
            () => this.respondParty(false));
        this._partyInviteWin = win;
        const t = document.createElement('div');
        t.style.cssText = 'position:absolute;left:12px;top:34px;width:216px;'
            + 'text-align:center;font-size:12px;line-height:1.6;';
        t.textContent = `${fromName} 邀请你加入队伍。`;
        win.appendChild(t);
        for (const [label, accept, x] of [['拒绝', false, 24], ['接受', true, 132]]) {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:${x}px;top:76px;width:80px;height:24px;`
                + 'cursor:pointer;text-align:center;line-height:24px;font-size:12px;'
                + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
            b.textContent = label;
            b.onclick = () => this.respondParty(accept);
            win.appendChild(b);
        }
    }

    // ------------------------------------------------------------ 在线列表

    /** 在线玩家名单：原版无此功能，本项目新增（名字/等级/职业/地图）。 */
    toggleOnlineList() {
        if (this._onlineWin && this._onlineWin.isConnected) {
            this._onlineWin.remove(); this._onlineWin = null;
            return;
        }
        const win = this._window('在线玩家', 250, 100, 300, 340, 385,
            () => { win.remove(); this._onlineWin = null; this._onlineListEl = null; });
        this._onlineWin = win;
        const tip = document.createElement('div');
        tip.style.cssText = 'position:absolute;left:10px;top:28px;width:280px;'
            + 'font-size:11px;color:#9fb08a;';
        tip.textContent = '名字 / 等级 / 职业 / 所在地图';
        win.appendChild(tip);
        const list = document.createElement('div');
        list.style.cssText = 'position:absolute;left:10px;top:46px;width:280px;height:284px;'
            + 'overflow:auto;background:rgba(0,0,0,.25);border:1px solid #4a3f28;';
        win.appendChild(list);
        this._onlineListEl = list;
        this._send('onlineList');   // 请求最新名单（收到 onlineList 消息后渲染）
    }

    _renderOnlineList(msg) {
        if (!this._onlineWin || !this._onlineWin.isConnected) this.toggleOnlineList();
        const list = this._onlineListEl;
        if (!list) return;
        list.innerHTML = '';
        const players = msg.players || [];
        if (!players.length) {
            const empty = document.createElement('div');
            empty.style.cssText = 'padding:10px;font-size:12px;color:#9fb08a;';
            empty.textContent = '当前没有在线玩家。';
            list.appendChild(empty);
            return;
        }
        const JOBS = ['战士', '法师', '道士'];
        for (const pl of players) {
            const row = document.createElement('div');
            row.style.cssText = 'height:22px;line-height:22px;padding:0 6px;font-size:12px;'
                + 'border-bottom:1px solid #3a3120;color:#e8d9b0;cursor:default;';
            const mt = pl.mapTitle || pl.map || '?';
            row.textContent = `${pl.name}  Lv.${pl.level}  ${JOBS[pl.job] || '?'}  @ ${mt}`;
            row.title = `${pl.name} — ${mt}${pl.map && pl.map !== mt ? ` (${pl.map})` : ''}`;
            list.appendChild(row);
        }
    }

    // ------------------------------------------------------------ 查看他人装备

    /** Ctrl+右键玩家后收到 userState 快照，弹原版 DUserState1 风格的装备窗。
     *  ① 底图 Prguse3[207]  ② 半身像 Prguse3[29/30]@(38,52)
     *  ③ 头发/衣服/武器/头盔叠在人物身上 @(31,96)+偏移（FState.pas:5295-5332）
     *  ④ 13 槽透明热区（DSWxxx）  ⑤ 名字居中@(122,23)  ⑥ 关闭键 Prguse[371]@(8,39)
     */
    async _showUserState(msg) {
        if (this._inspectWin) { this._inspectWin.remove(); this._inspectWin = null; }
        const win = this._track(this._mk(
            'position:absolute;left:568px;top:0;width:232px;height:362px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;z-index:30;'));
        this._inspectWin = win;

        // ① 窗口底图：Prguse3[207]（232×362，与本项目 F10 状态窗同一张，自带底部描述框）
        this.libUrl('prguse3', 207).then((url) => {
            if (url && win.isConnected) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;pointer-events:none;';
                win.insertBefore(bg, win.firstChild);
            }
        });

        // ② 人物半身像：男 Prguse3[29] / 女 Prguse3[30]，画在 (38,52)
        const sex = Number(msg.sex) === 1 ? 1 : 0;
        const hair = Number(msg.hair) || 0;
        this.libUrl('prguse3', sex ? 30 : 29).then((url) => {
            if (url && win.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:38px;top:52px;pointer-events:none;';
                win.insertBefore(im, win.firstChild.nextSibling); // 底图之后、装备叠层之前
            }
        });

        // ③ 头发/衣服/武器/头盔叠在人物身上（原版顺序，基准 31,96 + 纹理自带 px/py）
        const figure = this._mk('position:absolute;left:0;top:0;width:232px;height:362px;'
            + 'pointer-events:none;', win);
        const bySlot = {};
        for (const it of (msg.items || [])) if (it.slot != null) bySlot[it.slot] = it;
        const overlay = async (lib, idx) => {
            if (idx == null || idx < 0) return;
            let tex = null;
            try { tex = await this.libTexture(lib, idx); } catch (_) { tex = null; }
            if (!tex || !figure.isConnected) return;
            const src = tex.canvas;
            const cv = document.createElement('canvas');
            cv.width = src.width; cv.height = src.height;
            cv.getContext('2d').drawImage(src, 0, 0);
            cv.style.cssText = `position:absolute;left:${31 + (tex.px || 0)}px;`
                + `top:${96 + (tex.py || 0)}px;`;
            figure.appendChild(cv);
        };
        // 发型：原版 440+hair div 2（男）/ 480+（女），Prguse。
        // 纹理异步补齐即可（不阻塞名字/槽位/等级这些 DOM 骨架的建立）。
        overlay('prguse', (sex ? 480 : 440) + Math.floor(hair / 2));
        if (bySlot[0]) overlay('stateitem', bySlot[0].looks);  // 衣服
        if (bySlot[1]) overlay('stateitem', bySlot[1].looks);  // 武器
        if (bySlot[4]) overlay('stateitem', bySlot[4].looks);  // 头盔

        // ④ 13 槽透明热区（坐标 = DSWxxx）：叠在人物身上的 0/1/4 不放图标，其余放 WStateItem
        const ICON_SLOTS = new Set([2, 3, 5, 6, 7, 8, 9, 10, 11, 12]);
        const LABELS = ['衣服', '武器', '右手', '项链', '头盔', '手镯L', '手镯R',
            '戒指L', '戒指R', '护身符', '腰带', '靴子', '宝石'];
        for (const [slot, x, y, w, h] of DSW_SLOTS) {
            const it = bySlot[slot];
            const cell = this._mk(`position:absolute;left:${x}px;top:${y}px;`
                + `width:${w}px;height:${h}px;cursor:help;box-sizing:border-box;`, win);
            cell.title = it ? `${LABELS[slot]}：${it.name}` : LABELS[slot];
            if (it && ICON_SLOTS.has(slot)) {
                this.libUrl('stateitem', it.looks).then((url) => {
                    if (url && cell.isConnected) {
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'width:100%;height:100%;object-fit:contain;';
                        cell.appendChild(im);
                    }
                });
            }
        }

        // ⑤ 名字（原版居中 122,23，用原版关系色索引）；等级/职业放底部描述框
        const name = document.createElement('div');
        name.style.cssText = 'position:absolute;left:0;top:23px;width:232px;text-align:center;'
            + 'font-size:12px;color:#ffe9b0;text-shadow:1px 1px 0 #000;pointer-events:none;';
        name.textContent = msg.name || '?';
        const c = paletteFill(msg.nameColor);
        if (c) name.style.color = c;
        win.appendChild(name);
        const JOBS = ['战士', '法师', '道士'];
        const info = document.createElement('div');
        info.style.cssText = 'position:absolute;left:40px;top:307px;width:160px;height:44px;'
            + 'font-size:12px;line-height:15px;color:#e8d9b0;pointer-events:none;';
        info.textContent = `Lv.${msg.level || '?'}  ${JOBS[msg.job] || ''}`;
        win.appendChild(info);

        // ⑥ 关闭键：原版 DCloseUS1=Prguse[371] @ (8,39)，按下态图片平时隐藏
        const close = this._mk('position:absolute;left:8px;top:39px;width:16px;height:23px;'
            + 'cursor:pointer;', win);
        close.addEventListener('click', () => { win.remove(); this._inspectWin = null; });
        this.libUrl('prguse', 371).then((url) => {
            if (url && close.isConnected) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'visibility:hidden;display:block;';
                close.appendChild(im);
                close.addEventListener('mousedown', () => { im.style.visibility = 'visible'; });
                const restore = () => { im.style.visibility = 'hidden'; };
                close.addEventListener('mouseup', restore);
                close.addEventListener('mouseleave', restore);
            }
        });
    }

    // ------------------------------------------------------------ 消息入口

    handleMessage(type, msg) {
        switch (type) {
            case 'tradeInvite':
                this._showTradeInvite(msg.fromName || '玩家');
                break;
            case 'tradeStart':
                this.trade = { selfName: msg.selfName, otherName: msg.otherName };
                this._openTradeWindow();
                break;
            case 'tradeState':
                this._renderTrade(msg);
                break;
            case 'tradeEnd':
                if (this._tradeWin) { this._tradeWin.remove(); this._tradeWin = null; }
                if (this._goldDlg) { this._goldDlg.remove(); this._goldDlg = null; }
                this.trade = null;
                break;
            case 'partyInvite':
                this._showPartyInvite(msg.fromName || '玩家');
                break;
            case 'partyInfo':
                this._renderParty(msg);
                break;
            case 'partySys':
                this.onLog(msg.text || '', '#e8d9b0');
                break;
            case 'partyChat':
                this.onLog(`[队伍] ${msg.name}: ${msg.text}`, '#9fe08a');
                break;
            case 'onlineList':
                this._renderOnlineList(msg);
                break;
            case 'userState':
                this._showUserState(msg);
                break;
            default:
                break;
        }
    }

    destroy() {
        for (const el of this._layers) el.remove();
        this._layers = [];
        this._tradeWin = null;
        this._partyWin = null;
        this._inviteWin = null;
        this._partyInviteWin = null;
        this._goldDlg = null;
        this._onlineWin = null;
        this._onlineListEl = null;
        this._inspectWin = null;
        this.trade = null;
        this.party = null;
    }
}
