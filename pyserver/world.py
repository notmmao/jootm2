"""游戏世界模型（阶段 1：登录 → 进图 → 移动 → 视野广播 → tick 的最小闭环）。

对应 Java 侧 ``GameWorld`` / ``GameMap`` / ``Actor`` / ``Player`` / ``Monster``。
所有状态只在 asyncio 单事件循环内读写，不需要 ``world.lock``。

尚待精确移植的部分已在对应方法上用 ``TODO(stage3)`` 标注，主要是
``Player.recompute`` 的套装/幸运/准确-敏捷映射、战斗与技能公式、NPC 脚本、
掉落表、行会攻城等。本阶段优先保证**协议形状**与 Java 一致。
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

SIGHT_RANGE = 12
BAG_SIZE = 46
RESPAWN_MS = 15_000
HP_GROWTH, MP_GROWTH = 3, 2
BASE_HP, BASE_MP = (26, 16, 18), (12, 20, 16)
BASE_DC, BASE_MC, BASE_SC = (2, 0, 1), (0, 2, 0), (0, 0, 2)
U_DRESS, U_WEAPON, U_RIGHTHAND, U_CHARM = 0, 1, 2, 12
EQUIP_SLOTS = 13

# 与 Java ExperienceTable.NEED_EXPS 完全一致（MAXLEVEL=101）。
NEED_EXPS = (
    100, 200, 300, 400, 600, 900, 1200, 1700, 2500, 6000, 8000, 10000, 15000, 30000,
    40000, 50000, 70000, 100000, 120000, 140000, 250000, 300000, 350000, 400000, 500000,
    700000, 1000000, 1400000, 1800000, 2000000, 2400000, 2800000, 3200000, 3600000,
    4000000, 4800000, 5600000, 8200000, 9000000, 12000000, 16000000, 30000000, 50000000,
    80000000, 120000000, 160000000, 200000000, 250000000, 300000000, 350000000, 400000000,
    480000000, 560000000, 640000000, 740000000, 840000000, 950000000, 1070000000,
    1200000000, 1500000000, 1500000000, 1600000000, 1600000000, 1700000000, 1700000000,
    1800000000, 1800000000, 1900000000, 1900000000, 2000000000, 2000000000, 2000000000,
    2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000,
    2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000, 2000000000,
    2000000000, 2000000000, 2000000000, 2100000000, 2100000000, 2100000000, 2100000000,
    2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2100000000, 2140000000,
)


def encode(obj: Any) -> str:
    """与 Gson ``toString()`` 等价的输出：中文不转义、无多余空格。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def exp_for_level(level: int) -> int:
    return NEED_EXPS[max(1, min(level, len(NEED_EXPS))) - 1]


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

    def brief_json(self) -> dict:
        o = super().brief_json()
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
            if t and 0 <= slot < EQUIP_SLOTS and self.equipment[slot] is None:
                self.equipment[slot] = it
            else:
                remain.append(it)
        self.bag = remain

    def recompute(self, world: "GameWorld") -> None:
        """重算战斗属性与 HP/MP 上限。

        TODO(stage3): 精确移植 ``Player.recompute`` 的 StdMode 分派、套装加成、
        幸运/诅咒、buff 与 hpAdd/mpAdd 特例；当前实现保证字段与量级正确。
        """
        job = 0 if self.job not in (0, 1, 2) else self.job
        c = {k: 0 for k in ("acMin", "acMax", "macMin", "macMax", "dcMin", "dcMax",
                            "mcMin", "mcMax", "scMin", "scMax", "hit", "speed")}
        c["dcMin"] = c["dcMax"] = BASE_DC[job]
        c["mcMin"] = c["mcMax"] = BASE_MC[job]
        c["scMin"] = c["scMax"] = BASE_SC[job]
        hp_add = mp_add = 0
        for slot, it in enumerate(self.equipment):
            t = self._template(world, it)
            if t is None:
                continue
            hp_add += t.hpAdd
            mp_add += t.mpAdd
            if t.stdMode in (5, 6):
                c["hit"] += t.acMax
                c["dcMin"] += t.dcMin
                c["dcMax"] += t.dcMax
            else:
                c["acMin"] += t.acMin
                c["acMax"] += t.acMax
                c["macMin"] += t.macMin
                c["macMax"] += t.macMax
            c["mcMin"] += t.mcMin
            c["mcMax"] += t.mcMax
            c["scMin"] += t.scMin
            c["scMax"] += t.scMax
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
                     mac=t.macMin, macMax=t.macMax, needLevel=t.needLevel,
                     price=t.price, duraMax=t.duraMax)
        return o

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

    def to_player_json(self, world: "GameWorld") -> dict:
        c = self.combat
        bag_weight = 0
        for it in self.bag:
            t = self._template(world, it)
            if t is not None:
                bag_weight += t.weight
        mw_div = (3, 5, 4)[self.job if self.job in (0, 1, 2) else 0]
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
            "maxExp": exp_for_level(self.level), "weight": bag_weight,
            "maxWeight": 50 + (self.level // mw_div) * self.level,
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
    last_move_at: int = 0
    last_attack_at: int = 0
    paralyzed_until: int = 0


# ---------------------------------------------------------------- 世界

class GameWorld:
    def __init__(self, config: ServerConfig, db: Database):
        self.config = config
        self.db = db
        self.item_templates = db.load_item_templates()
        self.monster_templates = db.load_monster_templates()
        self.magic_templates = db.load_magic_templates()

        self.maps: dict[str, GameMap] = {}
        self.actors: dict[int, Actor] = {}
        self.players_by_name: dict[str, Player] = {}
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
            for _ in range(min(max(entry.count, 1), 3)):
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
            o.update(shield=a.shield_until > now, hidden=a.hide_until > now,
                     wideHit=a.wide_hit_on, longHit=a.long_hit_on, fireHit=a.fire_hit_on,
                     ac=f"{c.get('acMin', 0)}-{c.get('acMax', 0)}",
                     mac=f"{c.get('macMin', 0)}-{c.get('macMax', 0)}",
                     dc=f"{c.get('dcMin', 0)}-{c.get('dcMax', 0)}",
                     mc=f"{c.get('mcMin', 0)}-{c.get('mcMax', 0)}",
                     sc=f"{c.get('scMin', 0)}-{c.get('scMax', 0)}",
                     hit=c.get("hit", 0), speed=c.get("speed", 0))
        o["poison"] = a.poison_ticks > 0 if isinstance(a, Player) else False
        o["poisonArmor"] = a.poison_armor_until > now if isinstance(a, Player) else False
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

    def map_snapshot(self, p: Player, msg_type: str) -> dict:
        game_map = p.map or self.default_map
        p.seen.clear()
        nearby = []
        for a in self.nearby(game_map, p.x, p.y):
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
            "groundItems": [],
        }

    def update_sight(self, p: Player) -> None:
        game_map = p.map
        if game_map is None or p.session is None:
            return
        now_seen: set[int] = set()
        for a in game_map.actors.values():
            if a.alive and a.id != p.id and max(abs(a.x - p.x), abs(a.y - p.y)) <= SIGHT_RANGE:
                now_seen.add(a.id)
                if a.id not in p.seen:
                    p.session.send_json({"type": "entityAdd", "entity": a.brief_json()})
        for actor_id in p.seen - now_seen:
            p.session.send_json({"type": "entityRemove", "id": actor_id})
        p.seen = now_seen

    # ---- tick ------------------------------------------------------------

    def tick(self, now: int) -> None:
        """一帧：昼夜、门关闭、怪物复活与基础追击。

        TODO(stage3): 完整 AI（远程怪/守卫/宠物）、毒与 buff、火墙、
        地面物品过期、攻城计时、治疗术持续回血。
        """
        if now - self._last_day_check >= 60_000:
            self._last_day_check = now
            bright = day_bright_for_hour(datetime.now().hour)
            if bright != self.day_bright:
                self.day_bright = bright
                self.broadcast_all({"type": "dayChanging", "dayBright": bright})

        for game_map in self.maps.values():
            for (x, y), opened_at in list(game_map.open_doors.items()):
                if now - opened_at > 5_000:
                    del game_map.open_doors[(x, y)]
                    self.broadcast_around(game_map, {"type": "door", "x": x, "y": y,
                                                     "open": False}, x, y)

        for a in list(self.actors.values()):
            if not isinstance(a, Monster):
                continue
            if a.pending_respawn:
                if now >= a.respawn_at:
                    a.pending_respawn = False
                    a.alive = True
                    a.x, a.y = a.spawn_x, a.spawn_y
                    a.hp = a.max_hp = a.template.hp if a.template else a.max_hp
                    home = a.spawn_map or self.default_map
                    if a.map is not None and a.map is not home:
                        a.map.remove(a.id)
                    home.add(a)
                    self.broadcast_entity_add(a)
                continue
            if not a.alive or a.template is None:
                continue
            target = next((p for p in self.players_by_name.values()
                           if p.map is a.map and p.alive
                           and max(abs(p.x - a.x), abs(p.y - a.y)) <= SIGHT_RANGE), None)
            if target is None:
                continue
            walk_ms = max(200, a.template.walkSpeed or 800)
            attack_ms = max(300, a.template.attackSpeed or 1000)
            dist = max(abs(target.x - a.x), abs(target.y - a.y))
            if now - a.last_move_at >= walk_ms and dist > 1:
                a.last_move_at = now
                d = direction_toward(a.x, a.y, target.x, target.y, a.dir)
                if self.try_move(a, d):
                    self.broadcast_move(a)
            elif dist <= 1 and now - a.last_attack_at >= attack_ms:
                a.last_attack_at = now
                t = a.template
                damage = max(1, random.randint(min(t.dc, t.maxDc), max(t.dc, t.maxDc))
                             - target.combat.get("acMin", 0))
                target.hp = max(0, target.hp - damage)
                self.broadcast_around(a.map, {"type": "attack", "attackerId": a.id,
                                              "targetId": target.id, "damage": damage,
                                              "x": target.x, "y": target.y},
                                     target.x, target.y)
                self.send_stats(target)
                if target.hp <= 0:  # 简化：复活到出生点，不做死亡/掉落流程
                    target.hp = target.max_hp
                    target.x, target.y = target.home_x, target.home_y
                    self.send_stats(target)
                    self.send_system(target, "你被击败了，已回到出生点。")
