#!/usr/bin/env python3
"""周末双倍活动回归（纯标准库 + 轻量集成，务必用 3.10）。

覆盖：
    时间窗口（周六/周日全天、起止小时、跨午夜、force、enabled）→
    经验倍率 → 掉落概率倍率（含 100% 封顶、与 game_rates 相乘）→
    实际 kill_exp / drop_loot / roll_butch_loot 注入 boost →
    登录提示 + 状态翻转全服广播。

用法：
    D:/Python310/python.exe scripts/test_weekend_boost.py
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver import weekend  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.drop_rates import DropRates  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster  # noqa: E402

PASS = FAIL = 0


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

    def system_texts(self) -> list[str]:
        return [m.get("text", "") for m in self.messages
                if m.get("type") == "systemMessage"]

    def close(self) -> None:
        pass


def main() -> int:
    base = load()
    print("\n=== 1. 时间窗口判定（2026-11-19 周四）===")
    check("周六 00:00 生效", weekend.is_active(base, datetime(2026, 11, 21, 0, 0)))
    check("周日 23:59 生效", weekend.is_active(base, datetime(2026, 11, 22, 23, 59)))
    check("周一 00:00 不生效", not weekend.is_active(base, datetime(2026, 11, 23, 0, 0)))
    check("周五 20:00 不生效", not weekend.is_active(base, datetime(2026, 11, 20, 20, 0)))
    forced = replace(base, weekend_force=1)
    check("force=1 忽略星期", weekend.is_active(forced, datetime(2026, 11, 23, 10)))
    disabled = replace(base, weekend_enabled=0, weekend_force=1)
    check("enabled=0 优先级高于 force", not weekend.is_active(disabled))

    mon_only = replace(base, weekend_days=(1,))
    check("days 可配（仅周一）", weekend.is_active(mon_only, datetime(2026, 11, 23, 10))
          and not weekend.is_active(mon_only, datetime(2026, 11, 21, 10)))
    evening = replace(base, weekend_days=(6,), weekend_start_hour=20, weekend_end_hour=24)
    check("起止小时下界（20:00 生效 / 19:59 不生效）",
          weekend.is_active(evening, datetime(2026, 11, 21, 20, 0))
          and not weekend.is_active(evening, datetime(2026, 11, 21, 19, 59)))
    check("起止小时上界（23:59 生效 / 次日 0 点不生效）",
          weekend.is_active(evening, datetime(2026, 11, 21, 23, 59))
          and not weekend.is_active(evening, datetime(2026, 11, 22, 0, 0)))
    overnight = replace(base, weekend_days=(6, 7), weekend_start_hour=22, weekend_end_hour=2)
    check("跨午夜窗口（周六 23 点 / 周日 1 点生效，周六中午不生效）",
          weekend.is_active(overnight, datetime(2026, 11, 21, 23, 0))
          and weekend.is_active(overnight, datetime(2026, 11, 22, 1, 0))
          and not weekend.is_active(overnight, datetime(2026, 11, 21, 12, 0)))

    print("\n=== 2. 倍率与概率封顶 ===")
    sat, mon = datetime(2026, 11, 21, 10), datetime(2026, 11, 23, 10)
    check("活动期经验/爆率均 2.0",
          weekend.exp_rate(base, sat) == 2.0 and weekend.drop_rate(base, sat) == 2.0)
    check("非活动期均回落 1.0",
          weekend.exp_rate(base, mon) == 1.0 and weekend.drop_rate(base, mon) == 1.0)
    x3 = replace(base, weekend_exp_rate=3.0, weekend_drop_rate=0.0)
    check("倍率可配（经验 3.0 / 爆率 0 生效）",
          weekend.exp_rate(x3, sat) == 3.0 and weekend.drop_rate(x3, sat) == 0.0)
    check("与 game_rates 全局倍率相乘",
          DropRates(exp=0.5).exp_for("鹿", 2.0) == 1.0)
    check("按怪物 exp 覆盖后仍乘活动倍率",
          DropRates(monsters={"鹿": {"exp": 3.0}}).exp_for("鹿", 2.0) == 6.0)
    orig_random = random.random
    try:
        random.random = lambda: 0.4
        check("概率翻倍：1/4 原本不中、boost=2 命中",
              not DropRates().hit("鹿", "肉", 1, 4, 1.0)
              and DropRates().hit("鹿", "肉", 1, 4, 2.0))
        random.random = lambda: 0.99
        check("概率封顶 100%：2 倍 1/1 仍必中", DropRates().hit("鹿", "肉", 1, 1, 2.0))
        random.random = lambda: 0.0
        check("爆率 0 时任何随机数都不掉",
              not DropRates().hit("鹿", "肉", 1, 1, 0.0))
    finally:
        random.random = orig_random

    print("\n=== 3. 配置读取 ===")
    tmp = Path(tempfile.mkdtemp()) / "server.properties"
    tmp.write_text("weekend.days=1,3\nweekend.expRate=3.5\nweekend.dropRate=4\n"
                   "weekend.startHour=20.5\nweekend.endHour=23\nweekend.force=1\n",
                   encoding="utf-8")
    cfg2 = load(tmp)
    check("server.properties 覆盖生效",
          cfg2.weekend_days == (1, 3) and cfg2.weekend_exp_rate == 3.5
          and cfg2.weekend_drop_rate == 4.0 and cfg2.weekend_start_hour == 20.5
          and cfg2.weekend_end_hour == 23.0 and cfg2.weekend_force == 1,
          f"days={cfg2.weekend_days} exp={cfg2.weekend_exp_rate}")
    bad = load(tmp.parent / "missing.properties")
    check("缺省值周六日 2 倍", bad.weekend_days == (6, 7)
          and bad.weekend_exp_rate == 2.0 and bad.weekend_drop_rate == 2.0)

    print("\n=== 4. 登录提示与状态广播 ===")
    check("活动期登录提示含倍率", "×2" in weekend.login_text(base, sat))
    check("非活动期登录提示为预告", "周六" in weekend.login_text(base, mon))
    check("总开关关闭时不提示", weekend.login_text(disabled) == "")

    cfg = replace(base, weekend_force=1)
    db = Database(Path(tempfile.mkdtemp()) / "weekend-test.db")
    try:
        db.seed_game_data(cfg.mud2_dir)
        srv = GameService(cfg, db)
        sess = Session()
        name = "Wk" + str(random.randint(1000, 9999))
        for msg in ({"type": "register", "username": name, "password": "test12345"},
                    {"type": "login", "username": name, "password": "test12345"},
                    {"type": "createCharacter", "name": name, "sex": 0,
                     "hair": 1, "job": 0},
                    {"type": "enterGame", "name": name},
                    {"type": "noticeOk", "name": name}):
            srv.on_message(sess, json.dumps(msg, ensure_ascii=False))
        p = srv.find_by_session(sess)
        check("登录链路成功", p is not None)
        if p is None:
            return 1
        w = srv.world
        texts = sess.system_texts()
        check("进游戏收到周末活动提示",
              any("[周末活动]" in t and "×2" in t for t in texts), f"{texts[-3:]}")

        print("\n=== 5. 实际结算注入 boost ===")
        check("kill_exp 结算翻倍（100→200）", w.kill_exp("鹿", 100) == 200,
              f"got={w.kill_exp('鹿', 100)}")
        check("零经验不被倍率放大", w.kill_exp("鹿", 0) == 0)

        # 用 spy 记录 drop_loot / roll_butch_loot 传给 hit 的 boost，避免随机干扰。
        mon_name = next(k for k, v in w.drop_tables.items() if v)
        tmpl_base = next(iter(w.monster_templates.values()))
        tmpl = replace(tmpl_base, name=mon_name)
        m = Monster(id=w.new_id(), name=mon_name, x=p.x + 1, y=p.y,
                    hp=100, max_hp=100, level=1, exp=100, race=80,
                    race_img=tmpl.raceImg, appr=tmpl.appr, template=tmpl,
                    spawn_x=p.x + 1, spawn_y=p.y, spawn_map=p.map)
        m.map = p.map
        seen: list[float] = []
        orig_hit = DropRates.hit
        def spy_hit(self, monster, item, num, den, boost=1.0):
            seen.append(boost)
            return False
        DropRates.hit = spy_hit
        try:
            w.drop_loot(m, p)
            check("drop_loot 传入周末爆率倍率 2.0",
                  bool(seen) and all(b == 2.0 for b in seen), f"boosts={seen}")
            seen.clear()
            w.roll_butch_loot(m, p)
            check("挖肉产出同样享受双倍爆率",
                  bool(seen) and all(b == 2.0 for b in seen), f"boosts={seen}")
        finally:
            DropRates.hit = orig_hit

        print("\n=== 6. 状态翻转广播 ===")
        sess.drain()
        srv.weekend_active = False        # 当前 force=1 实为开启 → 人为制造翻转
        srv.tick()
        texts = sess.system_texts()
        check("开启瞬间全服广播一次", weekend.START_MSG in texts, f"{texts[-3:]}")
        sess.drain()
        srv.tick()
        check("状态未变不重复广播",
              not any("周末活动" in t for t in sess.system_texts()))
    finally:
        db.close()

    print("\n" + "=" * 60)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
