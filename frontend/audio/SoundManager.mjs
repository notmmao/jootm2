// 音效 / BGM 管理（方案 B：sound.lst 编号驱动的全量 MP3）。
//
// 挂载方式：main.mjs 里 `sound.install(conn)` 一次即可，服务端与 WS 协议零改动：
//   - Connection._dispatch 支持 '*' 通配，音频模块在这里做事件 → 原版音效编号映射；
//   - /resources/Audio/*.mp3 由现有静态路由直接提供（Python / Java 都支持 audio/mpeg）；
//   - /audio/audio_map.json 是 scripts/convert_audio.py 生成的编号索引（提交进仓库）。
//
// 编号来自 MIR2/GameOfMir/Client/SoundUtil.pas 的 s_* 常量，映射表来自
// resources/Wav/sound.lst（GBK）。若索引或 mp3 缺失，自动回退到原始 WAV。
//
// 浏览器自动播放策略：首次 pointerdown/keydown 之前不发声（unlocked=false）。
// 脚步/攻击是高频事件，统一节流 + 并发音频数限流，避免一秒造出几十个元素。

import { wavUrl } from '../gfx/resource.mjs';

const MAP_URL = '/audio/audio_map.json';
const LS_ENABLED = 'mir2.sound.enabled';
const LS_VOLUME = 'mir2.sound.volume';
const LS_BGM_VOLUME = 'mir2.sound.bgmVolume';

/** 原版 SoundUtil.pas 的 s_* 常量（只列本模块用到的）。 */
export const SFX = Object.freeze({
    walkL: 1, walkR: 2, runL: 3, runR: 4,
    hitShort: 50, hitSword: 52, hitLong: 56,
    struckSword: 62, struckBodySword: 70,
    strikeStone: 91, dropStonePiece: 92,
    rockDoor: 100, meltStone: 101,
    button: 103, buttonRock: 104, buttonGlass: 105,
    money: 106, eatDrug: 107, clickDrug: 108,
    spaceMoveOut: 109, spaceMoveIn: 110,
    clickWeapon: 111, clickArmor: 112, clickRing: 113,
    clickArmRing: 114, clickNecklace: 115, clickHelmet: 116,
    clickGrobes: 117, itemClick: 118,
    yedoMan: 130, longHit: 132, wideHit: 133,
    rushL: 134, rushR: 135, fireHitReady: 136, fireHit: 137,
    manStruck: 138, womanStruck: 139, manDie: 144, womanDie: 145,
});

/**
 * 怪物叫声偏移（原版声音索引 = 怪物外观 Appr × 10 + 200 + 下列偏移）。
 * 来源：传奇客户端 sound.lst 约定「外观×10+200」六态：
 *   0 发现敌人 / 1 站立 / 2 攻击喊声 / 3 攻击武器声 / 4 挨打叫声 / 5 死亡。
 * 多个独立资料（传奇论坛 / 博客园 / upx8 Wiki）一致确认，且本仓库 audio_map.json
 * 已含对应索引（如鸡 appr=46 → 660 段，661/662/664/665 齐全）。
 */
const MON_SOUND_OFFSET = Object.freeze({
    discover: 0, idle: 1, attack: 2, attackW: 3, hit: 4, die: 5,
});

/**
 * 原版游戏主场景 BGM（PlayScn.pas::TPlayScene.SoundOnTimer）：
 *   PlaySound(s_main_theme)，而 SoundUtil.pas 里 s_main_theme = 102，
 *   对应 sound.lst 的 `102: wav\102.wav`（实测 43.9 秒，与源码注释「长度：43秒」吻合）。
 * 定时器首次触发后 `Interval := 46 * 1000`，即每 46 秒重播一次、曲间留约 2.1 秒静音，
 * 并非无缝 loop。bmg_field（Field2.wav，191 秒）在原版客户端中没有任何 PlayBGM 调用点，
 * 是后期版本附加的长曲，仍保留为可选键但不作进游戏默认曲。
 */
const BGM_PLAY_ID = 102;
const BGM_PLAY_GAP_MS = 2100;   // 46s 周期 - 43.9s 曲长 ≈ 2.1s

/** 战士特攻编号（PlayScene.SWORD_HIT_FX 的 key）→ 原版音效。 */
const SWORD_SFX = Object.freeze({
    7: SFX.hitLong,        // 攻杀剑术
    12: SFX.longHit,       // 刺杀剑术
    25: SFX.wideHit,       // 半月弯刀
    26: SFX.fireHit,       // 烈火剑法
});

/**
 * 技能 → 近似音效。原版玩家施法音来自 Actor 的 m_nMagicStartSound 等字段，
 * 而本项目 WS 的 spellCast 只带 magicId，所以这里给一份可调整的近似映射：
 * 火系用烈火音、雷/冰用 Delphi 雷击特效音、辅助用短音。表里没有的用 itemClick。
 */
const MAGIC_SFX = Object.freeze({
    1: 137, 5: 137, 9: 137, 22: 137, 23: 137,          // 火球/大火球/地狱火/火墙/爆裂
    11: 8301, 28: 8301, 32: 8302, 21: 8226,            // 雷电/疾光电影/冰咆哮/地狱雷光
    4: 101, 15: 101, 16: 101,                          // 治愈/幽灵盾/神圣战甲
});

/** 背包物品单击音（照 SoundUtil.pas::ItemClickSound 的 case）。 */
export function itemClickSound(stdMode) {
    switch (stdMode) {
        case 0: case 31: return SFX.clickDrug;
        case 5: case 6: return SFX.clickWeapon;
        case 10: case 11: return SFX.clickArmor;
        case 22: case 23: return SFX.clickRing;
        case 24: case 26: return SFX.clickArmRing;
        case 19: case 20: case 21: return SFX.clickNecklace;
        case 15: return SFX.clickHelmet;
        default: return SFX.itemClick;
    }
}

function lsGet(key, fallback) {
    try { return localStorage.getItem(key) ?? fallback; } catch { return fallback; }
}
function lsSet(key, value) {
    try { localStorage.setItem(key, value); } catch { /* 隐私模式：忽略 */ }
}
function clamp01(v) {
    const n = Number(v);
    return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0.8;
}

/** 生成一个 0.04s 的静音 WAV data URI，用于首次手势时试探 autoplay 权限。 */
function silentWavUri() {
    const samples = 900;
    const buf = new ArrayBuffer(44 + samples * 2);
    const dv = new DataView(buf);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) dv.setUint8(off + i, s.charCodeAt(i)); };
    str(0, 'RIFF'); dv.setUint32(4, 36 + samples * 2, true);
    str(8, 'WAVEfmt '); dv.setUint32(16, 16, true);
    dv.setUint16(20, 1, true); dv.setUint16(22, 1, true);
    dv.setUint32(24, 22050, true); dv.setUint32(28, 44100, true);
    dv.setUint16(32, 2, true); dv.setUint16(34, 16, true);
    str(36, 'data'); dv.setUint32(40, samples * 2, true);
    const bytes = new Uint8Array(buf);   // 采样区全 0 = 静音
    let s = '';
    for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
    return 'data:audio/wav;base64,' + btoa(s);
}

class SoundManager {
    constructor() {
        this.enabled = lsGet(LS_ENABLED, '1') !== '0';
        this.volume = clamp01(lsGet(LS_VOLUME, '0.8'));
        this.bgmVolume = clamp01(lsGet(LS_BGM_VOLUME, '0.45'));
        this.base = '/resources/';
        this.audioDir = 'Audio/';
        this.sounds = {};
        this.bgm = {};
        this.missing = [];
        this.stats = null;
        this.mapReady = false;
        this.fallback = false;      // 索引不可用：退化成 resources.json 的 wavUrl()
        this.mapError = null;
        this.unlocked = false;      // 浏览器手势解锁（autoplay 策略）
        this.installed = false;

        this.templates = new Map(); // url -> HTMLAudioElement 播放模板
        this.voices = new Set();    // 正在播放的短音效
        this.maxVoices = 24;
        this.lastAt = new Map();    // 编号 -> 上次播放时间（节流）

        // 实体注册表：id -> 类型/appr，用于按原版公式播放怪物叫声（Appr×10+200+偏移）。
        // 由 PlayScene 在实体出现/更新时填充；索引缺失的怪物自动静默（不报错）。
        this._entAppr = new Map();
        this._entType = new Map();

        this.bgmAudio = null;
        this.bgmKey = null;
        this._bgmUrl = null;
        this._bgmReplayTimer = null;    // 原版 46 秒周期重播主场景音乐
        this.selfId = null;
        this.stepFlip = false;
        this.prevGold = null;
        this.prevHp = null;
    }

    /** 在 main.mjs 里调用一次：挂 WS 通配监听、全局点击、手势解锁、异步加载索引。 */
    install(conn) {
        if (this.installed) return this;
        this.installed = true;
        this.conn = conn;
        conn.on('*', (msg) => this._onMessage(msg));
        const prevClose = conn.onClose;
        conn.onClose = () => {
            if (prevClose) prevClose();
            this.selfId = null;
            this.setBgm('intro');
        };
        const unlock = () => this._unlock();
        document.addEventListener('pointerdown', unlock, { capture: true, once: true });
        document.addEventListener('keydown', unlock, { capture: true, once: true });
        document.addEventListener('click', (e) => this._onClick(e), true);
        this.setBgm('intro');       // 解锁后自动起播
        this._loadMap();
        console.info('[sound] 音频模块已挂载（默认开启，首次点击/按键后出声）；'
            + '调试用 window.__sound');
        return this;
    }

    async _loadMap() {
        try {
            const resp = await fetch(MAP_URL, { cache: 'no-cache' });
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            const data = await resp.json();
            this.base = data.base || '/resources/';
            this.audioDir = data.audioDir || 'Audio/';
            this.sounds = data.sounds || {};
            this.bgm = data.bgm || {};
            // 原版主场景音乐是 sound.lst 的编号 102，不在 bgm 表里；从编号表派生，
            // 无需改动 convert_audio.py 与已提交的索引文件。
            if (!this.bgm.play && this.sounds[String(BGM_PLAY_ID)]) {
                this.bgm.play = this.sounds[String(BGM_PLAY_ID)];
            }
            this.missing = data.missing || [];
            this.stats = data.stats || null;
            console.info(`[sound] 索引已加载：${Object.keys(this.sounds).length} 个编号 / `
                + `${Object.keys(this.bgm).length} 首 BGM / ${this.missing.length} 条缺失`);
        } catch (e) {
            this.fallback = true;
            this.mapError = String(e);
            console.warn('[sound] 索引不可用，回退 resources.json 的 wavUrl()（非数字文件名音效会缺失）', e);
        }
        this.mapReady = true;
        if (this.unlocked && this.enabled && this.bgmKey) this._startBgm();
    }

    _unlock() {
        if (this.unlocked) return;
        this.unlocked = true;
        try {
            const probe = new Audio(silentWavUri());
            probe.muted = true;
            probe.play().catch(() => {});   // 只是试探，失败也不影响后续
        } catch { /* 忽略 */ }
        if (this.enabled && this.bgmKey) this._startBgm();
        console.info('[sound] 浏览器音频已解锁');
    }

    // ---- 播放核心 -------------------------------------------------------

    _template(url) {
        let t = this.templates.get(url);
        if (!t) {
            t = new Audio();
            t.preload = 'auto';
            t.src = url;
            this.templates.set(url, t);
        }
        return t;
    }

    /** 播放一个 URL；primary 失败时改用 fallback（原始 WAV），只回退一次。 */
    _playUrl(url, fallback, volume, isBgm) {
        if (!isBgm && this.voices.size >= this.maxVoices) return null;
        const tpl = this._template(url);
        let el = tpl;
        if (!tpl.paused && !tpl.ended) el = tpl.cloneNode(true);   // 同名并发
        el.volume = clamp01(volume);
        let fellBack = false;
        const cleanup = () => {
            el.removeEventListener('ended', cleanup);
            el.removeEventListener('error', onError);
            this.voices.delete(el);
        };
        const onError = () => {
            if (fellBack || !fallback || fallback === url) { cleanup(); return; }
            fellBack = true;
            cleanup();
            this._playUrl(fallback, null, volume, isBgm);
        };
        if (!isBgm) {
            this.voices.add(el);
            el.addEventListener('ended', cleanup);
        }
        el.addEventListener('error', onError);
        const p = el.play();
        if (p && typeof p.catch === 'function') {
            p.catch((err) => {
                if (err && err.name === 'NotAllowedError') {
                    this.unlocked = false;
                    console.warn('[sound] 浏览器阻止了自动播放，请再点一次页面');
                    cleanup();
                } else if (err && err.name === 'NotSupportedError') {
                    onError();
                } else {
                    cleanup();
                }
            });
        }
        return el;
    }

    /**
     * 按原版编号播音效。返回是否真的发起播放（被节流/关闭/缺失时 false）。
     * opts: {gap: 最小间隔 ms, volume: 0..1}
     */
    play(id, opts = {}) {
        id = Number(id);
        if (!this.enabled || !this.unlocked || !this.mapReady || !Number.isFinite(id) || id <= 0) return false;
        const key = opts.key || String(id);   // 脚步等交替编号的事件用统一 key 节流
        const gap = opts.gap ?? 80;
        const now = performance.now();
        if (now - (this.lastAt.get(key) ?? -1e9) < gap) return false;

        let mp3 = null, wav = null;
        const entry = this.fallback ? null : this.sounds[String(id)];
        if (entry) {
            mp3 = this.base + this.audioDir + entry.mp3;
            wav = this.base + entry.wav;
        } else {
            wav = wavUrl(id);       // 索引不可用时的退化路径
        }
        if (!mp3 && !wav) return false;   // sound.lst 里指向缺失文件的 15 个编号，静默跳过
        this.lastAt.set(key, now);
        this._playUrl(mp3 || wav, mp3 ? wav : null, opts.volume ?? this.volume, false);
        return true;
    }

    // ---- BGM ------------------------------------------------------------

    /**
     * 切换 BGM：intro / select / play / field / gameover / theme / heroLogin / heroLogout。
     * `play` = 原版游戏主场景音乐（编号 102），`field` = 后期附加的 Field2 长曲（可手动切回试听）。
     */
    setBgm(key) {
        if (!key) return false;
        this.bgmKey = key;
        if (!this.mapReady || !this.enabled || !this.unlocked) return true;
        this._startBgm();
        return true;
    }

    _startBgm() {
        const key = this.bgmKey;
        if (!key || !this.enabled || !this.unlocked || this.fallback) return;
        const entry = this.bgm[key];
        if (!entry) return;
        const url = this.base + this.audioDir + entry.mp3;
        if (!this.bgmAudio) {
            this.bgmAudio = new Audio();
            this.bgmAudio.preload = 'auto';
        }
        // 主场景音乐按原版 46 秒定时器重播（曲间留 BGM_PLAY_GAP_MS 静音），
        // 其余场景（登录/选角/长曲）走 HTML5 loop 无缝循环。
        const timed = (key === 'play');
        clearTimeout(this._bgmReplayTimer);
        this._bgmReplayTimer = null;
        this.bgmAudio.loop = !timed;
        this.bgmAudio.onended = timed ? () => {
            if (this.bgmKey !== 'play' || !this.enabled || !this.unlocked) return;
            this._bgmReplayTimer = setTimeout(() => {
                if (this.bgmKey === 'play' && this.enabled && this.unlocked && this.bgmAudio) {
                    this.bgmAudio.currentTime = 0;
                    this.bgmAudio.play().catch(() => {});
                }
            }, BGM_PLAY_GAP_MS);
        } : null;
        this.bgmAudio.onerror = () => {
            const cur = this.bgm[this.bgmKey];
            if (!cur || !this.bgmAudio || this._bgmFellBackFor === this._bgmUrl) return;
            this._bgmFellBackFor = this._bgmUrl;
            this.bgmAudio.src = this.base + cur.wav;
            this.bgmAudio.play().catch(() => {});
        };
        this.bgmAudio.volume = this.bgmVolume;
        if (this._bgmUrl === url) {
            if (this.bgmAudio.paused) this.bgmAudio.play().catch(() => {});
            return;
        }
        this._bgmUrl = url;
        this._bgmFellBackFor = null;
        this.bgmAudio.pause();
        this.bgmAudio.src = url;
        this.bgmAudio.play().catch(() => {});
    }

    // ---- 开关 / 音量 -----------------------------------------------------

    toggle() {
        this.enabled = !this.enabled;
        lsSet(LS_ENABLED, this.enabled ? '1' : '0');
        if (!this.enabled) {
            clearTimeout(this._bgmReplayTimer);
            this._bgmReplayTimer = null;
            if (this.bgmAudio) this.bgmAudio.pause();
            for (const v of this.voices) { try { v.pause(); } catch { /* ignore */ } }
            this.voices.clear();
        } else if (this.unlocked && this.bgmKey) {
            this._startBgm();
        }
        console.info('[sound] ' + (this.enabled ? '开启' : '关闭'));
        return this.enabled;
    }

    setEnabled(on) {
        if (this.enabled === !!on) return this.enabled;
        return this.toggle();
    }

    setVolume(v) {
        this.volume = clamp01(v);
        lsSet(LS_VOLUME, String(this.volume));
        return this.volume;
    }

    setBgmVolume(v) {
        this.bgmVolume = clamp01(v);
        lsSet(LS_BGM_VOLUME, String(this.bgmVolume));
        if (this.bgmAudio) this.bgmAudio.volume = this.bgmVolume;
        return this.bgmVolume;
    }

    /** 调试 / 自测用：window.__sound.status() */
    status() {
        return {
            enabled: this.enabled,
            unlocked: this.unlocked,
            mapReady: this.mapReady,
            fallback: this.fallback,
            mapError: this.mapError,
            soundIds: Object.keys(this.sounds).length,
            bgmKeys: Object.keys(this.bgm),
            missingIds: this.missing.length,
            stats: this.stats,
            bgmKey: this.bgmKey,
            bgmPlaying: !!(this.bgmAudio && !this.bgmAudio.paused),
            volume: this.volume,
            bgmVolume: this.bgmVolume,
            voices: this.voices.size,
            selfId: this.selfId,
        };
    }

    // ---- 事件 → 音效 -----------------------------------------------------

    _onClick(e) {
        const target = e.target;
        if (!(target instanceof Element)) return;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) return;
        const tagged = target.closest('[data-sfx]');
        if (tagged) {
            this.play(Number(tagged.dataset.sfx) || SFX.button, { gap: 60 });
            return;
        }
        // 所有 WIL 图按钮热区都是 inline cursor:pointer（登录/选角/HUD/NPC/商店）
        let node = target;
        for (let i = 0; node && i < 4; i++, node = node.parentElement) {
            if ((node.style && node.style.cursor || '').toLowerCase() === 'pointer') {
                this.play(SFX.button, { gap: 80 });
                return;
            }
        }
    }

    _step(run) {
        const id = run
            ? (this.stepFlip ? SFX.runR : SFX.runL)
            : (this.stepFlip ? SFX.walkR : SFX.walkL);
        this.stepFlip = !this.stepFlip;
        this.play(id, { gap: 250, key: 'step', volume: this.volume * 0.7 });
    }

    _gold(gold) {
        if (typeof gold !== 'number') return;
        if (this.prevGold !== null && gold > this.prevGold) {
            this.play(SFX.money, { gap: 250 });
        }
        this.prevGold = gold;
    }

    _magic(magicId) {
        let id = MAGIC_SFX[magicId] || SFX.itemClick;
        if (!this.fallback && !this.sounds[String(id)]) id = SFX.itemClick;
        this.play(id, { gap: 100 });
    }

    // ---- 实体注册（供怪物叫声按 Appr 公式定位）-----------------------

    /** PlayScene 在实体出现 / 更新时调用：记录 id 的类型与外观 Appr。 */
    registerEntity(id, type, appr) {
        if (id == null) return;
        if (type != null) this._entType.set(id, type);
        if (appr != null) this._entAppr.set(id, appr);
    }

    /** 实体移除时清注册，避免内存泄漏与陈旧 Appr。 */
    unregisterEntity(id) {
        if (id == null) return;
        this._entAppr.delete(id);
        this._entType.delete(id);
    }

    /** 返回该 id 怪物的 Appr（非怪物返回 null，避免给玩家/NPC 误放怪叫）。 */
    _apprOf(id) {
        if (this._entType.get(id) === 'monster') {
            const a = this._entAppr.get(id);
            return (a == null) ? null : a;
        }
        return null;
    }

    /**
     * 按原版公式播放怪物叫声：Appr×10+200+偏移。
     * 索引不存在（本资源未收录该怪声音）则静默返回，不触发 404 噪声。
     * state: 'attack' | 'hit' | 'die' | 'idle' | 'discover' | 'attackW'
     */
    _monSound(appr, state, opts = {}) {
        if (appr == null) return;
        const off = MON_SOUND_OFFSET[state];
        if (off == null) return;
        const id = appr * 10 + 200 + off;
        if (this.fallback || !this.sounds[String(id)]) return;   // 资源无此音 → 静默
        this.play(id, { ...opts, key: opts.key || `mon${appr}_${state}` });
    }

    _onMessage(msg) {
        if (!msg || !msg.type) return;
        switch (msg.type) {
            case 'loginResult':
                if (msg.ok) this.setBgm('select');
                break;
            case 'characterList':
                this.setBgm('select');
                break;
            case 'enterGame':
                if (msg.player && msg.player.id != null) this.selfId = msg.player.id;
                // 原版 PlayScn.pas::TPlayScene.SoundOnTimer → PlaySound(s_main_theme=102)，
                // 不是 bmg_field（Field2 长曲在原版无调用点）。
                this.setBgm('play');
                break;
            case 'changeMap':
                if (msg.player && msg.player.id != null) this.selfId = msg.player.id;
                this.play(SFX.spaceMoveIn, { gap: 400 });
                break;
            case 'move':
                if (this.selfId !== null && msg.id === this.selfId) this._step(!!msg.run);
                break;
            case 'attack': {
                const tgt = msg.targetId, atk = msg.attackerId;
                const tgtSelf = this.selfId !== null && tgt === this.selfId;
                const atkSelf = this.selfId !== null && atk === this.selfId;
                if (tgtSelf) {
                    this.play(msg.damage > 0 ? SFX.manStruck : SFX.struckSword, { gap: 140 });
                }
                if (atkSelf) {
                    this.play(SWORD_SFX[msg.swordHit] || SFX.hitSword, { gap: 100 });
                }
                // 怪物叫声（原版 Appr×10+200+偏移）：攻击方是怪物且非纯受击事件 → 攻击喊声(+2)；
                // 受击方是怪物 → 挨打叫声(+4)。索引缺失自动静默。
                if (!atkSelf && atk != null && !msg.noAttackAnim) {
                    const a = this._apprOf(atk);
                    if (a != null) this._monSound(a, 'attack', { gap: 180, volume: this.volume * 0.5 });
                }
                if (!tgtSelf && tgt != null) {
                    const t = this._apprOf(tgt);
                    if (t != null) this._monSound(t, 'hit', { gap: 160, volume: this.volume * 0.55 });
                }
                break;
            }
            case 'spellCast':
                if (this.selfId === null || msg.casterId === this.selfId) this._magic(msg.magicId);
                else this.play(SFX.itemClick, { gap: 200, volume: this.volume * 0.45 });
                break;
            case 'death':
                if (this.selfId !== null && msg.id === this.selfId) {
                    this.play(SFX.manDie, { gap: 600 });
                } else {
                    const a = this._apprOf(msg.id);
                    if (a != null) this._monSound(a, 'die', { gap: 300, volume: this.volume * 0.7 });
                }
                break;
            case 'stats':
                if (msg.id == null || msg.id === this.selfId) {
                    this._gold(msg.gold);
                    if (typeof msg.hp === 'number') {
                        if (this.prevHp !== null && msg.hp < this.prevHp - 1) {
                            this.play(SFX.manStruck, { gap: 450, volume: this.volume * 0.7 });
                        }
                        this.prevHp = msg.hp;
                    }
                }
                break;
            case 'bagUpdate':
                this._gold(msg.gold);
                break;
            case 'itemDrop': {
                const name = String(msg.name || (msg.item && msg.item.name) || '');
                const isGold = Number(msg.gold || 0) > 0 || name.includes('金币');
                this.play(isGold ? SFX.money : SFX.dropStonePiece,
                    { gap: 200, volume: this.volume * 0.7 });
                break;
            }
            case 'spaceMove':
                this.play(SFX.spaceMoveIn, { gap: 300 });
                break;
            default:
                break;
        }
    }
}

/** 全局单例；PlayScene._toggleSound() 与背包点击音效都用它。 */
export const sound = new SoundManager();
