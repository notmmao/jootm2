// 登录 / 注册 / 改密码界面 —— 1:1 移植 Delphi TLoginScene（IntroScn.pas）
// + FrmDlg 的 DLogIn(Prguse[60]) / DNewAccount(Prguse[63]) / DChgPw(Prguse[50]) 窗口。
// 场景背景 = ChrSel[22]（800×600，照 TLoginScene.PlayScene 的 WChrSel.Images[102-80]）。
// 输入框照原版 TEdit：无边框（bsNone）黑底（clBlack）白字，叠加在背景图黑色输入区上。
// 按钮照原版 TDButton + SetImgIndex（Prguse 图），坐标照 FState.pas。

import { Scene } from '../scene/Scene.mjs';
import { SelectCharScene } from './SelectCharScene.mjs';
import { getLib } from '../image/Images.mjs';
import { sound } from '../audio/SoundManager.mjs';
import { SCREEN_WIDTH, SCREEN_HEIGHT } from '../core/config.mjs';

// 原版 IntroScn.pas::TLoginScene 开门：m_nMaxFrame=10、300ms/帧，
// 底图 ChrSel[22]，门帧 ChrSel[23+frame]（WChrSel.Images[103+frame-80]）。
// 播完置 g_boDoFadeOut → g_nFadeIndex 淡出 → DScreen.ChangeScene(stSelectChr)。
const DOOR_FRAMES = 10;
const DOOR_FRAME_MS = 300;
const DOOR_FIRST_INDEX = 23;
const DOOR_FADE_MS = 300;
const DOOR_SOUND = 100;      // SoundUtil.pas: s_rock_door_open = 100

// 窗口定义（FState.pas：DLogIn=60 / DNewAccount=63 / DChgPw=50，尺寸取自图）
const WIN_LOGIN = { bg: 60, w: 296, h: 254 };
const WIN_REG   = { bg: 63, w: 640, h: 473 };
const WIN_CHGPW = { bg: 50, w: 420, h: 299 };
// 按钮图（FState.pas：Ok=62 New=61 ChgPw=53 Cancel=52 Close=64）
const BTN_SUBMIT = 62, BTN_NEWUSER = 61, BTN_CHGPW = 53, BTN_CANCEL = 52, BTN_CLOSE = 64;

// 免密登录（用户 2026-11-23）：token 由服务端登录成功签发、存 localStorage；
// 用户名也记住（只存用户名，不存密码）。token 失效自动回退到手输。
const LS_TOKEN = 'jootm2.authToken';
const LS_USER = 'jootm2.lastUsername';

// 注册表单字段（照 IntroScn.pas Create：left=161，宽 116（问答 163），top 相对窗口）
const REG_FIELDS = [
    { key: 'username', top: 116, w: 116, max: 10 },
    { key: 'password', top: 137, w: 116, max: 10, pwd: true },
    { key: 'confirm',  top: 158, w: 116, max: 10, pwd: true },
    { key: 'name',     top: 187, w: 116, max: 20 },
    { key: 'ssno',     top: 207, w: 116, max: 14 },
    { key: 'birthday', top: 227, w: 116, max: 10 },
    { key: 'quiz1',    top: 256, w: 163, max: 20 },
    { key: 'answer1',  top: 276, w: 163, max: 12 },
    { key: 'quiz2',    top: 297, w: 163, max: 20 },
    { key: 'answer2',  top: 317, w: 163, max: 12 },
    { key: 'phone',    top: 347, w: 116, max: 14 },
    { key: 'mobile',   top: 368, w: 116, max: 13 },
    { key: 'email',    top: 388, w: 116, max: 40 },
];
// 改密码字段（照 IntroScn.pas：left=241，宽 137）
const CHGPW_FIELDS = [
    { key: 'username', top: 117, w: 137, max: 10 },
    { key: 'current',  top: 149, w: 137, max: 10, pwd: true },
    { key: 'newpw',    top: 176, w: 137, max: 10, pwd: true },
    { key: 'repeat',   top: 208, w: 137, max: 10, pwd: true },
];

export class LoginScene extends Scene {
    constructor(manager) {
        super(manager);
        this.prg = getLib('prguse');
        this.chr = getLib('chrsel');
        this._urls = new Map();     // Prguse 图 URL 缓存
        this._bgTex = null;         // ChrSel[22] 背景
        this._bgLoading = false;
        this.root = null;
        this._edits = [];           // 当前窗口的输入框（回车跳转）
        // 登录成功后的开门动画状态（原版 m_boNowOpening / m_nCurFrame / m_dwStartTime）
        this._door = { active: false, done: false, startAt: 0, frames: [] };
        this._autoTrying = false;   // 正在用 token 自动登录（失败时静默回退）
    }

    // WIL 纹理 → img URL（缓存）
    async _url(idx) {
        if (this._urls.has(idx)) return this._urls.get(idx);
        const tex = await this.prg.getTexture(idx);
        if (!tex) return null;
        const url = tex.canvas.toDataURL
            ? tex.canvas.toDataURL('image/png')
            : URL.createObjectURL(await tex.canvas.convertToBlob());
        this._urls.set(idx, url);
        return url;
    }

    onEnter() {
        const gui = this.manager.winManager.container;
        gui.innerHTML = '';
        this.root = document.createElement('div');
        this.root.style.cssText = 'position:absolute;inset:0;pointer-events:auto;';
        gui.appendChild(this.root);
        this.showLogin();
    }

    onExit() {
        if (this.root) this.root.remove();
    }

    // ---- 构建辅助 ---------------------------------------------------------

    // 无边框输入框（照原版 TEdit：bsNone + 黑底 + 白字 + PasswordChar）
    _edit(parent, left, top, width, opts = {}) {
        const el = document.createElement('input');
        el.type = opts.pwd ? 'password' : 'text';
        el.maxLength = opts.max || 20;
        el.autocomplete = 'off';
        el.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:${width}px;height:16px;`
            + 'border:none;background:#000;color:#fff;font-size:12px;font-family:SimSun,serif;'
            + 'padding:0 3px;box-sizing:content-box;outline:none;';
        parent.appendChild(el);
        this._edits.push(el);
        return el;
    }

    // WIL 图按钮（照原版 TDButton + SetImgIndex，坐标相对窗口）
    // 原版机制：窗口背景图已画按钮「正常态」；透明热区接收点击，按下才显示 img（按下态）、抬起恢复
    async _btn(parent, idx, left, top, onClick) {
        const url = await this._url(idx);
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
        parent.appendChild(hot);
        return hot;
    }

    // 窗口容器：背景图 + 屏幕居中（照原版 (SCREENWIDTH-w)/2, (SCREENHEIGHT-h)/2）
    async _win(def) {
        this.root.innerHTML = '';
        this._edits = [];
        const left = Math.floor((SCREEN_WIDTH - def.w) / 2);
        const top = Math.floor((SCREEN_HEIGHT - def.h) / 2);
        const win = document.createElement('div');
        win.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:${def.w}px;height:${def.h}px;`;
        const bgUrl = await this._url(def.bg);
        if (bgUrl) {
            const bg = document.createElement('img');
            bg.src = bgUrl;
            bg.draggable = false;
            bg.style.cssText = 'position:absolute;left:0;top:0;';
            win.appendChild(bg);
        }
        this.root.appendChild(win);
        return win;
    }

    // 错误提示（照原版 PassWdFail / NewIdRetry 的提示位）
    _err(parent, left, top, width) {
        const el = document.createElement('div');
        el.style.cssText = `position:absolute;left:${left}px;top:${top}px;width:${width}px;`
            + 'font-size:12px;color:#ff6b5e;text-align:center;font-family:SimSun,serif;';
        parent.appendChild(el);
        return el;
    }

    // 回车逐个跳转，最后一个回车提交（照原版 EdNewIdKeyPress）
    _enterChain(fields) {
        for (let i = 0; i < fields.length; i++) {
            const el = fields[i].el;
            el.addEventListener('keydown', (e) => {
                if (e.key !== 'Enter') return;
                if (i + 1 < fields.length) fields[i + 1].el.focus();
                else fields[i].submit();
            });
        }
    }

    // ---- 登录窗口（DLogIn） -----------------------------------------------

    async showLogin() {
        const win = await this._win(WIN_LOGIN);
        // 输入框：EdId(350,259) EdPasswd(350,291)，窗口居中(252,173) → 相对 (98,86)/(98,118)
        const id = this._edit(win, 98, 86, 137, { max: 20 });
        const pw = this._edit(win, 98, 118, 137, { pwd: true, max: 10 });
        id.addEventListener('keydown', (e) => { if (e.key === 'Enter') pw.focus(); });
        pw.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._login(); });

        // 登录失败不再画在窗口底部（原来 left=24,top=224 会压在按钮/背景上），
        // 改为 onMessage 里 _showMsgDlg 的居中弹窗，照原版 FrmDlg.DMessageDlg。

        // 按钮（FState.pas）：Ok(171,165) New(24,207) ChgPw(111,207) Close(252,28)
        await this._btn(win, BTN_SUBMIT, 171, 165, () => this._login());
        await this._btn(win, BTN_NEWUSER, 24, 207, () => this.showRegister());
        await this._btn(win, BTN_CHGPW, 111, 207, () => this.showChgpw());
        await this._btn(win, BTN_CLOSE, 252, 28, () => this.conn.close());

        // 记住的用户名预填；若有 token 则自动免密登录（连接此时已就绪）。
        let savedUser = '', token = '';
        try {
            savedUser = localStorage.getItem(LS_USER) || '';
            token = localStorage.getItem(LS_TOKEN) || '';
        } catch { /* 隐私模式 */ }
        if (savedUser) id.value = savedUser;
        if (token) {
            this._autoTrying = true;
            this.conn.send('tokenLogin', { token });
        } else {
            id.focus();   // 照 OpenScene：EdId.SetFocus
        }
    }

    // ---- 注册窗口（DNewAccount） ------------------------------------------

    async showRegister() {
        const win = await this._win(WIN_REG);
        // 13 个输入框（照 IntroScn.pas：left=161，top=116..388）
        const fields = REG_FIELDS.map(f => ({
            ...f,
            el: this._edit(win, 161, f.top, f.w, { pwd: f.pwd, max: f.max }),
        }));
        this._regInputs = {};
        for (const f of fields) this._regInputs[f.key] = f.el;

        this.errEl = this._err(win, 60, 452, 520);

        // 回车跳转 + 末尾提交
        fields[fields.length - 1].submit = () => this._register();
        this._enterChain(fields);

        // 按钮（FState.pas）：Ok(160,417) Cancel(448,419) Close(587,33)
        await this._btn(win, BTN_SUBMIT, 160, 417, () => this._register());
        await this._btn(win, BTN_CANCEL, 448, 419, () => this.showLogin());
        await this._btn(win, BTN_CLOSE, 587, 33, () => this.showLogin());

        fields[0].el.focus();
    }

    // 注册校验（照原版 NewIdCheckNewId / NewIdCheckSSno / NewIdCheckBirthDay 精神）
    _register() {
        const v = {};
        for (const f of REG_FIELDS) v[f.key] = this._regInputs[f.key].value.trim();
        if (v.username.length < 3) {
            this.errEl.textContent = '帐号至少 3 位。';
            this._regInputs.username.focus();
            return;
        }
        if (v.password.length < 4) {
            this.errEl.textContent = '密码至少 4 位。';
            this._regInputs.password.focus();
            return;
        }
        if (v.confirm !== v.password) {
            this.errEl.textContent = '两次密码不一致。';
            this._regInputs.confirm.focus();
            return;
        }
        // 服务端当前只认 username/password；其余字段一并发送（服务端忽略，将来扩展无需改前端）
        this.conn.send('register', {
            username: v.username, password: v.password,
            name: v.name, ssno: v.ssno, birthday: v.birthday,
            quiz1: v.quiz1, answer1: v.answer1, quiz2: v.quiz2, answer2: v.answer2,
            phone: v.phone, mobile: v.mobile, email: v.email,
        });
    }

    // ---- 改密码窗口（DChgPw） ----------------------------------------------

    async showChgpw() {
        const win = await this._win(WIN_CHGPW);
        const fields = CHGPW_FIELDS.map(f => ({
            ...f,
            el: this._edit(win, 241, f.top, f.w, { pwd: f.pwd, max: f.max }),
        }));
        this._chgInputs = {};
        for (const f of fields) this._chgInputs[f.key] = f.el;

        this.errEl = this._err(win, 30, 278, 360);

        fields[fields.length - 1].submit = () => this._chgpw();
        this._enterChain(fields);

        // 按钮（FState.pas）：Ok(181,253) Cancel(276,252)
        await this._btn(win, BTN_SUBMIT, 181, 253, () => this._chgpw());
        await this._btn(win, BTN_CANCEL, 276, 252, () => this.showLogin());

        fields[0].el.focus();
    }

    _chgpw() {
        // 服务端暂无 changePassword 协议（照 Delphi CM_CHANGEPASSWORD=2003，待补）
        this.errEl.textContent = '修改密码功能暂未开放。';
    }

    // ---- 逻辑 ---------------------------------------------------------------

    _login() {
        if (this._door.active) return;
        const u = this._edits[0].value.trim();
        const p = this._edits[1].value;
        if (!u || !p) {
            this._showMsgDlg('请输入帐号和密码。');
            return;
        }
        this._autoTrying = false;
        try { localStorage.setItem(LS_USER, u); } catch { /* 隐私模式 */ }
        this.conn.send('login', { username: u, password: p });
    }

    onMessage(type, msg) {
        if (type === 'loginResult') {
            if (msg.ok) {
                // 保存服务端签发的 token + 用户名，供下次免密登录/挂机后台使用。
                try {
                    if (msg.token) localStorage.setItem(LS_TOKEN, msg.token);
                    if (msg.username) localStorage.setItem(LS_USER, msg.username);
                } catch { /* 隐私模式 */ }
                this._autoTrying = false;
                this._startDoor();     // 原版：登录成功先开门，再淡出切选人
            } else {
                // 自动登录失败：静默清掉失效 token，回到手输界面（不弹错误框）。
                if (this._autoTrying) {
                    this._autoTrying = false;
                    try { localStorage.removeItem(LS_TOKEN); } catch { /* 隐私模式 */ }
                    if (this._edits[0]) this._edits[0].focus();
                    return;
                }
                // 原版 SM_PASSWD_FAIL 走 FrmDlg.DMessageDlg 居中弹窗，
                // LoginScene.PassWdFail 只重新显示输入框，并不在窗口里画一行字。
                this._showMsgDlg(msg.error || 'ID不存在或未知错误，请稍后再试。');
            }
        } else if (type === 'registerResult') {
            if (msg.ok) {
                this._login();   // 照原版注册成功后自动登录
            } else {
                this._showMsgDlg(msg.error || '注册失败。');
            }
        }
    }

    /** 原版 TLoginScene.OpenLoginDoor：隐藏登录框、播石门音、开始 10 帧开门。 */
    async _startDoor() {
        if (this._door.active || this._door.done) return;
        this._door.active = true;
        this._door.startAt = Date.now();
        if (this.root) this.root.style.display = 'none';   // HideLoginBox
        try { sound.play(DOOR_SOUND); } catch (e) { /* 音频未解锁时忽略 */ }
        const frames = [];
        for (let f = 0; f < DOOR_FRAMES; f++) {
            try { frames.push(await this.chr.getTexture(DOOR_FIRST_INDEX + f)); }
            catch (e) { frames.push(null); }
        }
        this._door.frames = frames;
    }

    /**
     * 原版 SM_PASSWD_FAIL → FrmDlg.DMessageDlg：屏幕居中的模态提示框，
     * 白字黑边、'\' 分行、行距 14。这里用 DOM 覆盖层近似底图 g_WMainImages[360/380]。
     */
    _showMsgDlg(text) {
        if (!this.root) return;
        const old = this.root.querySelector('[data-msgdlg]');
        if (old) old.remove();
        const mask = document.createElement('div');
        mask.dataset.msgdlg = '1';
        mask.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;'
            + 'justify-content:center;pointer-events:auto;';
        const box = document.createElement('div');
        box.style.cssText = 'width:320px;box-sizing:border-box;padding:20px 18px 14px;'
            + 'background:#14100a;border:2px solid #8a7448;box-shadow:0 0 0 1px #000,0 8px 24px rgba(0,0,0,.7);'
            + 'color:#fff;font:13px/20px SimSun,serif;text-align:center;';
        for (const line of String(text).split('\\')) {
            const d = document.createElement('div');
            d.textContent = line;
            d.style.cssText = 'text-shadow:1px 1px 0 #000,-1px -1px 0 #000;min-height:20px;';
            box.appendChild(d);
        }
        const ok = document.createElement('div');
        ok.textContent = '确定';
        ok.style.cssText = 'margin:14px auto 0;width:72px;height:22px;line-height:22px;cursor:pointer;'
            + 'border:1px solid #8a7448;background:#241d12;color:#ffe9a8;font-size:12px;';
        ok.addEventListener('click', () => mask.remove());
        box.appendChild(ok);
        mask.appendChild(box);
        this.root.appendChild(mask);
    }

    // 场景背景（照 TLoginScene.PlayScene：WChrSel.Images[102-80] = ChrSel[22] 铺满全屏）
    render(ctx) {
        ctx.fillStyle = '#000';
        ctx.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT);
        if (this._bgTex) {
            ctx.drawImage(this._bgTex.canvas, 0, 0);
        } else if (!this._bgLoading) {
            this._bgLoading = true;
            this.chr.getTexture(22).then((t) => { this._bgTex = t; });
        }
        if (!this._door.active || this._door.done) return;

        const elapsed = Date.now() - this._door.startAt;
        const frame = Math.min(DOOR_FRAMES - 1, Math.floor(elapsed / DOOR_FRAME_MS));
        const t = this._door.frames[frame];
        if (t && t.canvas) {
            const w = t.width || t.canvas.width;
            const h = t.height || t.canvas.height;
            // 原版 MSurface.Draw((SCREENWIDTH-d.Width) div 2 + 1,
            //                   (SCREENHEIGHT-d.Height) div 2 - 23, ..., TRUE)
            ctx.drawImage(t.canvas, Math.floor((SCREEN_WIDTH - w) / 2) + 1,
                          Math.floor((SCREEN_HEIGHT - h) / 2) - 23);
        }
        if (elapsed < DOOR_FRAMES * DOOR_FRAME_MS) return;
        // 10 帧播完 → 淡出到黑（原版 g_boDoFadeOut / g_nFadeIndex）→ ChangeScene(stSelectChr)
        const fade = Math.min(1, (elapsed - DOOR_FRAMES * DOOR_FRAME_MS) / DOOR_FADE_MS);
        ctx.fillStyle = `rgba(0,0,0,${fade})`;
        ctx.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT);
        if (fade >= 1) {
            this._door.done = true;
            this.manager.switchTo(new SelectCharScene(this.manager));
        }
    }
}
