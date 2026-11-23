#!/usr/bin/env python3
"""离线挂机装备维护（修理 + 药水解捆）单测（Python 3.10）。
    D:/Python310/python.exe scripts/test_afk_maintain.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.afk import OfflinePilot  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    if ok:
        P += 1
    else:
        F += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


class TestPilot(OfflinePilot):
    """非 slots 子类，带 __dict__ 可桩掉方法。"""
    pass


def make_pilot():
    pilot = TestPilot(manager=None, player_id=1)
    pilot._step_to = lambda *a, **k: True
    pilot.set_status = lambda *a, **k: None
    pilot.supply_last_act_at = 0
    return pilot


def item(mi, index, durability=100, dmax=100, std_mode=5):
    return {"makeIndex": mi, "index": index, "durability": durability,
            "durabilityMax": dmax, "stdMode": std_mode, "shape": 0}


def make_env(bag, repairable=True, repair_calls=None, use_calls=None,
             repair_enabled=1, unbind_enabled=1):
    srv = SimpleNamespace(config=SimpleNamespace(afk_supply_enabled=1,
                                                 afk_supply_repair=repair_enabled,
                                                 afk_supply_unbind=unbind_enabled))
    srv._repair_allowed = staticmethod(lambda npc, mode: repairable)
    srv._repair_item = lambda session, msg: (repair_calls.append(msg) if repair_calls is not None else None)
    srv._use_item = lambda session, msg: (use_calls.append(msg) if use_calls is not None else None)

    tmpls = {
        100: SimpleNamespace(stdMode=5, durabilityMax=100),   # 装备（武器）
        200: SimpleNamespace(stdMode=31, shape=9),            # 卷包
        300: SimpleNamespace(stdMode=0),                      # 药水
    }
    npc = SimpleNamespace(id=55, merchant=True, map=None, x=10, y=10, name="铁匠")
    p = SimpleNamespace(bag=bag, x=10, y=10, map=SimpleNamespace(name="0"),
                        session=object())
    w = SimpleNamespace(
        item_templates=tmpls,
        npcs_by_map={"0": [npc]},
        unbind_item_name=lambda shape: "金创药(小量)" if shape == 9 else None,
        item_template_by_name=lambda name: tmpls[300] if name == "金创药(小量)" else None,
    )
    npc.map = p.map
    return srv, w, p


print("[1] 低持久装备触发修理")
calls = []
pilot = make_pilot()
srv, w, p = make_env([item(1, 100, durability=30, dmax=100)], repair_calls=calls)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("返回 True（本 tick 处理）", acted is True)
check("调用了修理", len(calls) == 1, str(calls))
check("修理参数 npcId/makeIndex 正确", calls and calls[0]["npcId"] == 55
      and calls[0]["makeIndex"] == 1, str(calls))
check("记录已修 makeIndex", 1 in pilot.supply_repaired)

print("\n[2] 高持久装备不修")
calls = []
pilot = make_pilot()
srv, w, p = make_env([item(1, 100, durability=90, dmax=100)], repair_calls=calls)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("无需维护返回 False", acted is False)
check("未调用修理", len(calls) == 0)

print("\n[3] 药水捆触发解捆")
uses = []
pilot = make_pilot()
srv, w, p = make_env([item(2, 200)], use_calls=uses)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("返回 True", acted is True)
check("调用了 useItem 解捆", len(uses) == 1, str(uses))
check("解捆参数 makeIndex 正确", uses and uses[0]["makeIndex"] == 2, str(uses))
check("记录已拆 makeIndex", 2 in pilot.supply_unbound)

print("\n[4] 开关关闭")
for key, label in (("repair_enabled", "修理关闭"), ("unbind_enabled", "解捆关闭")):
    calls, uses = [], []
    pilot = make_pilot()
    kw = {key: 0}
    srv, w, p = make_env([item(1, 100, durability=20), item(2, 200)],
                         repair_calls=calls, use_calls=uses, **kw)
    acted = pilot._supply_maintain(srv, w, p, now=10000)
    if key == "repair_enabled":
        check("修理关闭时不修", len(calls) == 0, str(calls))
        check("修理关闭仍可解捆", len(uses) == 1, str(uses))
    else:
        check("解捆关闭时不拆", len(uses) == 0, str(uses))
        check("解捆关闭仍可修", len(calls) == 1, str(calls))

print("\n[5] 没有修理商人时跳过该装备")
calls = []
pilot = make_pilot()
srv, w, p = make_env([item(1, 100, durability=20)], repairable=False, repair_calls=calls)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("无修理商人时标记跳过（不卡死）", 1 in pilot.supply_repaired)
check("未调用修理", len(calls) == 0)

print("\n[6] 药品不在修理范围")
calls = []
pilot = make_pilot()
srv, w, p = make_env([item(3, 300, durability=10, std_mode=0)], repair_calls=calls)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("药品不触发修理", acted is False and len(calls) == 0)

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
