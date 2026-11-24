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
from pyserver.util import EQUIP_SLOTS, U_DRESS, U_WEAPON  # noqa: E402

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
             repair_enabled=1, unbind_enabled=1, equipment=None,
             off_calls=None, take_on_calls=None, bag_size=46,
             repair_price_ok=True):
    srv = SimpleNamespace(config=SimpleNamespace(afk_supply_enabled=1,
                                                 afk_supply_repair=repair_enabled,
                                                 afk_supply_unbind=unbind_enabled))
    srv._repair_allowed = staticmethod(lambda npc, mode: repairable)
    # 修理价：≥0 才能修（原版 GetItemPrice 为 0 → -1「这个物品无法修理」）。
    # afk 的候选筛选会调它，桩里必须给，否则 AttributeError。
    srv._repair_price = lambda npc, t, it, mode: 10 if repair_price_ok else -1

    def _repair(session, msg):
        if repair_calls is not None:
            repair_calls.append(msg)
        if not repair_price_ok:
            return
        # 真实 handler 会把 durability 补满（普通修理连 DuraMax 一起降），
        # 这里只补 durability，够校验「生效判定」即可。
        mi = int(msg.get("makeIndex", 0))
        found = next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None)
        if found is not None:
            found["durability"] = int(found.get("durabilityMax", 100) or 100)

    srv._repair_item = _repair
    srv._use_item = lambda session, msg: (use_calls.append(msg) if use_calls is not None else None)
    # 穿脱装备桩：真实 handler 需要世界/背包联动，这里只记录调用并模拟挪动
    eq = list(equipment) if equipment is not None else [None] * EQUIP_SLOTS

    def _take_off(session, msg):
        if off_calls is not None:
            off_calls.append(msg)
        slot = int(msg.get("slot", -1))
        if 0 <= slot < len(eq) and eq[slot] is not None:
            if len(bag) < bag_size:
                bag.append(eq[slot])
                eq[slot] = None

    def _take_on(session, msg):
        if take_on_calls is not None:
            take_on_calls.append(msg)
        slot = int(msg.get("slot", -1))
        mi = int(msg.get("makeIndex", 0))
        it = next((x for x in bag if int(x.get("makeIndex", 0)) == mi), None)
        if it is not None and 0 <= slot < len(eq) and eq[slot] is None:
            bag.remove(it)
            eq[slot] = it

    srv._take_off_item = _take_off
    srv._take_on_item = _take_on

    tmpls = {
        100: SimpleNamespace(stdMode=5, durabilityMax=100),   # 装备（武器）
        200: SimpleNamespace(stdMode=31, shape=9),            # 卷包
        300: SimpleNamespace(stdMode=0),                      # 药水
    }
    npc = SimpleNamespace(id=55, merchant=True, map=None, x=10, y=10, name="铁匠",
                          script=None)
    p = SimpleNamespace(bag=bag, equipment=eq, x=10, y=10, job=0,
                        map=SimpleNamespace(name="0"), session=object(),
                        bag_item=lambda mi: next(
                            (x for x in bag if int(x.get("makeIndex", 0)) == mi),
                            None))
    w = SimpleNamespace(
        item_templates=tmpls,
        npcs_by_map={"0": [npc]},
        unbind_item_name=lambda shape: "金创药(小量)" if shape == 9 else None,
        item_template_by_name=lambda name: tmpls[300] if name == "金创药(小量)" else None,
        # 本地补给判定会遍历货架找药水：给一条「金创药(小量) +50」的货源
        goods=lambda n: [{"index": 300, "price": 80}],
    )
    tmpls[300] = SimpleNamespace(stdMode=0, acMin=50, macMin=0, price=80, name="金创药")
    tmpls[400] = SimpleNamespace(stdMode=0, acMin=50, macMin=0, price=80, name="金创药")
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
# 语义已变（2026-11-24）：候选筛选现在绑定「修理商是谁」，找不到商人时
# 候选直接为空 → 返回 False，交给买药阶段收尾。关键是不卡死、不发无效请求；
# 「记入 supply_repaired」改由补给收尾的 gear_retry_until 长冷却来做
# （否则同图无修理商时会每 10 秒重触发一次补给）。
check("无修理商人时不卡死（返回 False，进入买药）", acted is False)
check("未调用修理", len(calls) == 0)

print("\n[6] 药品不在修理范围")
calls = []
pilot = make_pilot()
srv, w, p = make_env([item(3, 300, durability=10, std_mode=0)], repair_calls=calls)
acted = pilot._supply_maintain(srv, w, p, now=10000)
check("药品不触发修理", acted is False and len(calls) == 0)

# ---------------- 身上装备：脱下 → 修理 → 穿回（用户 2026-11-24）---------
print("\n[7] 身上武器持久 30% → 脱下、修理、穿回（原版只认背包物品）")
calls, offs, tons = [], [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(11, 100, durability=30, dmax=100)
srv, w, p = make_env([], equipment=eq, repair_calls=calls,
                     off_calls=offs, take_on_calls=tons)
acted = pilot._supply_maintain_equipped(srv, w, p, now=10000)
check("本 tick 已处理（返回 True）", acted is True)
check("发出了卸下请求", len(offs) == 1 and offs[0]["slot"] == U_WEAPON, str(offs))
check("槽位已空、装备进了背包",
      p.equipment[U_WEAPON] is None and len(p.bag) == 1)
check("进入「待修理」阶段", pilot.maint_stage == "on", pilot.maint_stage)
# 下一 tick 修理并穿回
srv, w, p = srv, w, p
pilot.supply_last_act_at = 0
pilot._supply_maintain_equipped(srv, w, p, now=11000)
check("调用了修理", len(calls) == 1, str(calls))
check("修理参数用 makeIndex 定位", calls and calls[0]["makeIndex"] == 11)
check("修完自动穿回", tons and tons[0]["slot"] == U_WEAPON, str(tons))
check("装备回到身上、背包清空",
      p.equipment[U_WEAPON] is not None and len(p.bag) == 0)
check("流程状态已复位", pilot.maint_stage == "" and pilot.maint_slot == -1)

print("\n[8] 耐久充足（80%）→ 不折腾")
calls, offs = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(12, 100, durability=80, dmax=100)
srv, w, p = make_env([], equipment=eq, repair_calls=calls, off_calls=offs)
acted = pilot._supply_maintain_equipped(srv, w, p, now=10000)
check("不触发（返回 False）", acted is False)
check("没有卸下请求", len(offs) == 0)

print("\n[9] 背包已满 → 卸不下来，绝不丢装备")
calls, offs = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(13, 100, durability=10, dmax=100)
bag = [item(600 + i, 100, 100, 100) for i in range(46)]
srv, w, p = make_env(bag, equipment=eq, repair_calls=calls, off_calls=offs)
acted = pilot._supply_maintain_equipped(srv, w, p, now=10000)
check("背包满时不卸下（返回 False）", acted is False)
check("没有卸下请求", len(offs) == 0)
check("装备仍在身上", p.equipment[U_WEAPON] is not None)

print("\n[10] 补给中断（失败/结束）→ 兜底把装备穿回去")
calls, tons = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(14, 100, durability=10, dmax=100)
srv, w, p = make_env([], equipment=eq, repair_calls=calls, take_on_calls=tons)
# 模拟「已脱下、尚未穿回」的中断现场
p.bag.append(p.equipment[U_WEAPON])
p.equipment[U_WEAPON] = None
pilot.maint_slot, pilot.maint_mi, pilot.maint_stage = U_WEAPON, 14, "on"
pilot._maint_reclaim(srv, w, p)
check("中断时发出穿回请求", tons and tons[0]["makeIndex"] == 14, str(tons))
check("装备回到身上", p.equipment[U_WEAPON] is not None)
check("状态已复位", pilot.maint_stage == "")

print("\n[11] 装备已被卖掉（不在背包）→ 放弃，不穿错")
calls, tons = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
srv, w, p = make_env([], equipment=eq, repair_calls=calls, take_on_calls=tons)
pilot.maint_slot, pilot.maint_mi, pilot.maint_stage = U_WEAPON, 15, "on"
pilot._maint_reclaim(srv, w, p)
check("不发起穿回", len(tons) == 0)
check("状态已复位", pilot.maint_stage == "")

print("\n[12] 修理关闭 → 不碰身上装备")
calls, offs = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(16, 100, durability=10, dmax=100)
srv, w, p = make_env([], equipment=eq, repair_calls=calls,
                     off_calls=offs, repair_enabled=0)
acted = pilot._supply_maintain_equipped(srv, w, p, now=10000)
check("关闭时不卸下", acted is False and len(offs) == 0)

print("\n[13] 衣服（U_DRESS）同样纳入维护")
calls, offs = [], []
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_DRESS] = item(17, 100, durability=20, dmax=100)
srv, w, p = make_env([], equipment=eq, repair_calls=calls, off_calls=offs)
acted = pilot._supply_maintain_equipped(srv, w, p, now=10000)
check("衣服低持久也会卸下", acted is True and offs and offs[0]["slot"] == U_DRESS)

print("\n[14] 装备耐久过低触发补给（25% 门槛）")
pilot = make_pilot()
eq = [None] * EQUIP_SLOTS
eq[U_WEAPON] = item(18, 100, durability=100, dmax=100)
srv, w, p = make_env([], equipment=eq)
check("满持久不触发", pilot._gear_needs_supply(w, p) is False)
adj = p.equipment   # make_env 里 equipment 是浅拷贝，改 p.equipment 才生效
adj[U_WEAPON] = item(19, 100, durability=30, dmax=100)
check("30%（高于进城门槛 25%）不触发", pilot._gear_needs_supply(w, p) is False)
adj[U_WEAPON] = item(20, 100, durability=20, dmax=100)
check("20%（低于 25%）触发专门补给", pilot._gear_needs_supply(w, p) is True)
pilot.supply_repaired.add(20)
check("已修过的 makeIndex 不再触发", pilot._gear_needs_supply(w, p) is False)

# ---------------- 补给地点判定（2026-11-24 四阶段实测的一组真 bug）---------
print("\n[15] 本地补给判定：只看「有没有空位买药」，不被卖不掉的杂物否决")
# 背景：背包里只有一根卖价 10 的蜡烛（stdMode 30）、本图药商不收它，
# 旧判据因此认为「不能本地补给」→ 跨 11 段门去比奇省 → 门不可达 → 停机。
# 真实需求只是买药，背包 14/46 有空位，就地买即可。
calls, offs = [], []
pilot = make_pilot()
bag = [item(30, 300, durability=0, dmax=0, std_mode=30)]      # 蜡烛
for i in range(3):
    bag.append(item(400 + i, 400, durability=0, dmax=0, std_mode=0))  # 药水
eq = [None] * EQUIP_SLOTS
srv, w, p = make_env(bag, equipment=eq, repair_calls=calls, off_calls=offs)
pilot.anchor_map = "T"
check("有空位 + 药商可买 → 可就地补给", pilot._can_supply_locally(srv, w, p) is True)

print("\n[16] 商人走不到（祖玛阁式孤立房间）→ 不算本地可补给")
# 祖玛阁 D5063 十二个房间互不相通，药品小贩在另一个房间：
# 「图里有药商」不等于「买得到药」，必须过连通性校验。
pilot._npc_reachable = staticmethod(lambda w, p, npc: False)
check("药商走不到 → 判定不可就地补给",
      pilot._can_supply_locally(srv, w, p) is False)
pilot._npc_reachable = staticmethod(lambda w, p, npc: True)
check("药商可达 → 恢复可就地补给",
      pilot._can_supply_locally(srv, w, p) is True)

print("\n[17] 背包已满时仍需有商人肯收（腾不出格子就不能买）")
pilot = make_pilot()
bag = [item(700 + i, 300, durability=0, dmax=0, std_mode=30) for i in range(46)]
srv, w, p = make_env(bag, equipment=[None] * EQUIP_SLOTS, bag_size=46)
check("满包且无商人收购 → 不可就地补给",
      pilot._can_supply_locally(srv, w, p) is False)

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
