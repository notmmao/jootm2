"""法定节假日双倍经验：按国务院放假通知的「放假日期」命中即经验 ×2。

配置在 ``config/server.properties``（缺省值见 ``config.py::DEFAULTS``）::

    holiday.enabled=1        # 总开关
    holiday.expRate=2.0      # 击杀经验倍率
    holiday.force=0          # 1=强制开启（临时活动/测试）
    holiday.dates=           # 自定义日期，逗号分隔 YYYY-MM-DD；留空=用内置表

内置表来源：国务院办公厅《关于2026年部分节假日安排的通知》（国办发明电〔2025〕7号，
2025-11-04 发布），取「放假日期」（不含调休上班日）：
    元旦 1/1-1/3，春节 2/15-2/23，清明 4/4-4/6，劳动节 5/1-5/5，
    端午 6/19-6/21，中秋 9/25-9/27，国庆 10/1-10/7。

语义：
  - 只乘在**击杀经验**判定时（不改 MonItems、不改 add_exp），与 game_rates.json
    全局倍率相乘。法定节假日**只加倍经验，不加倍爆率**（爆率仍归周末活动）。
  - 与周末活动倍率**取最大值**而非相乘：周末碰上节假日仍是 ×2，不会叠成 ×4。
  - 判定用服务器本地日期（部署机已确认 CST +0800），不依赖 UTC。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

START_MSG = "[节日活动] 法定节假日双倍经验现在开始，祝大家玩得开心！"
END_MSG = "[节日活动] 双倍经验活动已结束。"

# 2026 年法定节假日放假日期（国务院通知）。用 (月, 日) 区间，跨年无。
# 只列放假区间；调休上班日（如 1/4、2/14）不在内，本身也不该加成。
HOLIDAYS_2026: tuple[tuple[int, int, int, int, str], ...] = (
    (1, 1, 1, 3, "元旦"),
    (2, 15, 2, 23, "春节"),
    (4, 4, 4, 6, "清明节"),
    (5, 1, 5, 5, "劳动节"),
    (6, 19, 6, 21, "端午节"),
    (9, 25, 9, 27, "中秋节"),
    (10, 1, 10, 7, "国庆节"),
)

# 兜底：内置表只覆盖 2026；其它年份的法定节假日逐年由国务院发布，届时更新表
# 或用 holiday.dates 配置。这里列出每年固定日期的「法定节日当天」作为保底，
# 保证不配置也能在主要节日当天有双倍（完整调休放假以内置表/配置为准）。
_FIXED_ANNUAL: tuple[tuple[int, int, str], ...] = (
    (1, 1, "元旦"), (5, 1, "劳动节"), (10, 1, "国庆节"),
)


def _parse_custom_dates(raw: Any) -> set[str]:
    """解析 holiday.dates：逗号分隔的 YYYY-MM-DD。"""
    if not raw:
        return set()
    out = set()
    for part in str(raw).split(","):
        s = part.strip()
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            try:
                date.fromisoformat(s)
                out.add(s)
            except ValueError:
                pass
    return out


def _holiday_name(d: date, custom: set[str]) -> str | None:
    """返回当天命中的节假日名，未命中返回 None。"""
    if d.isoformat() in custom:
        return "节假日"
    if d.year == 2026:
        for m1, d1, m2, d2, name in HOLIDAYS_2026:
            if (d.month, d.day) >= (m1, d1) and (d.month, d.day) <= (m2, d2):
                return name
    for m, day, name in _FIXED_ANNUAL:
        if (d.month, d.day) == (m, day):
            return name
    return None


def is_active(cfg: Any, now: datetime | None = None) -> bool:
    if not getattr(cfg, "holiday_enabled", 0):
        return False
    if getattr(cfg, "holiday_force", 0):
        return True
    t = now if now is not None else datetime.now()
    custom = _parse_custom_dates(getattr(cfg, "holiday_dates", ""))
    return _holiday_name(t.date(), custom) is not None


def exp_rate(cfg: Any, now: datetime | None = None) -> float:
    """当前法定节假日经验倍率（非活动期恒 1.0）。"""
    if not is_active(cfg, now):
        return 1.0
    try:
        rate = float(getattr(cfg, "holiday_exp_rate", 2.0))
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, rate)


def login_text(cfg: Any, now: datetime | None = None) -> str:
    if not getattr(cfg, "holiday_enabled", 0):
        return ""
    t = now if now is not None else datetime.now()
    custom = _parse_custom_dates(getattr(cfg, "holiday_dates", ""))
    name = _holiday_name(t.date(), custom)
    if name or getattr(cfg, "holiday_force", 0):
        return f"[节日活动] {name or '节假日'}双倍经验进行中：经验 ×{exp_rate(cfg, now):g}！"
    return "[节日活动] 法定节假日全天双倍经验，敬请期待。"
