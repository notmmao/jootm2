#!/usr/bin/env python3
"""多人功能服务端离线回归（纯标准库，Python 3.10）。

覆盖本轮新增：
- 头顶气泡：普通聊天 chat 消息带说话者 id（气泡定位依据），同图附近可见。
- 密语：原版 SM_WHISPER（/玩家名 内容）——只定向发双方、目标离线有提示、
  不进世界频道、@ 仍是 GM 命令通道。
- 在线列表：onlineList 回全体在线玩家（名字/等级/职业/地图，按等级降序）。
- 查看装备：queryUserState（原版 CM_QUERYUSERSTATE=82 → SM_SENDUSERSTATE=751）
  近距校验 + 返回 13 槽装备；远距/死亡/不存在目标拒绝。

用法：
    D:/Python310/python.exe scripts/test_multiplayer_features.py
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

    def has(self, typ: str) -> bool:
        return any(m.get("type") == typ for m in self.messages)

    def of(self, typ: str) -> dict | None:
        return next((m for m in self.messages if m.get("type") == typ), None)

    def all_of(self, typ: str) -> list[dict]:
        return [m for m in self.messages if m.get("type") == typ]


def make_player(service, tag: str, job: int = 0) -> tuple[object, Session]:
    sess = Session()
    name = "MP" + tag + str(int(time.time() * 1000000) % 1000000)
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


def send(service, sess, msg: dict) -> None:
    service.on_message(sess, json.dumps(msg, ensure_ascii=False))


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-multiplayer-test.db"
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
    svc = build()
    w = svc.world
    p, s1 = make_player(svc, "A")
    q, s2 = make_player(svc, "B", job=1)
    nearby(p, q)

    print("\n[1] 普通聊天：chat 带说话者 id（气泡定位），同图附近可见")
    s1.drain(); s2.drain()
    send(svc, s1, {"type": "say", "text": "大家好"})
    c1 = s1.of("chat"); c2 = s2.of("chat")
    check("发送者收到 chat", c1 is not None)
    check("chat 带 id=说话者", c1 is not None and c1.get("id") == p.id)
    check("chat 带 name/text", c1 is not None and c1.get("name") == p.name
          and c1.get("text") == "大家好")
    check("附近广播：对方也收到", c2 is not None and c2.get("id") == p.id)
    # 原版 TCreature.Say → SendRefMsg(RM_HEAR)：远处/别图听不到
    r, s3 = make_player(svc, "C", job=2)
    r.map = next(m for m in w.maps.values() if m.name != p.map.name)
    r.x, r.y = 200, 200
    s3.drain()
    send(svc, s1, {"type": "say", "text": "远处的喊话"})
    check("远处/别图玩家收不到 chat", not s3.has("chat"))
    check("气泡所需字段齐全", c1 is not None and all(k in c1 for k in ("id", "name", "text")))

    print("\n[2] 密语：/名字 内容 只定向发双方")
    s1.drain(); s2.drain()
    send(svc, s1, {"type": "say", "text": f"/{q.name} 你好啊"})
    w1 = s1.all_of("whisper"); w2 = s2.all_of("whisper")
    check("接收方收到 whisper", len(w2) == 1)
    check("whisper incoming=True", w2 and w2[0].get("incoming") is True)
    check("whisper 内容正确", w2 and w2[0].get("text") == "你好啊"
          and w2[0].get("fromId") == p.id)
    check("发送方收到回显", len(w1) == 1 and w1[0].get("incoming") is False)
    check("密语不进世界频道", not s2.has("chat"))

    print("\n[3] 密语边界：离线目标 / 空内容 / @ 命令通道")
    s1.drain(); s2.drain()
    send(svc, s1, {"type": "say", "text": "/NoSuchPlayer999 在吗"})
    sysmsg = s1.of("systemMessage")
    check("离线目标有系统提示", sysmsg is not None and "不在线" in (sysmsg.get("text") or ""))
    check("离线目标不产生 whisper", not s1.has("whisper") and not s2.has("whisper"))
    s1.drain()
    send(svc, s1, {"type": "say", "text": "/   "})
    check("空密语给格式提示", s1.of("systemMessage") is not None)
    # 非 GM 的 @ 命令仍被拒（不误当密语）
    s1.drain()
    send(svc, s1, {"type": "say", "text": "@help"})
    check("@ 仍是 GM 命令通道", s1.of("systemMessage") is not None and not s1.has("chat"))

    print("\n[4] 在线列表：全体在线玩家 + 等级/职业/地图，等级降序")
    s1.drain()
    # 给 p 设一个高等级，验证排序
    p.level = 35
    q.level = 12
    send(svc, s1, {"type": "onlineList"})
    ol = s1.of("onlineList")
    check("收到 onlineList", ol is not None)
    players = ol.get("players", []) if ol else []
    check("含两名在线玩家", len(players) >= 2, f"实际 {len(players)} 人")
    check("字段含 name/level/job/map", all(
        all(k in e for k in ("name", "level", "job", "map")) for e in players))
    check("按等级降序", len(players) >= 2 and players[0]["level"] >= players[1]["level"])
    names = {e["name"] for e in players}
    check("含测试玩家 p/q", p.name in names and q.name in names)
    check("地图字段非空", all(e.get("map") for e in players))
    check("地图中文名(mapTitle)非空且非纯编号", all(
        e.get("mapTitle") and e["mapTitle"] != e["map"] for e in players),
        f"实际 {[e.get('mapTitle') for e in players]}")

    print("\n[5] 查看装备：近距 + 13 槽装备快照")
    # 给 q 穿一件装备（slot=1 武器）
    sample = {"makeIndex": 90001, "index": 100, "name": "木剑", "looks": 10,
              "durability": 20, "durabilityMax": 20, "stdMode": 5, "shape": 0}
    q.equipment[1] = dict(sample)
    s1.drain()
    send(svc, s1, {"type": "queryUserState", "id": q.id})
    us = s1.of("userState")
    check("近距查看收到 userState", us is not None)
    check("userState 带身份字段", us is not None and us.get("id") == q.id
          and us.get("name") == q.name)
    items = us.get("items", []) if us else []
    check("返回装备项（含初始装备）", len(items) >= 1, f"实际 {len(items)} 件")
    mine = next((it for it in items if it.get("makeIndex") == 90001), None)
    check("查看者塞入的武器在快照里", mine is not None)
    check("装备项带 slot/looks/name 字段", mine is not None
          and mine.get("slot") == 1 and "looks" in mine
          and isinstance(mine.get("looks"), int) and "name" in mine)
    check("未装备槽不下发", all(it.get("slot") is not None for it in items))

    print("\n[6] 查看装备边界：远距 / 死亡 / 不存在")
    s1.drain()
    q.x = p.x + 20  # 移到视野外
    send(svc, s1, {"type": "queryUserState", "id": q.id})
    check("远距不给装备快照", not s1.has("userState"))
    check("远距有系统提示", s1.of("systemMessage") is not None)
    nearby(p, q)
    q.alive = False
    s1.drain()
    send(svc, s1, {"type": "queryUserState", "id": q.id})
    check("死亡目标不给快照", not s1.has("userState"))
    q.alive = True
    s1.drain()
    send(svc, s1, {"type": "queryUserState", "id": 999999})
    check("不存在目标不给快照", not s1.has("userState"))

    print(f"\n===== 结果：{PASS} PASS / {FAIL} FAIL =====")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
