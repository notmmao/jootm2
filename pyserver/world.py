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
    parse_mon_gen, parse_notice, parse_start_point, read_map, scan_available_maps,
)
from .templates import ItemTemplate, MagicTemplate, MonsterTemplate
from .util import (
    AGGRO_RANGE, DOOR_OPEN_MS, GUARD_RACES, HSET_SHAPES,
    NEUTRAL_RACES,
    POISON_INTERVAL_MS, PSET_SHAPES, RANGED_ATTACK_RANGE, RANGED_CHASE_RANGE,
    RANGED_KEEP_AWAY, RANGED_RACES, REGEN_INTERVAL_MS, SHAPE_MANA_TO_HP,
    SHAPE_PARALYSIS_WEAPON, SHAPE_REVIVAL_RING, SHAPE_SUCK_HEALTH,
    SHAPE_TELEPORT_RING, SLAVE_COLORS, SLAVE_UP_EXP, YSET_SHAPES,
    java_div, java_round, slot_accepts, slot_for,
)
from .world_extra import BODY_LEATHERY, WorldExtra

SIGHT_RANGE = 12
# 旁观者血条同步间隔（ms）：玩家 hp 变化后最多延迟这么久才广播给周围玩家。
# 太小会放大广播量（每次回血/中毒都要发），太大血条看起来迟滞。绑定 100ms tick 的整数倍。
HP_SYNC_MS = 400
BAG_SIZE = 46
RESPAWN_MS = 15_000
HP_GROWTH, MP_GROWTH = 3, 2
BASE_HP, BASE_MP = (26, 16, 18), (12, 20, 16)
BASE_DC, BASE_MC, BASE_SC = (2, 0, 1), (0, 2, 0), (0, 0, 2)
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
    if 5 <= hour <= 9:
        return 0
    if 10 <= hour <= 16:
        return 1
    if 17 <= hour <= 19:
        return 2
    return 3


# ---------------------------------------------------------------- 地图

class GameMap:
    """``MapGrid`` 的运行时包装：阻挡网格 + 当前图上的实体 + 门开闭状态。"""

    __slots__ = ("grid", "actors", "open_doors")

    def __init__(self, grid: MapGrid):
        self.grid = grid
        self.actors: dict[int, "Actor"] = {}
        self.open_doors: dict[tuple[int, int], int] = {}

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
        return any(a.alive and a.x == x and a.y == y for a in self.actors.values())

    def add(self, actor: "Actor") -> None:
        self.actors[actor.id] = actor
        actor.map = self

    def remove(self, actor_id: int) -> None:
        self.actors.pop(actor_id, None)


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
        }


@dataclass(slots=True)
class Player(Actor):
    account_id: int = 0
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
    hide_until: int = 0
    poison_ticks: int = 0
    poison_armor_until: int = 0
    last_use_item_at: int = 0
    pk_point: int = 0
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
    summon_id: int = -1
    target_id: int = -1
    inc_healing: int = 0
    # 吃药写入的回血/回蓝池：原版不吃即时血，药水的 AC/MAC 写进
    # m_nIncHealth / m_nIncSpell（ObjBase.pas:23359-23366），由 _regen 的
    # 回血 tick 按「每 tick Level div 10 + 5」慢慢消耗（ObjBase.pas:3782-3850）。
    inc_health: int = 0
    inc_spell: int = 0
    # GM 账号标记（登录时按 config.GM_ACCOUNTS 判定）。
    is_gm: bool = False
    last_healing_at: int = 0
    poison_damage: int = 0
    last_poison_at: int = 0
    ac_buff_until: int = 0
    mac_buff_until: int = 0
    ac_buff_bonus: int = 0
    mac_buff_bonus: int = 0
    last_fire_hit_at: int = 0
    last_rush_rush_at: int = 0
    power_hit_count: int = 0
    power_hit_point: int = 0
    attack_mode: int = 1
    skill_cooldown: dict[int, int] = field(default_factory=dict)

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

    def brief_json(self) -> dict:
        # 显式基类调用，不用零参数 super()：本类是 @dataclass(slots=True)，
        # 3.10~3.12 重建类时不会修正 __class__ cell，零参数 super() 会报
        # "super(type, obj): obj must be an instance or subtype of type"。
        o = Actor.brief_json(self)
        o.update(sex=self.sex, hair=self.hair, job=self.job,
                 dress=self.dress_looks(self.world_ref),
                 weapon=self.weapon_shape(self.world_ref))
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

        weapon = self.equipment[U_WEAPON] if U_WEAPON < len(self.equipment) else None
        wt = self._template(world, weapon)
        if weapon is not None and wt is not None:
            base = wt.specialPwr if 1 <= wt.specialPwr <= 10 else 0
            self.luck = min(10, base + int(weapon.get("lucky", 0)))

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

        self.combat = c
        self.max_hp = BASE_HP[job] + hp_add + (self.level - 1) * HP_GROWTH
        self.max_mp = BASE_MP[job] + mp_add + (self.level - 1) * MP_GROWTH
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
        div = (20, 100, 50)[self.job if self.job in (0, 1, 2) else 0]
        return 15 + (self.level // div) * self.level

    def max_hand_weight(self) -> int:
        div = (13, 90, 42)[self.job if self.job in (0, 1, 2) else 0]
        return min(255, 12 + (self.level // div) * self.level)

    def instance_json(self, world: "GameWorld", it: dict) -> dict:
        t = self._template(world, it)
        o = {
            "makeIndex": it.get("makeIndex", 0), "index": it.get("index", 0),
            "name": t.name if t else "?", "looks": t.looks if t else 0,
            "durability": it.get("durability", 0),
            "durabilityMax": it.get("durabilityMax", 0),
        }
        if t:
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
            out[str(mid)] = {
                "name": mt.name if mt else str(mid),
                "level": s.get("level", 0), "key": s.get("key", 0),
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
        """最大负重（原版 ``m_Abil.MaxWeight``：战士 /3、法师 /5、道士 /4）。"""
        div = (3, 5, 4)[self.job if self.job in (0, 1, 2) else 0]
        return 50 + (self.level // div) * self.level

    def to_player_json(self, world: "GameWorld") -> dict:
        c = self.combat
        return {
            "id": self.id, "name": self.name, "type": "player",
            "sex": self.sex, "hair": self.hair, "job": self.job,
            "level": self.level, "exp": self.exp, "x": self.x, "y": self.y,
            "dir": self.dir, "hp": self.hp, "maxHp": self.max_hp,
            "mp": self.mp, "maxMp": self.max_mp, "gold": self.gold,
            "race": self.race, "appr": self.appr,
            "dress": self.dress_looks(world), "weapon": self.weapon_shape(world),
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
    # 挖肉磨损（原版 m_nBodyLeathery，初值 50，每次挖 Random(16)+5）
    body_leathery: int = BODY_LEATHERY
    next_attack_at: int = 0
    poison_ticks: int = 0
    poison_armor_until: int = 0
    poison_damage: int = 0
    last_poison_at: int = 0
    last_move_at: int = 0
    last_attack_at: int = 0
    paralyzed_until: int = 0

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
        if self.master_id >= 0 and 0 <= self.slave_exp_level < len(SLAVE_COLORS):
            o["nameColor"] = SLAVE_COLORS[self.slave_exp_level]
        # 尸体仍要作为实体下发给客户端（原版 SM_DEATH 保留 actor，画 ActDeath 帧）
        o["alive"] = self.alive
        o["corpse"] = (not self.alive and self.corpse_until > 0)
        return o


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
        self.ground_items: dict[int, dict] = {}
        self._id_gen = itertools.count(1)
        self._item_seq = itertools.count(max(1_000_000, db.max_item_make_index() + 1))
        self.day_bright = day_bright_for_hour(datetime.now().hour)
        self._last_day_check = 0
        self.last_regen_at = now_ms()

        sp = parse_start_point(config.envir_dir / "StartPoint.txt")
        self.start_x, self.start_y = sp.x, sp.y
        self.default_map = GameMap(read_map(sp.map_id, config.map_dir))
        self.maps[self.default_map.name] = self.default_map

        self.available_maps = scan_available_maps(config.map_dir)
        info: MapInfo = parse_map_info(config.envir_dir / "MapInfo.txt")
        self.map_titles = info.titles
        # 与 Java 一致：两端都没有 .map 文件的连接不生效。
        self.gates_by_src: dict[str, list[Gate]] = {}
        kept = 0
        for g in info.gates:
            if g.src_map not in self.available_maps or g.dst_map not in self.available_maps:
                continue
            self.gates_by_src.setdefault(g.src_map, []).append(g)
            kept += 1
        print(f"[world] loaded {len(info.titles)} map defs, {kept} gates")

        self.map_mini_index = parse_mini_map(config.envir_dir / "MiniMap.txt")
        print(f"[world] loaded {len(self.map_mini_index)} minimap entries")
        self.mon_gen_entries: list[MonGen] = parse_mon_gen(config.envir_dir / "MonGen.txt")
        self.notice_lines = parse_notice(config.notice_file)
        self.spawn_monsters(self.default_map)
        self.init_extra()

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
        self.actors.pop(p.id, None)
        self.players_by_name.pop(p.name, None)
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
                m = Monster(id=self.new_id(), name=t.name,
                            x=pos[0], y=pos[1], dir=4,
                            hp=t.hp, max_hp=t.hp, mp=t.mp, max_mp=t.mp,
                            level=t.level, race=t.race, race_img=t.raceImg, appr=t.appr,
                            template=t, spawn_x=pos[0], spawn_y=pos[1],
                            respawn_interval_ms=entry.interval_min * 60_000 if entry.interval_min > 0 else 0)
                m.exp = t.exp
                self.add_actor(m, game_map)
                spawned += 1
        if spawned:
            print(f"[world] map={game_map.name} {game_map.width}x{game_map.height}, "
                  f"spawned monsters={spawned}")
        return spawned

    # ---- 地图 -----------------------------------------------------------

    def get_or_load_map(self, name: str) -> GameMap | None:
        if not name:
            return self.default_map
        if name in self.maps:
            return self.maps[name]
        if name not in self.available_maps:
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
        return target

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
        a.x, a.y = nx, ny
        return True

    # ---- 广播 / 快照 ----------------------------------------------------

    def broadcast_all(self, msg: dict) -> None:
        text = encode(msg)
        for p in self.players_by_name.values():
            if p.session is not None:
                p.session.send_text(text)

    def broadcast_around(self, game_map: GameMap | None, msg: dict,
                         x: int, y: int, radius: int = SIGHT_RANGE) -> None:
        if game_map is None:
            return
        text = encode(msg)
        for a in game_map.actors.values():
            if isinstance(a, Player) and a.session is not None \
                    and max(abs(a.x - x), abs(a.y - y)) <= radius:
                a.session.send_text(text)

    def broadcast_move(self, a: Actor) -> None:
        self.broadcast_around(a.map, {"type": "move", "id": a.id, "x": a.x, "y": a.y,
                                      "dir": a.dir, "run": a.running}, a.x, a.y)

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

    def nearby(self, game_map: GameMap, x: int, y: int,
               radius: int = SIGHT_RANGE) -> list[Actor]:
        return [a for a in game_map.actors.values() if a.alive
                and max(abs(a.x - x), abs(a.y - y)) <= radius]

    @staticmethod
    def _corpse_visible(a: Actor) -> bool:
        """未到 makeGhost 时间的尸体：原版所有死亡对象都留 3 分钟（可挖肉的动物
        只是额外允许 Alt+左键挖）。"""
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
                and max(abs(a.x - x), abs(a.y - y)) <= radius]

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
                    "width": game_map.width, "height": game_map.height},
            "dayBright": self.day_bright,
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
        """
        for a in list(self.actors.values()):
            if isinstance(a, Player):
                self._regen(a, now)
                self._expire_buffs(a, now)
            elif isinstance(a, Monster):
                self._monster_ai(a, now)
            self._apply_poison(a, now)
        self._tick_day_bright(now)
        self._close_timed_out_doors(now)
        self._tick_fire_walls(now)
        self._tick_pending_ranged_hits(now)
        self._sync_player_hp(now)
        self.tick_extra(now)          # 地面物品过期 + 攻城计时
        self._expire_corpses(now)     # 尸体到点移除（原版 MakeGhost）
        self._respawn_dead(now)

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
        self.broadcast_all({"type": "dayChanging", "dayBright": bright})

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
            if now - f.get("last_tick", 0) < 1000:
                continue
            f["last_tick"] = now
            gm = f["map"]
            for a in list(gm.actors.values()):
                if not isinstance(a, Monster) or not a.alive or a.id == f["owner"]:
                    continue
                if a.x != f["x"] or a.y != f["y"]:
                    continue
                dmg = f["damage"]
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
                        self.add_exp(owner, a.template.exp if a.template else 0)
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
            if (isinstance(m, Monster) and isinstance(t, Player)
                    and m.alive and t.alive and m.map is t.map):
                self._apply_hit_to_player(m, t, h["damage"])
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
            self.add_actor(m, home)
            self.broadcast_entity_add(m)

    # -- 怪物 / 宠物 AI ----------------------------------------------------

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
        target = self._resolve_target(m, now)
        m.target_id = target.id if target is not None else -1

        if target is not None:
            d = m.distance_to(target)
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
                    m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                    if self.try_move(m, m.dir):
                        self.broadcast_move(m)
                return
            if d == 1:
                if now >= m.next_attack_at:
                    m.next_attack_at = now + attack_ms
                    m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                    dmg = self._ranged_damage(m, target)
                    if dmg <= 0:
                        # 原版：RM_HIT 照发（有挥击动作），但 StruckDamage 因
                        # nDamage<=0 直接返回 —— 目标不播受击动作、不掉血、不飘字。
                        self.broadcast_around(m.map, {"type": "attack", "attackerId": m.id,
                                                      "targetId": target.id, "damage": 0,
                                                      "noDamage": True,
                                                      "x": target.x, "y": target.y},
                                              m.x, m.y)
                    else:
                        self._apply_hit_to_player(m, target, dmg)
            elif now >= m.next_move_at:
                m.next_move_at = now + walk_ms
                m.dir = direction_toward(m.x, m.y, target.x, target.y, m.dir)
                if self.try_move(m, m.dir):
                    self.broadcast_move(m)
        elif not is_guard and now >= m.next_move_at:
            m.next_move_at = now + walk_ms
            if random.random() < 0.35:
                m.dir = int(random.random() * 8)
                if self.try_move(m, m.dir):
                    self.broadcast_move(m)

    def _summon_ai(self, m: Monster, now: int) -> None:
        """宠物跟随 / 协助攻击 / 神兽变身 / 升级（``GameWorld.summonAi``）。"""
        master = self.actor(m.master_id)
        if not isinstance(master, Player) or not master.alive or master.map is not m.map:
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
                    dmg = max(1, m.roll_attack()
                              - (enemy.template.ac if enemy.template else 0))
                    enemy.hp = max(0, enemy.hp - dmg)
                    self._send_hits(m, enemy, dmg)
                    if enemy.hp <= 0:
                        self.kill_monster(enemy, now, master)
                        self.add_exp(master, enemy.template.exp if enemy.template else 0)
                        self._gain_slave_exp(m, enemy)
            elif now >= m.next_move_at:
                m.next_move_at = now + walk_interval
                m.dir = direction_toward(m.x, m.y, enemy.x, enemy.y, m.dir)
                if self.try_move(m, m.dir):
                    self.broadcast_move(m)
        elif m.distance_to(master) > 2 and now >= m.next_move_at:
            m.next_move_at = now + walk_interval
            m.dir = direction_toward(m.x, m.y, master.x, master.y, m.dir)
            if self.try_move(m, m.dir):
                self.broadcast_move(m)

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

    def _resolve_target(self, m: Monster, now: int) -> Player | None:
        is_guard = m.template is not None and m.template.race in GUARD_RACES
        if m.template is not None and m.template.race in NEUTRAL_RACES:
            # 中立怪（鸡/鹿/羊/奥玛）在原版是 TMonster：Run 里没有 SearchTarget，
            # 只 Wondering 乱走，唯一的目标来源是被打时 TAnimalObject.Struck →
            # SetTargetCreat(hiter)。所以这里**不搜索**，只沿用已被打出仇恨的目标，
            # 目标死亡/隐身/跑出视野后自然放弃，回到闲逛。
            cur = self.actor(m.target_id) if m.target_id >= 0 else None
            if (isinstance(cur, Player) and cur.alive and cur.hide_until <= now
                    and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE):
                return cur
            return None
        cur = self.actor(m.target_id) if m.target_id >= 0 else None
        if (isinstance(cur, Player) and cur.alive and cur.hide_until <= now
                and cur.map is m.map and cur.distance_to(m) <= AGGRO_RANGE
                and (not is_guard or self._guard_hostile(m, cur))):
            return cur
        best, best_d = None, 1 << 30
        for p in self.players():
            if p.map is not m.map or p.hide_until > now:
                continue
            if is_guard and not self._guard_hostile(m, p):
                continue
            d = p.distance_to(m)
            if p.alive and d <= AGGRO_RANGE and d < best_d:
                best, best_d = p, d
        return best

    @staticmethod
    def _guard_hostile(m: Monster, p: Player) -> bool:
        return p.id == m.last_hiter_id or p.pk_point > 0

    def _ranged_damage(self, m: Monster, target: Player) -> int:
        """怪物对玩家的原始伤害（DC 掷点 − AC），可能 <= 0。

        原版 ``TBaseObject.AttackDir`` 的伤害结算在 ``nDamage <= 0`` 时直接 Exit
        （``ObjBase.pas:22470 StruckDamage``），``TSpitSpider.SpitAttack`` 也先
        ``if nDamage <= 0 then Exit`` 才发飞矢。旧实现套了 ``max(1, …)``，让
        DC=1 的鸡也能稳定打掉 1 点血，看起来就是「鸡会攻击人」。
        """
        return m.roll_attack() - target.combat.get("acMax", 0)

    def _ranged_attack(self, m: Monster, target: Player, now: int) -> None:
        """广播飞矢，按距离延迟结算伤害（原版 SendDelayMsg）。"""
        dmg = self._ranged_damage(m, target)
        if dmg <= 0:
            return                      # 原版 SpitAttack：伤害不足不发飞矢
        self.broadcast_around(m.map, {"type": "flyAxe", "attackerId": m.id,
                                      "targetId": target.id, "dir": m.dir,
                                      "x": target.x, "y": target.y}, m.x, m.y)
        delay = 600 + max(abs(m.x - target.x), abs(m.y - target.y)) * 50
        self.combat_state.setdefault("pending_ranged", []).append(
            {"at": now + delay, "monster": m.id, "target": target.id, "damage": dmg})

    def _apply_hit_to_player(self, m: Monster, target: Player, dmg: int) -> None:
        """魔法盾 / 绿毒 / 护身减伤后扣血，死亡走 ``killPlayer``。"""
        now = now_ms()
        if target.shield_until > now:
            dmg = java_div(dmg * 7, 10)
        if target.poison_armor_until > now:
            dmg = java_round(dmg * 1.2)
        if target.mana_to_health > 0 and target.mp > 0:
            mp_cost = min(target.mp, dmg)
            target.mp -= mp_cost
            dmg -= mp_cost
        target.hp = max(0, target.hp - max(0, dmg))
        self._send_hits(m, target, dmg)
        if target.hp <= 0:
            self.kill_player(target)

    def _send_hits(self, attacker: Actor, target: Actor, damage: int) -> None:
        self.broadcast_around(target.map, {"type": "attack", "attackerId": attacker.id,
                                           "targetId": target.id, "damage": damage,
                                           "x": target.x, "y": target.y},
                              target.x, target.y)
        self.send_stats(target)
