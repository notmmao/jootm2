#!/usr/bin/env python3
"""面对面交易 + 组队协议的服务端离线回归（纯标准库）。

对齐原版：
- 交易 ObjBase.pas:13918 ServerGetDealTry（同图/面对/距离）→ StartDeal 开窗
  → :14241 SM_DEALSUCCESS；取消 BrokeDeal/ResetDeal 退还（:13942/13967）。
- 组队 EnterGroup/LeaveGroup（:6073-6084）邀请须确认，群聊 SM_GROUPMESSAGE
  （:2325-2327），经验 GainExp（:3632-3653）。

用法：
    python scripts/test_trade_party.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


class Session:
    def __init__(self):
        self.messages: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def drain(self) -> None:
        self.messages.clear()

    def close(self) -> None:
        pass

    def has(self, typ: str) -> bool:
        return any(m.get("type") == typ for m in self.messages)

    def of(self, typ: str) -> dict | None:
        return next((m for m in self.messages if m.get("type") == typ), None)


def make_player(service, tag: str, job: int = 0) -> tuple[object, Session]:
    sess = Session()
    name = "TP" + tag + str(int(time.time() * 1000) % 1000000)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    return p, sess


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-tradeparty-test.db"
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return GameService(cfg, db)


def nearby(p, q) -> None:
    q.map = p.map
    q.x, q.y = p.x + 1, p.y


def main() -> None:
    service = build()
    w = service.world
    h = service._handlers
    p, s1 = make_player(service, "a")
    q, s2 = make_player(service, "b")
    nearby(p, q)

    print("\n[1] 组队邀请须确认，未确认不结队")
    s1.drain(); s2.drain()
    h["partyInvite"](s1, {"type": "partyInvite", "name": q.name})
    check("对方收到 partyInvite", s2.has("partyInvite"))
    check("邀请人收到系统提示", s1.has("systemMessage"))
    check("未确认前无队伍", w.party_of(p.id) is None and w.party_of(q.id) is None)

    print("\n[2] 接受后结队，双方收到 partyInfo（含血量）")
    h["partyAccept"](s2, {"type": "partyAccept"})
    party = w.party_of(p.id)
    check("双方同队", party is not None and w.party_of(q.id) is party)
    check("双方收到 partyInfo", s1.has("partyInfo") and s2.has("partyInfo"))
    info = s1.of("partyInfo")
    check("partyInfo 含 2 名成员+血量字段",
          info is not None and len(info["members"]) == 2
          and all("hp" in m and "maxHp" in m for m in info["members"]))

    print("\n[2b] 同队玩家之间 PvP 无效（原版 HAM_GROUP）")
    p.x, p.y = 10, 10
    q.x, q.y = 11, 10
    p.attack_mode = 1            # 全体攻击模式也不能伤队友
    hp_q = q.hp
    s1.drain()
    h["attack"](s1, {"type": "attack", "targetId": q.id})
    check("同队普通攻击不出伤害", q.hp == hp_q)
    check("提示不能攻击队友",
          any("队友" in m.get("text", "") for m in s1.messages))

    print("\n[3] 队伍聊天群发")
    s1.drain(); s2.drain()
    h["partyChat"](s1, {"type": "partyChat", "text": "下墓"})
    check("队友收到 partyChat", s2.has("partyChat")
          and s2.of("partyChat")["text"] == "下墓")

    print("\n[4] 拒绝邀请不结队")
    h["partyLeave"](s2, {"type": "partyLeave"})
    check("退队成功", w.party_of(p.id) is None and w.party_of(q.id) is None)
    s1.drain(); s2.drain()
    h["partyInvite"](s1, {"type": "partyInvite", "name": q.name})
    h["partyReject"](s2, {"type": "partyReject"})
    check("拒绝后无队伍", w.party_of(p.id) is None)
    check("邀请人收到拒绝提示",
          any("拒绝" in m.get("text", "") for m in s1.messages))

    print("\n[5] 交易请求 → 接受 → 双方开窗（tradeStart + tradeState）")
    s1.drain(); s2.drain()
    h["tradeRequest"](s1, {"type": "tradeRequest", "name": q.name})
    check("对方收到 tradeInvite", s2.has("tradeInvite"))
    h["tradeAccept"](s2, {"type": "tradeAccept"})
    check("交易会话建立", p.id in w.trades and q.id in w.trades)
    check("双方收到 tradeStart", s1.has("tradeStart") and s2.has("tradeStart"))
    check("双方收到 tradeState", s1.has("tradeState") and s2.has("tradeState"))

    print("\n[6] 放物品/金币 → 双方状态同步（对方视角在对侧）")
    # 给 p 塞一个物品用于交易
    it = dict(p.bag[0]) if p.bag else {"makeIndex": 99001, "index": 10,
                                       "name": "木剑", "looks": 5, "durability": 5}
    if not p.bag:
        p.bag.append(it)
    mi = p.bag[0]["makeIndex"]
    s1.drain(); s2.drain()
    h["tradeAddItem"](s1, {"type": "tradeAddItem", "makeIndex": mi})
    check("p 背包物品进入交易栏", any(i["makeIndex"] == mi for i in w.trades[p.id].a_items))
    check("双方收到 tradeState 刷新", s1.has("tradeState") and s2.has("tradeState"))
    st1, st2 = s1.of("tradeState"), s2.of("tradeState")
    check("p 视角 self 有物品", len(st1["self"]["items"]) >= 1)
    check("q 视角 other 有同一物品（对调正确）", len(st2["other"]["items"]) >= 1)

    print("\n[7] tradeSetGold 设置总额，可下调取回")
    q.gold = 5000
    h["tradeSetGold"](s2, {"type": "tradeSetGold", "amount": 1000})
    t = w.trades[p.id]
    check("q 交易栏金币=1000", t.b_gold == 1000 and q.gold == 4000,
          f"t.b_gold={t.b_gold} q.gold={q.gold}")
    h["tradeSetGold"](s2, {"type": "tradeSetGold", "amount": 400})
    check("下调后退回背包", t.b_gold == 400 and q.gold == 4600)

    print("\n[8] 取回物品 tradeRemoveItem")
    h["tradeRemoveItem"](s1, {"type": "tradeRemoveItem", "makeIndex": mi})
    check("交易栏物品清空", len(t.a_items) == 0)
    check("物品回到 p 背包", any(i["makeIndex"] == mi for i in p.bag))

    print("\n[9] 取消交易：双方收到 tradeEnd，金币/物品退还")
    h["tradeSetGold"](s2, {"type": "tradeSetGold", "amount": 0})
    s1.drain(); s2.drain()
    h["tradeCancel"](s1, {"type": "tradeCancel"})
    check("会话清理", p.id not in w.trades and q.id not in w.trades)
    check("双方收到 tradeEnd", s1.has("tradeEnd") and s2.has("tradeEnd"))
    check("金币全退", q.gold == 5000, f"q.gold={q.gold}")

    print("\n[10] 双方确认 → 成交互换")
    nearby(p, q)
    h["tradeRequest"](s1, {"type": "tradeRequest", "name": q.name})
    h["tradeAccept"](s2, {"type": "tradeAccept"})
    t = w.trades[p.id]
    p_item = p.bag[0]
    q_item = q.bag[0]
    p.gold = 1000          # 保证 p 有足够金币放入交易栏
    g1, g2 = p.gold, q.gold
    h["tradeAddItem"](s1, {"type": "tradeAddItem", "makeIndex": p_item["makeIndex"]})
    h["tradeAddItem"](s2, {"type": "tradeAddItem", "makeIndex": q_item["makeIndex"]})
    h["tradeSetGold"](s1, {"type": "tradeSetGold", "amount": 100})
    h["tradeSetGold"](s2, {"type": "tradeSetGold", "amount": 200})
    s1.drain(); s2.drain()
    h["tradeConfirm"](s1, {"type": "tradeConfirm"})
    check("单方确认不成交", p.id in w.trades)
    h["tradeConfirm"](s2, {"type": "tradeConfirm"})
    check("双方确认后成交", p.id not in w.trades)
    check("金币互换正确", p.gold == g1 - 100 + 200 and q.gold == g2 - 200 + 100,
          f"p={p.gold} q={q.gold}")
    check("物品互换", any(i["makeIndex"] == q_item["makeIndex"] for i in p.bag)
          and any(i["makeIndex"] == p_item["makeIndex"] for i in q.bag))
    check("双方收到 tradeEnd(success)",
          (s1.of("tradeEnd") or {}).get("reason") == "success"
          and (s2.of("tradeEnd") or {}).get("reason") == "success")

    print("\n[11] 距离校验：远处不能发起交易")
    far, sf = make_player(service, "c")
    far.map = w.get_or_load_map("1")
    far.x, far.y = 100, 100
    p.map = w.get_or_load_map("0"); p.x, p.y = 10, 10
    s1.drain()
    h["tradeRequest"](s1, {"type": "tradeRequest", "name": far.name})
    check("远处发起被拒", not s2.has("tradeInvite") and far.id not in w.trade_requests)

    print(f"\n结果：{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
