"""GameWorld 的领域扩展（物品/掉落/NPC/行会/组队/交易/攻城）。

以 mixin 方式挂到 ``GameWorld``，避免把 world.py 撑成单体。所有状态仍在
asyncio 单 loop 内串行读写，与 Java 的 ``synchronized (world.lock)`` 等价。
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .drop_rates import load_drop_rates
from .gamedata import parse_map_quest, read_lines
from .util import (
    BAG_SIZE, GROUND_ITEM_TTL_MS, RED_NAME_PK_POINT, SIGHT_RANGE, U_WEAPON,
    encode, java_div, now_ms,
)


# ---------------------------------------------------------------- 常量

# 尸体保留时长：原版 ``ObjBase.pas:3769`` → ``g_Config.dwMakeGhostTime``
# （``M2Share.pas`` 默认 3*60*1000）。**所有**死亡对象都留这么久，不只是可挖肉
# 的动物；到点 ``MakeGhost`` 才从视野里消失（RM_DISAPPEAR）。
CORPSE_MS = 180_000

# 挖肉磨损：原版 ``ObjBase.pas:17478-17495``
#   n10 := Random(16) + 5;  Dec(m_nBodyLeathery, n10);
#   if m_nBodyLeathery <= 0 then 骷髅 + TakeBagItems + 重置为 50
# 初值 50 见 ``ObjBase.pas:1286``。所以出肉次数是随机的 3~10 次（平均约 4），
# 不是固定的 2 次。
BODY_LEATHERY = 50

# 掉落物归属时长：原版 ``g_Config.dwFloorItemCanPickUpTime`` 默认
# ``2 * 60 * 1000``（ClientPickUpItem ObjBase.pas:1699）。到期后
# ``OfBaseObject := nil``，谁都能捡。
FLOOR_ITEM_OWNERSHIP_MS = 2 * 60 * 1000
BUTCH_WEAR_MIN = 5
BUTCH_WEAR_MAX = 20

# 可挖尸体 = 原版 ``m_boAnimal`` 为 True 的怪物类（``UsrEngn.pas:1835-1864``
# + 各怪物类构造函数）。原版**不是**按 race 段判定，而是逐类赋值：
#   51 鸡   TMonster        m_boAnimal := True  UsrEngn.pas:1838（磨损 50）
#   52 鹿   TMonster        m_boAnimal := True  UsrEngn.pas:1853（磨损 150）
#      └ 1/30 变 TChickenDeer                   UsrEngn.pas:1846（磨损 150）
#   53 狼   TATMonster      m_boAnimal := True  UsrEngn.pas:1861（磨损 150）
#   82 毒蜘蛛 TSpitSpider   m_boAnimal := True  ObjMon.pas:664
#   84 蝎子   TScorpion     m_boAnimal := True  ObjMon.pas:650
#   85 食人花 TStickMonster m_boAnimal := True  ObjMon2.pas:166
#   90 洞蛆   TGasAttackMonster m_boAnimal := True ObjMon.pas:865
# 118 天狼蜘蛛/工蚁（THighRiskSpider, ObjMon.pas:752）、
# 119 花吻蜘蛛（TBigPoisionSpider, ObjMon.pas:849）显式 :='False'，不可挖。
ANIMAL_RACES = frozenset({51, 52, 53, 82, 84, 85, 90})

# ``m_nBodyLeathery`` 初值：鸡 50（UsrEngn.pas:1840），鹿/狼 150
# （UsrEngn.pas:1849/1855/1863），其余走 TBaseObject.Create 里的默认 50
# （ObjBase.pas:1286）。磨损大 → 鹿/狼要挖 ~15 刀才出肉。
ANIMAL_LEATHERY = {51: 50, 52: 150, 53: 150}

# ``RC_ANIMAL=50 / RC_MONSTER=80``（MirClient/Grobal2.pas:1092-1096）：
# 只有 race ∈ [50,80) 的「动物」挖空后才变骷髅并 ApplyMeatQuality；
# race ≥ 80 的蜘蛛/食人花/洞蛆挖空只清尸体背包，尸体原样保留（可再挖，但已空）。
SKELETON_RACE_MIN = 50
SKELETON_RACE_MAX = 80

# 挖肉/转身间隔：``M2Share.pas:2121`` dwTurnIntervalTime = 600ms，
# ``ObjBase.pas:17466`` 里间隔不足直接 Exit（连点无效）。
BUTCH_INTERVAL_MS = 600


# ---------------------------------------------------------------- 数据结构

def gold_shape(n_gold: int) -> int:
    """原版 ``M2Share.pas:3568 GetGoldShape``：金币在地上按数量分 5 档外形。

    ```pascal
    Result := 112;
    if nGold >= 30 then Result := 113;
    if nGold >= 70 then Result := 114;
    if nGold >= 300 then Result := 115;
    if nGold >= 1000 then Result := 116;
    ```
    外形号是 DnItems.wil 的图标索引（112 小钱袋 → 116 大钱袋）。
    """
    s = 112
    if n_gold >= 30:
        s = 113
    if n_gold >= 70:
        s = 114
    if n_gold >= 300:
        s = 115
    if n_gold >= 1000:
        s = 116
    return s


@dataclass(slots=True)
class GroundItem:
    id: int
    item: dict | None
    gold: int
    x: int
    y: int
    created_at: int
    map: Any = None
    # 归属（原版 MapItem.OfBaseObject）：只有**怪物掉落**归属击杀者（含召唤
    # 主人，ObjBase.pas:20987 用 m_ExpHitter），玩家死亡散落 / 手动丢弃 /
    # 手动丢金币都传 nil（:21011 / :16265 / ClientDropGold）→ 谁都能捡。
    owner_id: int = -1

    def is_gold(self) -> bool:
        return self.gold > 0

    def to_json(self, name: str, looks: int) -> dict:
        if self.is_gold():
            # 原版金币堆外形按数量分档（M2Share.pas:3568 GetGoldShape），
            # 之前恒下发 looks=0，客户端只能画一颗圆点。
            return {"id": self.id, "x": self.x, "y": self.y, "gold": self.gold,
                    "name": "金币", "looks": gold_shape(self.gold)}
        return {"id": self.id, "x": self.x, "y": self.y, "gold": 0,
                "name": name, "looks": looks}


@dataclass(slots=True)
class DropEntry:
    sel_point: int
    max_point: int
    item_name: str
    count: int = 1


@dataclass(slots=True)
class Npc:
    id: int
    name: str
    script_name: str = ""
    map_name: str = ""
    x: int = 0
    y: int = 0
    dir: int = 0
    face: int = 0
    appr: int = 0
    kind: int = 0
    merchant: bool = False
    script: Any = None
    map: Any = None
    alive: bool = True
    level: int = 1
    race: int = 50          # MessageIds.RC_PEACENPC
    race_img: int = 0
    seen: set[int] = field(default_factory=set)

    def brief_json(self) -> dict:
        # 原版 TNormNpc.Create（ObjNpc.pas:5646-5652）设 m_nLight := 2，
        # TSuperGuard 是 4。客户端 m_LightMap 据此给 NPC 脚下加一圈光，
        # 所以城里站在商人旁边会明显亮一块（免蜡关闭时）。
        return {"id": self.id, "name": self.name, "type": "npc",
                "x": self.x, "y": self.y, "dir": self.dir,
                "hp": 1, "maxHp": 1, "race": self.race, "raceImg": self.race_img,
                "appr": self.appr, "level": self.level, "merchant": self.merchant,
                "face": self.face, "light": 2}


@dataclass(slots=True)
class Guild:
    name: str
    master_name: str
    members: list[str] = field(default_factory=list)
    war_guilds: list[str] = field(default_factory=list)
    ally_guilds: list[str] = field(default_factory=list)
    owns_castle: bool = False

    def member_count(self) -> int:
        return len(self.members)

    def add_member(self, name: str) -> bool:
        if name in self.members:
            return False
        self.members.append(name)
        return True


@dataclass(slots=True)
class Party:
    member_ids: list[int] = field(default_factory=list)

    def leader_id(self) -> int:
        return self.member_ids[0] if self.member_ids else -1


@dataclass(slots=True)
class TradeSession:
    a: Any
    b: Any
    a_items: list[dict] = field(default_factory=list)
    b_items: list[dict] = field(default_factory=list)
    a_gold: int = 0
    b_gold: int = 0
    a_confirm: bool = False
    b_confirm: bool = False


@dataclass(slots=True)
class Castle:
    name: str = "沙巴克"
    own_guild: str = ""
    map_id: str = "3"
    main_door_x: int = 0
    main_door_y: int = 0
    main_door_hp: int = 0
    main_door_max_hp: int = 0
    main_door_open: bool = False
    wall_hp: list[int] = field(default_factory=lambda: [0, 0, 0])
    wall_max_hp: list[int] = field(default_factory=lambda: [0, 0, 0])
    war_active: bool = False
    war_end_at: int = 0
    war_guild: str = ""

    def door_broken(self) -> bool:
        return self.main_door_hp <= 0

    def walls_broken(self) -> bool:
        return all(h <= 0 for h in self.wall_hp)


@dataclass(slots=True)
class GoodsEntry:
    index: int
    name: str
    price: int
    stock: int


# ---------------------------------------------------------------- 数据解析

def _split_quoted(line: str) -> list[str]:
    """空白分隔但保留双引号字段（对齐 NpcData.split / WorldData.splitQuoted）。"""
    out: list[str] = []
    i, n = 0, len(line)
    while i < n:
        while i < n and line[i].isspace():
            i += 1
        if i >= n:
            break
        if line[i] == '"':
            end = line.find('"', i + 1)
            if end < 0:
                out.append(line[i:])
                break
            out.append(line[i + 1:end])
            i = end + 1
        else:
            start = i
            while i < n and not line[i].isspace():
                i += 1
            out.append(line[start:i])
    return out


def _num(v: str, default: int = 0) -> int:
    try:
        return int(str(v).strip())
    except (ValueError, TypeError):
        return default


def load_drop_tables(envir: Path) -> dict[str, list[DropEntry]]:
    out: dict[str, list[DropEntry]] = {}
    d = envir / "MonItems"
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.txt")):
        entries: list[DropEntry] = []
        for line in read_lines(path):
            t = line.strip()
            if not t or t.startswith(";"):
                continue
            tokens = t.split()
            if len(tokens) < 2 or "/" not in tokens[0]:
                continue
            prob = tokens[0].split("/", 1)
            n, m = _num(prob[0]), _num(prob[1])
            if n > 0 and m > 0:
                entries.append(DropEntry(n - 1, m, tokens[1],
                                         _num(tokens[2], 1) if len(tokens) > 2 else 1))
        if entries:
            # 数据里有「洞蛆1 .txt」「蜜蜂 .txt」这类文件名带尾随空格的文件
            # （原版 Mir200 的 MonItems 就这样），不去空格永远匹配不上怪物名。
            out[path.stem.strip()] = entries
    return out


def load_npc_spawns(envir: Path) -> list[dict]:
    spawns: list[dict] = []
    for filename, merchant in (("Npcs.txt", False), ("MerChant.txt", True)):
        for line in read_lines(envir / filename):
            t = line.strip()
            if not t or t.startswith(";"):
                continue
            p = _split_quoted(t)
            if len(p) < 7:
                continue
            if merchant:
                spawns.append(dict(name=p[4], script_name=p[0], map_name=p[1],
                                   x=_num(p[2]), y=_num(p[3]), face=_num(p[5]),
                                   appr=_num(p[6]), kind=0, merchant=True))
            else:
                spawns.append(dict(name=p[0], script_name=p[0], map_name=p[2],
                                   x=_num(p[3]), y=_num(p[4]), face=_num(p[5]),
                                   appr=_num(p[6]), kind=_num(p[1]), merchant=False))
    return spawns


def load_unbind_list(envir: Path) -> dict[int, str]:
    """UnbindList.txt：卷包 shape → 拆出的物品名。"""
    out: dict[int, str] = {}
    for line in read_lines(envir / "UnbindList.txt"):
        t = line.strip()
        if not t or t.startswith(";"):
            continue
        p = _split_quoted(t)
        if len(p) >= 2:
            out[_num(p[0])] = p[1]
        elif "=" in t:
            k, v = t.split("=", 1)
            out[_num(k)] = v.strip()
    return out


def load_castle(envir: Path) -> Castle:
    c = Castle()
    keys = {
        "CastleName": "name", "OwnGuild": "own_guild", "CastleMap": "map_id",
        "MainDoorX": "main_door_x", "MainDoorY": "main_door_y",
        "MainDoorOpen": "main_door_open",
    }
    wall_keys = {"LeftWallHP": 0, "CenterWallHP": 1, "RightWallHP": 2}
    for line in read_lines(envir / "Castle" / "Sabuk.txt"):
        t = line.strip()
        if not t or t.startswith((";", "[")) or "=" not in t:
            continue
        k, v = (x.strip() for x in t.split("=", 1))
        if k in keys:
            attr = keys[k]
            setattr(c, attr, (_num(v) != 0) if attr == "main_door_open"
                    else (v if attr in ("name", "own_guild", "map_id") else _num(v)))
        elif k == "MainDoorHP":
            c.main_door_hp = c.main_door_max_hp = _num(v)
        elif k in wall_keys:
            i = wall_keys[k]
            c.wall_hp[i] = c.wall_max_hp[i] = _num(v)
    return c


# ---------------------------------------------------------------- Mixin

class WorldExtra:
    """挂到 GameWorld 的扩展方法集合。``init_extra()`` 必须在 __init__ 末尾调用。"""

    def init_extra(self) -> None:
        envir = self.config.envir_dir
        self.drop_tables = load_drop_tables(envir)
        # 统一倍率表（config/game_rates.json）：金币/物品掉落与击杀经验，
        # 改这里就能整体调节奏，不用去动 MonItems 里几百个 1/n 单条概率。
        # 旧文件名 drop_rates.json 作为兼容回退。
        rates_path = self.config.root / "config" / "game_rates.json"
        if not rates_path.is_file():
            rates_path = self.config.root / "config" / "drop_rates.json"
        self.drop_rates = load_drop_rates(rates_path)
        self.apply_magic_train_rate()
        self.unbind_items = load_unbind_list(envir)
        self.castle = load_castle(envir)
        self.guilds: dict[str, Guild] = {}
        self.guild_of_player: dict[str, str] = {}
        self.parties: dict[int, Party] = {}
        self.trades: dict[int, TradeSession] = {}
        self.trade_requests: dict[int, int] = {}
        self.combat_state: dict[str, Any] = {"fire_walls": [], "pets": {}}
        self.npcs: dict[int, Npc] = {}
        self.npcs_by_map: dict[str, list[Npc]] = {}
        self.script_cache: dict[str, Any] = {}
        self.map_quests = parse_map_quest(envir / "MapQuest.txt")
        self.quest_scripts: dict[str, Any] = {}
        self._load_map_quests()
        self._load_npcs()
        print(f"[world] loaded {len(self.drop_tables)} drop tables, "
              f"{len(self.npcs)} npcs, castle={self.castle.name}/{self.castle.map_id}, "
              f"quests={len(self.map_quests)}/{len(self.quest_scripts)}")

    def _load_map_quests(self) -> None:
        """加载 ``MapQuest_Def/*.txt``，与 Java ``GameWorld.loadMapQuests`` 一致。"""
        from .npc_script import load_script

        quest_dir = self.config.envir_dir / "MapQuest_Def"
        for q in self.map_quests:
            if q.qfile not in self.quest_scripts:
                self.quest_scripts[q.qfile] = load_script(quest_dir, q.qfile, "")

    def trigger_map_quest(self, p, m) -> None:
        """杀怪触发地图任务脚本（``GameWorld.triggerMapQuest``）。"""
        if not self.map_quests or m.map is None or p is None:
            return
        from .npc_script import NpcScriptEngine

        for q in self.map_quests:
            if q.map_id != m.map.name or not q.mon_name or q.mon_name != m.name:
                continue
            if p.quest_params.get(q.set_number, 0) != q.value:
                continue
            script = self.quest_scripts.get(q.qfile)
            if script is None:
                continue
            try:
                r = NpcScriptEngine(self).execute_script(p, None, script, "main")
            except Exception as e:      # 单个坏脚本不能打断世界 tick
                print(f"[quest] {q.qfile} failed: {type(e).__name__}: {e}")
                continue
            if r.say_text:
                self.send_system(p, r.say_text)

    # ---- NPC ------------------------------------------------------------

    def _load_npcs(self) -> None:
        from .npc_script import load_script

        envir = self.config.envir_dir
        for s in load_npc_spawns(envir):
            npc = Npc(id=self.new_id(), name=s["name"], script_name=s["script_name"],
                      map_name=s["map_name"], x=s["x"], y=s["y"], face=s["face"],
                      appr=s["appr"], kind=s["kind"], merchant=s["merchant"])
            d = envir / ("Market_Def" if npc.merchant else "Npc_Def")
            key = f"{'M' if npc.merchant else 'N'}:{npc.script_name}:{npc.map_name}"
            if key not in self.script_cache:
                self.script_cache[key] = load_script(d, npc.script_name, npc.map_name)
            npc.script = self.script_cache[key]
            self.npcs[npc.id] = npc
            self.npcs_by_map.setdefault(npc.map_name, []).append(npc)
            # NPC 作为静态 Actor 挂到地图，才能出现在 nearby / entityAdd 里。
            gm = self.maps.get(npc.map_name) or self.get_or_load_map(npc.map_name)
            if gm is not None:
                npc.map = gm
                gm.add(npc)

    def npc(self, npc_id: int) -> Npc | None:
        return self.npcs.get(int(npc_id))

    def actor(self, actor_id: int) -> Any:
        return self.actors.get(int(actor_id))

    def goods(self, npc: Npc | None) -> list[dict]:
        """商店货物：对齐 ``NpcScriptEngine.goods`` 的 [goods] 段。"""
        from .npc_script import NpcScriptEngine

        return NpcScriptEngine(self).goods(npc)

    # ---- 模板 / 背包 ----------------------------------------------------

    def item_template_by_name(self, name: str | None):
        if name is None:
            return None
        return next((t for t in self.item_templates.values() if t.name == name), None)

    def new_item_instance(self, t, dura: int | None = None) -> dict:
        d = dura if dura is not None else t.duraMax
        return {"makeIndex": self.next_item_make_index(), "index": t.index,
                "name": t.name, "durability": d, "durabilityMax": t.duraMax,
                "lucky": 0, "slot": -1}

    def count_item(self, p, name: str) -> int:
        if name == "金币":
            return min(p.gold, 2_147_483_647)
        n = 0
        for it in list(p.bag) + [e for e in p.equipment if e]:
            t = self.item_templates.get(int(it.get("index", 0)))
            if t is not None and t.name == name:
                n += 1
        return n

    def give_item(self, p, name: str, count: int) -> bool:
        if name == "金币":
            p.gold += count
            return True
        t = self.item_template_by_name(name)
        if t is None:
            return False
        if len(p.bag) + count > BAG_SIZE:
            return False
        for _ in range(count):
            p.bag.append(self.new_item_instance(t))
        return True

    def take_item(self, p, name: str, count: int) -> bool:
        if name == "金币":
            if p.gold < count:
                return False
            p.gold -= count
            return True
        if self.count_item(p, name) < count:
            return False
        left = count
        for i in range(len(p.bag) - 1, -1, -1):
            if left <= 0:
                break
            t = self.item_templates.get(int(p.bag[i].get("index", 0)))
            if t is not None and t.name == name:
                removed = p.bag.pop(i)
                p.clear_quick_ref(int(removed.get("makeIndex", 0)))
                left -= 1
        return True

    # ---- 地面物品 / 掉落 ------------------------------------------------

    def ground_item(self, item_id: int) -> GroundItem | None:
        return self.ground_items.get(int(item_id))

    def _broadcast_item_drop(self, gi: GroundItem) -> None:
        t = self.item_templates.get(int(gi.item.get("index", 0))) if gi.item else None
        o = gi.to_json(t.name if t else "?", t.looks if t else 0)
        o["type"] = "itemDrop"
        self.broadcast_around(gi.map, o, gi.x, gi.y, SIGHT_RANGE)

    def _broadcast_item_remove(self, gi: GroundItem) -> None:
        self.broadcast_around(gi.map, {"type": "itemRemove", "id": gi.id},
                              gi.x, gi.y, SIGHT_RANGE)

    def drop_instance(self, p, it: dict) -> GroundItem:
        # 原版玩家丢物品也走 DropItemDown(1) 的 GetDropPosition，但脚下格通常
        # 空着，这里保留「丢在脚下」的交互；格上限/金币合并仍按 AddToMap。
        gi = GroundItem(self.new_id(), it, 0, p.x, p.y, now_ms(), p.map)
        self.add_ground_item(gi)
        return gi

    def drop_item(self, p, make_index: int) -> bool:
        for i, it in enumerate(p.bag):
            if int(it.get("makeIndex", 0)) == make_index:
                p.bag.pop(i)
                self.drop_instance(p, it)
                return True
        return False

    def _can_pick_owner(self, p, gi) -> bool:
        """掉落归属判定（原版 ``ClientPickUpItem`` ObjBase.pas:1699-1707）：
        掉落 ``dwFloorItemCanPickUpTime``（默认 2 分钟）内归属
        ``MapItem.OfBaseObject``（只有怪物掉落会设置——击杀者/召唤主人），
        本人或同队成员可捡；超时原版 ``OfBaseObject := nil`` 谁都能捡；
        归属者离线（查不到）同样放行。"""
        if gi.owner_id < 0 or gi.owner_id == p.id:
            return True
        if now_ms() - gi.created_at > FLOOR_ITEM_OWNERSHIP_MS:
            return True
        owner = gi.map.players.get(int(gi.owner_id))
        if owner is None:
            return True
        my_party = self.party_of(p.id)
        if my_party is not None and owner.id in my_party.member_ids:
            return True
        owner_party = self.party_of(owner.id)
        if owner_party is not None and p.id in owner_party.member_ids:
            return True
        return False

    def pickup(self, p, item_id: int) -> bool:
        gi = self.ground_items.get(int(item_id))
        if gi is None or gi.map is not p.map:
            return False
        # 原版 ``GetItem(m_nCurrX, m_nCurrY)``（ObjBase.pas:1696）：**必须站在
        # 物品所在格上**才能捡——这也构成「站上去挡住别人捡」的玩法（玩家
        # 不能重叠占格，别人进不了这一格就永远捡不到）。
        if gi.x != p.x or gi.y != p.y:
            return False
        if not gi.is_gold() and len(p.bag) >= BAG_SIZE:
            self.send_system(p, "背包已满，无法拾取。")
            return False
        if not self._can_pick_owner(p, gi):
            # g_sCanotPickUpItem（ObjBase.pas:1705）
            self.send_system(p, "在一定时间以内无法捡起此物品！！！")
            return False
        del self.ground_items[gi.id]
        if gi.is_gold():
            p.gold += gi.gold
        elif gi.item is not None:
            p.bag.append(gi.item)
        self._broadcast_item_remove(gi)
        return True

    def scatter_pos(self, game_map, cx: int, cy: int, max_range: int = 3):
        """原版 ``TBaseObject.GetDropPosition``（ObjBase.pas:1534-1584）。

        ```pascal
        for i := 1 to nRange do
          for ii := -i to i do            // 行
            for III := -i to i do         // 列
              if GetItemEx(nDX,nDY,nItemCount) = nil and bo2C then Break;
        ```

        关键三点，旧实现全错了：
        1. 每轮扫的是 **整个 (2i+1)² 方形**（不是只取外环），所以总是从
           尸体左上方那一格开始，按行逐格铺开；
        2. **完全确定性、没有 random**，BOSS 一次掉几十上百件时就会一圈圈
           铺满一大片，这就是玩家俗称的「爆了」；
        3. 一圈都没有空格时，回退到「占用件数最少」的格（原版 n24 < 8），
           否则退回尸体所在格。

        ``GetItemEx``（Envir.pas:1276-1310）统计该格 OS_ITEMOBJECT 数量；
        ``chFlag != 0``、有门、有**活着**的 actor 时 bo2C=False，不能放东西。
        ``AddToMap``（Envir.pas:192-262）再限制同格最多 5 件（金币合并例外）。
        """
        counts: dict[tuple[int, int], int] = {}
        for gi in self.ground_items.values():
            if gi.map is game_map:
                k = (gi.x, gi.y)
                counts[k] = counts.get(k, 0) + 1
        # OS_MOVINGOBJECT 且 not m_boDeath → bo2C := False
        blocked = {(a.x, a.y) for a in game_map.actors.values() if a.alive}
        least, least_pos = 999, (cx, cy)
        for i in range(1, max(1, max_range) + 1):
            for dy in range(-i, i + 1):
                for dx in range(-i, i + 1):
                    x, y = cx + dx, cy + dy
                    if not game_map.can_walk(x, y) or (x, y) in blocked:
                        continue
                    n = counts.get((x, y), 0)
                    if n == 0:
                        return (x, y)            # 找到第一个空格立刻返回
                    if n < least:
                        least, least_pos = n, (x, y)
        if least < 8:
            return least_pos
        return (cx, cy) if game_map.can_walk(cx, cy) else None

    def add_ground_item(self, gi: GroundItem) -> bool:
        """原版 ``TEnvirnoment.AddToMap``（Envir.pas:192-262）。

        - 同格金币合并，且总量 ``<= 2000`` 才合并（否则另起一堆）；
        - 非金币物品同格最多 **5 件**（``ObjList.Count >= 5`` 直接拒绝）；
        - 合并时沿用旧 makeIndex，客户端先删旧堆再下发新数量。
        """
        if gi.is_gold():
            for other in self.ground_items.values():
                if (other.map is gi.map and other.x == gi.x and other.y == gi.y
                        and other.is_gold() and other.gold + gi.gold <= 2000):
                    other.gold += gi.gold
                    other.created_at = now_ms()
                    self._broadcast_item_drop(other)
                    return False
        elif sum(1 for o in self.ground_items.values()
                 if o.map is gi.map and o.x == gi.x and o.y == gi.y) >= 5:
            return False
        self.ground_items[gi.id] = gi
        self._broadcast_item_drop(gi)
        return True

    def drop_loot(self, m, killer=None) -> None:
        """怪物掉落（原版 ``ScatterBagItems(AttackBaseObject)`` ObjBase.pas:20987
        用 ``m_ExpHitter``——本项目以击杀者近似）。掉落物归属击杀者
        （含召唤主人）2 分钟（``FLOOR_ITEM_OWNERSHIP_MS``），本人/同队可捡。"""
        from .world import Player

        table = self.drop_tables.get(m.template.name if m.template else m.name)
        if not table:
            return
        mon = m.template.name if m.template else m.name
        rates = self.drop_rates
        owner_id = killer.id if killer is not None and isinstance(killer, Player) else -1
        for e in table:
            # 原版概率是 num/den（MonItems 的 `1/4`），倍率在判定时相乘。
            if not rates.hit(mon, e.item_name, e.sel_point + 1, e.max_point):
                continue
            pos = self.scatter_pos(m.map, m.x, m.y)
            if pos is None:
                continue
            if e.item_name == "金币":
                gold = java_div(e.count, 2) + int(random.random() * e.count)
                gold = max(1, int(gold * rates.amount(mon)))
                gi = GroundItem(self.new_id(), None, gold, pos[0], pos[1],
                                now_ms(), m.map, owner_id)
            else:
                t = self.item_template_by_name(e.item_name)
                if t is None:
                    continue
                dura = max(1, java_div(t.duraMax * (20 + int(random.random() * 81)), 100))
                gi = GroundItem(self.new_id(), self.new_item_instance(t, dura), 0,
                                pos[0], pos[1], now_ms(), m.map, owner_id)
            self.add_ground_item(gi)

    def roll_butch_loot(self, m, p) -> list[dict]:
        """挖肉产出：原版 ``TPlayObject.ClientGetButchItem``（ObjBase.pas:17491）
        挖空尸体后 ``TakeBagItems(BaseObject)`` —— 动物死亡不散落物品，
        MonItems 掉落表一直留在「尸体背包」里，挖空一次性全给玩家。

        这里在挖空的瞬间按同一张表掷骰（与 ``drop_loot`` 相同的 num/den 概率），
        金币直接入账，物品返回实例列表由调用方放进背包。
        """
        mon = m.template.name if m.template else m.name
        table = self.drop_tables.get(mon)
        if not table:
            return []
        rates = self.drop_rates
        out: list[dict] = []
        for e in table:
            if not rates.hit(mon, e.item_name, e.sel_point + 1, e.max_point):
                continue
            if e.item_name == "金币":
                gold = max(1, java_div(e.count, 2) + int(random.random() * e.count))
                p.gold += int(gold * rates.amount(mon))
                self.send_stats(p, extras={"gold"})
                continue
            t = self.item_template_by_name(e.item_name)
            if t is None:
                continue
            dura = max(1, java_div(t.duraMax * (20 + int(random.random() * 81)), 100))
            out.append(self.new_item_instance(t, dura))
        return out

    def ground_items_json(self, p) -> list[dict]:
        out = []
        for gi in self.ground_items.values():
            if gi.map is not p.map:
                continue
            if max(abs(gi.x - p.x), abs(gi.y - p.y)) > SIGHT_RANGE:
                continue
            t = self.item_templates.get(int(gi.item.get("index", 0))) if gi.item else None
            out.append(gi.to_json(t.name if t else "?", t.looks if t else 0))
        return out

    @staticmethod
    def butchable(m) -> bool:
        """原版 ``m_boAnimal``（各怪物类构造函数里的赋值，见 ``ANIMAL_RACES``）。

        ```pascal
        TScorpion.Create:      m_boAnimal := True;   // race 84 蝎子（ObjMon.pas:650）
        TSpitSpider.Create:    m_boAnimal := True;   // race 82 毒蜘蛛（ObjMon.pas:664）
        TStickMonster.Create:  m_boAnimal := True;   // race 85 食人花（ObjMon2.pas:166）
        TGasAttackMonster:     m_boAnimal := True;   // race 90 洞蛆（ObjMon.pas:865）
        TBigPoisionSpider:     m_boAnimal := False;  // race 119
        THighRiskSpider:       m_boAnimal := False;  // race 118
        ```
        鸡/鹿/羊/狼（51/52/53）由 ``UsrEngn.pas`` 的 ANIMAL_CHICKEN/DEER/WOLF
        分支赋值。本仓库 Monster.csv 里 race 52 还含「护卫」(appr 71) 这类
        非动物模板，用动物 appr 白名单（羊 43 / 狼 70 / 鸡 160 / 鹿 161）排除。
        食人花/毒蜘蛛/蝎子/洞蛆死亡不散落物品，尸体靠 Alt+左键挖出
        （MonItems/食人花.txt 等），这与原版 TakeBagItems 一致。
        """
        race = WorldExtra.butch_race(m)
        return race != 0

    @staticmethod
    def butch_race(m) -> int:
        """可挖则返回 race（51/52/53/82/84/85/90），否则 0。"""
        t = getattr(m, "template", None)
        if t is None:
            return 0
        race = int(t.race)
        if race not in ANIMAL_RACES:
            return 0
        if race in (51, 52, 53) and int(t.appr) not in (43, 70, 160, 161):
            return 0
        return race

    @staticmethod
    def butch_skeleton(m) -> bool:
        """挖空后是否变骷髅（原版 ``ObjBase.pas:17485``：race ∈ [50,80)）。"""
        race = WorldExtra.butch_race(m)
        return SKELETON_RACE_MIN <= race < SKELETON_RACE_MAX

    @staticmethod
    def base_leathery(m) -> int:
        """``m_nBodyLeathery`` 初值（鸡 50 / 鹿・狼 150 / 其余 50）。"""
        return ANIMAL_LEATHERY.get(WorldExtra.butch_race(m), BODY_LEATHERY)

    def kill_monster(self, m, now: int, killer=None) -> None:
        m.alive = False
        m.pending_respawn = True
        m.respawn_at = now + (m.respawn_interval_ms or 15_000)
        m.hp = 0
        m.target_id = -1
        t = m.template
        # 原版 TZilKinZombi.Die（ObjMon.pas:1285-1294，race 96）：nZilKillCount
        # > 0 时记复活时间 dw558/dw560 = (Random(20)+4)*1000（死后 4~23 秒），
        # 然后无条件 Dec(nZilKillCount)（减到 -1 表示彻底死亡）。复活是**同一
        # 对象原地复活**（ReAlive），不走 MonGen 重刷 —— 所以把重生点顺延到
        # 复活窗口之后；复活成功时取消重生（见 _tick_zilkin_revive），若附近
        # 一直没人导致复活不发生，则重生补怪照常，数量守恒。
        if t is not None and int(t.race) == 96:
            if m.zil_revives > 0:
                m.zil_revive_at = now + (random.randint(0, 19) + 4) * 1000
                m.respawn_at = max(m.respawn_at, m.zil_revive_at + 1000)
            m.zil_revives -= 1
        # 原版 ObjBase.pas:20983：动物死亡**不**走 ScatterBagItems，肉要玩家
        # Alt+左键挖。尸体一律保留 ``CORPSE_MS``（原版 dwMakeGhostTime 3 分钟），
        # 到点由 ``_expire_corpses`` 广播移除——**所有**怪都留尸体，不只是动物。
        animal = self.butchable(m)
        m.corpse_until = now + CORPSE_MS
        m.butch_count = 0
        m.skeleton = False
        # 原版 m_nBodyLeathery 在构造时按类赋值（鸡 50 / 鹿・狼 150），死亡时
        # **不**重置；未挖过就一直是最初的满磨损值。
        m.body_leathery = self.base_leathery(m)
        m.bag_looted = False
        if not animal:
            self.drop_loot(m, killer)
        if killer is not None:
            self.trigger_map_quest(killer, m)
        # corpse=True → 客户端按尸体处理（隐藏名字/血条、画倒地帧）；
        # animal 只是决定要不要提示「可以挖肉」。
        o = {"type": "death", "id": m.id, "corpse": True, "animal": animal}
        self.broadcast_around(m.map, o, m.x, m.y, SIGHT_RANGE)

    def level_for_exp(self, exp: int) -> int:
        """按**累计**经验反查等级（仅用于旧存档迁移，见 ``normalize_exp``）。

        ``NEED_EXPS`` 是每级升级阈值（100/200/300/400/600…），累计和才是等级门槛。
        """
        from .world import NEED_EXPS

        total = 0
        for level in range(1, 102):
            total += NEED_EXPS[min(level, len(NEED_EXPS)) - 1]
            if exp < total:
                return level
        return 101

    def kill_exp(self, monster: str, exp: int) -> int:
        """击杀经验倍率（``config/game_rates.json`` 的 ``exp``，可按怪物覆盖）。

        只在**击杀结算**处调用；``add_exp`` 本身保持原样，测试与 GM 加经验
        不受倍率影响。
        """
        rate = self.drop_rates.exp_for(monster)
        if exp <= 0 or rate == 1.0:
            return exp
        if rate <= 0:
            return 0
        return max(1, int(round(exp * rate)))

    def apply_magic_train_rate(self) -> None:
        """按 ``magicTrain`` 折算技能升级所需修炼点。

        升级判定（``_train_skill``）和客户端技能页都读 ``maxTrain``，只改模板
        一处即可；启动时调用一次，改配置需重启。
        """
        for t in self.magic_templates.values():
            t.maxTrain = [self.drop_rates.magic_need(v) for v in t.maxTrain]

    def add_exp(self, p, exp: int) -> None:
        """加经验 + 升级（对齐 ``ObjBase.pas:1844 GetExp``）。

        ```pascal
        Inc(m_Abil.Exp, dwExp);
        if m_Abil.Exp >= m_Abil.MaxExp then begin
          Dec(m_Abil.Exp, m_Abil.MaxExp);   // 只扣一次：一次加经验最多升一级
          Inc(m_Abil.Level);
          HasLevelUp(m_Abil.Level - 1);      // MaxExp := GetLevelExp(新等级)
        end;
        ```

        ``m_Abil.Exp`` 是**本级内**经验，不是累计值——旧实现按累计存，
        客户端拿「累计 1183」去比「本级 400」，EXP 条永远是满的。
        """
        p.exp += exp
        if exp > 0:
            self.send_system(p, f"获得经验 {exp} 点。", "#ffffff", "#b00000")
        if p.max_exp > 0 and p.exp >= p.max_exp:
            p.exp -= p.max_exp
            if p.level < 101:
                p.level += 1
            p.recompute(self)
            # 原版升级**不回满**：RecalcLevelAbilitys 只把超过新上限的 HP/MP
            # 钳回来（ObjBase.pas:1938-1939），没有 := MaxHP 的赋值。
            p.hp = min(p.hp, p.max_hp)
            p.mp = min(p.mp, p.max_mp)
            self.send_system(p, f"恭喜！你升到了 {p.level} 级。",
                             "#ffff00", "#b00000")
        self.send_stats(p)

    # ---- 玩家死亡 / 冲撞 / 火墙 / 召唤 --------------------------------

    def _remove_equipped_by_shape(self, p, shape: int) -> None:
        """移除身上第一件指定 Shape 的装备（对齐 ``removeEquippedByShape``）。"""
        for slot, it in enumerate(p.equipment):
            if it is None:
                continue
            t = self.item_templates.get(int(it.get("index", 0)))
            if t is not None and t.shape == shape:
                p.equipment[slot] = None
                return

    def kill_player(self, p, killer=None) -> None:
        # 原版 SM_NOWDEATH（Actor.pas:2388-2395）：客户端 m_boDeath := TRUE，
        # 播死亡音效与 bmg_gameover，PlayScn.pas:1476 把画面切成 ceGrayScale
        # 黑白屏；服务端**不**把角色搬回城。复活靠退出角色重新登录，
        # UsrEngn.pas:576-600 在登录时看到 HP<=0 才回出生点并给 HP := 14。
        # 旧实现这里立刻满血 + change_map(home)，于是「刚死就在城里复活、
        # 死亡音乐还在播」，正是用户反馈 25。
        if p.dead:
            return                      # 死亡状态下重复的伤害/毒跳不再触发
        if p.revival_ring:
            # 复活戒指免死一次后损毁：不移除的话下次 recompute 又会变回可用。
            self._remove_equipped_by_shape(p, 114)
            p.recompute(self)
            p.hp, p.mp = p.max_hp // 2, p.max_mp
            self.send_system(p, "复活戒指生效，你免于一死！")
            self.send_stats(p)
            return
        self.broadcast_around(p.map, {"type": "death", "id": p.id}, p.x, p.y)
        summon = self.actor(p.summon_id) if p.summon_id >= 0 else None
        if summon is not None and summon.alive:
            self.remove_summon(summon)
        p.hp, p.mp, p.dir = 0, 0, 4
        p.dead = True
        # 原版 m_boDeath 是**独立于「还在不在图上」**的死亡标志，但所有目标搜索都
        # 拿它过滤：`TAnimalObject.SearchTarget` 遍历视野时 `if not BaseObject.m_boDeath`
        # （ObjBase.pas:22677），`TBaseObject.Run` 每轮还会把尸体目标清成 nil
        # （ObjBase.pas:3884-3895）。本项目 `alive` 就是这套过滤用的旗标（怪物
        # `_resolve_target` 只认 `p.alive`），以前只置 `dead` 不置 `alive`，
        # 于是人死了怪物照旧围上来砍尸体——用户反馈 13。
        # 注意：尸体仍要能被别人看见，靠 `World._sight_actors` 的「尸体可见」分支
        # （`_corpse_visible` 对玩家也返回 True），与占位/目标筛选互不影响。
        p.alive = False
        p.poison_ticks = 0
        p.inc_health = p.inc_spell = 0
        p.target_id = -1
        self._death_drops(p, killer)
        self.send_stats(p)
        self.send_system(p, "你被击败了。按 Esc →「返回角色选择」重新登录即可在出生点复活。")

    def _death_drops(self, p, killer) -> None:
        """死亡掉落惩罚（原版 ``TBaseObject.Die`` ObjBase.pas:20982-21013）。

        按原版默认配置（M2Share.pas:2019-2026）实现：
        - 背包散落 ``boDieScatterBag=True``、``nDieScatterBagRate=3``：
          每件背包物品 ``Random(3)=0`` 掉落（1/3 概率）；
          红名 ``PKLevel>=2``（pk_point>=200）``boDieRedScatterBagAll=True`` 全掉
          （TPlayObject.ScatterBagItems，ObjBase.pas:26648-26687）；
        - 身上装备 ``DropUseItems``（:20689-20757）：被怪杀死才掉
          （``boKillByMonstDropUseItem=True``，:21005；被玩家杀
          ``boKillByHumanDropUseItem=False`` 不掉），每槽 ``Random(30)=0``
          掉落、红名 ``nDieRedDropUseItemRate=15``；
        - 金币 ``boDieDropGold=False``（:21012）默认不掉。

        落地半径 ``DropWide=2``（ScatterBagItems:26650 / DropUseItems 传 2）。
        毒死/未知来源按被怪杀处理（原版 m_LastHiter 即毒源怪）。
        """
        from .world import Player

        has_bag = bool(p.bag)
        has_equip = any(it is not None for it in p.equipment)
        if not has_bag and not has_equip:
            return
        red = p.pk_point >= RED_NAME_PK_POINT
        dropped: list[str] = []

        # ① 背包散落（原版 for i := Count-1 downto 0 逐件 Random(3)=0）
        keep: list[dict] = []
        for it in p.bag:
            if red or random.randrange(3) == 0:
                if self._death_drop_one(p, it):
                    dropped.append("物品")
                    continue
            keep.append(it)
        p.bag = keep

        # ② 身上装备：被怪杀才掉（每槽 Random(rate)=0）
        if not isinstance(killer, Player):
            rate = 15 if red else 30
            for slot in range(len(p.equipment)):
                it = p.equipment[slot]
                if it is not None and random.randrange(rate) == 0:
                    if self._death_drop_one(p, it):
                        p.equipment[slot] = None
                        dropped.append("装备")
        if dropped:
            p.recompute(self)
            self.send_bag_update(p)
            self.send_system(p, f"死亡掉落了 {len(dropped)} 件物品在尸体旁。")

    def _death_drop_one(self, p, it: dict) -> bool:
        pos = self.scatter_pos(p.map, p.x, p.y, 2)
        if pos is None:
            return False
        gi = GroundItem(self.new_id(), it, 0, pos[0], pos[1], now_ms(), p.map)
        if not self.add_ground_item(gi):
            return False
        it.pop("slot", None)
        return True

    def drop_gold(self, p, amount: int) -> bool:
        """原版 ``TPlayObject.ClientDropGold``（ObjBase.pas:16187-16212）：
        ``nGold >= m_nGold`` 拒绝——必须至少留 1 金币；成功则
        ``DropGoldDown``（:2301）``GetDropPosition`` 半径 3 落地，
        同格 <=2000 金币自动合并（``add_ground_item``）。"""
        if amount <= 0 or amount >= p.gold:
            return False
        pos = self.scatter_pos(p.map, p.x, p.y, 3)
        if pos is None:
            return False
        gi = GroundItem(self.new_id(), None, amount, pos[0], pos[1],
                        now_ms(), p.map)
        self.add_ground_item(gi)    # 金币 False = 已并入同格旧堆，同样算成功
        p.gold -= amount
        self.send_stats(p)
        return True

    def broadcast_rush(self, a, steps: int) -> None:
        self.broadcast_around(a.map, {"type": "move", "id": a.id, "x": a.x, "y": a.y,
                                      "dir": a.dir, "rush": True,
                                      "rushSteps": max(1, steps)}, a.x, a.y)

    def can_fire_fly(self, game_map, x: int, y: int) -> bool:
        return game_map.inside(x, y) and not game_map.is_high_wall(x, y)

    def make_fire_wall_at(self, p, tx: int, ty: int, damage: int,
                          duration_sec: int) -> int:
        expire = now_ms() + duration_sec * 1000
        cells = ((tx, ty - 1), (tx - 1, ty), (tx, ty), (tx + 1, ty), (tx, ty + 1))
        made = 0
        walls = self.combat_state.setdefault("fire_walls", [])
        for cx, cy in cells:
            if not self.can_fire_fly(p.map, cx, cy):
                continue
            walls.append({"map": p.map, "x": cx, "y": cy, "expire": expire,
                          "damage": damage, "owner": p.id, "last_tick": 0})
            made += 1
            self.broadcast_around(p.map, {"type": "fireWall", "x": cx, "y": cy,
                                          "duration": duration_sec}, cx, cy)
        return made

    def spawn_summon(self, owner, monster_name: str):
        from .world import Monster

        t = self.monster_templates.get(monster_name)
        if t is None:
            return None
        # 原版 TBaseObject.MakeSlave（ObjBase.pas:7167）：GetFrontPosition ——
        # 召唤物出现在主人**正前方一格**（面朝方向），不是随机撒在半径 2 内。
        pos = self._front_near(owner.map, owner.x, owner.y, owner.dir)
        if pos is None:
            return None
        m = Monster(id=self.new_id(), name=t.name, x=pos[0], y=pos[1], dir=owner.dir,
                    hp=t.hp, max_hp=t.hp, level=t.level, race=t.race,
                    race_img=t.raceImg, appr=t.appr, template=t,
                    spawn_x=pos[0], spawn_y=pos[1], master_id=owner.id)
        m.exp = t.exp
        self.add_actor(m, owner.map)
        self.combat_state.setdefault("pets", {})[m.id] = owner.id
        self.broadcast_entity_add(m)
        return m

    def remove_summon(self, m) -> None:
        m.alive = False
        m.pending_respawn = False
        master = self.actor(m.master_id) if m.master_id >= 0 else None
        if master is not None and getattr(master, "summon_id", -1) == m.id:
            master.summon_id = -1
        self.combat_state.get("pets", {}).pop(m.id, None)
        self.remove_actor(m.id)
        self.broadcast_around(m.map, {"type": "death", "id": m.id}, m.x, m.y)

    # ---- 传送卷 / 解包 --------------------------------------------------

    def unbind_item_name(self, shape: int) -> str | None:
        return self.unbind_items.get(shape)

    def _near_walkable(self, game_map, tx: int, ty: int) -> tuple[int, int]:
        """目标格不可站时在原版 ±4 窗口内就近找可站格（GetMapRangePlace 语义）。

        找不到则返回原点（原版 SpaceMove 也会强行落到该格）。
        """
        if game_map.can_walk(tx, ty):
            return tx, ty
        best = None
        best_d = None
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                nx, ny = tx + dx, ty + dy
                if 0 <= nx < game_map.width and 0 <= ny < game_map.height \
                        and game_map.can_walk(nx, ny):
                    d = abs(dx) + abs(dy)
                    if best_d is None or d < best_d:
                        best, best_d = (nx, ny), d
        return best if best is not None else (tx, ty)

    def random_walk_pos(self, game_map):
        for _ in range(100):
            x = int(random.random() * game_map.width)
            y = int(random.random() * game_map.height)
            if game_map.can_walk(x, y):
                return x, y
        return self.start_x, self.start_y

    def use_scroll(self, p, shape: int) -> bool:
        # 原版 EatUseItems（ObjBase.pas:23518-23574）逐 shape 对齐：
        #   1 地牢逃脱卷 → BaseObjectMove(m_sHomeMap,'','')：**随机**落点（空串
        #     坐标走 MapRandomMove，:23605-23613），不是固定 home 坐标；
        #   2 随机传送卷 → 仅当 !Envir.Flag.boNORANDOMMOVE 才本图随机（:23527），
        #     幸运房等图（MapInfo NORECALL/NORANDOMMOVE）原版直接无效；
        #   3 回城卷 → PKLevel>=2（pk_point>=200）去红名回家点（GOM 默认
        #     3/845/674，M2Share.pas:1693-1695），否则 home 固定坐标（:23537-43）；
        #   5 行会回城卷 → 攻城方成员去城堡回城点（Castle.pas:921-928
        #     GetHomeX/Y = 配置点 ±4 随机，GOM 默认 3/644/290），无行会/非占领方
        #     原版不传送（Result=False，物品不消耗）。
        if shape == 1:
            map_id, tx, ty = p.home_map, -1, -1
        elif shape == 2:
            if any(f.startswith("NORANDOMMOVE")
                   for f in self.map_flags.get(p.map.name if p.map else "0", ())):
                return False
            map_id = p.map.name if p.map else "0"
            tx = ty = -1
        elif shape == 3:
            if (p.pk_point or 0) >= RED_NAME_PK_POINT:
                map_id, tx, ty = "3", 845, 674
            else:
                map_id, tx, ty = p.home_map, p.home_x, p.home_y
        elif shape == 5:
            gname = self.guild_of_player.get(p.name)
            castle = getattr(self, "castle", None)
            if not gname or castle is None or castle.own_guild != gname:
                return False
            map_id, tx, ty = (castle.map_id or "3"), 644, 290
        else:
            return False
        target = self.get_or_load_map(map_id)
        if target is None:
            return False
        if shape in (3, 5):
            # 原版固定坐标直接 SpaceMove；落点不可站时按原版
            # GetMapRangePlace 就近找可站格（±4 窗口）。
            if not target.can_walk(tx, ty):
                fx, fy = self._near_walkable(target, tx, ty)
                tx, ty = fx, fy
            if shape == 5:
                tx = (tx - 4) + int(random.random() * 9)
                ty = (ty - 4) + int(random.random() * 9)
                if not target.can_walk(tx, ty):
                    tx, ty = self._near_walkable(target, 644, 290)
        else:
            tx, ty = self.random_walk_pos(target)
        # 原版随机卷轴/回城卷 EatUseItems（ObjBase.pas:20573/20590）→ SpaceMove：
        # 两段特效与宝宝跟随传送完全一致——离地 SM_SPACEMOVE_HIDE（magic 250-259
        # 10 帧 + s_spacemove_out）+ 现身 SM_SPACEMOVE_SHOW（magic 260 + 音 110）。
        # 旧实现只发 entityRemove+changeMap，没有这两段，所以卷轴传送「瞬移无特效」。
        self.space_move(p, map_id, tx, ty)
        if p.session is not None:
            p.session.send_json(self.map_snapshot(p, "changeMap"))
        self.broadcast_entity_add(p)
        self.space_move_show(p)
        return True

    # ---- 行会 / 攻城 ----------------------------------------------------

    def create_guild(self, name: str | None, master: str) -> Guild | None:
        if not name or not name.strip() or name in self.guilds or master in self.guild_of_player:
            return None
        g = Guild(name, master, [master])
        self.guilds[name] = g
        self.guild_of_player[master] = name
        self._save_guild(g)
        return g

    def guild_of(self, player_name: str) -> Guild | None:
        gname = self.guild_of_player.get(player_name)
        return self.guilds.get(gname) if gname else None

    def join_guild(self, player_name: str, guild_name: str | None) -> bool:
        if player_name in self.guild_of_player:
            return False
        g = self.guilds.get(guild_name or "")
        if g is None or not g.add_member(player_name):
            return False
        self.guild_of_player[player_name] = g.name
        self._save_guild(g)
        return True

    def leave_guild(self, player_name: str) -> bool:
        gname = self.guild_of_player.pop(player_name, None)
        if gname is None:
            return False
        g = self.guilds.get(gname)
        if g is not None:
            if player_name in g.members:
                g.members.remove(player_name)
            if g.master_name == player_name:
                del self.guilds[gname]
            else:
                self._save_guild(g)
        return True

    def declare_guild_war(self, a_name: str, b_name: str | None) -> bool:
        a, b = self.guilds.get(a_name), self.guilds.get(b_name or "")
        if a is None or b is None:
            return False
        if b.name not in a.war_guilds:
            a.war_guilds.append(b.name)
            b.war_guilds.append(a.name)
        return True

    def make_guild_ally(self, a_name: str, b_name: str | None) -> bool:
        a, b = self.guilds.get(a_name), self.guilds.get(b_name or "")
        if a is None or b is None:
            return False
        for g, other in ((a, b.name), (b, a.name)):
            if other in g.war_guilds:
                g.war_guilds.remove(other)
            if other not in g.ally_guilds:
                g.ally_guilds.append(other)
        return True

    def _save_guild(self, g: Guild) -> None:
        """行会落盘（与 Java GuildSave 同目录，纯文本，无第三方依赖）。"""
        # 与 Java 的行会目录分开，避免双栈并行时互相覆盖。
        d = self.config.db_file.parent / "pyserver_guilds"
        try:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{g.name}.json").write_text(
                encode({"name": g.name, "master": g.master_name,
                        "members": g.members, "wars": g.war_guilds,
                        "allies": g.ally_guilds}), encoding="utf-8")
        except OSError as e:
            print(f"[guild] save failed: {e}")

    def _castle_msg(self, text: str) -> None:
        self.broadcast_all({"type": "systemMessage", "text": text})

    def declare_siege(self, guild_name: str, duration_sec: int) -> bool:
        if self.castle.war_active or guild_name not in self.guilds:
            return False
        self.castle.war_active = True
        self.castle.war_end_at = now_ms() + duration_sec * 1000
        self.castle.war_guild = guild_name
        self._castle_msg(f"行会 {guild_name} 开始攻打 {self.castle.name}！")
        return True

    def attack_castle_door(self, p, damage: int) -> int:
        c = self.castle
        if not c.war_active or p.map is None or c.map_id != p.map.name:
            return 0
        if abs(p.x - c.main_door_x) > 1 or abs(p.y - c.main_door_y) > 1 or c.door_broken():
            return 0
        c.main_door_hp = max(0, c.main_door_hp - damage)
        if c.door_broken():
            c.main_door_open = True
            self._castle_msg(f"{c.name} 的城门已被攻破！")
        return damage

    def tick_siege(self, now: int) -> None:
        c = self.castle
        if not c.war_active or now < c.war_end_at:
            return
        c.war_active = False
        if c.door_broken() and c.walls_broken():
            c.own_guild = c.war_guild
            g = self.guilds.get(c.war_guild)
            if g is not None:
                g.owns_castle = True
            self._castle_msg(f"攻城战结束，行会 {c.war_guild} 占领了 {c.name}！")
        else:
            self._castle_msg("攻城战结束，守城成功。")
        c.war_guild = ""

    # ---- 组队 -----------------------------------------------------------

    def party_of(self, player_id: int) -> Party | None:
        return self.parties.get(int(player_id))

    def create_party(self, a_id: int, b_id: int) -> Party:
        party = Party([a_id, b_id])
        self.parties[a_id] = self.parties[b_id] = party
        return party

    def join_party(self, player_id: int, leader_id: int) -> bool:
        party = self.parties.get(leader_id)
        if party is None or player_id in self.parties or player_id in party.member_ids:
            return False
        party.member_ids.append(player_id)
        self.parties[player_id] = party
        return True

    def leave_party(self, player_id: int) -> bool:
        party = self.parties.pop(player_id, None)
        if party is None:
            return False
        if player_id in party.member_ids:
            party.member_ids.remove(player_id)
        for pid in party.member_ids:
            self.parties.pop(pid, None)
        return True

    # ---- tick -----------------------------------------------------------

    def tick_extra(self, now: int) -> None:
        for gid, gi in list(self.ground_items.items()):
            if now - gi.created_at >= GROUND_ITEM_TTL_MS:
                del self.ground_items[gid]
                self._broadcast_item_remove(gi)
        self.tick_siege(now)
