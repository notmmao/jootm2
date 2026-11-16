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
    "drop", "dropGold", "moveItem", "magicKeyChange",
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

    def _drop_gold(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        amount = _int(msg, "gold", 0)
        if self.world.drop_gold(p, amount):
            self.world.send_system(p, f"丢弃了 {amount} 金币。")
            self.send_bag_update(p)
        else:
            # 原版 ClientDropGold（ObjBase.pas:16187）：nGold >= m_nGold 拒绝——
            # 至少留 1 金币在身上。
            self.world.send_system(p, "无法丢弃这么多金币。")

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
        # 原版 EatItems 顶部（ObjBase.pas:23330-23334）：boNODRUG 地图（如禁药区）
        # 拒绝一切 StdMode 0~3 药品/油/卷轴。MapInfo.txt 解析已把尾部标志大写进
        # map_flags，本数据集目前没有 NODRUG 图，此守卫为保真保留（命中即拒绝）。
        if 0 <= t.stdMode <= 3 and "NODRUG" in w.map_flags.get(
                p.map.name if p.map else "", ()):
            w.send_system(p, "此地图不允许使用任何药品。")
            return
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
            # 原版 ObjBase.pas:23335-23438 EatItems → EatUseItems(StdItem.Shape)
            # (:23513-23591) 按 Shape 精确分流。旧实现把「非卷轴」一律当祝福油，
            # 导致修复油(9)/战神油(10)/增益药水(12) 全都被拿去加武器幸运。
            #   1/2/3/5 传送卷、4 祝福油 WeaptonMakeLuck、9 修复油 RepairWeapon、
            #   10 战神油 SuperRepairWeapon、12 临时增益药水、11 彩票(原版已注释掉)
            shape = t.shape
            if shape in (1, 2, 3, 5):
                self._use_scroll_item(p, t, it)
            elif shape == 4:
                self._use_bless_oil(p, it)
            elif shape in (9, 10):
                self._repair_weapon(p, it, super_repair=(shape == 10))
            elif shape == 12:
                self._use_status_potion(p, t, it)
            else:
                w.send_system(p, "无法使用该物品。")
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
            # Shape 1（疗伤药/太阳水）走 IncHealthSpell(AC, MAC) 立即加；
            # Shape 2（神水）置 m_boUserUnLockDurg（:23346）；其余 Shape
            # （特殊药水3/药瓶2 等）即使 AC/MAC 全 0 原版也 **Result:=True
            # 照样消耗**（:23349-23368 else 分支无条件返回 True）——旧实现
            # 「无回血值就拒绝使用」是错的，空效果药水喝下去一样消失。
            if t.shape == 1:
                if t.acMin > 0:
                    p.hp = min(p.max_hp, p.hp + t.acMin)
                if t.macMin > 0:
                    p.mp = min(p.max_mp, p.mp + t.macMin)
                used = True
            elif t.shape == 2:
                # 神水：本项目无绑定装备系统，解锁标志没有消费方，仅照原版消耗。
                p.user_unlock_durg = True
                used = True
            else:
                if t.acMin > 0:
                    p.inc_health += t.acMin
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

    def _use_status_potion(self, p, t, it: dict) -> None:
        """临时增益药水（StdMode=3 Shape=12，原版 EatItems :23383-23437）。

        字段映射（与 StdItems.csv 列义一致，原版 TStdItem.DC.. 即本项目
        dcMin..macMax 的 LoWord/HiWord 对）：
        - DC(dcMin)  >0 → 槽0 攻击力 +2+DC，时长 MacMax 秒（原版 MAC2）
        - MC(mcMin)  >0 → 槽1 魔法力 +2+MC
        - SC(scMin)  >0 → 槽2 道术力 +2+SC
        - AC2(acMax) >0 → 槽3 攻速 +AC2
        - AC(acMin)  >0 → 槽4 MaxHP +AC
        - MAC(macMin)>0 → 槽5 MaxMP +MAC
        原版同槽**覆盖**（:= 而非 Inc），时长同样被覆盖。任一生效即消耗。
        """
        w = self.world
        secs = t.macMax if t.macMax > 0 else 180     # MAC2：持续秒数
        until = now_ms() + secs * 1000
        slots = ((0, t.dcMin), (1, t.mcMin), (2, t.scMin),
                 (3, t.acMax), (4, t.acMin), (5, t.macMin))
        applied = False
        for idx, val in slots:
            if val > 0:
                p.status_arr[idx] = val
                p.status_until[idx] = until
                applied = True
        if not applied:
            w.send_system(p, "现在无法使用该物品。")
            return
        p.bag.remove(it)
        p.clear_quick_ref(int(it.get("makeIndex", 0)))
        p.recompute(w)
        w.send_stats(p)
        self.send_bag_update(p)
        w.send_system(p, f"使用了 {t.name}，{secs} 秒内有效。")

    def _use_bless_oil(self, p, oil_item: dict) -> None:
        """原版 ``WeaptonMakeLuck``（ObjBase.pas:23621-23671）逐字对齐。

        旧实现「50% +1 / 30% 无效 / 20% 诅咒」是自造概率，与原版完全不同：

        - **诅咒先掷**：``Random(20) = 1``（恰好等于 1，5%）→ 幸运 -1
          （btValue[3] 归 0 后转 Curse 计数），提示「你的武器被诅咒了」；
        - 否则按幸运分级判定成功（nRand = abs(DC2-DC) div 5，越钝的武器越难加）：
          幸运 <1 → **必成**；<3 → ``Random(nRand+6)=1``（1/(nRand+6)）；
          <7 → ``Random(nRand*40)=1``；≥7 恒无效；
        - **只要装了武器油就消耗**（原版 Result:=True 恒成立，失败提示「无效」
          也照样扣油；满幸运不是「拒绝使用」而是「白花一瓶」）；
          没装武器 Result=False，油不消耗。
        幸运存在 ``weapon["lucky"]``（可负 = 诅咒），recompute 汇总进 p.luck。
        """
        w = self.world
        weapon = p.equipment[U_WEAPON]
        if weapon is None:
            w.send_system(p, "请先装备武器。")
            return
        wt = w.item_templates.get(int(weapon.get("index", 0)))
        n_rand = abs((wt.dcMax - wt.dcMin)) // 5 if wt is not None else 0
        luck = int(weapon.get("lucky", 0))
        p.bag.remove(oil_item)
        p.clear_quick_ref(int(oil_item.get("makeIndex", 0)))
        if random.randrange(20) == 1:                    # Random(20)=1 → 诅咒
            luck = max(-10, luck - 1)
            w.send_system(p, "你的武器被诅咒了。")
        elif luck < 0:
            luck += 1                                    # 诅咒先被洗掉
            w.send_system(p, "武器被加幸运了...")
        elif luck < 1:
            luck += 1
            w.send_system(p, "武器被加幸运了...")
        elif luck < 3 and random.randrange(n_rand + 6) == 1:
            luck += 1
            w.send_system(p, "武器被加幸运了...")
        elif luck < 7 and n_rand > 0 and random.randrange(n_rand * 40) == 1:
            luck += 1
            w.send_system(p, "武器被加幸运了...")
        elif luck >= 7:
            w.send_system(p, "无效，武器幸运已满（+7）。")
        else:
            w.send_system(p, "无效。")
        weapon["lucky"] = luck
        p.recompute(w)
        w.send_stats(p)
        self.send_bag_update(p)

    def _repair_weapon(self, p, oil_item: dict, super_repair: bool = False) -> None:
        """原版 ``RepairWeapon`` / ``SuperRepairWeapon``（ObjBase.pas:23673-23700）。

        修复油(Shape=9) / 战神油(Shape=10) 双击即修**当前佩戴的武器**，不需要 NPC，
        也不需要把武器放进任何窗口：

        - 普通修复油：``Dec(DuraMax, (DuraMax-Dura) div 30)``（nRepairItemDecDura），
          再 ``Inc(Dura, Min(5000, DuraMax-Dura))``——补持久的代价是掉上限；
        - 战神油：``Dura := DuraMax``，上限不变。

        只有真修成了才消耗油（原版 ``EatItems`` 返回 True 才 Dispose 物品）。
        """
        w = self.world
        weapon = p.equipment[U_WEAPON]
        if weapon is None:
            w.send_system(p, "请先装备武器。")
            return
        dura = int(weapon.get("durability", 0))
        dura_max = int(weapon.get("durabilityMax", 0))
        if dura_max <= dura:
            w.send_system(p, "武器持久已满，不需要修复。")
            return
        if super_repair:
            new_dura, new_max = dura_max, dura_max
        else:
            new_max = max(0, dura_max - (dura_max - dura) // 30)
            gain = min(5000, new_max - dura)
            if gain <= 0:
                w.send_system(p, "武器磨损过重，修复油无效。")
                return
            new_dura = dura + gain
        p.bag.remove(oil_item)
        p.clear_quick_ref(int(oil_item.get("makeIndex", 0)))
        weapon["durabilityMax"] = new_max
        weapon["durability"] = new_dura
        # 原版 RM_DURACHANGE → SM_DURACHANGE 只按装备栏 slot 原地更新持久；
        # bagUpdate 负责把消耗掉的油从背包移除（并同步金币/负重）。
        w.send_dura_change(p, U_WEAPON, weapon)
        w.send_system(p, "武器修复成功...")
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
