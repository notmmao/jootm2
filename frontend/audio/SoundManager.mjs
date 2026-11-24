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
    // 脚步：地板 1-4 / 石 5-8 / 草 9-12 / 粗糙 13-16 / 木 17-20 / 洞穴 21-24 / 房间 25-28 / 水 29-32
    walkL: 1, walkR: 2, runL: 3, runR: 4,
    walkStoneL: 5, walkLawnL: 9, walkRoughL: 13, walkWoodL: 17,
    walkCaveL: 21, walkRoomL: 25, walkWaterL: 29,
    // 攻击武器音（Actor.pas:2273，按 m_btWeapon div 2）
    hitShort: 50, hitWooden: 51, hitSword: 52, hitDo: 53,
    hitAxe: 54, hitClub: 55, hitLong: 56, hitFist: 57,
    // 受击「武器音」（Actor.pas:2362）
    struckShort: 60, struckWooden: 61, struckSword: 62, struckDo: 63,
    struckAxe: 64, struckClub: 65,
    // 受击「身体/护甲音」（Actor.pas:2296-2309）
    struckBodySword: 70, struckBodyAxe: 71, struckBodyLongstick: 72, struckBodyFist: 73,
    struckArmorSword: 80, struckArmorAxe: 81, struckArmorLongstick: 82, struckArmorFist: 83,
    strikeStone: 91, dropStonePiece: 92,
    rockDoor: 100, meltStone: 101,
    button: 103, buttonRock: 104, buttonGlass: 105,
    money: 106, eatDrug: 107, clickDrug: 108,
    spaceMoveOut: 109, spaceMoveIn: 110,
    clickWeapon: 111, clickArmor: 112, clickRing: 113,
    clickArmRing: 114, clickNecklace: 115, clickHelmet: 116,
    clickGrobes: 117, itemClick: 118,
    yedoMan: 130, yedoWoman: 131, longHit: 132, wideHit: 133,
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
    appear: 0, discover: 0, idle: 1, attack: 2, attackW: 3, hit: 4, die: 5,
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

/**
 * 战士特攻：服务端 `swordHit` 带的是**魔法 id**（7 攻杀 / 12 刺杀 / 25 半月 / 26 烈火），
 * 对应原版动作 SM_POWERHIT / SM_LONGHIT / SM_WIDEHIT / SM_FIREHIT。
 * 原版 `TActor.RunActSound`（Actor.pas:2416-2459）在第 2 帧先播**武器音**
 * （按 m_btWeapon 分类，见 weaponHitSound），再叠一个特攻人声：
 *   攻杀 → s_yedo_man/s_yedo_woman(130/131，按性别)
 *   刺杀 → s_longhit(132)   半月 → s_widehit(133)   烈火 → s_firehit(137)
 */
const SWORD_VOICE = Object.freeze({
    7: 'yedo', 12: SFX.longHit, 25: SFX.wideHit, 26: SFX.fireHit,
});

/** 武器 shape → 攻击武器音（Actor.pas:2273 `case (m_btWeapon div 2)`）。 */
export function weaponHitSound(weapon) {
    const s = Math.floor(Number(weapon || 0) / 2);
    switch (s) {
        case 6: case 20: return SFX.hitShort;
        case 1: return SFX.hitWooden;
        case 2: case 13: case 9: case 5: case 14: case 22: return SFX.hitSword;
        case 4: case 17: case 10: case 15: case 16: case 23: return SFX.hitDo;
        case 3: case 7: case 11: return SFX.hitAxe;
        case 24: return SFX.hitClub;
        case 8: case 12: case 18: case 21: return SFX.hitLong;
        default: return SFX.hitFist;
    }
}

/**
 * 武器 shape → 受击「武器音」（Actor.pas:2356-2373 `m_nStruckWeaponSound`）。
 * 注意原版这里是 `attackweapon := hiter.m_btWeapon div 2` 之后又 `case (attackweapon div 2)`，
 * 等于**除了两次 2**（原版就这么写的，注释里 8/12/18/21 还给成了 `s_struck_wooden; //long`），
 * 这里照抄，别「修好」它。
 */
export function weaponStruckSound(weapon) {
    const s = Math.floor(Math.floor(Number(weapon || 0) / 2) / 2);
    switch (s) {
        case 6: case 20: return SFX.struckShort;
        case 1: return SFX.struckWooden;
        case 2: case 13: case 9: case 5: case 14: case 22: return SFX.struckSword;
        case 4: case 17: case 10: case 15: case 16: case 23: return SFX.struckDo;
        case 3: case 7: case 11: return SFX.struckAxe;
        case 24: return SFX.struckClub;
        case 8: case 12: case 18: case 21: return SFX.struckWooden;
        default: return 0;
    }
}

/**
 * 受击「身体音」：被打者是人时，按**攻击者武器**（单次 div 2）+ **自己衣服**
 * （`m_btDress div 2 = 3` 是重盔甲）选 `s_struck_body_*`(70-73) 或 `s_struck_armor_*`(80-83)。
 * 攻击者不是玩家时原版不重算，保持初始值 `s_struck_body_longstick`(72)。
 */
export function struckBodySound(weapon, dress, attackerIsPlayer) {
    if (!attackerIsPlayer) return SFX.struckBodyLongstick;
    const aw = Math.floor(Number(weapon || 0) / 2);
    const heavy = Math.floor(Number(dress || 0) / 2) === 3;
    switch (aw) {
        case 6: case 1: case 2: case 4: case 5: case 9: case 10:
        case 13: case 14: case 15: case 16: case 17:
            return heavy ? SFX.struckArmorSword : SFX.struckBodySword;
        case 3: case 7: case 11:
            return heavy ? SFX.struckArmorAxe : SFX.struckBodyAxe;
        case 8: case 12: case 18:
            return heavy ? SFX.struckArmorLongstick : SFX.struckBodyLongstick;
        default:
            return heavy ? SFX.struckArmorFist : SFX.struckBodyFist;
    }
}

/**
 * 脚下地砖 → 左脚步音基号（Actor.pas:2170-2257 的三道覆盖）。
 * 本项目的 1.80 map tile 第 5 个字节被用作「对象图库号」而不是 `btArea`，
 * 所以拿不到原版第一层（`btArea*10000 + wBkImg - 1`）；这里实现不依赖它的后两层：
 *   wMidImg-1：0..115 地面、120..124 草地
 *   wFrImg-1：石 / 木 / 房间（最后覆盖，优先级最高）
 * 以及 bng 的两个特判（825..1349 隔段石头、1375..1799 隔段洞穴）。
 */
export function footstepBase(bng, mid, fr) {
    let s = SFX.walkL;                                  // 默认地面
    const b = Number(bng);
    if (b >= 825 && b <= 1349) {
        if (Math.floor((b - 825) / 25) % 2 === 0) s = SFX.walkStoneL;
    }
    if (b >= 1375 && b <= 1799) {
        if (Math.floor((b - 1375) / 25) % 2 === 0) s = SFX.walkCaveL;
    }
    const m = Number(mid);
    if (m >= 0 && m <= 115) s = SFX.walkL;
    else if (m >= 120 && m <= 124) s = SFX.walkLawnL;
    const f = Number(fr);
    if ((f >= 221 && f <= 289) || (f >= 583 && f <= 658) || (f >= 1183 && f <= 1206)
        || (f >= 7163 && f <= 7295) || (f >= 7404 && f <= 7414)) {
        s = SFX.walkStoneL;
    } else if ((f >= 3125 && f <= 3267) || (f >= 3757 && f <= 3948) || (f >= 6030 && f <= 6999)) {
        s = SFX.walkWoodL;
    } else if (f >= 3316 && f <= 3589) {
        s = SFX.walkRoomL;
    }
    return s;
}

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

/**
 * 使用物品音（照 SoundUtil.pas:313 `ItemUseSound`）：
 *   stdMode 0 → s_click_drug(108)
 *   stdMode 1/2 → s_eat_drug(107)
 *   其它（卷轴 3 / 技能书 4 / 捆 31 …）→ **静默**
 * 旧实现不分 StdMode 一律播 107，所以解捆、读书、用卷轴全成了喝药声。
 * 返回 0 表示「原版这里没声音」，调用方 play(0) 会被直接忽略。
 */
export function itemUseSound(stdMode) {
    switch (Number(stdMode)) {
        case 0: return SFX.clickDrug;
        case 1: case 2: return SFX.eatDrug;
        default: return 0;
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
        this.ready = Promise.resolve();   // 启动加载层等待音频索引就绪

        this.templates = new Map(); // url -> HTMLAudioElement 播放模板
        this.voices = new Set();    // 正在播放的短音效
        this.maxVoices = 24;
        this.lastAt = new Map();    // 编号 -> 上次播放时间（节流）

        // 实体注册表：id -> 类型/appr/外观，用于按原版公式播放怪物叫声（Appr×10+200+偏移）
        // 与人形受击音（按攻击者武器 + 自己衣服）。由 PlayScene 在实体出现/更新时填充。
        this._entAppr = new Map();
        this._entType = new Map();
        this._entWeapon = new Map();
        this._entDress = new Map();
        this._entSex = new Map();
        // 脚下地砖查询：PlayScene 注册，返回 {bng, mid, fr}（已减 1 的图库帧号）。
        // 为 null 时脚步退回默认地面音。
        this.terrainAt = null;

        this.bgmAudio = null;
        this.bgmKey = null;
        this._bgmUrl = null;
        this._bgmReplayTimer = null;    // 原版 46 秒周期重播主场景音乐
        this.selfId = null;
        this.stepFlip = false;
        this._lastMovePos = null;   // 自己上一次移动消息的坐标：纯转向/被挡（坐标没变）不播脚步
        this.prevGold = null;
        this.prevHp = null;
        this.prevLevel = null;
    }

    /**
     * 把音频模块挂到一条 WebSocket 连接上（可重复调用：选角界面「退出」会新建连接）。
     * 会重置与该连接绑定的状态（selfId / 金币与血量差值），避免跨会话串音。
     */
    attach(conn) {
        this.conn = conn;
        conn.on('*', (msg) => this._onMessage(msg));
        const prevClose = conn.onClose;
        conn.onClose = () => {
            if (prevClose) prevClose();
            this.selfId = null;
            this.setBgm('intro');
        };
        this.selfId = null;
        this.prevGold = null;
        this.prevHp = null;
        this.prevLevel = null;
        this._lastMovePos = null;
        return this;
    }

    /** 在 main.mjs 里调用一次：挂全局点击、手势解锁、异步加载索引（并挂到首条连接）。 */
    install(conn) {
        if (this.installed) return this.attach(conn);
        this.installed = true;
        this.attach(conn);
        const unlock = () => this._unlock();
        // 不能 once：移动端（尤其 iPad Safari）在切后台、来电、低电量或并发音过多后
        // 会撤销音频权限，_playUrl 收到 NotAllowedError 会把 unlocked 置回 false。
        // 若首次手势后监听就被移除，就再也无法重新解锁 → 永久静音（平板托管后无声
        // 正是这条路径）。改为常驻监听，每次手势都幂等重试；touchstart 覆盖部分
        // 只发 touch 不发 pointerdown 的老 WebView。
        document.addEventListener('pointerdown', unlock, { capture: true });
        document.addEventListener('keydown', unlock, { capture: true });
        document.addEventListener('touchstart', unlock, { capture: true, passive: true });
        // 切后台回来后系统会把 <audio> 暂停且不自动恢复，回前台时主动续播 BGM。
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && this.unlocked && this.enabled) this._startBgm();
        });
        document.addEventListener('click', (e) => this._onClick(e), true);
        this.setBgm('intro');       // 解锁后自动起播
        this.ready = this._loadMap();
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
        if (this.unlocked) {
            // 已解锁但 BGM 被系统暂停（切后台/被拒后）→ 借这次手势重新起播。
            // 这里不能 return：移动端每次有效手势都要给一次恢复机会。
            if (this.enabled && this.bgmKey) this._startBgm();
            return;
        }
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
        let done = false;
        let reaper = 0;
        const cleanup = () => {
            if (done) return;
            done = true;
            clearTimeout(reaper);
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
            // 移动端（iOS 切后台/被打断）ended 不一定触发，voices 会只增不减，
            // 撑到 maxVoices 后所有音效静默。加超时兜底回收（普通音效都很短）。
            reaper = setTimeout(cleanup, 15000);
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
     * `intro` / `gameover` 按本资源包的试听结论指在 audio_map.json 里交换过：
     * intro = 68.6s 空格版 Game over2.wav（开场曲），gameover = 34.3s Log-in-long2.wav（死亡曲）。
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
            // data-sfx="0" = 显式静默（原版空快捷栏/空格子点击不发声）
            const id = Number(tagged.dataset.sfx);
            if (id === 0) return;
            this.play(id || SFX.button, { gap: 60 });
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

    /**
     * 脚步：原版在走路动作第 1 帧播 `m_nFootStepSound`（左）、第 4 帧播 `+1`（右），
     * 跑动整体 +2（Actor.pas:2679-2682 / 2258-2259）。
     * base 由脚下地砖决定（footstepBase），默认地板 1。
     */
    _step(run, base) {
        const b = base || SFX.walkL;
        const id = (run ? b + 2 : b) + (this.stepFlip ? 1 : 0);
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

    // ---- 实体注册（供怪物叫声 / 人形受击音定位）-----------------------

    /**
     * PlayScene 在实体出现 / 更新时调用。
     * looks（可选）= { weapon, dress, sex }：原版 m_btWeapon / m_btDress / m_btSex，
     * 用于按武器材质选攻击音、按衣服选护甲音、按性别选惨叫与死亡音。
     */
    registerEntity(id, type, appr, looks) {
        if (id == null) return;
        if (type != null) this._entType.set(id, type);
        if (appr != null) this._entAppr.set(id, appr);
        if (looks) {
            if (looks.weapon != null) this._entWeapon.set(id, looks.weapon);
            if (looks.dress != null) this._entDress.set(id, looks.dress);
            if (looks.sex != null) this._entSex.set(id, looks.sex);
        }
    }

    /** 实体移除时清注册，避免内存泄漏与陈旧外观。 */
    unregisterEntity(id) {
        if (id == null) return;
        this._entAppr.delete(id);
        this._entType.delete(id);
        this._entWeapon.delete(id);
        this._entDress.delete(id);
        this._entSex.delete(id);
    }

    /** 返回该 id 怪物的 Appr（非怪物返回 null，避免给玩家/NPC 误放怪叫）。 */
    _apprOf(id) {
        if (this._entType.get(id) === 'monster') {
            const a = this._entAppr.get(id);
            return (a == null) ? null : a;
        }
        return null;
    }

    /** 是否为玩家（原版 m_btRace = 0）；只有玩家才有武器/衣服/性别音。 */
    _isPlayer(id) {
        return this._entType.get(id) === 'player' || id === this.selfId;
    }

    /** 技能起手音：Actor.pas:2319 `m_nMagicStartSound = 10000 + MagicSerial*10`。 */
    _magic(magicId) {
        const id = 10000 + Number(magicId || 0) * 10;
        if (this.fallback || !this.sounds[String(id)]) {
            // 资源没收这个魔法音时退回短促点击音，别整段静默。
            this.play(SFX.itemClick, { gap: 120, key: 'magic' });
            return;
        }
        this.play(id, { gap: 120, key: 'magic' });
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
                // 换角色/重进游戏：等级基准重取，否则第一次升级不会响。
                this.prevLevel = null;
                this.prevHp = null;
                // 原版 PlayScn.pas::TPlayScene.SoundOnTimer → PlaySound(s_main_theme=102)，
                // 不是 bmg_field（Field2 长曲在原版无调用点）。
                this.setBgm('play');
                break;
            case 'changeMap':
                if (msg.player && msg.player.id != null) this.selfId = msg.player.id;
                this._lastMovePos = null;   // 换图后坐标可能相同，别把第一步吞掉
                this.play(SFX.spaceMoveIn, { gap: 400 });
                break;
            case 'move':
                if (this.selfId !== null && msg.id === this.selfId) {
                    // 纯转向 / 被障碍挡住（服务端广播但坐标没变）不播脚步声：
                    // 原版脚步在 SM_WALK/SM_RUN 动作第 1/4 帧触发，而 SM_WALK 只在真正走动时下发。
                    const moved = this._lastMovePos == null
                        || this._lastMovePos.x !== msg.x || this._lastMovePos.y !== msg.y;
                    this._lastMovePos = { x: msg.x, y: msg.y };
                    if (!moved) break;
                    let base = SFX.walkL;
                    if (this.terrainAt) {
                        try {
                            const t = this.terrainAt(msg.x, msg.y);
                            if (t) base = footstepBase(t.bng, t.mid, t.fr);
                        } catch { /* 地图未就绪：退回默认地面音 */ }
                    }
                    this._step(!!msg.run, base);
                } else if (msg.id != null) {
                    // 怪物走动时偶发「哼声」：原版 RunActSound 里走路/转身第 1 帧
                    // `if Random(8) = 1 then PlaySound(m_nNormalSound)`（Actor.pas:2465-2469）
                    const a = this._apprOf(msg.id);
                    if (a != null && Math.random() < 0.125) {
                        this._monSound(a, 'idle', { gap: 900, volume: this.volume * 0.35 });
                    }
                }
                break;
            case 'attack': {
                const tgt = msg.targetId, atk = msg.attackerId;
                const tgtSelf = this.selfId !== null && tgt === this.selfId;
                const atkSelf = this.selfId !== null && atk === this.selfId;
                const atkIsPlayer = atk != null && this._isPlayer(atk);
                const atkWeapon = atk != null ? (this._entWeapon.get(atk) || 0) : 0;

                // 攻击方：原版 SM_HIT/SM_POWERHIT… 第 2 帧播武器音（按 m_btWeapon 分类）+ 特攻人声。
                // 「攻击动作」与「命中」是两条独立消息：服务端先无条件 SendRefMsg(RM_HIT,…)
                // （ObjGuard.pas:49、ObjMon.pas:684/814），命中判定失败只是不发 RM_STRUCK。
                // 客户端 Actor.pas:2271-2283 在 SM_HIT 分支无条件设置 m_nWeaponSound，
                // 所以 **MISS 也有挥空武器音**，只是没有受击方的弯腰音/惨叫。
                // noAttackAnim 是我们自造的「纯受击」广播（对方只发 SM_STRUCK），才要跳过。
                if (!msg.noAttackAnim && atk != null) {
                    if (atkIsPlayer) {
                        this.play(weaponHitSound(atkWeapon), { gap: 90, key: `hit${atk}` });
                        const v = SWORD_VOICE[msg.swordHit];
                        if (v === 'yedo') {
                            // 攻杀：s_yedo_man / s_yedo_woman（Actor.pas:2426-2427，按性别）
                            this.play(this._entSex.get(atk) === 1 ? SFX.yedoWoman : SFX.yedoMan,
                                { gap: 220, key: `swv${atk}` });
                        } else if (v) {
                            this.play(v, { gap: 220, key: `swv${atk}` });
                        }
                    } else {
                        // 怪物攻击喊声 appr*10+202
                        const a = this._apprOf(atk);
                        if (a != null) this._monSound(a, 'attack', { gap: 180, volume: this.volume * 0.5 });
                    }
                }
                // noDamage（打中但 AC 减免后伤害<=0，原版 _Attack ObjBase.pas:5441
                // `if dam > 0` 才 StruckDamage+RM_STRUCK）同样不播受击音：攻击者
                // 挥刀音照播（RM_HIT 无条件），受击方弯腰/身体/惨叫全无——修复
                // 「角色被打不掉血却播挨打音效」（用户 2026-11-24）。
                if (msg.miss || msg.noDamage) break;

                // 受击方：原版 TActor.RunSound 的 SM_STRUCK 分支连着播三声
                // （受击武器音 + 身体/护甲音 + 惨叫），见 Actor.pas:2382-2387。
                if (tgt != null) {
                    if (this._isPlayer(tgt)) {
                        const ws = weaponStruckSound(atkWeapon);
                        if (ws) this.play(ws, { gap: 120, key: `strw${tgt}` });
                        this.play(struckBodySound(atkWeapon, this._entDress.get(tgt) || 0, atkIsPlayer),
                            { gap: 120, key: `strb${tgt}` });
                        this.play(this._entSex.get(tgt) === 1 ? SFX.womanStruck : SFX.manStruck,
                            { gap: 260, key: `scream${tgt}`,
                              volume: tgtSelf ? this.volume : this.volume * 0.65 });
                    } else {
                        // 怪物挨打：appr*10+204
                        const t = this._apprOf(tgt);
                        if (t != null) this._monSound(t, 'hit', { gap: 160, volume: this.volume * 0.55 });
                    }
                }
                break;
            }
            case 'spellCast':
                // 原版 SM_SPELL 起手音 = 10000 + MagicSerial*10（Actor.pas:2319），
                // 所有客户端都播；不再按魔法类型自造近似音。
                this._magic(msg.magicId);
                break;
            case 'death':
                if (this.selfId !== null && msg.id === this.selfId) {
                    // 自己死亡：按性别播死亡音，再切死亡 BGM（Actor.pas:2388-2395）
                    this.play(this._entSex.get(msg.id) === 1 ? SFX.womanDie : SFX.manDie, { gap: 600 });
                    this.setBgm('gameover');
                } else if (this._isPlayer(msg.id)) {
                    this.play(this._entSex.get(msg.id) === 1 ? SFX.womanDie : SFX.manDie,
                        { gap: 300, volume: this.volume * 0.7 });
                } else {
                    const a = this._apprOf(msg.id);
                    if (a != null) this._monSound(a, 'die', { gap: 300, volume: this.volume * 0.7 });
                }
                break;
            case 'stats':
                if (msg.id == null || msg.id === this.selfId) {
                    this._gold(msg.gold);
                    // 升级特效音：原版 DrawEffectHum(8) → s_meltstone(101)（ClMain.pas:6734）
                    if (typeof msg.level === 'number') {
                        if (this.prevLevel !== null && msg.level > this.prevLevel) {
                            this.play(SFX.meltStone, { gap: 800, key: 'levelup' });
                        }
                        this.prevLevel = msg.level;
                    }
                    // 不再按 hp 下降补受击音：原版的受击音只在 SM_STRUCK（attack 消息）里播，
                    // 这里再播一次会变成「一刀两声」。中毒/自然掉血原版本就无声。
                    if (typeof msg.hp === 'number') this.prevHp = msg.hp;
                }
                break;
            case 'bagUpdate':
                this._gold(msg.gold);
                break;
            case 'itemDrop':
                // 原版「物品落地 / 丢弃」没有任何 PlaySound 调用点（s_drop_stonepiece 在
                // Actor.pas:3521 是被注释掉的）。旧实现播 92（砸石头声）属自造，去掉。
                // 金币入袋的音由 _gold() 按 SM_GOLDCHANGED 规则播 106。
                break;
            case 'spaceMove':
                this.play(SFX.spaceMoveIn, { gap: 300 });
                break;
            case 'spaceMoveHide':
                // 原版 SM_SPACEMOVE_HIDE → s_spacemove_out(109)（Client\Actor.pas:1638）
                this.play(SFX.spaceMoveOut, { gap: 300 });
                break;
            case 'spaceMoveShow':
                // 原版 SM_SPACEMOVE_SHOW → s_spacemove_in(110)（Actor.pas:1650）
                this.play(SFX.spaceMoveIn, { gap: 300 });
                break;
            case 'digUp':
            case 'alive':
                // SM_DIGUP/SM_ALIVE → m_nAppearSound = 200 + Appr*10（Actor.pas:2345/2401，
                // offset 0）；资源没有该怪的声音时 _monSound 静默。
                if (msg.entity && msg.entity.appr != null) {
                    this._monSound(msg.entity.appr, 'appear',
                                   { gap: 300, volume: this.volume * 0.7 });
                }
                break;
            default:
                break;
        }
    }
}

/** 全局单例；PlayScene._toggleSound() 与背包点击音效都用它。 */
export const sound = new SoundManager();
