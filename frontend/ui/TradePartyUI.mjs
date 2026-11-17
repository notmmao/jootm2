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
 * 用法：new TradePartyUI({ container, conn, libUrl, onLog })，PlayScene.onMessage
 * 把上述 type 转给 handleMessage(type,msg)，onExit 调 destroy()。
 */
export class TradePartyUI {
    constructor({ container, conn, libUrl, onLog }) {
        this.container = container;
        this.conn = conn;
        this.libUrl = libUrl;
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
        const grid = document.createElement('div');
        grid.style.cssText = 'position:absolute;left:8px;top:26px;width:146px;height:132px;'
            + 'display:grid;grid-template-columns:repeat(6,23px);grid-auto-rows:22px;'
            + 'gap:1px;align-content:start;';
        for (let i = 0; i < 30; i++) {
            const cell = document.createElement('div');
            cell.style.cssText = 'width:23px;height:22px;box-sizing:border-box;'
                + 'border:1px solid #4a3f28;background:rgba(0,0,0,.3);'
                + 'display:grid;place-items:center;overflow:hidden;cursor:'
                + (side === 'self' && items[i] ? 'pointer' : 'default') + ';';
            const it = items[i];
            if (it) {
                cell.title = it.name;
                this.libUrl('items', it.looks).then((url) => {
                    if (url && cell.isConnected) {
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'max-width:21px;max-height:20px;';
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
        const x = side === 'self' ? 6 : 160;
        const box = document.createElement('div');
        box.style.cssText = `position:absolute;left:${x}px;top:26px;width:150px;height:170px;`;
        const name = document.createElement('div');
        name.style.cssText = 'position:absolute;left:0;top:0;width:100%;text-align:center;'
            + 'font-size:12px;color:#ffd76a;';
        name.textContent = data.name || '';
        box.appendChild(name);
        const sub = document.createElement('div');
        sub.style.cssText = 'position:absolute;left:0;top:158px;width:100%;text-align:center;'
            + 'font-size:12px;color:#fff;';
        sub.textContent = (data.gold || 0).toLocaleString() + ' 金币';
        box.appendChild(sub);
        this._itemGrid(box, data.items || [], side);
        win.appendChild(box);
        return sub;
    }

    _openTradeWindow() {
        if (this._tradeWin) return this._tradeWin;
        const win = this._window('交易', 120, 120, 314, 250, 385,
            () => this._send('tradeCancel'));
        this._tradeWin = win;

        const info = document.createElement('div');
        info.style.cssText = 'position:absolute;left:6px;top:200px;width:302px;'
            + 'font-size:11px;color:#c9b98f;text-align:center;';
        info.textContent = '点自己格子取回物品；点「放金币」设置交易金额；双方锁定后成交';
        win.appendChild(info);

        const goldBtn = document.createElement('div');
        goldBtn.style.cssText = 'position:absolute;left:24px;top:220px;width:120px;height:22px;'
            + 'cursor:pointer;text-align:center;line-height:22px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
        goldBtn.textContent = '放金币';
        goldBtn.onclick = () => this._showGoldDialog();
        win.appendChild(goldBtn);

        const lockBtn = document.createElement('div');
        lockBtn.style.cssText = 'position:absolute;left:168px;top:220px;width:120px;height:22px;'
            + 'cursor:pointer;text-align:center;line-height:22px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
        lockBtn.textContent = '锁定 / 确认';
        lockBtn.onclick = () => this._send('tradeConfirm');
        this._tradeLockBtn = lockBtn;
        win.appendChild(lockBtn);
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
            win.insertBefore(holder, win.firstChild);
            parts.push(holder);
        }
        if (this._tradeLockBtn) {
            this._tradeLockBtn.textContent = state.self.confirm ? '已锁定' : '锁定 / 确认';
            this._tradeLockBtn.style.color = state.self.confirm ? '#9fe08a' : '#ffe9a8';
        }
    }

    _showGoldDialog() {
        if (this._goldDlg) return;
        const dlg = this._window('放入金币', 250, 250, 220, 110, 385,
            () => { this._goldDlg.remove(); this._goldDlg = null; });
        this._goldDlg = dlg;
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '金币总额';
        input.style.cssText = 'position:absolute;left:16px;top:36px;width:120px;height:20px;'
            + 'font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        const submit = () => {
            const n = parseInt(input.value.replace(/[^0-9]/g, ''), 10);
            if (Number.isFinite(n) && n >= 0) this._send('tradeSetGold', { amount: n });
            dlg.remove(); this._goldDlg = null;
        };
        this._bindInput(input, submit);
        dlg.appendChild(input);
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
        const win = this._window('组队', 250, 120, 300, 300, 385,
            () => { win.remove(); this._partyWin = null; });
        this._partyWin = win;
        const list = document.createElement('div');
        list.style.cssText = 'position:absolute;left:10px;top:30px;width:280px;height:150px;'
            + 'overflow:auto;background:rgba(0,0,0,.25);border:1px solid #4a3f28;';
        win.appendChild(list);
        this._partyList = list;

        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '输入玩家名邀请入队';
        input.style.cssText = 'position:absolute;left:10px;top:190px;width:180px;height:22px;'
            + 'font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        const submit = () => { this.inviteParty(input.value.trim()); input.value = ''; };
        this._bindInput(input, submit);
        win.appendChild(input);
        const inv = document.createElement('div');
        inv.style.cssText = 'position:absolute;left:198px;top:190px;width:92px;height:22px;'
            + 'cursor:pointer;text-align:center;line-height:22px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ffe9a8;';
        inv.textContent = '邀请';
        inv.onclick = submit;
        win.appendChild(inv);

        const leave = document.createElement('div');
        leave.style.cssText = 'position:absolute;left:10px;top:262px;width:92px;height:22px;'
            + 'cursor:pointer;text-align:center;line-height:22px;font-size:12px;'
            + 'background:#4a3d24;border:1px solid #8a7343;color:#ff9a8a;';
        leave.textContent = '退出队伍';
        leave.onclick = () => this._send('partyLeave');
        win.appendChild(leave);

        const chat = document.createElement('input');
        chat.type = 'text';
        chat.placeholder = '队伍聊天 (回车发送)';
        chat.style.cssText = 'position:absolute;left:110px;top:262px;width:180px;height:22px;'
            + 'font-size:12px;color:#000;background:#d8d8d8;border:1px solid #888;';
        this._bindInput(chat, () => {
            if (chat.value.trim()) this._send('partyChat', { text: chat.value.trim() });
            chat.value = ''; chat.blur();
        });
        win.appendChild(chat);
        return win;
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
            row.textContent = `${pl.name}  Lv.${pl.level}  ${JOBS[pl.job] || '?'}  @ ${pl.map || '?'}`;
            row.title = `${pl.name} — ${pl.map || '未知地图'}`;
            list.appendChild(row);
        }
    }

    // ------------------------------------------------------------ 查看他人装备

    // 13 槽装备坐标（原版 U_DRESS..U_CHARM，复用 FState.pas DSWxxx 布局；
    // 与 PlayScene 的 EQUIP_SLOTS 同口径，这里独立定义避免跨模块耦合）。
    _inspectSlots() {
        return [
            [0, 96, 58, '衣服'], [1, 47, 16, '武器'], [2, 168, 61, '右手'],
            [3, 168, 23, '项链'], [4, 115, 29, '头盔'], [5, 168, 112, '手镯L'],
            [6, 42, 112, '手镯R'], [7, 168, 151, '戒指L'], [8, 42, 151, '戒指R'],
            [9, 42, 190, '护身符'], [10, 84, 190, '腰带'], [11, 126, 190, '靴子'],
            [12, 168, 190, '宝石'],
        ];
    }

    /** Ctrl+右键玩家后收到 userState 快照，弹独立装备窗（原版 DUserState1）。 */
    _showUserState(msg) {
        if (this._inspectWin) this._inspectWin.remove();
        const win = this._window(`${msg.name || '玩家'} 的装备`, 470, 110, 232, 300, 207,
            () => { win.remove(); this._inspectWin = null; });
        this._inspectWin = win;
        const head = document.createElement('div');
        head.style.cssText = 'position:absolute;left:12px;top:26px;width:208px;'
            + 'text-align:center;font-size:12px;color:#ffd76a;';
        const JOBS = ['战士', '法师', '道士'];
        head.textContent = `${msg.name || '?'}  Lv.${msg.level || '?'}  ${JOBS[msg.job] || ''}`;
        win.appendChild(head);
        const bySlot = {};
        for (const it of (msg.items || [])) {
            if (it.slot != null) bySlot[it.slot] = it;
        }
        for (const [slot, x, y, label] of this._inspectSlots()) {
            const cell = document.createElement('div');
            cell.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:34px;height:31px;`
                + 'box-sizing:border-box;border:1px solid #6b5636;background:rgba(0,0,0,.35);'
                + 'display:grid;place-items:center;overflow:hidden;';
            const it = bySlot[slot];
            if (it) {
                cell.title = `${label}：${it.name}`;
                this.libUrl('stateitem', it.looks).then((url) => {
                    if (url && cell.isConnected) {
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'max-width:32px;max-height:29px;';
                        cell.appendChild(im);
                    }
                });
            } else {
                cell.title = label;
            }
            win.appendChild(cell);
        }
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
