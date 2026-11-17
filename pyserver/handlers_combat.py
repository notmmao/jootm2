"""批次 B：物理攻击、剑术、33 个技能分支。

直译 Java ``GameService`` 1292–2610 行。数值语义刻意保留 Java 行为：
整数除法向零截断（``java_div``）、``Math.round`` = floor(x+0.5)（``java_round``）、
位标志与随机区间。协议下发的 JSON 字段保持一致。
"""
from __future__ import annotations

import random
from typing import Any

from .util import (
    BAG_SIZE, RED_NAME_PK_POINT, SIGHT_RANGE, U_ARMRINGL, U_BUJUK, U_WEAPON, _int,
    calc_get_exp, delphi_round, direction_delta, direction_toward, java_div,
    java_round, now_ms, random_mine_dura,
)
from .world import Monster, Player, mag_struck_damage, melee_hit, positive_hit

COMBAT_TYPES = frozenset({"attack", "dirAttack", "castMagic"})
POISON_INTERVAL_MS = 2_500

# 技能最高 3 级（原版 Magic.DB 的 btTrainLv 恒为 3，LocalDB.pas:382 写死；
# CheckMagicLevelup 里 `btLevel < 4` 的上界等价于「升到 3 级为止」）。
MAX_MAGIC_LEVEL = 3

# 原版 ``RC_ANIMAL = 50``（MirClient/Grobal2.pas:1092-1096）：race >= 50 才算
# 「动物/怪物」，玩家 race = RC_PLAYOBJECT = 0。RM_DELAYMAGIC 的 1.2 折减只对前者生效。
RC_ANIMAL = 50

# 原版组队经验加成（TPlayObject.GainExp，ObjBase.pas:15564）：按组内**有效人数**

# 护身符类技能的 (CheckAmulet nType, nCount)（Magic.pas:81-144 / :318 / :426-427）：
# 6 施毒术吃毒药粉（Shape<=2），13~19 吃 1 张护身符（Shape=5），
# 30 召唤神兽吃 5 张。缺失 = boSpellFail 硬失败（无特效 + MAGICFIREFAIL）。
_AMULET_NEED = {6: (2, 1), 13: (1, 1), 14: (1, 1), 15: (1, 1), 16: (1, 1),
                17: (1, 1), 18: (1, 1), 19: (1, 1), 30: (1, 5)}
# n（同图、非死亡、12 格内的成员）取 bonus[n]，2 人 1.3、3 人 1.4 … 11 人 2.2；
# 再按等级比例分给每个人（dwExp / sumlv * 个人等级，ObjBase.pas:15594）。
# boHighLevelKillMonFixExp=True 的「平均分配」分支默认关闭（M2Share.pas:2141）。
GROUPMAX = 11
PARTY_EXP_BONUS = (1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.0, 2.1, 2.2)


def is_heal_magic(magic_id: int) -> bool:
    return magic_id == 2


def direction_from(fx: int, fy: int, tx: int, ty: int) -> int:
    return direction_toward(fx, fy, tx, ty, 0)


def has_skill(p, magic_id: int) -> bool:
    return magic_id in p.skills


def skill_level(p, magic_id: int) -> int:
    return p.skills.get(magic_id, {}).get("level", 0)


def spell_cost(t, lv: int) -> int:
    return java_round(t.spell / 4 * (lv + 1)) + t.defSpell


# Magic.DB 的 btTrainLv 恒为 3（LocalDB.pas:382 写死），GetPower/GetPower13 都用它。
MAGIC_TRAIN_LV = 3


def _rand(n: int) -> int:
    """Delphi ``Random(n)``：0..n-1；n<=0 时恒 0（``Random(0)=0``）。"""
    return random.randrange(n) if n > 0 else 0


def mpow(t) -> int:
    """Magic.pas:230 ``MPow``：``wPower + Random(wMaxPower - wPower)``。

    注意 **不含上界**：Magic.DB 里 Power=8/MaxPower=8 的技能（火球、治愈…）
    永远只掷出 8；旧实现写成 ``Random(max-min+1)``，所有技能的上界都多 1 点。
    """
    return t.power + _rand(t.maxPower - t.power)


def get_power(t, lv: int, n: int) -> int:
    """Magic.pas:234 ``GetPower``：按技能等级线性放大 + DefPower 随机段。"""
    return (delphi_round(n / (MAGIC_TRAIN_LV + 1) * (lv + 1)) + t.defPower
            + _rand(t.defMaxPower - t.defPower))


def get_power13(t, lv: int, n: int) -> int:
    """Magic.pas:238 ``GetPower13``：n 的 1/3 固定，其余 2/3 随技能等级放大。

    施毒术的毒力、幽灵盾/困魔咒/隐身术的持续时间都走这条。
    """
    d10 = n / 3.0
    d18 = n - d10
    return (delphi_round(d18 / (MAGIC_TRAIN_LV + 1) * (lv + 1) + d10)
            + t.defPower + _rand(t.defMaxPower - t.defPower))


def get_rpow(lo: int, hi: int) -> int:
    """Magic.pas:247 ``GetRPow``：属性区间内取随机值（hi<=lo 取 lo）。"""
    if hi > lo:
        return random.randrange(hi - lo + 1) + lo
    return lo


def attack_power(a, base: int, n_power: int) -> int:
    """ObjBase.pas:2416 ``GetAttackPower``：``base + Random(nPower+1)``。

    幸运 >0 时 1/(10-luck) 概率直接取上界；诅咒（luck<0）则取底。
    """
    luck = int(getattr(a, "luck", 0))
    if luck > 0:
        if _rand(10 - min(9, luck)) == 0:
            return base + n_power
        return base + _rand(n_power + 1)
    result = base + _rand(n_power + 1)
    if luck < 0 and _rand(10 - max(0, -luck)) == 0:
        return base
    return result


def magic_power(p, t, lv: int) -> int:
    """法师系法术掷点：``GetAttackPower(GetPower(MPow)+MC底, MC高-MC底+1)``。"""
    lo, hi = p.combat.get("mcMin", 0), p.combat.get("mcMax", 0)
    return attack_power(p, get_power(t, lv, mpow(t)) + lo, (hi - lo) + 1)


def taoist_power(p, t, lv: int) -> int:
    """道士系法术掷点：同 magic_power 但取 SC。"""
    lo, hi = p.combat.get("scMin", 0), p.combat.get("scMax", 0)
    return attack_power(p, get_power(t, lv, mpow(t)) + lo, (hi - lo) + 1)


def heal_power(p, t, lv: int) -> int:
    """治愈术/群体治疗回血量（Magic.pas:308 / :533）。

    ``GetAttackPower(GetPower(MPow) + LoWord(SC)*2, (HiWord(SC)-LoWord(SC))*2 + 1)``
    —— SC **下限**×2，随机段是 SC 区间×2。
    """
    lo, hi = p.combat.get("scMin", 0), p.combat.get("scMax", 0)
    return attack_power(p, get_power(t, lv, mpow(t)) + lo * 2, (hi - lo) * 2 + 1)


def anti_magic(a) -> int:
    """``m_nAntiMagic``（ObjBase.pas:2855 初值 1 + 装备魔法躲避）。"""
    return int(a.combat.get("antiMagic", 1)) if isinstance(a, Player) else 1


def magic_hits(target) -> bool:
    """Magic.pas 各分支的命中闸门 ``Random(10) >= m_nAntiMagic``。

    怪物 AntiMagic 恒 1 → 90%；玩家带魔法躲避项链时按项链值降低。
    旧实现用 ``1 + MAC//5`` 做「魔法闪避」是自造规则：MAC 在原版只减伤不闪避。
    """
    return random.randrange(10) >= anti_magic(target)


def roll_dc_with_luck(p) -> int:
    """物理攻击掷骰（原版 GetAttackPower，ObjBase.pas:2416-2432）。

    旧实现 `random()*10 < luck` 概率口径错了：原版是
    ``Random(10 - _MIN(9, luck)) = 0``（幸运 1 → 1/9，幸运 7 → 1/3），
    且诅咒（luck<0）还有一条 ``Random(10 - _MAX(0,-luck)) = 0 → 取下限``
    的兜底分支——旧实现完全忽略了负幸运。
    """
    lo, hi = p.combat.get("dcMin", 0), p.combat.get("dcMax", 0)
    if hi <= lo:
        return lo
    luck = getattr(p, "luck", 0) or 0
    if luck > 0 and _rand(10 - min(9, luck)) == 0:
        return hi
    if luck < 0 and _rand(10 - max(0, -luck)) == 0:
        return lo
    return lo + _rand(hi - lo + 1)


def sword_second_dam(p, dam: int, magic_id: int, divisor: int) -> int:
    level = skill_level(p, magic_id)
    return max(1, java_round(dam / divisor * (level + 2)))


def green_poison_bonus(target, dmg: int) -> int:
    if getattr(target, "poison_armor_until", 0) > now_ms():
        return java_round(dmg * 1.2)
    return dmg


def hit_check(p, m) -> bool:
    """近战命中判定（原版 ``TBaseObject._Attack``，ObjBase.pas:22240-22247）。

    命中完全由**服务端**掷骰：命中点 / 敏捷点在 ``world.hit_point`` /
    ``world.speed_point`` 里按原版公式算（玩家 5 + 准确 div 职业 tick + 剑术技能
    加成；怪物直接取 Monster.DB 的 Hit / Speed）。客户端只播动作和飘伤害数字。
    """
    return melee_hit(p, m)


class CombatHandlers:
    """Mixin：依赖 ``self.world`` / ``self.find_by_session``。"""

    # ---- 目标查找 / 广播 ------------------------------------------------

    def _find_target_at(self, game_map, x: int, y: int):
        mon = pl = None
        for a in self.world.nearby(game_map, x, y, 0):
            if mon is None and isinstance(a, Monster):
                mon = a
            elif pl is None and isinstance(a, Player):
                pl = a
        return mon if mon is not None else pl

    def _blocked_point(self, p, target):
        if target is None or p.map is None:
            return None
        sx, sy = p.x, p.y
        for _ in range(12):
            if sx == target.x and sy == target.y:
                return None
            d = direction_from(sx, sy, target.x, target.y)
            dx, dy = direction_delta(d)
            nx, ny = sx + dx, sy + dy
            if not self.world.can_fire_fly(p.map, nx, ny):
                return sx, sy
            sx, sy = nx, ny
        return None

    @staticmethod
    def _hate(m, p) -> None:
        """受击记仇：原版 ``TAnimalObject.Struck(hiter)`` 会 ``SetTargetCreat(hiter)``。

        中立怪（race 51/52/80，原版 ``TMonster``）的 Run 里没有 SearchTarget，
        **只有这条路径能让它们拿到目标**——不写 target_id 就变成「打不还手」。
        """
        m.last_hiter_id = p.id
        m.target_id = p.id

    def _broadcast_magic_hit(self, p, m, damage: int, magic_id: int) -> None:
        w = self.world
        if damage > 0:
            self._hate(m, p)
            # 原版 ObjBase.pas:4515-4517：魔法伤害对 m_boAnimal 扣
            # m_nMeatQuality（nDamage*1000），魔法打死的动物肉品质极低。
            if isinstance(m, Monster) and m.meat_quality > 0:
                m.meat_quality = max(0, m.meat_quality - damage * 1000)
        w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                                   "targetId": m.id, "damage": damage,
                                   "magicId": magic_id, "x": m.x, "y": m.y},
                           m.x, m.y, SIGHT_RANGE)
        w.send_stats(m)

    def _broadcast_physical_hit(self, p, m, damage: int) -> None:
        w = self.world
        if damage > 0:
            self._hate(m, p)
        w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                                   "targetId": m.id, "damage": damage,
                                   "x": m.x, "y": m.y}, m.x, m.y, SIGHT_RANGE)
        w.send_stats(m)

    def _broadcast_struck(self, p, m, damage: int) -> None:
        w = self.world
        if damage > 0:
            self._hate(m, p)
        w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                                   "targetId": m.id, "damage": damage,
                                   "x": m.x, "y": m.y, "noAttackAnim": True},
                           m.x, m.y, SIGHT_RANGE)
        w.send_stats(m)

    def _grant_exp(self, p, m) -> None:
        """击杀经验结算（原版 ObjBase.pas:20809 死亡 → CalcGetExp → GainExp）。

        1. 先过 ``CalcGetExp``（ObjBase.pas:2250）等级惩罚：玩家等级 ≥ 怪等级+10
           时每超 1 级扣 1/15，保底 1（``boHighLevelKillMonFixExp`` 默认 False，
           惩罚生效）。
        2. 组队走 ``TPlayObject.GainExp``（ObjBase.pas:15557-15603）：有效成员
           （同图、非死亡、|dx|/|dy|≤12，源码两处笔误都写成 dx，按意图取 dy，
           等效切比雪夫 ≤12）n>1 时经验 ×bonus[n] 后**按等级比例**分配——
           不是平分。
        """
        w = self.world
        # 经验取**怪物实例**值（原版 m_dwFightExp）：race 96 复活僵尸每次原地
        # 复活会 ``m_dwFightExp := m_dwFightExp div 2``（ObjMon.pas:1305），
        # 读模板值会让复活过的僵尸仍按原始经验结算。
        exp = m.exp
        mon_level = m.template.level if m.template else 1
        exp = calc_get_exp(p.level, mon_level, exp)
        # 统一倍率表：击杀经验（config/game_rates.json 的 exp，按怪物可覆盖）
        exp = w.kill_exp(m.template.name if m.template else m.name, exp)
        party = w.party_of(p.id)
        if party is not None and len(party.member_ids) > 1:
            nearby = []
            for pid in party.member_ids:
                a = w.actor(pid)
                if (isinstance(a, Player) and a.alive and a.map is p.map
                        and a.distance_to(p) <= 12):
                    nearby.append(a)
            n = len(nearby)
            if n > 1:
                total = round(exp * PARTY_EXP_BONUS[min(n, GROUPMAX)])
                sumlv = sum(mp.level for mp in nearby)
                if sumlv > 0:
                    for mp in nearby:
                        w.add_exp(mp, round(total / sumlv * mp.level))
                    return
        w.add_exp(p, exp)

    def _kill_if_dead(self, p, m, now: int) -> None:
        if m.hp <= 0:
            self.world.kill_monster(m, now, p)
            self._grant_exp(p, m)

    # ---- 消息入口：普通攻击 --------------------------------------------

    def _attack(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        target = self.world.actor(_int(msg, "targetId", -1))
        if target is None or not target.alive:
            return
        # 原版 IsProperTarget（ObjBase.pas:21508-21513）：玩家打有主的怪，
        # 主人 = 自己 且攻击模式 ≠ 全体攻击(HAM_ALL) → False。本服没有
        # 攻击模式开关 → 打自家宝宝一律无效（客户端本就不该发，这里兜底）。
        if isinstance(target, Monster) and target.master_id == p.id:
            return
        dist = p.distance_to(target)
        if isinstance(target, Player):
            if dist <= 1:
                self._attack_player(p, target)
            elif dist == 2 and p.long_hit_on:
                self._sword_long_hit_player(p, target)
            return
        if not isinstance(target, Monster):
            return
        if dist <= 1:
            self._attack_monster(p, target)
        elif dist == 2 and p.long_hit_on:
            self._sword_long_hit_monster(p, target)

    def _attack_monster(self, p, m) -> None:
        w = self.world
        if p.distance_to(m) > 1:
            return
        p.dir = direction_from(p.x, p.y, m.x, m.y)
        p.target_id = m.id
        w.broadcast_move(p)
        if not hit_check(p, m):
            # 原版未命中：SM_HIT 照发（有挥击动作），但 nPower := 0 不触发
            # StruckDamage → 目标端不播受击、不飘数字（此处额外广播一条
            # miss 供客户端飘 MISS，纯视觉提示，不参与数值）。
            w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                             "targetId": m.id, "miss": True, "dir": p.dir,
                             "x": m.x, "y": m.y},
                              m.x, m.y, SIGHT_RANGE)
            return
        dam = roll_dc_with_luck(p)
        sword_hit, bonus = self._sword_flag(p, dam, False)
        if sword_hit in (26, 7):
            dam += bonus
        dmg = max(1, dam - (m.template.ac if m.template else 0))
        dmg = green_poison_bonus(m, dmg)
        m.hp = max(0, m.hp - dmg)
        # 原版 _Attack 结尾（ObjBase.pas:22396/22255）：命中且造成伤害（nPower>0）
        # 才磨损武器，磨损量 Random(5)+2 - m_AddAbil.btWeaponStrong（武器 Strong）。
        wear = random.randint(2, 6) - self._weapon_strong(p)
        if wear > 0:
            w.do_damage_weapon(p, wear)
        # 原版 ObjBase.pas:2807-2811 TBaseObject.Struck：动物每次被打扣
        # m_nMeatQuality Random(300)——砍得越狠肉越差。
        if m.meat_quality > 0:
            m.meat_quality = max(0, m.meat_quality - random.randint(0, 299))
        self._hate(m, p)          # 命中才记仇：未命中原版不触发 Struck
        if p.suck_health_rate > 0:
            heal = java_div(dmg * p.suck_health_rate, 100)
            if heal > 0:
                p.hp = min(p.max_hp, p.hp + heal)
        if p.paralysis_weapon and random.random() < 0.15:
            m.paralyzed_until = now_ms() + 2000
            w.send_system(p, f"麻痹了 {m.name}！")
        o = {"type": "attack", "attackerId": p.id, "targetId": m.id,
             "damage": dmg, "x": m.x, "y": m.y}
        if sword_hit:
            o["swordHit"] = sword_hit
        w.broadcast_around(m.map, o, m.x, m.y, SIGHT_RANGE)
        w.send_stats(m)
        now = now_ms()
        self._train_sword_skills(p, sword_hit)   # 基本剑术/攻杀剑术修炼点
        if sword_hit == 12:
            self._sword_long_attack(p, sword_second_dam(p, dam, 12, 5), m)
        elif sword_hit == 25:
            self._sword_wide_attack(p, sword_second_dam(p, dam, 25, 13), m)
        self._kill_if_dead(p, m, now)

    def _sword_flag(self, p, dam_ref: int, is_player: bool) -> int:
        """烈火 / 攻杀判定并返回 swordHit 标记；调用方自行把加成写回伤害。"""
        w = self.world
        if p.fire_hit_on:
            lv = skill_level(p, 26)
            bonus = java_div(dam_ref * (4 + lv * 4), 10)
            p.fire_hit_on = False
            w.send_system(p, "烈火！")
            return 26, bonus
        if has_skill(p, 7):
            if p.power_hit_count <= 0:
                lv = skill_level(p, 7)
                p.power_hit_count = 7 - lv
                p.power_hit_point = int(random.random() * p.power_hit_count)
            p.power_hit_count -= 1
            if p.power_hit_count == p.power_hit_point:
                p.power_hit_count = 0
                w.send_system(p, "攻杀！")
                return 7, 5 + skill_level(p, 7)
        if p.wide_hit_on:
            return 25, 0
        if p.long_hit_on:
            return 12, 0
        return 0, 0

    def _is_teammate(self, p, target) -> bool:
        """同队玩家不可互相攻击（原版 HAM_GROUP，Grobal2.pas:580）。
        本服无攻击模式切换 UI，直接用「同队互助」兜底。"""
        party = self.world.party_of(p.id)
        return party is not None and target.id in party.member_ids

    def _pvp_block_reason(self, p, target) -> str:
        """原版 CheckAttackRule2（ObjBase.pas:7917-7935）的等级压制：
        高级红名（Lv>10, PKLevel>=2）不能攻击低级（Lv<=10）非红名，反之亦然。
        安全区（:7922）与换图 3 秒保护（:7938）本项目无对应状态，暂不实现。
        """
        if self._is_teammate(p, target):
            return "不能攻击队友。"
        me_red = p.pk_point >= RED_NAME_PK_POINT
        tg_red = target.pk_point >= RED_NAME_PK_POINT
        if me_red and p.level > 10 and target.level <= 10 and not tg_red:
            return "对方是低级玩家，不能攻击。"
        if not me_red and p.level <= 10 and tg_red and target.level > 10:
            return "对方等级比你高，无法攻击。"
        return ""

    def _attack_player(self, p, target) -> None:
        w = self.world
        if p.attack_mode == 0:
            w.send_system(p, "和平模式下无法攻击玩家。")
            return
        block = self._pvp_block_reason(p, target)
        if block:
            w.send_system(p, block)
            return
        if p.distance_to(target) > 1:
            return
        p.dir = direction_from(p.x, p.y, target.x, target.y)
        p.target_id = target.id
        w.broadcast_move(p)
        if not hit_check(p, target):
            # 原版 _Attack 对玩家目标同样掷命中（目标命中点 > 0 才可能被闪避）
            w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                             "targetId": target.id, "miss": True, "dir": p.dir,
                             "x": target.x, "y": target.y},
                              target.x, target.y, SIGHT_RANGE)
            return
        raw = roll_dc_with_luck(p)
        sword_hit, bonus = self._sword_flag(p, raw, True)
        # 原版 Inc(nPower, m_nHitPlus) 发生在扣 AC 之前（ObjBase.pas:22122-22133）
        if sword_hit in (26, 7):
            raw += bonus
        dmg = max(1, raw - target.combat.get("acMax", 0))
        target.hp = max(0, target.hp - dmg)
        wear = random.randint(2, 6) - self._weapon_strong(p)
        if wear > 0:
            w.do_damage_weapon(p, wear)   # 原版 _Attack 对 PvP 同样磨损（:22396）
        w.wear_struck(target, dmg, attacker=p)   # StruckDamage（ObjBase.pas:22261）
        o = {"type": "attack", "attackerId": p.id, "targetId": target.id,
             "damage": dmg, "x": target.x, "y": target.y}
        if sword_hit:
            o["swordHit"] = sword_hit
        w.broadcast_around(target.map, o, target.x, target.y, SIGHT_RANGE)
        w.send_stats(target)
        self._train_sword_skills(p, sword_hit)   # 原版 _Attack 对 PvP 同样加点
        if sword_hit == 12:
            self._sword_long_attack(p, sword_second_dam(p, raw, 12, 5), target)
        elif sword_hit == 25:
            self._sword_wide_attack(p, sword_second_dam(p, raw, 25, 13), target)
        if target.hp <= 0:
            w.kill_player(target, p)

    def _sword_long_hit_monster(self, p, m) -> None:
        w = self.world
        d = direction_from(p.x, p.y, m.x, m.y)
        dx, dy = direction_delta(d)
        if p.x + dx * 2 != m.x or p.y + dy * 2 != m.y:
            return
        p.dir = d
        w.broadcast_move(p)
        dam = roll_dc_with_luck(p)
        fx, fy = p.x + dx, p.y + dy
        front = self._find_target_at(p.map, fx, fy)
        now = now_ms()
        if isinstance(front, Monster) and front.alive:
            if hit_check(p, front):
                dmg = max(1, dam - (front.template.ac if front.template else 0))
                front.hp = max(0, front.hp - dmg)
                self._broadcast_physical_hit(p, front, dmg)
                self._kill_if_dead(p, front, now)
        if not hit_check(p, m):
            # 原版未命中：SM_HIT 照发（有挥击动作），但 nPower := 0 不触发
            # StruckDamage → 目标端不播受击、不飘数字。
            w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                             "targetId": m.id, "miss": True, "dir": p.dir,
                             "x": m.x, "y": m.y},
                              m.x, m.y, SIGHT_RANGE)
            return
        second = sword_second_dam(p, dam, 12, 5)
        m.hp = max(0, m.hp - second)
        self._hate(m, p)
        w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                                   "targetId": m.id, "damage": second,
                                   "x": m.x, "y": m.y, "swordHit": 12},
                           m.x, m.y, SIGHT_RANGE)
        w.send_stats(m)
        self._train_sword_skills(p, 12)      # 刺杀主目标命中（原版 _Attack 统一加点）
        self._kill_if_dead(p, m, now)

    def _sword_long_hit_player(self, p, target) -> None:
        w = self.world
        if p.attack_mode == 0:
            w.send_system(p, "和平模式下无法攻击玩家。")
            return
        block = self._pvp_block_reason(p, target)
        if block:
            w.send_system(p, block)
            return
        d = direction_from(p.x, p.y, target.x, target.y)
        dx, dy = direction_delta(d)
        if p.x + dx * 2 != target.x or p.y + dy * 2 != target.y:
            return
        p.dir = d
        w.broadcast_move(p)
        dam = roll_dc_with_luck(p)
        front = self._find_target_at(p.map, p.x + dx, p.y + dy)
        if isinstance(front, Player) and front.alive and hit_check(p, front):
            dmg = max(1, dam - front.combat.get("acMax", 0))
            front.hp = max(0, front.hp - dmg)
            w.wear_struck(front, dmg, attacker=p)   # 原版 DirectAttack → StruckDamage（:21980）
            w.broadcast_around(front.map, {"type": "attack", "attackerId": p.id,
                                           "targetId": front.id, "damage": dmg,
                                           "x": front.x, "y": front.y},
                               front.x, front.y, SIGHT_RANGE)
            w.send_stats(front)
            if front.hp <= 0:
                w.kill_player(front, p)
        if not hit_check(p, target):
            w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                             "targetId": target.id, "miss": True, "dir": p.dir,
                             "x": target.x, "y": target.y},
                              target.x, target.y, SIGHT_RANGE)
            return
        second = sword_second_dam(p, dam, 12, 5)
        target.hp = max(0, target.hp - second)
        w.wear_struck(target, second, attacker=p)   # 原版 DirectAttack → StruckDamage
        w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                                        "targetId": target.id, "damage": second,
                                        "x": target.x, "y": target.y, "swordHit": 12},
                           target.x, target.y, SIGHT_RANGE)
        w.send_stats(target)
        self._train_sword_skills(p, 12)      # 刺杀主目标命中（原版 _Attack 统一加点）
        if target.hp <= 0:
            w.kill_player(target, p)

    def _sword_long_attack(self, p, second: int, exclude) -> None:
        dx, dy = direction_delta(p.dir)
        nb = self._find_target_at(p.map, p.x + dx * 2, p.y + dy * 2)
        if nb is not None and nb is not exclude and nb.alive:
            self._apply_sword_hit(p, nb, second)

    def _sword_wide_attack(self, p, second: int, exclude) -> None:
        for d in ((p.dir + 7) % 8, (p.dir + 1) % 8, (p.dir + 2) % 8):
            dx, dy = direction_delta(d)
            nb = self._find_target_at(p.map, p.x + dx, p.y + dy)
            if nb is not None and nb is not exclude and nb.alive:
                self._apply_sword_hit(p, nb, second)

    def _apply_sword_hit(self, p, target, second: int) -> None:
        """刺杀/半月扫到的副目标（原版 SwordLongAttack / SwordWideAttack →
        DirectAttack，ObjBase.pas:21986-22010）：**每个目标单独掷命中**
        （``Random(目标敏捷) < 命中`` 正向写法），未命中不出伤害。
        """
        w = self.world
        if not positive_hit(p, target):
            return
        if isinstance(target, Monster):
            second = green_poison_bonus(target, second)
            target.hp = max(0, target.hp - second)
            self._broadcast_physical_hit(p, target, second)
            self._kill_if_dead(p, target, now_ms())
        elif isinstance(target, Player):
            if p.attack_mode == 0 or self._pvp_block_reason(p, target):
                return
            target.hp = max(0, target.hp - second)
            # 原版 DirectAttack 副目标同样走 StruckDamage（ObjBase.pas:21980）
            w.wear_struck(target, second, attacker=p)
            w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                                            "targetId": target.id, "damage": second,
                                            "x": target.x, "y": target.y},
                               target.x, target.y, SIGHT_RANGE)
            w.send_stats(target)
            if target.hp <= 0:
                w.kill_player(target, p)

    # ---- 消息入口：方向攻击 --------------------------------------------

    def _dir_attack(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        d = _int(msg, "dir", -1)
        if d < 0 or d > 7:
            return
        p.dir = d
        w.broadcast_move(p)
        dx, dy = direction_delta(d)
        fx, fy = p.x + dx, p.y + dy
        # 原版 ObjBase.pas:8819-8847：CM_HEAVYHIT（StdMode 6 武器一律发重击，
        # ClMain.pas:2696）且武器耐久 > 0 时，若前方格不可走（石壁/矿堆）且
        # 武器 Shape=19（鹤嘴锄）→ PileStones 挖矿并 Exit（不进 AttackDir）；
        # 其他情况照常 AttackDir 挥砍。
        wt = self._weapon_template(p)
        if (wt is not None and wt.stdMode == 6 and wt.shape == 19
                and p.map.inside(fx, fy) and not p.map.can_walk(fx, fy)
                and self._weapon_dura(p) > 0):
            self._pile_stones(p, dx, dy)
            return
        front = self._find_target_at(p.map, fx, fy)
        if isinstance(front, Monster) and front.alive:
            self._attack_monster(p, front)
            return
        if isinstance(front, Player) and front.alive:
            self._attack_player(p, front)
            return
        raw = roll_dc_with_luck(p)
        sword_hit = 25 if p.wide_hit_on else (12 if p.long_hit_on else 0)
        if sword_hit == 12:
            self._sword_long_attack(p, sword_second_dam(p, raw, 12, 5), None)
        elif sword_hit == 25:
            self._sword_wide_attack(p, sword_second_dam(p, raw, 25, 13), None)
        o = {"type": "attack", "attackerId": p.id, "targetId": 0,
             "damage": 0, "x": fx, "y": fy}
        if sword_hit:
            o["swordHit"] = sword_hit
        w.broadcast_around(p.map, o, p.x, p.y, SIGHT_RANGE)

    # ---- 技能辅助 -------------------------------------------------------

    # ---- 挖矿 -----------------------------------------------------------

    def _weapon_template(self, p):
        """当前装备武器模板（无武器 → None）。"""
        if not p.equipment:
            return None
        it = p.equipment[U_WEAPON]
        if not it:
            return None
        return self.world.item_templates.get(int(it.get("index", 0)))

    def _weapon_dura(self, p) -> int:
        it = (p.equipment[U_WEAPON] if p.equipment else None) or {}
        return int(it.get("durability", 0) or 0)

    def _weapon_strong(self, p) -> int:
        """原版 ``m_AddAbil.btWeaponStrong``：武器的 Strong 属性（本项目
        ItemTemplate.strong，176 数据一般为 0）。"""
        t = self._weapon_template(p)
        return int(getattr(t, "strong", 0) or 0) if t is not None else 0

    def _damage_weapon(self, p, amount: int) -> None:
        """武器磨损入口（统一走 GameWorld.do_damage_weapon：原版
        DoDamageWeapon 负责节流下发 SM_DURACHANGE 与到 0 损毁清槽）。"""
        self.world.do_damage_weapon(p, amount)

    def _pile_stones(self, p, dx: int, dy: int) -> None:
        """原版 ``TPlayObject.PileStones``（ObjBase.pas:21881-21925）逐行直译。

        前方格必须有 TStoneMineEvent（MINE/MINE2 地图每格都建，这里惰性
        登记 ``world.mine_state``，创建时存量 ``Random(200)``）。每锄：
        存量 -1；``Random(4)=0``（nMakeMineHitRate）命中 → 武器磨损
        ``Random(15)+5``；命中且 ``Random(12)=0``（nMakeMineRate）→ MakeMine
        出矿石。存量挖空后 10 分钟补 ``Random(80)``（AddStoneMine，Event.pas:352）。
        命中原版还会在自己脚下建 ET_PILESTONES 碎石堆视觉事件（5 分钟），
        本项目暂无事件渲染层，略。
        """
        w = self.world
        mt = w.map_mine_type(p.map.name)
        # RM_HEAVYHIT 广播（:21924）：无论是否命中都广播挥锄动作。
        w.broadcast_around(p.map, {"type": "attack", "attackerId": p.id,
                                   "targetId": 0, "damage": 0,
                                   "x": p.x + dx, "y": p.y + dy},
                           p.x, p.y, SIGHT_RANGE)
        if mt is None:
            return
        key = (p.map.name, p.x + dx, p.y + dy)
        st = w.mine_state.get(key)
        now = now_ms()
        if st is None:
            # TStoneMineEvent.Create（Event.pas:84-94）：存量 Random(200)
            st = w.mine_state[key] = [random.randrange(200), now]
        if st[0] > 0:
            st[0] -= 1
            if random.randrange(4) == 0:
                self._damage_weapon(p, random.randint(5, 19))   # Random(15)+5
                if random.randrange(12) == 0:
                    self._make_mine(p, mt)
        elif now - st[1] > 600_000:
            # AddStoneMine（Event.pas:352-356）：补 m_nAddStoneCount = Random(80)
            st[0] = random.randrange(80)
            st[1] = now

    def _make_mine(self, p, mt: str) -> None:
        """原版 ``TPlayObject.MakeMine``（ObjBase.pas:24351-24474）。

        ``Random(120)`` 定矿种：1~2 金矿 / 3~20 银矿 / 21~45 铁矿 /
        46~56 黑铁矿石 / 其余铜矿（M2Share.pas:1960+ 默认配置）；
        耐久（纯度）RandomDrua = Random(13000)+3000、1/20 再 +Random(10000)。
        背包满直接挖不到（:24364 Exit；8836 提示背包已满）。
        MINE2（宝石矿区，sGemStone1..4）本项目 MapInfo 无配置，按同一档位
        映射到红宝石/绿宝石矿石（仅兜底，实际不可达）。
        """
        w = self.world
        if len(p.bag) >= BAG_SIZE:
            w.send_system(p, "背包已满，无法携带更多的东西")
            return
        roll = random.randrange(120)
        if mt == "mine2":
            name = ("红宝石矿石" if roll <= 2
                    else "绿宝石矿石" if roll <= 45 else "绿宝石矿石")
        elif roll <= 2:
            name = "金矿"
        elif roll <= 20:
            name = "银矿"
        elif roll <= 45:
            name = "铁矿"
        elif roll <= 56:
            name = "黑铁矿石"
        else:
            name = "铜矿"
        t = w.item_template_by_name(name)
        if t is None:
            return
        it = w.new_item_instance(t, random_mine_dura())
        p.bag.append(it)
        self.send_bag_update(p)

    # ---- 技能修炼（原版 TrainSkill / CheckMagicLevelup）-------------------

    def _can_train(self, p, magic_id: int) -> bool:
        """原版调用点前置条件：``btLevel < 3`` 且 ``TrainLevel[btLevel] <= 人物等级``。

        TrainLevel 就是 Magic.DB 的 NeedLevel（本项目 MagicTemplate.needLevel），
        所以 0 级练满点数后还要人物等级够才继续涨点（攻杀 19/22/24 级）。
        """
        t = self.world.magic_templates.get(magic_id)
        if t is None or magic_id not in p.skills:
            return False
        skill = p.skills[magic_id]
        lv = int(skill.get("level", 0))
        if lv >= MAX_MAGIC_LEVEL:
            return False
        need_lv = t.needLevel[lv] if lv < len(t.needLevel) else 0
        return need_lv <= p.level

    def _train_skill(self, p, magic_id: int, points: int) -> bool:
        """原版 ``TBaseObject.TrainSkill`` + ``CheckMagicLevelup``（ObjBase.pas:21817/21825）。

        返回是否升级。升级时下发 skills（等价原版 RM_MAGIC_LVEXP 之后的 SendAddMagic）。
        """
        w = self.world
        t = w.magic_templates.get(magic_id)
        skill = p.skills.get(magic_id)
        if t is None or skill is None:
            return False
        skill["train"] = int(skill.get("train", 0)) + points
        lv = int(skill.get("level", 0))
        need = t.maxTrain[lv] if lv < len(t.maxTrain) else 0
        if lv < MAX_MAGIC_LEVEL and need > 0 and int(skill["train"]) >= need:
            skill["train"] = int(skill["train"]) - need
            skill["level"] = lv + 1
            w.send_system(p, f"{p.name} 的 {t.name} 技能修炼成功！！！")
            w.send_stats(p, {"skills": p.skills_json(w)})
            return True
        # 未升级也要让客户端技能页的「CurTrain/MaxTrain」跟着走。原版是
        # SendDelayMsg(RM_MAGIC_LVEXP, ..., 3000) 异步推 CurTrain（ObjBase.pas:22294），
        # 这里用 1 秒节流直接带在 stats 里，避免每刀一条消息。
        now = now_ms()
        if now - p.skill_lvexp_at >= 1000:
            p.skill_lvexp_at = now
            w.send_stats(p, {"skills": p.skills_json(w)})
        return False

    def _train_sword_skills(self, p, sword_hit: int) -> None:
        """物理攻击命中后的剑术修炼（原版 ObjBase.pas:22283-22365）。

        原版在 ``_Attack`` 里 nPower > 0（已命中）之后按 hitMode 分别加点：
        - 基本剑术（3）：**每次**命中 ``TrainSkill(Random(3)+1)``；
        - 攻杀剑术（7）：只在 ``bo21``（攻杀/烈火发动的那一击）``Random(3)+1``；
        - 刺杀剑术（12, hitMode=4）／半月弯刀（25, hitMode=5）／
          烈火剑法（26, hitMode=7）：每次命中 ``TrainSkill(1)``。
        都要 ``btLevel < 3`` 且 TrainLevel[btLevel] <= 人物等级。
        刺杀/半月的**副目标**不加点（原版只在主目标结算处调用）。
        """
        if self._can_train(p, 3):
            self._train_skill(p, 3, random.randint(1, 3))
        if sword_hit in (26, 7) and self._can_train(p, 7):   # bo21
            self._train_skill(p, 7, random.randint(1, 3))
        for mid in (12, 25, 26):
            if sword_hit == mid and self._can_train(p, mid):
                self._train_skill(p, mid, 1)

    def _finish_cast(self, p, t, skill: dict, lv: int, now: int,
                     cost: int, success: bool) -> None:
        w = self.world
        p.mp -= cost
        p.skill_cooldown[t.magId] = now
        extras = None
        if success and skill.get("level", 0) < MAX_MAGIC_LEVEL:
            # 原版 Magic.pas:722-728：施法成功且 TrainLevel[btLevel] <= 人物等级
            # 才 TrainSkill(Random(3)+1)，升级时 SendAddMagic 刷新客户端。
            if self._train_skill(p, t.magId, random.randint(1, 3)):
                extras = {"skills": p.skills_json(w)}
        w.send_stats(p, extras)

    def _pass_through_magic(self, p, ndir: int, rng: int, pwr: int,
                            undead_attack: bool, magic_id: int, now: int) -> int:
        """原版 ``MagPassThroughMagic``（ObjBase.pas:2536）：直线穿透，最多 13 格。

        每格取该格的移动对象，命中闸门是 ``Random(10) >= m_nAntiMagic``（不是
        按 MAC 闪避），伤害走 ``RM_MAGSTRUCK`` 直接结算（扣 MAC，不除 1.2）。
        疾光电影（10）的 undeadattack 在原版是「每命中一个 ×1.5 并累乘」。
        """
        hit = 0
        # 调用方先 GetNextPosition(..., rng) 得到终点格，再由此往回逐格走
        dx, dy = direction_delta(ndir)
        ex, ey = p.x + dx * rng, p.y + dy * rng
        x, y = p.x, p.y
        cur_pwr = pwr
        for _ in range(13):
            step = direction_toward(x, y, ex, ey, 0)
            sdx, sdy = direction_delta(step)
            x, y = x + sdx, y + sdy
            if not p.map.inside(x, y):
                break
            a = self._find_target_at(p.map, x, y)
            if isinstance(a, Monster) and a.alive and magic_hits(a):
                # 原版这里是把 magpwr 变量本身 ×1.5（ObjBase.pas:2550-2552）：
                # 不分目标是否不死系，而且**逐次累乘**（3 个目标 = 1.5³）。看着像
                # 原版的笔误，但疾光电影（10）穿过一排怪越打越疼就是这个原因，
                # 按「还原原版」的要求照抄。
                if undead_attack:
                    cur_pwr = delphi_round(cur_pwr * 1.5)
                if self._direct_magic(p, a, cur_pwr, magic_id, now) > 0:
                    hit += 1
            if x == ex and y == ey:
                break
        return hit

    def _push_creature(self, m, d: int, push: int) -> bool:
        w = self.world
        dx, dy = direction_delta(d)
        x, y = m.x, m.y
        for _ in range(push):
            nx, ny = x + dx, y + dy
            if not m.map.can_walk(nx, ny) or m.map.occupied(nx, ny):
                break
            x, y = nx, ny
        if x != m.x or y != m.y:
            m.map.remove(m.id)
            m.x, m.y = x, y
            m.map.add(m)
            w.broadcast_move(m)
            return True
        return False

    def _push_around(self, p, level: int) -> int:
        pushed = 0
        for a in self.world.nearby(p.map, p.x, p.y, 1):
            if (not isinstance(a, Monster) or not a.alive
                    or p.level <= a.level or a.master_id == p.id):
                continue
            gap = p.level - a.level
            if random.random() * 20 >= 6 + level * 3 + gap:
                continue
            push = 1 + max(0, level - 1) + int(random.random() * 2)
            self._push_creature(a, direction_from(p.x, p.y, a.x, a.y), push)
            pushed += 1
        return pushed

    def _rush_rush(self, p, dir_value: int, lv: int) -> bool:
        w = self.world
        ndir = dir_value if 0 <= dir_value <= 7 else p.dir
        p.dir = ndir
        damage_level = lv + 1
        attack_cret = None
        pushed = 0
        max_steps = max(2, lv + 1)
        dx, dy = direction_delta(ndir)
        for _ in range(max_steps):
            cx, cy = p.x + dx, p.y + dy
            a = self._find_target_at(p.map, cx, cy)
            if (not isinstance(a, Monster) or not a.alive
                    or p.level <= a.level or a.master_id == p.id):
                break
            gap = p.level - a.level
            if random.random() * 20 >= 6 + lv * 3 + gap:
                break
            attack_cret = a
            if not self._push_creature(a, ndir, 1):
                break
            if w.try_move(p, ndir):
                pushed += 1
                damage_level -= 1
            else:
                break
        if attack_cret is None:
            for _ in range(max_steps):
                if not w.try_move(p, ndir):
                    break
                pushed += 1
        w.broadcast_rush(p, max(1, pushed))
        if attack_cret is not None:
            damage_level = max(0, damage_level)
            damage = ((1 + damage_level) * 4
                      + int(random.random() * ((1 + damage_level) * 5)))
            damage = max(1, damage - (attack_cret.template.ac if attack_cret.template else 0))
            attack_cret.hp = max(0, attack_cret.hp - damage)
            self._broadcast_struck(p, attack_cret, damage)
            self._kill_if_dead(p, attack_cret, now_ms())
        w.send_system(p, f"野蛮冲撞击退了 {pushed} 格。" if attack_cret is not None
                      else "你向前冲撞。")
        return True

    def _random_teleport(self, p, lv: int) -> bool:
        """原版瞬息移动（Magic.pas:951 ``MagSaceMove``）。

        ``if Random(11) < nLevel*2 + 4`` 才成功（0 级 4/11≈36%，3 级 10/11），
        成功后 ``MapRandomMove(m_sHomeMap, 1)``：**不是原地小范围闪避**，而是
        「家地图」上的随机点（``ObjBase.pas:9810``，边距按地图高度取 2/20/50）。
        本项目 ``home_map`` 即角色出生/回城点，语义一致。
        """
        w = self.world
        if random.randrange(11) >= lv * 2 + 4:
            return False
        gm = w.get_or_load_map(p.home_map) or p.map
        if gm is None:
            return False
        edge = 2 if gm.height < 30 else (20 if gm.height < 150 else 50)
        for _ in range(60):
            nx = random.randrange(max(1, gm.width - edge - 1)) + edge
            ny = random.randrange(max(1, gm.height - edge - 1)) + edge
            if not gm.can_walk(nx, ny) or gm.occupied(nx, ny):
                continue
            w.space_move(p, gm.name, nx, ny, variant=1)
            w.space_move_show(p, 1)
            return True
        return False

    # ---- 护身符 / 毒药粉（原版 CheckAmulet / UseAmulet，Magic.pas:81-144）----
    #
    # 原版只认**已装备**的两个槽：手镯位（U_ARMRINGL）与符位（U_BUJUK）。
    # 护身符 = StdMode 25 且 Shape=5，每张 100 点耐久；毒药粉 = StdMode 25
    # 且 Shape<=2（灰色=1 红毒 / 黄色=2 绿毒）。施法按张数扣除耐久，扣光后
    # 装备消失。

    def _find_amulet(self, p, kind: int, count: int):
        w = self.world
        for slot in (U_ARMRINGL, U_BUJUK):
            it = p.equipment[slot] if slot < len(p.equipment) else None
            t = w.item_templates.get(int(it.get("index", 0))) if it else None
            if t is None or t.stdMode != 25:
                continue
            ok = (t.shape == 5) if kind == 1 else (t.shape <= 2)
            if not ok:
                continue
            if int(it.get("durability", 0)) // 100 >= count:
                return slot, it, t
        return None

    def _use_amulet(self, p, slot: int, item: dict, count: int) -> None:
        """``UseAmulet``：扣 ``count*100`` 耐久，不足则整件消失（含重算与下发）。"""
        w = self.world
        dura = int(item.get("durability", 0) or 0)
        if dura > count * 100:
            item["durability"] = dura - count * 100
        else:
            item["durability"] = 0
            p.equipment[slot] = None
            p.recompute(w)
            w.send_stats(p)
        self.send_bag_update(p)

    # ---- 消息入口：施法 ------------------------------------------------

    def _cast_magic(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        magic_id = _int(msg, "magicId", -1)
        tx, ty = _int(msg, "targetX", -1), _int(msg, "targetY", -1)
        target_id = _int(msg, "targetId", -1)
        t = w.magic_templates.get(magic_id)
        if t is None:
            return
        # 纯被动技能（3 基本剑术 / 4 精神力战法 / 7 攻杀剑术）：原版
        # ClientSpellXY 直接 Result := True，MagicManager.DoSpell 里 IsWarrSkill
        # 再 return 一次 —— 不扣 MP、没有任何效果，学了就常驻生效。
        if magic_id in (3, 4, 7):
            w.send_system(p, "被动技能无需施放，学会后自动生效。")
            return
        skill = p.skills.get(magic_id)
        if skill is None:
            w.send_system(p, "尚未学会该技能。")
            return
        lv = skill.get("level", 0)
        now = now_ms()
        last = p.skill_cooldown.get(magic_id)
        if last is not None and now - last < t.delay:
            return
        cost = spell_cost(t, lv)

        # ---- 开关类：原版走 ClientSpellXY 的 case 分支，不扣 MP、不施法 ---------
        if magic_id == 12:                       # 刺杀剑术 ThrustingOnOff
            p.long_hit_on = not p.long_hit_on
            p.skill_cooldown[magic_id] = now
            w.send_system(p, "刺杀剑术开启。" if p.long_hit_on else "刺杀剑术关闭。")
            return
        if magic_id == 25:                       # 半月弯刀 HalfMoonOnOff
            p.wide_hit_on = not p.wide_hit_on
            p.skill_cooldown[magic_id] = now
            w.send_system(p, "半月弯刀开启。" if p.wide_hit_on else "半月弯刀关闭。")
            return

        if p.mp < cost:
            w.send_system(p, "魔法值不足。")
            # 原版 TPlayObject.DoSpell（ObjBase.pas:21864）MP 不足 Exit=False，
            # ClientSpellXY（:9234-9237）DoSpell 失败同样广播 RM_MAGICFIREFAIL
            # ——周围人看到挥手放下，无飞行特效。
            w.broadcast_around(p.map, {"type": "spellFail", "casterId": p.id},
                               p.x, p.y, SIGHT_RANGE)
            return

        # ---- 蓄力/冲撞：ClientSpellXY 里先扣 MP 再判定（ObjBase.pas:9098/9119）----
        if magic_id == 26:                       # 烈火剑法 AllowFireHitSkill
            if now - p.last_fire_hit_at < 10_000:
                w.send_system(p, "凝聚内力失败。")
                return
            p.mp -= cost
            p.last_fire_hit_at = now
            p.fire_hit_on = True
            p.skill_cooldown[magic_id] = now
            w.send_system(p, "你的武器因精神火焰而炙热。")
            w.send_stats(p)
            return
        if magic_id == 27:                       # 野蛮冲撞 DoMotaebo（3 秒一次）
            # ObjBase.pas:9112-9150：**先**判 3 秒冷却（不满足连 MP 都不扣，
            # 也没有任何提示），满足才 DamageSpell → DoMotaebo → 修炼。
            if now - p.last_rush_rush_at < 3_000:
                return
            p.last_rush_rush_at = now
            p.mp -= cost
            p.skill_cooldown[magic_id] = now
            ok = self._rush_rush(p, tx, lv)
            if ok and lv < MAX_MAGIC_LEVEL:
                # 这里的门槛是**严格小于**（ClientSpellXY 用 `< m_Abil.Level`，
                # 与 Magic.pas:722 施法修炼的 `<=` 不同）。
                need_lv = t.needLevel[lv] if lv < len(t.needLevel) else 0
                if need_lv < p.level:
                    self._train_skill(p, magic_id, random.randint(1, 3))
            w.send_stats(p)
            return

        # ---- TPlayObject.DoSpell：先 DamageSpell，再 MagicManager.DoSpell ------
        p.mp -= cost
        p.skill_cooldown[magic_id] = now
        # 施法半径 8 格（Magic.pas:266）：|dx|/|dy| 超 8 直接放弃，MP 已经扣掉。
        if tx >= 0 and ty >= 0 and (abs(p.x - tx) > 8 or abs(p.y - ty) > 8):
            w.send_stats(p)
            # 原版 MagicManager.DoSpell（Magic.pas:266-268）半径外 exit=False →
            # ClientSpellXY 广播 RM_MAGICFIREFAIL（ObjBase.pas:9236）。
            w.broadcast_around(p.map, {"type": "spellFail", "casterId": p.id},
                               p.x, p.y, SIGHT_RANGE)
            return
        if tx >= 0 and ty >= 0 and not is_heal_magic(magic_id):
            cast_dir = direction_from(p.x, p.y, tx, ty)
            if cast_dir != p.dir:
                p.dir = cast_dir
                w.broadcast_move(p)

        target = w.actor(target_id) if target_id > 0 else None
        if target is not None and not target.alive:
            target = None
        # CretInNearXY：目标必须落在施法点 3x3 内，否则视为对地面施法（target=nil）
        if (target is not None and tx >= 0 and ty >= 0
                and (abs(target.x - tx) > 1 or abs(target.y - ty) > 1)):
            target = None
        # 原版 IsProperTarget（ObjBase.pas:21508-21513）：自家宝宝不可作攻击
        # 目标（攻击模式≠全体攻击）。目标作废 → 按对地施法处理，特效照放、
        # 无伤害，与「打空」同一软失败路径。治疗系走 IsProperFriend
        # （ObjBase.pas:24192-24196，主人=自己 → True），宝宝**可**作治疗目标，
        # 不在此豁免之列。
        if (isinstance(target, Monster) and target.master_id == p.id
                and not is_heal_magic(magic_id)):
            target = None
        # 同队/等级压制玩家不可被攻击魔法选中（原版 HAM_GROUP / CheckAttackRule2）；
        # 治疗系不受限。
        if (not is_heal_magic(magic_id) and isinstance(target, Player)
                and self._pvp_block_reason(p, target)):
            target = None
        if isinstance(target, Monster):
            p.target_id = target.id
        if is_heal_magic(magic_id):
            if target is None:
                target = p
            tx, ty = target.x, target.y

        cast_tx, cast_ty = tx, ty
        blocked = False
        if magic_id in (1, 5, 13) and isinstance(target, Monster):
            bp = self._blocked_point(p, target)
            if bp is not None:
                cast_tx, cast_ty, blocked = bp[0], bp[1], True
        # 原版 MagicManager.DoSpell（Magic.pas:270）：RM_SPELL 施法**动作**在
        # 扣完 MP、进 case 分发前无条件广播（无特效）。
        cast = {"type": "spellCast", "casterId": p.id, "magicId": magic_id}
        w.broadcast_around(p.map, cast, p.x, p.y, SIGHT_RANGE)

        # 护身符硬失败预检（原版 case 6 / 13~19 开头 ``boSpellFail := True`` +
        # CheckAmulet，Magic.pas:318-427/427：缺失 → 函数尾 :714 exit，不发
        # RM_MAGICFIRE，DoSpell=False → ClientSpellXY 广播 RM_MAGICFIREFAIL）。
        # 其余失败（打空 / 抗魔 / 没目标）原版只是 boTrain=False，特效照放。
        amulet_need = _AMULET_NEED.get(magic_id)
        if amulet_need is not None and self._find_amulet(p, *amulet_need) is None:
            if magic_id == 6:
                w.send_system(p, "没有毒药粉。")
            elif amulet_need[1] > 1:
                w.send_system(p, f"护身符不足 {amulet_need[1]} 张。")
            else:
                w.send_system(p, "没有护身符。")
            w.broadcast_around(p.map, {"type": "spellFail", "casterId": p.id},
                               p.x, p.y, SIGHT_RANGE)
            self._finish_cast(p, t, skill, lv, now, 0, False)
            return

        # 原版 ClientSpellXY（ObjBase.pas:9234-9237）：RM_MAGICFIRE 只在
        # boSpellFire（各 case，Magic.pas:496/716/1067）里发；软失败（打空 /
        # 抗魔 / 没目标）特效照放，resolve 返回值只是 boTrain（加不加熟练度）。
        trained = self._resolve_magic(p, t, skill, lv, now, magic_id,
                                      tx, ty, target, blocked)
        fire = {"type": "magicFire", "casterId": p.id, "magicId": magic_id,
                "effect": t.effect, "targetX": cast_tx, "targetY": cast_ty}
        if blocked:
            fire["blocked"] = True
        w.broadcast_around(p.map, fire, p.x, p.y, SIGHT_RANGE)
        self._finish_cast(p, t, skill, lv, now, 0, trained)

    # ------------------------------------------------------------------
    # MagicManager.DoSpell 的分支（Magic.pas:278-713），逐条对照
    # ------------------------------------------------------------------

    def _mc_range(self, p) -> tuple[int, int]:
        return p.combat.get("mcMin", 0), p.combat.get("mcMax", 0)

    def _sc_range(self, p) -> tuple[int, int]:
        return p.combat.get("scMin", 0), p.combat.get("scMax", 0)

    @staticmethod
    def _anti_poison(a) -> int:
        return int(a.combat.get("antiPoison", 0)) if isinstance(a, Player) else 0

    def _delayed_magic(self, p, target, pwr: int, magic_id: int, now: int) -> int:
        """``RM_DELAYMAGIC`` 结算（ObjBase.pas:4565）。

        先按怪物 1.2 折减（``if race >= RC_ANIMAL then nPower := Round(nPower/1.2)``），
        再进 ``RM_MAGSTRUCK`` → ``GetMagStruckDamage``（扣 MAC / 过魔法盾）。
        """
        if target is None or not target.alive:
            return 0
        # 只有 race >= RC_ANIMAL(50) 的**动物/怪物**才吃 1.2 折减；
        # 玩家 race=0（RC_PLAYOBJECT），照原版不受这条影响。
        pwr = delphi_round(pwr / 1.2) if getattr(target, "race", 0) >= RC_ANIMAL else pwr
        dmg = mag_struck_damage(target, pwr)
        if dmg <= 0:
            return 0
        target.hp = max(0, target.hp - dmg)
        self._broadcast_magic_hit(p, target, dmg, magic_id)
        self._kill_if_dead(p, target, now)
        return dmg

    def _direct_magic(self, p, target, pwr: int, magic_id: int, now: int) -> int:
        """``RM_MAGSTRUCK`` 直接结算（爆裂火焰/冰咆哮/地狱雷光/火墙）。"""
        if target is None or not target.alive:
            return 0
        dmg = mag_struck_damage(target, pwr)
        if dmg <= 0:
            return 0
        target.hp = max(0, target.hp - dmg)
        self._broadcast_magic_hit(p, target, dmg, magic_id)
        self._kill_if_dead(p, target, now)
        return dmg

    def _resolve_magic(self, p, t, skill, lv, now, magic_id,
                       tx, ty, target, blocked) -> bool:
        """castMagic 的效果分派；返回 True 表示原版的 ``boTrain``（加熟练度）。"""
        w = self.world
        mc_lo, mc_hi = self._mc_range(p)
        sc_lo, sc_hi = self._sc_range(p)

        # 1 火球术 / 5 大火球（Magic.pas:279-300）
        if magic_id in (1, 5):
            if not isinstance(target, Monster) or blocked or not magic_hits(target):
                return False
            pwr = magic_power(p, t, lv)
            self._delayed_magic(p, target, pwr, magic_id, now)
            if magic_id == 5 and p.level > target.level:
                # MagHbFireBall 的击退：Random(lv)-1 > 0 才推
                gap = p.level - target.level
                if _rand(20) < 6 + lv * 3 + gap:
                    push = _rand(lv) - 1
                    if push > 0:
                        self._push_creature(target, direction_from(p.x, p.y,
                                                                  target.x, target.y), push)
            return True

        # 2 治愈术（:301）：IsProperFriend（ObjBase.pas:24192-24196）主人=自己
        # 的宝宝恒为友方，可作治疗目标；别的怪 / 别人的宝宝不可。
        if is_heal_magic(magic_id):
            if not (isinstance(target, Player)
                    or (isinstance(target, Monster) and target.master_id == p.id)):
                return False
            if target.hp >= target.max_hp:
                return False
            heal = max(1, heal_power(p, t, lv))
            if isinstance(target, Player):
                target.inc_healing = (min(300, target.inc_healing + heal)
                                      if target.inc_healing + heal < 300 else 300)
            else:
                # 宝宝没有 inc_healing 池：原版 RM_MAGHEALING 经 SendDelayMsg
                # 800ms 延迟后即时加血（Magic.pas:316），直接加并广播 stats。
                target.hp = min(target.max_hp, target.hp + heal)
            w.send_stats(target)
            return True

        # 6 施毒术（:318-383）：毒药粉 1 张；AntiPoison 越高中毒越难
        if magic_id == 6:
            if not isinstance(target, Monster):
                w.send_system(p, "没有目标。")
                return False
            am = self._find_amulet(p, 2, 1)
            if am is None:
                w.send_system(p, "没有毒药粉。")
                return False
            slot, item, st = am
            self._use_amulet(p, slot, item, 1)
            if random.randrange(self._anti_poison(target) + 7) > 6:
                w.send_system(p, f"{target.name} 抵抗了施毒术。")
                return False
            n = get_power13(t, lv, 40 if st.shape == 1 else 30) + get_rpow(sc_lo, sc_hi) * 2
            point = delphi_round(lv / 3 * (n / 10))     # nAmyOunsulPoint = 10
            if st.shape == 1:                            # 灰色药粉 = POISON_DECHEALTH（绿毒，掉血）
                target.poison_ticks = max(1, delphi_round(n * 1000 / POISON_INTERVAL_MS))
                target.poison_damage = point + 1
                target.last_poison_at = now
                w.send_system(p, f"对 {target.name} 施放了绿毒。")
            else:                                        # 黄色药粉 = POISON_DAMAGEARMOR（红毒，降防）
                target.poison_armor_until = now + n * 1000
                w.send_system(p, f"对 {target.name} 施放了红毒。")
            w.send_stats(target)
            return True

        # 8 抗拒火环（:384 MagPushArround）
        if magic_id == 8:
            pushed = self._push_around(p, lv)
            w.send_system(p, f"推开了 {pushed} 个敌人。" if pushed > 0
                          else "周围没有可推开的敌人。")
            return pushed > 0

        # 9 地狱火 / 10 疾光电影（:387-406）：穿透直线，最多 13 格
        if magic_id in (9, 10):
            ndir = direction_from(p.x, p.y, tx, ty)
            steps = 5 if magic_id == 9 else 8
            hit = self._pass_through_magic(p, ndir, steps, magic_power(p, t, lv),
                                           magic_id == 10, magic_id, now)
            w.send_system(p, f"{t.name} 击中了 {hit} 个目标。" if hit > 0 else "没有命中目标。")
            return hit > 0

        # 11 雷电术（:407）/ 13 灵魂火符（:420-450）
        # 灵魂火符在**命中判定之前**就 CheckAmulet(1,1) + UseAmulet(1,1)：
        # 打空了护身符也照扣（Magic.pas:418-424）。
        if magic_id in (11, 13):
            if magic_id == 13:
                am = self._find_amulet(p, 1, 1)
                if am is None:
                    w.send_system(p, "没有护身符。")
                    return False
                slot, item, _st = am
                self._use_amulet(p, slot, item, 1)
            if not isinstance(target, Monster) or blocked or not magic_hits(target):
                return False
            if magic_id == 11:
                pwr = magic_power(p, t, lv)
                undead = target.template.undead if target.template else 0
                if undead == 1:
                    pwr = delphi_round(pwr * 1.5)   # 只有雷电术对不死系 ×1.5
            else:
                pwr = taoist_power(p, t, lv)        # 灵魂火符走 SC，无不死系加成
            self._delayed_magic(p, target, pwr, magic_id, now)
            return True

        # 14 幽灵盾 / 15 神圣战甲术（:451-460 MagMakeDefenceArea）
        # 同属 CheckAmulet(1,1) 的 case 块（Magic.pas:414-416），两种盾都吃 1 张护身符。
        if magic_id in (14, 15):
            am = self._find_amulet(p, 1, 1)
            if am is None:
                w.send_system(p, "没有护身符。")
                return False
            slot, item, _st = am
            self._use_amulet(p, slot, item, 1)
            n = attack_power(p, get_power13(t, lv, 60) + sc_lo * 10, (sc_hi - sc_lo) + 1)
            buffed = 0
            for nb in w.nearby(p.map, tx, ty, 3):
                if not isinstance(nb, Player) or not nb.alive:
                    continue
                # DefenceUp / MagDefenceUp：只延长不缩短，AC/MAC 上限 +2+等级/7
                if nb.ac_buff_until < now + n * 1000:
                    nb.ac_buff_until = now + n * 1000
                    if magic_id == 15:
                        nb.ac_buff_bonus = 2 + nb.level // 7
                        nb.mac_buff_bonus = 0
                    else:
                        nb.mac_buff_bonus = 2 + nb.level // 7
                        nb.ac_buff_bonus = 0
                    nb.recompute(w)
                    w.send_stats(nb)
                buffed += 1
            w.send_system(p, f"施展了 {t.name}，为 {buffed} 个目标提升防御。")
            return buffed > 0

        # 16 困魔咒（:461 MagMakeHolyCurtain）：3x3 内等级判定，任一失败即整体失败
        if magic_id == 16:
            am = self._find_amulet(p, 1, 1)
            if am is None:
                w.send_system(p, "没有护身符。")
                return False
            slot, item, _st = am
            self._use_amulet(p, slot, item, 1)
            n = get_power13(t, lv, 40) + get_rpow(sc_lo, sc_hi) * 3
            result = 0
            for nb in w.nearby(p.map, tx, ty, 1):
                if not isinstance(nb, Monster) or not nb.alive:
                    continue
                if (random.randrange(4) + p.level - 1) > nb.level and nb.master_id < 0:
                    nb.paralyzed_until = now + n * 1000
                    w.send_stats(nb)
                    result += 1
                else:
                    result = 0
            if result <= 0:
                w.send_system(p, "困魔咒失败。")
                return False
            w.send_system(p, f"困住了 {result} 个目标。")
            return True

        # 17 召唤骷髅 / 30 召唤神兽（:465-475 / :537-556）
        # 护身符在造宝宝**之前**就消耗（13~19 共用段 :432-435、神兽 :541-542）。
        if magic_id in (17, 30):
            need = 1 if magic_id == 17 else 5
            am = self._find_amulet(p, 1, need)
            if am is None:
                w.send_system(p, "没有护身符。" if need == 1 else "护身符不足 5 张。")
                return False
            slot, item, _st = am
            self._use_amulet(p, slot, item, need)
            old = w.actor(p.summon_id) if p.summon_id >= 0 else None
            if isinstance(old, Monster) and old.alive:
                # 原版宝宝存活期间不再造新：MakeSlave（ObjBase.pas:7163）
                # 在 m_SlaveList.Count >= 上限时返回 nil → Result=False
                # → **不加熟练度**。骷髅（MagMakeSlave Magic.pas:1387-1421）
                # 什么都不做，旧宝宝原地保留；神兽（MagMakeSinSuSlave
                # :1382-1384）走 RecallSlave（ObjBase.pas:21940）把旧宝宝
                # SpaceMove 召回面前。
                if magic_id == 30:
                    w._slave_space_move(old, p, front=True)
                return False
            summon = w.spawn_summon(p, "变异骷髅" if magic_id == 17 else "神兽")
            if summon is None:
                w.send_system(p, "召唤失败。")
                return False
            summon.slave_make_level = lv
            p.summon_id = summon.id
            w.send_system(p, f"召唤了 {summon.name}。")
            return True

        # 18 隐身术 / 19 集体隐身术（:476-483）
        if magic_id in (18, 19):
            am = self._find_amulet(p, 1, 1)
            if am is None:
                w.send_system(p, "没有护身符。")
                return False
            slot, item, _st = am
            self._use_amulet(p, slot, item, 1)
            n = get_power13(t, lv, 30) + get_rpow(sc_lo, sc_hi) * 3
            hidden = 0
            targets = [p] if magic_id == 18 else [
                nb for nb in w.nearby(p.map, tx, ty, 1)
                if isinstance(nb, Player) and nb.alive]
            for nb in targets:
                if nb.hide_until > now:          # 原版：已在隐身状态不再叠加
                    continue
                self._make_transparent(nb, n, now)
                hidden += 1
            w.send_system(p, f"你进入了隐身状态（{n} 秒）。" if hidden > 0
                          else "范围内没有可隐身的目标。")
            return hidden > 0

        # 20 诱惑之光（:489 MagTamming）
        if magic_id == 20:
            if isinstance(target, Monster):
                return self._tamming(p, target, lv, now)
            return False

        # 21 瞬息移动（:495 MagSaceMove）
        if magic_id == 21:
            ok = self._random_teleport(p, lv)
            if not ok:
                w.send_system(p, "瞬息移动失败。")
            return ok

        # 22 火墙（:501 MagMakeFireCross）：十字 5 格，伤害/持续时间都按公式掷点
        if magic_id == 22:
            pwr = magic_power(p, t, lv)
            htime = get_power(t, lv, 10) + (get_rpow(mc_lo, mc_hi) >> 1)
            made = w.make_fire_wall_at(p, tx, ty, pwr, htime)
            w.send_system(p, f"释放了火墙。({made} 格)" if made > 0 else "火墙释放失败。")
            return made > 0

        # 23 爆裂火焰 / 33 冰咆哮（:510 MagBigExplosion，范围按 1 半径 = 3x3）
        if magic_id in (23, 33):
            pwr = magic_power(p, t, lv)
            hit = 0
            for nb in w.nearby(p.map, tx, ty, 1):
                if isinstance(nb, Monster) and nb.alive and nb.master_id != p.id:
                    if self._direct_magic(p, nb, pwr, magic_id, now) > 0:
                        hit += 1
            w.send_system(p, f"{t.name} 击中了 {hit} 个目标。" if hit > 0 else "没有命中目标。")
            return hit > 0

        # 24 地狱雷光（:518 MagElecBlizzard）：自身半径 2，非不死系只有 1/10
        if magic_id == 24:
            pwr = magic_power(p, t, lv)
            hit = 0
            for nb in w.nearby(p.map, p.x, p.y, 2):
                if isinstance(nb, Monster) and nb.alive and nb.master_id != p.id:
                    undead = nb.template.undead if nb.template else 0
                    part = pwr if undead == 1 else pwr // 10
                    if self._direct_magic(p, nb, part, magic_id, now) > 0:
                        hit += 1
            w.send_system(p, f"{t.name} 击中了 {hit} 个目标。" if hit > 0 else "没有命中目标。")
            return hit > 0

        # 28 心灵启示（:522）：Random(6) <= lv+3 才成功，目标血量显示 n 秒
        if magic_id == 28:
            if target is None:
                w.send_system(p, "没有目标。")
                return False
            if target.open_hp_until > now:
                return False
            if random.randrange(6) > lv + 3:
                w.send_system(p, "心灵启示失败。")
                return False
            n = get_power13(t, lv, get_rpow(sc_lo, sc_hi) * 2 + 30)
            target.open_hp_until = now + n * 1000
            w.broadcast_around(target.map, {
                "type": "openHealth", "id": target.id, "until": target.open_hp_until,
                "hp": target.hp, "maxHp": target.max_hp}, target.x, target.y, SIGHT_RANGE)
            return True

        # 29 群体治疗术（:532 MagBigHealing）：目标点 3x3 内的队友
        if magic_id == 29:
            # IsProperFriend：队友/自己 + 主人=自己的宝宝都可被群疗
            pwr = heal_power(p, t, lv)
            healed = 0
            for nb in w.nearby(p.map, tx, ty, 1):
                if not nb.alive or nb.hp >= nb.max_hp:
                    continue
                if not (isinstance(nb, Player)
                        or (isinstance(nb, Monster) and nb.master_id == p.id)):
                    continue
                if isinstance(nb, Player):
                    nb.inc_healing = (min(300, nb.inc_healing + pwr)
                                      if nb.inc_healing + pwr < 300 else 300)
                else:
                    nb.hp = min(nb.max_hp, nb.hp + pwr)
                w.send_stats(nb)
                healed += 1
            w.send_system(p, f"施展了 {t.name}，治疗了 {healed} 个目标。")
            return healed > 0

        # 31 魔法盾（:568 MagBubbleDefenceUp）：已开启时不再叠加
        if magic_id == 31:
            if p.shield_until > now:
                w.send_system(p, "魔法盾已经开启。")
                return False
            n = get_power(t, lv, get_rpow(mc_lo, mc_hi) + 15)
            p.shield_until = now + n * 1000
            p.shield_level = lv
            w.send_stats(p)
            w.send_system(p, f"魔法盾开启，持续 {n} 秒。")
            return True

        # 32 圣言术（:572 MagTurnUndead）：只对不死系，掷概率秒杀（无直接伤害）
        if magic_id == 32:
            if not isinstance(target, Monster) or not target.alive:
                w.send_system(p, "没有目标。")
                return False
            undead = target.template.undead if target.template else 0
            if undead != 1:
                w.send_system(p, "圣言术只对不死系怪物有效。")
                return False
            self._hate(target, p)
            if (random.randrange(2) + (p.level - 1)) > target.level:
                if target.level < 50:
                    gap = p.level - target.level
                    if random.randrange(100) < (lv * 8 - lv + 15 + gap):
                        target.hp = 0
                        w.broadcast_around(target.map, {
                            "type": "attack", "attackerId": p.id, "targetId": target.id,
                            "damage": target.max_hp, "magicId": 32,
                            "x": target.x, "y": target.y}, target.x, target.y, SIGHT_RANGE)
                        self._kill_if_dead(p, target, now)
                        w.send_system(p, f"圣言术消灭了 {target.name}！")
                        return True
            w.send_system(p, f"圣言术对 {target.name} 失效了。")
            return False    # 没秒掉 → 原版 Result=False，不加熟练度

        w.send_system(p, "该魔法暂未开放。")
        return False

    # ------------------------------------------------------------------
    # 技能辅助实现
    # ------------------------------------------------------------------

    def _make_transparent(self, actor, seconds: int, now: int) -> None:
        """原版 ``MagMakePrivateTransparent``（Magic.pas:734）。

        附近 9 格内把「以我为目标」的怪物仇恨清掉（距离 >1 或 50% 概率），
        然后给自己挂 STATE_TRANSPARENT；移动/攻击会立刻破除（那边由
        service 的移动处理清 hide_until）。
        """
        w = self.world
        for m in w.nearby(actor.map, actor.x, actor.y, 9):
            if not isinstance(m, Monster) or not m.alive:
                continue
            if m.target_id != actor.id:
                continue
            if (abs(m.x - actor.x) > 1 or abs(m.y - actor.y) > 1
                    or random.randrange(2) == 0):
                m.target_id = -1
        actor.hide_until = now + seconds * 1000
        w.send_stats(actor)

    def _tamming(self, p, target: Monster, lv: int, now: int) -> bool:
        """原版 ``MagTamming``（Magic.pas:773）：诱惑之光。

        1. ``Random(4-lv)=0`` 才进入判定（0 级 25%，3 级 100%）；
        2. ``Random(2)`` 一半概率「被定住」（OpenHolySeizeMode）；
        3. 等级 ≤ 自己+2、``RandomRange(自身等级, 自身等级+20)+lv*5 > 目标等级+10``
           才有机会；再受 50 级上限、5 只上限、非不死系、未被训过 限制；
        4. 成功概率 = 1/n14（n14 由目标最大 HP/100 决定，低于 2 取 2，否则 ×2）。
        """
        w = self.world
        if lv >= 4 or random.randrange(4 - lv) != 0:
            # 原版 else 分支：Random(2)=0 也返回 True（算「施法成功」）
            return random.randrange(2) == 0
        target.target_id = -1
        if random.randrange(2) == 0:
            if target.level <= p.level + 2:
                roll = random.randint(p.level, p.level + 20) + lv * 5
                if roll > target.level + 10:
                    undead = target.template.undead if target.template else 0
                    if (target.level <= 50 and undead != 1
                            and target.master_id < 0 and p.summon_id < 0):
                        hp_rate = max(2, target.max_hp // 100)
                        n14 = hp_rate * 2
                        if random.randrange(n14) == 0:
                            old = w.actor(p.summon_id) if p.summon_id >= 0 else None
                            if isinstance(old, Monster) and old.alive:
                                w.remove_summon(old)
                            target.master_id = p.id
                            target.slave_make_level = lv
                            target.target_id = -1
                            p.summon_id = target.id
                            w.send_system(p, f"诱惑了 {target.name}！")
                            return True
                        if random.randrange(14) == 0:
                            target.hp = 0
                            self._kill_if_dead(p, target, now)
                        w.send_system(p, "诱惑失败。")
                        return False
                    if undead == 1 and random.randrange(20) == 0:
                        target.hp = 0
                        self._kill_if_dead(p, target, now)
                w.send_system(p, "诱惑失败。")
                return False
            w.send_system(p, "诱惑失败。")
            return False
        target.paralyzed_until = now + random.randrange(lv * 5 + 10) * 1000
        w.send_stats(target)
        w.send_system(p, f"{target.name} 被定住了。")
        return True
