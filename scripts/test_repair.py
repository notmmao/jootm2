#!/usr/bin/env python3
"""修复油 / 武器幸运 / NPC 修理专项回归（离线，零第三方依赖）。

覆盖本轮两个用户反馈：
1. 修复油(StdMode=3 Shape=9) 被误当祝福油加幸运 → 按原版 EatUseItems 分流；
2. NPC @repair / @s_repair 没有投放装备的窗口 → 补完整问价/扣钱/修复链路。

用法：D:/Python310/python.exe scripts/test_repair.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.npc_script import NpcScript  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import U_WEAPON, now_ms  # noqa: E402
import pyserver.handlers_items as handlers_items  # noqa: E402


class Session:
    def __init__(self):
        self.messages: list[dict] = []
        self.account_id = 0
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True

    def drain(self) -> list[str]:
        out = [m.get("type", "?") for m in self.messages]
        self.messages.clear()
        return out


def check(name: str, cond: bool, results: list[tuple[str, bool, str]], note: str = "") -> None:
    results.append((name, bool(cond), note))


def main() -> int:
    cfg = load()
    db_path = ROOT / "data" / "jootm2-repair-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass          # 与 test_all_messages 一致：临时库允许复用
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    name = "Repair" + str(int(time.time()) % 100000)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    w = service.world
    h = service._handlers
    results: list[tuple[str, bool, str]] = []

    def template_by_name(n: str):
        return next((t for t in w.item_templates.values() if t.name == n), None)

    def template_by_shape(shape: int, exclude: set[str] | None = None):
        return next((t for t in w.item_templates.values()
                     if t.stdMode == 3 and t.shape == shape
                     and (exclude is None or t.name not in exclude)), None)

    oil_repair = template_by_name("修复油")           # Shape=9  RepairWeapon
    oil_super = template_by_name("战神油")            # Shape=10 SuperRepairWeapon
    oil_bless = template_by_name("祝福油")            # Shape=4  WeaptonMakeLuck
    oil_buff = template_by_name("攻击力药水")          # Shape=12 临时增益（未实现）
    weapon_t = next((t for t in w.item_templates.values()
                     if t.stdMode == 5 and t.weight < 20), None)
    for label, t in (("修复油", oil_repair), ("战神油", oil_super),
                     ("祝福油", oil_bless), ("攻击力药水", oil_buff),
                     ("武器模板", weapon_t)):
        check(f"夹具存在：{label}", t is not None, results)
    if None in (oil_repair, oil_super, oil_bless, oil_buff, weapon_t):
        return report(results)
    check("修复油 Shape=9 / 战神油 Shape=10 / 祝福油 Shape=4",
          (oil_repair.shape, oil_super.shape, oil_bless.shape) == (9, 10, 4),
          results, f"{(oil_repair.shape, oil_super.shape, oil_bless.shape)}")

    def worn(dura: int = 1000, dura_max: int = 4000) -> dict:
        it = w.new_item_instance(weapon_t)
        it["durability"], it["durabilityMax"] = dura, dura_max
        return it

    def use(it: dict):
        p.last_use_item_at = 0     # 绕开 USE_ITEM_INTERVAL_MS 节流
        sess.messages.clear()
        h["useItem"](sess, {"type": "useItem", "makeIndex": it["makeIndex"]})

    # ---- 1. 修复油：修佩戴武器、掉上限、不再加幸运 ------------------------
    weapon = worn()
    p.equipment[U_WEAPON] = weapon
    p.recompute(w)
    oil = w.new_item_instance(oil_repair)
    p.bag.append(oil)
    oil_mi = int(oil["makeIndex"])
    use(oil)
    check("修复油不写 lucky（不再误当祝福油）",
          int(weapon.get("lucky", 0)) == 0, results, f"lucky={weapon.get('lucky')}")
    check("修复油补当前持久", weapon["durability"] > 1000,
          results, f"dura={weapon['durability']}")
    check("修复油按 (DuraMax-Dura) div 30 掉上限",
          weapon["durabilityMax"] == 4000 - (4000 - 1000) // 30,
          results, f"duraMax={weapon['durabilityMax']}")
    check("修复油被消耗", p.bag_item(oil_mi) is None, results)
    check("修复油下发 duraChange",
          any(m.get("type") == "duraChange" for m in sess.messages), results)
    check("修复油提示武器修复成功",
          any("武器修复成功" in str(m.get("text", "")) for m in sess.messages), results)

    # 满持久不消耗油
    weapon["durability"] = weapon["durabilityMax"]
    oil = w.new_item_instance(oil_repair)
    p.bag.append(oil)
    oil_mi = int(oil["makeIndex"])
    use(oil)
    check("武器满持久时修复油不消耗", p.bag_item(oil_mi) is not None, results)

    # ---- 2. 战神油：补满且上限不变 --------------------------------------
    weapon = worn()
    p.equipment[U_WEAPON] = weapon
    oil = w.new_item_instance(oil_super)
    p.bag.append(oil)
    use(oil)
    check("战神油补满持久", weapon["durability"] == 4000, results,
          f"dura={weapon['durability']}")
    check("战神油不掉上限", weapon["durabilityMax"] == 4000, results)

    # ---- 3. 祝福油：原版 WeaptonMakeLuck 分级概率（非自造 50/30/20）------
    # 新模型（ObjBase.pas:23621-23671）：Random(20)=1 → 诅咒-1；否则幸运<1 必成、
    # <3 按 Random(nRand+6)=1、<7 按 Random(nRand*40)=1；≥7 白花油；油总消耗。
    orig_randrange = handlers_items.random.randrange

    def no_curse_then_success(n):        # randrange(20)=2 不诅咒；ladder 不进（luck<1 直通）
        return 2
    handlers_items.random.randrange = no_curse_then_success
    try:
        weapon["lucky"] = 0
        oil = w.new_item_instance(oil_bless)
        p.bag.append(oil)
        oil_mi = int(oil["makeIndex"])
        use(oil)
    finally:
        handlers_items.random.randrange = orig_randrange
    check("祝福油幸运 0→1（lucky<1 必成分支）", int(weapon.get("lucky", 0)) == 1,
          results, f"lucky={weapon.get('lucky')}")
    check("祝福油消耗（原版失败也扣油，成功必扣）",
          p.bag_item(oil_mi) is None, results)

    handlers_items.random.randrange = lambda n: 1     # 恰中 Random(20)=1 → 诅咒
    try:
        oil = w.new_item_instance(oil_bless)
        p.bag.append(oil)
        use(oil)
    finally:
        handlers_items.random.randrange = orig_randrange
    check("诅咒分支：幸运 1→0（btValue[3] 先被扣）",
          int(weapon.get("lucky", 0)) == 0, results, f"lucky={weapon.get('lucky')}")
    # luck=1 时进 nRand+6 分支：木剑 nRand=0 → randrange(6) 返回 6 失败
    weapon["lucky"] = 1
    handlers_items.random.randrange = lambda n: 6 if n == 20 else 5
    try:
        oil = w.new_item_instance(oil_bless)
        p.bag.append(oil)
        use(oil)
    finally:
        handlers_items.random.randrange = orig_randrange
    check("lucky=1 掷失败 → 无效且油仍消耗",
          int(weapon.get("lucky", 0)) == 1 and p.bag_item(int(oil["makeIndex"])) is None,
          results)
    # ---- 3b. 幸运口径 = 模板 AC低 + 油 − 模板 MAC低（原版 3401-3402）-----
    wt = weapon_t
    wt_ac_low = wt.acMin
    p.luck = 0
    p.recompute(w)
    inst_json = p.instance_json(w, weapon)
    check("instance_json 按 AC低+油值 下发 lucky",
          inst_json.get("lucky", 0) == wt_ac_low + 1,
          results, f"lucky={inst_json.get('lucky')}")

    weapon["lucky"] = 3
    p.recompute(w)
    expect = max(-10, min(10, p.combat["luck"] + 3 - p.combat["unluck"]))
    check("recompute 幸运 = ΣAC低 + 油 − ΣMAC低诅咒", p.luck == expect,
          results, f"p.luck={p.luck} expect={expect}")

    # ---- 3c. Shape=12 增益药水：写状态槽并到期（EatItems :23383-23437）---
    weapon["lucky"] = 0
    dc_before = p.combat["dcMax"]
    hp_before = p.max_hp
    oil = w.new_item_instance(oil_buff)     # 攻击力药水 dcMin=5 macMax=180
    p.bag.append(oil)
    use(oil)
    check("增益药水写入状态槽0", p.status_arr[0] == 5, results, str(p.status_arr))
    check("增益药水即时生效 dcMax +2+值",
          p.combat["dcMax"] == dc_before + 7, results,
          f"{dc_before} -> {p.combat['dcMax']}")
    check("增益药水被消耗", p.bag_item(int(oil["makeIndex"])) is None, results)
    # HP强化水走 MaxHP 槽4
    hp_t = next(t for t in w.item_templates.values()
                if t.stdMode == 3 and t.shape == 12 and t.name == "HP强化水")
    oil = w.new_item_instance(hp_t)         # acMin=50 macMax=120
    p.bag.append(oil)
    use(oil)
    check("HP强化水抬高 max_hp", p.max_hp == hp_before + 50, results,
          f"{hp_before} -> {p.max_hp}")
    # 到期清零（Run :4194-4201）
    p.status_until[0] = now_ms() - 1
    p.status_until[4] = now_ms() - 1
    w._expire_buffs(p, now_ms())
    check("状态药水到期后槽位清零", p.status_arr[0] == 0 and p.status_arr[4] == 0,
          results, str(p.status_arr))
    check("到期后 dcMax 回落", p.combat["dcMax"] == dc_before, results,
          f"dcMax={p.combat['dcMax']} expect={dc_before}")

    # ---- 5. NPC 修理：能力、问价、普通/特殊修理、失败分支 ------------------
    npc = next(iter(w.npcs.values()))
    npc.map, npc.x, npc.y = p.map, p.x, p.y
    script = NpcScript()
    script.abilities = {"repair", "s_repair"}
    npc.script = script
    p.gold = 1000000

    it = worn()
    p.bag.append(it)
    mi = int(it["makeIndex"])
    sess.messages.clear()
    h["npcSelect"](sess, {"type": "npcSelect", "npcId": npc.id, "value": "@repair"})
    check("@repair 下发 shop=repair 让前端开投放框",
          any(m.get("type") == "npcSay" and m.get("shop") == "repair"
              for m in sess.messages), results,
          str([(m.get("type"), m.get("shop")) for m in sess.messages]))

    sess.messages.clear()
    h["queryRepairCost"](sess, {"type": "queryRepairCost", "npcId": npc.id,
                                "makeIndex": mi, "mode": "repair"})
    price_msg = next((m for m in sess.messages if m.get("type") == "sellPrice"), None)
    price = int(price_msg["price"]) if price_msg else 0
    check("问价返回正整数（显示在价格行）", price > 0, results, f"price={price}")

    gold_before = p.gold
    sess.messages.clear()
    h["repairItem"](sess, {"type": "repairItem", "npcId": npc.id,
                           "makeIndex": mi, "mode": "repair"})
    check("普通修理扣金币", p.gold == gold_before - price, results,
          f"{gold_before} -> {p.gold}")
    check("普通修理补满到新上限", it["durability"] == it["durabilityMax"], results,
          f"dura={it['durability']}/{it['durabilityMax']}")
    check("普通修理掉上限", it["durabilityMax"] < 4000, results)
    check("修理成功下发 repairResult(ok=True)",
          any(m.get("type") == "repairResult" and m.get("ok") for m in sess.messages), results)
    check("修理后下发 bagUpdate",
          any(m.get("type") == "bagUpdate" for m in sess.messages), results)

    # 特殊修理：3 倍价、上限不变
    it2 = worn()
    p.bag.append(it2)
    mi2 = int(it2["makeIndex"])
    sess.messages.clear()
    h["queryRepairCost"](sess, {"type": "queryRepairCost", "npcId": npc.id,
                                "makeIndex": mi2, "mode": "s_repair"})
    sp = next((int(m["price"]) for m in sess.messages
               if m.get("type") == "sellPrice"), 0)
    sess.messages.clear()
    h["npcSelect"](sess, {"type": "npcSelect", "npcId": npc.id, "value": "@s_repair"})
    check("@s_repair 下发 shop=s_repair",
          any(m.get("type") == "npcSay" and m.get("shop") == "s_repair"
              for m in sess.messages), results)
    gold_before = p.gold
    sess.messages.clear()
    h["repairItem"](sess, {"type": "repairItem", "npcId": npc.id,
                           "makeIndex": mi2, "mode": "s_repair"})
    check("特殊修理价格约为普通三倍", sp >= price * 2, results,
          f"普通 {price} / 特修 {sp}")
    check("特殊修理扣金币", p.gold == gold_before - sp, results)
    check("特殊修理补满且上限不变",
          it2["durability"] == 4000 and it2["durabilityMax"] == 4000, results)

    # 满持久物品不可修
    full = worn(4000, 4000)
    p.bag.append(full)
    fmi = int(full["makeIndex"])
    sess.messages.clear()
    h["queryRepairCost"](sess, {"type": "queryRepairCost", "npcId": npc.id,
                                "makeIndex": fmi, "mode": "repair"})
    check("满持久问价返回 ????",
          next((int(m["price"]) for m in sess.messages
                if m.get("type") == "sellPrice"), -9) == 0, results)
    sess.messages.clear()
    h["repairItem"](sess, {"type": "repairItem", "npcId": npc.id,
                           "makeIndex": fmi, "mode": "repair"})
    check("满持久修理失败下发 repairResult(ok=False)",
          any(m.get("type") == "repairResult" and not m.get("ok")
              for m in sess.messages), results)

    # 装备中的物品不在背包 → 提示先卸下（原版 m_ItemList 语义）
    sess.messages.clear()
    h["repairItem"](sess, {"type": "repairItem", "npcId": npc.id,
                           "makeIndex": int(weapon["makeIndex"]), "mode": "repair"})
    check("身上的装备不能直接修（提示放进背包）",
          any("放进背包" in str(m.get("text", "")) for m in sess.messages), results)

    # ---- 6. 前端协议静态校验 ---------------------------------------------
    play = (ROOT / "frontend" / "scene" / "PlayScene.mjs").read_text(encoding="utf-8")
    for token in ("queryRepairCost", "repairItem", "repairResult",
                  "shopRepairMode", "幸运+"):
        check(f"前端包含 {token}", token in play, results)

    return report(results)


def report(results: list[tuple[str, bool, str]]) -> int:
    failed = [r for r in results if not r[1]]
    for name, ok, note in results:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{note}]" if note and not ok else ""))
    print(f"\n合计 {len(results) - len(failed)} PASS / {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
