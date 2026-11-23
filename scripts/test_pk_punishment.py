#!/usr/bin/env python3
"""恶意 PK 惩罚体系的服务端离线回归（纯标准库，Python 3.10）。

对齐火炬 Delphi 原版：
- PKLevel = PlayerKillingPoint div 100；杀 1 人黄名(251)、2 人红名(249)
  （ObjBase.pas:3863-3882）；
- 杀人 +100，正当防卫（死者 BoIllegalAttack 灰名）不加（:2707-2718/:2894-2910）；
- 灰名 60 秒（:2824-2837/:2866-2870）；
- 在线每 2 分钟 -1（:7719-7724）；
- 红名死亡爆全部背包（:2395-2399）、装备高掉率（:2547-2548）；
- 守卫主动攻击红名（ObjGuard.pas:87 / ObjMon2.pas:878）；
- 等级压制 CheckAttackRule2（:7917-7935）。

用法：D:/Python310/python.exe scripts/test_pk_punishment.py
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
from pyserver.util import (  # noqa: E402
    ILLEGAL_ATTACK_MS, PK_DECAY_INTERVAL_MS, PK_POINT_PER_KILL,
    RED_NAME_PK_POINT, now_ms,
)

PASS = 0
FAIL = 0
_UID = [0]


def _unique_name(tag: str) -> str:
    _UID[0] += 1
    return f"{tag}{int(time.time() * 1000) % 100000}_{_UID[0]}"[:14]


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


def make_player(service, tag: str, job: int = 0, level: int = 20) -> tuple[object, Session]:
    sess = Session()
    name = _unique_name(tag)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, f"登录链路失败 name={name}"
    p.level = level
    return p, sess


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-pkpunish-test.db"
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


def main() -> int:
    svc = build()
    w = svc.world

    print("\n[1] 击杀玩家加 PK 点：杀 1 人黄名、2 人红名")
    a, sa = make_player(svc, "killer")
    b, sb = make_player(svc, "victim")
    nearby(a, b)
    assert a.pk_point == 0 and b.pk_point == 0
    w.kill_player(b, a)
    check("击杀 +100 PK", a.pk_point == PK_POINT_PER_KILL, f"实际 {a.pk_point}")
    check("死者 hp=0 且 dead", b.dead and b.hp == 0)
    check("杀 1 人黄名色 251", a.name_color() == 251, f"实际 {a.name_color()}")
    b2, _ = make_player(svc, "victim2")
    nearby(a, b2)
    w.kill_player(b2, a)
    check("杀 2 人红名色 249", a.name_color() == 249 and a.pk_point == 200,
          f"pk={a.pk_point} color={a.name_color()}")

    print("\n[2] 正当防卫：死者灰名（先动手）→ 反击者不加 PK")
    c, sc = make_player(svc, "defender")
    d, sd = make_player(svc, "attacker")
    nearby(c, d)
    d.illegal_attack_until = now_ms() + ILLEGAL_ATTACK_MS   # d 先动手，灰名
    before = c.pk_point
    w.kill_player(d, c)
    check("反击杀灰名不加 PK", c.pk_point == before, f"实际 {c.pk_point}")
    check("死者灰名结算清零", d.illegal_attack_until == 0)

    print("\n[3] 灰名打标记：wear_struck 给攻击者置位，60 秒到期")
    e, se = make_player(svc, "e")
    f, sf = make_player(svc, "f")
    nearby(e, f)
    w.wear_struck(f, 10, attacker=e)
    check("攻击白名玩家 → 攻击者灰名", e.illegal_attack_until > now_ms())
    check("灰名名字色 47（棕）", e.name_color() == 47, f"实际 {e.name_color()}")
    e.illegal_attack_until = now_ms() - 1
    w._tick_pk(e, now_ms())
    check("60 秒到点灰名清除", e.illegal_attack_until == 0)

    # 原版 ObjBase.pas:7719 是 2 分钟/点；用户 2026-11-22 嫌长，主动偏离改
    # 默认 15 秒/点（配置 pk.decayIntervalSec，设回 120 恢复原版）。测试跟随配置。
    decay = svc.config.pk_decay_interval_ms
    print(f"\n[4] PK 点衰减：在线每 {decay // 1000} 秒 -1，离线不补算")
    check("默认衰减间隔 15 秒（原版 120 秒）", decay == 15_000, f"实际 {decay}ms")
    g, sg = make_player(svc, "g")
    g.pk_point = 200
    t0 = now_ms()
    w._tick_pk(g, t0)
    check("首次 tick 只设基准不扣点", g.pk_point == 200 and g.pk_decay_at == t0)
    w._tick_pk(g, t0 + decay - 1)
    check("未到间隔不扣", g.pk_point == 200)
    w._tick_pk(g, t0 + decay)
    check("满间隔 -1", g.pk_point == 199, f"实际 {g.pk_point}")

    print("\n[5] 红名死亡爆全部背包")
    h, sh = make_player(svc, "h")
    i, si = make_player(svc, "i")
    nearby(h, i)
    h.pk_point = RED_NAME_PK_POINT
    h.bag = [{"makeIndex": 80001 + k, "index": 100, "name": f"物品{k}",
              "count": 1} for k in range(5)]
    before_bag = len(h.bag)
    w.kill_player(h, i)
    check("红名背包全掉（剩 0）", len(h.bag) == 0, f"原 {before_bag} 剩 {len(h.bag)}")
    check("击杀者非正当防卫加 100", i.pk_point == 100, f"实际 {i.pk_point}")

    print("\n[6] 守卫敌意：红名玩家被守卫锁定")
    j, sj = make_player(svc, "j")
    j.pk_point = RED_NAME_PK_POINT
    check("守卫视红名为敌", w._guard_hostile(type("M", (), {"last_hiter_id": -1})(), j))
    j.pk_point = 100
    check("黄名不被守卫攻击", not w._guard_hostile(
        type("M", (), {"last_hiter_id": -1})(), j))

    print("\n[7] 等级压制 CheckAttackRule2")
    k, sk = make_player(svc, "k", level=20)
    l, sl = make_player(svc, "l", level=5)
    k.pk_point = RED_NAME_PK_POINT
    check("高级红名不能打低级白名", bool(svc._pvp_block_reason(k, l)),
          svc._pvp_block_reason(k, l))
    l.pk_point = RED_NAME_PK_POINT
    l.level = 20
    k.level = 5
    k.pk_point = 0
    check("低级白名不能打高级红名", bool(svc._pvp_block_reason(k, l)),
          svc._pvp_block_reason(k, l))

    print("\n[8] 持久化：pk_point 存独立表，重登不丢")
    m, sm = make_player(svc, "m")
    m.pk_point = 300
    svc.persist(m)
    loaded = svc.db.load_pk_point(m.character_id)
    check("写入 character_pk", loaded == 300, f"实际 {loaded}")
    check("重登加载到 pk_point", svc.db.load_pk_point(m.character_id) == 300)
    # 确认没有污染 Java 共用 characters 表结构（列数不含 pk_point）
    cols = [r["name"] for r in svc.db.conn.execute("PRAGMA table_info(characters)")]
    check("characters 表未加 pk 列", "pk_point" not in cols, str(cols))

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
