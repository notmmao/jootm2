#!/usr/bin/env python3
"""免密登录 token 回归（Python 3.10）。
    D:/Python310/python.exe scripts/test_auth_token.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver import auth_token  # noqa: E402

P = F = 0


def check(name, cond, detail=""):
    global P, F
    ok = bool(cond)
    if ok:
        P += 1
    else:
        F += 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" · {detail}" if detail else ""))


NOW = 1_700_000_000
t = auth_token.issue(25, "player", now=NOW)
check("签发非空", bool(t) and "." in t)
p = auth_token.verify(t, now=NOW + 10)
check("校验成功", p is not None and p["uid"] == 25 and p["u"] == "player", str(p))

print("\n[1] 篡改与过期")
bad = t[:-2] + ("aa" if not t.endswith("aa") else "bb")
check("篡改签名被拒", auth_token.verify(bad, now=NOW + 10) is None)
check("过期被拒", auth_token.verify(t, now=NOW + auth_token.TTL_SECONDS + 1) is None)
check("空 token 被拒", auth_token.verify("") is None)
check("乱码被拒", auth_token.verify("not.a.token") is None)

print("\n[2] 30 天有效期内")
check("29 天有效", auth_token.verify(t, now=NOW + 29 * 86400) is not None)
check("30 天+1 秒过期", auth_token.verify(t, now=NOW + 30 * 86400 + 1) is None)

print("\n[3] 中文用户名")
t2 = auth_token.issue(7, "测试号", now=NOW)
p2 = auth_token.verify(t2, now=NOW + 5)
check("中文用户名往返", p2 is not None and p2["u"] == "测试号", str(p2))

print(f"\n结果: {P} PASS / {F} FAIL")
sys.exit(1 if F else 0)
