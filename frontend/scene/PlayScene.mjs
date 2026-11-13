// 游戏场景（对应旧 PlayScene）
// 真实等距地图 + 实体（sprite）+ 鼠标按住移动（走/跑）+ HUD + 聊天。

import { Scene } from './Scene.mjs';
import { SelectCharScene } from '../ui/SelectCharScene.mjs';
import { MapActor, FLOOR_FALLBACK } from '../map/MapActor.mjs';
import { ActorSprite } from '../actor/ActorSprite.mjs';
import { getLib } from '../image/Images.mjs';
import { SCREEN_WIDTH, SCREEN_HEIGHT, MAPSURFACEHEIGHT } from '../core/config.mjs';
import { perf } from '../core/perf.mjs';
import { cancelWarm } from '../core/boot.mjs';
import { cancelPreload } from '../core/mapPreload.mjs';
import { scheduler } from '../gfx/scheduler.mjs';
import { toLocal } from '../core/viewport.mjs';
import { sound, itemClickSound, itemUseSound, SFX } from '../audio/SoundManager.mjs';

// 8 方向映射（与服务器 Direction 一致）
const DIRMAP = {
    '0,-1': 0, '1,-1': 1, '1,0': 2, '1,1': 3,
    '0,1': 4, '-1,1': 5, '-1,0': 6, '-1,-1': 7,
};

// 移动动作间隔（ms/步）：走慢跑快，动作之间必须有间隔
// 对齐动画周期：walk 动画 6 帧×90ms=540ms；run 动画 6 帧×120ms=720ms 覆盖 2 格 → 360ms/格
const WALK_INTERVAL = 540;   // 走（左键）
const RUN_INTERVAL = 360;    // 跑（右键）
const RUSH_INTERVAL = 360;   // 冲撞（野蛮冲撞）：对齐原版 ActRushLeft/Right 3 帧×120ms=360ms/格

// 玩家攻击动画时长（HA.hit：6 帧 × 85ms = 510ms），动作完成才允许下一次攻击
const ATTACK_ANIM_MS = 510;
// 玩家施法动画时长（HA.spell：6 帧 × 60ms = 360ms，原版 ActSpell），与攻击动作区分
const SPELL_ANIM_MS = 360;
// 挖肉蹲下时长（HA.ActSitdown：2 帧 × 300ms = 600ms，原版 Actor.pas:89）
const SITDOWN_ANIM_MS = 600;
// 命中帧延迟：原版攻击动作（HA.hit，6 帧 × 85ms）在第 2 帧（约 170ms，武器接触点）才结算命中，
// 故目标受击/伤害飘字/命中特效延后到此时触发，避免“先爆后砍”。
const HIT_FRAME_MS = 170;

// 怪物/NPC 移动过渡时长（ms）：对齐多数怪物 walk 动画 6 帧×120ms=720ms
const MONSTER_MOVE_DUR = 720;

// 聊天板可见行数（原版 DrawScrn.pas VIEWCHATLINE = 9，行高 12px）
const VIEWCHATLINE = 9;
const CHAT_LINE_HEIGHT = 12;

// 装备槽名（对应 MessageIds U_DRESS..U_CHARM）
const SLOT_NAMES = ['衣服', '武器', '右手', '项链', '头盔', '手镯L', '手镯R', '戒指L', '戒指R', '护身符', '腰带', '靴子', '宝石'];

// 装备槽位（相对 F9 状态窗口坐标，对齐原版 FState.pas DSWxxx）[slot, x, y, w, h]
const EQUIP_SLOTS = [
    [0,   96, 122, 53, 112],   // 衣服 DSWDress
    [1,   47,  80, 47,  87],   // 武器 DSWWeapon
    [2,  168, 125, 34,  31],   // 右手(蜡烛) DSWLight
    [3,  168,  87, 34,  31],   // 项链 DSWNecklace
    [4,  115,  93, 18,  18],   // 头盔 DSWHelmet
    [5,  168, 176, 34,  31],   // 手镯L DSWArmRingL
    [6,   42, 176, 34,  31],   // 手镯R DSWArmRingR
    [7,  168, 215, 34,  31],   // 戒指L DSWRingL
    [8,   42, 215, 34,  31],   // 戒指R DSWRingR
    [9,   42, 254, 34,  31],   // 护身符 DSWBujuk
    [10,  84, 254, 34,  31],   // 腰带 DSWBelt
    [11, 126, 254, 34,  31],   // 靴子 DSWBoots
    [12, 168, 254, 34,  31],   // 宝石 DSWCharm
];

// StdMode → 自动穿戴槽位（原版 ClFunc.pas GetTakeOnPosition：戒指先占左、手镯/手套先占右）
const TAKEON_POS = {
    5: 1, 6: 1,        // 武器
    10: 0, 11: 0,      // 男/女衣服
    15: 4,             // 头盔
    19: 3, 20: 3, 21: 3,   // 项链
    22: 7, 23: 7,      // 戒指
    24: 6, 26: 6,      // 手镯/手套
    30: 2,             // 蜡烛/火把/勋章
    25: 9,             // 护身符/符
    52: 11, 53: 12, 54: 10,   // 靴子/宝石/腰带
};

// 槽位能否收纳该 StdMode（原版 M2Share.pas IsTakeOnAvailable；头盔槽只收 15，左手腕额外收 25）
function slotAccepts(slot, stdMode) {
    switch (slot) {
        case 0: return stdMode === 10 || stdMode === 11;
        case 1: return stdMode === 5 || stdMode === 6;
        case 2: return stdMode === 30;
        case 3: return stdMode === 19 || stdMode === 20 || stdMode === 21;
        case 4: return stdMode === 15;
        case 5: return stdMode === 24 || stdMode === 25 || stdMode === 26;
        case 6: return stdMode === 24 || stdMode === 26;
        case 7:
        case 8: return stdMode === 22 || stdMode === 23;
        case 9: return stdMode === 25;
        case 10: return stdMode === 54;
        case 11: return stdMode === 52;
        case 12: return stdMode === 53;
        default: return false;
    }
}

// 属性数值行（原版 DStateWin StatePage 1：x=112，y 每 20px）[字段, y]
const ATTR_ROWS = [
    ['ac', 99], ['mac', 119], ['dc', 139], ['mc', 159], ['sc', 179],
    ['hp', 199], ['mp', 219],
];

// 战士主动技能（EffectType=0 但为「开关/蓄力/冲撞」主动技能）：
// 半月(25) = 攻击模式开关，烈火(26) = 蓄力，野蛮(27) = 冲撞
const SWORD_SKILLS = new Set([25, 26, 27]);

// 纯被动技能（无法主动施放）：基本剑术(3)/精神力战法(4)/攻杀(7)/刺杀(12)
// 刺杀默认生效（服务端登录自动开启 longHitOn，见 doEnter），不占快捷键位
const PASSIVE_MAGICS = new Set([3, 4, 7, 12]);

// ---- 技能特效帧（magic/magic2 库，对齐原版 EffectBase + EXPLOSIONBASE 简化映射）----
// 每项 = 起始帧 + 连续帧数，动画帧间隔约 80ms（原版 NextFrameTime）。
// 帧号对齐原版 EffectBase[Effect-1] 表：命中爆炸 = EffectBase + EXPLOSIONBASE(170)，
// 飞行 = EffectBase + FLYBASE(10)，部分技能有特例（爆裂/冰咆哮/圣言）。
const FX_FRAMES = {
    // 命中爆炸（EffectBase + 170）
    boomFire:     { lib: 'magic',  base: 170,  frames: 10, ft: 80 },   // 火球（Effect=1→0+170）
    boomBigFire:  { lib: 'magic',  base: 570,  frames: 10, ft: 80 },   // 大火球（Effect=3→400+170）
    boomSoulfu:   { lib: 'magic',  base: 1360, frames: 10, ft: 50 },   // 灵魂火符命中爆炸（原版 TExploBujaukEffect MagExplosionBase=1360, ExplosionFrame=10）
    boomBflame:   { lib: 'magic',  base: 1660, frames: 20, ft: 80 },   // 爆裂火焰（原版 MagExplosionBase=1660, ExplosionFrame=20）
    boomIce:      { lib: 'magic',  base: 3850, frames: 20, ft: 80 },   // 冰咆哮（原版 MagExplosionBase=3850, ExplosionFrame=20）
    boomHoly:     { lib: 'magic',  base: 3930, frames: 16, ft: 80 },   // 圣言术（原版 MagExplosionBase=3930, ExplosionFrame=16）
    diyu:         { lib: 'magic',  base: 930,  frames: 6,  ft: 50 },   // 地狱火火焰团（原版 mtFireGun：930-935，火焰沿施法者→目标喷射）
    jgdy:         { lib: 'magic',  base: 970,  frames: 6,  ft: 50, dirStride: 10 },  // 疾光电影闪电射线（原版 mtLightingThunder：970+Dir16*10）
    thunder:      { lib: 'magic2', base: 10,   frames: 6,  ft: 50 },   // 雷电（原版 mtThunder：EffectBase=10，帧10-13为贯穿屏幕闪电，帧14尾焰）
    thunderElec:  { lib: 'magic',  base: 1680, frames: 10, ft: 80 },   // 地狱雷光（原版 EffectBase[21]=1680，10 帧白色闪电扩散）
    fireRing:     { lib: 'magic',  base: 900,  frames: 6,  ft: 80 },   // 抗拒火环（原版 EffectBase[5]=900，6 帧金色光环）
    // 传送两段特效（原版 Client\Actor.pas:1635-1658，ImgLib=g_WMagicImages=Magic.wil，
    // 10 帧 × NextFrameTime 30ms = 300ms；variant=1 是瞬息移动技能专用 2 号特效）：
    // Hide = TScrollHideEffect 固定在旧坐标格（250-259 / 1590-1599）+ 音 109；
    // Show = TCharEffect 跟随角色（260-269 / 1600-1609）+ 音 110。
    spaceMoveHide:  { lib: 'magic', base: 250,  frames: 10, ft: 30 },  // SM_SPACEMOVE_HIDE（传送消失）
    spaceMoveShow:  { lib: 'magic', base: 260,  frames: 10, ft: 30 },  // SM_SPACEMOVE_SHOW（传送现身，卷轴/回城/GM/NPC）
    spaceMoveHide2: { lib: 'magic', base: 1590, frames: 10, ft: 30 },  // SM_SPACEMOVE_HIDE2（瞬息移动消失）
    spaceMove:      { lib: 'magic',  base: 1600, frames: 10, ft: 30 },  // SM_SPACEMOVE_SHOW2（瞬息移动现身）
    // 飞行弹道（EffectBase + FLYBASE 10）
    fireball:     { lib: 'magic',  base: 10,   frames: 6,  ft: 80 },   // 火球飞行（0+10）
    bigfireball:  { lib: 'magic',  base: 410,  frames: 6,  ft: 80 },   // 大火球飞行（400+10）
    soulfu:       { lib: 'magic',  base: 1160, frames: 3,  ft: 50, dirStride: 10 },  // 灵魂火符飞行（原版 TExploBujaukEffect base=1160，3 帧×50ms，Dir16*10 方向）
    // 远程怪飞矢（射箭/投斧）：mon5 库箭矢帧 = 2609 + Dir16*10（16 方向，原版 magiceff.pas ARCHERBASE 段，ImgLib=WMon5）
    flyAxe:       { lib: 'mon5',   base: 2609, frames: 1,  dirStride: 10 },
    // 治疗/持续
    heal:         { lib: 'magic',  base: 370,  frames: 10, ft: 80 },   // 治愈术（EffectType=2=mtExplosion，MagExplosionBase=EffectBase[1]200+170=370，10 帧）
    healGroup:    { lib: 'magic',  base: 1800, frames: 10, ft: 80 },   // 群体治疗术（mtExplosion magnumb=27 → MagExplosionBase=1800，10 帧）
    temptation:   { lib: 'magic',  base: 1570, frames: 10, ft: 80 },   // 诱惑之光（原版 MagExplosionBase=1570，10 帧目标头顶特效）
    firewall:     { lib: 'magic',  base: 1630, frames: 6,  ft: 40 },   // 火墙火焰（原版 clEvent.pas FIREBURNBASE=1630，6 帧循环）
    shield:       { lib: 'magic',  base: 3880, frames: 12, ft: 100 },  // 魔法盾泡泡（Effect=29→3880）
    ghostShield:  { lib: 'magic',  base: 1320, frames: 16, ft: 50 },   // 幽灵盾光环（Effect=11→TBujaukGroundEffect 1160+16*10，16 帧）
    holyArmor:    { lib: 'magic',  base: 1340, frames: 16, ft: 50 },   // 神圣战甲光环（Effect=12→1160+18*10，16 帧）
    hideSmoke:    { lib: 'magic',  base: 1540, frames: 10, ft: 80 },   // 隐身烟雾（集体隐身 Effect=17→TExploBujaukEffect MagExplosionBase=1540，10 帧）
    holyCurtain:  { lib: 'magic',  base: 1380, frames: 20, ft: 80 },   // 困魔咒（原版 mtKyulKai 注释 TKyulKai.Create(1380)，20 帧）
    // 神兽吐火火焰（原版 AxeMon.pas TWarriorElfMonster.RunFrameAction：攻击第 5 帧触发
    // TMapEffect.Create(WARRIORELFFIREBASE + 10*Dir + 1, 5帧, 100ms)——帧 = 820+10*Dir+1+rel，固定在神兽位置）
    elfFire:      { lib: 'mon18',  base: 820,  frames: 5,  ft: 100 },
};
// 战士打击光效（原版 Actor.pas 的 BoHitEffect：绘制在攻击者身上，帧 = HitEffectBase[n-1] + Dir*10 + 相对帧）
// HitEffectNumber → HitEffectBase（magiceff.pas）：1攻杀=800 2刺杀=1410 3半月=1700 4烈火=3480 6狂风=40
const SWORD_HIT_FX = {
    7:  { base: 800,  dirStride: 10 },   // 攻杀剑术（SM_POWERHIT → HitEffectNumber 1）
    12: { base: 1410, dirStride: 10 },   // 刺杀剑术（SM_LONGHIT → HitEffectNumber 2）
    25: { base: 1700, dirStride: 10 },   // 半月弯刀（SM_WIDEHIT → HitEffectNumber 3）
    26: { base: 3480, dirStride: 10 },   // 烈火剑法（SM_FIREHIT → HitEffectNumber 4）
};

// 每个技能 → 特效（fly=飞行弹道, hit=命中爆炸/雷击, ground=地面持续）
const MAGIC_VFX = {
    1:  { fly: 'fireball',     hit: 'boomFire' },      // 火球术
    5:  { fly: 'bigfireball',  hit: 'boomBigFire' },   // 大火球
    9:  { firegun: 'diyu' },                           // 地狱火（火焰从角色向前喷射）
    10: { beam: 'jgdy' },                              // 疾光电影（闪电从角色沿鼠标方向延伸）
    11: { hit: 'thunder' },                            // 雷电术（雷击）
    13: { fly: 'soulfu',       hit: 'boomSoulfu' },    // 灵魂火符
    14: { fly: 'soulfu',       hit: 'ghostShield' },   // 幽灵盾（符纸飞向目标展开魔御光环）
    15: { fly: 'soulfu',       hit: 'holyArmor' },     // 神圣战甲术（符纸飞向目标展开防御光环）
    18: {},                                            // 隐身术（原版 EffectType=4=mtFireWind 无特效，对齐原版）
    19: { fly: 'soulfu',       hit: 'hideSmoke' },     // 集体隐身术（符纸飞向目标点展开范围烟雾）
    16: { hit: 'holyCurtain' },                        // 困魔咒（目标点帷幕定身效果）
    22: { ground: 'firewall' },                        // 火墙
    23: { hit: 'boomBflame' },                         // 爆裂火焰
    8:  { self: 'fireRing' },                          // 抗拒火环（自身金色光环）
    24: { self: 'thunderElec' },                       // 地狱雷光（自身白色闪电扩散）
    32: { hit: 'boomHoly' },                           // 圣言术
    33: { hit: 'boomIce' },                            // 冰咆哮
    2:  { hit: 'heal' },                               // 治愈术
    29: { hit: 'healGroup' },                          // 群体治疗术（原版 mtExplosion magnumb=27 → MagExplosionBase=1800）
    6:  {},                                            // 施毒术（原版 nofire，无命中特效，效果=目标中毒变色）
    20: { hit: 'temptation' },                         // 诱惑之光（目标头顶特效）
};

export class PlayScene extends Scene {
    constructor(manager, snapshot) {
        super(manager);
        this.snapshot = snapshot;       // enterGame 快照
        this.self = null;
        this.entities = new Map();      // id -> 实体快照
        this.sprites = new Map();       // id -> ActorSprite
        this.mapActor = new MapActor(SCREEN_WIDTH, MAPSURFACEHEIGHT);
        // 免蜡：原版 MShare.pas 的 g_boForceNotViewFog（True = 强制不查看雾）。
        // 客户端偏好，和音效开关一样持久化；系统菜单里可随时切换。
        this._noFog = (() => {
            try { return localStorage.getItem('jootm2.noFog') === '1'; } catch { return false; }
        })();
        this.mapActor.forceNotViewFog = this._noFog;
        this._holding = false;       // 鼠标按住状态（按住持续移动，松开停止）
        this._mouseRun = false;      // 是否跑（右键）
        this._mouseDir = -1;         // 当前移动方向（鼠标相对人物，0-7）
        this._lastMoveAt = 0;        // 上次发送移动的时间戳（发送节流，对所有来源共用）
        this._pendingStep = false;   // 单击待发的一步（受节流约束，快速连点不会堆积成瞬移）
        this._lastHitTime = 0;       // 上次攻击时间戳（CanNextHit 攻速控制）
        this._dirAttackHolding = false;  // shift+左键 方向攻击持续状态（按住持续挥刀，松开停止）
        this._dirAttackDir = -1;         // 方向攻击方向（鼠标相对人物，0-7）
        this._shiftDown = false;         // Shift 键按住状态
        this.chaseTargetId = null;       // 锁定目标（原版 TargetCret：相邻砍、远了走过去）
        this.magicPage = 0;          // 技能页内部分页页码（每页 5 个技能）
        this.groundItems = new Map();// id -> 地面物品
        this.castTarget = null;      // 施法目标（怪物 id）
        this.magicTarget = null;     // 鼠标悬停的施法目标（原版 FocusCret：怪物/玩家）
        this._lastMouse = { mx: 0, my: 0 };   // 鼠标在 canvas 内的最近位置（施法朝此处）
        this.effects = [];           // 技能特效（飞行/命中/地面火墙）
        this.damageNumbers = [];     // 伤害飘字
        this._selfLevel = null;      // 升级特效检测基线（stats.level 变化时触发）
        this._fxCache = new Map();   // 特效帧纹理缓存 'lib/index' -> canvas
        this._zTop = 100;            // 窗口 z-index 计数器（点击前置递增）
        this._dragCleaners = [];     // 窗口拖动监听清理函数
        this._canvas = document.getElementById('screen');
        this.chatLines = [];
        this._chatInput = null;
    }

    async onEnter() {
        const { map, player, nearby, groundItems } = this.snapshot;
        this.self = player;
        this._selfLevel = player && player.level != null ? player.level : null;
        // 正常登录服务端必然已复活（HP<=0 → 出生点 HP=14），这里按快照兜底，
        // 保证换角色/重进不会残留上一局的黑白屏。
        this._selfDead = !!(player && player.dead);
        this.mapActor.selfDead = this._selfDead;
        this._mapLoadError = null;
        this.entities.clear();
        this.entities.set(player.id, player);
        this._registerSound(player);
        for (const e of nearby || []) { this.entities.set(e.id, e); this._registerSound(e); }
        // 脚步音按脚下地砖分类（原版 Actor.pas:2169-2257）：SoundManager 每次移动回调取一次。
        sound.terrainAt = (x, y) => this._terrainAt(x, y);
        this.groundItems.clear();
        for (const g of groundItems || []) this.groundItems.set(g.id, g);

        // HUD 先搭起来：其 DOM 图片与地图预取并行加载，地图一就绪就是完整画面，
        // 不会「地图先出现、HUD 图标再一块块冒出来」。
        this._buildHud();

        this._mapLoadingText = '正在加载地图…';
        // 进游戏先掐掉后台预热/预载：它们和首屏地面抢同一条带宽，慢网下会把
        // 「地面纹理」压到十几秒都下不来（实测 60KB/s 时 21 秒解出 0 张）。
        try { cancelWarm(); cancelPreload(); } catch { /* 老版本兼容 */ }
        // 已在飞的请求也要掐掉：后台预载的「取消」只保证不再领新任务，
        // 登录阶段那 10 张 800×600 开门帧仍在占着全部 6 条连接。
        try { scheduler.abortPending('enter-game'); } catch { /* 忽略 */ }
        try {
            await this.mapActor.enter(map.name, player.x || 0, player.y || 0,
                (done, total) => { this._mapLoadingText = `正在加载地图资源 ${done}/${total}…`; });
        } catch (e) {
            console.error('[play]', e);
            // 地图加载失败（如资源清单里没有该图）：留着错误文案，别永远「正在加载地图…」
            this._mapLoadError = String((e && e.message) || e);
        } finally {
            this._mapLoadingText = null;
        }

        this._miniMapIndex = this.snapshot.miniMapIndex;
        const sm = (this.snapshot && this.snapshot.map) || {};
        this._loadMiniMapTexture(this._miniMapIndex, sm.width, sm.height);   // 小地图底图（mmap.wil）
        this._applyBrightness(this.snapshot);   // 昼夜图标 + 视野暗度（DarkLevel）
        this._keyDown = (e) => this._onKeyDown(e);
        this._keyUp = (e) => this._onKeyUp(e);
        document.addEventListener('keydown', this._keyDown);
        document.addEventListener('keyup', this._keyUp);
        this._mdHandler = (e) => this._onMouseDown(e);
        this._muHandler = () => {
            this._holding = false;          // 松开停止移动
            this._dirAttackHolding = false; // 松开停止方向攻击
        };
        this._mmHandler = (e) => this._onMouseMove(e);   // 按住时实时更新方向
        this._canvas.addEventListener('mousedown', this._mdHandler);
        document.addEventListener('mouseup', this._muHandler);   // 全局松开（防止移出 canvas 后仍按住）
        document.addEventListener('mousemove', this._mmHandler);
    }

    // ---- HUD（对齐原版 TFrmDlg：DBottom 底板 Prguse[1] 800×251 @ y=349）----
    // 原版布局（屏幕坐标，DBottom.Top = SCREENHEIGHT - 251 = 349）：
    //   血球/魔法球 (40, Top+91) Prguse[4]（武士 lv<26 用 [5] 容器 + [6] 液体 @ (38, Top+90)）
    //   等级位图数字 (660, 496)、经验条 (666, 527)、负重条 (666, 560) —— 均用 Prguse[7]/[30..39]
    //   聊天记录 (208, 470) 374×108（9 行 × 12px）；聊天输入框 (208, 581) 387×12
    //   地图名 (8, 580)
    async _buildHud() {
        const gui = this.manager.winManager.container;
        gui.innerHTML = '';

        const HUD_Y = 600 - 251;   // 349

        // HUD 根容器：覆盖整个 800×600，子元素绝对定位；默认不拦截鼠标
        const root = document.createElement('div');
        root.style.cssText = 'position:absolute;left:0;top:0;width:800px;height:600px;pointer-events:none;'
            + 'font-family:SimSun,serif;color:#fff;';
        this.hudEl = root;
        gui.appendChild(root);

        // 底板 Prguse[1]（800×251）
        this._libUrl('prguse', 1).then((url) => {
            if (!url || !root.isConnected) return;
            const bg = document.createElement('img');
            bg.src = url; bg.draggable = false;
            bg.style.cssText = `position:absolute;left:0;top:${HUD_Y}px;width:800px;height:251px;`;
            root.insertBefore(bg, root.firstChild);
        });

        // 血球/魔法球（canvas：按 HP/MP 比例纵向裁切，液面从下往上）
        const warriorBall = this.self && this.self.job === 0 && this.self.level < 26;
        this.vitalsCanvas = document.createElement('canvas');
        this.vitalsCanvas.width = warriorBall ? 96 : 92;
        this.vitalsCanvas.height = warriorBall ? 92 : 90;
        this.vitalsCanvas.style.cssText = 'position:absolute;'
            + `left:${warriorBall ? 38 : 40}px;top:${HUD_Y + (warriorBall ? 90 : 91)}px;`;
        root.appendChild(this.vitalsCanvas);

        // 等级（位图数字 Prguse[30..39]，8px/位，首位前空 8px；锚点 (660, 496)）
        this.levelCanvas = document.createElement('canvas');
        this.levelCanvas.width = 32;
        this.levelCanvas.height = 14;
        this.levelCanvas.style.cssText = `position:absolute;left:660px;top:${HUD_Y + 147}px;`;
        root.appendChild(this.levelCanvas);

        // 经验条 / 负重条（Prguse[7] 76×13，右端裁宽度）
        this.expBarEl = this._mkHudBar(666, HUD_Y + 178);
        this.loadBarEl = this._mkHudBar(666, HUD_Y + 211);

        // 地图名 + 坐标（屏幕底部）
        this.mapNameEl = document.createElement('div');
        this.mapNameEl.style.cssText = 'position:absolute;left:8px;top:576px;font-size:12px;color:#fff;'
            + 'text-shadow:1px 1px 0 #000;';
        root.appendChild(this.mapNameEl);

        // 系统消息飘字（原版 DrawScrn.pas AddSysMsg：屏幕 (30,40) 起、行距 16、绿色、3 秒、最多 10 条）
        this.sysMsgs = [];
        this.sysMsgEl = document.createElement('div');
        this.sysMsgEl.style.cssText = 'position:absolute;left:30px;top:40px;width:520px;pointer-events:none;'
            + 'font-size:12px;';
        root.appendChild(this.sysMsgEl);

        // 昼夜时段图标（原版 FState.pas:3236-3245 —— Prguse[15]早/[12]白/[13]傍/[14]晚 @ (748, Top+79)）
        // 注意：本服不需要蜡烛照明，因此只画图标，不做任何黑暗/视野渲染
        this.dayIconEl = document.createElement('img');
        this.dayIconEl.draggable = false;
        this.dayIconEl.style.cssText = `position:absolute;left:748px;top:${HUD_Y + 79}px;display:none;`
            + 'image-rendering:pixelated;';
        root.appendChild(this.dayIconEl);
        this._dayBright = -1;
        this._underground = false;   // 地下/矿区：视野恒暗（服务端 map_snapshot 下发）

        // 小地图（原版 PlayScn.pas DrawMiniMap：右上 (SCREENWIDTH-120, 0)、120×120、1 格 = 1 像素）
        this.miniMapCanvas = document.createElement('canvas');
        this.miniMapCanvas.width = 120;
        this.miniMapCanvas.height = 120;
        this.miniMapCanvas.style.cssText = 'position:absolute;left:680px;top:0;width:120px;height:120px;'
            + 'display:none;pointer-events:none;image-rendering:pixelated;';
        root.appendChild(this.miniMapCanvas);
        this._miniMapTex = null;
        this._miniMapScale = 1.5;   // mmap 底图 1.5 像素/格；兜底自生成图 1 像素/格
        this._miniMapStyle = 0;     // 0=关、1=半透明、2=不透明（原版 ViewMiniMapStyle）
        // 大地图 / 系统菜单（原版 TAB 打开地图、切换大小地图、关闭；ESC 关窗口/开系统菜单）
        this._bigMapOpen = false;
        this._bigMapPanel = null;
        this._bigMapCanvas = null;
        this._bigMapTitle = null;
        this._sysMenu = null;

        // 聊天记录区（208,470，374×108 = 9 行 × 12px）
        // 原版没有滚动条：固定 9 行窗口 + ChatBoardTop 偏移，↑↓/PgUp/PgDn 滚动（ClMain.pas:1902-1925）
        this.chatLog = document.createElement('div');
        // 背景留空：露出底板 Prguse[1] 自带的白色聊天区（原版就是在白色区域上逐行刷背景色/文字色）
        this.chatLog.style.cssText = 'position:absolute;left:208px;top:470px;width:374px;height:108px;'
            + 'overflow:hidden;pointer-events:auto;font-size:12px;line-height:12px;color:#008000;';
        this.chatLog.addEventListener('wheel', (e) => {
            e.preventDefault();
            e.stopPropagation();
            this._scrollChat(e.deltaY > 0 ? 3 : -3);
        }, { passive: false });
        root.appendChild(this.chatLog);
        this.chatLines = [];      // {text, color}
        this.chatBoardTop = 0;    // 原版 DScreen.ChatBoardTop
        this._renderChat();       // 先渲染 9 个空行，保证固定行高窗口

        // 聊天输入框（208,581，387×12；原版 clSilver 底、默认隐藏，回车/斜杠唤出）
        this.chatInput = document.createElement('input');
        this.chatInput.type = 'text';
        this.chatInput.maxLength = 70;
        this.chatInput.style.cssText = 'position:absolute;left:208px;top:580px;width:387px;height:14px;'
            + 'box-sizing:border-box;background:#c0c0c0;color:#000;border:none;outline:none;'
            + 'font-family:SimSun,serif;font-size:12px;padding:0 2px;pointer-events:auto;display:none;';
        this.chatInput.addEventListener('keydown', (e) => {
            e.stopPropagation();
            if (e.key === 'Enter') {
                const t = this.chatInput.value.trim();
                if (t) this.conn.send('say', { text: t });
                this.chatInput.value = '';
                this.chatInput.blur();
                this.chatInput.style.display = 'none';
            } else if (e.key === 'Escape') {
                this.chatInput.value = '';
                this.chatInput.blur();
                this.chatInput.style.display = 'none';
            }
        });
        root.appendChild(this.chatInput);

        // 右上 4 个功能按钮（按下态 Prguse[8..11]，抬起态烘焙在底板上；坐标相对 DBottom）
        const topBtns = [
            { x: 643, y: 61, icon: 8, tip: '装备', action: () => { this.statePage = 0; this._toggleStateWin(); } },
            { x: 682, y: 41, icon: 9, tip: '背包', action: () => this._toggleBag() },
            { x: 722, y: 21, icon: 10, tip: '技能', action: () => this._openSkillSetup() },
            { x: 764, y: 11, icon: 11, tip: '声音', action: () => this._toggleSound() },
        ];
        for (const b of topBtns) {
            this._addHudButton(root, b.x, HUD_Y + b.y, 24, 24, b.icon, b.tip, b.action);
        }

        // 底板中部/右下按钮（Prguse 128..142，28×13）
        const botBtns = [
            { x: 219, icon: 130, tip: '小地图(V)', action: () => this._toggleMiniMap() },
            { x: 249, icon: 132, tip: '交易', action: () => this._log('交易功能未开放。') },
            { x: 279, icon: 134, tip: '行会', action: () => this._log('行会功能未开放。') },
            { x: 309, icon: 128, tip: '编组', action: () => this._log('编组功能未开放。') },
            // 属性加点：原版只有「有可分配点数」时才显示（且会闪烁），当前没有属性点系统 → 默认隐藏
            { x: 339, icon: 140, tip: '属性加点', hidden: true, action: () => this._log('暂无可分配点数。') },
            { x: 530, icon: 136, tip: '退出角色', action: () => this._backToSelect() },
            { x: 560, icon: 138, tip: '退出游戏', action: () => this._logout() },
        ];
        for (const b of botBtns) {
            const btn = this._addHudButton(root, b.x, HUD_Y + 104, 28, 13, b.icon, b.tip, b.action, true);
            if (b.hidden) {
                btn.style.display = 'none';
            }
        }

        // 药水快捷栏 6 格（原版 DBelt1..6：Left 285/328/371/415/459/503，Top 59，32×29；
        // 图标 WBagItem[Looks] 居中、右下角位图数字 1..6）
        this.quickCellEls = [];
        const quickXs = [285, 328, 371, 415, 459, 503];
        for (let i = 0; i < 6; i++) {
            const cell = document.createElement('div');
            cell.style.cssText = `position:absolute;left:${quickXs[i]}px;top:${HUD_Y + 59}px;width:32px;height:29px;`
                + 'cursor:pointer;pointer-events:auto;overflow:hidden;';
            cell.dataset.quick = String(i);
            cell.dataset.sfx = '0';   // 空格点击原版无声，_refreshQuickSlots 会按实际物品设置
            cell.title = `快捷栏 ${i + 1}`;
            cell.addEventListener('click', () => this._onQuickSlotClick(i));
            // 双击使用（原版 DBelt1DblClick → EatItem）
            cell.addEventListener('dblclick', () => this._useQuickSlot(i));
            root.appendChild(cell);
            const icon = document.createElement('img');
            icon.draggable = false;
            icon.style.cssText = 'position:absolute;left:0;top:0;display:none;';
            cell.appendChild(icon);
            // 格号（原版 PomiTextOut(Left+13, Top+19)，位图数字 Prguse[31..36]）
            const num = document.createElement('canvas');
            num.width = 8;
            num.height = 12;
            num.style.cssText = 'position:absolute;left:13px;top:16px;';
            cell.appendChild(num);
            this._libUrl('prguse', 30 + i + 1).then((url) => {
                if (!url) return;
                const im = new Image();
                im.onload = () => num.getContext('2d').drawImage(im, 0, 0);
                im.src = url;
            });
            this.quickCellEls.push({ cell, icon });
        }

        this._hudKey = null;
        this._hudTexReady = false;
        // 预取 HUD 位图（血球 / 等级数字）。之前这两个 canvas 是 async 函数，
        // 每帧「clearRect → await 取图 → 重画」，纹理未就绪时会留下空白帧（闪）。
        // 现在统一预取，之后全部同步绘制，清空与重画之间不可能被浏览器合成。
        const hudLib = getLib('prguse');
        if (hudLib) {
            const wanted = [4, 5, 6, ...Array.from({ length: 10 }, (_, i) => 30 + i)];
            Promise.all(wanted.map(i => hudLib.getTexture(i).catch(() => null)))
                .then(() => {
                    if (!this.vitalsCanvas || !this.vitalsCanvas.isConnected) return;
                    this._hudTexReady = true;
                    this._hudKey = null;
                    this._renderHud();
                });
        } else {
            this._hudTexReady = true;   // 无图库：按空纹理走，不阻塞 HUD
        }
        this._refreshQuickSlots();
        this._renderHud();

        // 背包窗口（F9）/ 状态窗口（F10）—— 原版 DItemBag / DStateWin
        this._buildStateWin();
        this._buildBag();
        // 进游戏给一次快捷键提示（原版靠玩家口口相传，这里直接列出，避免"不知道怎么打开"）
        this._log('[提示] Tab 大地图 / V 小地图 / Esc 系统菜单 / F9 背包 / F10 状态 / F11 技能', '#e8d9b0');
    }

    /**
     * HUD 按钮。
     * - alwaysShow=false（右上 4 个 8..11）：原版只在按下时画 FaceIndex，抬起态烘焙在底板上；
     * - alwaysShow=true（底部一排 128..142）：原版 DBotGroupDirectPaint 抬起画 N、按下画 N+1。
     */
    _addHudButton(root, x, y, w, h, iconIdx, tip, action, alwaysShow) {
        const btn = document.createElement('div');
        btn.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;`
            + 'cursor:pointer;pointer-events:auto;';
        btn.title = tip;
        root.appendChild(btn);
        const mkImg = (idx, shown) => {
            const img = document.createElement('img');
            img.draggable = false;
            img.style.cssText = 'position:absolute;left:0;top:0;width:100%;height:100%;'
                + `display:${shown ? 'block' : 'none'};`;
            btn.appendChild(img);
            this._libUrl('prguse', idx).then((url) => { if (url && img.isConnected) img.src = url; });
            return img;
        };
        if (alwaysShow) {
            const upImg = mkImg(iconIdx, true);       // 抬起态 N
            const downImg = mkImg(iconIdx + 1, false); // 按下态 N+1
            btn.addEventListener('mousedown', () => { upImg.style.display = 'none'; downImg.style.display = 'block'; });
            btn.addEventListener('mouseup', () => { upImg.style.display = 'block'; downImg.style.display = 'none'; });
            btn.addEventListener('mouseleave', () => { upImg.style.display = 'block'; downImg.style.display = 'none'; });
        } else {
            const downImg = mkImg(iconIdx, false);
            btn.addEventListener('mousedown', () => { downImg.style.display = 'block'; });
            btn.addEventListener('mouseup', () => { downImg.style.display = 'none'; });
            btn.addEventListener('mouseleave', () => { downImg.style.display = 'none'; });
        }
        btn.addEventListener('click', () => action());
        return btn;
    }

    /** 音效开关（原版 TogglePlaySoundEffect）→ 交给 SoundManager（含 BGM 与持久化）。 */
    _toggleSound() {
        const on = sound.toggle();
        this._log(on ? '[系统] 音效已开启。' : '[系统] 音效已关闭。');
    }

    /**
     * 免蜡开关：原版 MShare.pas ``g_boForceNotViewFog``，
     * ``PlayScn.pas:1471`` 的 ``if g_boViewFog and not g_boForceNotViewFog``。
     * 打开后完全跳过 ApplyLightMap + DrawFog，画面恒亮（老玩家俗称「免蜡」）。
     */
    _toggleNoFog() {
        this._noFog = !this._noFog;
        try { localStorage.setItem('jootm2.noFog', this._noFog ? '1' : '0'); } catch { /* 隐私模式 */ }
        if (this.mapActor) this.mapActor.forceNotViewFog = this._noFog;
        if (this._syncFogBtn) this._syncFogBtn();
        this._log(this._noFog
            ? '[系统] 免蜡已开启：视野全亮，不再受黑暗/火把限制。'
            : '[系统] 免蜡已关闭：黑暗区域需要蜡烛、火把或火墙照明。');
    }

    /** 返回人物选择（原版 Alt-X / 退出角色）：保持登录态与 WS 连接。 */
    _backToSelect() {
        this.conn.send('backToSelect');
    }

    /** 退出游戏：浏览器里没有「关闭客户端」，统一回人物选择，避免断连后卡在场景里。 */
    _logout() {
        this._backToSelect();
    }

    /** 经验/负重条：Prguse[7]（76×13），靠裁宽度表现比例。 */
    _mkHudBar(x, y) {
        const el = document.createElement('div');
        el.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:0;height:13px;`
            + 'background-repeat:no-repeat;background-position:left center;background-size:76px 13px;';
        this._libUrl('prguse', 7).then((url) => {
            if (url && el.isConnected) el.style.backgroundImage = `url(${url})`;
        });
        this.hudEl.appendChild(el);
        return el;
    }

    /** 刷新药水快捷栏：按 makeIndex 从背包取 looks 画居中图标（原版 DBelt1DirectPaint）。 */
    _refreshQuickSlots() {
        if (!this.quickCellEls || !this.self) return;
        const p = this.self;
        const qs = p.quickSlots || [];
        for (let i = 0; i < this.quickCellEls.length; i++) {
            const { cell, icon } = this.quickCellEls[i];
            const mi = qs[i] || 0;
            const it = mi > 0 ? (p.items || []).find(x => x.makeIndex === mi) : null;
            if (!it) {
                icon.style.display = 'none';
                icon.removeAttribute('src');
                cell.title = `快捷栏 ${i + 1}（空）`;
                cell.dataset.sfx = '0';       // 原版空格点击不播声
                continue;
            }
            cell.title = `${it.name}（快捷栏 ${i + 1}）`;
            // 原版 DBelt1Click → ItemClickSound(StdMode)：0/31→108、武器→111…（不是固定药品音）
            cell.dataset.sfx = String(itemClickSound(it.stdMode));
            // 药水格图标用 Items.wil（原版 WBagItem），不是 stateitem（那是装备/状态窗口用的）
            this._libUrl('items', it.looks).then((url) => {
                if (!url || !icon.isConnected) return;
                icon.onload = () => {
                    // 原版：Draw(Left + (Width-w) div 2, Top + (Height-h) div 2)
                    icon.style.left = Math.max(0, Math.floor((32 - icon.naturalWidth) / 2)) + 'px';
                    icon.style.top = Math.max(0, Math.floor((29 - icon.naturalHeight) / 2)) + 'px';
                };
                icon.src = url;
                icon.style.display = 'block';
            });
        }
    }

    /** 点药水格：拿着背包药品 → 放入；空手且格里有物品 → 使用（原版 DBelt1Click / DBelt1DblClick）。 */
    /**
     * 单击药水格（对齐原版 DBelt1Click）：空手拿起 / 放下 / 移动；使用是双击（DBelt1DblClick）。
     * 拿起后点背包 → 从快捷栏移出（物品回背包网格）；点另一格 → 移到那格。
     */
    _onQuickSlotClick(slot) {
        const p = this.self;
        if (!p) return;
        const mi = (p.quickSlots || [])[slot] || 0;
        const it = mi > 0 ? (p.items || []).find(x => x.makeIndex === mi) : null;

        if (!this.pickedItem) {
            if (!it) return;   // 空格：什么都不做（原版也只在有物品时拿起）
            this.pickedItem = it;
            this.pickedSource = 'quick';
            this.pickedSlot = slot;
            this._markPicked(this.quickCellEls[slot].cell);
            return;
        }
        if (this.pickedSource === 'quick') {
            if (this.pickedSlot === slot) {
                this._clearPicked();   // 点同一格：放下（取消选中）
                return;
            }
            // 移到另一格：先清原格再设新格
            this.conn.send('quickSlot', { slot: this.pickedSlot, makeIndex: 0 });
            this.conn.send('quickSlot', { slot, makeIndex: this.pickedItem.makeIndex });
            this._clearPicked();
            return;
        }
        if (this.pickedSource === 'bag') {
            // 从背包放进快捷栏（原版 DBelt1Click 的放下分支）
            this.conn.send('quickSlot', { slot, makeIndex: this.pickedItem.makeIndex });
            this._clearPicked();
            return;
        }
        this._log('请先把这个装备放回背包。');   // 拿着已穿装备时不接受
    }

    /** 数字键 1..6 使用对应快捷栏（原版 DBelt 快捷键）。 */
    _useQuickSlot(slot) {
        const p = this.self;
        if (!p) return;
        const mi = (p.quickSlots || [])[slot] || 0;
        if (mi <= 0) {
            return;   // 空格静默（原版也不提示）
        }
        this.conn.send('useItem', { makeIndex: mi });
        // 原始 useItem：快捷栏里查不到物品时不播（原版按真实 StdMode 决定，
        // 解捆(31)/书(4)/卷轴(3) 都是静默的，不能退回喝药音）。
        const slotItem = (p.items || []).find((it) => it.makeIndex === mi);
        if (slotItem) {
            const sfx = itemUseSound(slotItem.stdMode);
            if (sfx) sound.play(sfx);
        }
    }

    /** 唤出聊天输入框（回车 / 斜杠键，对齐原版 ClMain 的 Enter/@/!/ / 处理）。 */
    _openChatInput(prefill) {
        if (!this.chatInput) return;
        this.chatInput.style.display = 'block';
        this.chatInput.value = prefill || '';
        this.chatInput.focus();
    }

    // F1-F8 施法：按快捷键 key（F11 技能设置页绑定）查找技能
    _castFromSlot(fkey) {
        const skills = this.self && this.self.skills ? this.self.skills : {};
        for (const [magicId, sk] of Object.entries(skills)) {
            if (sk && sk.key === fkey) {
                this._castMagic(Number(magicId));
                return;
            }
        }
    }

    _castMagic(magicId) {
        // 纯被动技能（基本剑术3/精神力战法4/攻杀7）不可施放
        if (PASSIVE_MAGICS.has(magicId)) return;
        // 原版 ClMain.pas:2421 施法前提是 CanNextAction（上一个动作播完）；
        // 没有这道闸，治愈/施毒可以插在攻击动作里连续按，恢复完全不打断进攻节奏。
        if (!this._isIdle()) return;
        // 本地立刻进施法动作（不等服务端回包）： SPELL_ANIM_MS 内 _isIdle()=false，
        // 攻击/移动被锁住——对应原版 g_dwMagicDelayTime = 200 + Magic.DB Delay。
        if (this.self) {
            this.self._action = 'spell';
            this.self._actionAt = Date.now();
        }
        // 战士主动技能（半月/烈火/野蛮）：朝面向方向施放，不选目标（原版 EffectType=0 分支）
        if (SWORD_SKILLS.has(magicId)) {
            this.conn.send('castMagic', { magicId, targetX: this.self.dir, targetY: 0, targetId: 0 });
            return;
        }
        // 法师/道士技能：朝鼠标位置释放（原版 UseMagic：MagicTarget=FocusCret，无则取鼠标格子坐标）
        const { tx, ty } = this._gridFromXY(this._lastMouse.mx, this._lastMouse.my);
        let targetId = 0, targX = tx, targY = ty;
        const t = this.magicTarget;
        if (t && t.id !== this.self.id) {
            targetId = t.id;       // 悬停在目标上：以目标为落点
            targX = t.x; targY = t.y;
        }
        this.conn.send('castMagic', { magicId, targetX: targX, targetY: targY, targetId });
    }

    // 朝目标点移动一步（原版 ProcessActionMessages：每步按当前坐标重算朝目标的方向）
    // 鼠标相对画面中心（人物）→ 逻辑 8 方向
    _dirFromMouse(e) {
        // 经 viewport 换算回 800×600 逻辑坐标（#stage 可能被等比放大）
        const { x: mx, y: my } = toLocal(e.clientX, e.clientY, this._canvas);
        const ox = Math.floor((SCREEN_WIDTH - 48) / 2);
        const oy = Math.floor((MAPSURFACEHEIGHT - 32) / 2);
        const dx = mx - (ox + 24);   // 人物格子中心 X
        const dy = my - (oy + 16);   // 人物格子中心 Y
        if (dx === 0 && dy === 0) return -1;
        const ax = Math.abs(dx) / 48;
        const ay = Math.abs(dy) / 32;
        let lgx, lgy;
        if (ax > ay * 1.5) { lgx = Math.sign(dx); lgy = 0; }
        else if (ay > ax * 1.5) { lgx = 0; lgy = Math.sign(dy); }
        else { lgx = Math.sign(dx); lgy = Math.sign(dy); }
        return DIRMAP[lgx + ',' + lgy];
    }

    _onKeyDown(e) {
        if (e.target === this.chatInput) return;
        if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') { this._shiftDown = true; return; }
        // Tab：打开地图 → 切换大地图/小地图 → 关闭（原版 TAB 打开地图、切换大小地图、关闭地图）
        if (e.code === 'Tab') { e.preventDefault(); this._cycleMapView(); return; }
        // V：小地图 关闭 → 半透明 → 不透明 → 关闭（原版 ViewMiniMapStyle，HUD 按钮上的提示也是 V）
        if (e.code === 'KeyV') { e.preventDefault(); this._toggleMiniMap(); return; }
        // Esc：优先关闭最上层窗口，没有窗口时打开系统菜单（原版 ESC 关窗口 / 打开系统设置）
        if (e.code === 'Escape') {
            e.preventDefault();
            if (!this._closeTopWindow()) this._toggleSystemMenu();
            return;
        }
        // F9 背包 / F10 状态 / F11 技能（原版 ClMain.pas FormKeyDown）
        if (e.code === 'F9') { e.preventDefault(); this._toggleBag(); return; }
        if (e.code === 'F10') { e.preventDefault(); this.statePage = 0; this._toggleStateWin(); return; }
        if (e.code === 'F11') { e.preventDefault(); this._openSkillSetup(); return; }
        // 回车 / @ / ! / 斜杠 唤出聊天输入框（对齐原版 ClMain.pas:2005-2027）
        if (e.key === 'Enter' || e.key === '@' || e.key === '!' || e.key === '/') {
            e.preventDefault();
            this._openChatInput(e.key === '/' || e.key === '@' || e.key === '!' ? e.key : '');
            return;
        }
        // 数字键 1..6 使用药水快捷栏（原版 DBelt1..6）
        if (e.code >= 'Digit1' && e.code <= 'Digit6') {
            e.preventDefault();
            this._useQuickSlot(Number(e.code.substring(5)) - 1);
            return;
        }
        // 聊天板滚动（原版 ClMain.pas:1902-1925：↑↓ 单行、PgUp/PgDn 翻 9 行）
        if (e.code === 'ArrowUp') { e.preventDefault(); this._scrollChat(-1); return; }
        if (e.code === 'ArrowDown') { e.preventDefault(); this._scrollChat(1); return; }
        if (e.code === 'PageUp') { e.preventDefault(); this._scrollChat(-VIEWCHATLINE); return; }
        if (e.code === 'PageDown') { e.preventDefault(); this._scrollChat(VIEWCHATLINE); return; }
        // Space 不绑定攻击（原版 `ClMain.pas` 里没有 VK_SPACE 分支）
        // F9/F10/F11、Tab、V、Esc 已在函数开头处理；HUD 按钮仍然保留，两条入口等价。
        if (e.code >= 'F1' && e.code <= 'F8') {
            e.preventDefault();
            this._castFromSlot(Number(e.code.substring(1)));
        }
    }

    _onKeyUp(e) {
        if (e.code === 'ShiftLeft' || e.code === 'ShiftRight') {
            this._shiftDown = false;
            // 松开 Shift：停止方向攻击（即使左键仍按住）
            this._dirAttackHolding = false;
        }
    }

    // 攻击前判定（对齐原版 CanNextAction + CanNextHit）：
    // 1) 角色空闲（IsIdle，不在攻击/施法/移动动作中）——「后一个动作的前提是前一个动作完成」
    // 2) 攻速间隔到（CanNextHit：1400 - min(800, min(370, level*14) + HitSpeed*60)）
    _tryAttack(targetId) {
        if (!this._isIdle()) return;
        if (!this._canNextHit()) return;
        this._lastHitTime = Date.now();
        // 本地立即标记攻击动作（不等服务端回包）：动画播放期间 _isIdle()=false，无法再次攻击
        if (this.self) {
            this.self._action = 'attack';
            this.self._actionAt = Date.now();
        }
        this.conn.send('attack', { targetId });
    }

    // 方向攻击（原版 ssShift+左键 强制攻击）：朝方向挥刀，不依赖点中目标
    _dirAttack(dir) {
        if (!this._isIdle()) return;
        if (!this._canNextHit()) return;
        this._lastHitTime = Date.now();
        if (this.self) {
            this.self.dir = dir;
            this.self._action = 'attack';
            this.self._actionAt = Date.now();
        }
        this.conn.send('dirAttack', { dir });
    }

    /** 8 方向：自身指向目标格（原版 GetNextDirection，0上 1右上 2右 3右下 4下 5左下 6左 7左上）。 */
    _dirTo(tx, ty) {
        if (!this.self) return -1;
        const sx = Math.sign(tx - this.self.x);
        const sy = Math.sign(ty - this.self.y);
        if (sx === 0 && sy === 0) return -1;
        if (sy < 0) return sx > 0 ? 1 : (sx < 0 ? 7 : 0);
        if (sy > 0) return sx > 0 ? 3 : (sx < 0 ? 5 : 4);
        return sx > 0 ? 2 : 6;
    }

    /**
     * 锁定目标追击（原版 `ClMain.pas:2664-2689` 主循环：`if TargetCret <> nil then AttackTarget(TargetCret)`）。
     * 原版 `AttackTarget`：相邻 → 发 CM_HIT 砍；不相邻 → `TargetX/TargetY := 目标旁边格子` 走过去。
     * 这里等价实现：距离 <= 1 砍（_tryAttack 内部有 IsIdle + CanNextHit 节流），远了每隔 WalkInterval 朝目标走一步。
     */
    _tickChase(now) {
        if (this.chaseTargetId == null || !this.self) return;
        const t = this.entities.get(this.chaseTargetId);
        if (!t || t.alive === false || (t.hp != null && t.hp <= 0)) {
            this.chaseTargetId = null;
            this.castTarget = null;
            return;
        }
        const d = Math.max(Math.abs(t.x - this.self.x), Math.abs(t.y - this.self.y));
        if (d <= 1) {
            this._tryAttack(t.id);
            return;
        }
        if (!this._isIdle()) return;
        if (now - this._lastMoveAt < WALK_INTERVAL) return;
        const dir = this._dirTo(t.x, t.y);
        if (dir < 0) return;
        this.conn.send('move', { dir, run: false });
        this._lastMoveAt = now;
    }

    // 角色是否空闲（动作已完成）：self 无动作或已回到 stand
    _isIdle() {
        const s = this.self;
        if (!s) return false;
        const action = s._action || 'stand';
        return action === 'stand';
    }

    // 攻速间隔（原版 CanNextHit）。HitSpeed 简化取 0（StdItems.csv 无攻击速度字段）。
    _canNextHit() {
        const level = this.self ? (this.self.level || 1) : 1;
        let levelfast = Math.min(370, level * 14);
        levelfast = Math.min(800, levelfast);   // + HitSpeed*60（当前 0）
        const nexthit = Math.max(0, 1400 - levelfast);
        return (Date.now() - this._lastHitTime) > nexthit;
    }

    // ---- 鼠标按下（点怪攻击 / 点 NPC 对话 / 点物拾取 / 空地走）----
    _onMouseDown(e) {
        if (!this.self || !this.mapActor.map) return;
        if (e.button !== 0 && e.button !== 2) return;   // 0=左键 2=右键
        e.preventDefault();
        // 大地图 / 系统菜单是模态的：第一次点击只关闭面板，不穿透成游戏内移动/攻击
        if (this._bigMapOpen || (this._sysMenu && this._sysMenu.style.display === 'block')) {
            this._closeTopWindow();
            return;
        }
        // 选中（拿起）物品时点击画面 → 扔到对应格子（原版 DropItem）
        if (this.pickedItem) {
            const { tx, ty } = this._gridFromMouse(e);
            this.conn.send('drop', { makeIndex: this.pickedItem.makeIndex, x: tx, y: ty });
            this._clearPicked();
            return;
        }
        const run = e.button === 2;   // 左键走、右键跑
        const { mx, my } = this._mouseXY(e);

        // 原版 ClMain.pas:2832-2845：按住 Alt + 左键点尸体 → SendButchAnimal，
        // CM_BUTCH 带 actor id（Recog）+ x/y/dir；ButchAnimal 只取鼠标格 ±1 的尸体。
        if (e.button === 0 && e.altKey) {
            const { tx, ty } = this._gridFromMouse(e);
            let target = null;
            let best = 99;
            for (const c of this.entities.values()) {
                if (!c._corpse) continue;
                const d = Math.max(Math.abs(c.x - tx), Math.abs(c.y - ty));
                if (d <= 1 && d < best) { best = d; target = c; }
            }
            if (target) {
                this.conn.send('butch', { actorId: target.id, x: tx, y: ty });
                // 原版 ClMain.pas:2840 发完 CM_BUTCH 后本地再 SendMsg(CM_SITDOWN)，
                // 不等服务端回包就播蹲下动作（HA.ActSitdown 456 / 2 帧 / 300ms）。
                const dx = target.x - this.self.x;
                const dy = target.y - this.self.y;
                const d = DIRMAP[Math.sign(dx) + ',' + Math.sign(dy)];
                if (d != null) this.self.dir = d;
                this.self._action = 'sitdown';
                this.self._actionAt = Date.now();
                return;
            }
        }

        // Shift + 左键（原版 ClMain.pas:2546 `if (target <> nil) or (ssShift in Shift)`）：
        //   ① 点中目标（怪/玩家/NPC）→ 强制攻击该目标（绕过「只能打怪」的筛选，NPC 由服务端拒绝）
        //   ② 没点中任何目标 → 朝鼠标方向空挥，并进入「按住持续挥刀」状态（松开左键/Shift 停）
        if (e.button === 0 && (e.shiftKey || this._shiftDown)) {
            const target = this._actorAtScreen(mx, my, 'monster')
                || this._actorAtScreen(mx, my, 'player')
                || this._actorAtScreen(mx, my, 'npc');
            if (target && target.id !== this.self.id) {
                this.castTarget = target.id;   // 锁定目标（原版 g_TargetCret，无高亮）
                if (target.type === 'monster' || target.type === 'player') {
                    this.chaseTargetId = target.id;   // 锁定（NPC 不追）
                }
                this._tryAttack(target.id);
                return;
            }
            const dir = this._dirFromMouse(e);
            if (dir >= 0) {
                this._dirAttackHolding = true;
                this._dirAttackDir = dir;
                this._dirAttack(dir);   // 立即先挥一刀
                return;
            }
        }

        // 贴图区域判定（原版 GetAttackFocusCharacter）：仅左键触发怪物攻击/NPC 对话/玩家攻击/拾取；
        // 右键只用于「跑」，点 NPC/怪不对话不攻击
        if (e.button === 0) {
            const monster = this._actorAtScreen(mx, my, 'monster');
            if (monster && monster.hp > 0) {
                const d = this._dist(this.self, monster);
                this.castTarget = monster.id;    // 锁定目标（原版 g_TargetCret，无高亮）
                this.chaseTargetId = monster.id; // 锁定（原版 TargetCret：相邻砍、远了自动走过去）
                // 1 格正常攻击；2 格且刺杀开关开启 → 隔位刺杀（忽视防御）；更远则由 _tickChase 走过去
                if (d <= 1 || (d === 2 && this.self._longHit)) {
                    this._tryAttack(monster.id);
                }
                return;
            }
            const npc = this._actorAtScreen(mx, my, 'npc');
            if (npc) {
                // 原版点中 NPC 贴图即对话（客户端不限距离，服务端限 15 格）
                this.conn.send('npcTalk', { npcId: npc.id });
                return;
            }
            // 其他玩家（原版：点击玩家 = 攻击，与怪物同；排除自己，原版 IsSelectMyself 判断）
            const player = this._actorAtScreen(mx, my, 'player');
            if (player && player.id !== this.self.id) {
                const pd = this._dist(this.self, player);
                this.castTarget = player.id;
                this.chaseTargetId = player.id;  // 锁定（原版 TargetCret）
                if (pd <= 1 || (pd === 2 && this.self._longHit)) {
                    this._tryAttack(player.id);
                }
                return;
            }
            const { tx, ty } = this._gridFromMouse(e);
            const gi = this._groundAt(tx, ty);
            if (gi && gi.x === this.self.x && gi.y === this.self.y) {
                this.conn.send('pickup', { itemId: gi.id });
                return;
            }
            // 原版 ClMain.pas:2696：StdMode 6 武器（鹤嘴锄等重武器）攻击发
            // CM_HEAVYHIT；点相邻石壁/矿堆（不可走格）→ 朝该方向重击，
            // 服务端遇 Shape 19 鹤嘴锄 + MINE 地图即挖矿（ObjBase.pas:8819-8847）。
            // 远处的墙不拦：仍按移动处理，走过去被服务端挡住（原版同样行为）。
            const tiles = this.mapActor.map && this.mapActor.map.tiles;
            const tile = tiles && tiles[tx] ? tiles[tx][ty] : null;
            if (tile && !tile.canStand
                && Math.max(Math.abs(tx - this.self.x), Math.abs(ty - this.self.y)) <= 1) {
                const wdir = this._dirTo(tx, ty);
                if (wdir >= 0) {
                    this.chaseTargetId = null;
                    this._dirAttack(wdir);
                    return;
                }
            }
        }

        // 空地移动：记录方向，按住持续朝该方向移动（主循环按间隔节流，快速点击不累计）
        this.chaseTargetId = null;   // 手动移动 → 取消锁定目标
        const dir = this._dirFromMouse(e);
        if (dir < 0) return;
        this._holding = true;
        this._mouseRun = run;
        this._mouseDir = dir;
        // 首次点击保持「立刻走一步」的手感，但**不能重置节流**：旧实现每次点击把
        // _lastMoveAt 归零，快速连点会绕过 WALK_INTERVAL 连发 N 条 move，服务端一次推进
        // N 格，镜头匀速追赶期间动画却已按 600ms 超时回 stand →「坐标还在动、人却不走路」。
        // 现在距离上次发送不足一个间隔时只挂 _pendingStep（布尔，N 次连点最多补 1 步）。
        const stepDur = run ? RUN_INTERVAL : WALK_INTERVAL;
        const now = Date.now();
        if (now - this._lastMoveAt >= stepDur) {
            this.conn.send('move', { dir, run });
            this._lastMoveAt = now;
            this._pendingStep = false;
        } else {
            this._pendingStep = true;
        }
    }

    // 鼠标移动：始终记录最近位置 + 悬停施法目标（原版 FocusCret），按住时实时更新移动方向
    _onMouseMove(e) {
        const { mx, my } = this._mouseXY(e);
        this._lastMouse = { mx, my };
        // 悬停目标追踪（怪物优先，其次玩家）
        const mon = this._actorAtScreen(mx, my, 'monster');
        const pl = this._actorAtScreen(mx, my, 'player');
        this.magicTarget = (mon && mon.hp > 0) ? mon : (pl && pl.id !== this.self.id ? pl : null);
        if (this._dirAttackHolding) {
            // 方向攻击持续中：实时更新朝向
            const dir = this._dirFromMouse(e);
            if (dir >= 0) this._dirAttackDir = dir;
            return;
        }
        if (!this._holding) return;
        const dir = this._dirFromMouse(e);
        if (dir >= 0) this._mouseDir = dir;
    }

    // 鼠标 → 游戏格子（渲染中心 + 卷动偏移反推，与 screenX/screenY 互逆）
    _gridFromMouse(e) {
        const { mx, my } = this._mouseXY(e);
        return this._gridFromXY(mx, my);
    }

    _gridFromXY(mx, my) {
        const ox = Math.floor((SCREEN_WIDTH - 48) / 2);
        const oy = Math.floor((MAPSURFACEHEIGHT - 32) / 2);
        const tx = Math.floor((mx - ox + this.mapActor._shiftX) / 48) + this.mapActor._cx;
        const ty = Math.floor((my - oy + this.mapActor._shiftY) / 32) + this.mapActor._cy;
        return { tx, ty };
    }

    _dist(a, b) {
        return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));
    }

    // 鼠标事件 → canvas 内 800×600 逻辑坐标
    _mouseXY(e) {
        const { x, y } = toLocal(e.clientX, e.clientY, this._canvas);
        return { mx: x, my: y };
    }

    // 贴图区域命中实体（原版 GetAttackFocusCharacter：贴图中间 40×70 判定）
    _actorAtScreen(mx, my, type) {
        for (const e of this.entities.values()) {
            if (e.type !== type) continue;
            const sp = this._sprite(e);
            const layers = sp.getLayers(Date.now());
            if (!layers || !layers.length) continue;
            // 实体屏幕锚点（self 含走动画偏移 offsetX/offsetY）
            const isSelf = e.id === this.self.id;
            const ox = isSelf ? (this._viewX - e.x) * 48 : 0;
            const oy = isSelf ? (this._viewY - e.y) * 32 : 0;
            const anchorX = this.mapActor.screenX(e.x) + ox;
            const anchorY = this.mapActor.screenY(e.y) + oy;
            for (const layer of layers) {
                if (!layer.canvas) continue;
                const dx = anchorX + (layer.px || 0);
                const dy = anchorY + (layer.py || 0);
                const cw = layer.canvas.width;
                const ch = layer.canvas.height;
                const centx = cw > 40 ? (cw - 40) / 2 : 0;
                const centy = ch > 70 ? (ch - 70) / 2 : 0;
                if (mx >= dx + centx && mx <= dx + cw - centx
                    && my >= dy + centy && my <= dy + ch - centy) {
                    return e;
                }
            }
        }
        return null;
    }

    _actorAt(tx, ty, type) {
        for (const e of this.entities.values()) {
            if (e.type === type && e.x === tx && e.y === ty) return e;
        }
        return null;
    }

    _groundAt(tx, ty) {
        for (const g of this.groundItems.values()) {
            if (g.x === tx && g.y === ty) return g;
        }
        return null;
    }

    /**
     * 脚下地砖的三层图库索引（供脚步音分类，原版 Actor.pas:2169-2257）。
     * 原版把坐标 `div 2 * 2` 对齐到 2×2 大地砖，这里照抄（取偶数格）。
     * 返回 null 表示地图还没进好，调用方退回默认地面音。
     */
    _terrainAt(x, y) {
        const tiles = this.mapActor && this.mapActor.map && this.mapActor.map.tiles;
        if (!tiles) return null;
        const tx = Math.floor(x / 2) * 2, ty = Math.floor(y / 2) * 2;
        const col = tiles[tx] || tiles[x];
        if (!col) return null;
        const t = col[ty];
        if (!t) return null;
        return { bng: t.bng, mid: t.mid, fr: t.obj };
    }

    /**
     * 把实体登记到音频模块：怪物叫声按 Appr×10+200，人形受击/攻击/惨叫音
     * 按原版 m_btWeapon / m_btDress / m_btSex 选（weapon/dress 都是 shape*2+sex）。
     */
    _registerSound(e) {
        if (!e || e.id == null) return;
        sound.registerEntity(e.id, e.type, e.appr, {
            weapon: e.weapon, dress: e.dress, sex: e.sex,
        });
    }

    onMessage(type, msg) {
        switch (type) {
            case 'backToSelectResult':
                // 原版 Alt-X：服务端保留登录态与连接，客户端切回人物选择场景。
                // 服务端随后发 characterList，会路由到新的 SelectCharScene。
                if (msg.ok) this.manager.switchTo(new SelectCharScene(this.manager));
                break;
            case 'entityAdd':
                // 跳过自己：self 持有 enterGame 快照里的完整对象（toPlayerJson），
                // 不能被 toBriefJson 的简化实体覆盖，否则 self 与 entities[id] 引用分离。
                if (msg.entity && this.self && msg.entity.id !== this.self.id) {
                    // 换图/后进入视野时直接看到的是已经躺好的尸体（原版 ActDeath 静止帧）
                    if (msg.entity.corpse) {
                        msg.entity._corpse = true;
                        msg.entity._action = 'death';
                        msg.entity._actionAt = Date.now();
                    }
                    this.entities.set(msg.entity.id, msg.entity);
                    if (msg.entity.corpse) this._sprite(msg.entity).prefetchDeathFrame(msg.entity);
                    this._registerSound(msg.entity);
                }
                break;
            case 'entityRemove':
                this.entities.delete(msg.id);
                sound.unregisterEntity(msg.id);
                break;
            case 'move': {
                // 原版 SM_WALK 携带 feature，客户端对未知 id 现场建 actor
                // （PlayScn.pas SendMsg → FindActor 找不到就 NewActor）。
                // 怪物从视野外走进来时，站着不动的玩家只收得到 move——
                // 不在这里建实体就会「挨打有声、怪看不见，一动才出现」。
                if (msg.entity && (!this.self || msg.entity.id !== this.self.id)
                    && !this.entities.has(msg.id)) {
                    this.entities.set(msg.entity.id, msg.entity);
                    this._registerSound(msg.entity);
                }
                const e = this.entities.get(msg.id);
                if (e) {
                    const moved = (e.x !== msg.x || e.y !== msg.y);
                    // 记录移动起点（非 self 实体走动画过渡用：从旧格滑到新格）
                    e._fromX = e.x; e._fromY = e.y;
                    e.x = msg.x; e.y = msg.y; e.dir = msg.dir;
                    if (moved) {
                        // 冲撞（野蛮冲撞）用 rush 动画，普通移动用 run/walk
                        const action = msg.rush ? 'rush' : (msg.run ? 'run' : 'walk');
                        // 动作切换才重置动画相位；连续同动作保持相位连续（避免每步重播前几帧）
                        if (e._action !== action) {
                            e._action = action;
                            e._actionAt = Date.now();
                        }
                        e._lastMoveAt = Date.now();   // 停止超时回 stand 用
                        e._moveAt = Date.now();
                    }
                    // 纯转向（位置不变，如攻击转身）：只更新 dir，不触发移动动画
                }
                break;
            }
            case 'entityHp': {
                // 旁观者血条：服务端把「玩家血量变化」合批广播给周围玩家
                // （原版 SM_STRUCK 的 HP 字段被注释，自己那份走 SM_HEALTHSPELLCHANGED，
                // 这里补的 entityHp 只带 hp，不动其它属性）。
                if (msg.id === this.self?.id) break;   // 自己的血量以 stats 为准
                const e = this.entities.get(msg.id);
                if (e) {
                    if (msg.hp !== undefined) e.hp = msg.hp;
                    if (msg.maxHp) e.maxHp = msg.maxHp;
                }
                break;
            }
            case 'stats': {
                const e = this.entities.get(msg.id);
                if (e) {
                    if (msg.hp !== undefined) e.hp = msg.hp;
                    if (msg.maxHp !== undefined) e.maxHp = msg.maxHp;
                    if (msg.mp !== undefined) e.mp = msg.mp;
                    if (msg.maxMp !== undefined) e.maxMp = msg.maxMp;
                    if (msg.level !== undefined) e.level = msg.level;
                    if (msg.exp !== undefined) e.exp = msg.exp;
                    // 原版 SM_ABILITY 一次带全：MaxExp / Weight / MaxWeight 与等级同批下发。
                    // 缺了 maxExp 的话升级后 EXP 条会一直拿旧阈值算（恒满）。
                    if (msg.maxExp !== undefined) e.maxExp = msg.maxExp;
                    if (msg.weight !== undefined) e.weight = msg.weight;
                    if (msg.maxWeight !== undefined) e.maxWeight = msg.maxWeight;
                    if (msg.gold !== undefined) e.gold = msg.gold;
                    if (msg.gm !== undefined) e.gm = !!msg.gm;   // GM 标记（Ctrl+大地图传送用）
                    if (msg.skills !== undefined) e.skills = msg.skills;   // 技能快捷键更新
                    // 战斗属性（幽灵盾/神圣战甲等 buff 改变 AC/MAC，F10 状态页实时刷新）
                    if (msg.ac !== undefined) e.ac = msg.ac;
                    if (msg.mac !== undefined) e.mac = msg.mac;
                    if (msg.dc !== undefined) e.dc = msg.dc;
                    if (msg.mc !== undefined) e.mc = msg.mc;
                    if (msg.sc !== undefined) e.sc = msg.sc;
                    if (msg.hit !== undefined) e.hit = msg.hit;     // 准确（装备 AC 字段映射）
                    if (msg.speed !== undefined) e.speed = msg.speed; // 敏捷（装备 MAC 字段映射）
                    // 状态特效：魔法盾 / 隐身 / 中毒 / 定身 / 战士开关
                    if (msg.shield !== undefined) {
                        if (msg.shield && !e._shield) e._shieldAt = Date.now();   // 护盾开启时间（成形动画用）
                        e._shield = msg.shield;
                    }
                    if (msg.hidden !== undefined) e._hidden = msg.hidden;
                    if (msg.poison !== undefined) e._poison = msg.poison;          // 红毒（掉血）
                    if (msg.poisonArmor !== undefined) e._poisonArmor = msg.poisonArmor;   // 绿毒（减防）
                    if (msg.nameColor !== undefined) e.nameColor = msg.nameColor;   // 宠物名字颜色随等级
                    if (msg.raceImg !== undefined) e.raceImg = msg.raceImg;         // 神兽变身（raceImg 54↔55，换动作表）
                    if (msg.appr !== undefined) e.appr = msg.appr;                  // 神兽变身（appr 170↔171，换图库偏移段）
                    if (msg.appr !== undefined) this._registerSound(e);             // 同步怪物叫声 Appr（神兽变身换音）
                    if (msg.paralyzed !== undefined) {
                        if (msg.paralyzed && !e._paralyzed) e._paralyzedAt = Date.now();   // 麻痹开始时间（钉住动画用）
                        e._paralyzed = msg.paralyzed;
                    }
                    if (msg.wideHit !== undefined) e._wideHit = msg.wideHit;
                    if (msg.longHit !== undefined) e._longHit = msg.longHit;
                    if (msg.fireHit !== undefined) e._fireHit = msg.fireHit;
                    // 升级视觉特效：原版 SM_LEVELUP → DrawEffectHum(8)
                    // （ClMain.pas:4825-4832，音效 s_meltstone 由 SoundManager 播）。
                    // 服务端不发独立 levelup 消息，这里按自身 stats.level 跳变触发。
                    if (e === this.self && msg.level !== undefined) {
                        if (this._selfLevel != null && msg.level > this._selfLevel) {
                            this._spawnLevelupFx();
                        }
                        this._selfLevel = msg.level;
                    }
                }
                // 状态窗口打开时同步刷新当前页（属性页 AC/MAC 随 buff 变化、技能页快捷键变更）
                if (this.stateWin && this.stateWin.style.display === 'block') {
                    this._refreshStateWin();
                }
                break;
            }
            case 'attack': {
                const a = this.entities.get(msg.attackerId);
                // noAttackAnim：冲撞等不触发攻击者攻击动作（只播目标受击，对齐原版 RM_STRUCK）
                if (a && !msg.noAttackAnim) {
                    // dir 对齐原版 SM_HIT 的 wParam（= m_btDirection）：怪物攻击
                    // 前已转向目标（Attack(m_TargetCret, btDir)），玩家轻微位移后
                    // 下一刀必须面向新位置。
                    if (msg.dir !== undefined) a.dir = msg.dir;
                    a._action = 'attack'; a._actionAt = Date.now();
                    // 神兽吐火（变身 raceImg=55）：攻击时在神兽位置喷火焰
                    // （原版 AxeMon.pas TWarriorElfMonster.RunFrameAction：攻击第 5 帧触发 TMapEffect，帧=820+10*Dir+1 起 5 帧）
                    if (a.raceImg === 55) {
                        this._spawnFx({ kind: 'elfFire', dir: a.dir || 0, x: a.x, y: a.y });
                    }
                    // 战士打击光效（攻杀/刺杀/半月/烈火，叠加在攻击者身上，与原版 BoHitEffect 一致）。
                    // 延迟到命中帧（武器接触点）才点亮，避免“先爆后砍”。
                    if (msg.swordHit && !msg.miss && SWORD_HIT_FX[msg.swordHit]) {
                        a._swordHit = msg.swordHit; a._swordHitAt = Date.now() + HIT_FRAME_MS;
                    }
                }
                const t = this.entities.get(msg.targetId);
                // noDamage：怪物 DC 不足以破防（原版 nDamage<=0）——攻击方照播挥击，
                // 目标不受击、不飘字（StruckDamage 直接 Exit）
                if (t && !msg.noDamage) {
                    // 命中帧对齐：原版在挥砍第 2~3 帧（武器接触点，约 170ms）才触发目标受击 /
                    // 伤害飘字 / 命中特效，而非 attack 消息到达即触发，避免“先爆后砍”。
                    setTimeout(() => {
                        const cur = this.entities.get(msg.targetId);
                        if (!cur) return;
                        if (msg.miss) {
                            // 未命中：原版在目标位置飘 MISS，目标不播受击动作（没被打中）
                            this._spawnDamageNumber(cur.x, cur.y, 'MISS', 0, '#ff5050');
                            return;
                        }
                        cur._action = 'struck'; cur._actionAt = Date.now();
                        // 伤害飘字
                        this._spawnDamageNumber(cur.x, cur.y, msg.damage, msg.magicId);
                        // 魔法命中特效（攻击类技能在目标位置爆炸/雷击）
                        if (msg.magicId && MAGIC_VFX[msg.magicId] && MAGIC_VFX[msg.magicId].hit) {
                            this._spawnFx({ kind: 'hit', fx: MAGIC_VFX[msg.magicId].hit, x: cur.x, y: cur.y });
                        }
                    }, HIT_FRAME_MS);
                }
                break;
            }
            case 'flyAxe': {
                // 远程怪射箭/投斧：攻击者播攻击动画 + 箭矢从攻击者飞向目标（延迟伤害由服务端结算）
                const a = this.entities.get(msg.attackerId);
                if (a) {
                    if (msg.dir !== undefined) a.dir = msg.dir;   // 对齐 SM_HIT 朝向语义
                    a._action = 'attack'; a._actionAt = Date.now();
                    const dir16 = this._dir16From(a.x, a.y, msg.x, msg.y);
                    this._spawnFx({ kind: 'flyAxe', sx: a.x, sy: a.y, x: msg.x, y: msg.y, dir16 });
                }
                break;
            }
            case 'digUp': {
                // RM_DIGUP → SM_DIGUP（ClMain.pas:4970）：僵尸2/僵尸20/石墓尸王、
                // 食人花出土。消息带 entity（原版 FindActor 找不到就 NewActor），
                // 动作 = ActDeath 段正向播放（AxeMon.pas:352），脚下叠单帧尘土
                // （mon6 ZOMBIDIGUPDUSTBASE=420+dir，clEvent.pas:105，原版 blend=FALSE）。
                const ent = msg.entity;
                if (ent && (!this.self || ent.id !== this.self.id)) {
                    this.entities.set(ent.id, ent);
                    this._registerSound(ent);
                    ent._action = 'digup';
                    ent._actionAt = Date.now();
                    this._spawnFx({ kind: 'digDust', x: msg.x, y: msg.y, dir: msg.dir || 0 });
                }
                break;
            }
            case 'alive': {
                // RM_ALIVE → SM_ALIVE（Grobal2.pas RM_ALIVE=10153 / SM_ALIVE=27，
                // ClMain.pas:4786）：race 96 复活僵尸原地复活。原版客户端
                // m_boDeath/m_boSkeleton := FALSE（Actor.pas:1462-1465），动作 =
                // ActDeath 段按方向正向播放（AxeMon.pas:424）+ 出场音
                // （Actor.pas:2401 SM_ALIVE → PlaySound(m_nAppearSound)）。
                const ent = msg.entity;
                if (ent && (!this.self || ent.id !== this.self.id)) {
                    this.entities.set(ent.id, ent);
                    this._registerSound(ent);
                    ent.alive = true;
                    ent._corpse = false;
                    ent._dieUntil = 0;
                    ent.skeleton = false;
                    ent._action = 'alive';
                    ent._actionAt = Date.now();
                }
                break;
            }
            case 'death': {
                // 播死亡动画。原版 SM_DEATH 后 actor 不删除，尸体一直留到
                // dwMakeGhostTime（ObjBase.pas:3769，3 分钟）才由服务端广播
                // RM_DISAPPEAR。**所有**怪都留尸体，不只是可挖肉的动物——
                // 以前非动物不带 corpse 标记，客户端把尸体当活物画，于是出现
                // 「怪不动、头顶一根空血条、走开才消失」（用户反馈）。
                const e = this.entities.get(msg.id);
                if (e) {
                    e._action = 'die';
                    e._actionAt = Date.now();
                    e._corpse = true;
                    e.alive = false;
                    e._dieUntil = Date.now() + 1400;   // ActDie 10 帧 ×140ms
                    this._sprite(e).prefetchDeathFrame(e);
                    if (msg.animal) {
                        // 原版 m_boAnimal 覆盖的不只是鸡鹿羊狼：毒蜘蛛/蝎子/
                        // 食人花/洞蛆也能 Alt+左键挖尸体（挖空后不出肉）。
                        this._log('[提示] 按住 Alt + 左键点击尸体可以挖取', '#e8d9b0');
                    }
                }
                if (this.castTarget === msg.id) this.castTarget = null;
                // 原版 SM_NOWDEATH（Actor.pas:2388-2395）：自己死亡 → PlayBGM
                // (bmg_gameover) + m_boDeath，PlayScn.pas:1476 把 ObjSurface
                // 切成 ceGrayScale 黑白屏，服务端不回城、不换图。
                if (this.self && msg.id === this.self.id) {
                    this._selfDead = true;
                    if (this.mapActor) this.mapActor.selfDead = true;
                    this._holding = false;
                    this._pendingStep = false;
                    this._dirAttackHolding = false;
                    this._mouseDir = -1;
                    this.chaseTargetId = null;
                    this._clearPicked();
                    this._log('[系统] 你被击败了。按 Esc →「返回角色选择」重新登录即可在出生点复活。', '#ff8a8a');
                }
                break;
            }
            case 'butch': {
                // RM_BUTCH → SM_BUTCH：原版客户端把它**转成 SM_SITDOWN**
                // （ClMain.pas:4649），播放 HA.ActSitdown（456 / 2 帧 / 300ms）。
                // 自己的动作由点击时本地播放（ClMain.pas:2840 同款预测），这里只管别人。
                const a = this.entities.get(msg.actorId);
                if (a) {
                    if (msg.dir != null) a.dir = msg.dir;
                    a._action = 'sitdown';
                    a._actionAt = Date.now();
                }
                // 出肉后尸体变骷髅（原版 RM_SKELETON → m_boSkeleton，尸体**不删**，
                // 仍留到 makeGhost 时间）。骷髅帧 = ActDeath.start，不带方向。
                const corpse = this.entities.get(msg.id);
                if (corpse && msg.meat && !corpse.skeleton) {
                    corpse.skeleton = true;
                    corpse._action = 'death';
                    corpse._dieUntil = 0;
                    this._sprite(corpse).prefetchDeathFrame(corpse);
                }
                break;
            }
            case 'spellCast': {
                const c = this.entities.get(msg.casterId);
                // 施法动作（HA.spell 段，与普通攻击 hit 段不同）；怪物施法者不覆盖——
                // 神兽吐火等由 attack 消息设置攻击动画（ActAttack 两足站立喷火），避免被 spell→stand 回退覆盖
                if (c && c.type === 'player') { c._action = 'spell'; c._actionAt = Date.now(); }
                // 施法特效：飞行弹道（火球/灵魂火符/雷击飞向目标点）
                const vfx = MAGIC_VFX[msg.magicId];
                const tx = msg.targetX ?? (c ? c.x : 0);
                const ty = msg.targetY ?? (c ? c.y : 0);
                if (vfx && vfx.fly && c) {
                    // 服务端已算好符纸飞行终点（撞高墙时中断在墙前，targetX/Y=中断点）；
                    // blocked 表示撞墙：符纸停在墙前消失，不转命中爆炸
                    this._spawnFx({
                        kind: 'fly',
                        fx: vfx.fly,
                        sx: c.x, sy: c.y,
                        x: tx, y: ty,
                        hit: msg.blocked ? null : (vfx.hit || null),
                        dir16: this._dir16From(c.x, c.y, tx, ty),   // 灵魂火符等按飞行方向选帧
                    });
                } else if (vfx && vfx.firegun && c) {
                    // 地狱火：火焰从角色向目标方向喷射（原版 TFireGunEffect，火焰团沿连线推进）
                    this._spawnFx({
                        kind: 'firegun',
                        fx: vfx.firegun,
                        sx: c.x, sy: c.y,
                        x: tx, y: ty,
                    });
                } else if (vfx && vfx.beam && c) {
                    // 疾光电影：闪电从角色沿方向延伸（原版 TLightingThunder，970+Dir16*10 帧）
                    const dir16 = this._dir16From(c.x, c.y, tx, ty);
                    const def = FX_FRAMES[vfx.beam];
                    this._spawnFx({
                        kind: 'beam',
                        lib: def.lib,
                        base: def.base + dir16 * (def.dirStride || 10),
                        frames: def.frames,
                        ft: def.ft,
                        sx: c.x, sy: c.y,
                    });
                } else if (vfx && vfx.self && c) {
                    // 自身特效（抗拒火环/地狱雷光）：在施法者位置播放
                    this._spawnFx({ kind: 'hit', fx: vfx.self, x: c.x, y: c.y });
                } else if (vfx && vfx.hit && !vfx.fly) {
                    // 定点命中（爆裂/冰咆哮/圣言/治愈等）：直接在目标点播放
                    this._spawnFx({ kind: 'hit', fx: vfx.hit, x: tx, y: ty });
                }
                break;
            }
            case 'fireWall': {
                // 火墙地面特效（持续到时限）
                const dur = (msg.duration || 8) * 1000;
                this._spawnFx({ kind: 'ground', fx: 'firewall', x: msg.x, y: msg.y, dur });
                break;
            }
            case 'spaceMove':
            case 'spaceMoveHide':
            case 'spaceMoveShow': {
                // 传送两段特效（原版 SM_SPACEMOVE_HIDE/SHOW，variant=1 为瞬息移动 2 号特效）：
                // Hide 固定在旧坐标格（TMapEffect 锚定地图格），Show 原版跟随角色——
                // 到达后角色暂无位移，落在落点格等价；'spaceMove' 是旧的 variant=1 现身别名。
                const show = msg.type !== 'spaceMoveHide';
                const v1 = msg.variant === 1 || msg.type === 'spaceMove';
                const fx = show ? (v1 ? 'spaceMove' : 'spaceMoveShow')
                                : (v1 ? 'spaceMoveHide2' : 'spaceMoveHide');
                this._spawnFx({ kind: 'hit', fx, x: msg.x, y: msg.y });
                break;
            }
            case 'chat':
                // 聊天板是白色底（底板自带），普通聊天用黑色字
                this._log(`${msg.name}: ${msg.text}`, '#000000');
                break;
            case 'systemMessage':
                // 原版 AddSysMsg 是 clGreen（深绿 #008000，白底上可读）；
                // 服务端可带 fg/bg 覆盖（如经验增加 = 红底白字）
                this._log(msg.text, msg.fg || '#008000', msg.bg || null);
                this._addSysMsg(msg.text);
                break;
            case 'dayChanging':
                // 原版 SM_DAYCHANGING（ObjBase.pas:5726）连 DarkLevel 一起下发，
                // 不只是换图标：地下地图由 _underground 覆盖成恒暗。
                this._applyBrightness(msg);
                break;
            case 'itemDrop':
                this.groundItems.set(msg.id, msg);
                break;
            case 'itemRemove':
                this.groundItems.delete(msg.id);
                break;
            case 'bagUpdate':
                if (this.self) {
                    this.self.gold = msg.gold;
                    this.self.items = msg.items || [];
                    if (msg.dress !== undefined) this.self.dress = msg.dress;
                    if (msg.weapon !== undefined) this.self.weapon = msg.weapon;
                    // 负重/腕力随穿戴变化（F10 第 2 页）
                    if (msg.weight !== undefined) this.self.weight = msg.weight;
                    if (msg.maxWeight !== undefined) this.self.maxWeight = msg.maxWeight;
                    if (msg.wearWeight !== undefined) this.self.wearWeight = msg.wearWeight;
                    if (msg.handWeight !== undefined) this.self.handWeight = msg.handWeight;
                    if (msg.maxWearWeight !== undefined) this.self.maxWearWeight = msg.maxWearWeight;
                    if (msg.maxHandWeight !== undefined) this.self.maxHandWeight = msg.maxHandWeight;
                    // 药水快捷栏 6 格（原版 ItemArr[0..5]）
                    if (msg.quickSlots) this.self.quickSlots = msg.quickSlots;
                }
                this._refreshBag();
                this._refreshStateWin();
                this._refreshQuickSlots();
                if (this.shopGoldEl) this.shopGoldEl.textContent = '金币 ' + (this.self.gold || 0);
                // 卖出后投放框物品自动消失（被卖的物品已不在背包）
                if (this.shopSellItem) {
                    const still = (msg.items || []).some(x => x.makeIndex === this.shopSellItem.makeIndex);
                    if (!still) this._clearSellSpot();
                }
                break;
            case 'npcSay':
                this._showNpcDialog(msg);
                break;
            case 'sellPrice': {
                // 原版 SM_SENDBUYPRICE：Recog>0 → 「N 金币」，否则「???? 金币」
                if (!this.shopPriceEl || !this.shopSellItem) break;
                if (msg.makeIndex !== undefined && msg.makeIndex !== this.shopSellItem.makeIndex) break;
                const n = Number(msg.price) || 0;
                this.shopPriceEl.textContent = n > 0 ? `价格: ${n} 金币` : '价格: ???? 金币';
                break;
            }
            case 'storageOpen':
                // 原版 SM_SENDUSERSTORAGEITEM（ClMain.pas:5435 →
                // ClientGetSendUserStorage：关卖物窗口 + SpotDlgMode=dmStorage +
                // ShowShopSellDlg）：存入模式，点背包物品直接发 storageStore
                this._showStorageDlg(msg);
                break;
            case 'storageList':
                // 原版 SM_SAVEITEMLIST（ClMain.pas:5217 → ClientGetSaveItemList：
                // 菜单列表窗口 + BoStorageMenu=TRUE，行点击即发取回）
                this._showStorageList(msg);
                break;
            case 'npcClose':
                this._closeNpcDialog();
                break;
            case 'changeMap': {
                // 换图快照：重建实体 + 地面物品 + 切地图
                this.snapshot = msg;   // _renderHud 读 snapshot.map.title，不更新会一直显示旧图名
                this._applyBrightness(msg);   // 新地图的 DarkLevel / underground
                // 换图即视为复活/重登，清掉死亡黑白屏（死亡期间服务端不会换图，
                // 这里主要防 GM 传送 / 异常快照把死亡状态带进新场景）。
                this._selfDead = !!(msg.player && msg.player.dead);
                this.mapActor.selfDead = this._selfDead;
                this.entities.clear();
                this.groundItems.clear();
                this._holding = false;
                this._mouseDir = -1;
                this._viewX = null;   // 重置卷动中心
                this.castTarget = null;
                this.chaseTargetId = null;   // 换图取消锁定
                this._clearPicked();   // 换图丢弃手中拿起的物品状态
                if (msg.player) {
                    this.self = msg.player;
                    this._selfLevel = msg.player.level != null ? msg.player.level : null;
                    this.entities.set(msg.player.id, msg.player); this._registerSound(msg.player); }
                for (const e of msg.nearby || []) { this.entities.set(e.id, e); this._registerSound(e); }
                for (const g of msg.groundItems || []) this.groundItems.set(g.id, g);
                if (msg.map) {
                    const p = this.self || { x: 0, y: 0 };
                    this._mapLoadError = null;
                    this._mapLoadingText = '正在加载地图…';
                    // 小地图底图（mmap.wil）与地图瓦片预取无关，必须立即换：
                    // 以前放在 enter().then() 里，预取期间仍是上一张图的底图，
                    // 而 snapshot 已是新图 → 点位按新格数归一化到旧底图，整体偏移。
                    this._miniMapIndex = msg.miniMapIndex;
                    this._loadMiniMapTexture(this._miniMapIndex, msg.map.width, msg.map.height)
                        .then(() => { if (this._bigMapOpen) this._renderBigMap(); });
                    this.mapActor.enter(msg.map.name, p.x, p.y,
                        (done, total) => { this._mapLoadingText = `正在加载地图资源 ${done}/${total}…`; })
                        .then(() => { if (this._bigMapOpen) this._renderBigMap(); })
                        .catch((e) => { console.error('[play]', e); this._mapLoadError = String((e && e.message) || e); })
                        .finally(() => { this._mapLoadingText = null; });
                }
                break;
            }
            default:
                break;
        }
    }

    /**
     * 追加一条聊天（原版 DScreen.AddChatBoardString(str, fcolor, bcolor)）：
     * fg 前景色、bg 背景色（null = 透明，露出底板白色聊天区）；缓冲上限 200 行，在底部时自动跟随。
     */
    _log(text, fg, bg) {
        if (!this.chatLog) return;
        this.chatLines.push({ text, fg: fg || '#008000', bg: bg || null });
        if (this.chatLines.length > 200) {
            this.chatLines.shift();
            if (this.chatBoardTop > 0) {
                this.chatBoardTop--;   // 原版 DrawScrn.pas:182-185：删头行时同步回退
            }
        }
        // 原版 DrawScrn.pas:186-188：最后一行不在窗口内则 ChatBoardTop++（自动跟随，不打断手动上翻）
        if (this.chatLines.length - this.chatBoardTop > VIEWCHATLINE) {
            this.chatBoardTop++;
        }
        this._renderChat();
    }

    /** 渲染聊天板 9 行窗口（无滚动条，纯 ChatBoardTop 偏移）。 */
    _renderChat() {
        if (!this.chatLog) return;
        const lines = this.chatLines;
        const maxTop = Math.max(0, lines.length - 1);
        this.chatBoardTop = Math.max(0, Math.min(this.chatBoardTop, maxTop));
        this.chatLog.innerHTML = '';
        for (let i = 0; i < VIEWCHATLINE; i++) {
            const idx = this.chatBoardTop + i;
            const line = lines[idx];
            const d = document.createElement('div');
            // 每行独立前景/背景色（原版 ChatStrs 的 Objects / ChatBks）
            d.style.cssText = `height:${CHAT_LINE_HEIGHT}px;line-height:${CHAT_LINE_HEIGHT}px;`
                + 'white-space:pre;overflow:hidden;'
                + (line ? `color:${line.fg};` + (line.bg ? `background:${line.bg};` : '') : 'color:transparent;');
            d.textContent = line ? line.text : '';
            this.chatLog.appendChild(d);
        }
    }

    /** 滚动聊天板（原版 ↑↓ ±1、PgUp/PgDn ±VIEWCHATLINE；滚轮走这里）。 */
    _scrollChat(delta) {
        if (!this.chatLines) return;
        this.chatBoardTop = Math.max(0, Math.min(this.chatBoardTop + delta, Math.max(0, this.chatLines.length - 1)));
        this._renderChat();
    }

    /** 昼夜时段图标：0 早(Prguse[15]) / 1 白([12]) / 2 傍([13]) / 3 晚([14])。只画图标，不做黑暗渲染。 */
    _setDayBright(b) {
        if (!this.dayIconEl || b == null || b === this._dayBright) return;
        this._dayBright = b;
        const idx = [15, 12, 13, 14][b] != null ? [15, 12, 13, 14][b] : 15;
        this._libUrl('prguse', idx).then((url) => {
            if (!url || !this.dayIconEl) return;
            this.dayIconEl.src = url;
            this.dayIconEl.style.display = 'block';
        });
    }

    /**
     * 原版 SM_NEWMAP / SM_CHANGEMAP 的第 4 参数 cdir = DayBright()
     * （ObjBase.pas:5618/5796），客户端 PlayScn.pas:2388 写入 DarkLevel 并
     * 据此开关 g_boViewFog；SM_DAYCHANGING（ClMain.pas:4812）同理。
     * 地下/矿区（MapInfo 的 MINE/DARK 标志）视野恒暗，不受昼夜影响。
     */
    _applyBrightness(s) {
        if (!s) return;
        if (s.underground !== undefined) this._underground = !!s.underground;
        if (s.dayBright !== undefined) this._setDayBright(s.dayBright);
        const b = s.dayBright != null ? s.dayBright : this._dayBright;
        let dl = s.darkLevel;
        if (dl == null) dl = (b === 1) ? 0 : (b === 3 ? 1 : 2);
        if (this._underground) dl = Math.max(1, dl);
        if (this.mapActor) this.mapActor.darkLevel = dl | 0;
    }

    /**
     * 加载小地图底图：直接用客户端资源 mmap.wil（原版 `FrmMain.WMMap`），
     * 索引来自服务端 MiniMap.txt（`mapSnapshot.miniMapIndex`，-1 = 该地图没有小地图）。
     * 图是 1.5 像素/格（原版 DrawMiniMap 的 `XX*48 div 32`、`YY*32 div 32`）。
     *
     * 注意：MiniMap.txt 是 **1-based**，原版 ClMain.pas 收 `SM_READMINIMAP_OK`
     * 后以 `g_WMMapImages.Images[g_nMiniMapIndex-1]` 取图（Param-1）。
     * 之前直接用 1-based 值取图，0 号比奇省登记的是 101，实际取到 index 101
     * 那张 900×599 的别的地图；正确应是 index 100（1052×699 ≈ 700 格×1.5 /
     * 700 格×1），这正是「大地图 / 小地图点位跟地形对不上」的根因。
     */
    async _loadMiniMapTexture(index, mapW = 0, mapH = 0) {
        // 优先用 mmap.wil（原版 WMMap，x 1.5 / y 1 像素每格，索引来自 MiniMap.txt，需 -1）
        if (index != null && index > 0) {
            const lib = getLib('mmap');
            if (lib) {
                try {
                    const tex = await lib.getTexture(index - 1);
                    if (tex && tex.canvas) {
                        this._miniMapTex = tex.canvas;
                        this._miniMapScale = 1.5;
                        // 「像素/格」比例绑定在纹理自身上：换图时若新底图还在加载，
                        // 旧纹理仍用自己的比例，不会被新地图的格数带偏。
                        this._miniMapKx = mapW > 0 ? tex.canvas.width / mapW : 1.5;
                        this._miniMapKy = mapH > 0 ? tex.canvas.height / mapH : 1.0;
                        return;
                    }
                } catch (e) {
                    /* 落到兜底 */
                }
            }
        }
        // 兜底：MiniMap.txt 没登记该地图 / mmap 里缺这张图 → 用地图 tiles 临时生成 1 像素/格
        this._buildMiniMapTexture();
    }

    /**
     * 兜底缩略图：从 mapActor.map.tiles 生成，再放大到 **1.5 像素/格**（与 mmap.wil 一致，
     * 这样切到没有小地图的地图时，120×120 窗口覆盖的格数不变，视觉大小一致）。
     */
    _buildMiniMapTexture() {
        const map = this.mapActor && this.mapActor.map;
        if (!map || !map.tiles) {
            this._miniMapTex = null;
            return;
        }
        const w = map.width;
        const h = map.height;
        const raw = document.createElement('canvas');
        raw.width = w;
        raw.height = h;
        const rctx = raw.getContext('2d');
        const img = rctx.createImageData(w, h);
        const d = img.data;
        for (let x = 0; x < w; x++) {
            const col = map.tiles[x];
            if (!col) {
                continue;
            }
            for (let y = 0; y < h; y++) {
                const t = col[y];
                const i = (y * w + x) * 4;
                if (!t) {
                    d[i + 3] = 0;
                } else if (t.canStand) {
                    d[i] = 96; d[i + 1] = 116; d[i + 2] = 72; d[i + 3] = 255;
                } else {
                    d[i] = 44; d[i + 1] = 42; d[i + 2] = 40; d[i + 3] = 255;
                }
            }
        }
        rctx.putImageData(img, 0, 0);
        // 缩放：大地图用原版的 1.5 像素/格；小地图（室内十几~几十格）放大到填满 120×120 窗口，最多 8 倍
        const scale = Math.max(1.5, Math.min(8, 120 / Math.max(w, h)));
        const c = document.createElement('canvas');
        c.width = Math.max(1, Math.round(w * scale));
        c.height = Math.max(1, Math.round(h * scale));
        const ctx = c.getContext('2d');
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(raw, 0, 0, c.width, c.height);
        this._miniMapTex = c;
        this._miniMapScale = scale;
        this._miniMapKx = scale;
        this._miniMapKy = scale;
    }

    /** 绘制小地图：裁剪玩家周围 120×120 像素 + 自己（白）/怪物（红）/NPC（黄）/其他玩家（蓝）点。 */
    _renderMiniMap() {
        const cv = this.miniMapCanvas;
        if (!cv) return;
        if (!this._miniMapStyle || !this.self || !this._miniMapTex) {
            cv.style.display = 'none';
            // 关掉时顺手清空，避免下次显示的第一帧还留着旧地图/旧坐标的点
            cv.getContext('2d').clearRect(0, 0, 120, 120);
            return;
        }
        cv.style.display = 'block';
        const ctx = cv.getContext('2d');
        const tex = this._miniMapTex;
        const p = this.self;
        const VIEW = 120;
        // 取正确索引后（MiniMap.txt 1-based → mmap.wil 0-based），比奇省底图是
        // 1052×699，正好对应 700×700 格的 `mx=x*1.5, my=y`。这里用
        // 「纹理像素 / 地图格数」归一化，等价于原版公式，且对兜底自生成缩略图
        // 同样成立；窗口起点钳制在底图内，保证 w/h 不会变负导致底图整块不画。
        // 点位比例绑定在纹理上（mmap: x=1.5 / y=1 像素每格，等价原版
        // mx=x*48 div 32、my=y*32 div 32；兜底缩略图: 各向同性 scale）。
        // 不能取 snapshot.map.width/height：换图时新快照可能配上仍在加载的旧底图，
        // 用新格数归一化旧纹理会让点位整体偏移（矿区/洞穴换位后尤其明显）。
        const kx = this._miniMapKx || 1.5;
        const ky = this._miniMapKy || 1.0;
        const px = Math.round(p.x * kx);
        const py = Math.round(p.y * ky);
        // 以玩家为中心取 120×120 窗口，越界时钳到底图边缘（不能再让 w/h 变负）。
        const sx = Math.max(0, Math.min(tex.width - VIEW, px - VIEW / 2));
        const sy = Math.max(0, Math.min(tex.height - VIEW, py - VIEW / 2));
        const w = Math.min(VIEW, tex.width - sx);
        const h = Math.min(VIEW, tex.height - sy);
        const translucent = this._miniMapStyle === 1;
        ctx.clearRect(0, 0, VIEW, VIEW);
        // 不透明态垫深色底 + 描边；半透明态不垫底、只降 alpha，让游戏画面透出来，
        // 两态才有肉眼可辨的差异（原版 ViewMiniMapStyle 1/2）。
        if (!translucent) {
            ctx.fillStyle = '#0b0d0a';
            ctx.fillRect(0, 0, VIEW, VIEW);
        }
        ctx.globalAlpha = translucent ? 0.68 : 1;
        if (w > 0 && h > 0) {
            ctx.drawImage(tex, sx, sy, w, h, 0, 0, w, h);
        }
        ctx.globalAlpha = 1;
        cv.style.outline = translucent ? '1px solid rgba(190,205,160,0.45)'
            : '1px solid rgba(190,205,160,0.9)';
        const dot = (gx, gy, color) => {
            const mx = Math.round(gx * kx) - sx;
            const my = Math.round(gy * ky) - sy;
            if (mx < 1 || my < 1 || mx >= VIEW - 1 || my >= VIEW - 1) return;
            ctx.fillStyle = color;
            ctx.fillRect(mx - 1, my - 1, 3, 3);
        };
        for (const e of this.entities.values()) {
            if (e.id === p.id || e.alive === false) continue;
            if (e.type === 'monster') {
                dot(e.x, e.y, '#f87171');
            } else if (e.type === 'npc') {
                dot(e.x, e.y, '#e5c07b');
            } else {
                dot(e.x, e.y, '#7dd3fc');
            }
        }
        dot(p.x, p.y, '#ffffff');
    }

    /** V 键：小地图 关闭 → 半透明 → 不透明 → 关闭（原版 ViewMiniMapStyle 1/2/0）。 */
    _toggleMiniMap() {
        this._miniMapStyle = (this._miniMapStyle + 1) % 3;
        if (this._miniMapStyle && !this._miniMapTex) {
            this._loadMiniMapTexture(this._miniMapIndex != null ? this._miniMapIndex : -1);
        }
        this._log(this._miniMapStyle === 0 ? '小地图：关闭'
            : (this._miniMapStyle === 1 ? '小地图：半透明' : '小地图：不透明'), '#e8d9b0');
    }

    // ---- 大地图（Tab）/ 系统菜单（Esc）------------------------------------
    // 原版 ClMain.pas：TAB 打开地图、切换大地图与小地图、再按关闭；
    // ESC 先关窗口，没有窗口时打开系统设置菜单。这里用 DOM 覆盖层实现，
    // 底图复用 mmap.wil / 兜底自生成的小地图纹理（同一份，不再额外请求资源）。

    /** Tab：关 → 小地图 → 大地图 → 关（原版 TAB 的三态循环）。 */
    _cycleMapView() {
        if (this._bigMapOpen) {
            this._closeBigMap();
            this._miniMapStyle = 0;
            this._renderMiniMap();
            this._log('地图：关闭', '#e8d9b0');
            return;
        }
        if (this._miniMapStyle) {
            this._openBigMap();
            this._log('地图：大地图（再按 Tab 关闭）', '#e8d9b0');
            return;
        }
        this._miniMapStyle = 2;
        if (!this._miniMapTex) {
            this._loadMiniMapTexture(this._miniMapIndex != null ? this._miniMapIndex : -1);
        }
        this._renderMiniMap();
        this._log('地图：小地图（再按 Tab 看大地图）', '#e8d9b0');
    }

    _openBigMap() {
        this._bigMapOpen = true;
        if (!this._miniMapTex) {
            this._loadMiniMapTexture(this._miniMapIndex != null ? this._miniMapIndex : -1);
        }
        this._buildBigMapPanel();
        if (this._bigMapPanel) this._bigMapPanel.style.display = 'block';
        this._renderBigMap();
    }

    _closeBigMap() {
        this._bigMapOpen = false;
        if (this._bigMapPanel) this._bigMapPanel.style.display = 'none';
    }

    _buildBigMapPanel() {
        if (this._bigMapPanel || !this.hudEl) return;
        const p = document.createElement('div');
        p.style.cssText = 'position:absolute;left:100px;top:36px;width:600px;height:420px;'
            + 'background:rgba(8,10,14,0.94);border:2px solid #8a7448;box-sizing:border-box;'
            + 'pointer-events:auto;display:none;z-index:60;font-family:SimSun,serif;color:#e8d9b0;';
        const title = document.createElement('div');
        title.style.cssText = 'height:26px;line-height:26px;padding-left:10px;font-size:14px;'
            + 'color:#ffe9a8;background:#241d12;border-bottom:1px solid #8a7448;';
        title.textContent = '地图';
        p.appendChild(title);
        this._bigMapTitle = title;

        const cv = document.createElement('canvas');
        cv.width = 580;
        cv.height = 350;
        cv.style.cssText = 'position:absolute;left:8px;top:34px;width:580px;height:350px;'
            + 'background:#0b0d0a;image-rendering:pixelated;';
        p.appendChild(cv);
        this._bigMapCanvas = cv;
        // GM：Ctrl+点击大地图 = 传送到对应坐标（客户端只发 @移动 命令，
        // 服务端校验 is_gm；坐标换算是点位变换的逆变换）。
        cv.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            e.preventDefault();
            if (!e.ctrlKey) return;
            const g = this._bigMapGeom;
            if (!g || !this.self) return;
            const rect = cv.getBoundingClientRect();
            const scale = cv.width / rect.width;   // CSS 缩放后也要换算回画布像素
            const px = (e.clientX - rect.left) * scale;
            const py = (e.clientY - rect.top) * scale;
            const gx = Math.max(0, Math.min(g.mapW - 1,
                Math.floor((px - g.bx) / ((g.kx || 1.5) * (g.k || 1)))));
            const gy = Math.max(0, Math.min(g.mapH - 1,
                Math.floor((py - g.by) / ((g.ky || 1.0) * (g.k || 1)))));
            this.conn.send('say', { text: `@移动 ${g.mapName} ${gx} ${gy}` });
        });

        const tip = document.createElement('div');
        tip.style.cssText = 'position:absolute;left:10px;top:392px;font-size:11px;color:#9fb08a;';
        tip.textContent = '白=自己  红=怪物  黄=NPC  蓝=玩家   |   Tab 关闭 / Esc 关闭 / 点击面板关闭'
            + (this.self && this.self.gm ? '   |   GM: Ctrl+点击传送' : '');
        p.appendChild(tip);
        p.addEventListener('mousedown', (e) => {
            e.stopPropagation();
            this._closeBigMap();
        });
        this.hudEl.appendChild(p);       // hudEl 随 onExit 的 gui.innerHTML='' 一起清理
        this._bigMapPanel = p;
    }

    /** 整张地图等比铺进面板，并标出自己/怪物/NPC/其他玩家（复用小地图底图）。 */
    _renderBigMap() {
        if (!this._bigMapOpen) return;
        this._buildBigMapPanel();
        const cv = this._bigMapCanvas;
        if (!cv) return;
        const ctx = cv.getContext('2d');
        ctx.fillStyle = '#0b0d0a';
        ctx.fillRect(0, 0, cv.width, cv.height);
        const tex = this._miniMapTex;
        if (!tex || !this.self) {
            ctx.fillStyle = '#c9b98a';
            ctx.font = '14px SimSun,serif';
            ctx.fillText('该地图暂无地图资料', 12, 24);
            return;
        }
        // 与小地图同一口径：用「纹理像素 / 地图格数」归一化，等价于原版
        // `mx=x*48 div 32, my=y*32 div 32`（正确索引下 1052×699 ↔ 700×700 格）。
        // 修正 MiniMap.txt 的 1-based off-by-one 后，点位才真正落在对应地形上。
        const sm0 = (this.snapshot && this.snapshot.map) || (this.mapActor && this.mapActor.map) || {};
        // 与小地图同一口径，比例绑定纹理，避免换图时序错配。
        const kx = this._miniMapKx || 1.5;
        const ky = this._miniMapKy || 1.0;
        const mapW = Math.max(1, tex.width / kx);
        const mapH = Math.max(1, tex.height / ky);
        const k = Math.min((cv.width - 16) / tex.width, (cv.height - 16) / tex.height);
        const w = Math.max(1, Math.floor(tex.width * k));
        const h = Math.max(1, Math.floor(tex.height * k));
        const bx = Math.floor((cv.width - w) / 2);
        const by = Math.floor((cv.height - h) / 2);
        // 记录几何信息供 Ctrl+点击传送逆变换使用
        this._bigMapGeom = {
            bx, by, w, h, mapW, mapH, k, kx, ky,
            mapName: sm0.name || (this.mapActor && this.mapActor.map && this.mapActor.map.name) || '',
        };
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(tex, 0, 0, tex.width, tex.height, bx, by, w, h);
        const dot = (gx, gy, color) => {
            const dx = Math.round(bx + gx * kx * k);
            const dy = Math.round(by + gy * ky * k);
            ctx.fillStyle = color;
            ctx.fillRect(dx - 1, dy - 1, 3, 3);
        };
        for (const e of this.entities.values()) {
            if (e.alive === false || e.id === this.self.id) continue;
            if (e.type === 'monster') dot(e.x, e.y, '#f87171');
            else if (e.type === 'npc') dot(e.x, e.y, '#e5c07b');
            else dot(e.x, e.y, '#7dd3fc');
        }
        dot(this.self.x, this.self.y, '#ffffff');
        // 与 HUD 左下角同一口径：优先中文地图名 title（如「比奇省」），
        // 服务端 name 是内部编号 "0"，直接拿来当标题会显示成数字。
        const sm = (this.snapshot && this.snapshot.map) || this.mapActor.map || {};
        const name = sm.title || sm.name || '未知地图';
        if (this._bigMapTitle) {
            this._bigMapTitle.textContent = `${name}　(${this.self.x}, ${this.self.y})`;
        }
    }

    /** 关掉最上层的一个窗口；返回 false 表示没有任何窗口可关（此时 Esc 打开系统菜单）。 */
    _closeTopWindow() {
        if (this._bigMapOpen) { this._closeBigMap(); return true; }
        if (this._sysMenu && this._sysMenu.style.display === 'block') {
            this._closeSystemMenu();
            return true;
        }
        if (this.shopPanel) { this._closeShopPanel(); return true; }
        if (this.npcDlg) { this._closeNpcDialog(); return true; }
        if (this.stateWin && this.stateWin.style.display === 'block') {
            this.stateWin.style.display = 'none';
            return true;
        }
        if (this.bagPanel && this.bagPanel.style.display === 'block') {
            this.bagPanel.style.display = 'none';
            return true;
        }
        return false;
    }

    _toggleSystemMenu() {
        this._buildSystemMenu();
        if (!this._sysMenu) return;
        const open = this._sysMenu.style.display === 'block';
        this._sysMenu.style.display = open ? 'none' : 'block';
    }

    _closeSystemMenu() {
        if (this._sysMenu) this._sysMenu.style.display = 'none';
    }

    _buildSystemMenu() {
        if (this._sysMenu || !this.hudEl) return;
        const p = document.createElement('div');
        p.style.cssText = 'position:absolute;left:285px;top:120px;width:230px;padding:6px;'
            + 'background:rgba(10,12,16,0.96);border:2px solid #8a7448;box-sizing:border-box;'
            + 'pointer-events:auto;display:none;z-index:70;font-family:SimSun,serif;color:#e8d9b0;';
        const title = document.createElement('div');
        title.style.cssText = 'height:24px;line-height:24px;text-align:center;font-size:14px;'
            + 'color:#ffe9a8;background:#241d12;border-bottom:1px solid #8a7448;margin-bottom:6px;';
        title.textContent = '系统菜单';
        p.appendChild(title);
        const mk = (label, fn) => {
            const b = document.createElement('div');
            b.style.cssText = 'height:24px;line-height:24px;text-align:center;cursor:pointer;'
                + 'border:1px solid #5c4d30;margin:3px 6px;background:#241d12;'
                + 'color:#ffe9a8;font-size:12px;';
            b.textContent = label;
            b.addEventListener('mouseenter', () => { b.style.background = '#3a2f1c'; });
            b.addEventListener('mouseleave', () => { b.style.background = '#241d12'; });
            b.addEventListener('mousedown', (e) => e.stopPropagation());
            b.addEventListener('click', (e) => { e.stopPropagation(); fn(); });
            p.appendChild(b);
            return b;
        };
        mk('返回游戏', () => this._closeSystemMenu());
        mk('大地图 (Tab)', () => { this._closeSystemMenu(); this._openBigMap(); });
        mk('小地图 (V)', () => { this._closeSystemMenu(); this._toggleMiniMap(); });
        mk('音效：开 / 关', () => this._toggleSound());
        // 免蜡（原版 g_boForceNotViewFog）：开 = 强制全亮，关 = 需要蜡烛/火把。
        const fogBtn = mk('', () => this._toggleNoFog());
        this._syncFogBtn = () => {
            fogBtn.textContent = `免蜡：${this._noFog ? '开（全亮）' : '关（需火把）'}`;
            fogBtn.style.color = this._noFog ? '#9fe08a' : '#ffe9a8';
        };
        this._syncFogBtn();
        mk('背包 (F9)', () => { this._closeSystemMenu(); this._toggleBag(); });
        mk('状态 (F10)', () => { this._closeSystemMenu(); this.statePage = 0; this._toggleStateWin(); });
        mk('技能 (F11)', () => { this._closeSystemMenu(); this._openSkillSetup(); });
        mk('快捷键说明', () => { this._closeSystemMenu(); this._showKeyHints(); });
        mk('返回角色选择', () => { this._closeSystemMenu(); this._backToSelect(); });
        const tip = document.createElement('div');
        tip.style.cssText = 'margin:6px 4px 2px;text-align:center;font-size:11px;color:#9fb08a;';
        tip.textContent = 'Esc 关闭';
        p.appendChild(tip);
        this.hudEl.appendChild(p);
        this._sysMenu = p;
    }

    _showKeyHints() {
        this._log('[快捷键] Tab 大地图 / V 小地图 / Esc 系统菜单 / F9 背包 / F10 状态 / F11 技能 / F1-F8 技能栏', '#e8d9b0');
        this._log('[鼠标] 左键走或攻击 / 右键跑 / Shift+左键强制攻击 / 1-6 喝药 / 回车聊天', '#e8d9b0');
    }

    /** 屏幕上方系统消息飘字（原版 DScreen.AddSysMsg：3 秒后消失、最多 10 条）。 */
    _addSysMsg(text) {
        if (!this.sysMsgEl) return;
        const now = Date.now();
        this.sysMsgs = this.sysMsgs.filter(m => now - m.at < 3000);
        this.sysMsgs.push({ text, at: now });
        if (this.sysMsgs.length > 10) {
            this.sysMsgs.shift();
        }
        this._renderSysMsgs();
        clearTimeout(this._sysMsgTimer);
        this._sysMsgTimer = setTimeout(() => this._renderSysMsgs(), 3100);
    }

    _renderSysMsgs() {
        if (!this.sysMsgEl) return;
        const now = Date.now();
        this.sysMsgs = this.sysMsgs.filter(m => now - m.at < 3000);
        this.sysMsgEl.innerHTML = '';
        for (const m of this.sysMsgs) {
            const d = document.createElement('div');
            d.style.cssText = 'height:16px;line-height:16px;color:#00ff00;text-shadow:1px 1px 0 #000;white-space:pre;';
            d.textContent = m.text;
            this.sysMsgEl.appendChild(d);
        }
    }

    // ---- 背包面板（F9，原版 DItemBag）----
    // ---- 状态窗口（F10，原版 DStateWin）----

    // 窗口可拖动 + 点击前置（原版 TDWindow：MouseDown ChangeChildOrder 置顶，MouseMove 拖动）
    _makeWindowDraggable(win) {
        // 点击任意位置 → 前置（捕获阶段，子元素点击也触发）
        win.addEventListener('mousedown', () => this._bringToFront(win), true);
        // 整个窗口按住拖动；超过 4px 阈值才算拖动（否则视为点击，不影响格子/按钮）
        let drag = null;
        win.addEventListener('mousedown', (e) => {
            // 拖拽差值也必须换算成逻辑像素，否则放大后窗口移动比鼠标快 k 倍
            const p = toLocal(e.clientX, e.clientY);
            drag = { sx: p.x, sy: p.y, l: win.offsetLeft, t: win.offsetTop, moved: false };
        });
        const mm = (e) => {
            if (!drag || !win.isConnected) return;   // 窗口已移除则忽略
            const p = toLocal(e.clientX, e.clientY);
            const dx = p.x - drag.sx;
            const dy = p.y - drag.sy;
            if (!drag.moved && Math.abs(dx) < 4 && Math.abs(dy) < 4) return;
            drag.moved = true;
            win.style.left = Math.max(0, Math.min(SCREEN_WIDTH - win.offsetWidth, drag.l + dx)) + 'px';
            win.style.top = Math.max(0, Math.min(SCREEN_HEIGHT - win.offsetHeight, drag.t + dy)) + 'px';
        };
        const mu = () => {
            const wasMoved = drag && drag.moved;
            drag = null;
            if (wasMoved) win._suppressClick = Date.now();   // 拖动后抑制 click
        };
        document.addEventListener('mousemove', mm);
        document.addEventListener('mouseup', mu);
        // 拖动后抑制 click（避免拖动触发格子选中/按钮点击）
        win.addEventListener('click', (e) => {
            if (win._suppressClick && Date.now() - win._suppressClick < 300) {
                e.stopPropagation();
                e.preventDefault();
            }
        }, true);
        this._dragCleaners.push(() => {
            document.removeEventListener('mousemove', mm);
            document.removeEventListener('mouseup', mu);
        });
    }

    _bringToFront(win) {
        win.style.zIndex = ++this._zTop;
    }

    _toggleStateWin() {
        if (!this.stateWin) this._buildStateWin();
        const open = this.stateWin.style.display === 'block';
        this.stateWin.style.display = open ? 'none' : 'block';
        if (!open) this._refreshStateWin();
    }

    // 技能按钮：打开/关闭技能设置页（原版 VK_F11 → StatePage:=3 → OpenMyStatus 是 toggle 窗口）
    _openSkillSetup() {
        if (!this.stateWin) this._buildStateWin();
        const open = this.stateWin.style.display === 'block';
        this.statePage = 3;
        if (open) {
            this.stateWin.style.display = 'none';
        } else {
            this.magicPage = 0;   // 打开技能页时回到第一页（原版 OpenMyStatus → PageChanged StatePage=3 → MagicPage:=0）
            this.stateWin.style.display = 'block';
            this._refreshStateWin();
            this._bringToFront(this.stateWin);
        }
    }

    _buildStateWin() {
        if (this.stateWin) return;
        const gui = this.manager.winManager.container;
        const win = document.createElement('div');
        // 用 left 定位（原版右对齐 ≈ 800-232=568），便于拖动
        // 窗口背景用 Prguse3[207]（232×362）：比 Prguse[370]（232×325）高 37px，
        // 内页区域 168×234（对应 Prguse3 的 29/30/32/33），底部信息框也更完整。
        win.style.cssText = 'position:absolute;left:568px;top:0;width:232px;height:362px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        win.style.display = 'none';
        this._libUrl('prguse3', 207).then((url) => {
            if (url && this.stateWin === win) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.className = 'win-drag';
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                win.insertBefore(bg, win.firstChild);
            }
        });
        // 装备页（人物底图 + 13 装备槽）
        this.stateEquipWrap = document.createElement('div');
        this.stateEquipWrap.style.cssText = 'position:absolute;left:0;top:0;width:232px;height:362px;';
        win.appendChild(this.stateEquipWrap);
        this.stateBodyEl = document.createElement('div');
        this.stateBodyEl.style.cssText = 'position:absolute;left:38px;top:52px;';
        this.stateEquipWrap.appendChild(this.stateBodyEl);
        this.stateEquipEl = document.createElement('div');
        this.stateEquipEl.style.cssText = 'position:absolute;left:0;top:0;';
        this.stateEquipWrap.appendChild(this.stateEquipEl);
        // 装备页底部描述区：Prguse3[207] 底部信息框（约 y=304-354，内页底图到 y=286）
        this.stateDescEl = document.createElement('div');
        this.stateDescEl.style.cssText = 'position:absolute;left:34px;top:307px;width:180px;height:44px;'
            + 'font-size:11px;line-height:12px;color:#e8d9b0;overflow:hidden;pointer-events:none;';
        this.stateEquipWrap.appendChild(this.stateDescEl);
        // 属性页（数值对齐背景标签；Prguse3[207] 自带 AC/MAC/DC/MC/SC/HP/MP 标签）
        this.stateAttrWrap = document.createElement('div');
        this.stateAttrWrap.style.cssText = 'position:absolute;left:0;top:0;width:232px;height:362px;display:none;';
        win.appendChild(this.stateAttrWrap);
        this.stateAttrEl = document.createElement('div');
        this.stateAttrEl.style.cssText = 'position:absolute;left:112px;top:99px;width:96px;'
            + 'font-size:12px;line-height:20px;text-shadow:1px 1px 0 #000;';
        this.stateAttrWrap.appendChild(this.stateAttrEl);
        // 属性数值页（原版 StatePage=2：经验/重量/负重/腕力/精确度/敏捷度等，背景 Prguse3[32] 空白纹理页）
        this.stateAttr2Wrap = document.createElement('div');
        this.stateAttr2Wrap.style.cssText = 'position:absolute;left:0;top:0;width:232px;height:362px;display:none;';
        win.appendChild(this.stateAttr2Wrap);
        this._libUrl('prguse3', 32).then((url) => {
            if (url && this.stateWin === win) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:38px;top:52px;';
                this.stateAttr2Wrap.insertBefore(bg, this.stateAttr2Wrap.firstChild);
            }
        });
        this.stateAttr2El = document.createElement('div');
        this.stateAttr2El.style.cssText = 'position:absolute;left:58px;top:62px;width:150px;'
            + 'font-size:12px;line-height:14px;text-shadow:1px 1px 0 #000;';
        this.stateAttr2Wrap.appendChild(this.stateAttr2El);
        // 技能页（F12，原版 StatePage=3 魔法页：Prguse3[33] 背景（自带 5 个技能框）+ 5 个技能行 + 内部分页）
        this.stateSkillWrap = document.createElement('div');
        this.stateSkillWrap.style.cssText = 'position:absolute;left:0;top:0;width:232px;height:362px;display:none;';
        win.appendChild(this.stateSkillWrap);
        this._libUrl('prguse3', 33).then((url) => {
            if (url && this.stateWin === win) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:38px;top:52px;';
                // 插入到技能列表之前：背景异步加载完时列表已存在，保证列表文字不被背景遮挡
                this.stateSkillWrap.insertBefore(bg, this.stateSkillListEl);
            }
        });
        // 技能列表：5 行（图标 31x33 @原版 DStMag1-5 位置 46,59/96/134/171/208 + 名字/等级/快捷键）
        this.stateSkillListEl = document.createElement('div');
        this.stateSkillListEl.style.cssText = 'position:absolute;left:46px;top:59px;width:170px;height:185px;'
            + 'font-size:12px;color:#e8d9b0;text-shadow:1px 1px 0 #000;';
        this.stateSkillWrap.appendChild(this.stateSkillListEl);
        // 内部分页按钮（原版 DStPageUp=398 上 213,113 / DStPageDown=396 下 213,143），仅技能页显示
        const mkMagPage = (idx, top, delta) => {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:213px;top:${top}px;width:12px;height:21px;cursor:pointer;`;
            b.addEventListener('click', () => {
                this.magicPage += delta;
                this._refreshSkillPage();
            });
            this._libUrl('prguse', idx).then((url) => {
                if (url) { const im = document.createElement('img'); im.src = url; im.draggable = false; b.appendChild(im); }
            });
            this.stateSkillWrap.appendChild(b);
        };
        mkMagPage(398, 113, -1);   // 上翻
        mkMagPage(396, 143, 1);    // 下翻
        // 角色名（原版：Left+122 水平居中，Top+23，纯名字）
        this.stateNameEl = document.createElement('div');
        this.stateNameEl.style.cssText = 'position:absolute;left:0;top:23px;width:232px;text-align:center;'
            + 'font-size:12px;color:#f0e6c8;text-shadow:1px 1px 0 #000;';
        win.appendChild(this.stateNameEl);
        // 关闭按钮（原版 DCloseState=371，位置 8,39）：正常态红X画在背景图里，
        // img 为按下态，按下才显示、抬起恢复（原版 Downed 语义）
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:8px;top:39px;width:16px;height:23px;cursor:pointer;';
        close.addEventListener('click', () => { win.style.display = 'none'; });
        win.appendChild(close);
        this._libUrl('prguse', 371).then((url) => {
            if (url && this.stateWin === win) {
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
        // 翻页箭头（原版 DPrevState=373 上 / DNextState=372 下，位置 7,128 / 7,187）
        const mkArrow = (idx, top, page) => {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:7px;top:${top}px;width:24px;height:23px;cursor:pointer;`;
            b.addEventListener('click', () => {
                this.statePage = (this.statePage + page + 4) % 4;   // 0装备 1状态值 2属性数值 3技能 循环
                this._refreshStateWin();
            });
            this._libUrl('prguse', idx).then((url) => {
                if (url) { const im = document.createElement('img'); im.src = url; im.draggable = false; b.appendChild(im); }
            });
            win.appendChild(b);
        };
        mkArrow(373, 128, -1);   // 上箭头 → 上一页
        mkArrow(372, 187, 1);    // 下箭头 → 下一页
        this.stateWin = win;
        this.statePage = 0;
        this._makeWindowDraggable(win);
        gui.appendChild(win);
    }

    _refreshStateWin() {
        if (!this.stateWin || !this.self) return;
        const p = this.self;
        // 角色名（原版只显示名字）
        this.stateNameEl.textContent = p.name;
        // 分页显示
        this.stateEquipWrap.style.display = this.statePage === 0 ? 'block' : 'none';
        this.stateAttrWrap.style.display = this.statePage === 1 ? 'block' : 'none';
        this.stateAttr2Wrap.style.display = this.statePage === 2 ? 'block' : 'none';
        this.stateSkillWrap.style.display = this.statePage === 3 ? 'block' : 'none';
        if (this.statePage === 0) {
            this._refreshEquipPage();
        } else if (this.statePage === 1) {
            this._refreshAttrPage();
        } else if (this.statePage === 2) {
            this._refreshAttr2Page();
        } else {
            this._refreshSkillPage();
        }
    }

    // 技能页（F12）：已学技能列表（每页 5 个：图标 + 名字 + 等级 + 快捷键）
    _refreshSkillPage() {
        if (!this.stateSkillListEl) return;
        this.stateSkillListEl.innerHTML = '';
        const skills = this.self && this.self.skills ? this.self.skills : {};
        const entries = Object.entries(skills);
        if (!entries.length) {
            const empty = document.createElement('div');
            empty.textContent = '尚未学习任何技能';
            empty.style.color = '#888';
            empty.style.lineHeight = '37px';
            this.stateSkillListEl.appendChild(empty);
            return;
        }
        // 内部分页：每页 5 个，页码钳制
        const pageCount = Math.max(1, Math.ceil(entries.length / 5));
        if (this.magicPage >= pageCount) this.magicPage = pageCount - 1;
        if (this.magicPage < 0) this.magicPage = 0;
        const page = entries.slice(this.magicPage * 5, this.magicPage * 5 + 5);
        for (const [magicId, sk] of page) {
            const mid = Number(magicId);
            const passive = PASSIVE_MAGICS.has(mid);   // 被动技能白名单（基本剑术/精神力战法/攻杀）
            const row = document.createElement('div');
            row.style.cssText = 'position:relative;height:37px;overflow:hidden;'
                + (passive ? 'color:#888;' : '');
            // 技能图标（原版 MagIcon[Effect*2]，Effect=0 的被动技能也用 magicon[0]）
            // 单击图标弹快捷键设置对话框（原版 DStMag1Click，仅图标可点，整行不可点）
            if (sk.effect != null && sk.effect >= 0) {
                this._libUrl('magicon', sk.effect * 2).then((url) => {
                    if (url) {
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'position:absolute;left:0;top:0;width:31px;height:33px;'
                            + (passive ? '' : 'cursor:pointer;');
                        if (!passive) {
                            im.title = '点击设置快捷键';
                            im.addEventListener('click', () => this._setupSkillKey(mid, sk));
                        }
                        row.appendChild(im);
                    }
                });
            }
            // 名字（原版 TextOut bbx+48）
            const nameEl = document.createElement('div');
            nameEl.textContent = sk.name;
            nameEl.style.cssText = 'position:absolute;left:40px;top:1px;white-space:nowrap;';
            row.appendChild(nameEl);
            // 等级（原版 TextOut bbx+48+16, bby+8+15）
            const lvEl = document.createElement('div');
            lvEl.textContent = String(sk.level);
            lvEl.style.cssText = 'position:absolute;left:56px;top:17px;font-size:11px;';
            row.appendChild(lvEl);
            if (!passive) {
                // 快捷键图标（原版 keyimg Prguse[650..657]，此资源缺失改用 DKeySelDlg 的 F 键图 232/234/...）
                if (sk.key) {
                    this._libUrl('prguse', 232 + (sk.key - 1) * 2).then((url) => {
                        if (url) {
                            const im = document.createElement('img');
                            im.src = url; im.draggable = false;
                            im.style.cssText = 'position:absolute;left:137px;top:8px;';
                            row.appendChild(im);
                        }
                    });
                } else {
                    const unset = document.createElement('div');
                    unset.textContent = '未设置';
                    unset.style.cssText = 'position:absolute;left:70px;top:12px;font-size:11px;color:#a8a090;';
                    row.appendChild(unset);
                }
            }
            this.stateSkillListEl.appendChild(row);
        }
    }

    // 点击技能：弹出快捷键设置对话框（原版 DKeySelDlg：背景 620，此资源用 Prguse[229] 替代）
    // 布局/逻辑对齐原版 FState.pas（1487-1551 布局、4685-4810 SetMagicKeyDlg/DKsF1Click/DKsOkClick）：
    //   - 点 F1-F8/None 只「选择」（高亮，不关闭）；点「确定」才应用并关闭
    //   - None = 清除（key=0）；确定 = 应用；取消 = 关闭不应用
    _setupSkillKey(magicId, sk) {
        const gui = this.manager.winManager.container;
        if (this.keySetupEl) { this.keySetupEl.remove(); this.keySetupEl = null; }
        const W = 376, H = 179;
        const box = document.createElement('div');
        box.style.cssText = `position:absolute;left:${Math.floor((SCREEN_WIDTH - W) / 2)}px;`
            + `top:${Math.floor((SCREEN_HEIGHT - H) / 2)}px;width:${W}px;height:${H}px;`
            + 'z-index:9999;font-family:SimSun,serif;color:#c0c0c0;font-size:12px;pointer-events:auto;';
        this.keySetupEl = box;

        // 当前选择（原版 MagKeyCurKey）：打开时 = 当前快捷键，点 F 键/None 改选择，点确定才发消息
        let selKey = sk.key || 0;

        // 背景 Prguse[229]
        this._libUrl('prguse', 229).then((url) => {
            if (url && this.keySetupEl === box) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                box.insertBefore(bg, box.firstChild);
            }
        });

        // 技能图标 MagIcon[Effect*2]（背景图标槽 @47,24 36x37，图标 32x32 居中放 @49,26）
        this._libUrl('magicon', sk.effect * 2).then((url) => {
            if (url && this.keySetupEl === box) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                im.style.cssText = 'position:absolute;left:49px;top:26px;width:32px;height:32px;';
                box.appendChild(im);
            }
        });

        // 提示文字（原版 DKeySelDlgDirectPaint TextOut @95,38 → 图标右侧 @91,31）
        const name = document.createElement('div');
        name.textContent = sk.name + '快捷键盘被设置为.';
        name.style.cssText = 'position:absolute;left:91px;top:31px;white-space:nowrap;color:#c0c0c0;';
        box.appendChild(name);

        // F1-F8 键（原版 DKsF1..F8 @25/57/89/121/160/192/224/256,78 → 229 槽位 @34/66/98/130/171/203/235/267,83）
        // 正常态透明、当前键高亮 idx+1、按下显示 idx（原版 DKsF1DirectPaint 语义）
        const F_LEFT = [34, 66, 98, 130, 171, 203, 235, 267];
        const F_TOP = 83;
        const fIms = [];
        const refreshKeys = () => {
            for (let k = 1; k <= 8; k++) {
                const im = fIms[k - 1];
                if (selKey === k) {
                    this._libUrl('prguse', 232 + (k - 1) * 2 + 1).then((url) => {
                        if (url && this.keySetupEl === box) { im.src = url; im.style.visibility = 'visible'; }
                    });
                } else {
                    im.style.visibility = 'hidden';
                }
            }
        };
        for (let k = 1; k <= 8; k++) {
            const idx = 232 + (k - 1) * 2;
            const btn = document.createElement('div');
            btn.style.cssText = `position:absolute;left:${F_LEFT[k - 1]}px;top:${F_TOP}px;width:29px;height:30px;cursor:pointer;`;
            const im = document.createElement('img');
            im.draggable = false;
            im.style.cssText = 'position:absolute;left:0;top:0;visibility:hidden;';
            btn.appendChild(im);
            fIms.push(im);
            btn.addEventListener('mousedown', () => {
                this._libUrl('prguse', idx).then((url) => {
                    if (url && this.keySetupEl === box) { im.src = url; im.style.visibility = 'visible'; }
                });
            });
            btn.addEventListener('mouseup', refreshKeys);
            btn.addEventListener('mouseleave', refreshKeys);
            btn.addEventListener('click', () => { selKey = k; refreshKeys(); });
            box.appendChild(btn);
        }
        refreshKeys();

        // None 按钮（原版 DKsNone @296,78 → 229 @299,83）：点击清除（key=0）
        const noneBtn = document.createElement('div');
        noneBtn.style.cssText = 'position:absolute;left:299px;top:83px;width:39px;height:30px;cursor:pointer;';
        noneBtn.title = '清除快捷键';
        noneBtn.addEventListener('click', () => { selKey = 0; refreshKeys(); });
        box.appendChild(noneBtn);

        // 确定按钮（原版 DKsOk @296,120 → 229 确定槽 @222,131）：应用选择并关闭
        const ok = document.createElement('div');
        ok.style.cssText = 'position:absolute;left:222px;top:131px;width:73px;height:30px;cursor:pointer;';
        ok.addEventListener('click', () => {
            this.conn.send('magicKeyChange', { magicId, key: selKey });
            this.keySetupEl.remove();
            this.keySetupEl = null;
        });
        box.appendChild(ok);

        // 取消按钮：关闭不应用（放在确定左侧）
        const cancel = document.createElement('div');
        cancel.textContent = '取消';
        cancel.style.cssText = 'position:absolute;left:150px;top:138px;cursor:pointer;color:#e8d9b0;';
        cancel.addEventListener('click', () => { this.keySetupEl.remove(); this.keySetupEl = null; });
        box.appendChild(cancel);

        gui.appendChild(box);
    }

    // 装备页（对齐原版 FState.pas DStateWinDirectPaint 的 StatePage=0）：
    //   1) 裸身底图 Prguse[376/377] 画在 (38,52)；
    //   2) 发型/衣服/武器/头盔画在 (31,96) + 纹理自带偏移，叠在人物身上——衣服才能真正盖住裸身；
    //   3) 13 个 DSW 槽位在原版只是空按钮（点击热区），不画图标，装备外观由人物形象体现。
    async _refreshEquipPage() {
        const p = this.self;
        this.stateBodyEl.innerHTML = '';
        // 装备页底图用 Prguse3[29]（男）/ [30]（女）：168×234，比 Prguse[376/377]（168×199）高，
        // 自带底部一排 4 个格子（护身符/腰带/靴子/宝石），装备页才完整。
        const bodyIdx = p.sex === 1 ? 30 : 29;
        this._libUrl('prguse3', bodyIdx).then((url) => {
            if (url && this.stateWin && this.stateBodyEl) {
                const im = document.createElement('img');
                im.src = url; im.draggable = false;
                this.stateBodyEl.appendChild(im);
            }
        });

        // 人物身上的装备叠加层（在裸身底图之上、点击热区之下）
        if (this.stateFigureEl) {
            this.stateFigureEl.remove();
            this.stateFigureEl = null;
        }
        const figure = document.createElement('div');
        figure.style.cssText = 'position:absolute;left:0;top:0;width:232px;height:325px;pointer-events:none;';
        this.stateEquipWrap.insertBefore(figure, this.stateEquipEl);
        this.stateFigureEl = figure;

        const items = p.items || [];
        const equipped = {};
        for (const it of items) {
            if (it.slot != null) equipped[it.slot] = it;
        }
        // 原版 bbx := Left+38-7 = 31，bby := Top+52+44 = 96，绘制时再叠加纹理自带 ax/ay
        const draw = async (libKey, idx) => {
            if (idx == null || idx < 0) return;
            const lib = getLib(libKey);
            if (!lib) return;
            let tex = null;
            try {
                tex = await lib.getTexture(idx);
            } catch (e) {
                tex = null;
            }
            if (!tex || !figure.isConnected) return;
            // WIL 解出来的是 OffscreenCanvas（没有 toDataURL），拷到普通 canvas 再挂 DOM
            const cv = document.createElement('canvas');
            cv.width = tex.canvas.width;
            cv.height = tex.canvas.height;
            cv.getContext('2d').drawImage(tex.canvas, 0, 0);
            cv.style.cssText = `position:absolute;left:${31 + (tex.px || 0)}px;top:${96 + (tex.py || 0)}px;`;
            figure.appendChild(cv);
        };
        // 顺序同原版：发型 → 衣服 → 武器 → 头盔
        await draw('prguse', (p.sex === 1 ? 480 : 440) + Math.floor((p.hair || 0) / 2));
        if (equipped[0]) await draw('stateitem', equipped[0].looks);   // 衣服
        if (equipped[1]) await draw('stateitem', equipped[1].looks);   // 武器
        if (equipped[4]) await draw('stateitem', equipped[4].looks);   // 头盔

        // 13 个槽位：点击热区；人物形象上已画衣服/武器/头盔（slot 0/1/4），
        // 其余槽位（项链/手镯/戒指/护身符/腰带/靴子/宝石/右手）显示图标，否则戴上后 F10 看不到。
        // 对齐原版：Prguse[376]（裸身底图）自带 8 个格子凹槽（右手/项链/头盔/手镯L/R/戒指L/R），
        // 只在这些槽位画图标；最后一排（护身符/腰带/靴子/宝石）在原版没有视觉（376 只到 y=251），
        // 保留 DSW 透明热区（可点击）但不画格子/图标。
        // Prguse3[29]/[30] 自带 8+4 个格子凹槽；头盔/衣服/武器仍画在人物形象上，其余槽位显示图标
        const ICON_SLOTS = new Set([2, 3, 5, 6, 7, 8, 9, 10, 11, 12]);
        this.stateEquipEl.innerHTML = '';
        this._hideStateItemDesc();
        for (const [slot, x, y, w, h] of EQUIP_SLOTS) {
            const it = equipped[slot];
            const cell = document.createElement('div');
            cell.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;cursor:pointer;`
                + 'display:flex;align-items:center;justify-content:center;overflow:hidden;';
            cell.dataset.slot = String(slot);
            cell.dataset.sfx = String(it ? itemClickSound(it.stdMode) : SFX.itemClick);
            if (it) {
                if (ICON_SLOTS.has(slot)) {
                    this._libUrl('stateitem', it.looks).then((url) => {
                        if (!url) return;
                        const im = document.createElement('img');
                        im.src = url; im.draggable = false;
                        im.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;';
                        cell.appendChild(im);
                    });
                }
                // 悬停在装备槽上时，描述显示在窗口底部框内（对齐原版把信息放这块区域的用法）
                cell.addEventListener('mouseenter', () => this._showStateItemDesc(it));
                cell.addEventListener('mouseleave', () => this._hideStateItemDesc());
            }
            cell.addEventListener('click', () => this._onEquipSlotClick(slot, it, cell));
            this.stateEquipEl.appendChild(cell);
        }
    }

    // 装备页底部描述：显示某件装备的详细信息（与背包描述同一套字段）
    _showStateItemDesc(it) {
        if (!this.stateDescEl) return;
        const d = this._itemDesc(it);
        this.stateDescEl.innerHTML = '';
        const l1 = document.createElement('div');
        const nm = document.createElement('span');
        nm.style.color = '#ffff00';
        nm.textContent = d.name + ' ';
        l1.appendChild(nm);
        l1.appendChild(document.createTextNode(d.line1));
        this.stateDescEl.appendChild(l1);
        if (d.line2) { const l2 = document.createElement('div'); l2.textContent = d.line2; this.stateDescEl.appendChild(l2); }
        if (d.line3) { const l3 = document.createElement('div'); l3.textContent = d.line3; this.stateDescEl.appendChild(l3); }
    }

    _hideStateItemDesc() {
        if (this.stateDescEl) this.stateDescEl.innerHTML = '';
    }

    // 点击 F10 装备页的某个槽位（原版 DSWWeaponClick 三分支）
    _onEquipSlotClick(slot, equipped, cell) {
        if (this.pickedItem) {
            if (this.pickedSource === 'equip') {
                // 手里拿的是已穿装备：原版直接放回原槽（本地还原，不发消息）
                this._clearPicked();
                return;
            }
            const sm = this.pickedItem.stdMode;
            if (!slotAccepts(slot, sm)) {
                return;    // 槽位不接受这类物品：原版静默忽略
            }
            if (sm === 10 && this.self.sex !== 0) { this._log('男性服饰。'); return; }
            if (sm === 11 && this.self.sex !== 1) { this._log('女性服饰。'); return; }
            this.conn.send('takeOnItem', { makeIndex: this.pickedItem.makeIndex, slot });
            this._clearPicked();
            return;
        }
        if (equipped) {
            // 空手点有装备的槽位 → 拿起（原版 MovingItem.Index := -(sel+1)）
            this.pickedItem = equipped;
            this.pickedSource = 'equip';
            this.pickedSlot = slot;
            this._markPicked(cell);
        }
    }

    // 属性页：只显示属性数值（纯数字，标签由背景图提供；经验/金币由背景图自带，无需重复）
    _refreshAttrPage() {
        const p = this.self;
        this.stateAttrEl.innerHTML = '';
        for (const [field, y] of ATTR_ROWS) {
            const div = document.createElement('div');
            div.style.cssText = `position:absolute;left:0;top:${y - 99}px;`;
            div.textContent = field === 'hp' ? `${p.hp}/${p.maxHp}`
                : field === 'mp' ? `${p.mp}/${p.maxMp}`
                : (p[field] || '0-0');
            this.stateAttrEl.appendChild(div);
        }
    }

    // 属性数值页（原版 StatePage=2）：经验/重量/负重/腕力/精确度/敏捷度 + 防御恢复（当前未实现置 0）
    _refreshAttr2Page() {
        const p = this.self;
        this.stateAttr2El.innerHTML = '';
        const rows = [
            ['当前经验', `${p.exp || 0}`],
            ['升级经验', `${p.maxExp || 0}`],
            ['背包重量', `${p.weight || 0}/${p.maxWeight || 0}`],
            ['负重量', `${p.wearWeight || 0}/${p.maxWearWeight || 0}`],
            ['腕力', `${p.handWeight || 0}/${p.maxHandWeight || 0}`],
            ['精确度', `${p.hit || 0}`],
            ['敏捷度', `${p.speed || 0}`],
            ['魔法防御', `+${(p.antiMagic || 0) * 10}%`],
            ['中毒防御', `+${(p.antiPoison || 0) * 10}%`],
            ['中毒恢复', `+${(p.poisonRecover || 0) * 10}%`],
            ['体力恢复', `+${(p.healthRecover || 0) * 10}%`],
            ['魔法恢复', `+${(p.spellRecover || 0) * 10}%`],
        ];
        rows.forEach(([label, val], i) => {
            const line = document.createElement('div');
            line.style.cssText = `position:absolute;left:0;top:${i * 14}px;white-space:nowrap;`;
            const l = document.createElement('span');
            l.textContent = label;
            line.appendChild(l);
            const v = document.createElement('span');
            v.textContent = val;
            v.style.cssText = 'position:absolute;left:85px;';
            line.appendChild(v);
            this.stateAttr2El.appendChild(line);
        });
    }

    // ---- 背包（F10，原版 DItemBag）----

    _toggleBag() {
        if (!this.bagPanel) this._buildBag();
        const open = this.bagPanel.style.display === 'block';
        this.bagPanel.style.display = open ? 'none' : 'block';
        if (!open) this._refreshBag();
    }

    _buildBag() {
        if (this.bagPanel) return;
        const gui = this.manager.winManager.container;
        const panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;left:0;top:0;width:336px;height:270px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        panel.style.display = 'none';
        // 背景 Prguse[3]（标记 win-drag 用于拖动）
        this._libUrl('prguse', 3).then((url) => {
            if (url && this.bagPanel === panel) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.className = 'win-drag';
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                panel.insertBefore(bg, panel.firstChild);
            }
        });
        // 关闭按钮（红X，原版 DCloseBag 309,203）
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:309px;top:203px;width:14px;height:20px;cursor:pointer;';
        close.addEventListener('click', () => { panel.style.display = 'none'; });
        panel.appendChild(close);
        // 金币（原版 DGold 10,190）
        // 金币（原版 DItemBagDirectPaint：TextOut(Left+64, Top+185, GetGoldStr) 白色千分位）
        this.goldEl = document.createElement('div');
        this.goldEl.style.cssText = 'position:absolute;left:64px;top:185px;font-size:12px;color:#ffffff;text-shadow:1px 1px 0 #000;';
        panel.appendChild(this.goldEl);
        // 物品描述区（原版 DItemBagDirectPaint：鼠标悬停物品在 Left+70,Top+215 起显示 3 行描述）
        this.bagDescEl = document.createElement('div');
        this.bagDescEl.style.cssText = 'position:absolute;left:70px;top:215px;width:250px;'
            + 'font-size:12px;line-height:14px;color:#ffffff;text-shadow:1px 1px 0 #000;pointer-events:none;';
        panel.appendChild(this.bagDescEl);
        // 背包物品格子（原版 DItemGrid 20,13 286x162；TDGrid ColCount=8 RowCount=5 ColWidth=36 RowHeight=32）
        this.bagGridEl = document.createElement('div');
        this.bagGridEl.style.cssText = 'position:absolute;left:20px;top:13px;width:286px;height:162px;'
            + 'display:grid;grid-template-columns:repeat(8,36px);grid-template-rows:repeat(5,32px);'
            + 'grid-auto-rows:32px;grid-auto-columns:36px;row-gap:0;column-gap:0;';
        panel.appendChild(this.bagGridEl);
        // 拿着已穿装备点背包空白处 → 卸下（原版 DItemGridGridSelect 的 mi<0 分支；
        // 网格只渲染有物品的格子，空白区域用容器兜底）
        this.bagGridEl.addEventListener('click', (e) => {
            if (e.target !== this.bagGridEl) return;
            if (this.pickedItem && this.pickedSource === 'equip') {
                this.conn.send('takeOffItem', { slot: this.pickedSlot, makeIndex: this.pickedItem.makeIndex });
                this._clearPicked();
                return;
            }
            if (this.pickedItem && this.pickedSource === 'quick') {
                // 拿着快捷栏物品点背包空白处 → 从快捷栏取出（物品回到背包网格）
                this.conn.send('quickSlot', { slot: this.pickedSlot, makeIndex: 0 });
                this._clearPicked();
            }
        });
        this.bagPanel = panel;
        this._makeWindowDraggable(panel);
        gui.appendChild(panel);
    }

    _refreshBag() {
        if (!this.bagPanel || !this.self) return;
        // 原版 GetGoldStr：千分位逗号，无「金币」前缀
        this.goldEl.textContent = this._formatGold(this.self.gold || 0);
        const items = this.self.items || [];

        // 背包物品（8 列网格，图标居中）：双击使用、单击拿起（原版 GetItemCursor 交互）
        this.bagGridEl.innerHTML = '';
        this._clearPicked();   // 同时清 pickedItem：解包/买卖等刷新后，避免残留旧引用触发误换位
        // 快捷栏里的物品从背包网格里「移出」（原版 ItemArr[0..5] 是独立于背包 6..45 的槽，
        // 物品是从背包移进快捷栏的，所以背包里看不到它；服务端 bag 不变，这里只是视图过滤）
        const quickSet = new Set((this.self.quickSlots || []).filter(v => v > 0));
        const bagItems = items.filter(x => x.slot === undefined && !quickSet.has(x.makeIndex));
        // 网格只显示 8×5=40 格（原版 ItemArr[6..45]），第 41-46 个在溢出区（ItemArr[46..51]，
        // 原版 DItemGrid 不渲染）——服务端容量 MAXBAGITEM=46，用完网格物品后溢出区自动前移
        const visibleItems = bagItems.slice(0, 40);
        for (const it of visibleItems) {
            const cell = document.createElement('div');
            // 格子显式固定尺寸（36×32，对齐原版 TDGrid ColWidth=36/RowHeight=32）；grid place-items 居中
            cell.style.cssText = 'display:grid;place-items:center;overflow:hidden;cursor:pointer;'
                + 'width:36px;height:32px;';
            cell.title = it.name;
            cell.dataset.makeIndex = String(it.makeIndex);
            cell.dataset.sfx = String(itemClickSound(it.stdMode));   // SoundUtil.ItemClickSound
            cell.addEventListener('mouseenter', () => this._showItemDesc(it));
            cell.addEventListener('mouseleave', () => this._hideItemDesc());
            this._libUrl('items', it.looks).then((url) => {
                if (url) {
                    const im = document.createElement('img');
                    im.src = url; im.draggable = false;
                    im.style.cssText = 'max-width:34px;max-height:30px;';
                    cell.appendChild(im);
                }
            });
            // 双击：装备直接穿戴（自动选槽，原版 GetTakeOnPosition）；其余交给 useItem（药/书/卷，原版 EatItem）
            const pos = TAKEON_POS[it.stdMode];
            cell.addEventListener('dblclick', () => {
                if (pos !== undefined) {
                    this.conn.send('takeOnItem', { makeIndex: it.makeIndex, slot: pos });
                } else {
                    this.conn.send('useItem', { makeIndex: it.makeIndex });
                    // 原版 ItemUseSound：只有 StdMode 0/1/2 出声（108/107），
                    // 解捆(31)/技能书(4)/卷轴(3) 原版是静默的 → 返回 0 就不调 play。
                    const sfx = itemUseSound(it.stdMode);
                    if (sfx) sound.play(sfx);
                }
            });
            cell.addEventListener('click', () => this._onBagItemClick(it, cell));
            this.bagGridEl.appendChild(cell);
        }
    }

    // 金币千分位格式化（原版 GetGoldStr：每 3 位逗号）
    _formatGold(n) {
        return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    }

    // 物品描述（对齐原版 GetMouseItemInfo 按 StdMode 分类）
    _itemDesc(it) {
        const std = it.stdMode || 0;
        let line1 = '', line2 = '', line3 = '';
        if (std === 0) {
            // 药品：+HP +MP 重量
            if (it.ac > 0) line1 += '+' + it.ac + 'HP ';
            if (it.mac > 0) line1 += '+' + it.mac + 'MP ';
            if (it.weight) line1 += '重量' + it.weight;
        } else if (std <= 4) {
            // 消耗品/技能书：重量（+ 书需要等级）
            if (it.weight) line1 += '重量' + it.weight;
            if (std === 4 && it.needLevel) line3 += '需要等级 ' + it.needLevel;
        } else if ([5, 6, 10, 11, 15, 19, 20, 21, 22, 23, 24, 26, 30, 52, 54].includes(std)) {
            // 装备：重量 + 持久 + 攻击/魔法/道术/防御/魔御 + 需要等级
            if (it.weight) line1 += '重量' + it.weight;
            if (it.durabilityMax > 0) {
                line1 += (line1 ? '  ' : '') + '持久 ' + Math.round(it.durability / 1000) + '/' + Math.round(it.durabilityMax / 1000);
            }
            if (it.dcMax) line2 += '攻击 ' + (it.dc || 0) + '-' + it.dcMax + ' ';
            if (it.mcMax) line2 += '魔法 ' + (it.mc || 0) + '-' + it.mcMax + ' ';
            if (it.scMax) line2 += '道术 ' + (it.sc || 0) + '-' + it.scMax + ' ';
            if (it.acMax) line2 += '防御 ' + (it.ac || 0) + '-' + it.acMax + ' ';
            if (it.macMax) line2 += '魔御 ' + (it.mac || 0) + '-' + it.macMax + ' ';
            if (it.needLevel) line3 += '需要等级 ' + it.needLevel;
        } else {
            // 其他（食物 std=40 等）：只显示重量
            if (it.weight) line1 += '重量' + it.weight;
        }
        return { name: it.name, line1, line2, line3 };
    }

    _showItemDesc(it) {
        if (!this.bagDescEl) return;
        const d = this._itemDesc(it);
        this.bagDescEl.innerHTML = '';
        const line1 = document.createElement('div');
        const nameSpan = document.createElement('span');
        nameSpan.style.color = '#ffff00';
        nameSpan.textContent = d.name + ' ';
        line1.appendChild(nameSpan);
        line1.appendChild(document.createTextNode(d.line1));
        this.bagDescEl.appendChild(line1);
        if (d.line2) { const l2 = document.createElement('div'); l2.textContent = d.line2; this.bagDescEl.appendChild(l2); }
        if (d.line3) { const l3 = document.createElement('div'); l3.textContent = d.line3; this.bagDescEl.appendChild(l3); }
    }

    _hideItemDesc() {
        if (this.bagDescEl) this.bagDescEl.innerHTML = '';
    }

    // 背包物品单击：拿起 → 再点同一格取消 / 点另一格换位（移动）/ 拿着已穿装备点背包 = 卸下
    _onBagItemClick(it, cell) {
        // 仓库存入模式：点背包物品直接存（原版 dmStorage 分支 FState.pas:5454，
        // 不走「拿起→投放」流程）
        if (this.storageMode === 'deposit') {
            this.conn.send('storageStore', { npcId: this.storageNpcId, makeIndex: it.makeIndex });
            return;
        }
        if (!this.pickedItem) {
            this.pickedItem = it;            // 拿起
            this.pickedSource = 'bag';
            this.pickedSlot = -1;
            this._markPicked(cell);
            return;
        }
        if (this.pickedSource === 'equip') {
            // 拿着已穿装备点背包格 → 卸下（原版 DItemGridGridSelect 的 mi < 0 分支 → CM_TAKEOFFITEM）
            this.conn.send('takeOffItem', { slot: this.pickedSlot, makeIndex: this.pickedItem.makeIndex });
            this._clearPicked();
            return;
        }
        if (this.pickedSource === 'quick') {
            // 拿着快捷栏物品点背包格 → 从快捷栏取出（清引用，物品回到背包网格）
            this.conn.send('quickSlot', { slot: this.pickedSlot, makeIndex: 0 });
            this._clearPicked();
            return;
        }
        if (this.pickedItem.makeIndex === it.makeIndex) {
            this._clearPicked();             // 点同一物品：放下（取消）
            return;
        }
        // 点另一物品：换位
        this.conn.send('moveItem', { makeIndex: this.pickedItem.makeIndex, targetIndex: it.makeIndex });
        this._clearPicked();
    }

    _markPicked(cell) {
        if (this._pickedCell && this._pickedCell !== cell) this._pickedCell.style.outline = '';
        this._pickedCell = cell;
        cell.style.outline = '2px solid #ffe400';
    }

    _clearPicked() {
        if (this._pickedCell) this._pickedCell.style.outline = '';
        this._pickedCell = null;
        this.pickedItem = null;
        this.pickedSource = null;
        this.pickedSlot = -1;
    }

    // ---- NPC 对话框（原版 DMerchantDlg 样式）----

    // 任意 WIL 库图 → img URL（缓存）
    async _libUrl(libKey, idx) {
        if (this._texCache == null) this._texCache = new Map();
        const key = libKey + ':' + idx;
        if (this._texCache.has(key)) return this._texCache.get(key);
        const lib = getLib(libKey);
        if (!lib) return null;
        const tex = await lib.getTexture(idx);
        if (!tex) return null;
        const url = tex.canvas.toDataURL
            ? tex.canvas.toDataURL('image/png')
            : URL.createObjectURL(await tex.canvas.convertToBlob());
        this._texCache.set(key, url);
        return url;
    }

    // Prguse 图 → img URL（缓存）
    _prgUrl(idx) {
        return this._libUrl('prguse', idx);
    }

    _showNpcDialog(msg) {
        this._closeNpcDialog();
        // 非买卖对话（如点「返回」回主界面）时关闭买卖面板，避免残留
        if (!msg.shop) this._closeShopPanel();
        // 记录打开时玩家位置（原版 MDlgX/MDlgY），走远自动关闭
        this._npcDlgX = this.self.x;
        this._npcDlgY = this.self.y;
        const gui = this.manager.winManager.container;
        const dlg = document.createElement('div');
        dlg.style.cssText = 'position:absolute;left:0;top:0;width:416px;height:176px;pointer-events:auto;';
        this.npcDlg = dlg;
        this._makeWindowDraggable(dlg);
        gui.appendChild(dlg);

        // 背景 Prguse[384]（商人对话框 416×176，异步插入不阻塞文本）
        // 注意：不能用 z-index:-1，会被 #screen canvas 遮挡；背景图先插入、内容后追加即可盖在其上
        this._prgUrl(384).then((bgUrl) => {
            if (bgUrl && this.npcDlg === dlg) {
                const bg = document.createElement('img');
                bg.src = bgUrl;
                bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                dlg.insertBefore(bg, dlg.firstChild);
            }
        });

        // NPC 说话文本（白描边，从 30,20 开始；原版 lx=30 ly=20，字体 9号≈12px、行高 16px）
        // 不设 overflow:hidden：原版 canvas 逐行绘制不裁剪，长文本（如公约多页）末行「下一页」会溢出对话框仍可见可点
        const text = document.createElement('div');
        text.style.cssText = 'position:absolute;left:30px;top:20px;width:340px;'
            + 'font-family:SimSun,serif;font-size:12px;color:#ffffff;line-height:16px;'
            + 'text-shadow:1px 1px 0 #000;white-space:pre-wrap;';
        // 说话文本：解析 <label@value> 为内联黄色链接（原版 DMerchantDlgDirectPaint 语义）
        this._renderNpcSay(text, msg.text || '', msg.npcId);
        dlg.appendChild(text);

        // 买卖面板（原版 DSellDlg 独立窗口，替代挤在对话里的列表）
        if (msg.shop === 'buy' || msg.shop === 'sell') {
            this._showShopPanel(msg);
        }

        // 关闭按钮 Prguse[64]（399,1）：正常态红X画在背景图 384 里；透明热区接收点击，
        // img（按下态）按下才显示、抬起恢复（原版 DccCloseDirectPaint 的 Downed 语义）
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:399px;top:1px;width:16px;height:23px;cursor:pointer;';
        close.addEventListener('click', () => this.conn.send('npcSelect', { npcId: msg.npcId, value: 'exit' }));
        dlg.appendChild(close);
        this._prgUrl(64).then((closeUrl) => {
            if (closeUrl && this.npcDlg === dlg) {
                const img = document.createElement('img');
                img.src = closeUrl;
                img.draggable = false;
                img.style.cssText = 'visibility:hidden;display:block;';
                close.appendChild(img);
                close.addEventListener('mousedown', () => { img.style.visibility = 'visible'; });
                const restore = () => { img.style.visibility = 'hidden'; };
                close.addEventListener('mouseup', restore);
                close.addEventListener('mouseleave', restore);
            }
        });
    }

    // 买卖界面（原版：buy=DMenuDlg 物品列表+翻页+买入；sell=DSellDlg ok 框投放）
    _showShopPanel(msg) {
        this._closeShopPanel();
        this.shopNpcId = msg.npcId;
        if (msg.shop === 'buy') {
            this._showBuyMenu(msg);
        } else {
            this._showSellDlg(msg);
        }
    }

    // 购买：物品列表（原版 DMenuDlg，在 NPC 对话下方）+ 上下翻页 + 买入按钮
    _showBuyMenu(msg) {
        this.shopGoods = msg.goods || [];
        this.shopPage = 0;      // 列表起始行
        this.shopSel = -1;      // 选中商品全局索引
        const gui = this.manager.winManager.container;
        const panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;left:0;top:176px;width:308px;height:205px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        this.shopPanel = panel;
        this._makeWindowDraggable(panel);
        this._libUrl('prguse', 385).then((url) => {
            if (url && this.shopPanel === panel) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                panel.insertBefore(bg, panel.firstChild);
            }
        });
        // 标题「物品列表」(19,9) /「价格」(156,9)——原版 DMenuDlgDirectPaint 坐标，y 上移 2px
        const title = document.createElement('div');
        title.style.cssText = 'position:absolute;left:19px;top:9px;font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;white-space:nowrap;';
        title.textContent = '物品列表';
        panel.appendChild(title);
        const priceTitle = document.createElement('div');
        priceTitle.style.cssText = 'position:absolute;left:156px;top:9px;font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;white-space:nowrap;';
        priceTitle.textContent = '价格';
        panel.appendChild(priceTitle);
        // 第三列「持久」（原版 DMenuDlgDirectPaint：标题 SX(249)，行内 SX(255) 画 Grade）
        const duraTitle = document.createElement('div');
        duraTitle.style.cssText = 'position:absolute;left:241px;top:9px;font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;white-space:nowrap;';
        duraTitle.textContent = '持久';
        panel.appendChild(duraTitle);
        // 列表（y=30 起，保持当前位置，不挤标题）
        this.shopListEl = document.createElement('div');
        this.shopListEl.style.cssText = 'position:absolute;left:19px;top:30px;width:270px;height:138px;'
            + 'font-size:12px;line-height:13px;text-shadow:1px 1px 0 #000;';
        panel.appendChild(this.shopListEl);
        // 热区按钮（背景 385 已画正常态：箭头/买入/关闭；img 为按下态，按下显示抬起隐藏）
        const mkBtn = (idx, x, y, w, h, cb) => {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;cursor:pointer;`;
            b.addEventListener('click', cb);
            this._libUrl('prguse', idx).then((url) => {
                if (url) {
                    const im = document.createElement('img');
                    im.src = url; im.draggable = false;
                    im.style.cssText = 'visibility:hidden;display:block;';
                    b.appendChild(im);
                    b.addEventListener('mousedown', () => { im.style.visibility = 'visible'; });
                    const restore = () => { im.style.visibility = 'hidden'; };
                    b.addEventListener('mouseup', restore);
                    b.addEventListener('mouseleave', restore);
                }
            });
            panel.appendChild(b);
        };
        mkBtn(388, 43, 175, 24, 24, () => this._shopPageTurn(-9));
        mkBtn(387, 90, 175, 24, 24, () => this._shopPageTurn(9));
        mkBtn(386, 215, 171, 44, 16, () => this._buySelected(msg.npcId));
        mkBtn(64, 291, 0, 16, 23, () => this._closeShopPanel());
        gui.appendChild(panel);
        this._renderBuyList();
    }

    _renderBuyList() {
        if (!this.shopListEl) return;
        this.shopListEl.innerHTML = '';
        const max = 10;
        const start = this.shopPage;
        const goods = this.shopGoods;
        for (let i = 0; i < max; i++) {
            const idx = start + i;
            if (idx >= goods.length) break;
            const g = goods[idx];
            const row = document.createElement('div');
            row.style.cssText = `position:relative;height:13px;cursor:pointer;color:${idx === this.shopSel ? '#ff4444' : '#fff'};`;
            row.innerHTML = `<span style="position:absolute;left:0;">${idx === this.shopSel ? '▶' : ''}${g.name}</span>`
                + `<span style="position:absolute;left:137px;color:#fff;">${g.price}金币</span>`
                // 持久列：原版 pcg.Grade 恒为 -1（商店货物没有耐久），所以固定画 '-'
                + `<span style="position:absolute;left:222px;color:#fff;">-</span>`;
            // 原版点商品行播 s_glass_button_click(105)（FState.pas:5177）
            row.dataset.sfx = String(SFX.buttonGlass);
            row.addEventListener('click', () => { this.shopSel = idx; this._renderBuyList(); });
            this.shopListEl.appendChild(row);
        }
    }

    _shopPageTurn(delta) {
        const max = 10;
        const np = this.shopPage + delta;
        if (np < 0) return;
        if (np >= this.shopGoods.length) return;
        this.shopPage = np;
        this._renderBuyList();
    }

    _buySelected(npcId) {
        if (this.shopSel < 0 || this.shopSel >= this.shopGoods.length) return;
        const g = this.shopGoods[this.shopSel];
        this.conn.send('buyItem', { npcId, index: g.index, count: 1 });
    }

    // 卖出：ok 框（原版 DSellDlg 投放框）+ 背包物品列表
    _showSellDlg(msg) {
        this.shopSellItem = null;   // 选中要卖的物品
        const gui = this.manager.winManager.container;
        const panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;left:260px;top:176px;width:140px;height:181px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        this.shopPanel = panel;
        this._makeWindowDraggable(panel);
        this._libUrl('prguse', 392).then((url) => {
            if (url && this.shopPanel === panel) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                panel.insertBefore(bg, panel.firstChild);
            }
        });
        // 标题「价格: N 金币」（原版 DSellDlgDirectPaint：dmSell → '价格: ' + g_sSellPriceStr）
        this.shopPriceEl = document.createElement('div');
        this.shopPriceEl.style.cssText = 'position:absolute;left:8px;top:6px;font-size:12px;text-shadow:1px 1px 0 #000;';
        this.shopPriceEl.textContent = '价格: ';
        panel.appendChild(this.shopPriceEl);
        // 投放框 Spot (27,67) 61x52：拿起物品后点这里放入要卖的物品
        const spot = document.createElement('div');
        spot.style.cssText = 'position:absolute;left:27px;top:67px;width:61px;height:52px;'
            + 'display:flex;align-items:center;justify-content:center;overflow:hidden;cursor:pointer;';
        spot.title = '拿起物品后点这里放入要卖的物品';
        spot.addEventListener('click', () => {
            if (this.pickedItem) {
                this._pickSellItem(this.pickedItem);
                this._clearPicked();
            }
        });
        this.shopSpotEl = spot;
        panel.appendChild(spot);
        // OK 按钮（原版 393 按下态，背景已画 OK）+ 关闭（背景已画红X）——热区模式
        const mkBtn = (idx, x, y, w, h, cb) => {
            const b = document.createElement('div');
            b.style.cssText = `position:absolute;left:${x}px;top:${y}px;width:${w}px;height:${h}px;cursor:pointer;`;
            b.addEventListener('click', cb);
            this._libUrl('prguse', idx).then((url) => {
                if (url) {
                    const im = document.createElement('img');
                    im.src = url; im.draggable = false;
                    im.style.cssText = 'visibility:hidden;display:block;';
                    b.appendChild(im);
                    b.addEventListener('mousedown', () => { im.style.visibility = 'visible'; });
                    const restore = () => { im.style.visibility = 'hidden'; };
                    b.addEventListener('mouseup', restore);
                    b.addEventListener('mouseleave', restore);
                }
            });
            panel.appendChild(b);
        };
        mkBtn(393, 85, 150, 24, 13, () => this._sellOk(msg.npcId));
        mkBtn(64, 115, 0, 16, 23, () => this._closeShopPanel());
        gui.appendChild(panel);

        // 卖出模式：打开原版背包（右侧 475,0，原版 DItemBag 卖出位置），背包物品点击放入投放框
        this.shopSellMode = true;
        if (!this.bagPanel) this._buildBag();
        this.bagPanel.style.display = 'block';
        this.bagPanel.style.left = '475px';
        this.bagPanel.style.top = '0px';
        this._refreshBag();
    }

    _pickSellItem(it) {
        this.shopSellItem = it;
        // 投放框显示物品图标
        this.shopSpotEl.innerHTML = '';
        if (it.looks > 0) {
            this._libUrl('items', it.looks).then((url) => {
                if (url) {
                    const im = document.createElement('img');
                    im.src = url; im.draggable = false;
                    im.style.cssText = 'max-width:100%;max-height:100%;object-fit:contain;';
                    this.shopSpotEl.appendChild(im);
                }
            });
        }
        // 原版放进投放框后立刻问价（ClMain.pas:4085 g_boQueryPrice → SendQueryPrice），
        // 回来之前先显示物品名占位，问到价格后覆盖成「价格: N 金币」。
        this.shopPriceEl.textContent = '价格: …';
        this.conn.send('querySellPrice', { npcId: this.shopNpcId, makeIndex: it.makeIndex });
    }

    _sellOk(npcId) {
        if (!this.shopSellItem) return;
        this.conn.send('sellItem', { npcId, makeIndex: this.shopSellItem.makeIndex });
    }

    _clearSellSpot() {
        this.shopSellItem = null;
        if (this.shopSpotEl) this.shopSpotEl.innerHTML = '';
        if (this.shopPriceEl) this.shopPriceEl.textContent = '价格: ';
    }

    _closeShopPanel() {
        if (this.shopPanel) { this.shopPanel.remove(); this.shopPanel = null; }
        if (this.shopBagListEl) { this.shopBagListEl.remove(); this.shopBagListEl = null; }
        this.shopGoldEl = null;
        this.shopSellMode = false;
        this.shopSellItem = null;
        this.storageMode = null;
        this.storageNpcId = -1;
        // 恢复背包位置（卖出/存入时移到右侧）
        if (this.bagPanel) {
            this.bagPanel.style.left = '0px';
            this.bagPanel.style.top = '0px';
            this.bagPanel.style.display = 'none';
        }
    }

    // ---- 仓库（原版 dmStorage / BoStorageMenu 两套窗口，FState.pas） ----

    /** 存入窗口（原版 @storage → SM_SENDUSERSTORAGEITEM → dmStorage 模式的
     * DSellDlg：投放框只是视觉提示，点背包物品直接 SendStorageItem，
     * FState.pas:5454）。 */
    _showStorageDlg(msg) {
        this._closeShopPanel();
        this.storageMode = 'deposit';
        this.storageNpcId = msg.npcId;
        const gui = this.manager.winManager.container;
        const panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;left:260px;top:176px;width:140px;height:181px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        this.shopPanel = panel;
        this._makeWindowDraggable(panel);
        this._libUrl('prguse', 392).then((url) => {
            if (url && this.shopPanel === panel) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                panel.insertBefore(bg, panel.firstChild);
            }
        });
        const title = document.createElement('div');
        title.style.cssText = 'position:absolute;left:8px;top:6px;font-size:12px;text-shadow:1px 1px 0 #000;';
        title.textContent = '仓库保管';
        panel.appendChild(title);
        // 投放框（27,67)61x52：视觉提示（原版 dmStorage 下点背包物品直接存入，
        // 不经过投放框；这里保留框体与原版窗口外观一致）
        const spot = document.createElement('div');
        spot.style.cssText = 'position:absolute;left:27px;top:67px;width:61px;height:52px;'
            + 'display:flex;align-items:center;justify-content:center;overflow:hidden;';
        spot.title = '点击背包里的物品存入仓库';
        panel.appendChild(spot);
        // 关闭（背景已画红X，热区 115,0）
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:115px;top:0px;width:16px;height:23px;cursor:pointer;';
        close.addEventListener('click', () => this._closeShopPanel());
        panel.appendChild(close);
        gui.appendChild(panel);
        if (!this.bagPanel) this._buildBag();
        this.bagPanel.style.display = 'block';
        this.bagPanel.style.left = '475px';
        this.bagPanel.style.top = '0px';
        this._refreshBag();
    }

    /** 取出列表（原版 @getback → SM_SAVEITEMLIST → ShowShopMenuDlg +
     * BoStorageMenu=TRUE：物品行点击直接 SendTakeBackStorageItem，
     * FState.pas:5236）。 */
    _showStorageList(msg) {
        this._closeShopPanel();
        this.storageMode = 'getback';
        this.storageNpcId = msg.npcId != null ? msg.npcId : this.storageNpcId;
        const gui = this.manager.winManager.container;
        const panel = document.createElement('div');
        panel.style.cssText = 'position:absolute;left:0;top:176px;width:308px;height:205px;'
            + 'pointer-events:auto;font-family:SimSun,serif;color:#e8d9b0;';
        this.shopPanel = panel;
        this._makeWindowDraggable(panel);
        this._libUrl('prguse', 385).then((url) => {
            if (url && this.shopPanel === panel) {
                const bg = document.createElement('img');
                bg.src = url; bg.draggable = false;
                bg.style.cssText = 'position:absolute;left:0;top:0;';
                panel.insertBefore(bg, panel.firstChild);
            }
        });
        const title = document.createElement('div');
        title.style.cssText = 'position:absolute;left:19px;top:9px;font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;';
        title.textContent = '仓库物品';
        panel.appendChild(title);
        const duraTitle = document.createElement('div');
        duraTitle.style.cssText = 'position:absolute;left:222px;top:9px;font-size:12px;color:#fff;text-shadow:1px 1px 0 #000;';
        duraTitle.textContent = '持久';
        panel.appendChild(duraTitle);
        const list = document.createElement('div');
        list.style.cssText = 'position:absolute;left:19px;top:30px;width:270px;height:138px;'
            + 'font-size:12px;line-height:13px;text-shadow:1px 1px 0 #000;';
        panel.appendChild(list);
        const items = msg.items || [];
        for (const it of items) {
            const row = document.createElement('div');
            row.style.cssText = 'position:relative;height:13px;cursor:pointer;color:#fff;';
            row.innerHTML = `<span style="position:absolute;left:0;">${it.name}</span>`
                + `<span style="position:absolute;left:222px;color:#fff;">${Math.round((it.durability || 0) / 1000)}/${Math.round((it.durabilityMax || 0) / 1000)}</span>`;
            row.title = '点击取回该物品';
            row.dataset.sfx = String(SFX.buttonGlass);
            const npcId = this.storageNpcId;
            row.addEventListener('click', () => {
                this.conn.send('storageTake', { npcId, makeIndex: it.makeIndex });
            });
            list.appendChild(row);
        }
        const close = document.createElement('div');
        close.style.cssText = 'position:absolute;left:291px;top:0px;width:16px;height:23px;cursor:pointer;';
        close.addEventListener('click', () => this._closeShopPanel());
        panel.appendChild(close);
        gui.appendChild(panel);
    }

    // 解析 NPC 说话文本：<label@value> 渲染为内联黄色下划线链接，其余为普通文本
    // （对齐原版 DMerchantDlgDirectPaint 的 <cmdstr@cmdparam> 语义）
    _renderNpcSay(container, sayText, npcId) {
        let i = 0;
        const appendText = (s) => {
            if (!s) return;
            const span = document.createElement('span');
            span.textContent = s;
            container.appendChild(span);
        };
        while (i < sayText.length) {
            const lt = sayText.indexOf('<', i);
            if (lt < 0) { appendText(sayText.slice(i)); break; }
            const gt = sayText.indexOf('>', lt);
            if (gt < 0) { appendText(sayText.slice(i)); break; }
            appendText(sayText.slice(i, lt));
            const inner = sayText.slice(lt + 1, gt);
            const at = inner.indexOf('@');
            if (at >= 0) {
                // 原脚本格式 <label/@value>，label 末尾的 '/' 是分隔符，去掉
                const label = inner.slice(0, at).replace(/[\/ ]+$/, '');
                const value = inner.slice(at);   // @xxx
                const a = document.createElement('a');
                a.textContent = label;
                a.style.cssText = 'color:#ffd76a;text-decoration:underline;cursor:pointer;'
                    + 'text-shadow:1px 1px 0 #000;pointer-events:auto;';
                // 原版 NPC 对话选项点击播 s_glass_button_click(105)（FState.pas:5326）
                a.dataset.sfx = String(SFX.buttonGlass);
                a.addEventListener('click', () => this.conn.send('npcSelect', { npcId, value }));
                container.appendChild(a);
            } else {
                appendText(inner);
            }
            i = gt + 1;
        }
    }

    _closeNpcDialog() {
        if (this.npcDlg) { this.npcDlg.remove(); this.npcDlg = null; }
        this._npcDlgX = null;
        this._closeShopPanel();   // 关闭对话框时同步关闭买卖面板（买列表 / 卖 ok 框）
    }

    update() {}

    render(ctx) {
        if (!this.self) return;
        if (!this.mapActor.map) {
            // 地图 / 首屏纹理仍在加载：不清屏，保留上一帧（back buffer 里还是上一帧），
            // 只叠一条不透明提示。避免「每帧全黑清屏 → 纹理一块块冒出来」的闪屏。
            ctx.save();
            ctx.fillStyle = '#000';
            ctx.fillRect(0, 0, 260, 26);
            ctx.fillStyle = '#ffd76a';
            ctx.font = '14px SimSun,serif';
            ctx.textAlign = 'left';
            ctx.fillText(this._mapLoadingText || '正在加载地图…', 10, 18);
            if (this._mapLoadError) {
                ctx.fillStyle = 'rgba(0,0,0,.85)';
                ctx.fillRect(0, 270, 800, 60);
                ctx.fillStyle = '#ff6b6b';
                ctx.font = '14px SimSun,serif';
                ctx.textAlign = 'center';
                ctx.fillText('地图加载失败：' + this._mapLoadError, 400, 296);
                ctx.fillStyle = '#e8d9b0';
                ctx.fillText('请把此截图发给开发者，或刷新页面重试', 400, 318);
            }
            ctx.restore();
            return;
        }
        // 每帧清屏也不再用纯黑：地面层万一有一帧没盖满，露出来的是土地暗色而不是黑条。
        ctx.fillStyle = FLOOR_FALLBACK;
        ctx.fillRect(0, 0, SCREEN_WIDTH, SCREEN_HEIGHT);

        const now = Date.now();
        // 按住持续朝当前方向移动；_pendingStep 让「快点一下」也能走一步。
        // 两种来源共用 _lastMoveAt 节流，所以快速连点只会合并成一步/间隔，
        // 不会向服务端灌入一串 move 造成坐标跳跃与动画脱节。
        if ((this._holding || this._pendingStep) && this.self && this._mouseDir >= 0) {
            const stepDur = this._mouseRun ? RUN_INTERVAL : WALK_INTERVAL;
            if (now - this._lastMoveAt >= stepDur) {
                this.conn.send('move', { dir: this._mouseDir, run: this._mouseRun });
                this._lastMoveAt = now;
                this._pendingStep = false;
            }
        }
        // 方向攻击持续（shift+左键按住）：每完成一次攻击动作 + 攻速间隔到，就继续挥刀（空挥也持续）
        if (this._dirAttackHolding && this.self && this._dirAttackDir >= 0) {
            this._dirAttack(this._dirAttackDir);
        }
        // 锁定目标追击（原版 TargetCret + AttackTarget）：相邻砍、远了走一步
        this._tickChase(now);
        // NPC 对话框走远自动关闭（原版：离开打开时的位置 >= 8 格即 CloseMDlg）
        if (this.npcDlg && this._npcDlgX != null) {
            if (Math.abs(this.self.x - this._npcDlgX) >= 8
                || Math.abs(this.self.y - this._npcDlgY) >= 8) {
                this._closeNpcDialog();
                this._npcDlgX = null;
            }
        }
        // 匀速卷动：显示中心以「移动速度」匀速逼近玩家坐标（格/ms 固定），
        // 消除 lerp 指数逼近的「先快后慢」追停抖动；shift 整数化避免浮点绘制模糊。
        if (this._viewX == null) { this._viewX = this.self.x; this._viewY = this.self.y; this._lastFrame = now; }
        const dt = now - this._lastFrame;
        this._lastFrame = now;
        // 冲撞(rush)用冲撞速度，其余用走/跑速度（每格耗时 ms）
        const selfRushing = this.self._action === 'rush';
        const stepDur = selfRushing ? RUSH_INTERVAL : (this._mouseRun ? RUN_INTERVAL : WALK_INTERVAL);
        const maxStep = dt / stepDur;                                    // 本帧最大卷动（格）
        const dx = this.self.x - this._viewX;
        const dy = this.self.y - this._viewY;
        this._viewX += dx > maxStep ? maxStep : (dx < -maxStep ? -maxStep : dx);
        this._viewY += dy > maxStep ? maxStep : (dy < -maxStep ? -maxStep : dy);
        // 冲撞到位后回站立：rush 动画覆盖整个冲撞过程，而不是 600ms 超时提前结束（消除「走两步再平移」）
        if (selfRushing && Math.abs(this.self.x - this._viewX) < 0.001 && Math.abs(this.self.y - this._viewY) < 0.001) {
            this.self._action = 'stand';
        }
        const vcx = Math.floor(this._viewX);
        const vcy = Math.floor(this._viewY);
        const shiftX = Math.round((this._viewX - vcx) * 48);
        const shiftY = Math.round((this._viewY - vcy) * 32);
        // 镜头还在追赶逻辑坐标时，自己的走/跑动画必须继续播。
        // 一次推进多格（连点堆积 / 网络抖动 / 冲撞）时镜头要花 N×WALK_INTERVAL 才追上，
        // 固定的 600ms 超时会过早把动作切回 stand，出现「地图还在滑、人却站着不动」。
        const selfCatchingUp = this._viewX != null
            && (Math.abs(this.self.x - this._viewX) > 0.02
                || Math.abs(this.self.y - this._viewY) > 0.02);
        // 原版 PlayScn.pas:2268 ActorDied：死亡 actor 会从 m_ActorList 里删掉并
        // Insert 到「第一个活着的 actor 之前」——即所有尸体排在最前面先画。
        // 注意这只能保证**同一行内**尸体先画；跨行遮挡靠 MapActor 在行循环之后
        // 把「自己」再画一遍（原版 PlayScn.pas:1361）来保证。
        const entities = [];
        const corpses = [];
        const tBuild = performance.now();
        for (const e of this.entities.values()) {
            // 动物尸体：倒地动画播完后切到 ActDeath 静止帧，并保持到挖完/过期
            if (e._corpse && e._dieUntil && now >= e._dieUntil) {
                e._action = 'death';
                e._dieUntil = 0;
            }
            // 移动停止超时回站立（所有实体：连续移动动画循环，停止后归位 stand）
            const stillScrolling = (e.id === this.self.id) && selfCatchingUp;
            if (!stillScrolling && (e._action === 'walk' || e._action === 'run')
                && (now - (e._lastMoveAt || 0)) > 600) {
                e._action = 'stand';
            }
            // 攻击/施法/受击/挖肉动作播放完回站立（动作完成，解除 _isIdle 锁定，可进行下一次攻击/施法）
            const animMs = e._action === 'sitdown' ? SITDOWN_ANIM_MS
                : (e._action === 'spell' ? SPELL_ANIM_MS : ATTACK_ANIM_MS);
            if ((e._action === 'attack' || e._action === 'spell' || e._action === 'struck'
                 || e._action === 'sitdown')
                && (now - (e._actionAt || 0)) > animMs) {
                e._action = 'stand';
            }
            const sp = this._sprite(e);
            const isSelf = e.id === this.self.id;
            let ox = 0, oy = 0;
            if (isSelf) {
                // self 走动画偏移：从逻辑格滑向过渡位置 _viewX（地图反向偏移共用 _viewX）
                ox = (this._viewX - e.x) * 48;
                oy = (this._viewY - e.y) * 32;
            } else if (e._moveAt != null && e._fromX != null) {
                // 怪物/NPC 移动过渡：从旧格滑到新格（时长对齐 walk 动画周期）
                const t = (now - e._moveAt) / MONSTER_MOVE_DUR;
                if (t < 1) {
                    ox = (e._fromX - e.x) * 48 * (1 - t);
                    oy = (e._fromY - e.y) * 32 * (1 - t);
                }
            }
            const snap = {
                x: e.x, y: e.y,   // 逻辑位置（遮挡排序用）
                offsetX: ox, offsetY: oy,
                // 尸体不显示名字/血条（原版 DrawScrn.pas:306 `not m_boDeath`）
                name: e._corpse ? '' : e.name, isSelf,
                hp: e.hp, maxHp: e._corpse ? 0 : e.maxHp, type: e.type,
                dead: !!e._corpse,
                // 悬停目标（原版 g_FocusCret / g_MagicTarget）只让 sprite 变亮，不画框；
                // 点击锁定的 castTarget（原版 g_TargetCret）在原版没有任何视觉标记。
                isFocus: this.magicTarget != null && e.id === this.magicTarget.id,
                alpha: e._hidden ? 0.5 : undefined,   // 隐身半透明
                poison: !!e._poison,                  // 红毒（掉血）绿色调
                poisonArmor: !!e._poisonArmor,        // 绿毒（减防）红色调
                nameColor: e.nameColor,               // 宠物名字颜色（调色板索引）
                // 原版 m_nLight / m_nChrLight（ObjBase.pas:3387、SM_LOGON 的
                // Hibyte(cdir)）：服务端按 U_RIGHTHAND 蜡烛/火把下发 3，否则 1；
                // NPC 是 TNormNpc.m_nLight=2。供 m_LightMap 收集多光源。
                light: e.light != null ? e.light : (isSelf ? 1 : 0),
                layers: sp.getLayers(now),
            };
            (e._corpse || e._action === 'die' ? corpses : entities).push(snap);
        }
        entities.unshift(...corpses);
        perf.add('build', performance.now() - tBuild);
        // 原版 PlayScn.pas:1398-1411：魔法特效 TMagicEff.Light 与场景事件
        // TClEvent.m_nLight（火墙/火堆/神圣战甲术）也要 AddLight，施法瞬间
        // 周围会跟着亮一下。每帧从当前特效列表重建，复用同一个数组。
        const lights = this.mapActor.extraLights;
        lights.length = 0;
        for (const fx of this.effects) {
            if (fx.x == null || fx.y == null) continue;
            const lit = fx.kind === 'ground' || fx.kind === 'elfFire'
                || fx.kind === 'hit' || fx.kind === 'fly'
                || fx.kind === 'firegun' || fx.kind === 'beam';
            if (lit) lights.push({ x: fx.x, y: fx.y, level: 1 });
        }
        // 掉落物交给 MapActor 按行调度，才能被同格/下一行的角色、树木、建筑正确遮挡
        let tPhase = performance.now();
        this.mapActor.render(ctx, vcx, vcy, entities, shiftX, shiftY,
            (c, g, sx, sy, slot) => this._drawGroundItem(c, g, sx, sy, slot),
            this.groundItems);
        perf.add('map', performance.now() - tPhase);
        tPhase = performance.now();
        this._renderEffects(ctx, vcx, vcy, shiftX, shiftY);
        perf.add('fx', performance.now() - tPhase);
        // 原版 PlayScn.pas:1471-1481：死亡时 g_boViewFog := FALSE，跳过
        // ApplyLightMap/DrawFog，改在 ObjSurface 上画 ceGrayScale 黑白画面。
        // 这里只对离屏 back buffer 生效；DOM HUD / 小地图属于之后的 DrawScrn，
        // 与原版一样保持彩色。
        if (this._selfDead) this._drawDeathGray(ctx);
        tPhase = performance.now();
        this._renderHud();
        this._renderMiniMap();
        this._renderBigMap();   // 大地图打开时逐帧刷新自己/怪物位置
        perf.add('hud', performance.now() - tPhase);
    }

    /**
     * 原版 PlayScn.pas:1476 的 ``g_DeathColorEffect = ceGrayScale``：
     * 死亡后把整张 ObjSurface 的调色板映射成灰度。canvas 2d 没有 8 位
     * 调色板，用一次 ``filter='grayscale(1)'`` 的自身重绘等价实现（GPU 合成，
     * 比逐帧 getImageData 快得多）；不支持 filter 的浏览器退化成灰纱覆盖。
     * 死亡提示条画在最上层，告诉玩家怎么复活——原版靠 Alt-X 退出角色，
     * 这里统一走系统菜单「返回角色选择」。
     */
    _drawDeathGray(ctx) {
        const cv = ctx.canvas;
        if (cv) {
            ctx.save();
            if ('filter' in ctx) {
                ctx.filter = 'grayscale(1) brightness(0.72)';
                ctx.globalCompositeOperation = 'copy';
                ctx.drawImage(cv, 0, 0);
            } else {
                ctx.fillStyle = 'rgba(120,120,120,0.55)';
                ctx.fillRect(0, 0, SCREEN_WIDTH, MAPSURFACEHEIGHT);
            }
            ctx.restore();
        }
        ctx.save();
        ctx.fillStyle = 'rgba(0,0,0,0.78)';
        ctx.fillRect(0, 186, SCREEN_WIDTH, 62);
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.font = 'bold 20px SimSun,serif';
        ctx.fillStyle = '#e8d9b0';
        ctx.fillText('你 已 死 亡', SCREEN_WIDTH / 2, 194);
        ctx.font = '13px SimSun,serif';
        ctx.fillStyle = '#c8b98e';
        ctx.fillText('按 Esc →「返回角色选择」，重新登录即可在出生点复活',
                     SCREEN_WIDTH / 2, 221);
        ctx.restore();
    }

    // 实体精灵缓存（entityAdd 替换对象后重建）
    _sprite(e) {
        let sp = this.sprites.get(e.id);
        if (!sp || sp.entity !== e) {
            sp = new ActorSprite(e);
            this.sprites.set(e.id, sp);
        }
        return sp;
    }

    // ---- 技能特效渲染系统（飞行/命中/地面火墙 + 伤害飘字）----

    // 16 方向（对齐原版 GetFlyDirection16）：0上 4右 8下 12左，用于疾光电影等射线型特效选帧
    _dir16From(sx, sy, tx, ty) {
        const fx = tx - sx, fy = ty - sy;
        if (fx === 0) return fy < 0 ? 0 : 8;
        if (fy === 0) return fx < 0 ? 12 : 4;
        if (fx > 0 && fy < 0) {
            if (-fy > fx * 4) return 0;
            if (-fy > fx * 1.4) return 1;
            if (-fy > fx / 1.9) return 2;
            if (-fy > fx / 4) return 3;
            return 4;
        }
        if (fx > 0 && fy > 0) {
            if (fy > fx * 4) return 8;
            if (fy > fx * 1.4) return 7;
            if (fy > fx / 1.9) return 6;
            if (fy > fx / 4) return 5;
            return 4;
        }
        if (fx < 0 && fy > 0) {
            if (fy > -fx * 4) return 8;
            if (fy > -fx * 1.4) return 9;
            if (fy > -fx / 1.9) return 10;
            if (fy > -fx / 4) return 11;
            return 12;
        }
        if (-fy > -fx * 4) return 0;
        if (-fy > -fx * 1.4) return 15;
        if (-fy > -fx / 1.9) return 14;
        if (-fy > -fx / 4) return 13;
        return 12;
    }

    _spawnFx(fx) {
        fx.startAt = Date.now();
        this.effects.push(fx);
    }

    _spawnLevelupFx() {
        // 原版 DrawEffectHum(8)（ClMain.pas:6730-6733）：NewMagic(self, -1, 999,
        // mtExplosion) + s_meltstone。magnumb=999 的分支（PlayScn.pas:1538-1546）
        // 用【人物选择场景】解除石化的光特效：ImgLib=g_WChrSelImages、
        // MagExplosionBase=4、ExplosionFrame=14、NextFrameTime=80。
        // 原版绘制坐标 g_MySelf.m_nTargetX+260 / m_nTargetY-55 依赖投掷目标
        // 残留值（未投掷恒 0，特效落在屏幕左上），这里改为以角色为中心绘制。
        this._spawnFx({ kind: 'levelup', x: this.self.x, y: this.self.y });
    }

    _spawnDamageNumber(x, y, damage, magicId, color) {
        const sx = this.mapActor.screenX(x);
        const sy = this.mapActor.screenY(y);
        this.damageNumbers.push({
            x: sx + 24, y: sy,
            text: String(damage),
            color: color || (magicId ? '#5ad2ff' : '#ffffff'),   // 魔法伤害蓝、物理白；MISS 红由调用方传入
            startAt: Date.now(),
        });
    }

    _fxTexture(fxDef, frame) {
        const idx = fxDef.base + frame;
        const key = fxDef.lib + '/' + idx;
        if (this._fxCache.has(key)) return this._fxCache.get(key);
        const lib = getLib(fxDef.lib);
        if (lib) {
            lib.getTexture(idx).then(t => this._fxCache.set(key, t || null));
        }
        this._fxCache.set(key, null);
        return null;
    }

    _drawFx(ctx, def, frame, gx, gy, ox = 0, oy = 0) {
        const tex = this._fxTexture(def, frame);
        if (!tex) return;
        const sx = this.mapActor.screenX(gx) + ox;
        const sy = this.mapActor.screenY(gy) + oy;
        // 原版 DrawBlend：lighter 光效混合 + 帧锚点 px/py 以格子中心为基准
        ctx.save();
        ctx.globalCompositeOperation = 'lighter';
        ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
        ctx.restore();
    }

    _renderEffects(ctx, vcx, vcy, shiftX, shiftY) {
        if (!this.self) return;
        const now = Date.now();
        // 魔法盾：护盾泡泡（magic 3880 起：前 10 帧成形动画播放一次，之后循环最后 2 帧泡泡，跟随实体偏移）
        for (const e of this.entities.values()) {
            if (!e._shield) continue;
            const def = FX_FRAMES.shield;
            const ft = def.ft || 100;
            const elapsed = now - (e._shieldAt || now);
            const formFrames = 10;   // 成形动画帧数（3880-3889）
            let frame;
            if (elapsed < formFrames * ft) {
                frame = Math.floor(elapsed / ft);                    // 成形动画
            } else {
                frame = formFrames + Math.floor((elapsed - formFrames * ft) / ft) % 2;   // 泡泡循环（3890-3891）
            }
            // 跟随实体偏移（self 走动画 / 怪物移动过渡）
            let ox = 0, oy = 0;
            if (e.id === this.self.id) {
                ox = (this._viewX - e.x) * 48;
                oy = (this._viewY - e.y) * 32;
            } else if (e._moveAt != null && e._fromX != null) {
                const t = (now - e._moveAt) / MONSTER_MOVE_DUR;
                if (t < 1) {
                    ox = (e._fromX - e.x) * 48 * (1 - t);
                    oy = (e._fromY - e.y) * 32 * (1 - t);
                }
            }
            this._drawFx(ctx, def, frame, e.x, e.y, ox, oy);
        }
        // 困魔咒：定身期间循环播放末尾几帧（钉子效果，把怪物钉在原地；完整展开动画由 spellCast 的 hit 播放）
        for (const e of this.entities.values()) {
            if (!e._paralyzed) continue;
            const def = FX_FRAMES.holyCurtain;
            const ft = def.ft || 80;
            const elapsed = now - (e._paralyzedAt || now);
            const formFrames = def.frames;            // 完整展开动画 20 帧
            if (elapsed < formFrames * ft) continue;  // 展开动画播放中（hit 特效负责），避免重复
            const loopStart = formFrames - 4;         // 末尾 4 帧（1396-1399）循环
            const frame = loopStart + Math.floor((elapsed - formFrames * ft) / ft) % 4;
            // 跟随实体偏移（self 走动画 / 怪物移动过渡）
            let ox = 0, oy = 0;
            if (e.id === this.self.id) {
                ox = (this._viewX - e.x) * 48;
                oy = (this._viewY - e.y) * 32;
            } else if (e._moveAt != null && e._fromX != null) {
                const t = (now - e._moveAt) / MONSTER_MOVE_DUR;
                if (t < 1) {
                    ox = (e._fromX - e.x) * 48 * (1 - t);
                    oy = (e._fromY - e.y) * 32 * (1 - t);
                }
            }
            this._drawFx(ctx, def, frame, e.x, e.y, ox, oy);
        }
        // 战士打击光效（攻杀/刺杀/半月/烈火）：叠加在攻击者身上，帧 = HitEffectBase + Dir*10 + 相对帧
        // 相对帧随攻击动画推进（原版 currentframe-startframe，ActHit 6 帧 × 85ms）
        for (const e of this.entities.values()) {
            if (!e._swordHit) continue;
            const def = SWORD_HIT_FX[e._swordHit];
            if (!def) continue;
            const elapsed = now - (e._swordHitAt || now);
            const ft = 85;   // 与攻击动画帧时一致（HA.hit ftime）
            const frames = 6;
            if (elapsed >= frames * ft) { e._swordHit = 0; continue; }
            const rel = Math.floor(elapsed / ft);
            const frame = def.base + (e.dir || 0) * def.dirStride + rel;
            let ox = 0, oy = 0;
            if (e.id === this.self.id) {
                ox = (this._viewX - e.x) * 48;
                oy = (this._viewY - e.y) * 32;
            } else if (e._moveAt != null && e._fromX != null) {
                const t = (now - e._moveAt) / MONSTER_MOVE_DUR;
                if (t < 1) {
                    ox = (e._fromX - e.x) * 48 * (1 - t);
                    oy = (e._fromY - e.y) * 32 * (1 - t);
                }
            }
            const tex = this._fxTexture({ lib: 'magic', base: frame }, 0);
            if (tex) {
                const sx = this.mapActor.screenX(e.x) + ox;
                const sy = this.mapActor.screenY(e.y) + oy;
                ctx.save();
                ctx.globalCompositeOperation = 'lighter';
                ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
                ctx.restore();
            }
        }
        const survivors = [];
        for (const fx of this.effects) {
            const elapsed = now - fx.startAt;
            if (fx.kind === 'ground') {
                // 火墙地面火焰：烧满时限（原版 TFireBurnEvent/ET_FIRE 不因被踩而消失，踩踏伤害由服务端 tick 处理）
                const def = FX_FRAMES[fx.fx];
                if (elapsed > (fx.dur || 8000)) continue;   // 持续到时限
                this._drawFx(ctx, def, Math.floor(elapsed / (def.ft || 40)) % def.frames, fx.x, fx.y);
                survivors.push(fx);
            } else if (fx.kind === 'elfFire') {
                // 神兽吐火火焰：mon18 820+10*Dir+1 起 5 帧（原版 TWarriorElfMonster.RunFrameAction）
                const def = FX_FRAMES.elfFire;
                const ft = def.ft || 100;
                const rel = Math.floor(elapsed / ft) % def.frames;
                this._drawFx(ctx, def, 1 + (fx.dir || 0) * 10 + rel, fx.x, fx.y);
                if (elapsed < def.frames * ft) survivors.push(fx);
            } else if (fx.kind === 'digDust') {
                // 僵尸出土尘土：mon6 ZOMBIDIGUPDUSTBASE=420+dir 单帧（clEvent.pas:105），
                // 原版 m_boBlend=FALSE 正常混合（非光效）；服务端尘土事件 5 分钟超时
                // （ObjMon.pas:1213 TEvent.Create(..., 5*60*1000)），这里同样留 5 分钟。
                const tex = this._fxTexture({ lib: 'mon6', base: 420 }, fx.dir || 0);
                if (tex) {
                    const sx = this.mapActor.screenX(fx.x);
                    const sy = this.mapActor.screenY(fx.y);
                    ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
                }
                if (elapsed < 300000) survivors.push(fx);
            } else if (fx.kind === 'fly') {
                const def = FX_FRAMES[fx.fx];
                const dist = Math.max(1, Math.abs(fx.x - fx.sx) + Math.abs(fx.y - fx.sy));
                const dur = dist * 55;   // 飞行时长（每格 55ms）
                const p = Math.min(1, elapsed / dur);
                const cx = fx.sx + (fx.x - fx.sx) * p;
                const cy = fx.sy + (fx.y - fx.sy) * p;
                // 灵魂火符等有方向帧：帧 = base + Dir16*10 + 相对帧（对齐 TExploBujaukEffect.DrawEff）
                const dirOffset = def.dirStride ? (fx.dir16 || 0) * def.dirStride : 0;
                this._drawFx(ctx, def, dirOffset + Math.floor(elapsed / (def.ft || 80)) % def.frames, cx, cy);
                if (p >= 1) {
                    if (fx.hit) this._spawnFx({ kind: 'hit', fx: fx.hit, x: fx.x, y: fx.y });
                    continue;   // 飞行结束，转为命中
                }
                survivors.push(fx);
            } else if (fx.kind === 'flyAxe') {
                // 远程怪飞矢（箭矢/飞斧）：普通混合（非光效），从攻击者飞向目标，时长对齐服务端延迟
                const def = FX_FRAMES.flyAxe;
                const dist = Math.max(1, Math.abs(fx.x - fx.sx) + Math.abs(fx.y - fx.sy));
                const dur = 600 + dist * 50;   // 对齐原版 SendDelayMsg(600 + Max(dx,dy)*50)
                const p = Math.min(1, elapsed / dur);
                const cx = fx.sx + (fx.x - fx.sx) * p;
                const cy = fx.sy + (fx.y - fx.sy) * p;
                const frame = (fx.dir16 || 0) * (def.dirStride || 10);   // 帧 = 2609 + Dir16*10
                const tex = this._fxTexture(def, frame);
                if (tex) {
                    const sx = this.mapActor.screenX(cx);
                    const sy = this.mapActor.screenY(cy);
                    ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
                }
                if (p >= 1) continue;   // 箭矢到达，伤害由服务端延迟 attack 结算
                survivors.push(fx);
            } else if (fx.kind === 'beam') {
                // 疾光电影：闪电射线，在施法者位置沿方向播放（base 已含 Dir16*10 偏移）
                const frame = Math.floor(elapsed / (fx.ft || 80));
                if (frame >= fx.frames) continue;
                const tex = this._fxTexture({ lib: fx.lib, base: fx.base }, frame);
                if (tex) {
                    const sx = this.mapActor.screenX(fx.sx);
                    const sy = this.mapActor.screenY(fx.sy);
                    ctx.save();
                    ctx.globalCompositeOperation = 'lighter';
                    ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
                    ctx.restore();
                }
                survivors.push(fx);
            } else if (fx.kind === 'firegun') {
                // 地狱火：火焰团从施法者向目标喷射（原版 TFireGunEffect，6 团火焰沿线分布，头小尾大）
                const def = FX_FRAMES[fx.fx];
                const dist = Math.max(1, Math.abs(fx.x - fx.sx) + Math.abs(fx.y - fx.sy));
                const dur = dist * 60;   // 火焰喷射时长（每格 60ms）
                const p = Math.min(1, elapsed / dur);
                const hx = fx.sx + (fx.x - fx.sx) * p;   // 火焰头
                const hy = fx.sy + (fx.y - fx.sy) * p;
                const segX = (fx.x - fx.sx) / 5;
                const segY = (fx.y - fx.sy) / 5;
                for (let i = 0; i < 6; i++) {
                    const nx = hx - segX * i;   // i=0 火焰头，i=5 尾焰（沿反方向往回）
                    const ny = hy - segY * i;
                    const tex = this._fxTexture({ lib: def.lib, base: def.base }, i);   // 帧 930+i
                    if (tex) {
                        const sx = this.mapActor.screenX(nx);
                        const sy = this.mapActor.screenY(ny);
                        ctx.save();
                        ctx.globalCompositeOperation = 'lighter';
                        ctx.drawImage(tex.canvas, sx + (tex.px || 0), sy + (tex.py || 0));
                        ctx.restore();
                    }
                }
                if (p >= 1) continue;   // 火焰到达目标，熄灭
                survivors.push(fx);
            } else if (fx.kind === 'levelup') {
                // 升级光效：chrsel 帧 4 起 14 帧 × 80ms（原版 MagExplosionBase=4 /
                // ExplosionFrame=14 / NextFrameTime=80，PlayScn.pas:1540-1545）。
                // 这批图 292×350、WIL 自带锚点 px/py 是通用占位值（7/-44），
                // 不作偏移，以角色所在格中心为基准居中绘制（人形锚点略上移）。
                const ft = 80;
                const frames = 14;
                const rel = Math.floor(elapsed / ft);
                if (rel >= frames) continue;   // 一次性播放完移除
                const tex = this._fxTexture({ lib: 'chrsel', base: 4 }, rel);
                if (tex) {
                    const sx = this.mapActor.screenX(fx.x) + 24 - tex.width / 2;
                    const sy = this.mapActor.screenY(fx.y) + 16 - tex.height / 2 - 20;
                    ctx.save();
                    ctx.globalCompositeOperation = 'lighter';   // 原版 DrawBlend
                    ctx.drawImage(tex.canvas, sx, sy);
                    ctx.restore();
                }
                survivors.push(fx);
            } else {
                // hit：一次性播放
                const def = FX_FRAMES[fx.fx];
                const frame = Math.floor(elapsed / (def.ft || 80));
                if (frame >= def.frames) continue;   // 播放完移除
                this._drawFx(ctx, def, frame, fx.x, fx.y);
                survivors.push(fx);
            }
        }
        this.effects = survivors;

        // 伤害飘字（上飘 30px，800ms 淡出）
        const nums = [];
        for (const d of this.damageNumbers) {
            const p = (now - d.startAt) / 800;
            if (p >= 1) continue;
            const ty = d.y - p * 30;
            ctx.save();
            ctx.globalAlpha = 1 - p;
            ctx.font = 'bold 13px SimSun,serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = d.color;
            ctx.strokeStyle = '#000';
            ctx.lineWidth = 3;
            ctx.strokeText(d.text, d.x, ty);
            ctx.fillText(d.text, d.x, ty);
            ctx.restore();
            nums.push(d);
        }
        this.damageNumbers = nums;
    }

    // 单个地面物品（原版 PlayScn.pas 的 `for j` 行循环里绘制 DropedItemList）。
    // 锚点 = 格中心：`ix + HALFX - Width/2`、`iy + HALFY - Height/2`
    // （HALFX=24 / HALFY=16，Grobal2.pas）。sx/sy 由 MapActor 按行传入。
    _drawGroundItem(ctx, g, sx, sy, slot = 0) {
        if (!this.self) return;
        // 原版把同格物品全叠在格中心；这里按同格序号做几像素的确定性错开，
        // 让「一格最多 5 件」的堆叠看得清（拾取判定仍按格，不受偏移影响）。
        const ox = slot ? ((slot % 3) - 1) * 7 : 0;
        const oy = slot ? (Math.floor(slot / 3) - 1) * 5 : 0;
        ctx.save();
        if (g.gold > 0) {
            // 金币堆：原版按数量分 5 档钱袋图标（M2Share.pas:3568 GetGoldShape，
            // <30→112 ≥30→113 ≥70→114 ≥300→115 ≥1000→116，DnItems 库）；
            // 服务端已在 itemDrop/groundItems 里下发 looks。取不到贴图时
            // 才退回黄色圆点占位。
            if (g.looks > 0 && this._drawDropTex(ctx, g.looks, sx + ox, sy + oy)) {
                // 已按钱袋图标绘制
            } else {
                ctx.fillStyle = '#fbbf24';
                ctx.beginPath();
                ctx.arc(sx + 24 + ox, sy + 16 + oy, 5, 0, Math.PI * 2);
                ctx.fill();
            }
        } else if (g.looks > 0) {
            // 物品：DnItems[looks] 图标（原版掉落物贴图）
            this._drawDropTex(ctx, g.looks, sx + ox, sy + oy);
        }
        // 同格多件时只给第一件画名字，避免文字糊成一片
        if (g.name && slot === 0) {
            ctx.font = '11px SimSun,serif';
            ctx.textAlign = 'center';
            ctx.fillStyle = '#fff';
            ctx.fillText(g.name, sx + 24, sy + 6);
        }
        ctx.restore();
    }

    // 绘制掉落物物品图标（DnItems 库，异步加载 + 缓存）。
    // 返回 true = 已绘制；false = 贴图未就绪（调用方可画占位）。
    _drawDropTex(ctx, looks, sx, sy) {
        if (!this._dropTex) this._dropTex = new Map();
        if (!this._dropTexLoading) this._dropTexLoading = new Set();
        let tex = this._dropTex.get(looks);
        if (!tex) {
            if (!this._dropTexLoading.has(looks)) {
                this._dropTexLoading.add(looks);
                const lib = getLib('dnitems');
                if (lib) {
                    lib.getTexture(looks).then((t) => {
                        if (t) this._dropTex.set(looks, t);
                        this._dropTexLoading.delete(looks);
                    }).catch(() => this._dropTexLoading.delete(looks));
                }
            }
            return false;
        }
        ctx.drawImage(tex.canvas, sx + (48 - tex.width) / 2, sy + (32 - tex.height) / 2);
        return true;
    }

    _renderHud() {
        if (!this.hudEl || !this.self) return;
        const p = this.self;
        // 地图名/坐标是纯 DOM 文本，单独更新，不参与 canvas 重绘判重
        // （以前把 x/y 放进 key，移动时每帧都会重绘血球 + 等级 → 反复清空 canvas）。
        if (this.mapNameEl) {
            const map = this.snapshot && this.snapshot.map ? this.snapshot.map : null;
            const title = (map && (map.title || map.name)) || '';
            const text = `${title} ${p.x}:${p.y}`.trim();
            if (this.mapNameEl.textContent !== text) this.mapNameEl.textContent = text;
        }
        // 只在数值变化时重绘 canvas
        const key = `${p.hp}/${p.maxHp}/${p.mp}/${p.maxMp}/${p.level}/${p.exp}/${p.maxExp}/${p.weight}/${p.maxWeight}`;
        if (this._hudKey === key) return;
        this._hudKey = key;
        this._renderVitals();
        this._renderLevelNumber();
        // 经验条 / 负重条（Prguse[7]，76px 满宽）
        if (this.expBarEl && p.maxExp > 0) {
            this.expBarEl.style.width = Math.max(0, Math.min(76, Math.round(76 * (p.exp || 0) / p.maxExp))) + 'px';
        }
        if (this.loadBarEl && p.maxWeight > 0) {
            this.loadBarEl.style.width = Math.max(0, Math.min(76, Math.round(76 * (p.weight || 0) / p.maxWeight))) + 'px';
        }
    }

    /**
     * 取 HUD 位图；没有就异步补拉一次并登记，拉到后强制重绘。
     *
     * 原版对每个 HUD 元素是「拿到图就画、没图就跳过」（`FState.pas:3813`
     * `d := g_WMainImages.Images[7]; if d <> nil then`），并没有「全部就绪才画」的总闸。
     * 之前用一个全局 `_hudTexReady` 同时卡住血球和等级数字：只要那一批 getTexture 里有
     * 一个没 settle（进图时 `scheduler.abortPending()` 会 abort 在飞请求），
     * 血球和等级就**永久空白**——用户反馈的「血球不准时 lv 也为空」就是这个。
     */
    _ensureHudTex(lib, idx) {
        const tex = lib.textures.get(idx);
        if (tex) return tex;
        const missing = this._hudMissing || (this._hudMissing = new Set());
        if (missing.has(idx)) return null;
        missing.add(idx);
        const again = () => { missing.delete(idx); this._hudKey = null; };
        lib.getTexture(idx).then(() => { again(); this._renderHud(); })
            .catch(() => setTimeout(() => { again(); this._renderHud(); }, 1000));
        return null;
    }

    /** 血球/魔法球：Prguse[4]（HP 左半 + MP 右半，各按比例纵向裁切）；
     *  武士且 lv<26 用 Prguse[5] 容器 + Prguse[6] 红色液体（对齐 FState.pas:3249-3280）。
     *  纹理已在 _buildHud 预取到 WIL.textures，这里全程同步：clearRect 与 drawImage
     *  在同一个调用栈完成，浏览器不可能把「清空后的空白」单独呈现出来。 */
    _renderVitals() {
        const p = this.self;
        if (!p || !this.vitalsCanvas) return;
        const lib = getLib('prguse');
        if (!lib) return;
        const ctx = this.vitalsCanvas.getContext('2d');
        ctx.clearRect(0, 0, this.vitalsCanvas.width, this.vitalsCanvas.height);
        if (p.job === 0 && p.level < 26) {
            const base = this._ensureHudTex(lib, 5);
            const liquid = this._ensureHudTex(lib, 6);
            if (base) ctx.drawImage(base.canvas, 0, 0);
            if (liquid) {
                const h = liquid.canvas.height;
                const top = Math.round(h / Math.max(1, p.maxHp) * (p.maxHp - p.hp));
                if (top < h) {
                    ctx.drawImage(liquid.canvas, 0, top, liquid.canvas.width, h - top, 0, top, liquid.canvas.width, h - top);
                }
            }
        } else {
            const d = this._ensureHudTex(lib, 4);
            if (d) {
                const w = d.canvas.width;
                const h = d.canvas.height;
                const half = w >> 1;
                const hpTop = Math.round(h / Math.max(1, p.maxHp) * (p.maxHp - p.hp));
                if (hpTop < h) ctx.drawImage(d.canvas, 0, hpTop, half - 1, h - hpTop, 0, hpTop, half - 1, h - hpTop);
                const mpTop = Math.round(h / Math.max(1, p.maxMp) * (p.maxMp - p.mp));
                if (mpTop < h) {
                    ctx.drawImage(d.canvas, half + 1, mpTop, w - half - 1, h - mpTop, half + 1, mpTop, w - half - 1, h - mpTop);
                }
            }
        }
    }

    /** 等级：位图数字 Prguse[30..39]（8px/位，首位前空 8px，对齐 PomiTextOut）。 */
    _renderLevelNumber() {
        const p = this.self;
        if (!p || !this.levelCanvas) return;
        const lib = getLib('prguse');
        if (!lib) return;
        const ctx = this.levelCanvas.getContext('2d');
        ctx.clearRect(0, 0, this.levelCanvas.width, this.levelCanvas.height);
        const s = String(p.level == null ? '' : p.level);
        for (let i = 0; i < s.length; i++) {
            const digit = s.charCodeAt(i) - 48;
            if (digit < 0 || digit > 9) continue;
            const tex = this._ensureHudTex(lib, 30 + digit);
            if (tex) ctx.drawImage(tex.canvas, (i + 1) * 8, 0);
        }
    }

    onExit() {
        if (sound.terrainAt) sound.terrainAt = null;   // 场景切走：别让音效模块继续读这张地图
        document.removeEventListener('keydown', this._keyDown);
        if (this._keyUp) document.removeEventListener('keyup', this._keyUp);
        if (this._mdHandler) this._canvas.removeEventListener('mousedown', this._mdHandler);
        if (this._muHandler) document.removeEventListener('mouseup', this._muHandler);
        if (this._mmHandler) document.removeEventListener('mousemove', this._mmHandler);
        // 清理窗口拖动监听
        for (const fn of this._dragCleaners) fn();
        this._dragCleaners = [];
        this._holding = false;
        this._dirAttackHolding = false;
        this._shiftDown = false;
        this.bagPanel = null;
        this.stateWin = null;
        this.npcDlg = null;
        this.shopPanel = null;
        this.shopBagListEl = null;
        this.shopListEl = null;
        this.shopSpotEl = null;
        this.shopGoldEl = null;
        this.shopSellItem = null;
        this.pickedItem = null;
        this.pickedSource = null;   // 'bag' | 'equip'
        this.pickedSlot = -1;       // pickedSource === 'equip' 时的槽位号
        this._pickedCell = null;
        // 大地图 / 系统菜单等 HUD 覆盖层随 gui.innerHTML 一起移除，引用一并置空
        this._bigMapOpen = false;
        this._bigMapPanel = null;
        this._bigMapCanvas = null;
        this._bigMapTitle = null;
        this._sysMenu = null;
        this._pendingStep = false;
        const gui = this.manager.winManager.container;
        gui.innerHTML = '';
    }
}
