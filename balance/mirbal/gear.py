"""按「职业 × 等级 × 装备档」配装：用真实 Player.recompute 评分，贪心逐槽选择。

装备档：
- ``none``  ：只穿新手默认（布衣 + 木剑，即建号自带）
- ``shop``  ：商店能买到的、穿得上的最好装备
- ``best``  ：商店 ∪ 可掉落（MonGen 有刷的怪会掉）里穿得上的最好装备
"""
from __future__ import annotations

from pyserver.util import (U_ARMRINGL, U_ARMRINGR, U_BUJUK, U_DRESS, U_HELMET,
                           U_NECKLACE, U_RINGL, U_RINGR, U_WEAPON, slot_accepts)

TIERS = ("none", "shop", "best")
TIER_NAMES = {"none": "新手装", "shop": "商店装", "best": "本级最强"}
SLOTS = (U_WEAPON, U_DRESS, U_HELMET, U_NECKLACE, U_ARMRINGL, U_ARMRINGR, U_RINGL, U_RINGR)


def score(p, job: int) -> float:
    c = p.combat

    def avg(a, b):
        return (c.get(a, 0) + c.get(b, 0)) / 2.0

    ac, mac = avg("acMin", "acMax"), avg("macMin", "macMax")
    if job == 0:
        att = avg("dcMin", "dcMax") * 3 + c.get("dcMax", 0) * 0.5
        dfn = ac * 1.6 + mac * 0.6
    elif job == 1:
        att = avg("mcMin", "mcMax") * 3 + c.get("mcMax", 0) * 0.5
        dfn = ac * 0.9 + mac * 0.7
    else:
        att = avg("scMin", "scMax") * 3 + c.get("scMax", 0) * 0.5 + avg("dcMin", "dcMax") * 0.3
        dfn = ac * 1.2 + mac * 0.7
    return att + dfn + c.get("hit", 0) * (1.0 if job == 0 else 0.3) + c.get("speed", 0) * 0.4 \
        + c.get("luck", 0) * (1.5 if job == 0 else 0.3) + p.max_hp * 0.02


def _can_wear(p, t, slot, w) -> bool:
    if t.stdMode == 10 and p.sex != 0:
        return False
    if t.stdMode == 11 and p.sex != 1:
        return False
    if slot == U_WEAPON:
        if t.weight > p.max_hand_weight():
            return False
    elif t.weight + p.wear_weight_ex(w, slot) > p.max_wear_weight():
        return False
    need_val = {0: p.level, 1: p.combat.get("dcMax", 0), 2: p.combat.get("mcMax", 0),
                3: p.combat.get("scMax", 0)}
    if t.need not in need_val:
        return False
    return need_val[t.need] >= t.needLevel


class GearPicker:
    def __init__(self, ds):
        self.ds = ds
        w = ds.w
        droppable = set()
        for m in ds.monsters.values():
            if m.spawns:
                droppable.update(nm for nm, p, _ in m.drops if p > 0)
        self.shop = {n for n in ds.shop_price if n in ds.item_by_name}
        self.sources = {"shop": self.shop, "best": self.shop | droppable}
        self.cands = {}
        for tier, names in self.sources.items():
            lst = []
            for n in names:
                t = ds.item_by_name.get(n)
                if t is None:
                    continue
                for slot in SLOTS:
                    if slot_accepts(slot, t.stdMode):
                        lst.append((slot, t))
            self.cands[tier] = lst

    def equip(self, p, tier: str) -> dict:
        """就地给 p 配装（会覆盖身上装备），返回 {槽: 物品名}。"""
        w = self.ds.w
        if tier == "none":                    # 建号自带：木剑 + 布衣
            for s in range(len(p.equipment)):
                if s != U_BUJUK:
                    p.equipment[s] = None
            for nm, slot in (("木剑", U_WEAPON), ("布衣(男)" if p.sex == 0 else "布衣(女)", U_DRESS)):
                t = self.ds.item_by_name.get(nm)
                if t is not None:
                    p.equipment[slot] = w.new_item_instance(t)
            p.recompute(w)
            return {s: w.item_templates[int(e["index"])].name
                    for s, e in enumerate(p.equipment) if e and s != U_BUJUK}
        for s in range(len(p.equipment)):
            if s != U_BUJUK:
                p.equipment[s] = None
        p.recompute(w)
        by_slot = {}
        for slot, t in self.cands[tier]:
            by_slot.setdefault(slot, []).append(t)
        # 两轮：首轮把 Need=DC/MC/SC 依赖的装备也有机会穿上
        for _round in range(2):
            for slot in SLOTS:
                cur = p.equipment[slot]
                best_t, best_s = None, score(p, p.job)
                for t in by_slot.get(slot, ()):
                    if not _can_wear(p, t, slot, w):
                        continue
                    p.equipment[slot] = w.new_item_instance(t)
                    p.recompute(w)
                    s = score(p, p.job)
                    if s > best_s + 1e-9:
                        best_t, best_s = t, s
                    p.equipment[slot] = cur
                    p.recompute(w)
                if best_t is not None:
                    p.equipment[slot] = w.new_item_instance(best_t)
                    p.recompute(w)
        return {s: w.item_templates[int(e["index"])].name for s, e in enumerate(p.equipment)
                if e and s != U_BUJUK}
