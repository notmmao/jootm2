"""批次 D/E：行会、攻城、组队、仓库、交易。

直译 Java ``GameService`` 2612–3070 行。所有下发字段与消息文本保持一致。
"""
from __future__ import annotations

from .util import _int, _str
from .world_extra import TradeSession
from .world import SIGHT_RANGE, Player

GUILD_TYPES = frozenset({
    "createGuild", "joinGuild", "leaveGuild", "guildInfo", "guildChat",
    "siege", "guildWar", "guildAlly",
})
PARTY_TYPES = frozenset({
    "partyInvite", "partyAccept", "partyReject", "partyLeave", "partyChat",
    "partyInfo",
})
STORAGE_TYPES = frozenset({"storageOpen", "storageStore", "storageTake"})
TRADE_TYPES = frozenset({
    "tradeRequest", "tradeAccept", "tradeReject", "tradeCancel",
    "tradeAddItem", "tradeRemoveItem", "tradeAddGold", "tradeConfirm",
    "tradeSetGold",
})
# 多人社交扩展：在线列表（本项目新增，原版无窗口）、查看他人装备
# （原版 Ctrl+右键 → CM_QUERYUSERSTATE=82 → SM_SENDUSERSTATE=751）。
SOCIAL_EXTRA_TYPES = frozenset({"onlineList", "queryUserState"})
SOCIAL_TYPES = GUILD_TYPES | PARTY_TYPES | STORAGE_TYPES | TRADE_TYPES | SOCIAL_EXTRA_TYPES


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
    # 原版 EnterGroup/LeaveGroup（ObjBase.pas:6073-6084）：邀请须对方同意；
    # SM_GROUPMESSAGE 群聊遍历成员（:2325-2327）。本项目队伍数据在 world.parties，
    # 前端全靠 partyInfo 推送绘制队员列表/血条。

    def _broadcast_party_info(self, party) -> None:
        # 快照构造 + 广播下沉到 world（tick_extra 也要用它刷新队员血量）
        if party is not None:
            self.world.broadcast_party_info(party)

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
        if target.id == p.id:
            w.send_system(p, "不能邀请自己。")
            return
        if w.party_of(target.id) is not None:
            w.send_system(p, f"{target.name} 已在队伍中。")
            return
        # 邀请请求须对方确认（原版 CM_ADDGROUPMEMBER 由被邀请方 Client 发起）
        w.party_invites[target.id] = p.id
        w.send_system(p, f"已向 {target.name} 发出组队邀请。")
        if target.session is not None:
            target.session.send_json({"type": "partyInvite", "fromId": p.id,
                                      "fromName": p.name})

    def _party_accept(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        inviter_id = w.party_invites.pop(p.id, None)
        if inviter_id is None:
            w.send_system(p, "没有待接受的组队邀请。")
            return
        inviter = w.actor(inviter_id)
        if not isinstance(inviter, Player) or not inviter.alive:
            w.send_system(p, "邀请人已不在线。")
            return
        party = w.party_of(inviter_id)
        if party is None:
            party = w.create_party(inviter_id, p.id)
        elif not w.join_party(p.id, inviter_id):
            w.send_system(p, "加入队伍失败。")
            return
        w.send_system(inviter, f"{p.name} 加入了队伍。")
        w.send_system(p, f"你加入了 {inviter.name} 的队伍。")
        self._broadcast_party_info(party)

    def _party_reject(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        inviter_id = w.party_invites.pop(p.id, None)
        if inviter_id is None:
            return
        inviter = w.actor(inviter_id)
        if isinstance(inviter, Player):
            w.send_system(inviter, f"{p.name} 拒绝了你的组队邀请。")

    def _party_leave(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        party = w.party_of(p.id)
        ok = w.leave_party(p.id)
        w.send_system(p, "你退出了队伍。" if ok else "你不在队伍中。")
        if ok and party is not None:
            w.broadcast_party_info(party)
            for pid in party.member_ids:
                a = w.actor(pid)
                if isinstance(a, Player) and a.session is not None:
                    a.session.send_json({"type": "partySys",
                                         "text": f"{p.name} 退出了队伍。"})

    def _party_info(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        party = self.world.party_of(p.id)
        if party is None:
            p.session.send_json({"type": "partyInfo", "leaderId": -1, "members": []})
        else:
            self._broadcast_party_info(party)

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
    # 原版面对面交易（ObjBase.pas:13918 ServerGetDealTry → :14241 SM_DEALSUCCESS）：
    # 请求→开窗→放物/改金→双方锁定确认→交换。前端两个窗口（自己/对方）靠
    # tradeStart 开窗 + tradeState 增量刷新状态，物品图标走 items_json 的 looks。

    def _trade_side(self, t: TradeSession, who: str) -> dict:
        """某一侧的展示数据：谁、放入物品、放入金币、是否已确认锁定。"""
        if who == "a":
            p, items, gold, confirm = t.a, t.a_items, t.a_gold, t.a_confirm
        else:
            p, items, gold, confirm = t.b, t.b_items, t.b_gold, t.b_confirm
        return {
            "name": p.name,
            "items": [p.instance_json(self.world, it) for it in items],
            "gold": gold, "confirm": confirm,
        }

    def _trade_state_msg(self, t: TradeSession) -> dict:
        """发给 A 的快照：self=A、other=B；发给 B 时两侧对调（见 _push_trade）。"""
        return {"type": "tradeState", "self": self._trade_side(t, "a"),
                "other": self._trade_side(t, "b")}

    def _push_trade(self, t: TradeSession, opening: bool = False) -> None:
        """把交易窗口状态推给双方（原版 SM_DEALMENU / REMOTEADDITEM 等）。
        opening=True 时先发 tradeStart 开窗，再发一次状态。"""
        for viewer, side, other in ((t.a, "a", "b"), (t.b, "b", "a")):
            if viewer.session is None:
                continue
            if opening:
                viewer.session.send_json({"type": "tradeStart",
                                          "selfName": viewer.name,
                                          "otherName": (t.b if viewer is t.a else t.a).name})
            m = self._trade_state_msg(t)
            if viewer is t.b:      # B 视角两侧对调
                m["self"], m["other"] = m["other"], m["self"]
            viewer.session.send_json(m)

    def _trade_close(self, t: TradeSession, reason: str) -> None:
        for p in (t.a, t.b):
            if p.session is not None:
                p.session.send_json({"type": "tradeEnd", "reason": reason})

    def _trade_pair_ok(self, p, t: TradeSession | None) -> bool:
        """面对面校验（原版 ServerGetDealTry:13918-13940）：同图、活体、距离 ≤2。"""
        w = self.world
        if t is None:
            w.send_system(p, "没有进行中的交易。")
            return False
        other = t.b if p is t.a else t.a
        if (not other.alive or other.map is not p.map
                or max(abs(other.x - p.x), abs(other.y - p.y)) > 2):
            w.send_system(p, "交易对象已离开或距离太远。")
            self._trade_cancel_session(t)
            return False
        return True

    def _trade_cancel_session(self, t: TradeSession) -> None:
        """退还双方物品/金币并清会话（原版 BrokeDeal/ResetDeal:13942/13967）。"""
        w = self.world
        if w.trades.get(t.a.id) is not t:
            return
        w.trades.pop(t.a.id, None)
        w.trades.pop(t.b.id, None)
        t.a.bag.extend(t.a_items)
        t.b.bag.extend(t.b_items)
        t.a_gold = t.b_gold = 0
        t.a_items, t.b_items = [], []
        self._trade_close(t, "cancel")
        w.send_system(t.a, "交易取消。")
        w.send_system(t.b, "交易取消。")
        self.send_bag_update(t.a)
        self.send_bag_update(t.b)

    def _trade_request(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        target = w.player(_str(msg, "name") or "")
        if target is None or not target.alive:
            w.send_system(p, "玩家不在线。")
            return
        if target.id == p.id:
            w.send_system(p, "不能和自己交易。")
            return
        if max(abs(target.x - p.x), abs(target.y - p.y)) > 2 or target.map is not p.map:
            w.send_system(p, "对方不在附近。")
            return
        if p.id in w.trades or target.id in w.trades:
            w.send_system(p, "对方或你正在交易中。")
            return
        w.trade_requests[target.id] = p.id
        w.send_system(p, f"已向 {target.name} 发起交易请求。")
        w.send_system(target, f"{p.name} 想与你交易。")
        if target.session is not None:
            target.session.send_json({"type": "tradeInvite", "fromId": p.id,
                                      "fromName": p.name})

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
        if not isinstance(a, Player) or not a.alive:
            return
        if max(abs(a.x - p.x), abs(a.y - p.y)) > 2 or a.map is not p.map:
            w.send_system(p, "对方已走远，交易取消。")
            return
        t = TradeSession(a, p)
        w.trades[a.id] = w.trades[p.id] = t
        w.send_system(a, f"{p.name} 接受了交易。")
        w.send_system(p, "交易开始。")
        self._push_trade(t, opening=True)

    def _trade_reject(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        requester_id = w.trade_requests.pop(p.id, None)
        if requester_id is None:
            return
        a = w.actor(requester_id)
        if isinstance(a, Player):
            w.send_system(a, f"{p.name} 拒绝了你的交易请求。")

    def _trade_cancel(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        t = self.world.trades.get(p.id)
        if t is not None:
            self._trade_cancel_session(t)

    def _trade_add_item(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if not self._trade_pair_ok(p, t):
            return
        found = p.bag_item(_int(msg, "makeIndex", -1))
        if found is None:
            w.send_system(p, "物品不存在。")
            return
        mine = t.a_items if p is t.a else t.b_items
        if len(mine) >= 14:
            w.send_system(p, "交易栏已满。")
            return
        mine.append(found)
        p.bag.remove(found)
        p.clear_quick_ref(int(found.get("makeIndex", 0)))
        w.send_system(p, "已放入一件物品。")
        self._push_trade(t)
        self.send_bag_update(p)

    def _trade_remove_item(self, session, msg: dict) -> None:
        """把交易栏物品取回背包（原版 CM_DEALDELITEM → SM_DELITEM_OK）。"""
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if not self._trade_pair_ok(p, t):
            return
        mine = t.a_items if p is t.a else t.b_items
        mi = _int(msg, "makeIndex", -1)
        found = next((it for it in mine if int(it.get("makeIndex", 0)) == mi), None)
        if found is None:
            return
        mine.remove(found)
        p.bag.append(found)
        w.send_system(p, "已取回一件物品。")
        self._push_trade(t)
        self.send_bag_update(p)

    def _trade_add_gold(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if not self._trade_pair_ok(p, t):
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
        self._push_trade(t)
        self.send_bag_update(p)

    def _trade_set_gold(self, session, msg: dict) -> None:
        """把交易栏金币设为指定总额（原版 CM_DEALCHGGOLD 语义）：
        差额自动在「背包 ↔ 交易栏」之间调整，可下调/取回。"""
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if not self._trade_pair_ok(p, t):
            return
        want = max(0, _int(msg, "amount", 0))
        cur = t.a_gold if p is t.a else t.b_gold
        held = p.gold + cur
        if want > held:
            w.send_system(p, "金币不足。")
            return
        if p is t.a:
            t.a_gold = want
        else:
            t.b_gold = want
        p.gold = held - want
        self._push_trade(t)
        self.send_bag_update(p)

    def _trade_confirm(self, session, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        t = w.trades.get(p.id)
        if not self._trade_pair_ok(p, t):
            return
        if p is t.a:
            t.a_confirm = True
        else:
            t.b_confirm = True
        self._push_trade(t)
        if t.a_confirm and t.b_confirm:
            self._execute_trade(t)
        else:
            w.send_system(p, "已确认，等待对方确认。")

    def _execute_trade(self, t: TradeSession) -> None:
        w = self.world
        # 容量校验（原版 :14163-14167）：双方背包要放得下对方物品
        if len(t.a.bag) + len(t.b_items) > 46 or len(t.b.bag) + len(t.a_items) > 46:
            w.send_system(t.a, "背包空间不足，交易失败。")
            w.send_system(t.b, "背包空间不足，交易失败。")
            self._trade_cancel_session(t)
            return
        t.b.bag.extend(t.a_items)
        t.a.bag.extend(t.b_items)
        t.a.gold += t.b_gold
        t.b.gold += t.a_gold
        w.trades.pop(t.a.id, None)
        w.trades.pop(t.b.id, None)
        w.send_system(t.a, "交易完成。")
        w.send_system(t.b, "交易完成。")
        self._trade_close(t, "success")
        self.send_bag_update(t.a)
        self.send_bag_update(t.b)
        w.send_stats(t.a)
        w.send_stats(t.b)

    # ---- 多人扩展：在线列表 / 查看他人装备 ------------------------------

    def _online_list(self, session, msg: dict) -> None:
        """在线玩家名单（原版无此功能，本项目新增）。

        只回名字/等级/职业/所在地图，不暴露坐标/血量（隐私且省带宽）。
        原版 GM 命令 who/total（ObjBase.pas:11680）只报在线人数，不列姓名。
        """
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        players = []
        for other in w.players():
            players.append({
                "name": other.name,
                "level": other.level,
                "job": other.job,
                "map": other.map.name if other.map else "",
            })
        players.sort(key=lambda e: (-e["level"], e["name"]))
        p.session.send_json({"type": "onlineList", "players": players})

    def _query_user_state(self, session, msg: dict) -> None:
        """查看他人装备：原版 Ctrl+右键 → CM_QUERYUSERSTATE(82) → SM_SENDUSERSTATE(751)。

        火炬 M2Server/ObjBase.pas:14284-14317：近距校验 CretInNearXY 后回
        TUserStateInfo（名字/颜色/外观 + UseItems[0..12]），13 槽装备。
        本项目的 ``items_json`` 已把装备项带 ``slot``，过滤出来即 TUserStateInfo.UseItems。
        """
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        actor_id = _int(msg, "id", _int(msg, "actorId", 0))
        target = w.actor(actor_id)
        if target is None or not isinstance(target, Player) or not target.alive:
            return
        # 原版 CretInNearXY：同图 + 近距（SIGHT_RANGE 与视野/说话一致）
        if target.map is not p.map or p.distance_to(target) > SIGHT_RANGE:
            w.send_system(p, "目标不在附近，无法查看装备。")
            return
        equipped = [it for it in target.items_json(w) if it.get("slot") is not None]
        p.session.send_json({
            "type": "userState",
            "id": target.id,
            "name": target.name,
            "level": target.level,
            "job": target.job,
            "sex": target.sex,
            "hair": target.hair,
            "nameColor": target.name_color(),
            "dress": target.dress_looks(w),
            "weapon": target.weapon_shape(w),
            "items": equipped,
        })
