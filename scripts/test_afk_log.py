#!/usr/bin/env python3
"""离线修炼日志（pyserver/afk_log.py）回归。

覆盖三件事：
1. AfkSessionLog 单元行为：文件名/追加/JSON 可解析/非法字符清理/关闭；
2. 拒绝原因落日志：request_start 与 promote 的各类拒绝都写 reject 事件，
   且 reason 文案带具体数据（用户 2026-11-24「拒绝原因没写好」）；
3. 会话全生命周期：promote → 采样 → 死亡 → 结算，jsonl 里能还原事件序列。

务必用 3.10 跑：
    D:/Python310/python.exe scripts/test_afk_log.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.afk_log import AfkSessionLog, _safe_name  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}" + (f" · {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}" + (f" · {detail}" if detail else ""))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------- 1. 单元
def case_unit() -> None:
    print("\n=== 1. AfkSessionLog 单元行为 ===")
    tmp = Path(tempfile.mkdtemp()) / "logs"
    log = AfkSessionLog(tmp, "测试/角色", character_id=7)
    check("打开成功", log.ok and log.path is not None, str(log.path))
    check("目录自动创建", tmp.is_dir())
    check("文件名清理非法字符", "/" not in log.path.name, log.path.name)
    check("文件名带角色名", "测试_角色" in log.path.name, log.path.name)
    log.event("session_start", 1000, level=20)
    log.sample(1060, kills=3, dKills=3, hpPct=88)
    log.reject(2000, "promote", "等级不足：当前 10 级，离线修炼要求 15 级",
               map="3", level=10, plan={"minutes": 60})
    log.close()
    check("关闭后 ok=False", not log.ok)
    recs = read_jsonl(log.path)
    check("事件行数 = 3", len(recs) == 3, f"{len(recs)}")
    check("每行都有 t/kind", all("t" in r and "kind" in r for r in recs))
    check("游戏时间 ms 有记录", recs[0].get("ms") == 1000)
    check("kind 顺序正确", [r["kind"] for r in recs]
          == ["session_start", "sample", "reject"])
    check("reject 带 stage/reason", recs[2]["stage"] == "promote"
          and "等级不足" in recs[2]["reason"])

    # 同名再开一次 → 追加，不覆盖（同一天多轮修炼要能分段对比）
    log2 = AfkSessionLog(tmp, "测试/角色", character_id=7)
    log2.event("session_end", 3000, reason="正常结束")
    log2.close()
    recs2 = read_jsonl(log2.path)
    check("同日同角色追加而非覆盖", len(recs2) == 4, f"{len(recs2)}")
    check("追加内容是新事件", recs2[-1]["kind"] == "session_end")

    # 关闭开关 → 不落盘
    off = AfkSessionLog(tmp, "关闭的角色", enabled=False)
    off.event("sample", 1, x=1)
    off.close()
    check("enabled=False 不落盘", off.path is None and not off.ok)

    check("_safe_name 清空通配符", _safe_name("a*b?c:d") == "a_b_c_d",
          _safe_name("a*b?c:d"))
    check("_safe_name 空名兜底", _safe_name("") == "unknown")


# --------------------------------------------------- 2. 服务端链路
class ClientSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.closed = False

    def send_json(self, msg) -> None:
        self.messages.append(msg)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    async def close(self) -> None:
        self.closed = True


def make_service(**overrides):
    cfg = load()
    overrides.setdefault("afk_log_dir", Path(tempfile.mkdtemp()) / "logs")
    cfg = replace(cfg, **overrides)
    db_path = Path(tempfile.mkdtemp()) / "afk-log-test.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    return cfg, db, db_path, GameService(cfg, db)


def login(srv, session, name, job=0):
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(session)
    assert p is not None, "登录链路失败"
    return p


def case_reject_text() -> None:
    print("\n=== 2. 拒绝原因文案（要带具体数据）===")
    cfg, db, _dbp, srv = make_service(afk_min_level=15, afk_excluded_maps=(),
                                      afk_max_agents=10, afk_per_map_limit=2)
    sess = ClientSession()
    p = login(srv, sess, "LogReject")
    mgr = srv.afk

    # 等级不足：必须写出当前等级与要求等级
    p.level = 10
    ok, why, _info = mgr.request_start(p, 60, "balanced")
    check("低等级被拒", not ok, why)
    check("等级拒绝带当前/要求等级", "10" in why and "15" in why, why)

    # 余额不足：必须写出剩余分钟
    p.level = 20
    db.save_afk(p.character_id, 5, "", 0, "", 0)
    ok, why, _info = mgr.request_start(p, 60, "balanced")
    check("余额不足被拒", not ok, why)
    check("余额拒绝带剩余分钟", "5" in why, why)

    # 拒绝同时落日志：stage=request_start，reason 与返回文案一致
    log_dir = cfg.afk_log_dir
    files = list(Path(log_dir).glob("*_LogReject.jsonl"))
    check("拒绝生成了日志文件", bool(files), str(files[:1]))
    if files:
        recs = read_jsonl(files[0])
        rejects = [r for r in recs if r["kind"] == "reject"]
        check("reject 事件 ≥2 条", len(rejects) >= 2, f"{len(rejects)}")
        check("stage 标为 request_start",
              all(r["stage"] == "request_start" for r in rejects))
        check("reason 含具体数值",
              any("15" in r["reason"] for r in rejects),
              rejects[-1]["reason"])

    # 下线接管被拒（地图黑名单）：报告 + 日志都要带地图名
    cfg2, db2, _dbp2, srv2 = make_service(afk_min_level=1,
                                          afk_excluded_maps=("0",))
    s2 = ClientSession()
    p2 = login(srv2, s2, "LogPromote", job=0)
    w = srv2.world
    p2.map = w.get_or_load_map("0") or p2.map
    p2.afk_plan = {"minutes": 60, "preset": "balanced"}
    ok2 = srv2.afk.promote(p2)
    check("黑名单地图下线接管被拒", not ok2)
    reps = db2.pending_afk_reports(p2.character_id)
    check("拒绝写入报告", bool(reps))
    if reps:
        reason = reps[0]["reason"]
        check("报告拒绝原因带地图名", "0" in reason, reason)
        check("报告说明不扣时长",
              "不扣" in (reps[0]["notice"] or ""), reps[0]["notice"] or "")
    files2 = list(Path(cfg2.afk_log_dir).glob("*_LogPromote.jsonl"))
    check("promote 拒绝也落日志", bool(files2))
    if files2:
        recs = read_jsonl(files2[0])
        rej = [r for r in recs if r["kind"] == "reject"]
        check("stage 标为 promote", bool(rej) and rej[-1]["stage"] == "promote",
              rej[-1]["stage"] if rej else "")


def case_lifecycle() -> None:
    print("\n=== 3. 会话全生命周期日志 ===")
    cfg, db, _dbp, srv = make_service(afk_min_level=1, afk_excluded_maps=(),
                                      afk_max_agents=10, afk_per_map_limit=5,
                                      afk_daily_minutes=600,
                                      afk_pool_cap_minutes=600)
    sess = ClientSession()
    p = login(srv, sess, "LogLife")
    w = srv.world
    p.level = 22
    p.recompute(w)
    p.gold = 20000
    db.save_afk(p.character_id, 600, "", 0, "", 0)
    p.afk_plan = {"minutes": 120, "preset": "balanced"}
    ok = srv.afk.promote(p)
    check("下线接管成功", ok)
    if not ok:
        return
    pilot = srv.afk._pilots.get(p.id)
    check("pilot 已建立", pilot is not None)
    check("日志已打开", pilot is not None and pilot.log is not None
          and pilot.log.ok)

    # 跑几分钟真实 tick（虚拟时间靠真实 now_ms，这里直接推分钟节拍）
    import pyserver.afk as afk_mod
    now = afk_mod.now_ms()
    for i in range(1, 4):
        srv.afk._last_charge_at = now + i * 60_000 - 60_000
        srv.afk.tick(now + i * 60_000)
    path = pilot.log.path if pilot.log else None
    check("采样已写入", path is not None and path.exists())

    # 结算
    srv.afk.disable(p, "测试结算")
    check("结算后日志关闭", pilot.log is None)
    recs = read_jsonl(path)
    kinds = [r["kind"] for r in recs]
    check("有 session_start", "session_start" in kinds)
    check("有 sample ≥1", kinds.count("sample") >= 1, f"{kinds.count('sample')}")
    check("有 session_end", "session_end" in kinds)
    check("session_end 是最后一条", kinds[-1] == "session_end")
    start = next(r for r in recs if r["kind"] == "session_start")
    check("start 带 plan/preset/level",
          start.get("plan") == 120 and "preset" in start
          and start.get("level") == 22, str(list(start)))
    end = recs[-1]
    check("end 带 consumed/reason",
          "consumed" in end and end.get("reason") == "测试结算", str(end.get("reason")))
    check("end 带经验/击杀/金币汇总",
          {"expGained", "kills", "goldDelta", "deaths"} <= set(end), str(list(end)))
    # 采样字段：区间增量与瞬时状态齐备
    samp = next(r for r in recs if r["kind"] == "sample")
    need = {"level", "exp", "dExp", "kills", "dKills", "gold", "dGold",
            "hp", "maxHp", "hpPct", "map", "x", "y", "bag", "bagFree",
            "supply", "supplyFails", "remaining", "deaths", "status"}
    check("采样字段齐备", need <= set(samp), str(sorted(need - set(samp))))

    # 报告里的原因不再写成「玩家上线，离线修炼结束」这类误导文案
    print("\n=== 4. takeover 文案 ===")
    cfg3, db3, _dbp3, srv3 = make_service(afk_min_level=1, afk_excluded_maps=(),
                                          afk_max_agents=10, afk_per_map_limit=5)
    s3 = ClientSession()
    p3 = login(srv3, s3, "LogTakeover")
    db3.save_afk(p3.character_id, 600, "", 0, "", 0)
    p3.afk_plan = {"minutes": 60, "preset": "balanced"}
    srv3.afk.promote(p3)
    srv3.afk.takeover("LogTakeover")
    reps = db3.pending_afk_reports(p3.character_id)
    check("上线结算写入报告", bool(reps))
    if reps:
        reason = reps[0]["reason"]
        check("文案为「正常结束」", "正常结束" in reason, reason)
        check("不再是「玩家上线，离线修炼结束」",
              "玩家上线" not in reason, reason)


def case_supply_loop() -> None:
    """满包必须尊重补给冷却（2026-11-24 日志抓出的真实 bug）。

    复现条件：背包满 → `_survive` 每 tick 调 `_on_bag_full` → 而
    `_supply_begin` 不检查冷却，一轮 800ms 的空转补给（本地商人在身边、
    卖无可卖、药已满）走完立刻又起一轮。修复前 90 分钟 2885 轮。
    """
    print("\n=== 5. 满包不得绕过补给冷却 ===")
    cfg, db, _dbp, srv = make_service(afk_min_level=1, afk_excluded_maps=(),
                                      afk_max_agents=10, afk_per_map_limit=5,
                                      afk_supply_enabled=1)
    sess = ClientSession()
    p = login(srv, sess, "LogBagLoop")
    import pyserver.afk as afk_mod
    mgr = srv.afk
    pilot = afk_mod.OfflinePilot(mgr, p.id, preset="balanced",
                                 started_at=afk_mod.now_ms(),
                                 exp_start=p.exp, level_start=p.level,
                                 max_exp_start=p.max_exp, gold_start=p.gold,
                                 last_exp=p.exp, last_bag=len(p.bag),
                                 last_gold=p.gold,
                                 last_progress_at=afk_mod.now_ms())
    mgr._pilots[p.id] = pilot
    # 冷却期内反复触发满包：supply_state 必须保持空、轮次不增长
    pilot.supply_retry_until = afk_mod.now_ms() + 180_000
    before = pilot.log_supplies
    for _ in range(30):
        pilot._on_bag_full(srv, srv.world, p, afk_mod.now_ms())
    check("冷却期内不重起补给", pilot.supply_state == ""
          and pilot.log_supplies == before,
          f"state={pilot.supply_state!r} 轮次 {before}→{pilot.log_supplies}")
    # 冷却结束后应当能正常开始
    pilot.supply_retry_until = 0
    pilot._on_bag_full(srv, srv.world, p, afk_mod.now_ms())
    check("冷却结束后可重起补给", pilot.supply_state != "",
          pilot.supply_state)
    mgr.disable(p, "测试收尾")


def main() -> int:
    case_unit()
    case_reject_text()
    case_lifecycle()
    case_supply_loop()
    print("\n" + "=" * 60)
    print(f"结果: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
