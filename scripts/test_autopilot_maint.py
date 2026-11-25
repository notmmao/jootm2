#!/usr/bin/env python3
"""在线托管「一键维护」（卖肉 + 修理身上装备）单测（Python 3.10）。

不依赖真实 GameService：直接驱动 MaintenanceFlow.tick，桩掉 srv/w/p 的
穿脱/买卖/修理调用，验证状态机推进顺序与安全约束（脱→修→穿、中断穿回、
本图无修理商跳过、无肉无低耐久不动作）。

    D:/Python310/python.exe scripts/test_autopilot_maint.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.autopilot import (MAINT_REPAIR, MAINT_SELL, MaintenanceFlow,  # noqa: E402
                                npc_really_repairs)
from pyserver.util import (EQUIP_SLOTS, U_ARMRINGL, U_DRESS, U_NECKLACE,  # noqa: E402
                           U_RINGL, U_WEAPON)

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


def item(mi, index, durability=100, dmax=100, std_mode=5):
    return {"makeIndex": mi, "index": index, "durability": durability,
            "durabilityMax": dmax, "stdMode": std_mode, "shape": 0}


class _Script:
    """带 [@repair] 段的 NPC 脚本桩。"""

    def __init__(self, abilities=None, sections=()):
        self.abilities = abilities or set()
        self._sections = set(sections)

    def section(self, name):
        return [name] if name in self._sections else None


def make_env(meat=(), equipment=None, repair_npc=True, repair_price_ok=True,
             sell_price=20, bag_size=46):
    """返回 (flow-driver, srv, w, p, calls)。

    meat: [(mi, index)] 背包里的肉（stdMode 40）。
    equipment: dict slot -> item（身上装备）。
    repair_npc: 本图是否有可修理商（script 带 abilities={'repair'}）。
    """
    calls = {"sell": [], "repair": [], "off": [], "on": []}
    eq = [None] * EQUIP_SLOTS
    if equipment:
        for slot, it in equipment.items():
            eq[slot] = it
    bag = [item(mi, idx, std_mode=40) for (mi, idx) in meat]
    tmpls = {}
    for idx in (500,):                        # 肉模板
        tmpls[idx] = SimpleNamespace(stdMode=40, durabilityMax=0, price=sell_price)
    for slot, it in (equipment or {}).items():
        # 装备用独立 index（避免覆盖肉模板 500 的 stdMode）
        eidx = it["index"]
        tmpls[eidx] = SimpleNamespace(stdMode=5 if slot == U_WEAPON else
                                      (10 if slot == U_DRESS else
                                       (19 if slot == U_NECKLACE else
                                        (22 if slot == U_RINGL else 24))),
                                      durabilityMax=it["durabilityMax"],
                                      price=1000)

    p_map = SimpleNamespace(name="0", height=100)
    npc = SimpleNamespace(id=55, merchant=True, map=p_map, x=8, y=8, name="铁匠",
                          script=_Script(abilities={"repair"}) if repair_npc else None)
    if not repair_npc:
        npc.script = None

    p = SimpleNamespace(
        bag=bag, equipment=eq, x=8, y=8, job=0, id=1, name="T",
        map=p_map, session=object(),
        bag_item=lambda mi: next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None),
    )

    def _sell_item(session, msg):
        calls["sell"].append(msg)
        mi = int(msg.get("makeIndex", 0))
        found = next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None)
        if found is not None:
            bag.remove(found)

    def _repair(session, msg):
        calls["repair"].append(msg)
        if not repair_price_ok:
            return
        mi = int(msg.get("makeIndex", 0))
        found = next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None)
        if found is not None:
            found["durability"] = int(found.get("durabilityMax", 100) or 100)

    def _take_off(session, msg):
        calls["off"].append(msg)
        slot = int(msg.get("slot", -1))
        if 0 <= slot < len(eq) and eq[slot] is not None and len(bag) < bag_size:
            bag.append(eq[slot])
            eq[slot] = None

    def _take_on(session, msg):
        calls["on"].append(msg)
        slot = int(msg.get("slot", -1))
        mi = int(msg.get("makeIndex", 0))
        it = next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None)
        if it is not None and 0 <= slot < len(eq) and eq[slot] is None:
            bag.remove(it)
            eq[slot] = it

    srv = SimpleNamespace(
        _sell_item=_sell_item,
        _repair_item=_repair,
        _repair_price=lambda npc_, t, it, mode: 10 if repair_price_ok else -1,
        _sell_price=lambda npc_, t, it=None: sell_price,
        _take_off_item=_take_off,
        _take_on_item=_take_on,
        world=SimpleNamespace(
            send_system=lambda *a, **k: None,
            npcs_by_map={"0": [npc]},
            npc=lambda nid: npc if nid == npc.id else None,
        ),
    )
    w = srv.world
    w.item_templates = tmpls
    pilot = SimpleNamespace(
        maint=None, acted=False, last_progress_at=0,
        path=[], path_key=(), plan=None, target_id=0, loot_id=0,
        set_status=lambda *a, **k: None,
        _step_to=lambda *a, **k: True,
        _survive=lambda *a, **k: None,
    )
    return pilot, srv, w, p, calls


def run_until(pilot, srv, w, p, now_start=1000, steps=60):
    """推进维护状态机直到 pilot.maint 变 None（收尾）或步数用尽。"""
    t = now_start
    flow = pilot.maint
    for _ in range(steps):
        if flow is None:
            break
        flow.tick(pilot, srv, w, p, t)
        t += 700
        flow = pilot.maint
    return t


print("[1] 有肉+有低耐久装备：卖肉 → 修装备 → 收尾")
pilot, srv, w, p, calls = make_env(
    meat=[(1, 500), (2, 500)],
    equipment={U_WEAPON: item(10, 600, durability=20, dmax=100),
               U_DRESS: item(11, 600, durability=30, dmax=100)})
pilot.maint = MaintenanceFlow(started_at=1000)
run_until(pilot, srv, w, p)
check("卖掉 2 块肉", len(calls["sell"]) == 2, f"{len(calls['sell'])}")
check("2 件装备修理", len(calls["repair"]) == 2, f"{len(calls['repair'])}")
# 每件都经历 脱下→修理→穿回：off/on 各 2 次
check("脱下 2 次", len(calls["off"]) == 2, f"{len(calls['off'])}")
check("穿回 2 次", len(calls["on"]) == 2, f"{len(calls['on'])}")
check("维护结束 maint=None", pilot.maint is None)
check("武器耐久已补满", p.equipment[U_WEAPON]["durability"] == 100,
      str(p.equipment[U_WEAPON]))

print("\n[2] 脱下→修理→穿回顺序：一件不会并行（r_off 独占）")
pilot, srv, w, p, calls = make_env(
    equipment={U_WEAPON: item(10, 600, durability=10, dmax=100)})
flow = MaintenanceFlow(started_at=1000)
pilot.maint = flow
# 手动逐拍观察：应先 off，之后 repair，再 on，且 off 后 r_off=True
flow.tick(pilot, srv, w, p, 1000)   # 贴近了（距离<=12）→ 走 repair_step → off
check("第一步已脱下（r_off）", flow.r_off is True, f"off={calls['off']}")
check("脱下后背包有该件", p.bag_item(10) is not None)
flow.tick(pilot, srv, w, p, 2000)   # 修 + 穿回
check("修完并穿回", flow.repaired == 1 and p.equipment[U_WEAPON] is not None,
      f"repaired={flow.repaired} eq={p.equipment[U_WEAPON] is not None}")
check("穿回后 r_off 复位", flow.r_off is False)

print("\n[3] 本图无修理商：跳过该件，不脱下（避免裸奔）")
pilot, srv, w, p, calls = make_env(
    equipment={U_WEAPON: item(10, 500, durability=10, dmax=100)},
    repair_npc=False)
flow = MaintenanceFlow(started_at=1000)
pilot.maint = flow
run_until(pilot, srv, w, p, steps=40)
check("从未脱下装备", len(calls["off"]) == 0, f"{len(calls['off'])}")
check("修理计数为 0", flow.repaired == 0 if pilot.maint else calls["repair"].__len__() == 0)
check("件被记入跳过黑名单", flow.skipped >= 1 or 10 in flow.failed_mis,
      f"skipped={flow.skipped} failed={flow.failed_mis}")

print("\n[4] 修不动（修理价 -1）：不重复死磕，记 failed 后收尾")
pilot, srv, w, p, calls = make_env(
    equipment={U_WEAPON: item(10, 500, durability=10, dmax=100)},
    repair_price_ok=False)
flow = MaintenanceFlow(started_at=1000)
pilot.maint = flow
run_until(pilot, srv, w, p, steps=40)
# repair_price_ok=False → _repair_npc 里 _repair_price 返回 -1 → 无候选 → 跳过
check("修理商判定为不可修（未脱下）", len(calls["off"]) == 0, f"{len(calls['off'])}")
check("收尾后 maint=None", pilot.maint is None)

print("\n[5] 维护中途 abort：脱下待修的装备必须穿回")
pilot, srv, w, p, calls = make_env(
    equipment={U_WEAPON: item(10, 600, durability=10, dmax=100)})
flow = MaintenanceFlow(started_at=1000)
pilot.maint = flow
flow.tick(pilot, srv, w, p, 1000)   # 脱下，r_off=True，装备进背包
check("已脱下（r_off=True）", flow.r_off is True)
check("装备暂在背包", p.bag_item(10) is not None and p.equipment[U_WEAPON] is None)
flow.abort(pilot, srv, w, p)         # 中断
check("abort 后穿回身上", p.equipment[U_WEAPON] is not None and flow.r_off is False,
      f"eq={p.equipment[U_WEAPON] is not None}")

print("\n[6] npc_really_repairs：只有脚本声明 repair 的商人才算数")
check("有 @repair 能力 → True",
      npc_really_repairs(SimpleNamespace(script=_Script(abilities={"repair"}))))
check("有 [@repair] 段 → True",
      npc_really_repairs(SimpleNamespace(script=_Script(sections={"repair"}))))
check("无脚本 → False",
      not npc_really_repairs(SimpleNamespace(script=None)))
check("脚本无修理能力/段 → False",
      not npc_really_repairs(SimpleNamespace(script=_Script(abilities={"buy"}))))

print("\n[7] 只有肉（无低耐久装备）：卖完肉即收尾，不碰装备")
pilot, srv, w, p, calls = make_env(meat=[(1, 500)])
flow = MaintenanceFlow(started_at=1000)
pilot.maint = flow
run_until(pilot, srv, w, p, steps=20)
check("卖了 1 块肉", len(calls["sell"]) == 1)
check("没有脱下动作", len(calls["off"]) == 0)
check("收尾 maint=None", pilot.maint is None)

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
