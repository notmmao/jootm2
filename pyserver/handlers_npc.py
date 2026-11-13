"""批次 C：NPC 对话、脚本选择、买卖物品。

直译 Java ``GameService.npcTalk / npcSelect / handleNpcResult / buyItem /
sellItem / buyPrice``，脚本执行走 ``NpcScriptEngine``。
"""
from __future__ import annotations

from .npc_script import NpcScriptEngine
from .util import BAG_SIZE, _int, _str, delphi_round
from .world import Player

NPC_TYPES = frozenset({"npcTalk", "npcSelect", "buyItem", "sellItem", "querySellPrice"})


class NpcHandlers:
    """Mixin：依赖 ``self.world`` / ``self.find_by_session`` / ``self.send_bag_update``。"""

    def _engine(self) -> NpcScriptEngine:
        """惰性创建（GameService 自己定义 __init__，mixin 的不会被 MRO 调用）。"""
        engine = getattr(self, "_npc_engine", None)
        if engine is None:
            engine = self._npc_engine = NpcScriptEngine(self.world)
        return engine

    def _npc_and_player(self, session, msg: dict):
        p = self.find_by_session(session)
        if p is None:
            return None, None
        npc = self.world.npc(_int(msg, "npcId", -1))
        if npc is None or p.distance_to(npc) > 15:
            return None, None
        return p, npc

    def _npc_talk(self, session, msg: dict) -> None:
        p, npc = self._npc_and_player(session, msg)
        if p is None:
            return
        self._handle_npc_result(session, p, npc,
                                self._engine().execute(p, npc, "main"))

    def _npc_select(self, session, msg: dict) -> None:
        p, npc = self._npc_and_player(session, msg)
        if p is None:
            return
        value = _str(msg, "value") or "main"
        label = value[1:] if value.startswith("@") else value
        if label.lower() in ("exit", "quit"):
            self._send_npc_close(session, npc.id)
            return
        is_buy, is_sell = label.lower() == "buy", label.lower() == "sell"
        is_storage, is_getback = (label.lower() == "storage",
                                  label.lower() == "getback")
        r = self._engine().execute(p, npc, label)
        if is_buy:
            r.shop_mode = "buy"
        elif is_sell:
            r.shop_mode = "sell"
        elif is_storage:
            r.storage_mode = "deposit"
        elif is_getback:
            r.storage_mode = "getback"
        if (not r.say_text and r.shop_mode is None and r.storage_mode is None
                and not r.closed and not r.moved and label.lower() != "main"):
            # 标签不存在（如普通 NPC 也被点了 @storage）→ 回主对话，
            # 同时清掉 storage_mode，避免无仓库能力的 NPC 开出仓库窗口。
            r.storage_mode = None
            r = self._engine().execute(p, npc, "main")
        self._handle_npc_result(session, p, npc, r)

    def _handle_npc_result(self, session, p, npc, r) -> None:
        w = self.world
        if r.moved and r.moved_map is not None:
            session.send_json(w.map_snapshot(p, "changeMap"))
            w.broadcast_entity_add(p)
            # SpaceMove 第二段：现身特效（260，原版 RM_SPACEMOVE_SHOW）
            w.space_move_show(p)
            return
        if r.closed:
            self._send_npc_close(session, npc.id)
            return
        o = {"type": "npcSay", "npcId": npc.id, "name": npc.name,
             "text": r.say_text, "options": list(r.options)}
        if r.shop_mode is not None:
            o["shop"] = r.shop_mode
            o["goods"] = w.goods(npc)
        session.send_json(o)
        # 仓库（原版 @storage → SM_SENDUSERSTORAGEITEM 开投放框；@getback →
        # SM_SAVEITEMLIST 开列表窗口，物品行点击即取回）
        if r.storage_mode == "deposit":
            session.send_json({"type": "storageOpen", "npcId": npc.id})
        elif r.storage_mode == "getback":
            items = []
            for it in p.storage:
                t = w.item_templates.get(int(it.get("index", 0)))
                items.append(Player.storage_json(
                    it, t.name if t else "?", t.looks if t else 0))
            session.send_json({"type": "storageList", "npcId": npc.id,
                               "items": items})

    @staticmethod
    def _send_npc_close(session, npc_id: int) -> None:
        session.send_json({"type": "npcClose", "npcId": npc_id})

    def _buy_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        npc = w.npc(_int(msg, "npcId", -1))
        if npc is None:
            return
        index = _int(msg, "index", -1)
        count = max(1, _int(msg, "count", 1))
        t = w.item_templates.get(index)
        if t is None:
            w.send_system(p, "物品不存在。")
            return
        # 原版 ``TMerchant.ClientBuyItem``（ObjNpc.pas:1939-1969）只在
        # m_GoodsList（= [goods] 补货出来的清单）里找商品，并用
        # GetUserPrice(GetUserItemPrice(...)) 定价。旧实现允许买**任意**
        # StdItems 模板，且 _buy_price 找不到就退回 t.price，导致「本店
        # 能买到的武器比原版多、还全是原价」。这里收紧到货单。
        goods = w.goods(npc)
        g = next((row for row in goods if row["index"] == index), None)
        if g is None:
            w.send_system(p, "本店不卖这个物品。")
            return
        total = g["price"] * count
        if p.gold < total:
            w.send_system(p, "金币不足。")
            return
        if len(p.bag) + count > BAG_SIZE:
            w.send_system(p, "背包已满，无法购买。")
            return
        p.gold -= total
        w.give_item(p, t.name, count)
        w.send_system(p, f"购买了 {t.name} x{count}，花费 {total} 金币。")
        self.send_bag_update(p)

    def _get_item_price(self, npc, t) -> int:
        """原版 ``GetItemPrice``（ObjNpc.pas:999-1024）：

        该店补货清单里的物品 → 价表价 ``Round(Price * 1.1)``（CheckItemPrice）；
        不在清单但 StdMode 在 ``+N``（m_ItemTypeList）里 → ``StdItem.Price``；
        都不满足 → ``-1``（NPC 不收）。
        """
        for g in self.world.goods(npc):
            if g["index"] == t.index:
                return delphi_round(t.price * 1.1)
        types = getattr(getattr(npc, "script", None), "item_types", None)
        if types and t.stdMode not in types:
            return -1
        return t.price

    def _user_item_price(self, npc, t, it) -> int:
        """原版 ``TMerchant.GetUserItemPrice``（ObjNpc.pas:1838-1919）逐行直译。

        在 GetItemPrice 基础上按耐久折价（StdMode > 4 且 DuraMax > 0 才进入）：
        - StdMode 40 肉：耐久 = 肉品质（ApplyMeatQuality 写入）；品质 = DuraMax
          时卖最贵，偏低打折，超过 DuraMax 反而暴跌（原版 else 分支就是减价，
          照抄）。
        - StdMode 43 矿：UserDuraMax 强制 ≥10000，纯度同上逻辑（超上限按 1.3 倍）。
        - 其余 StdMode > 4（武器装备）：升级点数 btValue[0..7] 加价（本项目无
          升级系统 → n14=0 跳过）→ 按 DuraMax 比例折算 → 磨损折旧。
        肉/矿走完自身分支后还会再走一遍装备折旧段（原版三个 if 顺序执行），
        属原版行为，照抄。
        """
        n10 = self._get_item_price(npc, t)
        if n10 <= 0:
            return n10
        it = it or {}
        dura = int(it.get("durability", t.duraMax) or 0)
        dumax = int(it.get("durabilityMax", t.duraMax) or 0)
        if t.stdMode > 4 and t.duraMax > 0 and dumax > 0:
            if t.stdMode == 40:                       # 肉（:1855-1865）
                if dura <= dumax:
                    n20 = n10 / 2.0 / dumax * (dumax - dura)
                    n10 = max(2, delphi_round(n10 - n20))
                else:
                    n10 += delphi_round(n10 / dumax * 2.0 * (dumax - dura))
            if t.stdMode == 43:                       # 矿（:1867-1878）
                if dumax < 10000:
                    dumax = 10000                     # 原版直接改 UserItem.DuraMax
                if dura <= dumax:
                    n20 = n10 / 2.0 / dumax * (dumax - dura)
                    n10 = max(2, delphi_round(n10 - n20))
                else:
                    n10 += delphi_round(n10 / dumax * 1.3 * (dumax - dura))
            if t.stdMode > 4:                         # 装备折旧段（:1880-1915）
                # n14 = sum(btValue[0..7])：升级属性点，本项目无升级 → 恒 0，
                # ``n10 div 5 * n14`` 加价段跳过。
                n10 = delphi_round(n10 / t.duraMax * dumax)
                n20 = n10 / 2.0 / dumax * (dumax - dura)
                n10 = max(2, delphi_round(n10 - n20))
        return n10

    def _sell_price(self, npc, t, it=None) -> int:
        """原版 ``ClientSellItem``（ObjNpc.pas:2156）：
        ``nPrice := GetSellItemPrice(GetUserItemPrice(UserItem))``，
        即 ``Round(GetUserItemPrice / 2.0)``（GetSellItemPrice :2127-2130）。
        ``<=0`` → 卖不掉（sellPrice 下发 0，客户端显示「???? 金币」）。
        """
        n10 = self._user_item_price(npc, t, it)
        if n10 <= 0:
            return -1
        return delphi_round(n10 / 2.0)

    def _query_sell_price(self, session, msg: dict) -> None:
        """原版 CM_MERCHANTQUERYSELLPRICE → SM_SENDBUYPRICE。

        客户端把物品放进出售框后立刻问价，界面上显示「价格: N 金币」；
        不实现的话玩家得先把物品卖掉才知道能卖多少。
        """
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        npc = w.npc(_int(msg, "npcId", -1))
        if npc is None:
            return
        found = p.bag_item(_int(msg, "makeIndex", -1))
        if found is None:
            return
        t = w.item_templates.get(int(found.get("index", 0)))
        if t is None:
            return
        # 原版：价格 <= 0 时下发 0，客户端显示「???? 金币」
        session.send_json({"type": "sellPrice", "npcId": npc.id,
                           "makeIndex": int(found.get("makeIndex", 0)),
                           "price": max(0, self._sell_price(npc, t, found))})

    def _sell_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        npc = w.npc(_int(msg, "npcId", -1))
        if npc is None:
            return
        found = p.bag_item(_int(msg, "makeIndex", -1))
        if found is None:
            w.send_system(p, "物品不存在。")
            return
        t = w.item_templates.get(int(found.get("index", 0)))
        if t is None:
            return
        # 原版 ClientSellItem：GetSellItemPrice 为 0 时直接不成交（连提示都没有，这里补一句）
        price = self._sell_price(npc, t, found)
        if price <= 0:
            w.send_system(p, "这个物品卖不出价钱。")
            return
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        p.gold += price
        w.send_system(p, f"出售了 {t.name}，获得 {price} 金币。")
        self.send_bag_update(p)
