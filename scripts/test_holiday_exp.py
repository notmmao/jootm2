#!/usr/bin/env python3
"""法定节假日双倍经验回归（Python 3.10）。

覆盖：内置 2026 放假表、自定义日期、force、总开关、与周末倍率取最大值不叠乘。
    D:/Python310/python.exe scripts/test_holiday_exp.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver import holiday  # noqa: E402
from pyserver.config import load as load_config  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    if ok:
        P += 1
    else:
        F += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


cfg = load_config(ROOT)
check("默认开启", cfg.holiday_enabled == 1)
check("默认倍率 2.0", cfg.holiday_exp_rate == 2.0)

print("\n[1] 内置 2026 放假表（国务院放假日期）")
cases = [
    ("2026-01-01", True, "元旦"), ("2026-01-03", True, "元旦末日"),
    ("2026-01-04", False, "元旦调休上班日不加成"),
    ("2026-02-15", True, "春节首日"), ("2026-02-23", True, "春节末日"),
    ("2026-02-14", False, "春节调休上班日"),
    ("2026-04-05", True, "清明"), ("2026-05-01", True, "劳动节"),
    ("2026-06-20", True, "端午"), ("2026-09-26", True, "中秋"),
    ("2026-10-01", True, "国庆首日"), ("2026-10-07", True, "国庆末日"),
    ("2026-11-23", False, "普通工作日"),
]
for ds, expect, label in cases:
    d = datetime.strptime(ds, "%Y-%m-%d")
    check(f"{ds} {label}", holiday.is_active(cfg, d) == expect)

print("\n[2] 倍率与开关")
check("命中日倍率=2.0", holiday.exp_rate(cfg, datetime(2026, 10, 1)) == 2.0)
check("非命中日倍率=1.0", holiday.exp_rate(cfg, datetime(2026, 11, 23)) == 1.0)
off = SimpleNamespace(holiday_enabled=0, holiday_exp_rate=2.0,
                      holiday_force=0, holiday_dates="")
check("总开关关闭", not holiday.is_active(off, datetime(2026, 10, 1)))
force = SimpleNamespace(holiday_enabled=1, holiday_exp_rate=3.0,
                        holiday_force=1, holiday_dates="")
check("force 强制开启", holiday.is_active(force, datetime(2026, 11, 23)))
check("force 用自定义倍率", holiday.exp_rate(force, datetime(2026, 11, 23)) == 3.0)

print("\n[3] 自定义日期")
custom = SimpleNamespace(holiday_enabled=1, holiday_exp_rate=2.0,
                         holiday_force=0, holiday_dates="2026-12-24,2026-12-25")
check("自定义日期命中", holiday.is_active(custom, datetime(2026, 12, 24)))
check("自定义日期未命中", not holiday.is_active(custom, datetime(2026, 12, 26)))

print("\n[4] 登录提示")
check("命中日提示带双倍", "双倍" in holiday.login_text(cfg, datetime(2026, 10, 1)))
check("非命中日提示不误报", "进行中" not in holiday.login_text(cfg, datetime(2026, 11, 23)))

print("\n[5] 与周末倍率取最大值（不叠乘）")
from pyserver.weekend import exp_rate as weekend_exp  # noqa: E402
# 2026-10-03 是周六（国庆假期内）：两者都命中，应取 max=2 而非 4
w = weekend_exp(cfg, datetime(2026, 10, 3))
h = holiday.exp_rate(cfg, datetime(2026, 10, 3))
check("周末+节假日当天都命中", w == 2.0 and h == 2.0, f"weekend={w} holiday={h}")
check("合并倍率=2（非4）", max(w, h) == 2.0)

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
