"""游戏世界模型（对齐 Java ``GameWorld`` / ``Actor`` / ``Player`` / ``Monster``）。

所有状态只在 asyncio 单事件循环内读写，不需要 ``world.lock``。
tick 子系统（回血 / buff / 毒 / 火墙 / 飞矢 / 怪物与宠物 AI / 重生）
按 Java ``GameWorld.tick`` 的调用顺序直译。
"""
from __future__ import annotations

import itertools
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .config import ServerConfig
from .db import Database
from .gamedata import (
    Gate, MapGrid, MapInfo, MonGen, parse_map_info, parse_mini_map,
    map_size,
    parse_mon_gen, parse_notice, parse_start_point, parse_start_points, read_map, scan_available_maps,
)
from .templates import ItemTemplate, MagicTemplate, MonsterTemplate
from .util import (
    AGGRO_RANGE, CHICKEN_DEER_RUN, CHICKEN_DEER_VIEW, DOOR_OPEN_MS, GUARD_RACES,
    HSET_SHAPES, ILLEGAL_ATTACK_MS, PK_DECAY_INTERVAL_MS,
    NEUTRAL_RACES,
    POISON_INTERVAL_MS, PSET_SHAPES, RANGED_ATTACK_RANGE, RANGED_CHASE_RANGE,
    RANGED_KEEP_AWAY, RANGED_RACES, RED_NAME_PK_POINT, REGEN_INTERVAL_MS,
    SHAPE_MANA_TO_HP, SHAPE_PARALYSIS_WEAPON, SHAPE_REVIVAL_RING,
    SHAPE_SUCK_HEALTH, SHAPE_TELEPORT_RING, SLAVE_COLORS, SLAVE_UP_EXP,
    NAME_COLOR_DEFAULT, NAME_COLOR_PK1, NAME_COLOR_PK2, NAME_COLOR_PKFLAG,
    SUPER_GUARD_RACES, SUPER_GUARD_VIEW, YSET_SHAPES,
    calc_get_exp, delphi_round, java_div, java_round, init_meat_quality,
    slot_accepts, slot_for,
)
from .world_extra import BODY_LEATHERY, WorldExtra

SIGHT_RANGE = 12

# 房间图（max(w,h)<100）默认不在主城大图标注入口（用户约定：主城进店铺/民房
# 不标）。但这两张是洞穴系列的必经大厅——石墓入口→石墓一~五层、祖玛寺庙大殿
# → 祖玛一~七层，性质等同洞穴入口，用户要求在主城大地图上标出。
# 原版客户端大地图本无门点标注（本项目增强），故无 Delphi 依据，用显式白名单
# 精确控制，避免「房间图→cave 图」这类泛判据把 30+ 民房店铺一起放出。
BIGMAP_EXTRA_ENTRIES: frozenset[str] = frozenset({"D710", "0157"})
# 大地图不显示的入口目标图：秘密通道是地下暗道的过路点，用户要求在地图
# 标注里隐去（原版大地图本无门点标注，这是本项目增强，遵从产品决策）。
BIGMAP_HIDE_ENTRIES: frozenset[str] = frozenset({"D701"})


def _dedup_gate_marks(gates: list[Gate], titles: dict[str, str]) -> list[dict]:
    """每个目标图只发一个大地图标注，避免文字标签重复/重叠。

    MapInfo.txt 里一个门口写成 8~14 行相邻坐标（原版用一片坐标覆盖门口），
    且同一目标图在地图上可能有多处相距很远的入口（如毒蛇山谷回盟重省有
    (298,58)/(559,75) 两簇、回比奇省有 (413,565)/(413,547) 两簇）。只按坐标
    邻近聚类（旧算法，切比雪夫距离 <= 3）会把这些入口各画一份 → 视觉上出现
    「两份重叠」。现在改为按目标图分组，每组只取门点最密集的一簇中心作为
    唯一标注点。纯显示用途——实际踩门换图仍走完整的 ``gates_by_src``，
    不受影响；也不影响小地图/寻路。
    """
    by_dst: dict[str, list[Gate]] = {}
    for g in gates:
        if g.dst_map in BIGMAP_HIDE_ENTRIES:
            continue
        by_dst.setdefault(g.dst_map, []).append(g)

    marks: list[dict] = []
    for to, gs in by_dst.items():
        # 组内邻近聚类（门口坐标常连成一条链，跨度可达 5 格）
        clusters: list[list[Gate]] = []
        for g in gs:
            for c in clusters:
                if any(max(abs(m.src_x - g.src_x), abs(m.src_y - g.src_y)) <= 3
                       for m in c):
                    c.append(g)
                    break
            else:
                clusters.append([g])
        # 取门点最密集的一簇作为该目标图的唯一标注；同规模时取先出现的
        best = max(clusters, key=len)
        mid_x = sorted(g.src_x for g in best)[len(best) // 2]
        mid_y = sorted(g.src_y for g in best)[len(best) // 2]
        marks.append({"x": mid_x, "y": mid_y, "to": to,
                      "title": titles.get(to, to)})
    return marks
# 旁观者血条同步间隔（ms）：玩家 hp 变化后最多延迟这么久才广播给周围玩家。
# 太小会放大广播量（每次回血/中毒都要发），太大血条看起来迟滞。绑定 100ms tick 的整数倍。
HP_SYNC_MS = 400
BAG_SIZE = 46
RESPAWN_MS = 15_000
# 空图休眠：最后一名在线玩家离开超过 MON_DESPAWN_MS 就整图清怪（实测
# 12 张大图满怪 tick 一次 131ms / CPU≈65%，无人看的图纯属空转）；
# DESPAWN_CHECK_MS 是清怪巡检周期。AI 休眠（不游走）则是即时的。
MON_DESPAWN_MS = 5 * 60_000
DESPAWN_CHECK_MS = 30_000
# 怪物目标全图扫描的错峰节流：原版 TAnimalObject.Run 每 tick 重扫视野
# （Delphi 编译型无压力），Python 里 766 怪 × 10 次/秒的全图 O(N) 扫描
# 占掉 ~30% CPU（实测 profile：_resolve_target 68s 采样占 44s）。
# 有缓存目标时 O(1) 校验直接沿用，只有目标失效后才按此周期重扫。
MON_SCAN_MS = 600
HP_GROWTH, MP_GROWTH = 3, 2
BASE_HP, BASE_MP = (26, 16, 18), (12, 20, 16)
BASE_DC, BASE_MC, BASE_SC = (2, 0, 1), (0, 2, 0), (0, 0, 2)

# ---- 命中 / 敏捷（原版 m_btHitPoint / m_btSpeedPoint）-----------------------
# 基础值：M2Share.pas:128-129 DEFHIT=5 / DEFSPEED=15。
# 玩家由两部分相加（ObjBase.pas:3394-3395 + 18563-18570 RecalcAbilitys/RecalcHitSpeed）：
#   ① 装备/套装：`m_AddAbil.wHitPoint` / `wSpeedPoint` —— ItmUnit.pas
#      ApplyItemParameters 里把物品的准确(AC2)/敏捷(MAC2) **1:1 原始累加**
#      （武器 AC2、项链/手镯/戒指 StdMode 20/22/24/41 的 AC2/MAC2）；
#   ② 可分配加点 m_BonusAbil（ClientAdjustBonus 升一级给的点数）按
#      `BonusTick.Hit / .Speed` 折算：
#      战士 20/35、法师 25/35、道士 30/30，道士再 +3 敏捷。
#   ③ 剑术技能：基本剑术 Round(9/3*lv)、精神力战法 Round(8/3*lv)、
#      攻杀剑术 Round(3/3*lv) 直接加到 m_btHitPoint。
# 怪物走 MonInitialize（UsrEngn.pas:2605-2606）：直接取 Monster.DB 的 Hit / Speed。
# 本项目没有「升级加点」系统（m_BonusAbil 恒 0），所以玩家点数 = 基础 + 装备 + 技能。
DEF_HIT, DEF_SPEED = 5, 15
# 加点折算 tick（保留说明：若后续实作加点系统要用）
BONUS_TICK_HIT = (20, 25, 30)
BONUS_TICK_SPEED = (35, 35, 30)
# 技能附加命中：3 基本剑术 9/3、4 精神力战法 8/3、7 攻杀剑术 3/3
SKILL_HIT_BONUS = ((3, 9, 3), (4, 8, 3), (7, 3, 3))

U_DRESS, U_WEAPON, U_RIGHTHAND, U_CHARM = 0, 1, 2, 12
EQUIP_SLOTS = 13

# 与 Java ExperienceTable.NEED_EXPS 完全一致（MAXLEVEL=101，index 0→等级1，index 100→等级101）。
# 早期移植只抄了前 65 项导致 65 级后 exp_for_level / level_for_exp 被钳到表尾（经验需求冻结、满级≈65）；
# 现补全全部 101 项，升级阈值按原版逐值推进到 101 级。
NEED_EXPS = (
    100, 200, 300, 400, 600, 900, 1200, 1700, 2500, 6000,
    8000, 10000, 15000, 30000, 40000, 50000, 70000, 100000, 120000, 140000,
    250000, 300000, 350000, 400000, 500000, 700000, 1000000, 1400000, 1800000, 2000000,
    2400000, 2800000, 3200000, 3600000, 4000000, 4800000, 5600000, 8200000, 9000000, 12000000,
    16000000, 30000000, 50000000, 80000000, 120000000, 160000000, 200000000, 250000000, 300000000, 350000000,
    400000000, 480000000, 560000000, 640000000, 740000000, 840000000, 950000000, 1070000000, 1200000000, 1500000000,
    1500000000, 1600000000, 1600000000, 1700000000, 1700000000, 1800000000, 1800000000, 1900000000, 1900000000, 2000000000,
    2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000,
    2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000,
    2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000,
    2140000000,
)


def encode(obj: Any) -> str:
    """与 Gson ``toString()`` 等价的输出：中文不转义、无多余空格。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def exp_for_level(level: int) -> int:
    return NEED_EXPS[max(1, min(level, len(NEED_EXPS))) - 1]


def normalize_exp(level: int, exp: int) -> tuple[int, int]:
    """旧存档迁移：把「累计经验」换算成原版语义的「本级内经验」。

    历史实现把 ``Player.exp`` 当累计总经验用（``level_for_exp`` 按累计和反查等级），
    而原版 ``ObjBase.pas:1844 GetExp`` 的 ``m_Abil.Exp`` 是**本级内**经验，升级时
    ``Dec(Exp, MaxExp)``。两种语义混用会让客户端出现「当前经验 1183 / 升级经验 400」。

    判定：``exp < exp_for_level(level)`` 说明已经是新语义（本级内不会超过阈值），
    原样返回；否则按旧语义逐级扣减。返回 ``(等级, 本级内经验)``。
    """
    level = max(1, min(int(level), len(NEED_EXPS)))
    exp = max(0, int(exp))
    if exp < exp_for_level(level):
        return level, exp
    total, lv = 0, 1
    while lv < len(NEED_EXPS) and exp - total >= exp_for_level(lv):
        total += exp_for_level(lv)
        lv += 1
    return lv, exp - total


def now_ms() -> int:
    return int(time.time() * 1000)


def direction_delta(d: int) -> tuple[int, int]:
    """8 方向表，与 ``Direction.dx/dy`` 一致（0=上，顺时针）。"""
    dx = (0, 1, 1, 1, 0, -1, -1, -1)[d & 7]
    dy = (-1, -1, 0, 1, 1, 1, 0, -1)[d & 7]
    return dx, dy


def direction_toward(fx: int, fy: int, tx: int, ty: int, fallback: int) -> int:
    sx = (tx > fx) - (tx < fx)
    sy = (ty > fy) - (ty < fy)
    for d in range(8):
        dx, dy = direction_delta(d)
        if ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0)) == (sx, sy):
            return d
    return fallback


def day_bright_for_hour(hour: int) -> int:
    """原版 ``FrnEngn.pas:85-91`` 的 ``g_nGameTime`` 分档（按真实小时）。

    1 = 白天（全亮）、3 = 深夜、0/2 = 过渡。之前是均分四段，与原版不符，
    ``DayBright()`` 的暗度计算必须建立在这张表上。
    """
    if hour in (5, 6, 7, 8, 9, 10, 16, 17, 18, 19, 20, 21, 22):
        return 1
    if hour in (11, 23):
        return 2
    if hour in (4, 15):
        return 0
    return 3


def filter_show_name(name: str) -> str:
    """原版 ``M2Share.pas:3630 FilterShowName``：名字从第一个数字/减号处截断。

    Monster.DB 里同名怪有多个变体（不同外观/命中/行走参数），转成本项目的
    Monster.csv 时为了 key 唯一加了数字后缀（僵尸/僵尸1/僵尸2/僵尸20…）；
    MonGen.txt 也用带后缀的名字索引模板。原版下发给客户端的显示名走
    ``TBaseObject.GetShowName`` → ``FilterShowName``，所以玩家看到的始终是
    「僵尸」「骷髅」「稻草人」，而不是「僵尸3」「僵尸20」。
    """
    for i, ch in enumerate(name):
        if ('0' <= ch <= '9') or ch == '-':
            return name[:i]
    return name


# ---------------------------------------------------------------- 地图

class GameMap:
    """``MapGrid`` 的运行时包装：阻挡网格 + 当前图上的实体 + 门开闭状态。

    ``by_pos`` / ``players`` 是两张维护索引（``add``/``remove``/``try_move``
    三个入口负责增删），用来替代把 ``occupied`` 和广播做成 O(n) 全图扫描 ——
    大图满怪时一次 ``occupied`` 要遍历上千实体，怪物每次移动都会踩。
    """

    __slots__ = ("grid", "actors", "open_doors", "by_pos", "players",
                 "last_active_at", "mon_spawned")

    def __init__(self, grid: MapGrid):
        self.grid = grid
        self.actors: dict[int, "Actor"] = {}
        self.open_doors: dict[tuple[int, int], int] = {}
        self.by_pos: dict[tuple[int, int], list["Actor"]] = {}
        self.players: dict[int, "Player"] = {}
        # 空图休眠：0 = 从未有玩家进过（保持满编冷置，AI 跳过但不清怪）；
        # 否则是最后一名在线玩家离开/在图时刻，超过阈值才清怪。
        self.last_active_at = 0
        self.mon_spawned = False

    @property
    def name(self) -> str:
        return self.grid.name

    @property
    def width(self) -> int:
        return self.grid.width

    @property
    def height(self) -> int:
        return self.grid.height

    def inside(self, x: int, y: int) -> bool:
        return self.grid.inside(x, y)

    def can_walk(self, x: int, y: int) -> bool:
        return self.grid.can_walk(x, y)

    def is_high_wall(self, x: int, y: int) -> bool:
        return self.grid.is_high_wall(x, y)

    def is_door(self, x: int, y: int) -> bool:
        return (x, y) in self.grid.doors

    def is_door_open(self, x: int, y: int) -> bool:
        return (x, y) in self.open_doors

    def open_door(self, x: int, y: int, at: int) -> None:
        self.open_doors[(x, y)] = at

    def occupied(self, x: int, y: int) -> bool:
        """按位索引查询，语义与旧线性扫描一致（``a.alive`` 才算占位）。"""
        lst = self.by_pos.get((x, y))
        if not lst:
            return False
        return any(a.alive for a in lst)

    def add(self, actor: "Actor") -> None:
        self.actors[actor.id] = actor
        actor.map = self
        self.by_pos.setdefault((actor.x, actor.y), []).append(actor)
        if isinstance(actor, Player):
            self.players[actor.id] = actor
            self.last_active_at = now_ms()

    def remove(self, actor_id: int) -> None:
        a = self.actors.pop(actor_id, None)
        if a is None:
            return
        lst = self.by_pos.get((a.x, a.y))
        if lst is not None:
            try:
                lst.remove(a)
            except ValueError:
                pass
            if not lst:
                self.by_pos.pop((a.x, a.y), None)
        if isinstance(a, Player):
            self.players.pop(a.id, None)
            if self.players:
                self.last_active_at = now_ms()

    def reposition(self, a: "Actor", nx: int, ny: int) -> None:
        """try_move 成功后同步位索引（坐标突变只发生在 try_move / 传送）。"""
        old = (a.x, a.y)
        lst = self.by_pos.get(old)
        if lst is not None:
            try:
                lst.remove(a)
            except ValueError:
                pass
            if not lst:
                self.by_pos.pop(old, None)
        a.x, a.y = nx, ny
        self.by_pos.setdefault((nx, ny), []).append(a)


# ---------------------------------------------------------------- 实体

@dataclass(slots=True)
class Actor:
    id: int = 0
    name: str = ""
    x: int = 0
    y: int = 0
    dir: int = 4
    hp: int = 1
    max_hp: int = 1
    mp: int = 0
    max_mp: int = 0
    level: int = 1
    exp: int = 0
    race: int = 0
    race_img: int = 0
    appr: int = 0
    alive: bool = True
    running: bool = False
    map: GameMap | None = None
    seen: set[int] = field(default_factory=set)
    # 原版 STATE_OPENHEATH（心灵启示）：头顶血条只在被「启示」后显示一段时间
    # （DrawScrn.pas:306 的 actor.m_boOpenHealth；默认不画）。
    open_hp_until: int = 0

    def is_player(self) -> bool:
        return isinstance(self, Player)

    def distance_to(self, other: "Actor") -> int:
        """切比雪夫距离，与 Java ``Actor.distanceTo`` 一致（8 方向格距）。"""
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def brief_json(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "type": "player" if self.is_player() else "monster",
            "x": self.x, "y": self.y, "dir": self.dir,
            "hp": self.hp, "maxHp": self.max_hp,
            "race": self.race, "raceImg": self.race_img,
            "appr": self.appr, "level": self.level,
            # 心灵启示到期时间（0 = 不显示血条）；客户端按此开关头顶血条
            "openHealth": self.open_hp_until if self.open_hp_until > now_ms() else 0,
        }


@dataclass(slots=True)
class Player(Actor):
    account_id: int = 0
    # 换图锁：防止换图瞬间仍在途的 move 落在新地图，踩中相邻回程门被弹回。
    map_change_at: int = 0
    character_id: int = 0
    sex: int = 0
    hair: int = 0
    job: int = 0
    gold: int = 0
    home_map: str = "default"
    home_x: int = 0
    home_y: int = 0
    bag: list[dict] = field(default_factory=list)
    equipment: list[dict | None] = field(default_factory=lambda: [None] * EQUIP_SLOTS)
    storage: list[dict] = field(default_factory=list)
    skills: dict[int, dict] = field(default_factory=dict)
    quick_slots: list[int] = field(default_factory=lambda: [0] * 6)
    combat: dict[str, int] = field(default_factory=dict)
    session: Any = None
    world_ref: Any = None
    long_hit_on: bool = False
    wide_hit_on: bool = False
    fire_hit_on: bool = False
    shield_until: int = 0
    shield_level: int = 0
    hide_until: int = 0
    poison_ticks: int = 0
    poison_armor_until: int = 0
    last_use_item_at: int = 0
    pk_point: int = 0
    # 原版 BoIllegalAttack / IllegalAttackTime（ObjBase.pas:2824-2837）：
    # 主动攻击白名玩家后置灰名，60 秒后清除；期间被反击属正当防卫。
    illegal_attack_until: int = 0
    # 原版 time10min（ObjBase.pas:7719）：上次减 PK 点的时刻，每 2 分钟 -1。
    pk_decay_at: int = 0
    quest_params: dict[int, int] = field(default_factory=dict)
    guild_name: str = ""
    last_attack_at: int = 0
    last_spell_at: int = 0
    last_butch_at: int = 0   # 原版 m_dwTurnTick：挖肉/转身间隔（600ms）
    # 战斗 / 技能状态（对齐 Java Player 字段）
    luck: int = 0
    suck_health_rate: int = 0
    mana_to_health: int = 0
    paralysis_weapon: bool = False
    revival_ring: bool = False
    teleport_ring: bool = False
    # 诱惑之光可带 2/3/4/5 只（官服口径，lv+2）；骷髅/神兽仍只占第一只。
    summon_ids: list[int] = field(default_factory=list)
    target_id: int = -1
    inc_healing: int = 0
    # 吃药写入的回血/回蓝池：原版不吃即时血，药水的 AC/MAC 写进
    # m_nIncHealth / m_nIncSpell（ObjBase.pas:23359-23366），由 _regen 的
    # 回血 tick 按「每 tick Level div 10 + 5」慢慢消耗（ObjBase.pas:3782-3850）。
    inc_health: int = 0
    inc_spell: int = 0
    # GM 账号标记（登录时按 config.GM_ACCOUNTS 判定）。
    is_gm: bool = False
    # AI Bot 玩家标记（活跃气氛的假人，docs/OFFLINE-AFK-PLAN.md §4）：
    # 不落库、重启按 gamedata/bots.json 重建；选怪/名单等按需区分。
    is_bot: bool = False
    last_healing_at: int = 0
    poison_damage: int = 0
    last_poison_at: int = 0
    ac_buff_until: int = 0
    mac_buff_until: int = 0
    ac_buff_bonus: int = 0
    mac_buff_bonus: int = 0
    # 原版 m_boUserUnLockDurg（ObjBase.pas:230，神水 Shape=2 置位）：本项目
    # 无装备绑定系统，仅作记录（无消费方）。
    user_unlock_durg: bool = False
    # 原版 m_wStatusArrValue / m_dwStatusArrTimeOutTick（ObjBase.pas:120-121，
    # EatItems Shape=12 段 :23383-23437 写入，RecalcAbilitys :3424-3451 消费，
    # Run :4194-4201 到点清零）。6 槽：DC/MC/SC/攻速/MaxHP/MaxMP。
    status_arr: list = field(default_factory=lambda: [0] * 6)
    status_until: list = field(default_factory=lambda: [0] * 6)
    last_fire_hit_at: int = 0
    last_rush_rush_at: int = 0
    power_hit_count: int = 0
    power_hit_point: int = 0
    # 技能修炼进度下发节流（原版 TrainSkill 后用 SendDelayMsg(RM_MAGIC_LVEXP, 3000ms)
    # 把 CurTrain 推给客户端；技能页的「25/50」进度条靠它刷新）
    skill_lvexp_at: int = 0
    attack_mode: int = 1
    skill_cooldown: dict[int, int] = field(default_factory=dict)
    # 原版 m_boDeath：死亡后客户端停在黑白屏，不自动回城；重登时按
    # UsrEngn.pas:576-600 回出生点并给 HP=14。
    dead: bool = False
    # 脚本 TimeRecall（原版 m_boTimeRecall/m_sMoveMap/m_dwTimeRecallTick，
    # ObjBase.pas:634-638）：{"map","x","y","expire"}，到期 SpaceMove 回记录点；
    # 主动换图自动取消（ObjBase.pas:23616）。
    time_recall: dict | None = None
    # 离线修炼（docs/OFFLINE-AFK-PLAN.md）：afk_plan 是在线时配置的待生效计划
    # （下线瞬间由 OfflineAgentManager.promote 接管）；afk_offline 标记角色当前
    # 正由离线代理驱动（经验系数、离线报告都认这个旗标）。
    afk_plan: dict | None = None
    afk_offline: bool = False

    def bag_item(self, make_index: int) -> dict | None:
        return next((it for it in self.bag
                     if int(it.get("makeIndex", 0)) == make_index), None)

    def clear_quick_ref(self, make_index: int) -> None:
        if make_index <= 0:
            return
        for i in range(len(self.quick_slots)):
            if self.quick_slots[i] == make_index:
                self.quick_slots[i] = 0

    def quick_slot_valid(self, slot: int) -> bool:
        return (0 <= slot < len(self.quick_slots)
                and self.quick_slots[slot] > 0
                and self.bag_item(self.quick_slots[slot]) is not None)

    def wear_weight_ex(self, world: "GameWorld", exclude_slot: int) -> int:
        total = 0
        for i, it in enumerate(self.equipment):
            if i in (U_WEAPON, U_RIGHTHAND) or i == exclude_slot:
                continue
            t = self._template(world, it)
            if t is not None:
                total += t.weight
        return total

    @property
    def summon_id(self) -> int:
        """兼容视图：第一只召唤物，无则 -1（原字段语义）。

        真正的状态在 ``summon_ids``（诱惑之光官服可带 lv+2 只）。
        """
        return self.summon_ids[0] if self.summon_ids else -1

    @summon_id.setter
    def summon_id(self, value: int) -> None:
        if value < 0:
            self.summon_ids.clear()
        elif value not in self.summon_ids:
            self.summon_ids.append(value)

    def light_level(self) -> int:
        """原版 ObjBase.pas:3387-3392 ``RecalcAbilitys`` 末尾的 m_nLight。

        ``U_RIGHTHAND``（StdMode 30 = 蜡烛/火把/火炬，ClFunc.pas:630）装着
        且耐久 > 0 → m_nLight := 3，否则 0，并用 RM_CHANGELIGHT 通知周围。
        客户端 THumActor.Light（Actor.pas）取 max(m_nChrLight, m_nMagLight)，
        其中 m_nChrLight 来自 SM_LOGON 的 Hibyte(cdir)，默认 1 级基础微光。
        这里把两者合并成一个等级下发给前端的 m_LightMap。
        """
        it = self.equipment[U_RIGHTHAND] if len(self.equipment) > U_RIGHTHAND else None
        if it is not None and int(it.get("durability", 0)) > 0:
            return 3
        return 1

    def name_color(self) -> int:
        """头顶名字颜色的原版关系色（ObjBase.pas:19197 GetNamecolor）。

        PKLevel = pk_point // 100：>=2 深红、=1 黄；PK 标记（红名但 PKLevel<1）
        用原版 $2F 棕。普通玩家仍为白 255，和原版默认一致。增强模式打开时
        前端还会把「自己」单独染成金黄，但红名优先，避免把危险状态盖掉。
        """
        if self.pk_point >= RED_NAME_PK_POINT:
            return NAME_COLOR_PK2
        if self.pk_point >= 100:
            return NAME_COLOR_PK1
        # 灰名（原版 GetThisCharColor ObjBase.pas:3894-3898）：主动攻击白名
        # 后 60 秒内盖成索引 47（棕），比白名优先；红/黄名则不被灰名盖。
        if self.illegal_attack_until > 0:
            return NAME_COLOR_PKFLAG
        if self.pk_point > 0:
            return NAME_COLOR_PKFLAG
        return NAME_COLOR_DEFAULT

    def brief_json(self) -> dict:
        # 显式基类调用，不用零参数 super()：本类是 @dataclass(slots=True)，
        # 3.10~3.12 重建类时不会修正 __class__ cell，零参数 super() 会报
        # "super(type, obj): obj must be an instance or subtype of type"。
        o = Actor.brief_json(self)
        o.update(sex=self.sex, hair=self.hair, job=self.job,
                 dress=self.dress_looks(self.world_ref),
                 weapon=self.weapon_shape(self.world_ref),
                 light=self.light_level(), dead=self.dead,
                 nameColor=self.name_color())
        # 死亡玩家留在原地当尸体（原版 m_boDeath）：别人后进视野时靠这两个字段
        # 画 ActDie 末帧，否则会按「站立」帧画出一具站着的尸体。
        o["alive"] = self.alive
        o["corpse"] = self.dead
        return o

    def _template(self, world: "GameWorld | None", item: dict | None) -> ItemTemplate | None:
        if item is None or world is None:
            return None
        return world.item_templates.get(int(item.get("index", 0)))

    def dress_looks(self, world: "GameWorld | None" = None) -> int:
        t = self._template(world, self.equipment[U_DRESS] if self.equipment else None)
        return (t.shape if t else 0) * 2 + self.sex

    def weapon_shape(self, world: "GameWorld | None" = None) -> int:
        t = self._template(world, self.equipment[U_WEAPON] if self.equipment else None)
        return (t.shape if t else 0) * 2 + self.sex

    def distribute_items(self, world: "GameWorld") -> None:
        """落盘时带 slot 标记的物品回到装备槽，其余留在背包。"""
        # 调用方先把持久化 items 放进 bag，这里原地分流。
        self.equipment = [None] * EQUIP_SLOTS
        remain: list[dict] = []
        for it in self.bag:
            slot = int(it.get("slot", -1))
            t = self._template(world, it)
            if (t and 0 <= slot < EQUIP_SLOTS and self.equipment[slot] is None
                    and slot_accepts(slot, t.stdMode)):
                self.equipment[slot] = it
            else:
                remain.append(it)
        self.bag = remain

    def recompute(self, world: "GameWorld") -> None:
        """直译 Java ``Player.recompute``（ObjBase.pas ApplyItemParameters）。

        AC/MAC 字段按 StdMode 重载成准确 / 幸运 / 敏捷 / 回血等属性；
        套装 Shape、武器幸运、buff 与装备特殊效果都在这里重新推导。
        """
        job = 0 if self.job not in (0, 1, 2) else self.job
        c = {k: 0 for k in (
            "acMin", "acMax", "macMin", "macMax", "dcMin", "dcMax",
            "mcMin", "mcMax", "scMin", "scMax", "hit", "speed",
            "antiMagic", "antiPoison", "poisonRecover", "healthRecover",
            "spellRecover", "hitSpeed", "luck", "unluck",
        )}
        c["antiMagic"] = 1
        c["dcMin"] = c["dcMax"] = BASE_DC[job]
        c["mcMin"] = c["mcMax"] = BASE_MC[job]
        c["scMin"] = c["scMax"] = BASE_SC[job]

        self.luck = 0
        self.suck_health_rate = 0
        self.mana_to_health = 0
        self.paralysis_weapon = False
        self.revival_ring = False
        self.teleport_ring = False
        pset, hset, yset = [False] * 3, [False] * 3, [False] * 3
        hp_add = mp_add = 0

        for it in self.equipment:
            t = self._template(world, it)
            if t is None:
                continue
            mode = t.stdMode
            if mode in (5, 6):                       # 武器：AC=准确/幸运，MAC=攻速/诅咒
                c["hit"] += t.acMax
                c["luck"] += t.acMin
                c["unluck"] += t.macMin
                c["hitSpeed"] += t.macMax - 10 if t.macMax > 10 else -t.macMax
            elif mode == 19:                         # 项链：魔躲 / 幸运
                c["antiMagic"] += t.acMax
                c["unluck"] += t.macMin
                c["luck"] += t.macMax
            elif mode in (20, 24):                   # 项链 / 手镯：准确 / 敏捷
                c["hit"] += t.acMax
                c["speed"] += t.macMax
            elif mode == 21:                         # 回血 / 回蓝 / 攻速
                c["healthRecover"] += t.acMax
                c["spellRecover"] += t.macMax
                c["hitSpeed"] += t.acMin - t.macMin
            elif mode == 23:                         # 毒抗 / 解毒 / 攻速
                c["antiPoison"] += t.acMax
                c["poisonRecover"] += t.macMax
                c["hitSpeed"] += t.acMin - t.macMin
            elif mode == 53:                         # 宝石：HP/MP 加成
                hp_add += t.hpAdd
                mp_add += t.mpAdd
            else:                                    # 其余类型 AC/MAC 即防御
                c["acMin"] += t.acMin
                c["acMax"] += t.acMax
                c["macMin"] += t.macMin
                c["macMax"] += t.macMax
            # DC/MC/SC 对所有 StdMode 统一累加
            c["dcMin"] += t.dcMin
            c["dcMax"] += t.dcMax
            c["mcMin"] += t.mcMin
            c["mcMax"] += t.mcMax
            c["scMin"] += t.scMin
            c["scMax"] += t.scMax

            shape = t.shape
            if shape == SHAPE_PARALYSIS_WEAPON:
                self.paralysis_weapon = True
            if shape == SHAPE_TELEPORT_RING:
                self.teleport_ring = True
            if shape == SHAPE_REVIVAL_RING:
                self.revival_ring = True
            if shape in SHAPE_MANA_TO_HP:
                self.mana_to_health += t.aniCount
            if shape in SHAPE_SUCK_HEALTH:
                self.suck_health_rate += t.aniCount
            for shapes, flags in ((PSET_SHAPES, pset), (HSET_SHAPES, hset),
                                  (YSET_SHAPES, yset)):
                for i, s in enumerate(shapes):
                    if shape == s:
                        flags[i] = True

        # 原版链路（ItmUnit.pas:125 GetItemAddValue + :569-570
        # ApplyItemParameters ITEM_WEAPON 段）：祝福油 btValue[3] 先并入
        # 武器 AC 低字节，然后 btLuck += AC、btUnLuck += MAC，最后
        # m_nLuck := ΣbtLuck − ΣbtUnLuck（ObjBase.pas:3401-3402）——**所有**
        # 装备统一累加，项链幸运不受「有没有武器」影响。
        # 旧口径「specialPwr 1..10 当幸运」是 Java 时代的近似（1.80 数据
        # specialPwr 全是 327xx 高位垃圾），已作废：绿玉裁决的幸运在
        # AcMin=2，正是原版 AC 低字节。油值存 weapon["lucky"]
        # （可负 = 诅咒，对应 btValue[4] 并入 MAC 低字节的语义）。
        weapon = self.equipment[U_WEAPON] if U_WEAPON < len(self.equipment) else None
        oil = int(weapon.get("lucky", 0)) if weapon is not None else 0
        self.luck = max(-10, min(10, c["luck"] + oil - c["unluck"]))

        if pset[1] and pset[2]:
            c["hit"] += 1
            if pset[0]:
                c["dcMin"] += 1
                c["dcMax"] += 3
        if hset[1] and hset[2] and hset[0]:
            c["mcMin"] += 1
            c["mcMax"] += 2
        if yset[1] and yset[2] and yset[0]:
            c["scMin"] += 1
            c["scMax"] += 2

        if self.ac_buff_until > now_ms():
            c["acMax"] += self.ac_buff_bonus
            c["macMax"] += self.mac_buff_bonus

        # 原版 RecalcAbilitys（ObjBase.pas:3424-3451）：状态药水按 6 槽叠加，
        # DC/MC/SC 是**上限** +2+值（MakeLong 只动 HiWord），攻速 Inc(nHitSpeed)，
        # HP/MP 是 MaxHP/MaxMP 加值（Min(65535) 钳制）。
        now = now_ms()
        sa, su = self.status_arr, self.status_until
        dc_add = sa[0] if sa[0] > 0 and su[0] > now else 0
        mc_add = sa[1] if sa[1] > 0 and su[1] > now else 0
        sc_add = sa[2] if sa[2] > 0 and su[2] > now else 0
        spd_add = sa[3] if sa[3] > 0 and su[3] > now else 0
        hp_add2 = sa[4] if sa[4] > 0 and su[4] > now else 0
        mp_add2 = sa[5] if sa[5] > 0 and su[5] > now else 0
        if dc_add:
            c["dcMax"] += 2 + dc_add
        if mc_add:
            c["mcMax"] += 2 + mc_add
        if sc_add:
            c["scMax"] += 2 + sc_add
        if spd_add:
            c["hitSpeed"] += spd_add

        self.combat = c
        # HP/MP 强化水走 MaxHP/MaxMP（原版 :3443/3450，65535 钳制），不是血池。
        self.max_hp = min(65535, BASE_HP[job] + hp_add + (self.level - 1) * HP_GROWTH + hp_add2)
        self.max_mp = min(65535, BASE_MP[job] + mp_add + (self.level - 1) * MP_GROWTH + mp_add2)
        self.hp = max(0, min(self.hp, self.max_hp))
        self.mp = max(0, min(self.mp, self.max_mp))

    def _rng(self, key: str) -> str:
        return f"{self.combat.get(key + 'Min', 0)}-{self.combat.get(key + 'Max', 0)}"

    def wear_weight(self, world: "GameWorld") -> int:
        total = 0
        for i, it in enumerate(self.equipment):
            if i in (U_WEAPON, U_RIGHTHAND):
                continue
            t = self._template(world, it)
            if t is not None:
                total += t.weight
        return total

    def hand_weight(self, world: "GameWorld") -> int:
        total = 0
        for i in (U_WEAPON, U_RIGHTHAND):
            t = self._template(world, self.equipment[i])
            if t is not None:
                total += t.weight
        return total

    def max_wear_weight(self) -> int:
        # 原版 ObjBase.pas:4101/4117/4131：15 + Round((Level/div)*Level)
        div = (20, 100, 50)[self.job if self.job in (0, 1, 2) else 0]
        return 15 + int(self.level / div * self.level + 0.5)

    def max_hand_weight(self) -> int:
        # 同上：12 + Round((Level/div)*Level)，战士 255 封顶
        div = (13, 90, 42)[self.job if self.job in (0, 1, 2) else 0]
        return min(255, 12 + int(self.level / div * self.level + 0.5))

    def instance_json(self, world: "GameWorld", it: dict) -> dict:
        t = self._template(world, it)
        o = {
            "makeIndex": it.get("makeIndex", 0), "index": it.get("index", 0),
            "name": t.name if t else "?", "looks": t.looks if t else 0,
            "durability": it.get("durability", 0),
            "durabilityMax": it.get("durabilityMax", 0),
        }
        if t:
            # 原版 FState.pas GetMouseItemInfo:4238-4240 武器 tooltip：
            # 幸运 = LoWord(AC)（含 GetItemAddValue 并入的 btValue[3] 油值，
            # ItmUnit.pas:125），诅咒 = LoWord(MAC)。specialPwr 在 1.80 数据
            # 里是 327xx 高位（GM 标记之类），**与幸运无关**——旧口径作废。
            # 本项目油值存实例 lucky（可负=诅咒，合并显示）。
            base_luck = t.acMin if t.stdMode in (5, 6) else 0
            o["lucky"] = base_luck + int(it.get("lucky", 0))
            o.update(stdMode=t.stdMode, shape=t.shape, weight=t.weight,
                     dc=t.dcMin, dcMax=t.dcMax, mc=t.mcMin, mcMax=t.mcMax,
                     sc=t.scMin, scMax=t.scMax, ac=t.acMin, acMax=t.acMax,
                     mac=t.macMin, macMax=t.macMax, need=t.need,
                     needLevel=t.needLevel)
        return o

    @staticmethod
    def storage_json(it: dict, name: str, looks: int) -> dict:
        """``ItemInstance.toJson``：仓库下发的精简字段。"""
        return {"makeIndex": it.get("makeIndex", 0), "index": it.get("index", 0),
                "name": name, "looks": looks,
                "durability": it.get("durability", 0),
                "durabilityMax": it.get("durabilityMax", 0)}

    def items_json(self, world: "GameWorld") -> list[dict]:
        out = [self.instance_json(world, it) for it in self.bag]
        for slot, it in enumerate(self.equipment):
            if it is not None:
                e = self.instance_json(world, it)
                e["slot"] = slot
                out.append(e)
        return out

    def skills_json(self, world: "GameWorld") -> dict:
        out: dict[str, dict] = {}
        for mid, s in self.skills.items():
            mt: MagicTemplate | None = world.magic_templates.get(int(mid))
            lv = int(s.get("level", 0))
            # 原版 TClientMagic 同时下发 CurTrain（已修炼点数）与 Magic.Def.MaxTrain，
            # 客户端技能页按「CurTrain/MaxTrain[level]」显示修炼进度
            # （MirClient/FState.pas:3114-3118）。没有 train/maxTrain 就只剩一个等级数字，
            # 玩家无法感知「打了多少刀才升一级」。
            max_train = mt.maxTrain[lv] if mt and lv < len(mt.maxTrain) else 0
            out[str(mid)] = {
                "name": mt.name if mt else str(mid),
                "level": lv, "key": s.get("key", 0),
                "train": int(s.get("train", 0)), "maxTrain": max_train,
                "effectType": mt.effectType if mt else 0,
                "effect": mt.effect if mt else 0,
            }
        return out

    @property
    def max_exp(self) -> int:
        """本级升级所需经验（原版 ``m_Abil.MaxExp = GetLevelExp(Level)``）。

        用 property 而不是字段：等级一变就自动跟着变，不会像旧实现那样在
        ``to_player_json`` 里算一次、之后永远停在登录时的旧值。
        """
        return exp_for_level(self.level)

    def bag_weight(self, world: "GameWorld") -> int:
        """背包总重量（原版 ``m_Abil.Weight``，只统计背包不含身上装备）。"""
        total = 0
        for it in self.bag:
            t = self._template(world, it)
            if t is not None:
                total += t.weight
        return total

    @property
    def max_weight(self) -> int:
        """最大负重（原版 ``m_Abil.MaxWeight``：战士 /3、法师 /5、道士 /4）。

        原版 ObjBase.pas:4101/4117/4131 是 ``50 + Round((Level/div)*Level)``
        （Delphi Round 四舍五入、远离零），旧代码用整除导致中低等级系统性偏严
        ——14 级法师魔法头盔被误判「过重」即由此而起。正数统一 +0.5 取整。
        """
        div = (3, 5, 4)[self.job if self.job in (0, 1, 2) else 0]
        return 50 + int(self.level / div * self.level + 0.5)

    def to_player_json(self, world: "GameWorld") -> dict:
        c = self.combat
        return {
            "id": self.id, "name": self.name, "type": "player",
            "sex": self.sex, "hair": self.hair, "job": self.job,
            "level": self.level, "exp": self.exp, "x": self.x, "y": self.y,
            "dir": self.dir, "hp": self.hp, "maxHp": self.max_hp,
            "mp": self.mp, "maxMp": self.max_mp, "gold": self.gold,
            "race": self.race, "appr": self.appr,
            "nameColor": self.name_color(),
            "dress": self.dress_looks(world), "weapon": self.weapon_shape(world),
            "light": self.light_level(), "dead": self.dead,
            "ac": self._rng("ac"), "mac": self._rng("mac"),
            "dc": self._rng("dc"), "mc": self._rng("mc"), "sc": self._rng("sc"),
            "maxExp": self.max_exp, "weight": self.bag_weight(world),
            "maxWeight": self.max_weight,
            "wearWeight": self.wear_weight(world), "maxWearWeight": self.max_wear_weight(),
            "handWeight": self.hand_weight(world), "maxHandWeight": self.max_hand_weight(),
            "hit": c.get("hit", 0), "speed": c.get("speed", 0),
            "skills": self.skills_json(world), "items": self.items_json(world),
            "quickSlots": [v if v else 0 for v in self.quick_slots[:6]],
        }


@dataclass(slots=True)
class Monster(Actor):
    template: MonsterTemplate | None = None
    spawn_x: int = 0
    spawn_y: int = 0
    spawn_map: GameMap | None = None
    respawn_interval_ms: int = 0
    respawn_at: int = 0
    pending_respawn: bool = False
    target_id: int = -1
    master_id: int = -1
    last_hiter_id: int = -1
    slave_exp: int = 0
    slave_exp_level: int = 0
    slave_make_level: int = 0
    slave_dc_bonus: int = 0
    transformed: bool = False
    change_face_at: int = 0
    next_move_at: int = 0
    # 原版 SM_DEATH 后 actor 不立即删除，而是保留为尸体直到 dwMakeGhostTime
    # （ObjBase.pas:3769，3 分钟）才 MakeGhost。所有怪都留尸体；重生**不复用**
    # 同一 actor（新建实例、新 id），旧尸体继续留到过期，避免 id 冲突。
    corpse_until: int = 0
    butch_count: int = 0
    skeleton: bool = False
    # 原版 TakeBagItems 会把尸体背包清空（ObjBase.pas:20537-20556）：race ≥ 80
    # 的蜘蛛/食人花/洞蛆挖空后**不变骷髅**，尸体仍可再挖，但背包已空
    # （第二次起只报「未发现任何物品」），所以要用标记防重复掷骰。
    bag_looted: bool = False
    # 挖肉磨损（原版 m_nBodyLeathery，初值 50，每次挖 Random(16)+5）
    body_leathery: int = BODY_LEATHERY
    # 原版 m_nMeatQuality（ObjBase.pas:185）：肉品质，挖肉出肉时写进肉耐久
    # （ApplyMeatQuality:20517），卖价随品质变化。出生按 UsrEngn.pas:1835-1864
    # 初始化：鸡 3000~6500、鹿/狼 8000~16000；被打/中毒/挖肉都会降低。
    meat_quality: int = 0
    next_attack_at: int = 0
    # 下次允许做全图目标扫描的时刻（MON_SCAN_MS 错峰节流，见 _acquire_target）
    next_scan_at: int = 0
    poison_ticks: int = 0
    poison_armor_until: int = 0
    poison_damage: int = 0
    last_poison_at: int = 0
    last_move_at: int = 0
    last_attack_at: int = 0
    paralyzed_until: int = 0
    # 原版 TStickMonster.m_boFixedHideMode（ObjMon2.pas:164）：食人花潜地隐藏，
    # 目标靠近 abs<4 出土，目标离开 abs>4 潜回。隐藏时不进视野快照。
    hidden: bool = False
    # 原版 TChickenDeer（ObjMon.pas:531，race 52 鹿/羊 1/30 变种）：
    # 视野 5、见人就跑，永不攻击（UsrEngn.pas:1844）。
    chicken_deer: bool = False
    # 原版 TZilKinZombi（ObjMon.pas:1263-1313，race 96 复活僵尸）：
    # nZilKillCount —— 出生 1/3 概率为 Random(3)+1（1~3），每次死亡无条件
    # Dec；>0 时死亡会安排复活（dw558/dw560：死后 (Random(20)+4)*1000 ms）。
    zil_revives: int = 0
    zil_revive_at: int = 0

    def __post_init__(self) -> None:
        # 原版 ``m_dwFightExp`` 是**实例**字段（MonInitialize 取 Monster.DB.Exp，
        # UsrEngn.pas:2612 一带），不是模板值：race 96 复活僵尸每次原地复活都会
        # ``m_dwFightExp := m_dwFightExp div 2``（ObjMon.pas:1305）。因此杀怪结算
        # 必须读 ``m.exp``；这里在所有构造点统一初始化，避免漏赋值结算成 0。
        if self.template is not None and self.exp <= 0:
            self.exp = int(getattr(self.template, "exp", 0) or 0)

    def roll_attack(self) -> int:
        """对齐 ``Monster.rollAttack``：DC ~ max(DC,MaxDC) 上加宠物等级加成。"""
        t = self.template
        if t is None:
            return 1
        lo = t.dc + self.slave_dc_bonus
        hi = max(t.dc, t.maxDc) + self.slave_dc_bonus
        return lo + int(random.random() * (hi - lo + 1))

    def brief_json(self) -> dict:
        # 同 Player：slots dataclass 在 3.10~3.12 下禁用零参数 super()。
        o = Actor.brief_json(self)
        # 原版 GetShowName → FilterShowName：Monster.csv 的「僵尸3/僵尸20」等
        # 变体编号不能显示给玩家（M2Share.pas:3630）。
        o["name"] = filter_show_name(self.name)
        if self.master_id >= 0 and 0 <= self.slave_exp_level < len(SLAVE_COLORS):
            o["nameColor"] = SLAVE_COLORS[self.slave_exp_level]
        else:
            # 普通怪仍是原版默认白；增强模式打开时前端按这个索引着色。
            o["nameColor"] = NAME_COLOR_DEFAULT
        if self.master_id >= 0:
            # 原版客户端靠名字里的「名(主人名)」括号识别宝宝（ClMain.pas:2852），
            # 本服宝宝名字不带括号 → 显式下发 masterId 供前端过滤误伤。
            o["masterId"] = self.master_id
        # 尸体仍要作为实体下发给客户端（原版 SM_DEATH 保留 actor，画 ActDeath 帧）
        o["alive"] = self.alive
        o["corpse"] = (not self.alive and self.corpse_until > 0)
        # 挖空变骨架（RM_SKELETON → m_boSkeleton）：重进地图/回到视野时也要恢复
        # 骨架帧（Actor.pas:2124 GetDefaultFrame 骷髅走 ActDeath.start 不带方向），
        # 否则玩家走远再回来尸体「变回」未挖状态——挖过肉的尸体重复可挖。
        o["skeleton"] = bool(self.skeleton)
        return o


# ---- 命中 / 敏捷点数（原版 m_btHitPoint / m_btSpeedPoint）------------------

def delphi_random(n: int) -> int:
    """Delphi ``Random(n)``：0..n-1 均匀分布；``n <= 0`` 时返回 0（不抛异常）。

    原版命中判定全靠 ``Random(m_btSpeedPoint)``，怪/玩家敏捷为 0 的情况
    （例如 Monster.DB Speed 列填 0 的怪）必须按 0 处理，不能抛 ValueError。
    """
    return random.randrange(n) if n > 0 else 0


def hit_point(actor) -> int:
    """原版 ``m_btHitPoint``（准确 / 命中点）。

    玩家：``DEFHIT + 装备准确 + 剑术技能加成``（ObjBase.pas:3395 / 18563 /
    18590-18632，装备准确 1:1 原始累加，只剩可分配加点才按技能 tick 折算）。
    怪物：``Monster.DB.Hit``（UsrEngn.pas:2606）。
    """
    if isinstance(actor, Player):
        hit = DEF_HIT + int(actor.combat.get("hit", 0))
        for magic_id, num, den in SKILL_HIT_BONUS:
            lv = int(actor.skills.get(magic_id, {}).get("level", 0))
            if lv > 0:
                hit += java_round(num * lv / den)
        return hit
    if isinstance(actor, Monster):
        return int(getattr(actor.template, "hit", 0) or 0) if actor.template else 0
    return 0


def speed_point(actor) -> int:
    """原版 ``m_btSpeedPoint``（敏捷 / 闪避点）。

    玩家：``DEFSPEED + 装备敏捷``，道士额外 +3（ObjBase.pas:3394 / 18565-18570）；
    怪物：``Monster.DB.Speed``（UsrEngn.pas:2605）。
    """
    if isinstance(actor, Player):
        sp = DEF_SPEED + int(actor.combat.get("speed", 0))
        return sp + 3 if actor.job == 2 else sp
    if isinstance(actor, Monster):
        return int(getattr(actor.template, "speed", 0) or 0) if actor.template else 0
    return 0


def melee_hit(attacker, target) -> bool:
    """近战命中判定：``TBaseObject._Attack``（ObjBase.pas:22240-22247）。

    ```pascal
    if AttackTarget.m_btHitPoint > 0 then       // 目标命中点 > 0 才可能被闪避
      if m_btHitPoint < Random(AttackTarget.m_btSpeedPoint) then nPower := 0;
    ```
    命中率 = ``(min(hit+1, speed)) / speed``，即命中点 ≥ 敏捷-1 时必中。
    """
    if hit_point(target) <= 0:
        return True
    return not (hit_point(attacker) < delphi_random(speed_point(target)))


def mag_struck_damage(target, dmg: int, now: int | None = None) -> int:
    """原版 ``TBaseObject.GetMagStruckDamage``（ObjBase.pas:22441）。

    ```pascal
    n14 := LoWord(MAC) + Random(HiWord(MAC) - LoWord(MAC) + 1);
    nDamage := _MAX(0, nDamage - n14);
    if (nDamage > 0) and m_boAbilMagBubbleDefence then
      nDamage := Round((nDamage/100) * (btMagBubbleDefenceLevel+2) * 8);
    ```

    火炬源码是 16/24/32/40%（私服口径）；本项目按热血传奇**官服**
    （BV1nQrkYYEM7）取 15×（等级+1）= 15/30/45/60%。

    两条要点：**MAC 只减伤不闪避**；魔法盾只作用于魔法伤害
    —— 物理路径 ``_apply_hit_to_player`` 不过这一步。

    火墙的 ``RM_MAGSTRUCK_MINE``、火球/雷电的 ``RM_MAGSTRUCK``、
    ``RM_DELAYMAGIC`` 的门槛判定都走这里。
    """
    if now is None:
        now = now_ms()
    compat = getattr(target, "combat", None)
    if compat is not None:                    # 玩家：MAC 区间随机
        lo = int(compat.get("macMin", 0))
        hi = int(compat.get("macMax", 0))
        dmg = max(0, dmg - (lo + delphi_random(hi - lo + 1)))
        if dmg > 0 and getattr(target, "shield_until", 0) > now:
            # 官服 15/30/45/60%（BV1nQrkYYEM7）；火炬私服口径为 (lv+2)*8。
            dmg = delphi_round(dmg * 15 * (getattr(target, "shield_level", 0) + 1) / 100)
        return dmg
    tpl = getattr(target, "template", None)
    mac = getattr(tpl, "mac", 0) if tpl is not None else 0
    return max(0, dmg - int(mac or 0))


def positive_hit(attacker, target) -> bool:
    """原版**正向**写法 ``Random(目标敏捷) < 命中点`` 才命中。

    用在两处（与近战 ``melee_hit`` 的 ``命中 < Random(敏捷)`` 差 1 点容差，
    原版两处写法本来就不一致，这里分别保留）：
    - ``DirectAttack``（ObjBase.pas:21978）：刺杀/半月扫到的**副目标**；
    - 怪物远程 ``TSpitSpider.SpitAttack`` / ``TArrowMonster``（ObjMon.pas:699/829/890）。
    """
    return delphi_random(speed_point(target)) < hit_point(attacker)


# ---------------------------------------------------------------- 世界

class GameWorld(WorldExtra):
    def __init__(self, config: ServerConfig, db: Database):
        self.config = config
        self.db = db
        self.item_templates = db.load_item_templates()
        self.monster_templates = db.load_monster_templates()
        self.magic_templates = db.load_magic_templates()

        self.maps: dict[str, GameMap] = {}
        self.actors: dict[int, Actor] = {}
        self.players_by_name: dict[str, Player] = {}
        # 旁观者血条同步：玩家 hp 变化时向周围广播（见 _sync_player_hp）
        self._hp_sync_cache: dict[int, tuple[int, int]] = {}
        self._hp_sync_at = 0
        self._last_despawn_check = 0
        self.ground_items: dict[int, dict] = {}
        # 挖矿矿点存量（原版 TStoneMineEvent，Event.pas:84-94）：键 (地图, x, y)
        # 值 [剩余量, 生成/补矿 tick]。存量创建时 Random(200)，挖空 10 分钟后
        # AddStoneMine 补 Random(80)。只对 MINE/MINE2 标志地图生效。
        self.mine_state: dict[tuple[str, int, int], list] = {}
        self._id_gen = itertools.count(1)
        self._item_seq = itertools.count(max(1_000_000, db.max_item_make_index() + 1))
        self.day_bright = day_bright_for_hour(datetime.now().hour)
        self._last_day_check = 0
        self.last_regen_at = now_ms()

        sp = parse_start_point(config.envir_dir / "StartPoint.txt")
        self.start_x, self.start_y = sp.x, sp.y
        self.default_map = GameMap(read_map(sp.map_id, config.map_dir))
        self.maps[self.default_map.name] = self.default_map
        # 全部出生点登记（原版 CheckHomePos 用：走到出生点 ±50 格内即搬家，
        # 回城卷/死亡回城回的就是这个动态家点；ObjBase.pas:13103-13121）
        self.start_points = parse_start_points(config.envir_dir / "StartPoint.txt")
        self._home_check: dict[int, int] = {}   # player_id -> 上次检查时刻

        self.available_maps = scan_available_maps(config.map_dir)
        # 磁盘文件名大小写不统一（d710.map / D701.map），MapInfo.txt 里的
        # gate id 也不统一（D710 / D701）。Windows 文件系统不敏感所以一直
        # 没暴露，Linux 部署会整条门被丢弃（石墓 D710、地牢 D601 等）。
        # 存在性判断一律走小写集合；gate / map 的 id 本身保留 MapInfo 原始
        # 大小写，因为 map_titles / map_flags / mini_index 的 key 就是它。
        self._available_lower = {m.lower() for m in self.available_maps}
        info: MapInfo = parse_map_info(config.envir_dir / "MapInfo.txt")
        self.map_titles = info.titles
        self.map_flags = info.flags          # DARK / DAY / MINE 等地图标志
        # 与 Java 一致：两端都没有 .map 文件的连接不生效。
        self.gates_by_src: dict[str, list[Gate]] = {}
        kept = 0
        for g in info.gates:
            if (g.src_map.lower() not in self._available_lower
                    or g.dst_map.lower() not in self._available_lower):
                continue
            self.gates_by_src.setdefault(g.src_map, []).append(g)
            kept += 1
        print(f"[world] loaded {len(info.titles)} map defs, {kept} gates")

        # 大地图入口只标「可探索区域」（洞穴/野外大图，用户约定：主城村庄
        # 进房间的入口不显示）。判据 = 目标图 max(w,h) >= 100：房间图实测
        # 最大 55（皇宫 51x55），洞穴/野外最小 200（D401 200x200），中间无
        # 过渡，阈值取 100 稳分离。尺寸只读 .map 头 4 字节。
        self.cave_maps: set[str] = set()
        for _gs in self.gates_by_src.values():
            for _g in _gs:
                if _g.dst_map not in self.cave_maps:
                    _s = map_size(_g.dst_map, config.map_dir)
                    if _s and max(_s) >= 100:
                        self.cave_maps.add(_g.dst_map)
        # 洞穴系列必经大厅（石墓入口 / 祖玛大殿）显式纳入，见 BIGMAP_EXTRA_ENTRIES 注释。
        # 必须两端 .map 都存在才加，避免标注指向不存在的图。
        for _mid in BIGMAP_EXTRA_ENTRIES:
            if _mid.lower() in self._available_lower:
                self.cave_maps.add(_mid)

        self.map_mini_index = parse_mini_map(config.envir_dir / "MiniMap.txt")
        print(f"[world] loaded {len(self.map_mini_index)} minimap entries")
        self.mon_gen_entries: list[MonGen] = parse_mon_gen(config.envir_dir / "MonGen.txt")
        self.notice_lines = parse_notice(config.notice_file)
        self.spawn_monsters(self.default_map)
        self.init_extra()
        # 我本沉默私服内容剥离（config/content_policy.json）
        from .content_filter import ContentPolicy, filter_world
        self.content_policy = ContentPolicy.load(config.root)
        self.content_filter_stat = filter_world(self.content_policy, self)
        if self.content_policy.enabled:
            s = self.content_filter_stat
            # items/monsters 在 db 层已从源头过滤（这里为 0 是正常的，看 db 日志）
            print(f"[world] content policy: -maps={s['maps']} -spawns={s['mon_gen']} "
                  f"-npcs={s['npcs']} high_drop_reset={len(s['high_drop'])}")

    # ---- id / 生命周期 -------------------------------------------------

    def new_id(self) -> int:
        return next(self._id_gen)

    def next_item_make_index(self) -> int:
        return next(self._item_seq)

    def player(self, name: str) -> Player | None:
        return self.players_by_name.get(name)

    def players(self) -> list[Player]:
        return list(self.players_by_name.values())

    def online_count(self) -> int:
        return len(self.players_by_name)

    def monster_count(self) -> int:
        return sum(1 for a in self.actors.values() if isinstance(a, Monster) and a.alive)

    def add_actor(self, actor: Actor, game_map: GameMap | None = None) -> None:
        self.actors[actor.id] = actor
        (game_map or actor.map or self.default_map).add(actor)

    def add_player(self, p: Player) -> None:
        self.actors[p.id] = p
        self.players_by_name[p.name] = p
        if p.map is not None:
            p.map.add(p)

    def remove_player(self, p: Player) -> None:
        # 主人登出 = 原版 m_boGhost：宝宝 1 秒后 MakeGhost（ObjBase.pas:3986），
        # 不清理的话骷髅会永远留在原地，主人下次上线还带着旧的召唤 id。
        for sid in list(p.summon_ids):
            summon = self.actor(sid)
            if isinstance(summon, Monster) and summon.alive:
                self.remove_summon(summon)
        p.summon_ids.clear()
        self.actors.pop(p.id, None)
        self.players_by_name.pop(p.name, None)
        self._home_check.pop(p.id, None)
        if p.map is not None:
            p.map.remove(p.id)

    def remove_actor(self, actor_id: int) -> None:
        a = self.actors.pop(actor_id, None)
        if a is None:
            return
        if a.map is not None:
            a.map.remove(actor_id)
            self.broadcast_around(a.map, {"type": "entityRemove", "id": actor_id},
                                  a.x, a.y, SIGHT_RANGE)

    # ---- 刷怪 -----------------------------------------------------------

    def _free_near(self, game_map: GameMap, cx: int, cy: int, radius: int) -> tuple[int, int] | None:
        r = max(0, radius)
        for _ in range(50):
            x = cx + (random.randint(-r, r) if r > 0 else 0)
            y = cy + (random.randint(-r, r) if r > 0 else 0)
            if game_map.can_walk(x, y) and not game_map.occupied(x, y):
                return x, y
        return (cx, cy) if game_map.can_walk(cx, cy) else None

    def _front_near(self, game_map: GameMap, cx: int, cy: int,
                    direction: int) -> tuple[int, int] | None:
        """正前方一格（原版 ``GetFrontPosition``，ObjBase.pas:4298）。

        召唤物落点用它：``TBaseObject.MakeSlave`` 先 ``GetFrontPosition`` 再
        ``RegenMonsterByName``（7167-7168），所以骷髅总是出现在主人面朝的那一格，
        不是随机撒在周围。正前方被占/不可走时退回 ``_free_near`` 兜底。
        """
        dx, dy = direction_delta(direction)
        fx, fy = cx + dx, cy + dy
        if (game_map.inside(fx, fy) and game_map.can_walk(fx, fy)
                and not game_map.occupied(fx, fy)):
            return fx, fy
        return self._free_near(game_map, cx, cy, 2)

    def _back_near(self, game_map: GameMap, cx: int, cy: int,
                   direction: int) -> tuple[int, int] | None:
        """背后一格（原版 ``GetBackPosition``，ObjBase.pas:22583）：宝宝瞬移落点。

        背后被墙/被占时逐步扩大搜索（原版 ``SpaceMove`` 的 ``GetRandXY`` 也是
        找不到可走点就继续挪，直到 201 次），保证宝宝真能跟过来而不是留在原地。
        """
        dx, dy = direction_delta((direction + 4) & 7)
        bx, by = cx + dx, cy + dy
        if (game_map.inside(bx, by) and game_map.can_walk(bx, by)
                and not game_map.occupied(bx, by)):
            return bx, by
        for radius in (1, 2, 3, 4, 6, 8):
            for ddx in range(-radius, radius + 1):
                for ddy in range(-radius, radius + 1):
                    if max(abs(ddx), abs(ddy)) != radius:
                        continue
                    x, y = cx + ddx, cy + ddy
                    if (game_map.inside(x, y) and game_map.can_walk(x, y)
                            and not game_map.occupied(x, y)):
                        return x, y
        return None

    def spawn_monsters(self, game_map: GameMap) -> int:
        spawned = 0
        for entry in self.mon_gen_entries:
            if entry.map_id != game_map.name:
                continue
            t = self.monster_templates.get(entry.monster_name)
            if t is None:
                continue
            for _ in range(max(entry.count, 0)):
                pos = self._free_near(game_map, entry.x, entry.y, entry.range_)
                if pos is None:
                    continue
                self._spawn_monster(game_map, t, pos[0], pos[1],
                                    entry.interval_min * 60_000 if entry.interval_min > 0 else 0)
                spawned += 1
        if spawned:
            print(f"[world] map={game_map.name} {game_map.width}x{game_map.height}, "
                  f"spawned monsters={spawned}")
        game_map.mon_spawned = True
        return spawned

    def _spawn_monster(self, game_map: GameMap, t, x: int, y: int,
                       respawn_interval_ms: int = 0) -> Monster:
        m = Monster(id=self.new_id(), name=t.name,
                    x=x, y=y, dir=4,
                    hp=t.hp, max_hp=t.hp, mp=t.mp, max_mp=t.mp,
                    level=t.level, race=t.race, race_img=t.raceImg, appr=t.appr,
                    template=t, spawn_x=x, spawn_y=y,
                    respawn_interval_ms=respawn_interval_ms)
        m.exp = t.exp
        init_meat_quality(m)
        self._init_race_traits(m, t)
        self.add_actor(m, game_map)
        return m

    def regen_monster_by_name(self, map_id: str, x: int, y: int, name: str) -> bool:
        """原版 ``UserEngine.RegenMonsterByName``（NPC 脚本 MonGen / MakeSlave）：
        按名字查模板刷一只；模板或地图不存在静默失败（返回 False）。"""
        t = self.monster_templates.get(name)
        if t is None:
            return False
        game_map = self.get_or_load_map(map_id)
        if game_map is None:
            return False
        pos = None
        # 落点自指定格起环形全扫描（原版 MonGen 玩家语境下怪挤在测验小屋，
        # 随机尝试会在高密度时漏放；环形扫描保证只要还有空格就能放下）。
        for radius in range(0, 13):
            for dx in range(-radius, radius + 1):
                for dy in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    px, py = x + dx, y + dy
                    if (game_map.inside(px, py) and game_map.can_walk(px, py)
                            and not game_map.occupied(px, py)):
                        pos = (px, py)
                        break
                if pos is not None:
                    break
            if pos is not None:
                break
        if pos is None:
            return False
        self._spawn_monster(game_map, t, pos[0], pos[1])
        return True

    def count_map_monsters(self, map_id: str) -> int:
        gm = self.maps.get(map_id)
        if gm is None:
            return 0
        return sum(1 for a in gm.actors.values()
                   if isinstance(a, Monster) and a.alive)

    def count_map_humans(self, map_id: str) -> int:
        return sum(1 for p in self.players_by_name.values()
                   if p.map is not None and p.map.name == map_id)

    def start_time_recall(self, p: Player, minutes: int) -> None:
        """原版 ``TPlayObject`` 脚本 ``TimeRecall``（ObjNpc.pas:7800-7806）：
        记录玩家当前 map/x/y，n 分钟后 SpaceMove 送回。取消点只有两处：
        到期执行本身、重登（新 Player 对象字段重置）；脚本 ``map`` 换图
        **不**取消（原版取消在 BaseObjectMove，回城类传送才走它）。"""
        p.time_recall = {"map": p.map.name if p.map else "",
                         "x": p.x, "y": p.y,
                         "expire": now_ms() + max(minutes, 0) * 60_000}

    def _tick_time_recall(self, p: Player, now: int) -> None:
        """原版 ObjBase.pas:6440-6443：到期 SpaceMove 回记录点。"""
        tr = p.time_recall
        if not tr or now < int(tr["expire"]):
            return
        p.time_recall = None
        if self.space_move(p, tr["map"], int(tr["x"]), int(tr["y"])) is not None:
            if p.session is not None:
                p.session.send_json(self.map_snapshot(p, "changeMap"))
                self.broadcast_entity_add(p)
                self.space_move_show(p)

    def _tick_pk(self, p: Player, now: int) -> None:
        """PK 点衰减 + 灰名到期（原版 ObjBase.pas:7719-7724 / :2866-2870）。

        - 在线每 ``PK_DECAY_INTERVAL_MS``（2 分钟）``DecPkPoint(1)``，离线不补算。
          ``pk_decay_at`` 为 0（读档/新角色）时以当前时刻为基准，避免一上线
          就把积攒的离线时间一次性扣掉（原版也没有离线补算）。
        - 灰名 60 秒到期清 ``BoIllegalAttack``，并通过 RM_CHANGENAMECOLOR
          刷新名字颜色（本项目广播 entityAdd 让周围重绘）。
        """
        changed = False
        if p.illegal_attack_until and now >= p.illegal_attack_until:
            p.illegal_attack_until = 0
            changed = True
        if p.pk_point > 0:
            if not p.pk_decay_at:
                p.pk_decay_at = now
            elif now - p.pk_decay_at >= PK_DECAY_INTERVAL_MS:
                p.pk_decay_at = now
                p.pk_point -= 1
                changed = True
        elif p.pk_decay_at and p.pk_point <= 0:
            p.pk_decay_at = 0
        if changed:
            # 名字颜色（黄→白/灰→白）要即时反映给周围玩家。
            self.broadcast_entity_add(p)

    def clear_map_monsters(self, map_id: str) -> int:
        """原版脚本 ``MonClear``（MapMonster 清怪）：直接移除活怪（无尸体无掉落）。"""
        gm = self.maps.get(map_id)
        if gm is None:
            return 0
        removed = 0
        for a in [a for a in gm.actors.values()
                  if isinstance(a, Monster) and a.alive]:
            gm.remove(a.id)
            self.actors.pop(a.id, None)
            self.broadcast_around(gm, {"type": "entityRemove", "id": a.id},
                                  a.x, a.y, SIGHT_RANGE)
            removed += 1
        return removed

    # ---- 地图 -----------------------------------------------------------

    def get_or_load_map(self, name: str) -> GameMap | None:
        if not name:
            return self.default_map
        if name in self.maps:
            return self.maps[name]
        if name.lower() not in self._available_lower:
            return None
        game_map = GameMap(read_map(name, self.config.map_dir))
        self.maps[name] = game_map
        self.spawn_monsters(game_map)
        return game_map

    def gate_at(self, map_id: str, x: int, y: int) -> Gate | None:
        return next((g for g in self.gates_by_src.get(map_id, ())
                     if g.src_x == x and g.src_y == y), None)

    def change_map(self, p: Player, dst_map_id: str, x: int, y: int) -> GameMap | None:
        target = self.get_or_load_map(dst_map_id)
        if target is None:
            return None
        if p.map is not None:
            p.map.remove(p.id)
        p.x, p.y = x, y
        target.add(p)
        self._follow_summons(p)
        self.player_entered(p)
        # 原版 SM_CHANGEMAP 后客户端 CancelAction 停住动作；这里用短锁拦下
        # 换图消息到达前已在网络中的 move，否则会落在新地图继续走，
        # 容易一脚踩中紧邻落点的回程门被弹回（例如 D401 (76,15) ↔ D411 (60,7)）。
        p.map_change_at = now_ms()
        return target

    # ---- 传送（SpaceMove，与过门不同：过门 EnterAnotherMap 无特效） --------

    def space_move(self, p: Player, dst_map_id: str, x: int, y: int,
                   variant: int = 0) -> GameMap | None:
        """原版 ``TBaseObject.SpaceMove``（ObjBase.pas:4359）两段传送特效。

        - 离开前 ``RM_SPACEMOVE_FIRE`` → 客户端 ``SM_SPACEMOVE_HIDE``：
          ``TScrollHideEffect.Create(250, 10, x, y, self)``（magic 库 250-259，
          10 帧 × 30ms，固定在旧坐标格）+ 音 ``s_spacemove_out(109)``
          （Client\\Actor.pas:1635）。
        - 到达后由调用方先发 changeMap 快照，再调 :meth:`space_move_show`：
          ``nInt=1 → RM_SPACEMOVE_SHOW2(1600)``, 否则 ``RM_SPACEMOVE_SHOW(260)``，
          ``TCharEffect.Create(..., 10, self)`` 10 帧 × 30ms 跟随角色 +
          音 ``s_spacemove_in(110)``（Actor.pas:1645-1658）。
        variant=1 只给瞬息移动技能与宝宝跟随（ObjMon.pas:494 等），其余
        （随机/回城卷 EatUseItems Shape1/3、GM 移动、NPC 脚本 MOVE）都是 0。

        消息顺序（对移动者本人）：spaceMoveHide → changeMap 快照 → spaceMoveShow。
        """
        self.broadcast_around(p.map, {"type": "entityRemove", "id": p.id},
                              p.x, p.y, SIGHT_RANGE)
        self.broadcast_around(p.map, {"type": "spaceMoveHide", "x": p.x, "y": p.y,
                                      "variant": variant}, p.x, p.y, SIGHT_RANGE)
        return self.change_map(p, dst_map_id, x, y)

    def space_move_show(self, p: Player, variant: int = 0) -> None:
        """SpaceMove 第二段：新地图上的现身特效（SM_SPACEMOVE_SHOW/SHOW2）。"""
        self.broadcast_around(p.map, {"type": "spaceMoveShow", "x": p.x, "y": p.y,
                                      "variant": variant}, p.x, p.y, SIGHT_RANGE)

    def try_move(self, a: Actor, direction: int) -> bool:
        dx, dy = direction_delta(direction)
        nx, ny = a.x + dx, a.y + dy
        a.dir = direction
        game_map = a.map
        if game_map is None or not game_map.inside(nx, ny):
            return False
        if game_map.is_door(nx, ny) and not game_map.is_door_open(nx, ny):
            if not isinstance(a, Player):
                return False
            game_map.open_door(nx, ny, now_ms())
            self.broadcast_around(game_map, {"type": "door", "x": nx, "y": ny, "open": True},
                                  nx, ny, SIGHT_RANGE)
        if not game_map.can_walk(nx, ny):
            return False
        if game_map.occupied(nx, ny):
            return False
        game_map.reposition(a, nx, ny)
        return True

    # ---- 广播 / 快照 ----------------------------------------------------

    def broadcast_all(self, msg: dict) -> None:
        text = encode(msg)
        for p in self.players_by_name.values():
            if p.session is not None:
                p.session.send_text(text)

    def broadcast_around(self, game_map: GameMap | None, msg: dict,
                         x: int, y: int, radius: int = SIGHT_RANGE) -> None:
        """只遍历图上玩家（``map.players`` 索引），空图零开销直接返回。

        怪物 AI 每次移动/攻击都走这里，大图满怪时旧的「遍历全部实体 +
        isinstance 过滤」是纯浪费 —— 玩家始终是极少数。
        """
        if game_map is None:
            return
        players_map = getattr(game_map, "players", None)
        if players_map is not None and not players_map:
            return
        text = encode(msg)
        if players_map is not None:
            for a in players_map.values():
                if a.session is not None \
                        and max(abs(a.x - x), abs(a.y - y)) <= radius:
                    a.session.send_text(text)
            return
        for a in game_map.actors.values():   # 兼容缺 players 索引的测试 shim
            if isinstance(a, Player) and a.session is not None \
                    and max(abs(a.x - x), abs(a.y - y)) <= radius:
                a.session.send_text(text)

    def broadcast_move(self, a: Actor) -> None:
        # 原版 SM_WALK / SM_RUN / SM_TURN 每条都携带 TCharDesc.feature+status
        # （ObjBase.pas:5292-5312 / 5430-5460），客户端收到未知 id 会凭 feature
        # 现场创建 actor。这里同样把实体快照附带在 move 里：怪物从视野外
        # （>12 格）走进来时，站着的玩家只收得到 move（update_sight 只在玩家
        # 自己移动时才跑）——没有 entity 就是「怪已贴身砍人（有挨打音效）
        # 但看不见、一动才出现」的直接原因。
        self.broadcast_around(a.map, {"type": "move", "id": a.id, "x": a.x, "y": a.y,
                                      "dir": a.dir, "run": a.running,
                                      "entity": a.brief_json()}, a.x, a.y)

    def broadcast_entity_add(self, a: Actor) -> None:
        self.broadcast_around(a.map, {"type": "entityAdd", "entity": a.brief_json()}, a.x, a.y)

    def send_system(self, p: Player, text: str, fg: str | None = None,
                    bg: str | None = None) -> None:
        if p.session is None:
            return
        msg = {"type": "systemMessage", "text": text}
        if fg:
            msg["fg"] = fg
        if bg:
            msg["bg"] = bg
        p.session.send_json(msg)

    def send_stats(self, a: Actor, extras: dict | None = None) -> None:
        now = now_ms()
        o: dict[str, Any] = {
            "type": "stats", "id": a.id, "hp": a.hp, "maxHp": a.max_hp,
            "mp": a.mp, "maxMp": a.max_mp, "level": a.level, "exp": a.exp,
        }
        if isinstance(a, Player):
            c = a.combat
            # 原版 SM_ABILITY 一次带全：经验/升级经验/负重/最大负重 都在这条消息里。
            # 旧实现只发 exp 不发 maxExp，客户端 no 升级后永远拿着登录时的旧 MaxExp，
            # EXP 条横竖都不准（面板会出现「当前经验 1183 / 升级经验 400」）。
            o.update(maxExp=a.max_exp,
                     gm=isinstance(a, Player) and a.is_gm,
                     weight=a.bag_weight(self), maxWeight=a.max_weight,
                     shield=a.shield_until > now, hidden=a.hide_until > now,
                     wideHit=a.wide_hit_on, longHit=a.long_hit_on, fireHit=a.fire_hit_on,
                     ac=f"{c.get('acMin', 0)}-{c.get('acMax', 0)}",
                     mac=f"{c.get('macMin', 0)}-{c.get('macMax', 0)}",
                     dc=f"{c.get('dcMin', 0)}-{c.get('dcMax', 0)}",
                     mc=f"{c.get('mcMin', 0)}-{c.get('mcMax', 0)}",
                     sc=f"{c.get('scMin', 0)}-{c.get('scMax', 0)}",
                     hit=c.get("hit", 0), speed=c.get("speed", 0))
        o["poison"] = getattr(a, "poison_ticks", 0) > 0
        o["poisonArmor"] = getattr(a, "poison_armor_until", 0) > now
        if isinstance(a, Monster):
            o["paralyzed"] = a.paralyzed_until > now
        if extras:
            o.update(extras)
        if isinstance(a, Player) and a.session is not None:
            a.session.send_json(o)
        else:
            self.broadcast_around(a.map, o, a.x, a.y)

    # ---- 装备持久磨损（原版 DoDamageWeapon / StruckDamage）-----------------

    def send_bag_update(self, p: Player) -> None:
        """``GameService.sendBagUpdate``：全量背包 + 金币 + 快捷栏 + 负重。

        从 GameService 下沉到 GameWorld：装备被磨损/损毁的推送在怪物 AI
        受击路径（wear_struck）里也要发，而怪物 AI 在 world 层。"""
        if p.session is None:
            return
        p.session.send_json({
            "type": "bagUpdate", "gold": p.gold,
            "dress": p.dress_looks(self), "weapon": p.weapon_shape(self),
            "weight": p.bag_weight(self), "maxWeight": p.max_weight,
            "wearWeight": p.wear_weight(self), "handWeight": p.hand_weight(self),
            "maxWearWeight": p.max_wear_weight(), "maxHandWeight": p.max_hand_weight(),
            "quickSlots": [p.quick_slots[i] if p.quick_slot_valid(i) else 0
                           for i in range(len(p.quick_slots))],
            "items": p.items_json(self),
        })

    def send_dura_change(self, p: Player, slot: int, it: dict) -> None:
        """原版 RM_DURACHANGE → SM_DURACHANGE（Grobal2.pas:642 = 642 号消息）：
        客户端 ClientGetDuraChange（ClMain.pas:5125→6132）按装备栏 slot 原地
        更新持久显示，不重建物品。"""
        if p.session is None:
            return
        p.session.send_json({
            "type": "duraChange", "slot": slot,
            "durability": int(it.get("durability", 0)),
            "durabilityMax": int(it.get("durabilityMax", 0)),
        })

    def do_damage_weapon(self, p: Player, amount: int) -> None:
        """原版 ``TBaseObject.DoDamageWeapon``（ObjBase.pas:18967-19009）。

        - 持久扣 amount；扣到 ≤ 0 时武器**直接损毁清槽**（SendDelItems +
          ``wIndex := 0``，GameOfMir 里不是留在身上变「已破坏」）；
        - 节流：``Round(持久/1.03)``（:18975/19005）变化才发 SM_DURACHANGE。
        """
        if p.equipment is None:
            return
        it = p.equipment[U_WEAPON]
        if it is None:
            return
        old = int(it.get("durability", 0) or 0)
        old_point = delphi_round(old / 1.03)
        dura = old - amount
        if dura <= 0:
            it["durability"] = 0
            p.equipment[U_WEAPON] = None
            self.send_system(p, "武器持久用完，已损毁。")
            self.send_bag_update(p)
            return
        it["durability"] = dura
        if delphi_round(dura / 1.03) != old_point:
            self.send_dura_change(p, U_WEAPON, it)

    def mark_illegal_attack(self, attacker: Player, victim: Player, now: int) -> None:
        """原版 ``AddPkHiter``（ObjBase.pas:2824-2837）：白/黄名玩家主动攻击
        另一个白/黄名玩家时，给攻击者置 ``BoIllegalAttack`` 灰名 60 秒。

        灰名的意义：受害者反击杀死攻击者时，``IsGoodKilling``（:2894-2910）
        看到攻击者是灰名 → 判正当防卫，反击者不加 PK 点。红名双方互殴不
        置灰名（原版 `if (PkLevel<2) and (hiter.PkLevel<2)`）。
        """
        if attacker.pk_point >= RED_NAME_PK_POINT or victim.pk_point >= RED_NAME_PK_POINT:
            return
        # 受害者已是灰名 = 受害者先动的手，这里攻击者是反击方，不再置灰名。
        if victim.illegal_attack_until > now:
            return
        attacker.illegal_attack_until = now + ILLEGAL_ATTACK_MS

    def wear_struck(self, target: Player, damage: int,
                    attacker: "Actor | None" = None) -> None:
        """原版 ``TBaseObject.StruckDamage``（ObjBase.pas:22461-22560）：被击中
        且伤害 > 0 时装备磨损——先对衣服（U_DRESS）固定磨损 ``Random(10)+5``
        （:22478-22516），再对**全部**装备槽（含武器、含衣服）各 1/8 概率磨损
        同量（:22517-22560）；到 0 直接损毁清槽（衣服还要 FeatureChanged，
        本项目用 bagUpdate 刷外观）；节流 ``Round(持久/1000)``（显示 1 点）
        变化才发 SM_DURACHANGE。POISON_DAMAGEARMOR 的 ×1.2 本项目暂无该毒
        状态，略。
        """
        # 原版 AddPkHiter 在 Struck（挨打）里给攻击者置灰名，不在 _Attack 里。
        # 只在实际造成伤害（damage>0）时才算「动手」，与磨损口径一致；即使
        # 没穿装备（equipment 为空）也要打标记，故放在 equipment 检查之前。
        if damage > 0 and isinstance(attacker, Player) and attacker is not target:
            self.mark_illegal_attack(attacker, target, now_ms())
        if damage <= 0 or not target.equipment:
            return
        n_dam = random.randint(5, 14)
        self._wear_slot_struck(target, U_DRESS, n_dam)   # 22478 衣服固定磨损
        for slot in range(len(target.equipment)):        # 22517 全槽各 1/8
            if target.equipment[slot] is None:
                continue
            if random.randrange(8) != 0:
                continue
            self._wear_slot_struck(target, slot, n_dam)

    def _wear_slot_struck(self, target: Player, slot: int, amount: int) -> None:
        it = target.equipment[slot]
        if it is None:
            return
        old = int(it.get("durability", 0) or 0)
        old_disp = delphi_round(old / 1000)
        dura = old - amount
        if dura <= 0:
            it["durability"] = 0
            target.equipment[slot] = None
            if slot == U_DRESS:
                self.send_system(target, "衣服持久用完，已损毁。")
            self.send_bag_update(target)
            return
        it["durability"] = dura
        if delphi_round(dura / 1000) != old_disp:
            self.send_dura_change(target, slot, it)

    def nearby(self, game_map: GameMap, x: int, y: int,
               radius: int = SIGHT_RANGE) -> list[Actor]:
        return [a for a in game_map.actors.values() if a.alive
                and not getattr(a, "hidden", False)
                and max(abs(a.x - x), abs(a.y - y)) <= radius]

    @staticmethod
    def _corpse_visible(a: Actor) -> bool:
        """未到 makeGhost 时间的尸体：原版所有死亡对象都留 3 分钟（可挖肉的动物
        只是额外允许 Alt+左键挖）。

        玩家死亡不倒计时：原版 m_boDeath 的角色一直留在地图上（`m_boGhost`
        才是登出），只有重新登录才回到出生点，所以尸体要一直可见。
        """
        if isinstance(a, Player):
            return not a.alive and a.dead
        return (isinstance(a, Monster) and not a.alive
                and a.corpse_until > now_ms())

    def _sight_actors(self, game_map: GameMap, x: int, y: int,
                      radius: int = SIGHT_RANGE) -> list[Actor]:
        """视野内下发给客户端的实体 = 活体 + 未过期尸体。

        只用于 map_snapshot / update_sight；战斗目标筛选仍用 nearby()（仅活体），
        否则怪物会攻击自己同伴的尸体。
        """
        return [a for a in game_map.actors.values()
                if (a.alive or self._corpse_visible(a))
                and not getattr(a, "hidden", False)
                and max(abs(a.x - x), abs(a.y - y)) <= radius]

    def is_underground(self, map_name: str) -> bool:
        """地下/矿区地图恒暗（视野受限）。

        原版 ``ObjBase.pas:4283 DayBright`` 只认 ``boDARKness``；本项目的
        MapInfo.txt 里比奇矿区是 ``MINE``、天然洞穴没有任何标志，但客户端
        1.76/1.80 在这些地下地图都需要火把视野。这里把 ``DARK``/``MINE``
        标志以及标题里的「洞/矿/穴/墓」并入地下判定，作为配置层近似。
        """
        flags = self.map_flags.get(map_name, ())
        if any(f.startswith("MINE") or f.startswith("DARK") for f in flags):
            return True
        title = self.map_titles.get(map_name, "")
        return any(k in title for k in ("洞", "矿", "穴", "墓", "窟"))

    def map_mine_type(self, map_name: str) -> str | None:
        """地图矿区标志（原版 ``Envir.Flag.boMINE / boMINE2``）。

        返回 ``"mine"`` / ``"mine2"`` / ``None``。本项目 MapInfo.txt 只有 24 张
        ``MINE`` 地图（比奇矿区系），没有 ``MINE2``（宝石矿区）。
        原版 svMain.pas:867-883 MakeStoneMines：对 MINE/MINE2 地图每格都建
        TStoneMineEvent；这里改为惰性登记进 ``self.mine_state``。
        """
        for f in self.map_flags.get(map_name, ()):
            fu = f.strip().upper()
            if fu == "MINE2":
                return "mine2"
            if fu == "MINE":
                return "mine"
        return None

    def dark_level(self, map_name: str, underground: bool | None = None) -> int:
        """对齐 ``DayBright()``：0 全亮 / 1 深夜 / 2 中等暗 / 3 正午。

        ``SM_NEWMAP``、``SM_CHANGEMAP`` 的第 4 参数 cdir 就是 ``DayBright()``
        （ObjBase.pas:5618/5796），客户端 ``PlayScn.pas:2388`` 收下后写入
        ``DarkLevel`` 并据此开关 ``g_boViewFog``。
        """
        if underground is None:
            underground = self.is_underground(map_name)
        flags = self.map_flags.get(map_name, ())
        if any(f.startswith("DAY") for f in flags):       # boDAYLIGHT
            return 0
        if underground:                                    # boDARKness
            return 1
        if self.day_bright == 1:
            return 0
        if self.day_bright == 3:
            return 1
        return 2

    def map_snapshot(self, p: Player, msg_type: str) -> dict:
        game_map = p.map or self.default_map
        p.seen.clear()
        nearby = []
        for a in self._sight_actors(game_map, p.x, p.y):
            if a.id != p.id:
                nearby.append(a.brief_json())
                p.seen.add(a.id)
        return {
            "type": msg_type,
            "map": {"name": game_map.name,
                    "title": self.map_titles.get(game_map.name, game_map.name),
                    "width": game_map.width, "height": game_map.height,
                    # MapInfo.txt 的连接行（src x,y -> dst）：大地图只标
                    # 洞穴/野外大图入口（cave_maps），主城村庄进房间的
                    # 入口不显示（用户约定）。dst 标题取 [dst 名称] 定义。
                    "gates": _dedup_gate_marks(
                        [g for g in self.gates_by_src.get(game_map.name, ())
                         if g.dst_map in self.cave_maps], self.map_titles)},
            "dayBright": self.day_bright,
            "darkLevel": self.dark_level(game_map.name),
            "underground": self.is_underground(game_map.name),
            "miniMapIndex": self.map_mini_index.get(game_map.name, -1),
            "player": p.to_player_json(self),
            "nearby": nearby,
            "groundItems": self.ground_items_json(p),
        }

    def update_sight(self, p: Player) -> None:
        game_map = p.map
        if game_map is None or p.session is None:
            return
        now_seen: set[int] = set()
        for a in self._sight_actors(game_map, p.x, p.y):
            if a.id != p.id:
                now_seen.add(a.id)
                if a.id not in p.seen:
                    p.session.send_json({"type": "entityAdd", "entity": a.brief_json()})
        for actor_id in p.seen - now_seen:
            p.session.send_json({"type": "entityRemove", "id": actor_id})
        p.seen = now_seen

    # ---- tick ------------------------------------------------------------

    def tick(self, now: int) -> None:
        """直译 Java ``GameWorld.tick`` 的调用顺序。

        实体状态（回血 / buff / 怪物与宠物 AI / 毒） → 昼夜 → 门 → 火墙 →
        地面物品与攻城（``tick_extra``） → 延迟飞矢 → 死亡重生。

        空图休眠：怪物 AI / 毒只在「图上有在线玩家」时跑。无人看的图里
        怪物游走是纯浪费（广播也没有收件人）；重生与尸体过期仍按时间推进。
        图上的最后一名在线玩家离开超过 :data:`MON_DESPAWN_MS` 后整图清怪，
        玩家再进图时由 :meth:`player_entered` 重新满编刷出。
        """
        active_maps = set()
        for p in self.players():
            if p.session is not None and p.map is not None:
                active_maps.add(p.map)
                p.map.last_active_at = now
        for a in list(self.actors.values()):
            # 原版死亡角色停在原地等重登，不再回血/被毒跳/被 buff tick；
            # 否则 inc_health 池会把 HP 从 0 抬起来，黑白屏却还挂着。
            if getattr(a, "dead", False):
                continue
            if isinstance(a, Player):
                self._tick_time_recall(a, now)
                self._tick_pk(a, now)
                self._regen(a, now)
                self.check_home_pos(a, now)
                self._expire_buffs(a, now)
                self._apply_poison(a, now)
            elif isinstance(a, Monster):
                if a.map in active_maps:
                    self._monster_ai(a, now)
                    self._apply_poison(a, now)
        self._tick_day_bright(now)
        self._close_timed_out_doors(now)
        self._tick_fire_walls(now)
        self._tick_pending_ranged_hits(now)
        self._sync_player_hp(now)
        self.tick_extra(now)          # 地面物品过期 + 攻城计时
        self._tick_zilkin_revive(now)  # race 96 复活僵尸（TZilKinZombi.Run）
        self._expire_corpses(now)     # 尸体到点移除（原版 MakeGhost）
        self._respawn_dead(now)
        if now - self._last_despawn_check >= DESPAWN_CHECK_MS:
            self._last_despawn_check = now
            self._despawn_idle_maps(now)

    def _despawn_idle_maps(self, now: int) -> None:
        """玩家离开超过 ``MON_DESPAWN_MS`` 的图整图清怪（含尸体/待重生）。

        依据：实测 12 张大图满怪（1 万只）一次 tick 131ms、CPU ≈65%，而
        空图里的怪物游走没有任何观察者。``last_active_at == 0`` 的图（从未
        有人进过）不清，保持首访满编；清怪后 ``mon_spawned=False``，
        :meth:`player_entered` 会重新刷。地面物品只按自身过期时间走。
        """
        for gm in list(self.maps.values()):
            if not gm.mon_spawned or gm.last_active_at == 0:
                continue
            if now - gm.last_active_at <= MON_DESPAWN_MS:
                continue
            removed = 0
            for a in [a for a in gm.actors.values() if isinstance(a, Monster)]:
                gm.remove(a.id)
                self.actors.pop(a.id, None)
                removed += 1
            gm.mon_spawned = False
            print(f"[world] map={gm.name} idle >{MON_DESPAWN_MS // 1000}s, "
                  f"despawned monsters={removed}")

    def player_entered(self, p: Player) -> None:
        """玩家进图钩子：休眠过的图重新满编刷怪（等价 get_or_load_map 首刷）。"""
        gm = p.map
        if gm is not None and not gm.mon_spawned:
            self.spawn_monsters(gm)

    def _sync_player_hp(self, now: int) -> None:
        """把玩家血量变化同步给周围玩家，否则旁观者看到的血条永远不动。

        原版依据：``Actor.pas:1550`` 里 ``SM_STRUCK`` 的 HP 字段被注释掉了，
        自己的血量走 ``SM_HEALTHSPELLCHANGED``（只发给本人）。多人时别人的血条
        在原版也是靠 ``SM_STRUCK`` 的 hp 才更新的，这里补一条只带血量的轻量消息
        （``entityHp``），按 ``HP_SYNC_MS`` 合批，避免每次扣血都广播。
        """
        if now - self._hp_sync_at < HP_SYNC_MS:
            return
        self._hp_sync_at = now
        cache = self._hp_sync_cache
        alive: set[int] = set()
        for p in self.players():
            if p.session is None:
                continue
            aid = p.id
            alive.add(aid)
            key = (p.hp, p.max_hp)
            if cache.get(aid) == key:
                continue
            cache[aid] = key
            self.broadcast_around(p.map, {"type": "entityHp", "id": aid,
                                          "hp": p.hp, "maxHp": p.max_hp}, p.x, p.y)
        for aid in [k for k in cache if k not in alive]:
            cache.pop(aid, None)

    # -- 玩家状态：回血 / buff / 毒 ---------------------------------------

    def check_home_pos(self, p: Player, now: int) -> None:
        """原版 TUserHuman.CheckHomePos（ObjBase.pas:13103-13121，Run 循环
        每 3s 调一次）：玩家站在 StartPoint.txt 登记的出生点地图、且坐标在
        登记点 ±50 格内时，把「家」搬到那里——回城卷（StdMode 3 Shape 3
        → UserSpaceMove(HomeMap, HomeX, HomeY)，ObjBase.pas:3174）回的就
        是这个动态家点。无登记匹配则保持原值（建号出生点）。"""
        last = self._home_check.get(p.id, 0)
        if now - last < 3000:
            return
        self._home_check[p.id] = now
        map_name = p.map.name if p.map else ""
        for sp in self.start_points:
            if sp.map_id != map_name:
                continue
            if abs(p.x - sp.x) <= 50 and abs(p.y - sp.y) <= 50:
                # 原版逐个比对不 break（同图多点时后匹配的覆盖），照搬
                p.home_map, p.home_x, p.home_y = sp.map_id, sp.x, sp.y

    def _regen(self, p: Player, now: int) -> None:
        """HP/MP 自然回复 + 药水/治愈术回血池（``ObjBase.pas:3782-3850``）。

        原版把「药水回血」「回蓝」「治愈术」三个池子合在**同一个** tick：
        间隔 dwInChsTime = 600 - min(400, Level*10) ms，每 tick 每池消耗
        m_nPerHealth / m_nPerSpell = Level div 10 + 5、m_nPerHealing = 5；
        HP 满时清空 inc_health/inc_healing，MP 满时清空 inc_spell。
        """
        changed = False
        heal_tick = 600 - min(400, p.level * 10)
        if (p.inc_health > 0 or p.inc_spell > 0 or p.inc_healing > 0) \
                and now - p.last_healing_at >= heal_tick:
            per = max(1, p.level // 10 + 5)      # m_nPerHealth / m_nPerSpell
            hp_add = min(p.inc_health, per)
            mp_add = min(p.inc_spell, per)
            heal_add = min(p.inc_healing, 5)     # m_nPerHealing 恒 5
            p.inc_health -= hp_add
            p.inc_spell -= mp_add
            p.inc_healing -= heal_add
            if hp_add or heal_add:
                p.hp = min(p.max_hp, p.hp + hp_add + heal_add)
            if mp_add:
                p.mp = min(p.max_mp, p.mp + mp_add)
            if p.hp >= p.max_hp:
                p.inc_health = 0
                p.inc_healing = 0
            if p.mp >= p.max_mp:
                p.inc_spell = 0
            p.last_healing_at = now
            changed = True
        if now - self.last_regen_at < REGEN_INTERVAL_MS:
            if changed:
                self.send_stats(p)
            return
        if p.hp < p.max_hp:
            p.hp += 1
            changed = True
        if p.mp < p.max_mp:
            p.mp += 1
            changed = True
        if changed:
            self.last_regen_at = now
            self.send_stats(p)

    def _expire_buffs(self, p: Player, now: int) -> None:
        if 0 < p.ac_buff_until <= now:
            p.ac_buff_until = 0
            p.ac_buff_bonus = 0
            p.mac_buff_bonus = 0
            p.recompute(self)
            self.send_stats(p)
        if 0 < p.shield_until <= now:
            p.shield_until = 0
            self.send_stats(p)
        # 原版 Run（ObjBase.pas:4194-4220）：m_wStatusArrValue 到点清零并
        # RecalcAbilitys——状态药水（Shape=12）的 DC/MC/SC/攻速/HP/MP 加成
        # 到期要在这里主动重算，否则 MaxHP 加成一值永远挂着。
        if any(v > 0 and su <= now for v, su in zip(p.status_arr, p.status_until)):
            expired = [i for i, (v, su) in enumerate(zip(p.status_arr, p.status_until))
                       if v > 0 and su <= now]
            for i in expired:
                p.status_arr[i] = 0
            p.recompute(self)
            self.send_stats(p)

    def _apply_poison(self, a: Actor, now: int) -> None:
        """红毒每 2500ms 一跳；绿毒到期清除；毒死走正常死亡流程。"""
        if not a.alive:
            return
        ticks = getattr(a, "poison_ticks", 0)
        if ticks > 0 and now - getattr(a, "last_poison_at", 0) >= POISON_INTERVAL_MS:
            a.last_poison_at = now
            a.hp = max(0, a.hp - getattr(a, "poison_damage", 0))
            a.poison_ticks = ticks - 1
            if a.poison_ticks <= 0:
                a.last_poison_at = 0
            # 原版 ObjBase.pas:4255-4260：绿毒每跳对 m_boAnimal 还扣
            # m_nMeatQuality 1000——中毒打死的动物肉品质很低。
            if isinstance(a, Monster) and a.meat_quality > 0:
                a.meat_quality = max(0, a.meat_quality - 1000)
            self.send_stats(a)
            if a.hp <= 0:
                if isinstance(a, Monster):
                    self.kill_monster(a, now, None)
                elif isinstance(a, Player):
                    self.kill_player(a)
                return
        until = getattr(a, "poison_armor_until", 0)
        if 0 < until <= now:
            a.poison_armor_until = 0
            self.send_stats(a)

    # -- 场景计时：昼夜 / 门 / 火墙 / 飞矢 / 重生 --------------------------

    def _tick_day_bright(self, now: int) -> None:
        if now - self._last_day_check < 60_000:
            return
        self._last_day_check = now
        bright = day_bright_for_hour(datetime.now().hour)
        if bright == self.day_bright:
            return
        self.day_bright = bright
        # darkLevel 用「非地下地图」口径下发，地下地图由客户端按 underground 覆盖成恒暗。
        dl = 0 if bright == 1 else (1 if bright == 3 else 2)
        self.broadcast_all({"type": "dayChanging", "dayBright": bright, "darkLevel": dl})

    def _close_timed_out_doors(self, now: int) -> None:
        for game_map in self.maps.values():
            for (x, y), opened_at in list(game_map.open_doors.items()):
                if now - opened_at > DOOR_OPEN_MS:
                    del game_map.open_doors[(x, y)]
                    self.broadcast_around(game_map, {"type": "doorState", "x": x,
                                                     "y": y, "open": False}, x, y)

    def _tick_fire_walls(self, now: int) -> None:
        walls = self.combat_state.setdefault("fire_walls", [])
        if not walls:
            return
        keep = []
        for f in walls:
            if now >= f["expire"]:
                continue
            keep.append(f)
            # 原版 TFireBurnEvent.Run（Event.pas:239-245）：每 **3 秒** 结算一次，
            # 且伤害走 RM_MAGSTRUCK_MINE → GetMagStruckDamage（要扣 MAC）。
            if now - f.get("last_tick", 0) < 3000:
                continue
            f["last_tick"] = now
            gm = f["map"]
            for a in list(gm.actors.values()):
                if not isinstance(a, Monster) or not a.alive or a.id == f["owner"]:
                    continue
                if a.x != f["x"] or a.y != f["y"]:
                    continue
                dmg = mag_struck_damage(a, f["damage"], now)
                if dmg <= 0:
                    continue
                a.hp = max(0, a.hp - dmg)
                self.broadcast_around(gm, {"type": "attack", "attackerId": f["owner"],
                                           "targetId": a.id, "damage": dmg,
                                           "magicId": 22, "x": a.x, "y": a.y},
                                      a.x, a.y)
                self.send_stats(a)
                if a.hp <= 0:
                    owner = self.actor(f["owner"])
                    self.kill_monster(a, now, owner)
                    if isinstance(owner, Player):
                        t = a.template
                        # 原版火墙/宝宝击杀同样走主人的 CalcGetExp（ObjBase.pas:20848）；
                        # 经验取怪物实例值（复活僵尸会 div 2）
                        exp = calc_get_exp(owner.level, t.level if t else 1, a.exp)
                        self.add_exp(owner, self.kill_exp(
                            t.name if t else a.name, exp))
        self.combat_state["fire_walls"] = keep

    def _tick_pending_ranged_hits(self, now: int) -> None:
        queue = self.combat_state.get("pending_ranged") or []
        if not queue:
            return
        keep = []
        for h in queue:
            if now < h["at"]:
                keep.append(h)
                continue
            m = self.actor(h["monster"])
            t = self.actor(h["target"])
            if (isinstance(m, Monster) and m.alive and m.map is not None
                    and t is not None and t.alive and m.map is t.map):
                if isinstance(t, Player):
                    self._apply_hit_to_player(m, t, h["damage"])
                elif isinstance(t, Monster) and t.master_id >= 0:
                    # 召唤物吃远程伤害：通用怪打怪结算
                    t.hp = max(0, t.hp - h["damage"])
                    self._send_hits(m, t, h["damage"])
                    if t.hp <= 0:
                        self.kill_monster(t, now, None)
        self.combat_state["pending_ranged"] = keep

    def _expire_corpses(self, now: int) -> None:
        """尸体到 ``corpse_until`` 后移除并广播（原版 ``MakeGhost`` → RM_DISAPPEAR）。

        以前非动物怪出生即从视野剔除却**不发移除消息**，客户端会一直挂着那具
        「不动、血条为空」的尸体，直到玩家走动重新拉视野才消失。
        """
        for a in list(self.actors.values()):
            if not isinstance(a, Monster) or a.alive:
                continue
            if a.corpse_until <= 0 or now < a.corpse_until:
                continue
            a.corpse_until = 0
            a.pending_respawn = False
            a.respawn_at = 0
            if a.map is not None:
                a.map.remove(a.id)
            self.actors.pop(a.id, None)
            if a.map is not None:
                self.broadcast_around(a.map, {"type": "entityRemove", "id": a.id},
                                      a.x, a.y, SIGHT_RANGE)

    def _respawn_dead(self, now: int) -> None:
        """重生 = 新建一个 actor（原版每次重生都是新对象、新 id）。

        旧尸体保持在 ``actors`` 里直到 ``_expire_corpses`` 收走，这样「尸体留
        3 分钟」和「15 秒重生」可以同时成立，不会互相顶掉。
        """
        for a in list(self.actors.values()):
            if not isinstance(a, Monster) or not a.pending_respawn \
                    or now < a.respawn_at:
                continue
            a.pending_respawn = False
            a.respawn_at = 0
            t = a.template
            if t is None:
                continue
            home = a.spawn_map or self.default_map
            m = Monster(id=self.new_id(), name=t.name,
                        x=a.spawn_x, y=a.spawn_y, dir=4,
                        hp=t.hp, max_hp=t.hp, mp=t.mp, max_mp=t.mp,
                        level=t.level, race=t.race, race_img=t.raceImg, appr=t.appr,
                        template=t, spawn_x=a.spawn_x, spawn_y=a.spawn_y,
                        spawn_map=home,
                        respawn_interval_ms=a.respawn_interval_ms)
            m.exp = t.exp
            init_meat_quality(m)
            self._init_race_traits(m, t)
            self.add_actor(m, home)
            self.broadcast_entity_add(m)

    # -- 怪物 / 宠物 AI ----------------------------------------------------

    @staticmethod
    def _init_race_traits(m: Monster, t) -> None:
        """按 race 初始化出生特例（spawn / respawn 两处共用）。

        - 85 食人花 / 95 TDigOutZombi：``m_boFixedHideMode := True`` 潜地；
        - 96 TZilKinZombi（ObjMon.pas:1265-1276）：``nZilKillCount := 0``，
          ``if Random(3) = 0 then nZilKillCount := Random(3) + 1``（1/3 概率
          可复活 1~3 次）。
        """
        if t.race in (85, 95):
            m.hidden = True
        if t.race == 96:
            m.zil_revives = random.randint(1, 3) if random.random() < 1 / 3 else 0

    def _tick_zilkin_revive(self, now: int) -> None:
        """race 96 复活僵尸（TZilKinZombi.Run，ObjMon.pas:1296-1313）。

        原版复活条件（逐条对齐）：
        ``m_boDeath and (not m_boGhost) and (nZilKillCount >= 0)
        and (m_wStatusTimeArr[POISON_STONE] = 0)
        and (m_VisibleActors.Count > 0) and (到时 dw560)``；
        满足后 ``MaxHP := MaxHP shr 1``、``m_dwFightExp := m_dwFightExp div 2``、
        HP 拉满、``ReAlive``（广播 RM_ALIVE）、``m_dwWalkTick := +1000``。

        本项目对应：pending_respawn=True 表示尸体还没被重生补刷（原版
        not m_boGhost + 未走 MonGen 重刷）；石化状态未实作，恒通过；
        m_VisibleActors 近似为 SIGHT_RANGE 内有活玩家。
        """
        for m in list(self.actors.values()):
            if not isinstance(m, Monster) or m.alive:
                continue
            if not m.zil_revive_at or now < m.zil_revive_at:
                continue
            if not m.pending_respawn:
                continue    # 已被 _respawn_dead 补了新怪（原版 m_boGhost / 已重刷）
            t = m.template
            if t is None or int(t.race) != 96:
                m.zil_revive_at = 0
                continue
            if m.corpse_until <= 0 or now >= m.corpse_until:
                continue    # 尸体已过期（m_boGhost），不再复活
            if not any(p.map is m.map and p.alive and p.hide_until <= now
                       and abs(p.x - m.x) <= SIGHT_RANGE
                       and abs(p.y - m.y) <= SIGHT_RANGE
                       for p in self.players()):
                continue    # m_VisibleActors.Count = 0：附近没有观察者，下轮再试
            m.zil_revive_at = 0
            m.max_hp = max(1, m.max_hp // 2)      # MaxHP shr 1
            m.exp = m.exp // 2                    # m_dwFightExp div 2（实例值，见 __post_init__）
            m.hp = m.max_hp
            m.alive = True
            m.pending_respawn = False             # 原地复活，不再补新怪
            m.respawn_at = 0
            m.next_move_at = now + 1000           # m_dwWalkTick := GetTickCount + 1000
            self.broadcast_around(
                m.map,
                {"type": "alive", "entity": m.brief_json(),
                 "x": m.x, "y": m.y, "dir": m.dir},
                m.x, m.y, SIGHT_RANGE)

    def _walk_toward(self, m: Monster, tx: int, ty: int) -> bool:
        """原版 ``TAnimalObject.GotoTargetXY``（ObjBase.pas:2709-2759）：
        先朝目标方向走一格；被挡则按 ``n20 = Random(3)`` 决定的旋转方向
        （2/3 顺时针 / 1/3 逆时针）依次重试相邻方向，最多 7 次——**每 tick
        最多走一格**（循环只在还没挪动时继续，一旦走成功立即结束）。
        之前只试一个方向，被树/墙/其他怪挡住就原地站桩，这是「怪物容易
        卡住」的直接原因。"""
        base = direction_toward(m.x, m.y, tx, ty, m.dir)
        m.dir = base
        if self.try_move(m, base):
            return True
        cw = random.random() < 2 / 3
        for k in range(1, 8):
            d = (base + (k if cw else -k)) & 7
            if self.try_move(m, d):
                return True
        return False

    def _monster_ai(self, m: Monster, now: int) -> None:
        if m.pending_respawn or not m.alive or m.template is None:
            return
        if 0 < m.paralyzed_until <= now:
            m.paralyzed_until = 0
            self.send_stats(m)
        if now < m.paralyzed_until:
            return
        if m.master_id >= 0:
            self._summon_ai(m, now)
            return
        t = m.template
        is_guard = t.race in GUARD_RACES
        walk_ms = max(200, t.walkSpeed or 800)
        attack_ms = max(300, t.attackSpeed or 1000)
        if t.race == 85:
            self._stick_ai(m, now, attack_ms)
            return
        if t.race == 95 and m.hidden:
            # TDigOutZombi（ObjMon.pas:1193-1260，race 95 僵尸2/僵尸20/石墓尸王）：
            # 刷出即 ``m_boFixedHideMode := True`` 埋在地下；视野内有活目标
            # ``abs<=3`` 才 ComeOut（RM_DIGUP + 脚下尘土事件），出土后走
            # TMonster 基础 AI，**不会**再钻回去（与食人花 ComeDown 不同）。
            for p in self.players():
                if p.map is not m.map or not p.alive or p.hide_until > now:
                    continue
                if abs(m.x - p.x) <= 3 and abs(m.y - p.y) <= 3:
                    m.hidden = False
                    m.dir = direction_toward(m.x, m.y, p.x, p.y, m.dir)
                    self.broadcast_around(
                        m.map,
                        {"type": "digUp", "entity": m.brief_json(),
                         "x": m.x, "y": m.y, "dir": m.dir},
                        m.x, m.y, SIGHT_RANGE)
                    m.next_move_at = now + 1000   # 原版 m_dwWalkTick := +1000
                    break
            return
        if m.chicken_deer:
            self._chicken_deer_ai(m, now, walk_ms)
            return
        target = self._acquire_target(m, now)

        if target is not None:
            d = m.distance_to(target)
            if isinstance(target, Monster):
                # 大刀守卫打怪（TSuperGuard.AttackTarget，ObjGuard.pas:31-66）：
                # RM_HIT 挥刀 + _Attack 结算；守卫**不移动**（Run 无 Walk 分支），
                # 目标跑出视野后下一轮扫描不再锁定（_resolve_target 返回 None）。
                # 守卫杀怪：m_ExpHitter 是守卫（非玩家无主人）→ 无经验
                # （ObjBase.pas:20804-20854 分支），掉落照常。
                if (t.race in SUPER_GUARD_RACES and d <= 2
                        and now >= m.next_attack_at):
                    m.next_attack_at = now + attack_ms
                    m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                    if not melee_hit(m, target):
                        # 原版守卫也走 _Attack：未命中只有 RM_HIT（挥刀），无伤害
                        self.broadcast_around(
                            m.map, {"type": "attack", "attackerId": m.id,
                                    "targetId": target.id, "damage": 0,
                                    "noDamage": True, "dir": m.dir,
                                    "x": target.x, "y": target.y},
                            m.x, m.y, SIGHT_RANGE)
                        return
                    dmg = max(1, m.roll_attack()
                              - (target.template.ac if target.template else 0))
                    target.hp = max(0, target.hp - dmg)
                    self.broadcast_around(
                        m.map, {"type": "attack", "attackerId": m.id,
                                "targetId": target.id, "damage": dmg,
                                "dir": m.dir,
                                "x": target.x, "y": target.y}, m.x, m.y, SIGHT_RANGE)
                    self.send_stats(target)
                    if target.hp <= 0:
                        self.kill_monster(target, now, None)
                if t.race in SUPER_GUARD_RACES:
                    return      # 守卫站桩：走完上面分支即止
                # 普通怪打召唤物继续走下方通用近战/远程分支（原版 TMonster 的
                # AttackTarget 对宠物/骷髅同样 _Attack 结算，ObjBase.pas:22240）
            if t.race in RANGED_RACES:
                if d <= RANGED_ATTACK_RANGE:
                    if now >= m.next_attack_at:
                        m.next_attack_at = now + attack_ms
                        m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                        self._ranged_attack(m, target, now)
                    if (not is_guard and d <= RANGED_KEEP_AWAY
                            and now >= m.next_move_at and random.random() < 0.33):
                        m.next_move_at = now + walk_ms
                        if self.try_move(m, (m.dir + 4) & 7):
                            self.broadcast_move(m)
                elif (not is_guard and d <= RANGED_CHASE_RANGE
                      and now >= m.next_move_at):
                    m.next_move_at = now + walk_ms
                    if self._walk_toward(m, target.x, target.y):
                        self.broadcast_move(m)
                return
            if d == 1:
                if now >= m.next_attack_at:
                    m.next_attack_at = now + attack_ms
                    m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                    dmg = self._ranged_damage(m, target)
                    if not melee_hit(m, target):
                        # 原版 TBaseObject.Attack → _Attack（ObjBase.pas:22240）：
                        # 命中点 < Random(目标敏捷) 时 nPower := 0，SM_HIT 照发
                        # 但不出伤害消息——客户端只看到挥击动作，不播受击。
                        self.broadcast_around(m.map, {"type": "attack", "attackerId": m.id,
                                                      "targetId": target.id, "damage": 0,
                                                      "noDamage": True,
                                                      "dir": m.dir,
                                                      "x": target.x, "y": target.y},
                                              m.x, m.y)
                    elif dmg <= 0:
                        # 原版：RM_HIT 照发（有挥击动作），但 StruckDamage 因
                        # nDamage<=0 直接返回 —— 目标不播受击动作、不掉血、不飘字。
                        self.broadcast_around(m.map, {"type": "attack", "attackerId": m.id,
                                                      "targetId": target.id, "damage": 0,
                                                      "noDamage": True,
                                                      "dir": m.dir,
                                                      "x": target.x, "y": target.y},
                                              m.x, m.y)
                    else:
                        if isinstance(target, Player):
                            self._apply_hit_to_player(m, target, dmg)
                        else:
                            # 召唤物挨打：原版通用怪打怪结算（守卫分支同款）
                            target.hp = max(0, target.hp - dmg)
                            self._send_hits(m, target, dmg)
                            if target.hp <= 0:
                                self.kill_monster(target, now, None)
            elif (now >= m.next_move_at and not is_guard):
                # 原版守卫不追击：TGuardUnit.Run / TSuperGuard.Run 都没有 Walk
                # 分支，目标跑出视野即放弃（ObjMon2.pas:924-980 / ObjGuard.pas:87）。
                # 追击走 GotoTargetXY（被挡重试相邻方向）。
                m.next_move_at = now + walk_ms
                if self._walk_toward(m, target.x, target.y):
                    self.broadcast_move(m)
        elif not is_guard and now >= m.next_move_at:
            # 原版 TAnimalObject.Wondering（ObjBase.pas:22723-22728）：
            # 每 walk tick Random(20)=0（5%）才动，其中 Random(4)=1（1/4）
            # 只转向（TurnTo），其余沿当前方向继续走一格——不是每次随机换向。
            m.next_move_at = now + walk_ms
            if random.random() < 0.05:
                if random.random() < 0.25:
                    m.dir = int(random.random() * 8)
                elif self.try_move(m, m.dir):
                    self.broadcast_move(m)

    def _chicken_deer_ai(self, m: Monster, now: int, walk_ms: int) -> None:
        """原版 ``TChickenDeer``（ObjMon.pas:531-598，鹿/羊 1/30 变种）：

        - ``Create`` 设 ``m_nViewRange := 5``（比普通鹿的默认视野更近）；
        - ``Run`` 遍历 ``m_VisibleActors`` 找**最近**的合法目标，找到就
          ``m_boRunAwayMode := True`` 并记为目标；``TMonster.Run`` 的逃跑分支
          （ObjMon.pas:447）**跳过 AttackTarget**，所以这种鹿/羊永不攻击；
        - 目标 ``abs <= 6`` 才起步逃跑，走位点由 ``GetNextPosition`` 取背离方向。
        这里按「朝背离目标的方向走一格」实现，走不通就试相邻两个方向；目标
        离开视野后恢复 ``Wondering``（原版同款随机乱走）。
        """
        nearest = None
        nd = CHICKEN_DEER_VIEW + 1
        for p in self.players():
            if p.map is not m.map or not p.alive or p.hide_until > now:
                continue
            d = p.distance_to(m)
            if d <= CHICKEN_DEER_VIEW and d < nd:
                nearest, nd = p, d
        if nearest is None:
            m.target_id = -1
            if now >= m.next_move_at:
                m.next_move_at = now + walk_ms
                if random.random() < 0.35:
                    m.dir = int(random.random() * 8)
                    if self.try_move(m, m.dir):
                        self.broadcast_move(m)
            return
        m.target_id = nearest.id
        if nd > CHICKEN_DEER_RUN or now < m.next_move_at:
            return
        m.next_move_at = now + walk_ms
        away = direction_toward(nearest.x, nearest.y, m.x, m.y, m.dir)
        for delta in (0, 1, -1, 2, -2):
            d = (away + delta) & 7
            if self.try_move(m, d):
                self.broadcast_move(m)
                return

    def _stick_ai(self, m: Monster, now: int, attack_ms: int) -> None:
        """原版 TStickMonster（ObjMon2.pas:154-297，race 85 食人花）：

        - ``m_boStickMode := True``：**永不移动**（Run 里没有任何 WalkTo）；
        - ``m_boFixedHideMode := True``：默认潜地隐藏，``CheckComeOut`` 有
          活目标 ``abs<4``（nComeOutValue）才 ComeOut（RM_DIGUP），
          目标死亡/无/``abs>4``（nAttackRange）就 ComeDown（RM_DIGDOWN）；
        - 出土后只 ``GetAttackDir`` 打相邻格，够不着也**不会**走过去。

        隐藏态不进视野快照（_sight_actors/nearby 过滤 hidden），出土广播
        entityAdd、潜回广播 entityRemove，客户端即出现/消失。
        """
        target = self._acquire_target(m, now)
        if m.hidden:
            if target is not None and m.distance_to(target) < 4:
                m.hidden = False
                # 原版 TStickMonster.ComeOut（ObjMon2.pas:203）也是 RM_DIGUP，
                # 不是单纯的实体出现 —— 客户端要播 ActDeath 出土动画 + 尘土。
                self.broadcast_around(
                    m.map,
                    {"type": "digUp", "entity": m.brief_json(),
                     "x": m.x, "y": m.y, "dir": m.dir},
                    m.x, m.y, SIGHT_RANGE)
            return
        if target is None or m.distance_to(target) > 4:
            m.hidden = True
            self.broadcast_around(m.map, {"type": "entityRemove", "id": m.id},
                                  m.x, m.y)
            return
        if m.distance_to(target) == 1 and now >= m.next_attack_at:
            m.next_attack_at = now + attack_ms
            m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
            dmg = self._ranged_damage(m, target)
            if not melee_hit(m, target):
                # 原版 TStickMonster 攻击同样走 TBaseObject._Attack：未命中只发
                # RM_HIT（张嘴动作），不结算伤害。
                self.broadcast_around(m.map, {"type": "attack", "attackerId": m.id,
                                              "targetId": target.id, "damage": 0,
                                              "noDamage": True,
                                              "dir": m.dir,
                                              "x": target.x, "y": target.y},
                                      m.x, m.y)
            elif dmg <= 0:
                self.broadcast_around(m.map, {"type": "attack", "attackerId": m.id,
                                              "targetId": target.id, "damage": 0,
                                              "noDamage": True,
                                              "dir": m.dir,
                                              "x": target.x, "y": target.y},
                                      m.x, m.y)
            else:
                if isinstance(target, Player):
                    self._apply_hit_to_player(m, target, dmg)
                else:
                    # 食人花够得着的也可能是道士宝宝（Monster，master_id>=0）；
                    # 和 _monster_ai 近战 / _tick_pending_ranged_hits 同款走通用
                    # 怪打怪结算。此前直接调 _apply_hit_to_player 会读
                    # target.mana_to_health（仅 Player 有）抛 AttributeError，
                    # 让这一轮 tick 后面的逻辑全被跳过（GM-ANALYSIS §0.1）。
                    target.hp = max(0, target.hp - dmg)
                    self._send_hits(m, target, dmg)
                    if target.hp <= 0:
                        self.kill_monster(target, now, None)

    def _summon_ai(self, m: Monster, now: int) -> None:
        """宠物跟随 / 协助攻击 / 神兽变身 / 升级（``GameWorld.summonAi``）。"""
        master = self.actor(m.master_id)
        if not isinstance(master, Player) or not master.alive:
            return
        # 原版 TSlave.Run（ObjMon.pas:489-495）：主人换图、或拉开超过 20 格时
        # ``SpaceMove(master.map, backX, backY, 1)`` 直接瞬移跟过去（不是留在原地）。
        if (master.map is not m.map
                or abs(master.x - m.x) > 20 or abs(master.y - m.y) > 20):
            self._slave_space_move(m, master)
            return
        hit_interval = max(500, 3000 - m.slave_make_level * 600)
        walk_interval = max(200, 1200 - m.slave_make_level * 250)
        enemy = None
        mt = self.actor(master.target_id) if master.target_id >= 0 else None
        if (isinstance(mt, Monster) and mt.alive and mt.map is m.map
                and mt.master_id < 0 and m.distance_to(mt) <= 11):
            enemy = mt
        if enemy is None:
            enemy = self._nearest_enemy(master, m)

        if m.race == 113:
            if enemy is not None:
                if not m.transformed:
                    m.transformed = True
                    m.race_img = 55
                    m.appr = 171
                    self.send_stats(m)
                m.change_face_at = now
            elif m.transformed and now - m.change_face_at > 60_000:
                m.transformed = False
                m.race_img = 54
                m.appr = 170
                self.send_stats(m)

        if enemy is not None:
            d = m.distance_to(enemy)
            ranged = m.race == 113 and m.transformed
            if d <= (2 if ranged else 1):
                if now >= m.next_attack_at:
                    m.next_attack_at = now + hit_interval
                    m.dir = direction_toward(m.x, m.y, enemy.x, enemy.y, m.dir)
                    if not melee_hit(m, enemy):
                        # 宝宝攻击也走 _Attack 的命中判定
                        self.broadcast_around(enemy.map,
                                              {"type": "attack", "attackerId": m.id,
                                               "targetId": enemy.id, "damage": 0,
                                               "noDamage": True, "dir": m.dir,
                                               "x": enemy.x, "y": enemy.y},
                                              enemy.x, enemy.y)
                        return
                    dmg = max(1, m.roll_attack()
                              - (enemy.template.ac if enemy.template else 0))
                    enemy.hp = max(0, enemy.hp - dmg)
                    self._send_hits(m, enemy, dmg)
                    # 原版 TAnimalObject.Struck(hiter) → SetTargetCreat(hiter)：
                    # 被打的怪记仇，怪转头打宝宝（这就是道士宝宝顶怪的仇恨来源）
                    enemy.last_hiter_id = m.id
                    enemy.target_id = m.id
                    if enemy.hp <= 0:
                        self.kill_monster(enemy, now, master)
                        t = enemy.template
                        # 宝宝击杀：主人按 CalcGetExp（ObjBase.pas:20845-20852），
                        # 经验取怪物实例值（复活僵尸会 div 2）
                        exp = calc_get_exp(master.level, t.level if t else 1,
                                           enemy.exp)
                        self.add_exp(master, self.kill_exp(
                            t.name if t else enemy.name, exp))
                        self._gain_slave_exp(m, enemy)
            elif now >= m.next_move_at:
                m.next_move_at = now + walk_interval
                if self._walk_toward(m, enemy.x, enemy.y):
                    self.broadcast_move(m)
        elif m.distance_to(master) > 2 and now >= m.next_move_at:
            m.next_move_at = now + walk_interval
            if self._walk_toward(m, master.x, master.y):
                self.broadcast_move(m)

    def _slave_space_move(self, m: Monster, master: Player, front: bool = False) -> None:
        """宝宝瞬移（原版 ``TBaseObject.SpaceMove(..., 1)``，ObjMon.pas:494）。

        原版 ``nInt=1`` → ``RM_SPACEMOVE_SHOW2``（magic 1600，10 帧×30ms），
        离开时还有 ``RM_SPACEMOVE_FIRE`` → ``SM_SPACEMOVE_HIDE2``（magic 1590）。
        本项目沿用玩家传送的两段消息（``spaceMoveHide``/``spaceMoveShow`` +
        ``variant=1``），旧地图先移除实体、新地图重新下发快照。

        front=False：宝宝跟随跟随落主人**背后**一格（``GetBackPosition``）；
        front=True：召唤召回（原版 ``RecallSlave``，ObjBase.pas:21940-21958）
        落主人**面前**一格（``GetFrontPosition``），召唤神兽宝宝存活期间
        再施放走这条路（Magic.pas:1382-1384）。
        """
        if master.map is None:
            return
        if front:
            pos = self._front_near(master.map, master.x, master.y, master.dir)
        else:
            pos = self._back_near(master.map, master.x, master.y, master.dir)
        if pos is None:
            return
        old_map, ox, oy = m.map, m.x, m.y
        self.broadcast_around(old_map, {"type": "spaceMoveHide", "x": ox, "y": oy,
                                        "variant": 1}, ox, oy, SIGHT_RANGE)
        moved_map = old_map is not master.map
        if moved_map:
            if old_map is not None:
                old_map.remove(m.id)
            self.broadcast_around(old_map, {"type": "entityRemove", "id": m.id},
                                  ox, oy, SIGHT_RANGE)
        m.x, m.y = pos
        m.dir = master.dir
        if moved_map:
            master.map.add(m)
            self.broadcast_entity_add(m)
        else:
            master.map.reposition(m, pos[0], pos[1])
        self.broadcast_around(m.map, {"type": "spaceMoveShow", "x": m.x, "y": m.y,
                                      "variant": 1}, m.x, m.y, SIGHT_RANGE)

    def _follow_summons(self, p: Player) -> None:
        """主人过门/传送后，宝宝立刻跟过去（原版下一次 TSlave.Run 才动，
        但本项目空图不跑 AI，必须在这里主动搬）。"""
        for a in list(self.actors.values()):
            if (isinstance(a, Monster) and a.alive and a.master_id == p.id
                    and a.map is not p.map):
                self._slave_space_move(a, p)

    def _nearest_enemy(self, master: Player, self_m: Monster) -> Monster | None:
        if master.map is None:
            return None
        best, best_d = None, 6
        for a in self.nearby(master.map, master.x, master.y, 6):
            if (isinstance(a, Monster) and a.alive and a.id != self_m.id
                    and a.master_id < 0):
                d = self_m.distance_to(a)
                if d < best_d:
                    best, best_d = a, d
        return best

    def _gain_slave_exp(self, slave: Monster, killed: Monster) -> None:
        lv = slave.slave_exp_level
        more = SLAVE_UP_EXP[lv] if 0 <= lv < len(SLAVE_UP_EXP) else 0
        next_exp = 100 + killed.level * 15 + more
        slave.slave_exp += killed.level
        if slave.slave_exp > next_exp:
            slave.slave_exp -= next_exp
            if slave.slave_exp_level < slave.slave_make_level * 2 + 1:
                slave.slave_exp_level += 1
                self._apply_slave_level(slave)

    def _apply_slave_level(self, slave: Monster) -> None:
        lv = slave.slave_exp_level
        base_hp = slave.template.hp if slave.template else slave.max_hp
        if slave.race in (86, 113):       # 骷髅 / 神兽
            slave.slave_dc_bonus = java_round(3 * (0.3 + lv * 0.1) * lv)
            slave.max_hp = base_hp + java_round(base_hp * (0.3 + lv * 0.1) * lv)
        else:
            slave.slave_dc_bonus = 2 * lv
            slave.max_hp = min(base_hp + 60 * lv,
                               base_hp + java_round(base_hp * 0.15) * lv)
        slave.hp = min(slave.hp, slave.max_hp)
        self.send_stats(slave)

    def _acquire_target(self, m: Monster, now: int) -> Actor | None:
        """目标获取（带全图扫描节流），管理 ``m.target_id`` 并返回当前目标。

        - 缓存目标有效（存活/同图/未隐身/未隐遁/仇恨半径内）→ O(1) 直接沿用，
          攻击与追击行为与逐 tick 重扫完全一致；
        - 目标失效 → 到 ``next_scan_at`` 才做一次全图重扫（``_resolve_target``），
          其余 tick 返回 None（怪原地游走——原版扫不到目标也是同样表现），
          扫描时刻按 ``m.id % 5`` 错峰，避免全图怪同 tick 集体重扫；
        - 被打时 Struck 逻辑直接写 ``m.target_id``，下一 tick 即被当作缓存
          目标沿用，仇恨响应不受节流影响（原版 SetTargetCreat 语义）。
        """
        cur = self.actor(m.target_id) if m.target_id >= 0 else None
        if (cur is not None and cur.alive and cur.map is m.map
                and m.distance_to(cur) <= AGGRO_RANGE
                and not getattr(cur, "hidden", False)
                and (not isinstance(cur, Player) or cur.hide_until <= now)):
            return cur
        if now < m.next_scan_at:
            if m.target_id >= 0 and self.actor(m.target_id) is None:
                m.target_id = -1    # 目标实体已被移除（尸体清掉），别悬挂
            return None
        m.next_scan_at = now + MON_SCAN_MS + (m.id % 5) * 100
        t = self._resolve_target(m, now)
        m.target_id = t.id if t is not None else -1
        return t

    def _resolve_target(self, m: Monster, now: int) -> Actor | None:
        t = m.template
        is_guard = t is not None and t.race in GUARD_RACES
        if t is not None and t.race in SUPER_GUARD_RACES:
            # 大刀守卫 TSuperGuard.Run（ObjGuard.pas:87-126）：每攻击冷却扫视野
            # (m_nViewRange=7)，目标条件 = PKLevel>=2 的玩家 或 race>=RC_MONSTER(80)
            # 且非任务怪的**任何对象（含玩家召唤物）**，取扫描到的第一个锁定。
            # race 12 TPetSuperGuard（m_boAttackPet=False）不打有主的召唤物，
            # 除非它正以自己为目标（ObjGuard.pas:108-119）。守卫不移动，站桩打。
            for a in list(m.map.actors.values()) if m.map is not None else ():
                if a is m or not getattr(a, "alive", False):
                    continue
                if not isinstance(a, (Monster, Player)):
                    continue    # NPC 不参与（原版 race<RC_MONSTER 不满足目标条件）
                if a.distance_to(m) > SUPER_GUARD_VIEW:
                    continue
                if isinstance(a, Player):
                    if a.pk_point >= RED_NAME_PK_POINT:
                        return a
                    continue
                if isinstance(a, Monster) and a.template is not None and a.race >= 80:
                    if t.race == 12 and a.master_id >= 0 and a.target_id != m.id:
                        continue
                    return a
            return None
        if t is not None and t.race in NEUTRAL_RACES:
            # 中立怪（鸡/鹿/羊/奥玛）在原版是 TMonster：Run 里没有 SearchTarget，
            # 只 Wondering 乱走，唯一的目标来源是被打时 TAnimalObject.Struck →
            # SetTargetCreat(hiter)。所以这里**不搜索**，只沿用已被打出仇恨的目标，
            # 目标死亡/隐身/跑出视野后自然放弃，回到闲逛。召唤物打它同样走
            # Struck（SetTargetCreat），所以目标可以是玩家也可以是有主的召唤物。
            cur = self.actor(m.target_id) if m.target_id >= 0 else None
            if (isinstance(cur, Player) and cur.alive and cur.hide_until <= now
                    and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE):
                return cur
            if (isinstance(cur, Monster) and cur.master_id >= 0 and cur.alive
                    and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE):
                return cur
            return None
        cur = self.actor(m.target_id) if m.target_id >= 0 else None
        if (isinstance(cur, Player) and cur.alive and cur.hide_until <= now
                and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE
                and (not is_guard or self._guard_hostile(m, cur))):
            return cur
        if (isinstance(cur, Monster) and cur.master_id >= 0 and cur.alive
                and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE
                and not is_guard):
            # 召唤物也在仇恨名单里（原版 TAnimalObject.SearchTarget 的
            # IsProperTarget 含宠物/骷髅——道士宝宝顶怪时怪会转火宝宝）。
            return cur
        best, best_d = None, 1 << 30
        cand = (list(m.map.actors.values()) if m.map is not None
                else list(self.players()))
        for a in cand:
            if isinstance(a, Player):
                if a.map is not m.map or a.hide_until > now or not a.alive:
                    continue
                if is_guard and not self._guard_hostile(m, a):
                    continue
            elif isinstance(a, Monster):
                if a.master_id < 0 or not a.alive or is_guard:
                    continue    # 守卫只管红名玩家（ObjMon2 TGuard 语义）
            else:
                continue
            d = a.distance_to(m)
            if d <= AGGRO_RANGE and d < best_d:
                best, best_d = a, d
        return best

    @staticmethod
    def _guard_hostile(m: Monster, p: Player) -> bool:
        # TGuardUnit.IsProperTarget（ObjMon2.pas:875-878）：①m_LastHiter=对象
        # ②其当前目标指向 112 守卫（简化为还击）③PKLevel>=2 —— PKLevel =
        # PkPoint div 100（ObjBase.pas:2236），>=2 即深红名 pk_point>=200。
        return p.id == m.last_hiter_id or p.pk_point >= RED_NAME_PK_POINT

    def _ranged_damage(self, m: Monster, target) -> int:
        """怪物攻击的原始伤害（DC 掷点 − AC），可能 <= 0。

        原版 ``TBaseObject.AttackDir`` 的伤害结算在 ``nDamage <= 0`` 时直接 Exit
        （``ObjBase.pas:22470 StruckDamage``），``TSpitSpider.SpitAttack`` 也先
        ``if nDamage <= 0 then Exit`` 才发飞矢。旧实现套了 ``max(1, …)``，让
        DC=1 的鸡也能稳定打掉 1 点血，看起来就是「鸡会攻击人」。
        """
        if isinstance(target, Player):
            ac = target.combat.get("acMax", 0)
        else:
            ac = target.template.ac if target is not None and target.template else 0
        return m.roll_attack() - ac

    def _ranged_attack(self, m: Monster, target: Actor, now: int) -> None:
        """广播飞矢，按距离延迟结算伤害（原版 SendDelayMsg）。"""
        dmg = self._ranged_damage(m, target)
        if dmg <= 0:
            return                      # 原版 SpitAttack：伤害不足不发飞矢
        self.broadcast_around(m.map, {"type": "flyAxe", "attackerId": m.id,
                                      "targetId": target.id, "dir": m.dir,
                                      "x": target.x, "y": target.y}, m.x, m.y)
        if not positive_hit(m, target):
            # 原版 SpitAttack / ArrowMonster（ObjMon.pas:699/829/890）：RM_HIT
            # 先发（箭矢照飞），命中判定不过就不 StruckDamage —— 落地无伤害。
            return
        delay = 600 + max(abs(m.x - target.x), abs(m.y - target.y)) * 50
        self.combat_state.setdefault("pending_ranged", []).append(
            {"at": now + delay, "monster": m.id, "target": target.id, "damage": dmg})

    def _apply_hit_to_player(self, m: Monster, target: Player, dmg: int) -> None:
        """魔法盾 / 绿毒 / 护身减伤后扣血，死亡走 ``killPlayer``。"""
        if not target.alive:
            return                      # 尸体不再挨打（原版 m_boDeath 目标清空）
        now = now_ms()
        # 魔法盾（STATE_BUBBLEDEFENCEUP）只折减**魔法**伤害（GetMagStruckDamage，
        # ObjBase.pas:22451）；物理伤害在 StruckDamage 里不过这条，以前在这
        # 里统一 ×0.7 是错的。
        if target.poison_armor_until > now:
            dmg = java_round(dmg * 1.2)
        if target.mana_to_health > 0 and target.mp > 0:
            mp_cost = min(target.mp, dmg)
            target.mp -= mp_cost
            dmg -= mp_cost
        target.hp = max(0, target.hp - max(0, dmg))
        self.wear_struck(target, dmg)   # 原版 StruckDamage（ObjBase.pas:22261）
        self._send_hits(m, target, dmg)
        # 原版 TSpitSpider.SpitAttack（ObjMon.pas:706-712）：m_boUsePoison 的
        # 吐毒蜘蛛命中后 Random(m_btAntiPoison + 20) = 0 即 POISON_DECHEALTH
        # 30 秒。构造里 m_boUsePoison := True 的：TSpitSpider（race 82 毒蜘蛛/
        # 剧毒蜘蛛/沙虫等）与 TBigPoisionSpider（race 119 花吻蜘蛛）；
        # THighRiskSpider（118 天狼蜘蛛）显式 := False，不毒。
        t = m.template
        if t is not None and t.race in (82, 119) and target.alive:
            anti = int(target.combat.get("antiPoison", 0))
            if random.randint(0, anti + 19) == 0:
                sec = 30
                target.poison_ticks = max(
                    target.poison_ticks,
                    max(1, java_round(sec * 1000 / POISON_INTERVAL_MS)))
                target.poison_damage = 1
                target.last_poison_at = now_ms()
                self.send_stats(target)
        if target.hp <= 0:
            self.kill_player(target, m)

    def _send_hits(self, attacker: Actor, target: Actor, damage: int) -> None:
        # dir 对齐原版 SM_HIT 的 wParam = m_btDirection（ObjBase.pas:5322-5327）：
        # 客户端播放挥击动作时按该朝向选帧——玩家轻微位移后怪物的下一次
        # 挥击必须转向玩家（原版 Attack(m_TargetCret, btDir) 每刀都带新朝向）。
        self.broadcast_around(target.map, {"type": "attack", "attackerId": attacker.id,
                                           "targetId": target.id, "damage": damage,
                                           "dir": attacker.dir,
                                           "x": target.x, "y": target.y},
                              target.x, target.y)
        self.send_stats(target)
