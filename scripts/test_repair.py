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
from pyserver.util import U_WEAPON  # noqa: E402
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

    # ---- 3. 祝福油：只由 Shape=4 触发，且幸运下发到物品说明 ----------------
    orig_random = handlers_items.random.random
    handlers_items.random.random = lambda: 0.1     # 固定走 +1 分支
    try:
        oil = w.new_item_instance(oil_bless)
        p.bag.append(oil)
        use(oil)
    finally:
        handlers_items.random.random = orig_random
    check("祝福油 +1 幸运", int(weapon.get("lucky", 0)) == 1, results,
          f"lucky={weapon.get('lucky')}")
    p.recompute(w)
    check("recompute 把实例幸运计入 p.luck", p.luck >= 1, results, f"p.luck={p.luck}")
    inst_json = p.instance_json(w, weapon)
    check("instance_json 下发 lucky 供 tooltip 显示",
          inst_json.get("lucky", 0) >= 1, results, f"lucky={inst_json.get('lucky')}")

    # ---- 4. Shape=12 增益药水不再被误当祝福油 -----------------------------
    before = int(weapon.get("lucky", 0))
    oil = w.new_item_instance(oil_buff)
    p.bag.append(oil)
    oil_mi = int(oil["makeIndex"])
    use(oil)
    check("Shape=12 增益药水不改幸运", int(weapon.get("lucky", 0)) == before, results)
    check("未实现的增益药水不消耗", p.bag_item(oil_mi) is not None, results)

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
