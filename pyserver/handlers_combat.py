"""批次 B：物理攻击、剑术、33 个技能分支。

直译 Java ``GameService`` 1292–2610 行。数值语义刻意保留 Java 行为：
整数除法向零截断（``java_div``）、``Math.round`` = floor(x+0.5)（``java_round``）、
位标志与随机区间。协议下发的 JSON 字段保持一致。
"""
from __future__ import annotations

import random
from typing import Any

from .util import (
    BAG_SIZE, SIGHT_RANGE, U_WEAPON, _int, delphi_round, direction_delta,
    direction_toward, java_div, java_round, now_ms, random_mine_dura,
)
from .world import Monster, Player

COMBAT_TYPES = frozenset({"attack", "dirAttack", "castMagic"})
POISON_INTERVAL_MS = 2_500


def is_heal_magic(magic_id: int) -> bool:
    return magic_id == 2


def is_projectile_magic(magic_id: int) -> bool:
    return magic_id in (1, 5, 11, 13, 23, 33)


def is_area_magic(magic_id: int) -> bool:
    return magic_id in (23, 33)


def direction_from(fx: int, fy: int, tx: int, ty: int) -> int:
    return direction_toward(fx, fy, tx, ty, 0)


def has_skill(p, magic_id: int) -> bool:
    return magic_id in p.skills


def skill_level(p, magic_id: int) -> int:
    return p.skills.get(magic_id, {}).get("level", 0)


def spell_cost(t, lv: int) -> int:
    return java_round(t.spell / 4 * (lv + 1)) + t.defSpell


def heal_power(p, t, lv: int) -> int:
    """治愈术回血量（原版 Magic.pas:301 SKILL_HEALLING / :172 MagBigHealing）。

    nPower := GetAttackPower(GetPower(MPow(UserMagic)) + LoWord(SC) * 2,
                             (HiWord(SC) - LoWord(SC)) * 2 + 1);
    其中 GetPower（Magic.pas:59）= ROUND(n / (btTrainLv+1) * (btLevel+1))
        + btDefPower + Random(btDefMaxPower - btDefPower)，btTrainLv 恒为 3
        （LocalDB.pas:382 硬编码），GetAttackPower（ObjBase.pas:2416）= base + Random(range+1)。
    注意是 SC **下限**×2 而不是上限；不掺 MC。旧实现用 magic_power（带 MC 掷点）
    再加 scMax*2，回血量大约是原版的 2~4 倍。
    """
    mpow = t.power + int(random.random() * (t.maxPower - t.power))     # MPow：Random 不含上界
    base = (delphi_round(mpow / (3 + 1) * (lv + 1)) + t.defPower
            + int(random.random() * max(0, t.defMaxPower - t.defPower)))
    sc_lo = p.combat.get("scMin", 0)
    sc_hi = p.combat.get("scMax", 0)
    lo = base + sc_lo * 2
    rng = max(0, (sc_hi - sc_lo) * 2 + 1)
    luck = getattr(p, "luck", 0)
    if luck > 0 and int(random.random() * 10) == 0:   # GetAttackPower 的幸运暴击
        return lo + rng
    return lo + int(random.random() * (rng + 1))


def magic_power(p, t, lv: int) -> int:
    base = t.power + int(random.random() * (t.maxPower - t.power + 1))
    scaled = (java_round(base / 4 * (lv + 1)) + t.defPower
              + int(random.random() * max(1, t.defMaxPower - t.defPower + 1)))
    lo, hi = p.combat.get("mcMin", 0), p.combat.get("mcMax", 0)
    mc = lo + int(random.random() * (hi - lo + 1))
    return scaled + mc


def magic_damage(p, t, lv: int, m) -> int:
    return max(1, magic_power(p, t, lv) - (m.template.mac if m.template else 0))


def taoist_damage(p, t, lv: int, m) -> int:
    base = t.power + int(random.random() * (t.maxPower - t.power + 1))
    scaled = (java_round(base / 4 * (lv + 1)) + t.defPower
              + int(random.random() * max(1, t.defMaxPower - t.defPower + 1)))
    lo, hi = p.combat.get("scMin", 0), p.combat.get("scMax", 0)
    sc = lo + int(random.random() * (hi - lo + 1))
    return max(1, scaled + sc - (m.template.mac if m.template else 0))


def roll_dc_with_luck(p) -> int:
    lo, hi = p.combat.get("dcMin", 0), p.combat.get("dcMax", 0)
    if hi <= lo:
        return lo
    if p.luck > 0 and random.random() * 10 < p.luck:
        return hi
    return lo + int(random.random() * (hi - lo + 1))


def sword_second_dam(p, dam: int, magic_id: int, divisor: int) -> int:
    level = skill_level(p, magic_id)
    return max(1, java_round(dam / divisor * (level + 2)))


def green_poison_bonus(target, dmg: int) -> int:
    if getattr(target, "poison_armor_until", 0) > now_ms():
        return java_round(dmg * 1.2)
    return dmg


def hit_check(p, m, hit_bonus: int) -> bool:
    hit = 15 + p.combat.get("hit", 0) + hit_bonus
    dodge = 8 + (m.template.speed if m.template else 0)
    if dodge <= 0:
        return True
    return random.random() * (hit + dodge) < hit


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
        w = self.world
        exp = m.template.exp if m.template else 0
        party = w.party_of(p.id)
        if party is not None and len(party.member_ids) > 1:
            nearby = []
            for pid in party.member_ids:
                a = w.actor(pid)
                if (isinstance(a, Player) and a.alive and a.map is p.map
                        and a.distance_to(p) <= 10):
                    nearby.append(a)
            share = java_div(exp, max(1, len(nearby)))
            for mp in nearby:
                w.add_exp(mp, share)
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
        hit_bonus = 0
        if has_skill(p, 3):
            hit_bonus += 3 * skill_level(p, 3)
        if has_skill(p, 4):
            hit_bonus += java_div(8 * skill_level(p, 4), 3)
        if has_skill(p, 7):
            hit_bonus += skill_level(p, 7)
        if not hit_check(p, m, hit_bonus):
            w.send_system(p, "攻击未命中。")
            # 保真：未命中也广播 attack(miss)，让目标端在原版位置飘 MISS 飘字
            # （原版 SM_STRUCK 携带 miss 标志，目标不扣血、不播受击动作，但能看到落空）
            w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                             "targetId": m.id, "miss": True, "x": m.x, "y": m.y},
                              m.x, m.y, SIGHT_RANGE)
            return
        dam = roll_dc_with_luck(p)
        sword_hit, bonus = self._sword_flag(p, dam, False)
        if sword_hit in (26, 7):
            dam += bonus
        dmg = max(1, dam - (m.template.ac if m.template else 0))
        dmg = green_poison_bonus(m, dmg)
        m.hp = max(0, m.hp - dmg)
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

    def _attack_player(self, p, target) -> None:
        w = self.world
        if p.attack_mode == 0:
            w.send_system(p, "和平模式下无法攻击玩家。")
            return
        if p.distance_to(target) > 1:
            return
        p.dir = direction_from(p.x, p.y, target.x, target.y)
        p.target_id = target.id
        w.broadcast_move(p)
        raw = roll_dc_with_luck(p)
        sword_hit, bonus = self._sword_flag(p, raw, True)
        dmg = max(1, raw - target.combat.get("acMax", 0))
        if sword_hit in (26, 7):
            dmg += bonus
        target.hp = max(0, target.hp - dmg)
        o = {"type": "attack", "attackerId": p.id, "targetId": target.id,
             "damage": dmg, "x": target.x, "y": target.y}
        if sword_hit:
            o["swordHit"] = sword_hit
        w.broadcast_around(target.map, o, target.x, target.y, SIGHT_RANGE)
        w.send_stats(target)
        if sword_hit == 12:
            self._sword_long_attack(p, sword_second_dam(p, raw, 12, 5), target)
        elif sword_hit == 25:
            self._sword_wide_attack(p, sword_second_dam(p, raw, 25, 13), target)
        if target.hp <= 0:
            w.kill_player(target)

    def _sword_long_hit_monster(self, p, m) -> None:
        w = self.world
        d = direction_from(p.x, p.y, m.x, m.y)
        dx, dy = direction_delta(d)
        if p.x + dx * 2 != m.x or p.y + dy * 2 != m.y:
            return
        p.dir = d
        w.broadcast_move(p)
        hit_bonus = 0
        if has_skill(p, 3):
            hit_bonus += 3 * skill_level(p, 3)
        if has_skill(p, 4):
            hit_bonus += java_div(8 * skill_level(p, 4), 3)
        if has_skill(p, 7):
            hit_bonus += skill_level(p, 7)
        dam = roll_dc_with_luck(p)
        fx, fy = p.x + dx, p.y + dy
        front = self._find_target_at(p.map, fx, fy)
        now = now_ms()
        if isinstance(front, Monster) and front.alive:
            if hit_check(p, front, hit_bonus):
                dmg = max(1, dam - (front.template.ac if front.template else 0))
                front.hp = max(0, front.hp - dmg)
                self._broadcast_physical_hit(p, front, dmg)
                self._kill_if_dead(p, front, now)
        if not hit_check(p, m, hit_bonus):
            w.send_system(p, "攻击未命中。")
            # 保真：未命中也广播 attack(miss)，让目标端在原版位置飘 MISS 飘字
            # （原版 SM_STRUCK 携带 miss 标志，目标不扣血、不播受击动作，但能看到落空）
            w.broadcast_around(m.map, {"type": "attack", "attackerId": p.id,
                             "targetId": m.id, "miss": True, "x": m.x, "y": m.y},
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
        self._kill_if_dead(p, m, now)

    def _sword_long_hit_player(self, p, target) -> None:
        w = self.world
        if p.attack_mode == 0:
            w.send_system(p, "和平模式下无法攻击玩家。")
            return
        d = direction_from(p.x, p.y, target.x, target.y)
        dx, dy = direction_delta(d)
        if p.x + dx * 2 != target.x or p.y + dy * 2 != target.y:
            return
        p.dir = d
        w.broadcast_move(p)
        dam = roll_dc_with_luck(p)
        front = self._find_target_at(p.map, p.x + dx, p.y + dy)
        if isinstance(front, Player) and front.alive:
            dmg = max(1, dam - front.combat.get("acMax", 0))
            front.hp = max(0, front.hp - dmg)
            w.broadcast_around(front.map, {"type": "attack", "attackerId": p.id,
                                           "targetId": front.id, "damage": dmg,
                                           "x": front.x, "y": front.y},
                               front.x, front.y, SIGHT_RANGE)
            w.send_stats(front)
            if front.hp <= 0:
                w.kill_player(front)
        second = sword_second_dam(p, dam, 12, 5)
        target.hp = max(0, target.hp - second)
        w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                                        "targetId": target.id, "damage": second,
                                        "x": target.x, "y": target.y, "swordHit": 12},
                           target.x, target.y, SIGHT_RANGE)
        w.send_stats(target)
        if target.hp <= 0:
            w.kill_player(target)

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
        w = self.world
        if isinstance(target, Monster):
            second = green_poison_bonus(target, second)
            target.hp = max(0, target.hp - second)
            self._broadcast_physical_hit(p, target, second)
            self._kill_if_dead(p, target, now_ms())
        elif isinstance(target, Player):
            if p.attack_mode == 0:
                return
            target.hp = max(0, target.hp - second)
            w.broadcast_around(target.map, {"type": "attack", "attackerId": p.id,
                                            "targetId": target.id, "damage": second,
                                            "x": target.x, "y": target.y},
                               target.x, target.y, SIGHT_RANGE)
            w.send_stats(target)
            if target.hp <= 0:
                w.kill_player(target)

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

    def _damage_weapon(self, p, amount: int) -> None:
        """武器磨损（原版 DoDamageWeapon）。挖到耐久 0 后原版不再可挖
        （ObjBase.pas:8823 ``m_UseItems[U_WEAPON].Dura > 0``）。"""
        it = (p.equipment[U_WEAPON] if p.equipment else None)
        if not it:
            return
        it["durability"] = max(0, int(it.get("durability", 0) or 0) - amount)

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

    def _finish_cast(self, p, t, skill: dict, lv: int, now: int,
                     cost: int, success: bool) -> None:
        w = self.world
        p.mp -= cost
        p.skill_cooldown[t.magId] = now
        if success and skill.get("level", 0) < 3:
            skill["train"] = skill.get("train", 0) + 1 + int(random.random() * 3)
            need = t.maxTrain[lv] if lv < len(t.maxTrain) else 0
            if need > 0 and skill["train"] >= need:
                skill["train"] -= need
                skill["level"] = lv + 1
                w.send_system(p, f"你的 {t.name} 升到了 {skill['level'] + 1} 级！")
        w.send_stats(p)

    def _splash_magic(self, p, m, damage: int, magic_id: int, now: int) -> None:
        splash = java_div(damage, 2)
        for nb in self.world.nearby(m.map, m.x, m.y, 1):
            if not isinstance(nb, Monster) or nb is m or not nb.alive:
                continue
            nb.hp = max(0, nb.hp - splash)
            self._broadcast_magic_hit(p, nb, splash, magic_id)
            self._kill_if_dead(p, nb, now)

    def _elec_blizzard(self, p, pwr: int, now: int) -> int:
        hit = 0
        for a in self.world.nearby(p.map, p.x, p.y, 2):
            if not isinstance(a, Monster) or not a.alive:
                continue
            undead = a.template.undead if a.template else 0
            dmg = pwr if undead == 1 else max(1, java_div(pwr, 10))
            a.hp = max(0, a.hp - dmg)
            self._broadcast_magic_hit(p, a, dmg, 24)
            self._kill_if_dead(p, a, now)
            hit += 1
        return hit

    def _pass_through_magic(self, p, ndir: int, rng: int, pwr: int,
                            undead_attack: bool, magic_id: int, now: int) -> int:
        hit = 0
        x, y = p.x, p.y
        dx, dy = direction_delta(ndir)
        for _ in range(rng):
            x, y = x + dx, y + dy
            if not p.map.inside(x, y):
                break
            a = self._find_target_at(p.map, x, y)
            if not isinstance(a, Monster) or not a.alive:
                continue
            mac = a.template.mac if a.template else 0
            if random.random() * 10 < 1 + java_div(mac, 5):
                continue
            dmg = pwr
            undead = a.template.undead if a.template else 0
            if undead_attack and undead == 1:
                dmg = java_round(dmg * 1.5)
            a.hp = max(0, a.hp - dmg)
            self._broadcast_magic_hit(p, a, dmg, magic_id)
            self._kill_if_dead(p, a, now)
            hit += 1
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
            if not isinstance(a, Monster) or not a.alive or p.level <= a.level:
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
            if not isinstance(a, Monster) or not a.alive or p.level <= a.level:
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

    def _random_teleport(self, p) -> None:
        # 原版瞬息移动（Magic.pas:959 + SpaceMove nInt=1）：两段都是 2 号特效
        # —— 消失 SM_SPACEMOVE_HIDE2（TScrollHideEffect 1590-1599）+ 现身 SHOW2（1600-1609）。
        w = self.world
        for _ in range(100):
            nx = p.x + int(random.random() * 9) - 4
            ny = p.y + int(random.random() * 9) - 4
            if p.map.can_walk(nx, ny) and not p.map.occupied(nx, ny):
                w.broadcast_around(p.map, {"type": "spaceMoveHide", "x": p.x, "y": p.y,
                                           "variant": 1}, p.x, p.y)
                p.map.remove(p.id)
                p.x, p.y = nx, ny
                p.map.add(p)
                p.session.send_json(w.map_snapshot(p, "changeMap"))
                w.broadcast_entity_add(p)
                w.broadcast_around(p.map, {"type": "spaceMoveShow", "x": p.x, "y": p.y,
                                           "variant": 1}, p.x, p.y)
                return
        w.send_system(p, "传送失败。")

    def _poison_shape(self, p) -> int:
        for shape in (1, 2):
            for it in p.bag:
                t = self.world.item_templates.get(int(it.get("index", 0)))
                if t is not None and t.stdMode == 25 and t.shape == shape:
                    return shape
        return 0

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
        if magic_id in (3, 4, 7, 12):
            w.send_system(p, "被动技能无法主动施放。")
            return
        skill = p.skills.get(magic_id)
        if skill is None:
            w.send_system(p, "尚未学会该技能。")
            return
        lv = skill.get("level", 0)
        cost = spell_cost(t, lv)
        if p.mp < cost:
            w.send_system(p, "魔法值不足。")
            return
        now = now_ms()
        last = p.skill_cooldown.get(magic_id)
        if last is not None and now - last < t.delay:
            return
        if (magic_id not in (7, 12, 25, 26, 27) and tx >= 0 and ty >= 0):
            cast_dir = direction_from(p.x, p.y, tx, ty)
            if cast_dir != p.dir:
                p.dir = cast_dir
                w.broadcast_move(p)

        # 开关 / 蓄力类技能：提前结算并 return
        if magic_id == 25:
            p.wide_hit_on = not p.wide_hit_on
            w.send_system(p, "半月弯刀开启。" if p.wide_hit_on else "半月弯刀关闭。")
            self._finish_cast(p, t, skill, lv, now, cost, True)
            return
        if magic_id == 26:
            if now - p.last_fire_hit_at < 10_000:
                w.send_system(p, "凝聚内力失败。")
                return
            p.last_fire_hit_at = now
            p.fire_hit_on = True
            w.send_system(p, "你的武器因精神火焰而炙热。")
            self._finish_cast(p, t, skill, lv, now, cost, True)
            return
        if magic_id == 27:
            if now - p.last_rush_rush_at < 3000:
                w.send_system(p, "冲撞需要休息。")
                return
            p.last_rush_rush_at = now
            self._finish_cast(p, t, skill, lv, now, cost,
                              self._rush_rush(p, tx, lv))
            return

        target = w.actor(target_id) if target_id > 0 else None
        if target is None or not target.alive:
            target = self._find_target_at(p.map, tx, ty)
        if isinstance(target, Monster) and target.alive:
            p.target_id = target.id
        cast_tx, cast_ty = (p.x, p.y) if is_heal_magic(magic_id) else (tx, ty)
        blocked = False
        if (magic_id in (1, 5, 13) and isinstance(target, Monster) and target.alive):
            bp = self._blocked_point(p, target)
            if bp is not None:
                cast_tx, cast_ty, blocked = bp[0], bp[1], True
        cast = {"type": "spellCast", "casterId": p.id, "magicId": magic_id,
                "effect": t.effect, "targetX": cast_tx, "targetY": cast_ty}
        if blocked:
            cast["blocked"] = True
        w.broadcast_around(p.map, cast, p.x, p.y, SIGHT_RANGE)

        success = self._resolve_magic(p, t, skill, lv, now, magic_id,
                                      tx, ty, target, blocked)
        self._finish_cast(p, t, skill, lv, now, cost, success)

    def _resolve_magic(self, p, t, skill, lv, now, magic_id,
                       tx, ty, target, blocked) -> bool:
        """castMagic 的效果分派；返回是否施放成功（决定是否加熟练度）。"""
        w = self.world
        if is_heal_magic(magic_id):
            heal = max(1, heal_power(p, t, lv))
            p.inc_healing = min(300, p.inc_healing + heal)
            w.send_system(p, f"施展了 {t.name}。")
            w.send_stats(p)
            return True
        if magic_id in (9, 10):
            ndir = direction_from(p.x, p.y, tx, ty)
            p.dir = ndir
            w.broadcast_move(p)
            rng = 5 if magic_id == 9 else 8
            hit = self._pass_through_magic(p, ndir, rng, magic_power(p, t, lv),
                                           magic_id == 10, magic_id, now)
            w.send_system(p, f"{t.name} 击中了 {hit} 个目标。" if hit > 0 else "没有命中目标。")
            return True
        if magic_id == 24:
            hit = self._elec_blizzard(p, magic_power(p, t, lv), now)
            w.send_system(p, f"{t.name} 击中了 {hit} 个目标。" if hit > 0 else "没有命中目标。")
            return True
        if is_projectile_magic(magic_id):
            if isinstance(target, Monster) and target.alive:
                if p.distance_to(target) > 8:
                    return False
                mac = target.template.mac if target.template else 0
                if blocked:
                    w.send_system(p, "目标被障碍物挡住了。")
                elif random.random() * 10 < 1 + java_div(mac, 5):
                    w.send_system(p, "魔法被躲过了。")
                else:
                    damage = (taoist_damage(p, t, lv, target) if magic_id == 13
                              else magic_damage(p, t, lv, target))
                    undead = target.template.undead if target.template else 0
                    if magic_id == 11 and undead == 1:
                        damage = java_round(damage * 1.5)
                    target.hp = max(0, target.hp - damage)
                    self._broadcast_magic_hit(p, target, damage, magic_id)
                    self._kill_if_dead(p, target, now)
                    if is_area_magic(magic_id):
                        self._splash_magic(p, target, damage, magic_id, now)
            return True
        if magic_id == 6:
            if target is None or not target.alive:
                w.send_system(p, "没有目标。")
                return False
            shape = self._poison_shape(p)
            if shape == 0:
                w.send_system(p, "没有毒药粉。")
                return False
            sc = p.combat.get("scMax", 0)
            if shape == 2:
                sec = 13 + 7 * (lv + 1) + 2 * sc
                target.poison_armor_until = now + sec * 1000
                w.send_system(p, f"对 {target.name} 施放了绿毒，防御下降。")
            else:
                sec = 10 + 5 * (lv + 1) + 2 * sc
                target.poison_ticks = max(1, java_round(sec * 1000 / POISON_INTERVAL_MS))
                target.poison_damage = 1 + lv
                target.last_poison_at = now
                w.send_system(p, f"对 {target.name} 施放了红毒，持续掉血。")
            w.send_stats(target)
            return True
        if magic_id in (17, 30):
            old = w.actor(p.summon_id) if p.summon_id >= 0 else None
            if isinstance(old, Monster) and old.alive:
                w.remove_summon(old)
            summon = w.spawn_summon(p, "变异骷髅" if magic_id == 17 else "神兽")
            if summon is not None:
                summon.slave_make_level = lv
                p.summon_id = summon.id
                w.send_system(p, f"召唤了 {summon.name}。")
                return True
            w.send_system(p, "召唤失败。")
            return False
        if magic_id == 8:
            pushed = self._push_around(p, lv)
            w.send_system(p, f"推开了 {pushed} 个敌人。" if pushed > 0
                          else "周围没有可推开的敌人。")
            return True
        if magic_id == 21:
            self._random_teleport(p)
            return True
        if magic_id in (18, 19):
            dur = (30 + 3 * p.combat.get("scMax", 0)) * 1000
            hidden = 0
            if magic_id == 18:
                p.hide_until = now + dur
                w.send_stats(p)
                hidden = 1
            else:
                for nb in w.nearby(p.map, tx, ty, 1):
                    if isinstance(nb, Player) and nb.alive:
                        nb.hide_until = now + dur
                        w.send_stats(nb)
                        hidden += 1
            w.send_system(p, f"你进入了隐身状态（{dur // 1000} 秒）。" if hidden > 0
                          else "范围内没有可以隐身的目标。")
            return True
        if magic_id == 22:
            wx = target.x if target is not None else tx
            wy = target.y if target is not None else ty
            damage = max(1, p.combat.get("mcMax", 0) + t.power + lv * 2)
            made = w.make_fire_wall_at(p, wx, wy, damage, 8 + lv * 2)
            w.send_system(p, f"释放了火墙。({made} 格)" if made > 0 else "火墙释放失败。")
            return made > 0
        if magic_id == 20:
            if isinstance(target, Monster) and target.alive:
                if target.master_id == p.id:
                    target.paralyzed_until = now + (10 + lv * 5) * 1000
                    w.send_stats(target)
                    w.send_system(p, f"{target.name} 被定身了。")
                    return True
                if random.random() < 0.05 + lv * 0.05:
                    if target.master_id >= 0:
                        old_master = w.actor(target.master_id)
                        if isinstance(old_master, Player) and old_master.summon_id == target.id:
                            old_master.summon_id = -1
                        target.hp = max(1, java_div(target.hp, 10))
                        w.send_system(p, f"你抢走了 {target.name}！")
                    target.master_id = p.id
                    target.slave_make_level = lv
                    p.summon_id = target.id
                    w.send_system(p, f"诱惑了 {target.name}！")
                else:
                    w.send_system(p, "诱惑失败。")
                return True
            return False
        if magic_id == 29:
            heal = max(1, heal_power(p, t, lv))
            healed = 0
            for nb in w.nearby(p.map, p.x, p.y, 5):
                if isinstance(nb, Player) and nb.alive and nb.hp < nb.max_hp:
                    nb.inc_healing = min(300, nb.inc_healing + heal)
                    w.send_stats(nb)
                    healed += 1
            w.send_system(p, f"施展了 {t.name}，治疗了 {healed} 个目标。")
            return True
        if magic_id in (14, 15):
            dur = (30 + lv * 10) * 1000
            buffed = 0
            for nb in w.nearby(p.map, p.x, p.y, 3):
                if not isinstance(nb, Player) or not nb.alive:
                    continue
                nb.ac_buff_until = now + dur
                bonus = 2 + java_div(nb.level, 7)
                if magic_id == 15:
                    nb.ac_buff_bonus = bonus
                else:
                    nb.mac_buff_bonus = bonus
                nb.recompute(w)
                w.send_stats(nb)
                buffed += 1
            w.send_system(p, f"施展了 {t.name}，为 {buffed} 个目标提升防御。")
            return True
        if magic_id == 31:
            dur = (30 + lv * 10) * 1000
            p.shield_until = now + dur
            w.send_system(p, f"魔法盾开启，减伤持续 {dur // 1000} 秒。")
            return True
        if magic_id == 16:
            if isinstance(target, Monster) and target.alive:
                target.paralyzed_until = now + (5 + lv * 3) * 1000
                w.send_stats(target)
                w.send_system(p, f"困住了 {target.name}。")
                return True
            w.send_system(p, "没有目标。")
            return False
        if magic_id == 32:
            if isinstance(target, Monster) and target.alive:
                undead = target.template.undead if target.template else 0
                if undead != 1:
                    w.send_system(p, "圣言术只对不死系怪物有效。")
                    return False
                damage = magic_damage(p, t, lv, target) * 3
                target.hp = max(0, target.hp - damage)
                self._broadcast_magic_hit(p, target, damage, magic_id)
                self._kill_if_dead(p, target, now)
                w.send_system(p, f"圣言术对 {target.name} 造成 {damage} 伤害。")
                return True
            w.send_system(p, "没有目标。")
            return False
        if magic_id == 28:
            if isinstance(target, Monster) and target.alive:
                w.send_system(p, f"{target.name} 血量 {target.hp}/{target.max_hp}")
                return True
            w.send_system(p, "没有目标。")
            return False
        w.send_system(p, "该魔法暂未开放。")
        return False
