"""把当前生效数据整理成分析用的统一结构（全部来自 pyserver 加载结果）。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from pyserver.world import NEED_EXPS
from pyserver.util import calc_get_exp  # noqa: F401  (re-export)

# 守卫/练功师/NPC 类：不参与练级分析
NON_FARM_NAMES = ("护卫", "虎卫", "鹰卫", "刀卫", "练功师", "卫士", "弓箭手")
JOBS = {0: "战士", 1: "法师", 2: "道士"}
GOLD = "金币"


@dataclass
class Spawn:
    map_id: str
    map_title: str
    x: int
    y: int
    range_: int
    count: int
    interval_s: int


@dataclass
class MonInfo:
    name: str
    level: int
    hp: int
    ac: int
    mac: int
    dc: int
    dcmax: int
    exp: int
    race: int
    hit: int
    speed: int
    attack_speed: int
    walk_speed: int
    spawns: list = field(default_factory=list)
    drops: list = field(default_factory=list)      # [(item, p, count)]
    gold_per_kill: float = 0.0
    item_value_per_kill: float = 0.0               # NPC 回收价期望
    item_price_per_kill: float = 0.0               # 物品标价期望（市场口径）

    @property
    def total_count(self) -> int:
        return sum(s.count for s in self.spawns)

    @property
    def maps(self) -> list:
        return sorted({s.map_title or s.map_id for s in self.spawns})

    def supply_per_hour(self) -> float:
        """全服理论最大击杀/小时（每只怪死后按 MonGen 间隔重生；忽略击杀用时）。"""
        return sum(s.count * 3600.0 / max(1, s.interval_s) for s in self.spawns)


class DataSet:
    def __init__(self, srv, tree_label: str):
        self.srv = srv
        self.w = w = srv.world
        self.tree = tree_label
        self.rates = w.drop_rates
        self.need_exps = list(NEED_EXPS)
        self.items = w.item_templates
        self.item_by_name = {t.name: t for t in w.item_templates.values()}
        self._build_shops()
        self._build_monsters()

    # ---- 商店 ----
    def _build_shops(self):
        from pyserver.npc_script import NpcScriptEngine
        w = self.w
        eng = NpcScriptEngine(w)
        self.shop_price: dict[str, int] = {}
        self.shop_where: dict[str, list] = defaultdict(list)
        self.merchant = None
        seen_scripts = set()
        self.merchants = []                 # 每种脚本取一个代表（价格只与脚本相关）
        for n in w.npcs.values():
            sc = getattr(n, "script", None)
            if sc is not None and n.merchant and id(sc) not in seen_scripts:
                seen_scripts.add(id(sc))
                self.merchants.append(n)
        for n in w.npcs.values():
            if getattr(n, "script", None) is None:
                continue
            try:
                goods = eng.goods(n)
            except Exception:
                goods = []
            if goods and n.merchant and self.merchant is None:
                self.merchant = n
            for g in goods:
                nm = g["name"]
                pr = int(g["price"])
                if nm not in self.shop_price or pr < self.shop_price[nm]:
                    self.shop_price[nm] = pr
                title = w.map_titles.get(n.map_name, n.map_name)
                if f"{title}·{n.name}" not in self.shop_where[nm]:
                    self.shop_where[nm].append(f"{title}·{n.name}")

    def sell_value(self, item_name: str, dura_ratio: float = 0.6) -> int:
        """全服最高 NPC 回收价（真实 _sell_price；掉落耐久 20~100% 取期望 0.6）。"""
        key = (item_name, dura_ratio)
        cache = self.__dict__.setdefault("_sell_cache", {})
        if key in cache:
            return cache[key]
        t = self.item_by_name.get(item_name)
        best = 0
        if t is not None:
            it = {"durability": max(1, int(t.duraMax * dura_ratio)), "durabilityMax": t.duraMax}
            for n in self.merchants:
                try:
                    best = max(best, self.srv._sell_price(n, t, it))
                except Exception:
                    pass
        cache[key] = best
        return best

    # ---- 怪物 ----
    def _build_monsters(self):
        w = self.w
        spawns = defaultdict(list)
        for e in w.mon_gen_entries:
            if e.monster_name not in w.monster_templates:
                continue
            title = w.map_titles.get(e.map_id, e.map_id)
            interval = e.interval_min * 60 if e.interval_min > 0 else 15
            spawns[e.monster_name].append(Spawn(e.map_id, title, e.x, e.y, e.range_,
                                                e.count, interval))
        self.monsters: dict[str, MonInfo] = {}
        for name, t in w.monster_templates.items():
            m = MonInfo(name=name, level=t.level, hp=t.hp, ac=t.ac, mac=t.mac, dc=t.dc,
                        dcmax=max(t.dc, t.maxDc), exp=t.exp, race=t.race,
                        hit=getattr(t, "hit", 0), speed=getattr(t, "speed", 0),
                        attack_speed=getattr(t, "attackSpeed", 0),
                        walk_speed=getattr(t, "walkSpeed", 0))
            m.spawns = spawns.get(name, [])
            for e in w.drop_tables.get(name, []):
                p = min(1.0, self.rates.probability(name, e.item_name) * (e.sel_point + 1) / max(1, e.max_point))
                m.drops.append((e.item_name, p, e.count))
                if e.item_name == GOLD:
                    m.gold_per_kill += p * e.count * self.rates.amount(name)
                else:
                    m.item_value_per_kill += p * self.sell_value(e.item_name)
                    it = self.item_by_name.get(e.item_name)
                    if it is not None:
                        m.item_price_per_kill += p * it.price
            self.monsters[name] = m

    def farmable(self) -> list:
        out = []
        for m in self.monsters.values():
            if not m.spawns or m.exp <= 0 or m.hp <= 0:
                continue
            if any(k in m.name for k in NON_FARM_NAMES):
                continue
            out.append(m)
        return out

    def drop_sources(self, item_name: str) -> list:
        """[(怪物名, 每次击杀概率)]"""
        out = []
        for m in self.monsters.values():
            if not m.spawns:
                continue
            for nm, p, _c in m.drops:
                if nm == item_name and p > 0:
                    out.append((m.name, p))
        return out

    def cum_exp(self) -> list:
        tot, out = 0, []
        for v in self.need_exps:
            tot += v
            out.append(tot)
        return out
