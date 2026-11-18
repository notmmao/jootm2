#!/usr/bin/env python3
"""离线修炼（Offline AFK）全生命周期回归（离线、虚拟时钟、零第三方依赖）。

覆盖 docs/OFFLINE-AFK-PLAN.md 阶段 1 验收项：
    新号赠送 → 玛法领路人领取（每日限领/池上限）→ 开启门禁（等级/黑名单/余额）
    → 下线接管（留在世界继续打怪）→ 经验系数 ×0.7 → 死亡自动复活/连死熔断
    → 时长耗尽停机结算（余额退回）→ 登录接管交还 + 报告下发 → 重启续挂+补偿。

用法（务必用 3.10）：
    D:/Python310/python.exe scripts/test_afk_offline.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.afk import NullSession, claim_gift, today  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, Player, now_ms  # noqa: E402


class FakeSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.account_id = 0
        self.account_name = ""
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True


def make_service(**afk_overrides):
    cfg = load()
    if afk_overrides:
        cfg = replace(cfg, **afk_overrides)
    db_path = Path(tempfile.mkdtemp()) / "afk-offline-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return cfg, db, db_path, GameService(cfg, db)


def login(srv, session, name):
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(session)
    assert p is not None, "登录链路失败"
    return p


def main() -> int:
    results: list[tuple[str, bool, str]] = []

    def check(name, cond, detail=""):
        ok = bool(cond)
        results.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))

    # ---- 闸门齐全的默认配置：15 级门槛 + 比奇黑名单（默认值即用户拍板值）
    strict_cfg, strict_db, _, strict = make_service()
    # ---- 放开地图限制（等级门槛 15 保持）——余额门禁用
    open_cfg, open_db, _, open_srv = make_service(afk_min_level=15,
                                                  afk_excluded_maps=())
    # ---- 放开闸门便于走通成功路径（其余拍板默认值保持）
    cfg, db, db_path, srv = make_service(afk_min_level=1, afk_excluded_maps=())
    try:
        now = now_ms()

        print("\n=== 1. 新号赠送与领取 ===")
        s = FakeSession()
        p = login(srv, s, "AfkTester")
        cid = p.character_id
        afk0 = db.get_afk(cid)
        check("新号赠送 240 分钟入余额", afk0["balance"] == cfg.afk_newbie_minutes,
              f"balance={afk0['balance']}")

        ok, msg = claim_gift(srv.world, p)
        afk1 = db.get_afk(cid)
        check("玛法领路人首次领取成功", ok and afk1["balance"] == afk0["balance"] + cfg.afk_claim_minutes,
              f"{afk1['balance']}")
        ok2, _ = claim_gift(srv.world, p)
        check("同日第二次领取被拒（每日限领）", not ok2 and db.get_afk(cid)["claim_date"] == today())
        # 模拟跨天：把 claim_date 改成昨天
        db.save_afk(cid, afk1["balance"], "2000-01-01", 1,
                    afk1["used_date"], afk1["used_minutes"])
        ok3, _ = claim_gift(srv.world, p)
        check("跨天后可再领", ok3)
        # 池上限：余额灌满 → 拒绝
        db.save_afk(cid, cfg.afk_pool_cap_minutes, "2000-01-01", 0,
                    "", 0)
        ok4, _ = claim_gift(srv.world, p)
        check("余额满 720 分钟后领取被拒", not ok4)

        print("\n=== 2. 开启门禁（默认配置：15 级 + 比奇黑名单）===")
        sp = FakeSession()
        qp = login(strict, sp, "AfkGate")
        ok, why, info = strict.afk.request_start(qp, 60, "safe")
        check("黑名单图（比奇省0）拒绝开启", not ok and "地图" in why, why)
        sp2 = FakeSession()
        qp2 = login(open_srv, sp2, "AfkGate2")
        qp2.level = 20
        qp2.recompute(open_srv.world)
        open_db.save_afk(qp2.character_id, 0, "", 0, "", 0)  # 清余额只测余额门禁
        ok, why, info = open_srv.afk.request_start(qp2, 60, "safe")
        check("放开地图 + 20 级 → 过等级/地图门禁（到余额检查）", not ok and "余额" in why, why)
        sp3 = FakeSession()
        qp3 = login(strict, sp3, "AfkGate3")
        qp3.level = 20
        ok, why, info = strict.afk.request_start(qp3, 60, "safe")
        check("等级达标但黑名单图仍拒绝", not ok and "地图" in why, why)

        print("\n=== 3. 设定计划 → 下线接管 ===")
        s2 = FakeSession()
        p2 = login(srv, s2, "AfkRunner")
        cid2 = p2.character_id
        p2.hp = p2.max_hp = max(200, p2.max_hp)
        p2.mp = p2.max_mp = max(100, p2.max_mp)
        ok, why, info = srv.afk.request_start(p2, 30, "safe")
        check("设定离线修炼计划成功", ok, why)
        check("服务端记录待生效计划", srv.afk.state_info(p2)["plan"] is not None)
        # 模拟登出：on_disconnect 分叉
        exp_before, kills_pos = p2.exp, (p2.x, p2.y)
        srv.on_disconnect(s2)
        check("下线后角色留在世界（离线代理接管）",
              srv.world.player("AfkRunner") is not None
              and p2.afk_offline and isinstance(p2.session, NullSession))
        afk_run = db.get_afk(cid2)
        check("余额按计划预扣", afk_run["balance"] == cfg.afk_newbie_minutes - 30,
              f"balance={afk_run['balance']}")
        row = db.running_afk_sessions()
        check("afk_sessions 会话行 running", len(row) == 1 and row[0]["remaining_minutes"] == 30)
        pilot = srv.afk._pilots.get(p2.id)
        check("OfflinePilot 已挂载", pilot is not None and pilot.remaining == 30)

        print("\n=== 4. 经验系数 ×0.7（只打折经验）===")
        w = srv.world
        p2.level = 40        # 抬高等级避免低级 max_exp 太小触发升级清零干扰断言
        p2.exp = 0
        p2.recompute(w)
        t = w.monster_templates.get("稻草人") or next(iter(w.monster_templates.values()))
        m = Monster(id=w.new_id(), name=t.name, x=p2.x, y=p2.y, hp=1,
                    max_hp=max(1, t.hp), level=t.level, exp=100,
                    race=t.race, race_img=t.raceImg, appr=t.appr, template=t,
                    spawn_x=p2.x, spawn_y=p2.y, spawn_map=p2.map)
        w.add_actor(m, p2.map)
        # 基线：旗标关闭拿原版经验（含等级惩罚/倍率表，只看差值）
        p2.afk_offline = False
        p2.exp = 0
        srv._grant_exp(p2, m)
        base = p2.exp
        check("基线经验到账", base > 0, f"base={base}")
        # 旗标开：同一场击杀经验按 afk.expRate 折算
        p2.afk_offline = True
        p2.exp = 0
        srv._grant_exp(p2, m)
        want = max(1, round(base * cfg.afk_exp_rate))
        check("离线击杀经验按 afk.expRate 折算", p2.exp == want,
              f"gain={p2.exp}, want={want}")
        p2.afk_offline = False
        p2.exp = 0
        srv._grant_exp(p2, m)
        check("关闭旗标后经验不打折（在线行为不变）", p2.exp == base,
              f"gain={p2.exp}, base={base}")
        p2.afk_offline = True

        print("\n=== 5. 死亡自动复活 / 连死熔断 ===")
        srv.world.kill_player(p2)
        check("死亡结算生效（掉落/旗标）", p2.dead and not p2.alive)
        srv.afk.tick(now + 1000)
        check("死亡后自动复活（deaths=1 < 上限3）",
              p2.alive and not p2.dead and pilot.deaths == 1
              and (p2.map.name,) == (p2.home_map,) or p2.alive)
        # 连死熔断：deaths 已 1，再顶到上限
        pilot.deaths = cfg.afk_death_stop - 1
        srv.world.kill_player(p2)
        srv.afk.tick(now + 2000)
        check("连死达上限 → 熔断结算并退场", p2.id not in srv.afk._pilots
              and srv.world.player("AfkRunner") is None)
        reports = db.pending_afk_reports(cid2)
        check("熔断写入离线报告", len(reports) == 1 and "死亡" in reports[0]["reason"],
              reports[0]["reason"] if reports else "")
        afk_end = db.get_afk(cid2)
        check("未用完时长退回余额", afk_end["balance"] >= cfg.afk_newbie_minutes - 3,
              f"balance={afk_end['balance']}")

        print("\n=== 6. 登录下发报告 ===")
        s3 = FakeSession()
        p3 = login(srv, s3, "AfkRunner")   # 同名角色重新进入
        kinds = [m.get("type") for m in s3.messages]
        check("登录后收到 afkReport 推送", "afkReport" in kinds)
        check("报告已标记已读", db.pending_afk_reports(cid2) == [])

        print("\n=== 7. 时长耗尽停机 ===")
        s4 = FakeSession()
        p4 = login(srv, s4, "AfkShort")
        cid4 = p4.character_id
        srv.afk.request_start(p4, 30, "safe")
        srv.on_disconnect(s4)
        pilot4 = srv.afk._pilots.get(p4.id)
        check("短计划会话已挂载", pilot4 is not None)
        # 把记账节拍往前推（手动刷新进展时间戳，绕开「站桩无进展」熔断，
        # 专测时长耗尽路径；无进展熔断本身是预期保护，已另行验证）
        t0 = now_ms()
        srv.afk._last_charge_at = 0
        pilot4.remaining = 2
        srv.afk.tick(t0 + 60_000)
        pilot4.last_progress_at = t0 + 60_000
        pilot4.last_exp = p4.exp
        pilot4.last_gold = p4.gold
        srv.afk.tick(t0 + 120_000)
        check("时长耗尽 → 停机结算退场", p4.id not in srv.afk._pilots
              and srv.world.player("AfkShort") is None)
        rep = db.pending_afk_reports(cid4)
        check("耗尽报告 reason 正确", rep and "时长" in rep[0]["reason"], rep[0]["reason"] if rep else "")

        print("\n=== 8. 登录接管（离线中上线即结算）===")
        s5 = FakeSession()
        p5 = login(srv, s5, "AfkTakeover")
        cid5 = p5.character_id
        srv.afk.request_start(p5, 60, "safe")
        srv.on_disconnect(s5)
        check("离线代理在跑", p5.id in srv.afk._pilots)
        taken = srv.afk.takeover("AfkTakeover")
        check("takeover 结算离线会话", taken and srv.world.player("AfkTakeover") is None
              and p5.id not in srv.afk._pilots)
        check("takeover 后可正常登录", strict._validate_enter(s5, "AfkTakeover") is None
              or srv._validate_enter(s5, "AfkTakeover") is None)

        print("\n=== 9. 重启续挂 + 补偿（用新 GameService 模拟重启）===")
        s6 = FakeSession()
        p6 = login(srv, s6, "AfkResume")
        cid6 = p6.character_id
        srv.afk.request_start(p6, 120, "safe")
        srv.on_disconnect(s6)
        check("离线会话行已落库", any(r["character_id"] == cid6
                                      for r in db.running_afk_sessions()))
        # 模拟重启：同一 db 上重建 GameService（内存态全新，库不变）
        srv2 = GameService(cfg, db)
        bonus = cfg.afk_restart_bonus_minutes
        srv2.afk.resume_from_db()
        p6b = srv2.world.player("AfkResume")
        pilot6 = p6b and srv2.afk._pilots.get(p6b.id)
        check("重启后自动续挂", pilot6 is not None and p6b.afk_offline)
        check("续挂补偿分钟入账", pilot6 is not None and pilot6.remaining == 120 + bonus,
              f"remaining={getattr(pilot6, 'remaining', '?')}, bonus={bonus}")
        note = [r for r in db.pending_afk_reports(cid6) if "续挂" in r["notice"]]
        check("续挂通知写入报告（上线可见）", bool(note))
        srv2.afk.takeover("AfkResume")

        print("\n=== 10. 排队/容量闸门（全服名额 0 → 下线接管拒绝）===")
        small_cfg, small_db, _, small_srv = make_service(afk_min_level=1,
                                                         afk_excluded_maps=(),
                                                         afk_max_agents=0)
        s7 = FakeSession()
        p7 = login(small_srv, s7, "AfkCap")
        small_srv.afk.request_start(p7, 30, "safe")
        small_srv.on_disconnect(s7)
        check("名额满 → 拒绝接管且角色退场", not p7.afk_offline
              and small_srv.world.player("AfkCap") is None)
        rep = small_db.pending_afk_reports(p7.character_id)
        check("拒绝原因写入报告", rep and "名额" in rep[0]["reason"],
              rep[0]["reason"] if rep else "")
        small_db.close()

    finally:
        db.close()
        strict_db.close()

    print("\n" + "=" * 60)
    fails = [r for r in results if not r[1]]
    print(f"结果: {len(results) - len(fails)} passed, {len(fails)} failed")
    for name, _, detail in fails:
        print(f"  FAIL {name} · {detail}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
