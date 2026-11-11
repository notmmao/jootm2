// 选角 / 建角界面 —— 1:1 移植 Delphi TSelectChrScene（IntroScn.pas）
// + FrmDlg 的 DSelectChr（按钮 Prguse[66..72]）/ DCreateChr（Prguse[73]，职业/性别/发型 74..80）。
// 场景背景 = Prguse[65]（800×600，照 PlayScene 的 WProgUse.Images[65]）。
// 角色立绘 = ChrSel 站立帧（40 + job*40 + sex*120，10 帧循环，每 300ms）；未选中显示冻结帧（60 + job*40 + sex*120）。
// 名字/等级/职业用描边字（BoldTextOut 白字黑边）。

import { Scene } from '../scene/Scene.mjs';
import { PlayScene } from '../scene/PlayScene.mjs';
import { NoticeScene } from './NoticeScene.mjs';
import { getLib } from '../image/Images.mjs';
import { SCREEN_WIDTH, SCREEN_HEIGHT } from '../core/config.mjs';
import { preloadMapTextures, preloadState, waitIdle } from '../core/mapPreload.mjs';
import { getBootInfo } from '../core/boot.mjs';

const JOB_NAMES = ['武士', '魔法师', '道士'];   // GetJobName

// 立绘位置（照 PlayScene：bx/by=冻结帧锚点，fx/fy=站立帧锚点；n=1 时 +340/+2）
const CHAR_POS = [
    [{ bx: 71, by: 52, fx: 71, fy: 52 }, { bx: 65, by: 55, fx: 65, fy: 55 }],
    [{ bx: 77, by: 46, fx: 77, fy: 46 }, { bx: 171, by: 97, fx: 141, fy: 83 }],
    [{ bx: 85, by: 63, fx: 85, fy: 63 }, { bx: 164, by: 103, fx: 141, fy: 83 }],
];
const STAND_FRAME_COUNT = 10;   // 站立动画帧数（SELECTEDFRAME）
const FRAME_MS = 300;           // 站立帧推进间隔

// 建角字段（照 MakeNewChar / FState.pas DCreateChr 按钮布局）
const CREATE_JOB_BTNS = [74, 75, 76];   // 武士/法师/道士

export class SelectCharScene extends Scene {
    constructor(manager) {
        super(manager);
        this.prg = getLib('prguse');
        this.chr = getLib('chrsel');
        this._prgCache = new Map();  // Prguse 图 → img URL
        this._chrCache = new Map();  // ChrSel 图 → {canvas, failed?, failedAt?}
        this._bgTex = null;
        this._bgReady = false;       // 背景首次加载结束（失败也放行，避免永久黑屏）
        this._bgTriedAt = 0;
        this._wanted = new Set();    // 当前角色需要的全部立绘帧编号
        this._pendingPortraits = new Set();
        this._portraitsReady = false;
        this._lastTex = [null, null]; // 每个槽最近成功的一帧（帧回退，绝不整人消失）
        this.chars = [null, null];   // 两个角色槽（照 ChrArr[0..1]）
        this.selected = 0;
        this.createMode = false;
        this.newIndex = 0;
        this._frame = 0;
        this._frameAt = 0;
        this.root = null;
        this.createWin = null;
    }

    onEnter() {
        const gui = this.manager.winManager.container;
        gui.innerHTML = '';
        this.root = document.createElement('div');
        this.root.style.cssText = 'position:absolute;inset:0;pointer-events:auto;';
        gui.appendChild(this.root);
        this._buildButtons();
        // 背景与立绘统一预取：预取完成前不推进动画帧、不清屏重画半成品，
        // 避免 10 帧站立动画逐帧懒加载时人物一帧帧消失（选角界面闪屏主因）。
        this._preload();
        this.conn.send('listCharacters');
    }

    onExit() {
        if (this.root) this.root.remove();
        document.removeEventListener('keydown', this._keyHandler);
    }

    // ---- 纹理加载 ---------------------------------------------------------

    // Prguse 图 → img URL（缓存 + 并发去重，避免同一张图重复 PNG 编码卡主线程）
    async _prgUrl(idx) {
        if (this._prgCache.has(idx)) return this._prgCache.get(idx);
        if (!this._prgPending) this._prgPending = new Map();
        if (this._prgPending.has(idx)) return this._prgPending.get(idx);
        const p = (async () => {
            const tex = await this.prg.getTexture(idx);
            if (!tex) return null;
            const url = tex.canvas.toDataURL
                ? tex.canvas.toDataURL('image/png')
                : URL.createObjectURL(await tex.canvas.convertToBlob());
            this._prgCache.set(idx, url);
            return url;
        })().finally(() => { this._prgPending.delete(idx); });
        this._prgPending.set(idx, p);
        return p;
    }

    /** 首屏预取：背景 + 一组默认立绘；characterList 到达后按实际角色补齐。 */
    async _preload() {
        try {
            const t = await this.prg.getTexture(65);
            this._bgTex = t ? t.canvas : null;
        } catch (e) {
            console.warn('[select] 背景纹理加载失败', e);
        } finally {
            this._bgReady = true;
            this._bgTriedAt = Date.now();
        }
        this._ensurePortraits();
    }

    /**
     * 按当前角色槽算出需要的立绘帧（站立 10 帧 + 冻结帧），一次性并发预取。
     * 建角模式额外预取「当前所选职业/性别」的立绘：原版 MakeNewChar 把
     * ChrArr[NewIndex].Valid 置 TRUE，PlayScene 按 UserChr.Job/Sex 实时画人物，
     * 所以点职业/性别按钮要立刻换形象，不能只在提交时生效。
     */
    _ensurePortraits() {
        const ids = new Set();
        const addChar = (rawJob, sex) => {
            const job = Math.max(0, Math.min(2, rawJob | 0));
            const s = sex ? 1 : 0;
            const base = 40 + job * 40 + s * 120;
            for (let f = 0; f < STAND_FRAME_COUNT; f++) ids.add(base + f);
            ids.add(60 + job * 40 + s * 120);
        };
        for (const c of this.chars) {
            if (c) addChar(c.job, c.sex);
        }
        if (this.createMode && this.createState) {
            addChar(this.createState.job, this.createState.sex);
        }
        // 还没有角色时先预取武士男/女两组，避免建角后首圈等待
        if (!ids.size) {
            for (const sex of [0, 1]) addChar(0, sex);
        }
        this._wanted = ids;
        for (const id of ids) this._loadPortrait(id);
        this._checkPortraits();
    }

    _loadPortrait(idx) {
        if (this._pendingPortraits.has(idx)) return;
        const e = this._chrCache.get(idx);
        if (e && (e.canvas || (e.failed && Date.now() - (e.failedAt || 0) < 5000))) return;
        this._pendingPortraits.add(idx);
        this._chrCache.set(idx, { canvas: null });
        this.chr.getTexture(idx).then((t) => {
            const cur = this._chrCache.get(idx);
            if (cur) {
                cur.canvas = t ? t.canvas : null;
                if (!t) { cur.failed = true; cur.failedAt = Date.now(); }
            }
            this._pendingPortraits.delete(idx);
            this._checkPortraits();
        }).catch(() => {
            const cur = this._chrCache.get(idx);
            if (cur) { cur.failed = true; cur.failedAt = Date.now(); }
            this._pendingPortraits.delete(idx);
            this._checkPortraits();
        });
    }

    _checkPortraits() {
        const ids = this._wanted;
        if (!ids || !ids.size) { this._portraitsReady = false; return; }
        let ready = true;
        for (const id of ids) {
            const e = this._chrCache.get(id);
            // 空图 / 失败也视为就绪（由帧回退兜底），避免永久停在加载态
            if (!e || (!e.canvas && !e.failed)) { ready = false; break; }
        }
        this._portraitsReady = ready;
    }

    /**
     * 取立绘纹理。当前帧未就绪时回退该槽最近成功的一帧，
     * 绝不返回空让人物整帧消失（选角界面「闪」的直接原因）。
     */
    _chrTex(idx, slot) {
        const e = this._chrCache.get(idx);
        if (!e) {
            this._loadPortrait(idx);
            return this._lastTex[slot] || null;
        }
        if (e.canvas) {
            this._lastTex[slot] = e.canvas;
            return e.canvas;
        }
        if (e.failed && Date.now() - (e.failedAt || 0) > 5000) {
            this._chrCache.delete(idx);
            this._loadPortrait(idx);
        } else if (!this._pendingPortraits.has(idx)) {
            this._loadPortrait(idx);
        }
        return this._lastTex[slot] || null;
    }

    // ---- DOM 构建 ---------------------------------------------------------

    // Prguse 图按钮（绝对坐标，照 FState.pas）
    // WIL 图按钮：正常态画在背景图里；透明热区接收点击，img（按下态）按下显示、抬起恢复
    async _btn(idx, left, top, onClick) {
        const url = await this._prgUrl(idx);
        if (!url) return null;
        const hot = document.createElement('div');
        hot.style.cssText = `position:absolute;left:${left}px;top:${top}px;cursor:pointer;`;
        const el = document.createElement('img');
        el.src = url;
        el.draggable = false;
        el.style.cssText = 'visibility:hidden;display:block;';
        el.addEventListener('load', () => {
            hot.style.width = el.naturalWidth + 'px';
            hot.style.height = el.naturalHeight + 'px';
        });
        hot.appendChild(el);
        hot.addEventListener('mousedown', () => { el.style.visibility = 'visible'; });
        const restore = () => { el.style.visibility = 'hidden'; };
        hot.addEventListener('mouseup', restore);
        hot.addEventListener('mouseleave', restore);
        hot.addEventListener('click', onClick);
        this.root.appendChild(hot);
        return hot;
    }

    _buildButtons() {
        // 选角按钮（FState.pas DSelectChr），并行加载
        return Promise.all([
            this._btn(66, 133, 453, () => this._select(0)),    // 选角色1
            this._btn(67, 685, 454, () => this._select(1)),    // 选角色2
            this._btn(68, 385, 456, () => this._start()),      // 开始
            this._btn(69, 348, 486, () => this._newChr()),     // 创建角色
            this._btn(70, 347, 506, () => this._erase()),      // 删除角色
            this._btn(71, 362, 527, () => {}),                 // 制作人员（原版空实现）
            this._btn(72, 379, 547, () => this.conn.close()),  // 退出
        ]);
    }

    // ---- 交互 ---------------------------------------------------------------

    _select(n) {
        if (this.chars[n] && this.selected !== n) {
            this.selected = n;
            this._preloadSelected();
        }
    }

    /** 按角色上次下线的地图坐标预载首屏纹理（服务端 characterList 附带 map/x/y）。 */
    _preloadSelected() {
        const start = getBootInfo().start;
        if (!start) return;                 // 老服务端连出生点都没给，无能为力
        const c = this.chars[this.selected];
        if (c && c.map != null) {
            const mini = String(start.map) === String(c.map) ? start.miniMapIndex : 0;
            preloadMapTextures(c.map, c.x, c.y, { miniMapIndex: mini });
        } else {
            // 旧版服务端 characterList 不带坐标：仍按默认出生点确保预载已启动，
            // 且选角阶段就启动比等后台队列排到它更早。
            preloadMapTextures(start.map, start.x, start.y,
                { miniMapIndex: start.miniMapIndex });
        }
    }

    _start() {
        const c = this.chars[this.selected];
        if (!c) {
            this._msg('一开始你应该创建一个新角色。\n选择「创建角色」你就能建立一个新角色了。');
            return;
        }
        if (this._entering) return;         // 防重复点击
        this._entering = true;
        this._pendingName = c.name;         // 记住角色名，notice 确认后用它进游戏

        const finish = () => {
            clearInterval(tick);
            this._showEntering('');
            this.conn.send('enterGame', { name: c.name });
        };
        let tick = null;
        if (!preloadState.active) { finish(); return; }
        const p0 = preloadState.progress;
        // 预载才刚起步时（慢网首开）：等也等不完，白等 10 秒不如直接进游戏，
        // 已下好的那部分纹理仍然有效，剩下交给 MapActor 的 6 秒兜底。
        if (p0.total > 0 && p0.done < p0.total * 0.5) { finish(); return; }

        // 接近完成：在选角界面补最多 5 秒，避免进游戏后触发 6 秒超时出残缺地图。
        this._showEntering('正在载入地图资源…');
        tick = setInterval(() => {
            const p = preloadState.progress;
            if (p.total) this._showEntering(`正在载入地图资源 ${p.done}/${p.total}…`);
        }, 400);
        Promise.race([
            waitIdle(),
            new Promise(r => setTimeout(r, 5000)),
        ]).then(finish);
    }

    /** 选角界面底部的一行提示（预载等待时显示，不影响原版按钮布局）。 */
    _showEntering(text) {
        if (!this.root) return;
        if (!this._enteringEl) {
            const el = document.createElement('div');
            el.style.cssText = 'position:absolute;left:0;right:0;bottom:6px;text-align:center;'
                + 'color:#e8d9b0;font:13px SimSun,serif;text-shadow:1px 1px 0 #000;'
                + 'pointer-events:none;z-index:5;';
            this.root.appendChild(el);
            this._enteringEl = el;
        }
        this._enteringEl.textContent = text || '';
    }

    _newChr() {
        if (!this.chars[0]) this._openCreate(0);
        else if (!this.chars[1]) this._openCreate(1);
        else this._msg('你可以为每个单独的帐户创建两个角色。');
    }

    _erase() {
        const c = this.chars[this.selected];
        if (c && window.confirm(`"${c.name}" 删除的角色是不能被恢复的。\n你真的想要删除角色吗？`)) {
            this.conn.send('deleteCharacter', { name: c.name });
        }
    }

    _msg(text) {
        // 原版 DMessageDlg 消息框（简化：浏览器 alert）
        window.alert(text);
    }

    // ---- 建角窗口（DCreateChr） --------------------------------------------

    async _openCreate(index) {
        this.createMode = true;
        this.newIndex = index;
        this.createState = { job: 0, sex: 0, hair: 0 };   // 建角状态（同步初始化，避免 await 后才可用）
        this._createBtns = [];                 // 建角按钮（含选中态图），供 _refreshCreateSelection 使用
        const left = index === 0 ? 415 : 75;   // 照 MakeNewChar：槽0在右、槽1在左
        const top = 15;

        // 窗口容器 + 名字输入框先同步创建（照 EdChrName：窗口 Left+71, Top+107，宽137），
        // 背景图/按钮异步插入，保证 _nameInput 立即可用。
        const win = document.createElement('div');
        win.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:300px;height:417px;`;
        this.root.appendChild(win);
        this.createWin = win;

        const name = document.createElement('input');
        name.type = 'text';
        name.maxLength = 14;
        name.style.cssText = 'position:absolute;left:71px;top:107px;width:137px;height:16px;'
            + 'border:none;background:#000;color:#fff;font-size:12px;font-family:SimSun,serif;'
            + 'padding:0 3px;box-sizing:content-box;outline:none;';
        win.appendChild(name);
        this._nameInput = name;

        // 操作提示 + 当前选择文字。原版只靠实时人物立绘反馈（DCreateChr 上无文字），
        // 这里补一行明确状态，避免「点了职业按钮没反应、不知道选没选上」。
        const hint = document.createElement('div');
        // 放在性别按钮(231)与发型按钮(308)之间的空白带，避开面板自带的「职业」标题(≈131)
        // 和「姓名」输入框(107)；原来放 131 会和面板文字叠成一团。
        hint.style.cssText = 'position:absolute;left:20px;top:275px;width:270px;font-size:11px;'
            + 'text-align:center;color:#cfe0a8;text-shadow:1px 1px 0 #000;'
            + 'pointer-events:none;line-height:14px;';
        hint.textContent = '① 选职业 ② 选性别 ③ 输名字 ④ 确定';
        win.appendChild(hint);

        const info = document.createElement('div');
        info.style.cssText = 'position:absolute;left:20px;top:390px;width:270px;font-size:12px;'
            + 'color:#ffe9a8;text-shadow:1px 1px 0 #000;pointer-events:none;line-height:16px;';
        win.appendChild(info);
        this._createInfo = info;
        this._refreshCreateSelection();

        // 窗口背景 Prguse[73]（300×417，异步插入）
        this._prgUrl(73).then((bgUrl) => {
            if (!bgUrl || !this.createWin) return;
            const bg = document.createElement('img');
            bg.src = bgUrl;
            bg.draggable = false;
            // 不能用 z-index:-1：win 自身没有建立层叠上下文，负层级会掉到游戏
            // canvas 后面，整张 DCreateChr 面板（连同按钮底图）就彻底看不见，
            // 只剩几个「按下态」小图标浮在人物身上 —— 这正是「不知道怎么选职业」的观感来源。
            // 作为第一个子元素、z-index:0，后面的输入框/按钮按 DOM 顺序自然盖在它上面。
            bg.style.cssText = 'position:absolute;left:0;top:0;z-index:0;pointer-events:none;';
            win.insertBefore(bg, win.firstChild);
        });

        // 职业/性别/发型/确定/关闭按钮（并行加载，坐标照 FState.pas；透明热区 + 按下态 img）
        const mk = (idx, l, t, onClick, group = null, value = 0, tip = '') =>
            this._prgUrl(idx).then((url) => {
            if (!url) return;
            const hot = document.createElement('div');
            hot.style.cssText = `position:absolute;left:${l}px;top:${t}px;cursor:pointer;`;
            hot.title = tip;                       // 悬停提示：武士/法师/道士、男/女…
            const el = document.createElement('img');
            el.src = url;
            el.draggable = false;
            el.style.cssText = 'visibility:hidden;display:block;';
            el.addEventListener('load', () => {
                hot.style.width = el.naturalWidth + 'px';
                hot.style.height = el.naturalHeight + 'px';
            });
            hot.appendChild(el);
            hot.addEventListener('mousedown', () => { el.style.visibility = 'visible'; });
            // 抬起/移出后恢复成「当前选中项高亮」，而不是全部隐藏：
            // 原版选中职业/性别后按钮保持按下态，否则用户看不出选了哪个。
            const restore = () => this._refreshCreateSelection();
            hot.addEventListener('mouseup', restore);
            hot.addEventListener('mouseleave', restore);
            hot.addEventListener('click', onClick);
            win.appendChild(hot);
            this._createBtns.push({ hot, el, group, value });
        });
        // 职业（DccWarrior(48,157) DccWizzard(93,157) DccMonk(138,157)）
        // 性别（DccMale(93,231) DccFemale(138,231)）
        // 发型（DccLeftHair(76,308) DccRightHair(170,308)）
        // 确定（DccOk 图62 (102,359)）/ 关闭（DccClose 图64 (248,31)）
        return Promise.all([
            mk(74, 48, 157, () => this._setJob(0), 'job', 0, '武士（战士）'),
            mk(75, 93, 157, () => this._setJob(1), 'job', 1, '魔法师（法师）'),
            mk(76, 138, 157, () => this._setJob(2), 'job', 2, '道士'),
            mk(77, 93, 231, () => this._setSex(0), 'sex', 0, '男'),
            mk(78, 138, 231, () => this._setSex(1), 'sex', 1, '女'),
            mk(79, 76, 308, () => this._setHair(-1), null, 0, '上一个发型'),
            mk(80, 170, 308, () => this._setHair(+1), null, 0, '下一个发型'),
            mk(62, 102, 359, () => this._createOk(), null, 0, '确定创建角色'),
            mk(64, 248, 31, () => this._createClose(), null, 0, '关闭'),
        ]).then(() => {
            this._refreshCreateSelection();
            name.focus();
            this._keyHandler = (e) => { if (e.key === 'Enter') this._createOk(); };
            document.addEventListener('keydown', this._keyHandler);
        });
    }

    /** 把当前选择的职业/性别以「按下态」高亮，并刷新状态文字（原版 TDButton.Down 语义）。 */
    _refreshCreateSelection() {
        const st = this.createState || { job: 0, sex: 0, hair: 0 };
        for (const b of this._createBtns || []) {
            const on = (b.group === 'job' && st.job === b.value)
                    || (b.group === 'sex' && st.sex === b.value);
            b.el.style.visibility = on ? 'visible' : 'hidden';
        }
        if (this._createInfo) {
            this._createInfo.textContent = `职业：${JOB_NAMES[st.job]}　`
                + `性别：${st.sex ? '女' : '男'}　发型：${st.hair + 1}`;
        }
    }

    _setJob(job) {
        this.createState.job = Math.max(0, Math.min(2, job | 0));
        this._refreshCreateSelection();
        this._ensurePortraits();     // 原版 SelChrNewJob → SelectChr → PlayScene 立刻换人物
    }
    _setSex(sex) {
        this.createState.sex = sex ? 1 : 0;
        this._refreshCreateSelection();
        this._ensurePortraits();     // 原版 SelChrNewm_btSex 同理
    }
    _setHair(d) {
        this.createState.hair = (this.createState.hair + d + 10) % 10;
        this._refreshCreateSelection();
    }

    _createOk() {
        const name = this._nameInput.value.trim();
        if (!name) { this._nameInput.focus(); return; }
        this.conn.send('createCharacter', { name, ...this.createState });
        this._createClose();
    }

    _createClose() {
        this.createMode = false;
        if (this.createWin) { this.createWin.remove(); this.createWin = null; }
        this._createBtns = [];
        this._createInfo = null;
        this._nameInput = null;
        document.removeEventListener('keydown', this._keyHandler);
        this._ensurePortraits();     // 恢复成真实角色列表需要的帧，别把预览帧留在 _wanted
    }

    // ---- 消息 ---------------------------------------------------------------

    onMessage(type, msg) {
        if (type === 'characterList') {
            const list = msg.characters || [];
            this.chars = [list[0] || null, list[1] || null];
            if (!this.chars[this.selected]) {
                this.selected = this.chars[0] ? 0 : (this.chars[1] ? 1 : 0);
            }
            // 角色职业/性别可能变化，补齐对应立绘帧（含建角 / 删角后的刷新）
            this._ensurePortraits();
            // 一拿到角色位置就开始预载首屏地图纹理；比启动时的默认出生点更精确，
            // mapPreload 会让默认任务让位，避免把带宽花在玩家不会去的地图。
            this._preloadSelected();
        } else if (type === 'createCharacterResult') {
            if (!msg.ok) this._msg(msg.error || '创建失败。');
            this.conn.send('listCharacters');
        } else if (type === 'deleteCharacterResult') {
            this.conn.send('listCharacters');
        } else if (type === 'enterGameResult') {
            if (!msg.ok) this._msg(msg.error || '进入游戏失败。');
        } else if (type === 'notice') {
            this.manager.switchTo(new NoticeScene(this.manager, this._pendingName, msg.lines || []));
        } else if (type === 'enterGame') {
            this.manager.switchTo(new PlayScene(this.manager, msg));
        }
    }

    // ---- 渲染 ---------------------------------------------------------------

    _text(ctx, text, x, y) {
        // 原版 IntroScn.pas 用 BoldTextOut → Windows TextOut(x, y)，y 是「字顶」。
        // canvas 默认 alphabetic 基线会让文字整体上浮约 10px，压在背景虚线框上边框上；
        // 必须显式改成 top，再把坐标对齐 Delphi 的 493/522/552 与 495/524/554。
        ctx.save();
        ctx.font = 'bold 13px SimSun,serif';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.lineWidth = 3;
        ctx.strokeStyle = '#000';
        ctx.strokeText(text, x, y);
        ctx.fillStyle = '#fff';
        ctx.fillText(text, x, y);
        ctx.restore();
    }

    render(ctx) {
        // 背景首次加载期间不清屏、保留上一帧（back buffer 里是登录界面完整一帧），
        // 避免每帧「清黑 → 背景/立绘一块块冒出来」造成的整屏闪。
        if (!this._bgReady) {
            ctx.save();
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, 250, 26);
            ctx.fillStyle = '#ffd76a';
            ctx.font = '14px SimSun,serif';
            ctx.textAlign = 'left';
            ctx.fillText('正在加载角色选择界面…', 10, 18);
            ctx.restore();
            return;
        }

        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT);
        if (this._bgTex) {
            ctx.drawImage(this._bgTex, 0, 0);
        } else if (Date.now() - this._bgTriedAt > 3000) {
            // 背景加载失败时低频重试，不每帧发请求
            this._bgTriedAt = Date.now();
            this.prg.getTexture(65).then((t) => {
                if (t) this._bgTex = t.canvas;
            }).catch(() => {});
        }

        // 立绘全部就绪后才推进动画帧；否则保持当前帧 + 帧回退，
        // 不让 10 帧站立动画在首圈逐帧懒加载时一帧帧消失。
        const now = performance.now();
        if (this._portraitsReady && now - this._frameAt >= FRAME_MS) {
            this._frameAt = now;
            this._frame = (this._frame + 1) % STAND_FRAME_COUNT;
        }

        // 两个角色立绘
        for (let n = 0; n < 2; n++) {
            const c = this.chars[n];
            // 建角中的槽位改由下方的实时预览绘制
            if (!c || (this.createMode && n === this.newIndex)) continue;
            const pos = CHAR_POS[c.job] && CHAR_POS[c.job][c.sex];
            if (!pos) continue;
            const off = n === 1 ? { bx: 340, by: 2, fx: 340, fy: 2 } : { bx: 0, by: 0, fx: 0, fy: 0 };

            let img, x, y;
            if (n === this.selected) {
                // 站立动画帧（40 + job*40 + frame + sex*120）
                img = 40 + c.job * 40 + this._frame + c.sex * 120;
                x = pos.fx + off.fx;
                y = pos.fy + off.fy;
            } else {
                // 冻结帧（60 + job*40 + sex*120，静止首帧）
                img = 60 + c.job * 40 + c.sex * 120;
                x = pos.bx + off.bx;
                y = pos.by + off.by;
            }
            const tex = this._chrTex(img, n);
            if (tex) ctx.drawImage(tex, x, y);

            // 名字/等级/职业：Delphi IntroScn.pas::TSelectChrScene.PlayScene 的 BoldTextOut 原坐标
            // 左槽 117,493 / 117,522 / 117,552；右槽 671,495 / 671,524 / 671,554。
            if (n === 0) {
                this._text(ctx, c.name, 117, 493);
                this._text(ctx, String(c.level), 117, 522);
                this._text(ctx, JOB_NAMES[c.job] || '', 117, 552);
            } else {
                this._text(ctx, c.name, 671, 495);
                this._text(ctx, String(c.level), 671, 524);
                this._text(ctx, JOB_NAMES[c.job] || '', 671, 554);
            }
        }

        // 建角实时预览：原版 MakeNewChar 把 ChrArr[NewIndex].Valid := TRUE，
        // PlayScene 按 UserChr.Job/Sex 绘制该槽人物，点职业/性别按钮立刻换形象。
        // 旧版这里该槽是空的 → 点职业按钮完全没反馈，用户以为「不能选职业」。
        if (this.createMode && this.createState) {
            const n = this.newIndex;
            const job = Math.max(0, Math.min(2, this.createState.job | 0));
            const sex = this.createState.sex ? 1 : 0;
            const pos = CHAR_POS[job] && CHAR_POS[job][sex];
            if (pos) {
                const dx = n === 1 ? 340 : 0;
                const dy = n === 1 ? 2 : 0;
                const img = 40 + job * 40 + this._frame + sex * 120;
                const tex = this._chrTex(img, n);
                if (tex) ctx.drawImage(tex, pos.fx + dx, pos.fy + dy);
                const lx = n === 0 ? 117 : 671;
                this._text(ctx, this._nameInput ? this._nameInput.value.trim() : '', lx, n === 0 ? 493 : 495);
                this._text(ctx, JOB_NAMES[job], lx, n === 0 ? 552 : 554);
            }
        }
    }
}
