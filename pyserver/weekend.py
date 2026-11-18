"""周末双倍活动：击杀经验与掉落概率按配置倍率加成。

配置在 ``config/server.properties``（缺省值见 ``config.py::DEFAULTS``）::

    weekend.enabled=1        # 总开关
    weekend.expRate=2.0      # 击杀经验倍率
    weekend.dropRate=2.0     # 掉落概率倍率（金币/物品条目，命中率封顶 100%）
    weekend.days=6,7         # 生效星期，1=周一 .. 7=周日
    weekend.startHour=0      # 起始小时（可含小数，20.5 = 20:30）
    weekend.endHour=24       # 结束小时（不含；小于起始小时表示跨午夜）
    weekend.force=0          # 1=强制开启（临时活动/测试用，忽略星期与时间）

语义：只乘在**判定时**，不修改任何 MonItems 掉落表；与 ``game_rates.json``
的全局倍率相乘。用户拍板（2026-11-19）：概率×2（非数量×2）、周六日全天、
登录提示 + 状态切换全服广播。判定用服务器本地时间，不依赖 UTC。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

START_MSG = "[周末活动] 双倍经验、双倍爆率现在开始，祝大家玩得开心！"
END_MSG = "[周末活动] 双倍活动已结束，下次周末再见。"

_FALLBACK_DAYS = (6, 7)


def is_active(cfg: Any, now: datetime | None = None) -> bool:
    """当前是否处于周末双倍时段。"""
    if not getattr(cfg, "weekend_enabled", 0):
        return False
    if getattr(cfg, "weekend_force", 0):
        return True
    t = now if now is not None else datetime.now()
    if t.isoweekday() not in (getattr(cfg, "weekend_days", None) or _FALLBACK_DAYS):
        return False
    hour = t.hour + t.minute / 60 + t.second / 3600
    start = float(getattr(cfg, "weekend_start_hour", 0))
    end = float(getattr(cfg, "weekend_end_hour", 24))
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end        # 跨午夜窗口（如 22:00~02:00）


def exp_rate(cfg: Any, now: datetime | None = None) -> float:
    """当前击杀经验倍率（非活动期恒 1.0）。"""
    if not is_active(cfg, now):
        return 1.0
    try:
        rate = float(getattr(cfg, "weekend_exp_rate", 2.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, rate)


def drop_rate(cfg: Any, now: datetime | None = None) -> float:
    """当前掉落概率倍率（非活动期恒 1.0）。"""
    if not is_active(cfg, now):
        return 1.0
    try:
        rate = float(getattr(cfg, "weekend_drop_rate", 2.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, rate)


def login_text(cfg: Any, now: datetime | None = None) -> str:
    """进入游戏时的一行活动状态提示。"""
    if not getattr(cfg, "weekend_enabled", 0):
        return ""
    if is_active(cfg, now):
        return (f"[周末活动] 正在进行：经验 ×{exp_rate(cfg, now):g}、"
                f"爆率 ×{drop_rate(cfg, now):g}！")
    return "[周末活动] 周六、周日全天开启双倍经验与双倍爆率，敬请期待。"
