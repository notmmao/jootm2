"""跨模块共用的小工具。

从 service.py 抽出，避免各 handler mixin 与 service 形成循环 import。
语义对齐 Java ``GameService`` 的 ``integer() / stringVal() / longVal()``。
"""
from __future__ import annotations

import json
import math
import random
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

# 怪物 AI（对齐原版 UsrEngn.pas:1831 AddBaseObject 的 race → 类派发）
RANGED_RACES = frozenset({82, 87, 93, 94, 104, 112, 20})
# 守卫：原版 SUPREGUARD=11 / PETSUPREGUARD=12 / ARCHER_POLICE=20 / RC_ARCHERGUARD=112
GUARD_RACES = frozenset({11, 12, 20, 112})
# 大刀守卫（TSuperGuard race 11 / TPetSuperGuard race 12，ObjGuard.pas）：
# 主动攻击视野(7)内的一切怪物（race >= RC_MONSTER=80）与红名玩家；
# m_boAttackPet=True 时**玩家的召唤物也照打**（race 12 唯一例外，见 _resolve_target）。
SUPER_GUARD_RACES = frozenset({11, 12})
# 原版 PKLevel = PkPoint div 100（ObjBase.pas:2236），守卫目标条件 PKLevel >= 2
# （TGuardUnit.IsProperTarget ObjMon2.pas:878 / TSuperGuard.Run ObjGuard.pas:100）
# = 深红名 pk_point >= 200。黄名（100~199）不会被守卫攻击。
RED_NAME_PK_POINT = 200
# 中立怪：不主动搜敌，**被打才还击**。原版这些 race 实例化成 TMonster，
# 而 TMonster.Run（ObjMon.pas:415）里没有 SearchTarget 调用（只有 Wondering 乱走），
# 目标只可能来自 TAnimalObject.Struck(hiter) → SetTargetCreat(hiter)。
#   ANIMAL_CHICKEN=51 鸡 / ANIMAL_DEER=52 鹿·羊 / MONSTER_OMA=80 奥玛
# 对照：53 狼 与 81~ 各类走 TATMonster，Run 里每 1.5~3 秒 SearchTarget → 主动。
NEUTRAL_RACES = frozenset({51, 52, 80})
# 大刀守卫视野（TSuperGuard.Create：m_nViewRange := 7，ObjGuard.pas:71）
SUPER_GUARD_VIEW = 7


def calc_get_exp(player_level: int, mon_level: int, exp: int) -> int:
    """原版 ``TBaseObject.CalcGetExp``（ObjBase.pas:2250-2261）经验惩罚。

    ```pascal
    if g_Config.boHighLevelKillMonFixExp or (m_Abil.Level < (nLevel + 10)) then
      Result := nExp                                    // 玩家 < 怪等级+10：全额
    else
      Result := nExp - Round((nExp / 15) * (m_Abil.Level - (nLevel + 10)));
    if Result <= 0 then Result := 1;                    // 保底 1
    ```

    ``boHighLevelKillMonFixExp`` 默认 False（M2Share.pas:2141）——惩罚默认生效，
    即玩家等级 ≥ 怪等级+10 时每超 1 级扣 1/15。Delphi ``Round`` 是银行家舍入，
    Python 3 的 ``round`` 同为 half-to-even，直接对应。
    """
    if player_level < mon_level + 10:
        return exp
    result = exp - round(exp / 15 * (player_level - mon_level - 10))
    return result if result > 0 else 1
RANGED_ATTACK_RANGE = 7
RANGED_KEEP_AWAY = 4
RANGED_CHASE_RANGE = 11
# 原版 TChickenDeer.Create：``m_nViewRange := 5``（ObjMon.pas:534）；
# Run 里 abs(target)<=6 才起步逃跑（ObjMon.pas:589）。
CHICKEN_DEER_VIEW = 5
CHICKEN_DEER_RUN = 6

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


def init_meat_quality(m) -> None:
    """原版 UsrEngn.pas:1835-1864：动物生成时初始化 m_nMeatQuality（肉品质）。

    - ANIMAL_CHICKEN(51 鸡)：``Random(3500) + 3000`` → 3000~6500
    - ANIMAL_DEER(52 鹿)：``Random(8000) + 8000`` → 8000~16000；
      但有 ``Random(30) = 0`` 的 1/30 概率换成 ``TChickenDeer`` 变种
      （ObjMon.pas:531，视野 5 + 见人就跑），品质 ``Random(20000) + 10000``
      → 10000~30000，肉能卖更贵。
    - ANIMAL_WOLF(53 狼)：``Random(8000) + 8000`` → 8000~16000
    其余怪物品质为 0（可挖但不出肉的蜘蛛/蝎子/食人花/洞蛆没有品质语义）。
    挖肉（ClientGetButchItem）/ 受击（Struck -Random(300)）/ 绿毒（每跳 -1000）/
    魔法伤害（-nDamage*1000）都会降低品质，出肉时 ApplyMeatQuality 把品质写进
    肉的耐久 → 卖价随之变化。
    """
    race = getattr(m, "race", 0)
    if race == 51:
        m.meat_quality = random.randrange(3500) + 3000
    elif race == 52:
        if random.randrange(30) == 0:            # TChickenDeer 变种
            m.meat_quality = random.randrange(20000) + 10000
            # ObjMon.pas:531-598：TChickenDeer 视野 5、见人就跑（不攻击）。
            m.chicken_deer = True
        else:
            m.meat_quality = random.randrange(8000) + 8000
    elif race == 53:
        m.meat_quality = random.randrange(8000) + 8000


def random_mine_dura() -> int:
    """原版 TPlayObject.MakeMine 的 RandomDrua（ObjBase.pas:24352-24358）：

    ``Random(13000) + 3000``，1/20 概率再 ``+ Random(10000)`` → 3000~23000。
    矿石耐久即「纯度」，卖价随耐久变化（GetUserItemPrice StdMode 43）。
    """
    n = random.randrange(13000) + 3000
    if random.randrange(20) == 0:
        n += random.randrange(10000)
    return n


def delphi_round(x: float) -> int:
    """Delphi 的 ``Round()``：四舍六入五取偶（3.5→4、2.5→2、10.5→10）。

    Delphi 源码里到处是 ROUND（商店定价 ``Round(StdItem.Price*1.1)``、
    治愈术 GetPower、``Math.round`` 的 Java 移植是 floor(x+0.5)，在 .5
    且向下为偶数时会多 1，不能混用。
    """
    f = int(x // 1) if x >= 0 else -int(-x // 1)
    d = abs(x) - abs(f)
    if d < 0.5:
        return f
    if d > 0.5:
        return f + (1 if x > 0 else -1)
    if f % 2 == 0:
        return f
    return f + (1 if x > 0 else -1)


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
