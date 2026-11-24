#!/usr/bin/env python3
"""离线修炼日志分析器：把 data/afk-logs/*.jsonl 汇总成人看的报告。

用途（用户 2026-11-24 要求「单独存储，以便分析，为以后优化找到依据」）：
回答「这一轮为什么收益低」「补给占了多久」「卡在哪张图」「死亡集中在什么
状态」这类问题，输出每分钟收益曲线、地图分布、补给耗时占比、事件时间线。

用法（务必 3.10）：
    D:/Python310/python.exe scripts/afk_log_report.py                  # 全部文件
    D:/Python310/python.exe scripts/afk_log_report.py --day 2026-11-24
    D:/Python310/python.exe scripts/afk_log_report.py --name 角色名
    D:/Python310/python.exe scripts/afk_log_report.py --timeline       # 事件时间线

零第三方依赖，只读日志，不改任何游戏数据。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402


def read_records(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue          # 写到一半被截断的行直接跳过
    return out


def split_sessions(recs: list[dict]) -> list[list[dict]]:
    """按 session_start / session_end 切分会话；缺起止的尾部记录单独成段。"""
    out: list[list[dict]] = []
    cur: list[dict] = []
    for r in recs:
        if r.get("kind") == "session_start" and cur:
            out.append(cur)
            cur = [r]
            continue
        cur.append(r)
        if r.get("kind") == "session_end":
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def fmt_minutes(sec_or_ms: float, is_ms: bool = True) -> str:
    v = sec_or_ms / 60_000 if is_ms else sec_or_ms
    return f"{v:.0f} 分" if v >= 1 else f"{v * 60:.0f} 秒"


def report_session(idx: int, recs: list[dict], timeline: bool) -> None:
    start = recs[0]
    end = recs[-1] if recs[-1].get("kind") == "session_end" else None
    samples = [r for r in recs if r.get("kind") == "sample"]
    deaths = [r for r in recs if r.get("kind") == "death"]
    s_begin = [r for r in recs if r.get("kind") == "supply_begin"]
    s_end = [r for r in recs if r.get("kind") == "supply_end"]
    s_fail = [r for r in recs if r.get("kind") == "supply_fail"]
    rejects = [r for r in recs if r.get("kind") == "reject"]
    errors = [r for r in recs if r.get("kind") == "error"]
    broken = [r for r in recs if r.get("kind") == "item_broken"]

    print(f"\n--- 会话 #{idx} ---")
    print(f"  开始 {start.get('t', '?')}  等级 {start.get('level', '?')}  "
          f"档位 {start.get('preset', '?')}  计划 {start.get('plan', '?')} 分钟"
          + ("（重启续挂）" if start.get("resumed") else ""))
    if end:
        print(f"  结束 {end.get('t', '?')}  原因 {end.get('reason', '')}"
              f"  实耗 {end.get('consumed', 0)} 分钟"
              f"（计划 {end.get('planned', 0)}，剩余 {end.get('remaining', 0)}）")
        print(f"  收益 经验 +{end.get('expGained', 0)}  击杀 {end.get('kills', 0)}"
              f"  金币 {end.get('goldDelta', 0):+d}  死亡 {end.get('deaths', 0)}"
              f"  等级 {start.get('level', '?')} → {end.get('level', '?')}")
        consumed = max(1, int(end.get("consumed") or 0))
        print(f"  均值 每分钟 经验 {end.get('expGained', 0) / consumed:.0f}"
              f"  击杀 {end.get('kills', 0) / consumed:.2f}")
    else:
        print("  （会话未正常结束：服务端停机 / 进程被杀）")
    print(f"  采样 {len(samples)} 条  死亡 {len(deaths)} 次  "
          f"补给 {len(s_begin)} 轮  异常 {len(errors)} 次")
    if rejects:
        for r in rejects:
            print(f"  ! 拒绝[{r.get('stage')}] {r.get('reason')}")

    if samples:
        # 地图分布（按采样条数近似停留时长）
        maps = Counter(r.get("map", "?") for r in samples)
        dwell = ", ".join(f"{m}:{n}" for m, n in maps.most_common(4))
        print(f"  地图分布(采样数) {dwell}")
        # 补给状态占比：supply 非空即处于补给流程
        in_supply = sum(1 for r in samples if r.get("supply"))
        print(f"  补给占时 ≈ {in_supply / len(samples) * 100:.0f}%"
              f"（{in_supply}/{len(samples)} 采样）")
        # 血线分布
        low_hp = sum(1 for r in samples if int(r.get("hpPct", 100)) <= 35)
        print(f"  低血量采样(≤35%) {low_hp} 条"
              f"（{low_hp / len(samples) * 100:.0f}%）")
        # 背包饱和度
        free = [int(r.get("bagFree", 0)) for r in samples]
        if free:
            print(f"  背包空位 最少 {min(free)}  平均 {sum(free) / len(free):.1f}")
        # 每分钟收益曲线（稀疏打印：最多 12 个点）
        d_exp = [int(r.get("dExp", 0)) for r in samples]
        step = max(1, len(d_exp) // 12)
        curve = " ".join(str(d_exp[i]) for i in range(0, len(d_exp), step))
        print(f"  分钟经验曲线 {curve}")
        kills_total = sum(int(r.get("dKills", 0)) for r in samples)
        exp_total = sum(d_exp)
        zero_exp = sum(1 for v in d_exp if v == 0)
        print(f"  区间合计 经验 {exp_total}  击杀 {kills_total}"
              f"  零经验分钟 {zero_exp}/{len(d_exp)}"
              f"（{zero_exp / len(d_exp) * 100:.0f}%）")

    # 补给轮次的成败与耗时
    if s_begin:
        ok_cnt = len(s_end)
        fail_cnt = len(s_fail)
        print(f"  补给成功 {ok_cnt} 轮 / 失败 {fail_cnt} 次")
        reasons = Counter(r.get("why", "") for r in s_begin)
        print("  补给触发原因 " + ", ".join(f"{k or '?'}:{v}"
                                            for k, v in reasons.most_common()))
        if s_fail:
            fails = Counter(r.get("why", "") for r in s_fail)
            print("  失败原因 " + ", ".join(f"{k or '?'}:{v}"
                                            for k, v in fails.most_common(5)))
    if deaths:
        states = Counter(r.get("supply", "") or "打怪中" for r in deaths)
        print("  死亡时状态 " + ", ".join(f"{k}:{v}" for k, v in states.items()))
    if broken:
        # 装备耐久耗尽爆掉（用户 2026-11-25：怀疑首饰掉太快，需实证）
        by_item = Counter(f"{r.get('slotName', '?')}·{r.get('item', '?')}"
                          for r in broken)
        print("  耐久爆掉 " + ", ".join(f"{k}:{v}" for k, v in by_item.most_common(6))
              + f"（共 {len(broken)} 件）")
        end_ms = int(end.get("ms", 0)) if end and end.get("ms") else 0
        if end_ms:
            per_min = len(broken) / max(1, (end_ms - int(start.get("ms", end_ms))) / 60_000)
            if per_min:
                print(f"  爆掉速率 ≈ {per_min:.2f} 件/分钟")

    if timeline:
        print("  时间线：")
        for r in recs:
            k = r.get("kind")
            if k == "sample":
                continue
            detail = {kk: vv for kk, vv in r.items()
                      if kk not in ("t", "kind", "ms")}
            print(f"    {r.get('t', '?')} {k} {detail}")


def main() -> int:
    cfg = load()
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(cfg.afk_log_dir))
    ap.add_argument("--day", default="", help="只看某个日期（YYYY-MM-DD）")
    ap.add_argument("--name", default="", help="只看某个角色名（模糊匹配）")
    ap.add_argument("--timeline", action="store_true", help="打印完整事件时间线")
    ap.add_argument("--limit", type=int, default=20, help="最多分析多少个文件")
    args = ap.parse_args()

    log_dir = Path(args.dir)
    if not log_dir.is_dir():
        print(f"日志目录不存在：{log_dir}")
        print("提示：先让角色跑一次离线修炼，或用 "
              "scripts/sim_afk_longrun.py --log 生成模拟日志。")
        return 1
    files = sorted(log_dir.glob("*.jsonl"))
    if args.day:
        files = [f for f in files if f.name.startswith(args.day)]
    if args.name:
        files = [f for f in files if args.name in f.name]
    if not files:
        print("没有匹配的日志文件。")
        return 1
    files = files[-args.limit:]

    print(f"日志目录 {log_dir}，共 {len(files)} 个文件")
    grand = defaultdict(lambda: {"min": 0, "exp": 0, "kills": 0,
                                "gold": 0, "deaths": 0, "supplies": 0,
                                "sessions": 0})
    for f in files:
        recs = read_records(f)
        if not recs:
            continue
        print(f"\n=== {f.name}（{len(recs)} 条记录）===")
        sessions = split_sessions(recs)
        name = f.stem.split("_", 1)[-1] if "_" in f.stem else f.stem
        for i, s in enumerate(sessions, 1):
            if not s:
                continue
            report_session(i, s, args.timeline)
            end = s[-1] if s[-1].get("kind") == "session_end" else None
            g = grand[name]
            g["sessions"] += 1
            if end:
                g["min"] += int(end.get("consumed") or 0)
                g["exp"] += int(end.get("expGained") or 0)
                g["kills"] += int(end.get("kills") or 0)
                g["gold"] += int(end.get("goldDelta") or 0)
                g["deaths"] += int(end.get("deaths") or 0)
                g["supplies"] += int(end.get("supplies") or 0)

    print("\n" + "=" * 60)
    print("按角色汇总（仅统计正常结算的会话）")
    for name, g in grand.items():
        m = max(1, g["min"])
        print(f"  {name}: {g['sessions']} 轮 {g['min']} 分钟  "
              f"经验 {g['exp']}（{g['exp'] / m:.0f}/分）  "
              f"击杀 {g['kills']}（{g['kills'] / m:.2f}/分）  "
              f"金币 {g['gold']:+d}  死亡 {g['deaths']}  补给 {g['supplies']} 轮")
    return 0


if __name__ == "__main__":
    sys.exit(main())
