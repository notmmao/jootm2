"""我本沉默私服内容过滤：加载期剥离，原始数据文件零改动。

配置见 ``config/content_policy.json``：
- items/monsters/maps/npcs：glob 清单，命中的模板/刷怪/地图/NPC 不加载；
- high_drop：按掉落表结构识别"高爆变体"，把它的表换回同显示名的官服基线变体表。

判据不用 itemval 排序：赤月恶魔/祖玛教主等官服 BOSS 的无后缀表本身 itemval
就极高（7 万+），按数值排序会误伤。改用「装备类且概率≥1/10 的条目数」，
私服高爆表普遍 15~37 条，官服基线变体 0~5 条；再用 keep_boss 白名单兜底。
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

POLICY_FILE = "content_policy.json"


def _show_name(name: str) -> str:
    m = re.search(r"[0-9-]", name)
    return name[:m.start()] if m else name


@dataclass(frozen=True)
class HighDropRule:
    enabled: bool = True
    min_high_prob_items: int = 8
    min_prob: float = 0.1
    keep_boss: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ContentPolicy:
    enabled: bool
    items: tuple[str, ...]
    monsters: tuple[str, ...]
    maps: tuple[str, ...]
    npcs: tuple[str, ...]
    high_drop: HighDropRule

    @classmethod
    def load(cls, root: Path) -> "ContentPolicy":
        path = Path(root) / "config" / POLICY_FILE
        if not path.is_file():
            return cls(False, (), (), (), (), HighDropRule(False))
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls(False, (), (), (), (), HighDropRule(False))
        h = d.get("high_drop") or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            items=tuple(d.get("items") or ()),
            monsters=tuple(d.get("monsters") or ()),
            maps=tuple(d.get("maps") or ()),
            npcs=tuple(d.get("npcs") or ()),
            high_drop=HighDropRule(
                enabled=bool(h.get("enabled", True)),
                min_high_prob_items=int(h.get("min_high_prob_items", 8)),
                min_prob=float(h.get("min_prob", 0.1)),
                keep_boss=tuple(h.get("keep_boss") or ()),
            ),
        )

    @staticmethod
    def _match(name: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in patterns)

    def drop_item(self, name: str) -> bool:
        return self.enabled and self._match(name, self.items)

    def drop_monster(self, name: str) -> bool:
        return self.enabled and self._match(name, self.monsters)

    def drop_map(self, map_id: str, title: str = "") -> bool:
        if not self.enabled:
            return False
        return self._match(str(map_id), self.maps) or bool(title and self._match(title, self.maps))

    def drop_npc(self, name: str, script_name: str = "") -> bool:
        if not self.enabled:
            return False
        return self._match(name, self.npcs) or bool(script_name and self._match(script_name, self.npcs))

    def summary(self) -> str:
        if not self.enabled:
            return "内容策略：关闭（原始全集）"
        return (f"内容策略：开启 items={len(self.items)} monsters={len(self.monsters)} "
                f"maps={len(self.maps)} npcs={len(self.npcs)} "
                f"high_drop={'开' if self.high_drop.enabled else '关'}")


def filter_csv_rows(policy: ContentPolicy, kind: str, rows: list[list[str]]) -> list[list[str]]:
    """在 db._import_csv 落库前过滤。kind: item / monster / magic。"""
    if not policy.enabled or not rows:
        return rows
    if kind == "item":
        return [r for r in rows if len(r) > 1 and not policy.drop_item(r[1].strip())]
    if kind == "monster":
        return [r for r in rows if r and not policy.drop_monster(r[0].strip())]
    return rows


def _high_prob_count(entries, std_mode: dict[str, int], min_prob: float) -> int:
    """掉落表里「装备类(stdMode!=0) 且概率≥min_prob」的条目数。"""
    n = 0
    for e in entries:
        if std_mode.get(e.item_name, 0) == 0:
            continue
        if (e.sel_point + 1) / max(1, e.max_point) >= min_prob:
            n += 1
    return n


def filter_drop_tables(policy: ContentPolicy, tables: dict, std_mode: dict[str, int]) -> tuple[dict, list[str]]:
    """识别高爆变体表，替换为同显示名官服基线变体的表。

    返回 (新表, 被处理的怪名列表)。官服 BOSS 白名单内的怪一律不动。
    """
    rule = policy.high_drop
    if not policy.enabled or not rule.enabled:
        return tables, []
    keep = set(rule.keep_boss)
    heat = {name: _high_prob_count(ents, std_mode, rule.min_prob) for name, ents in tables.items()}
    groups: dict[str, list[str]] = {}
    for name in tables:
        groups.setdefault(_show_name(name), []).append(name)

    changed: list[str] = []
    out = dict(tables)
    for base, names in groups.items():
        if base in keep:
            continue
        # 基线 = 高概率装备条目最少的变体（官服表通常 0~5 条）
        baseline = min(names, key=lambda n: (heat[n], n))
        if heat[baseline] >= rule.min_high_prob_items:
            continue  # 整组都是高爆，没有可信基线，保持原样
        for name in names:
            if name == baseline or heat[name] < rule.min_high_prob_items:
                continue
            out[name] = list(tables[baseline])
            changed.append(name)
    return out, changed


def filter_world(policy: ContentPolicy, world) -> dict:
    """在 GameWorld.__init__ 末尾（init_extra 之后）调用，过滤内存对象。

    返回统计 dict，便于日志与回归测试断言。
    """
    stat = dict(items=0, monsters=0, maps=0, mon_gen=0, npcs=0, high_drop=[])
    if not policy.enabled:
        stat["policy"] = "off"
        return stat

    # 1) 模板表（item_templates 按 index 索引，名字在 template.name）
    drop_idx = [i for i, t in world.item_templates.items() if policy.drop_item(t.name)]
    for i in drop_idx:
        world.item_templates.pop(i, None)
    stat["items"] = len(drop_idx)

    drop_mon = [n for n in world.monster_templates if policy.drop_monster(n)]
    for n in drop_mon:
        world.monster_templates.pop(n, None)
    stat["monsters"] = len(drop_mon)

    # 2) 地图：标题/标志/可用清单/门/刷怪
    drop_maps = {k for k in world.available_maps if policy.drop_map(k)}
    for k in list(world.map_titles):
        if policy.drop_map(k, str(world.map_titles[k])):
            drop_maps.add(k)
            world.map_titles.pop(k, None)
            world.map_flags.pop(k, None)
    for k in drop_maps:
        world.available_maps.discard(k)
    stat["maps"] = len(drop_maps)

    for src, gates in list(world.gates_by_src.items()):
        kept = [g for g in gates if g.src_map not in drop_maps and g.dst_map not in drop_maps]
        if kept:
            world.gates_by_src[src] = kept
        else:
            world.gates_by_src.pop(src, None)

    before = len(world.mon_gen_entries)
    world.mon_gen_entries = [e for e in world.mon_gen_entries
                             if e.map_id not in drop_maps and e.monster_name in world.monster_templates]
    stat["mon_gen"] = before - len(world.mon_gen_entries)

    # 3) NPC
    drop_npc_ids = [i for i, n in world.npcs.items()
                    if policy.drop_npc(n.name, n.script_name)]
    for i in drop_npc_ids:
        npc = world.npcs.pop(i, None)
        if npc and npc.map_name in world.npcs_by_map:
            world.npcs_by_map[npc.map_name] = [x for x in world.npcs_by_map[npc.map_name] if x.id != i]
    stat["npcs"] = len(drop_npc_ids)

    # 4) 高爆掉落表（清表已剥离怪的条目 + 变体回退）
    std_mode = {t.name: t.stdMode for t in world.item_templates.values()}
    tables = {n: e for n, e in world.drop_tables.items()
              if n in world.monster_templates and n not in drop_mon}
    world.drop_tables, changed = filter_drop_tables(policy, tables, std_mode)
    stat["high_drop"] = changed
    stat["policy"] = "on"
    return stat
