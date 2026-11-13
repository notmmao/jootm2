"""批次 D/E：行会、攻城、组队、仓库、交易。

直译 Java ``GameService`` 2612–3070 行。所有下发字段与消息文本保持一致。
"""
from __future__ import annotations

from .util import _int, _str
from .world_extra import TradeSession
from .world import Player

GUILD_TYPES = frozenset({
    "createGuild", "joinGuild", "leaveGuild", "guildInfo", "guildChat",
    "siege", "guildWar", "guildAlly",
})
PARTY_TYPES = frozenset({"partyInvite", "partyLeave", "partyChat"})
STORAGE_TYPES = frozenset({"storageOpen", "storageStore", "storageTake"})
TRADE_TYPES = frozenset({
    "tradeRequest", "tradeAccept", "tradeCancel",
    "tradeAddItem", "tradeAddGold", "tradeConfirm",
})
SOCIAL_TYPES = GUILD_TYPES | PARTY_TYPES | STORAGE_TYPES | TRADE_TYPES


class SocialHandlers:
    """Mixin：依赖 ``self.world`` / ``self.find_by_session`` / ``self.send_bag_update``。"""

    # ---- 行会 / 攻城 ----------------------------------------------------

    def _create_guild(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        name = _str(msg, "name")
        if not name or not name.strip():
            return
        g = self.world.create_guild(name, p.name)
        if g is None:
            self.world.send_system(p, "创建行会失败（名字重复或你已在行会）。")
        else:
            self.world.send_system(p, f"你创建了行会 {name}，并成为会长。")

    def _join_guild(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        name = _str(msg, "name") or ""
        if self.world.join_guild(p.name, name):
            self.world.send_system(p, f"你加入了行会 {name}。")
        else:
            self.world.send_system(p, "加入行会失败（行会不存在或你已在行会）。")

    def _leave_guild(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        self.world.send_system(p, "你已退出行会。" if self.world.leave_guild(p.name)
                               else "你不在任何行会中。")

    def _guild_info(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        g = self.world.guild_of(p.name)
        if g is None:
            self.world.send_system(p, "你不在任何行会中。")
            return
        text = f"行会 {g.name}（会长 {g.master_name}，{g.member_count()} 人）"
        if g.owns_castle:
            text += " [沙巴克城主]"
        self.world.send_system(p, text)
        self.world.send_system(p, "成员: " + ", ".join(g.members))

    def _guild_chat(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        g = self.world.guild_of(p.name)
        if g is None:
            return
        text = _str(msg, "text")
        if not text:
            return
        o = {"type": "guildChat", "name": p.name, "guild": g.name, "text": text}
        for member in g.members:
            mp = self.world.player(member)
            if mp is not None and mp.session is not None:
                mp.session.send_json(o)

    def _siege(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        g = w.guild_of(p.name)
        if g is None or g.master_name != p.name:
            w.send_system(p, "只有会长可以发起攻城。")
            return
        dmg = w.attack_castle_door(p, max(1, p.combat.get("dcMax", 0)))
        if dmg > 0:
            w.send_system(p, f"攻击城门，造成 {dmg} 伤害。")
            return
        w.send_system(p, "你的行会开始攻城（5 分钟）！" if w.declare_siege(g.name, 300)
                      else "无法攻城（已有攻城进行中）。")

    def _guild_war(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        g = w.guild_of(p.name)
        if g is None or g.master_name != p.name:
            w.send_system(p, "只有会长可以宣战。")
            return
        target = _str(msg, "name") or ""
        w.send_system(p, f"你的行会与 {target} 宣战。" if w.declare_guild_war(g.name, target)
                      else "宣战失败（行会不存在）。")

    def _guild_ally(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        g = w.guild_of(p.name)
        if g is None or g.master_name != p.name:
            w.send_system(p, "只有会长可以结盟。")
            return
        target = _str(msg, "name") or ""
        w.send_system(p, f"你的行会与 {target} 结盟。" if w.make_guild_ally(g.name, target)
                      else "结盟失败（行会不存在）。")

    # ---- 组队 -----------------------------------------------------------

    def _party_invite(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        name = _str(msg, "name")
        if not name:
            return
        target = w.player(name)
        if target is None or not target.alive:
            w.send_system(p, "玩家不在线。")
            return
        party = w.party_of(p.id)
        if party is not None:
            if w.join_party(target.id, party.leader_id()):
                w.send_system(p, f"{target.name} 加入了队伍。")
                w.send_system(target, f"你加入了 {p.name} 的队伍。")
            else:
                w.send_system(p, "邀请失败（对方已在队伍中）。")
        else:
            w.create_party(p.id, target.id)
            w.send_system(p, f"你和 {target.name} 组成了队伍。")
            w.send_system(target, f"你和 {p.name} 组成了队伍。")

    def _party_leave(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        self.world.send_system(p, "你退出了队伍。" if self.world.leave_party(p.id)
                               else "你不在队伍中。")

    def _party_chat(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        party = self.world.party_of(p.id)
        if party is None:
            return
        text = _str(msg, "text")
        if not text:
            return
        o = {"type": "partyChat", "name": p.name, "text": text}
        for pid in party.member_ids:
            a = self.world.actor(pid)
            if isinstance(a, Player) and a.session is not None:
                a.session.send_json(o)

    # ---- 仓库 -----------------------------------------------------------

    def _storage_open(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        items = []
        for it in p.storage:
            t = w.item_templates.get(int(it.get("index", 0)))
            items.append(Player.storage_json(it, t.name if t else "?",
                                             t.looks if t else 0))
        session.send_json({"type": "storage", "items": items})

    def _storage_npc_ok(self, p, msg) -> bool:
        """原版 ClientStorageItem（ObjBase.pas:24713-24718）：存/取都要
        「仓库 NPC 同图且 15 格内」。消息没带 npcId 时退回旧口径（放行），
        兼容旧客户端消息。"""
        npc_id = _int(msg, "npcId", -1)
        if npc_id < 0:
            return True
        npc = self.world.npc(npc_id)
        return (npc is not None and npc.map is p.map
                and abs(npc.x - p.x) < 15 and abs(npc.y - p.y) < 15)

    def _storage_list_msg(self, p) -> dict:
        w = self.world
        items = []
        for it in p.storage:
            t = w.item_templates.get(int(it.get("index", 0)))
            items.append(Player.storage_json(it, t.name if t else "?",
                                             t.looks if t else 0))
        return {"type": "storageList", "items": items}

    def _storage_store(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        found = p.bag_item(_int(msg, "makeIndex", -1))
        if found is None:
            w.send_system(p, "物品不存在。")
            return
        # 原版容量 39（ObjBase.pas:24720 m_StorageItemList.Count < 39）
        if len(p.storage) >= 39:
            w.send_system(p, "仓库已满。请取出在放")
            return
        if not self._storage_npc_ok(p, msg):
            w.send_system(p, "保管失败。")
            return
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        p.storage.append(found)
        w.send_system(p, "已存入仓库。")
        self.send_bag_update(p)

    def _storage_take(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        make_index = _int(msg, "makeIndex", -1)
        found = next((it for it in p.storage
                      if int(it.get("makeIndex", 0)) == make_index), None)
        if found is None:
            w.send_system(p, "物品不存在。")
            return
        if not self._storage_npc_ok(p, msg):
            w.send_system(p, "你不能取回。")
            return
        p.storage.remove(found)
        p.bag.append(found)
        w.send_system(p, "已取出。")
        self.send_bag_update(p)
        # 原版取回成功客户端 DelStorageItem 移除该行（ClMain.pas:5232）；
        # 这里重发列表刷新打开中的取物窗口。
        session.send_json(self._storage_list_msg(p))

    # ---- 交易 -----------------------------------------------------------

    def _trade_request(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        target = w.player(_str(msg, "name") or "")
        if target is None or not target.alive:
            w.send_system(p, "玩家不在线。")
            return
        w.trade_requests[target.id] = p.id
        w.send_system(p, f"已向 {target.name} 发起交易请求。")
        w.send_system(target, f"{p.name} 想与你交易。")

    def _trade_accept(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        requester_id = w.trade_requests.pop(p.id, None)
        if requester_id is None:
            w.send_system(p, "没有待接受的交易请求。")
            return
        a = w.actor(requester_id)
        if a is None or not a.alive:
            return
        t = TradeSession(a, p)
        w.trades[a.id] = w.trades[p.id] = t
        w.send_system(a, f"{p.name} 接受了交易。")
        w.send_system(p, "交易开始。")

    def _trade_cancel(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.pop(p.id, None)
        if t is None:
            return
        t.a.bag.extend(t.a_items)
        t.b.bag.extend(t.b_items)
        t.a.gold += t.a_gold
        t.b.gold += t.b_gold
        w.trades.pop(t.a.id, None)
        w.trades.pop(t.b.id, None)
        w.send_system(t.a, "交易取消。")
        w.send_system(t.b, "交易取消。")
        self.send_bag_update(t.a)
        self.send_bag_update(t.b)

    def _trade_add_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if t is None:
            w.send_system(p, "没有进行中的交易。")
            return
        found = p.bag_item(_int(msg, "makeIndex", -1))
        if found is None:
            w.send_system(p, "物品不存在。")
            return
        mine, other = (t.a_items, t.b) if p is t.a else (t.b_items, t.a)
        mine.append(found)
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        w.send_system(p, "已放入一件物品。")
        w.send_system(other, "对方放入了一件物品。")

    def _trade_add_gold(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if t is None:
            w.send_system(p, "没有进行中的交易。")
            return
        amount = _int(msg, "amount", 0)
        if amount <= 0 or amount > p.gold:
            w.send_system(p, "金币不足。")
            return
        p.gold -= amount
        if p is t.a:
            t.a_gold += amount
        else:
            t.b_gold += amount
        w.send_system(p, f"已放入 {amount} 金币。")

    def _trade_confirm(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if t is None:
            return
        if p is t.a:
            t.a_confirm = True
        else:
            t.b_confirm = True
        if t.a_confirm and t.b_confirm:
            self._execute_trade(t)
        else:
            w.send_system(p, "已确认，等待对方确认。")

    def _execute_trade(self, t: TradeSession) -> None:
        w = self.world
        t.b.bag.extend(t.a_items)
        t.a.bag.extend(t.b_items)
        t.a.gold += t.b_gold
        t.b.gold += t.a_gold
        w.trades.pop(t.a.id, None)
        w.trades.pop(t.b.id, None)
        w.send_system(t.a, "交易完成。")
        w.send_system(t.b, "交易完成。")
        self.send_bag_update(t.a)
        self.send_bag_update(t.b)
        w.send_stats(t.a)
        w.send_stats(t.b)
