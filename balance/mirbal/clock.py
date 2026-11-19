"""虚拟时钟：替换 ``time.time``，让 pyserver 内部所有 ``now_ms()`` 跟着走。

必须在 import pyserver 之前调用 :func:`install`。
"""
from __future__ import annotations

import time as _time

_real_time = _time.time
_now = [1_700_000_000.0]


def install() -> None:
    _time.time = lambda: _now[0]


def now_ms() -> int:
    return int(_now[0] * 1000)


def set_ms(ms: int) -> None:
    _now[0] = ms / 1000.0


def advance_ms(ms: int) -> int:
    _now[0] += ms / 1000.0
    return now_ms()


def real() -> float:
    return _real_time()
