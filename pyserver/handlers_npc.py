"""批次 C：NPC 对话、脚本选择、买卖物品。

直译 Java ``GameService.npcTalk / npcSelect / handleNpcResult / buyItem /
sellItem / buyPrice``，脚本执行走 ``NpcScriptEngine``。
"""
from __future__ import annotations

from .npc_script import NpcScriptEngine
from .util import BAG_SIZE, _int, _str, delphi_round

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
        r = self._engine().execute(p, npc, label)
        if is_buy:
            r.shop_mode = "buy"
        elif is_sell:
            r.shop_mode = "sell"
        if (not r.say_text and r.shop_mode is None and not r.closed
                and not r.moved and label.lower() != "main"):
            r = self._engine().execute(p, npc, "main")
        self._handle_npc_result(session, p, npc, r)

    def _handle_npc_result(self, session, p, npc, r) -> None:
        w = self.world
        if r.moved and r.moved_map is not None:
            session.send_json(w.map_snapshot(p, "changeMap"))
            w.broadcast_entity_add(p)
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

    def _sell_price(self, npc, t) -> int:
        """原版 ``ClientSellItem``（ObjNpc.pas:2156）：
        ``nPrice := GetSellItemPrice(GetUserItemPrice(UserItem))``，
        即 ``Round(GetItemPrice(...) / 2.0)``（GetSellItemPrice :2127-2130）。

        ``GetItemPrice``（:999-1024）语义：
        - 该店 ``[goods]`` 补货清单里的物品 → 补货价 ``Round(Price * 1.1)``；
        - 不在清单但 StdMode 在脚本头 ``+N`` 列表里 → ``StdItem.Price``；
        - 都不满足 → ``-1``，``nPrice <= 0`` 卖不掉（NPC 不收这类东西）。
        所以本店有售的物品卖价是 ``Round(Round(Price*1.1)/2)``（如木剑 28），
        其余物品只有类型通过才按 ``Round(Price/2)`` 收。
        耐久折价（肉 StdMode 40 / 矿 43 / 武器装备，GetUserItemPrice:1838+）未做，
        见 §39.7 遗留。
        """
        if npc is not None:
            for g in self.world.goods(npc):
                if g["index"] == t.index:
                    return delphi_round(delphi_round(t.price * 1.1) / 2.0)
            types = getattr(getattr(npc, "script", None), "item_types", None)
            if types and t.stdMode not in types:
                return -1
        return delphi_round(t.price / 2.0)

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
                           "price": max(0, self._sell_price(npc, t))})

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
        price = self._sell_price(npc, t)
        if price <= 0:
            w.send_system(p, "这个物品卖不出价钱。")
            return
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        p.gold += price
        w.send_system(p, f"出售了 {t.name}，获得 {price} 金币。")
        self.send_bag_update(p)
