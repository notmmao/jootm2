"""批次 A：物品 / 背包 / 装备 / 快捷栏消息。

直译 Java ``GameService`` 的 pickup / useItem / takeOnItem / takeOffItem /
quickSlot / dropItem / moveItem / magicKeyChange，以及它们依赖的
useScrollItem / unbundleItem / useBlessOil / learnSkillBook / consume / equip。
"""
from __future__ import annotations

import random
from typing import Any

from .util import (
    BAG_SIZE, EQUIP_SLOTS, USE_ITEM_INTERVAL_MS, U_WEAPON, _int, now_ms,
    slot_accepts, slot_for,
)

ITEM_TYPES = frozenset({
    "pickup", "useItem", "takeOnItem", "takeOffItem", "quickSlot",
    "drop", "moveItem", "magicKeyChange",
})


class ItemHandlers:
    """Mixin：依赖 ``self.world`` / ``self.find_by_session`` / ``self.send_bag_update``。"""

    # ---- 公共辅助 -------------------------------------------------------

    def _player_and_item(self, session, msg: dict, key: str = "makeIndex"):
        p = self.find_by_session(session)
        if p is None:
            return None, None, None
        it = p.bag_item(_int(msg, key, -1))
        if it is None:
            self.world.send_system(p, "物品不存在。")
            return p, None, None
        t = self.world.item_templates.get(int(it.get("index", 0)))
        return p, it, t

    # ---- 拾取 / 丢弃 / 换位 ---------------------------------------------

    def _pickup(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        item_id = _int(msg, "itemId", -1)
        gi = self.world.ground_item(item_id)
        if self.world.pickup(p, item_id):
            label = "物品"
            if gi is not None and gi.is_gold():
                label = f"{gi.gold} 金币"
            elif gi is not None and gi.item is not None:
                t = self.world.item_templates.get(int(gi.item.get("index", 0)))
                label = t.name if t is not None else "物品"
            self.world.send_system(p, f"拾取了 {label}。")
            self.world.send_stats(p)
            self.send_bag_update(p)

    def _drop(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        make_index = _int(msg, "makeIndex", -1)
        if any(e is not None and int(e.get("makeIndex", 0)) == make_index
               for e in p.equipment):
            self.world.send_system(p, "请先把装备放回背包再丢弃。")
            return
        if self.world.drop_item(p, make_index):
            p.clear_quick_ref(make_index)
            self.world.send_system(p, "丢弃了物品。")
            self.send_bag_update(p)
        else:
            self.world.send_system(p, "物品不存在。")

    def _move_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        src_mi, dst_mi = _int(msg, "makeIndex", -1), _int(msg, "targetIndex", -1)
        src = dst = -1
        for i, it in enumerate(p.bag):
            mi = int(it.get("makeIndex", 0))
            if mi == src_mi:
                src = i
            if mi == dst_mi:
                dst = i
        if src < 0 or dst < 0 or src == dst:
            return
        p.bag[src], p.bag[dst] = p.bag[dst], p.bag[src]
        self.send_bag_update(p)

    # ---- 使用物品 -------------------------------------------------------

    def _use_item(self, session, msg: dict) -> None:
        p, it, t = self._player_and_item(session, msg)
        if p is None or it is None:
            return
        if t is None:
            return
        w = self.world
        if (0 <= t.stdMode <= 3) or t.stdMode == 31:
            now = now_ms()
            if now - p.last_use_item_at < USE_ITEM_INTERVAL_MS:
                return
            p.last_use_item_at = now
        if t.stdMode == 4:
            self._learn_skill_book(p, t, it)
        elif 0 <= t.stdMode <= 2:
            self._consume(p, t, it)
        elif t.stdMode == 3:
            if t.shape in (1, 2, 3, 5):
                self._use_scroll_item(p, t, it)
            else:
                self._use_bless_oil(p, it)
        elif t.stdMode == 31:
            self._unbundle_item(p, t, it)
        elif slot_for(t.stdMode) >= 0:
            self._take_on_slot(p, it, t, slot_for(t.stdMode))
        else:
            w.send_system(p, "无法使用该物品。")

    def _consume(self, p, t, it: dict) -> None:
        w = self.world
        used = False
        if t.stdMode == 0:
            # 原版 ObjBase.pas:23335-23368：StdMode 0 的药水**不**即时加血，
            # AC 写 m_nIncHealth、MAC 写 m_nIncSpell 回血池，由 regen tick
            # 按「每 tick Level div 10 + 5」慢慢消耗——这就是「药水慢慢恢复」。
            # 例外：Shape 1（疗伤药）走 IncHealthSpell(AC, MAC) 立即加。
            if t.shape == 1:
                if t.acMin > 0:
                    p.hp = min(p.max_hp, p.hp + t.acMin)
                if t.macMin > 0:
                    p.mp = min(p.max_mp, p.mp + t.macMin)
                used = (t.acMin > 0 or t.macMin > 0)
            else:
                if t.acMin > 0:
                    p.inc_health += t.acMin
                    used = True
                if t.macMin > 0:
                    p.inc_spell += t.macMin
                    used = True
        elif t.stdMode in (1, 2):
            # 原版 1=肉类（饥饿度，本项目无饥饿系统）、2=毒药类：消耗但无回血。
            used = True
        if used:
            p.bag.remove(it)
            p.clear_quick_ref(int(it.get("makeIndex", 0)))
            w.send_stats(p)
            w.send_system(p, f"使用了 {t.name}。")
            self.send_bag_update(p)
        else:
            w.send_system(p, "现在无法使用该物品。")

    def _learn_skill_book(self, p, book, it: dict) -> None:
        w = self.world
        mt = next((m for m in w.magic_templates.values() if m.name == book.name), None)
        if mt is None:
            w.send_system(p, "无效的技能书。")
            return
        if mt.magId in p.skills:
            w.send_system(p, "你已经学会了该技能。")
            return
        if mt.job != 99 and mt.job != p.job:
            w.send_system(p, "职业不符，无法学习。")
            return
        need = mt.needLevel[0] if mt.needLevel else 0
        if p.level < need:
            w.send_system(p, f"等级不足，需要 {need} 级。")
            return
        p.skills[mt.magId] = {"level": 0, "train": 0, "key": 0}
        p.bag.remove(it)
        p.clear_quick_ref(int(it.get("makeIndex", 0)))
        # 原版 TPlayObject.ReadBook（ObjBase.pas:23459-23472）：加入 m_MagicList 后
        # 立刻 RecalcAbilitys + SendAddMagic(SM_ADDMAGIC)。SM_ADDMAGIC 是「服务端主动
        # 推单条技能」，本项目对应 stats 里的 skills 全量快照——旧实现只回 bagUpdate，
        # 客户端 this.self.skills 一直是登录时的旧值，技能栏要重登才出现新技能。
        p.recompute(w)
        w.send_stats(p, {"skills": p.skills_json(w)})
        w.send_system(p, f"学会了 {mt.name}！")
        self.send_bag_update(p)

    def _use_scroll_item(self, p, t, it: dict) -> None:
        w = self.world
        p.bag.remove(it)
        p.clear_quick_ref(int(it.get("makeIndex", 0)))
        if w.use_scroll(p, t.shape):
            w.send_stats(p)
            w.send_system(p, f"使用了 {t.name}。")
            self.send_bag_update(p)
        else:
            p.bag.append(it)
            w.send_system(p, "现在无法使用该物品。")

    def _unbundle_item(self, p, t, it: dict) -> None:
        w = self.world
        name = w.unbind_item_name(t.shape)
        target = w.item_template_by_name(name) if name else None
        if target is None:
            w.send_system(p, "无法使用该物品。")
            return
        count = 6
        if len(p.bag) + count - 1 > BAG_SIZE:
            w.send_system(p, "背包空间不足，无法解包。")
            return
        p.bag.remove(it)
        for _ in range(count):
            p.bag.append(w.new_item_instance(target))
        w.send_system(p, f"解开了 {name} x{count}。")
        self.send_bag_update(p)

    def _use_bless_oil(self, p, oil_item: dict) -> None:
        w = self.world
        weapon = p.equipment[U_WEAPON]
        if weapon is None:
            w.send_system(p, "请先装备武器。")
            return
        luck = int(weapon.get("lucky", 0))
        if luck >= 7:
            w.send_system(p, "武器幸运已满（+7）。")
            return
        p.bag.remove(oil_item)
        p.clear_quick_ref(int(oil_item.get("makeIndex", 0)))
        r = random.random()
        if r < 0.5:
            luck += 1
            w.send_system(p, f"祝福油生效，武器幸运 +1（当前 {luck}）。")
        elif r < 0.8:
            w.send_system(p, "祝福油无效，武器幸运不变。")
        else:
            luck = max(0, luck - 1)
            w.send_system(p, f"诅咒！武器幸运 -1（当前 {luck}）。")
        weapon["lucky"] = luck
        p.recompute(w)
        w.send_stats(p)
        self.send_bag_update(p)

    # ---- 穿戴 / 卸下 ----------------------------------------------------

    def _take_on_item(self, session, msg: dict) -> None:
        p, it, t = self._player_and_item(session, msg)
        if p is None or it is None:
            return
        if t is None:
            return
        slot = _int(msg, "slot", -1)
        if slot < 0 or slot >= EQUIP_SLOTS:
            slot = slot_for(t.stdMode)
        if slot < 0:
            self.world.send_system(p, "无法装备该物品。")
            return
        self._take_on_slot(p, it, t, slot)

    def _can_take_on(self, p, t, slot: int) -> bool:
        w = self.world
        if t.stdMode == 10 and p.sex != 0:
            w.send_system(p, "男性服饰。")
            return False
        if t.stdMode == 11 and p.sex != 1:
            w.send_system(p, "女性服饰。")
            return False
        if slot in (U_WEAPON, 2):
            if t.weight > p.max_hand_weight():
                w.send_system(p, "过重。")
                return False
        elif t.weight + p.wear_weight_ex(w, slot) > p.max_wear_weight():
            w.send_system(p, "过重。")
            return False
        need_val = {0: p.level, 1: p.combat.get("dcMax", 0),
                    2: p.combat.get("mcMax", 0), 3: p.combat.get("scMax", 0)}
        if t.need == 0:
            if p.level >= t.needLevel:
                return True
            w.send_system(p, f"等级不足，需要 {t.needLevel} 级。")
            return False
        if t.need in need_val and need_val[t.need] >= t.needLevel:
            return True
        w.send_system(p, "不适合您使用。")
        return False

    def _take_on_slot(self, p, it: dict, t, slot: int) -> bool:
        w = self.world
        if not slot_accepts(slot, t.stdMode):
            w.send_system(p, "该物品不能装备到此位置。")
            return False
        if not self._can_take_on(p, t, slot):
            return False
        old = p.equipment[slot]
        p.bag.remove(it)
        p.equipment[slot] = it
        if old is not None:
            p.bag.append(old)
        p.recompute(w)
        w.send_stats(p)
        self.send_bag_update(p)
        w.send_system(p, f"装备了 {t.name}。")
        w.broadcast_entity_add(p)
        return True

    def _take_off_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        slot = _int(msg, "slot", -1)
        if slot < 0 or slot >= EQUIP_SLOTS:
            return
        cur = p.equipment[slot]
        make_index = _int(msg, "makeIndex", -1)
        if cur is None or (make_index >= 0 and int(cur.get("makeIndex", 0)) != make_index):
            w.send_system(p, "该位置没有装备。")
            return
        if len(p.bag) >= BAG_SIZE:
            w.send_system(p, "背包已满，无法卸下。")
            return
        p.equipment[slot] = None
        p.bag.append(cur)
        p.recompute(w)
        w.send_stats(p)
        self.send_bag_update(p)
        t = w.item_templates.get(int(cur.get("index", 0)))
        w.send_system(p, f"卸下了 {t.name if t is not None else '装备'}。")
        w.broadcast_entity_add(p)

    # ---- 快捷栏 / 技能键 ------------------------------------------------

    def _quick_slot(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        slot = _int(msg, "slot", -1)
        make_index = _int(msg, "makeIndex", 0)
        if slot < 0 or slot >= len(p.quick_slots):
            return
        if make_index > 0:
            it = p.bag_item(make_index)
            if it is None:
                self.world.send_system(p, "物品不存在。")
                self.send_bag_update(p)
                return
            t = self.world.item_templates.get(int(it.get("index", 0)))
            if t is None or t.stdMode > 3:
                self.world.send_system(p, "该物品不能放入快捷栏。")
                self.send_bag_update(p)
                return
            for i in range(len(p.quick_slots)):
                if i != slot and p.quick_slots[i] == make_index:
                    p.quick_slots[i] = 0
        p.quick_slots[slot] = make_index
        self.send_bag_update(p)

    def _magic_key_change(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        magic_id, key = _int(msg, "magicId", -1), _int(msg, "key", 0)
        skill = p.skills.get(magic_id)
        if skill is None:
            return
        # 原版 TPlayObject.ClientChangeMagicKey（ObjBase.pas:17506-17520）**不校验**
        # 技能类型：被动技能（基本剑术/精神力战法/攻杀）同样可以绑键，客户端
        # DStMag1Click（FState.pas:3671-3701）也只过滤「键已被别的技能占用」。
        # 之前这里拒绝被动技能是自造限制，已去掉。按下这些键后客户端发 CM_SPELL，
        # 服务端 DoSpell 对战士技能直接返回（实际无效果）——与原版一致。
        if key != 0:
            for other in p.skills.values():
                if other is not skill and other.get("key", 0) == key:
                    other["key"] = 0
        skill["key"] = key
        self.world.send_stats(p, {"skills": p.skills_json(self.world)})
