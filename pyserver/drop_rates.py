"""统一倍率表：金币/物品掉落、击杀经验与技能修炼速度，集中配置，不改数据文件。

原版 ``Mir200/Envir/MonItems/<怪物>.txt`` 每行是 ``n/m 物品名 [数量]``，命中概率
``n/m``（绝大多数写成 ``1/m``）。本表只在**判定时**乘倍率，所以原始数据保持不动：

``config/game_rates.json``::

    {
      "exp": 1.0,             // 击杀经验倍率（1.0 = 原版节奏）
      "gold": 1.0,            // 金币条目概率倍率
      "item": 1.0,            // 非金币条目概率倍率
      "goldAmount": 1.0,      // 金币数量倍率
      "magicTrain": 1.0,      // 技能修炼速度倍率（2.0 = 所需点数减半）
      "monsters": {           // 按怪物覆盖（与全局相乘，可覆盖 exp）
        "鹿": {"gold": 2.0, "item": 2.0, "goldAmount": 1.5, "exp": 2.0},
        "鸡": {"item": 0}     // 0 = 该怪物这一类别不掉
      },
      "items": {              // 按物品覆盖（与上面两项相乘）
        "战神油": 0.5
      }
    }

概率封顶 100%；经验倍率 0 也能生效（该怪物不给经验）。
``magicTrain`` 0 = 技能不再升级；原始需求见 ``mud2/DB/Magic.csv`` 的 L1/L2/L3Train。
文件缺失或字段非法时全部按 1.0 处理，不影响启动。
旧文件名 ``config/drop_rates.json`` 仍可读取（兼容用，新配置请用 game_rates.json）。
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GOLD_ITEM = "金币"


def _rate(value: Any, default: float = 1.0) -> float:
    try:
        r = float(value)
    except (TypeError, ValueError):
        return default
    return r if r >= 0 else default


@dataclass(frozen=True, slots=True)
class DropRates:
    """一组掉落倍率。默认 1.0 等价于「完全按原版 MonItems」。"""

    gold: float = 1.0
    item: float = 1.0
    gold_amount: float = 1.0
    exp: float = 1.0
    magic_train: float = 1.0
    monsters: dict[str, dict[str, float]] = field(default_factory=dict)
    items: dict[str, float] = field(default_factory=dict)

    def probability(self, monster: str, item_name: str, boost: float = 1.0) -> float:
        """返回该怪物掉该物品的概率倍率（1.0 = 原版）。

        ``boost`` 用于活动期额外相乘（周末双倍），不写回配置表。
        """
        is_gold = item_name == GOLD_ITEM
        rate = self.gold if is_gold else self.item
        rate *= self.items.get(item_name, 1.0)
        per = self.monsters.get(monster)
        if per:
            rate *= per.get("gold" if is_gold else "item", 1.0)
        return max(0.0, rate * boost)

    def amount(self, monster: str) -> float:
        """返回该怪物掉金币时的数量倍率。"""
        per = self.monsters.get(monster)
        if per and "goldAmount" in per:
            return max(0.0, per["goldAmount"])
        return max(0.0, self.gold_amount)

    def exp_for(self, monster: str, boost: float = 1.0) -> float:
        """返回击杀该怪物时的经验倍率（0 = 不给经验）。``boost`` 为活动加成。"""
        per = self.monsters.get(monster)
        rate = per["exp"] if per and "exp" in per else self.exp
        return max(0.0, rate * boost)

    def magic_need(self, raw: int) -> int:
        """把 Magic.csv 的修炼点需求按 ``magicTrain`` 折算（0 = 不再升级）。"""
        if raw <= 0:
            return 0
        rate = max(0.0, self.magic_train)
        if rate == 1.0:
            return raw
        if rate <= 0:
            return 0
        return max(1, int(round(raw / rate)))

    def hit(self, monster: str, item_name: str, num: int, den: int,
            boost: float = 1.0) -> bool:
        """按原版 ``num/den`` 概率掷点，再乘倍率与活动加成（封顶 100%）。"""
        if den <= 0:
            return False
        p = self.probability(monster, item_name, boost) * num / den
        if p <= 0:
            return False
        return p >= 1 or random.random() < p


def _parse_floats(raw: Any) -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if str(k).startswith("_") or isinstance(v, dict):
                continue
            out[str(k)] = _rate(v)
    return out


def load_drop_rates(path: Path | str | None) -> DropRates:
    """读取倍率表；任何异常都退化成「全 1.0」。"""
    if path is None:
        return DropRates()
    p = Path(path)
    if not p.is_file():
        return DropRates()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"[drop] 读取 {p} 失败，按原版倍率运行：{exc}")
        return DropRates()
    if not isinstance(data, dict):
        return DropRates()

    monsters: dict[str, dict[str, float]] = {}
    raw_mons = data.get("monsters")
    if isinstance(raw_mons, dict):
        for name, rule in raw_mons.items():
            if isinstance(rule, dict):
                cleaned = {k: _rate(v) for k, v in rule.items() if k != "_comment"}
                monsters[str(name)] = cleaned

    return DropRates(
        gold=_rate(data.get("gold", 1.0)),
        item=_rate(data.get("item", 1.0)),
        gold_amount=_rate(data.get("goldAmount", 1.0)),
        exp=_rate(data.get("exp", 1.0)),
        magic_train=_rate(data.get("magicTrain", 1.0)),
        monsters=monsters,
        items=_parse_floats(data.get("items")),
    )
