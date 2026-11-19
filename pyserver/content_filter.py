"""我本沉默私服内容过滤：加载期剥离，原始数据文件零改动。

配置见 ``config/content_policy.json``：
- items/monsters/maps/npcs：glob 清单，命中的模板/刷怪/地图/NPC 不加载（不再刷新）；
- high_drop.reset：显式黑名单 {高爆怪名: 官服基线怪名}，命中者掉落表换成基线表
  （怪照常刷新，只是不再掉私服高价值装备）。

两类"剔除"语义不同，别混：
- items/monsters/maps/npcs = 不刷新（模板/刷怪项从内存删除）；
- high_drop.reset = 照常刷新，只换掉落表（清掉高爆产出）。

不再使用旧的启发式算法（同显示名变体对比 + 装备概率条目数阈值 + keep_boss
白名单）：那种做法会随数据变动误伤官服 BOSS，也不直观。改成名单驱动后可预期。
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
    # 显式黑名单：高爆怪名 -> 官服基线怪名（掉落表整体换成基线表）
    reset: dict[str, str] = field(default_factory=dict)


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
                reset={str(k): str(v) for k, v in (h.get("reset") or {}).items()},
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


def filter_drop_tables(policy: ContentPolicy, tables: dict,
                       std_mode: dict[str, int] | None = None) -> tuple[dict, list[str]]:
    """按显式黑名单把高爆怪的掉落表换成官服基线表（怪仍刷新）。

    std_mode 保留为可选参数只为兼容旧调用，不再参与判定。
    返回 (新表, 被处理的怪名列表)；名单外的怪一律不动。
    """
    rule = policy.high_drop
    if not policy.enabled or not rule.enabled or not rule.reset:
        return tables, []
    out = dict(tables)
    changed: list[str] = []
    for name, baseline in rule.reset.items():
        if name not in tables:
            continue  # 该怪已被 monsters 清单剥离，不在掉落表里
        if baseline not in tables:
            continue  # 基线怪不存在（数据变动），保守跳过，不硬改
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

    # 4) 高爆掉落表（黑名单驱动：先剔除已剥离怪，再按 reset 名单换回官服基线表）
    tables = {n: e for n, e in world.drop_tables.items()
              if n in world.monster_templates and n not in drop_mon}
    world.drop_tables, changed = filter_drop_tables(policy, tables)
    stat["high_drop"] = changed
    stat["policy"] = "on"
    return stat
