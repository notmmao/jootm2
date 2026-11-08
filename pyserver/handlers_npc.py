"""批次 C：NPC 对话、脚本选择、买卖物品。

直译 Java ``GameService.npcTalk / npcSelect / handleNpcResult / buyItem /
sellItem / buyPrice``，脚本执行走 ``NpcScriptEngine``。
"""
from __future__ import annotations

from .npc_script import NpcScriptEngine
from .util import BAG_SIZE, _int, _str

NPC_TYPES = frozenset({"npcTalk", "npcSelect", "buyItem", "sellItem"})


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
        total = self._buy_price(npc, t) * count
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

    def _buy_price(self, npc, t) -> int:
        for g in self.world.goods(npc):
            if g["index"] == t.index:
                return g["price"]
        return max(1, t.price)

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
        price = max(1, t.price // 2)
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        p.gold += price
        w.send_system(p, f"出售了 {t.name}，获得 {price} 金币。")
        self.send_bag_update(p)
