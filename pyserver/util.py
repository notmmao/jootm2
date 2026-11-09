"""跨模块共用的小工具。

从 service.py 抽出，避免各 handler mixin 与 service 形成循环 import。
语义对齐 Java ``GameService`` 的 ``integer() / stringVal() / longVal()``。
"""
from __future__ import annotations

import json
import math
import time
from typing import Any

SIGHT_RANGE = 12
AGGRO_RANGE = 8
RESPAWN_MS = 15_000
REGEN_INTERVAL_MS = 2_000
HEALING_INTERVAL_MS = 500
POISON_INTERVAL_MS = 2_500
BAG_SIZE = 46
GROUND_ITEM_TTL_MS = 60_000
DOOR_OPEN_MS = 5_000
USE_ITEM_INTERVAL_MS = 300
EQUIP_SLOTS = 13

# 怪物 AI（对齐 Java GameWorld 常量 / ObjMon2.pas）
RANGED_RACES = frozenset({82, 87, 93, 94, 104, 112, 20})
GUARD_RACES = frozenset({20, 112})
RANGED_ATTACK_RANGE = 7
RANGED_KEEP_AWAY = 4
RANGED_CHASE_RANGE = 11

# 装备特殊 Shape（对齐 Java Player 常量 / ObjBase.pas）
SHAPE_PARALYSIS_WEAPON = 23
SHAPE_TELEPORT_RING = 112
SHAPE_REVIVAL_RING = 114
SHAPE_MANA_TO_HP = frozenset({133, 134, 135})
SHAPE_SUCK_HEALTH = frozenset({136, 137, 138})
PSET_SHAPES = (153, 154, 155)   # 战士套装：项链 / 手镯 / 戒指
HSET_SHAPES = (156, 157, 158)   # 法师
YSET_SHAPES = (159, 160, 161)   # 道士
SLAVE_COLORS = (255, 254, 147, 154, 229, 168, 180, 252)
SLAVE_UP_EXP = (0, 0, 50, 100, 200, 300, 600)

U_DRESS, U_WEAPON, U_RIGHTHAND, U_NECKLACE = 0, 1, 2, 3
U_HELMET, U_ARMRINGL, U_ARMRINGR = 4, 5, 6
U_RINGL, U_RINGR, U_BUJUK, U_BELT, U_BOOTS, U_CHARM = 7, 8, 9, 10, 11, 12


def _str(msg: dict, key: str) -> str | None:
    v = msg.get(key)
    return v if isinstance(v, str) else None


def _int(msg: dict, key: str, default: int = 0) -> int:
    v = msg.get(key)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return default
    return default


def _bool(msg: dict, key: str, default: bool = False) -> bool:
    v = msg.get(key)
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return default


def _error(msg_type: str, text: str) -> dict:
    return {"type": msg_type, "ok": False, "error": text}


def encode(obj: Any) -> str:
    """与 Gson ``toString()`` 等价：中文不转义、无多余空格。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def now_ms() -> int:
    return int(time.time() * 1000)


def java_div(a: int, b: int) -> int:
    """Java 整数除法：向零截断（Python // 对负数向下取整）。"""
    if b == 0:
        return 0
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def java_round(x: float) -> int:
    """``Math.round(double)`` == floor(x + 0.5)（注意与 Python round 的 banker's 不同）。"""
    return math.floor(x + 0.5)


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else (hi if v > hi else v)


def direction_delta(d: int) -> tuple[int, int]:
    return ((0, 1, 1, 1, 0, -1, -1, -1)[d & 7],
            (-1, -1, 0, 1, 1, 1, 0, -1)[d & 7])


def direction_toward(fx: int, fy: int, tx: int, ty: int, fallback: int) -> int:
    sx = (tx > fx) - (tx < fx)
    sy = (ty > fy) - (ty < fy)
    for d in range(8):
        dx, dy = direction_delta(d)
        if ((dx > 0) - (dx < 0), (dy > 0) - (dy < 0)) == (sx, sy):
            return d
    return fallback


def slot_for(std_mode: int) -> int:
    """``Player.slotFor``：按 StdMode 自动选装备槽。"""
    return {
        5: U_WEAPON, 6: U_WEAPON,
        10: U_DRESS, 11: U_DRESS,
        15: U_HELMET,
        19: U_NECKLACE, 20: U_NECKLACE, 21: U_NECKLACE,
        22: U_RINGL, 23: U_RINGL,
        24: U_ARMRINGR, 26: U_ARMRINGR,
        25: U_BUJUK,
        30: U_RIGHTHAND,
        52: U_BOOTS, 53: U_CHARM, 54: U_BELT,
    }.get(std_mode, -1)


def slot_accepts(slot: int, std_mode: int) -> bool:
    """``Player.slotAccepts``：对齐原版 M2Share.IsTakeOnAvailable。"""
    return {
        U_DRESS: std_mode in (10, 11),
        U_WEAPON: std_mode in (5, 6),
        U_RIGHTHAND: std_mode == 30,
        U_NECKLACE: std_mode in (19, 20, 21),
        U_HELMET: std_mode == 15,
        U_ARMRINGL: std_mode in (24, 25, 26),
        U_ARMRINGR: std_mode in (24, 26),
        U_RINGL: std_mode in (22, 23),
        U_RINGR: std_mode in (22, 23),
        U_BUJUK: std_mode == 25,
        U_BELT: std_mode == 54,
        U_BOOTS: std_mode == 52,
        U_CHARM: std_mode == 53,
    }.get(slot, False)


def load_skills(raw: str) -> dict[int, dict]:
    """兼容两种落盘格式：``{magicId:{level,train,key}}`` 与旧 ``{magicId:level}``。"""
    out: dict[int, dict] = {}
    try:
        obj = json.loads(raw or "{}")
    except (ValueError, TypeError):
        return out
    if not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        try:
            mid = int(k)
        except (ValueError, TypeError):
            continue
        if isinstance(v, dict):
            out[mid] = {"level": _int(v, "level"), "train": _int(v, "train"),
                        "key": _int(v, "key")}
        elif isinstance(v, (int, float)):
            out[mid] = {"level": int(v), "train": 0, "key": 0}
    return out
