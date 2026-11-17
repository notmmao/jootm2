"""游戏服务门面：WebSocket JSON 消息分派 + REST 管理接口。

对应 Java 侧 ``GameService``。Java switch 的 46 个分支已全部迁移，按领域拆分到
``handlers_items / handlers_npc / handlers_combat / handlers_social`` 四个 mixin，
本文件保留账号/角色/进图/移动/聊天/登出这些主干与统一分派。

所有 handler 都在 asyncio 单事件循环内同步执行，等价于 Java 的
``synchronized (world.lock)``，无需额外加锁。
"""
from __future__ import annotations

import json
import random
import time
from collections import Counter
from typing import Any, Callable

from .config import GM_ACCOUNTS, ServerConfig
from .db import Database
from .autopilot import (DEFAULT_PRESET, MANUAL_TYPES, PRESETS, AutoPilotManager,
                        normalize_preset)
from .handlers_combat import COMBAT_TYPES, CombatHandlers
from .handlers_items import ITEM_TYPES, ItemHandlers
from .handlers_npc import NPC_TYPES, NpcHandlers
from .handlers_social import SOCIAL_TYPES, SocialHandlers
from .templates import dumps
from .util import RED_NAME_PK_POINT, U_DRESS, U_WEAPON
from .version import load_version
from .world import (BAG_SIZE, GameWorld, Monster, Player, SIGHT_RANGE, direction_toward,
                    encode, normalize_exp, now_ms)
from .world_extra import (BODY_LEATHERY, BUTCH_INTERVAL_MS, BUTCH_WEAR_MAX,
                          BUTCH_WEAR_MIN, CORPSE_MS)

MAX_CHARS_PER_ACCOUNT = 2

# 46 个 Java switch 分支全部有对应实现。
MIGRATED_TYPES = frozenset({
    "ping", "login", "register", "listCharacters", "createCharacter",
    "deleteCharacter", "enterGame", "noticeOk", "move", "say", "logout",
    "backToSelect", "butch", "autopilot",
}) | ITEM_TYPES | NPC_TYPES | COMBAT_TYPES | SOCIAL_TYPES

PENDING_TYPES: frozenset[str] = frozenset()
ALL_TYPES = MIGRATED_TYPES


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


def _error(msg_type: str, text: str) -> dict:
    return {"type": msg_type, "ok": False, "error": text}


def _load_skills(raw: str) -> dict[int, dict]:
    """兼容两种落盘格式：``{magicId:{level,train,key}}`` 与旧的 ``{magicId:level}``。"""
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
        except ValueError:
            continue
        if isinstance(v, dict):
            out[mid] = {"level": _int(v, "level"), "train": _int(v, "train"),
                        "key": _int(v, "key")}
        elif isinstance(v, (int, float)):
            out[mid] = {"level": int(v), "train": 0, "key": 0}
    return out


class GameService(ItemHandlers, NpcHandlers, CombatHandlers, SocialHandlers):
    def __init__(self, config: ServerConfig, db: Database):
        self.config = config
        self.db = db
        self.world = GameWorld(config, db)
        self.started_at = now_ms()
        self.pending_hits: Counter[str] = Counter()
        self._reported_pending: set[str] = set()
        # 在线托管：进程内实例，绝不落库；on_disconnect 是唯一清理点。
        self.autopilot = AutoPilotManager(self)
        self._handlers: dict[str, Callable[[Any, dict], None]] = {
            # 账号 / 角色 / 场景主干
            "ping": self._ping,
            "login": self._login,
            "register": self._register,
            "listCharacters": self._list_characters,
            "createCharacter": self._create_character,
            "deleteCharacter": self._delete_character,
            "enterGame": self._enter_game,
            "noticeOk": self._notice_ok,
            "move": self._move,
            "say": self._say,
            "logout": self._logout,
            "backToSelect": self._back_to_select,
            "butch": self._butch,
            "autopilot": self._autopilot,
            # 物品 / 背包 / 装备 / 快捷栏
            "pickup": self._pickup,
            "useItem": self._use_item,
            "takeOnItem": self._take_on_item,
            "takeOffItem": self._take_off_item,
            "quickSlot": self._quick_slot,
            "drop": self._drop,
            # 原版 CM_DROPGOLD（ClMain.pas:3759 SendDropGold → ObjBase.pas:4771）
            "dropGold": self._drop_gold,
            "moveItem": self._move_item,
            "magicKeyChange": self._magic_key_change,
            # NPC / 商店
            "npcTalk": self._npc_talk,
            "npcSelect": self._npc_select,
            "buyItem": self._buy_item,
            "sellItem": self._sell_item,
            # 原版 CM_QUERYSELLPRICE（ClMain.pas:3683 SendQueryPrice）→ SM_SENDBUYPRICE
            "querySellPrice": self._query_sell_price,
            # 原版 CM_MERCHANTQUERYREPAIRCOST / CM_USERREPAIRITEM（Grobal2.pas:131-132）
            "queryRepairCost": self._query_repair_cost,
            "repairItem": self._repair_item,
            # 战斗 / 技能
            "attack": self._attack,
            "dirAttack": self._dir_attack,
            "castMagic": self._cast_magic,
            # 行会 / 攻城
            "createGuild": self._create_guild,
            "joinGuild": self._join_guild,
            "leaveGuild": self._leave_guild,
            "guildInfo": self._guild_info,
            "guildChat": self._guild_chat,
            "siege": self._siege,
            "guildWar": self._guild_war,
            "guildAlly": self._guild_ally,
            # 组队
            "partyInvite": self._party_invite,
            "partyAccept": self._party_accept,
            "partyReject": self._party_reject,
            "partyLeave": self._party_leave,
            "partyChat": self._party_chat,
            "partyInfo": self._party_info,
            # 多人扩展：在线列表 / 查看他人装备（Ctrl+右键，原版 CM_QUERYUSERSTATE）
            "onlineList": self._online_list,
            "queryUserState": self._query_user_state,
            # 仓库
            "storageOpen": self._storage_open,
            "storageStore": self._storage_store,
            "storageTake": self._storage_take,
            # 交易
            "tradeRequest": self._trade_request,
            "tradeAccept": self._trade_accept,
            "tradeReject": self._trade_reject,
            "tradeCancel": self._trade_cancel,
            "tradeAddItem": self._trade_add_item,
            "tradeRemoveItem": self._trade_remove_item,
            "tradeAddGold": self._trade_add_gold,
            "tradeSetGold": self._trade_set_gold,
            "tradeConfirm": self._trade_confirm,
        }
        if set(self._handlers) != set(MIGRATED_TYPES):
            raise RuntimeError("消息分派表与 MIGRATED_TYPES 不一致")
        missing = MIGRATED_TYPES - self._handlers.keys()
        if missing:
            raise RuntimeError(f"MIGRATED_TYPES 中有未实现的 handler: {sorted(missing)}")

    # ---- 消息入口 -------------------------------------------------------

    def on_message(self, session: Any, text: str) -> None:
        try:
            msg = json.loads(text)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        msg_type = msg.get("type")
        if not isinstance(msg_type, str):
            return
        # 原版 m_boDeath：死亡后停在黑白屏，只能聊天 / 返回角色选择 / 退出，
        # 不能走动、攻击、施法、吃药、买东西（服务端一并拦，防止绕过客户端）。
        owner = None
        if msg_type not in ("ping", "login", "register", "listCharacters",
                            "createCharacter", "deleteCharacter", "enterGame",
                            "noticeOk", "say", "logout", "backToSelect"):
            owner = self.find_by_session(session)
            if owner is not None and getattr(owner, "dead", False):
                return
        # 在线托管「手动优先」：收到任何手动游戏动作立即暂停（服务端为准，
        # 前端另有一份停发/提示）。托管自身是直接调 handler 而非走 on_message，
        # 因此不会误伤自己的动作；聊天/社交不在 MANUAL_TYPES 里，边聊天边托管
        # 不会被打断。
        if msg_type in MANUAL_TYPES:
            if owner is None:
                owner = self.find_by_session(session)
            if owner is not None:
                self.autopilot.pause(owner)
        handler = self._handlers.get(msg_type)
        if handler is not None:
            try:
                handler(session, msg)
            except Exception as e:  # 单条消息出错不能拖垮整个 loop
                print(f"[ws] handler {msg_type} failed: {type(e).__name__}: {e}")
            return
        if msg_type in PENDING_TYPES:
            self.pending_hits[msg_type] += 1
            if msg_type not in self._reported_pending:
                self._reported_pending.add(msg_type)
                print(f"[migration] '{msg_type}' not ported yet (ignored, Java switch parity)")

    def on_disconnect(self, session: Any) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        # 托管生命周期的唯一清理点：登出 / 退回选角 / 断线全部汇合于此。
        # 只清内存实例，不写任何状态到库，重登必须玩家显式再开一次。
        self.autopilot.on_disconnect(p)
        # 社交会话清理：退队并通知队友、取消交易并退还双方物品/金币，
        # 否则对方窗口卡死、交易栏物品随断线凭空消失。
        party = self.world.party_of(p.id)
        if self.world.leave_party(p.id) and party is not None:
            self.world.broadcast_party_info(party)
        self.world.party_invites.pop(p.id, None)
        self.world.trade_requests.pop(p.id, None)
        trade = self.world.trades.get(p.id)
        if trade is not None:
            self._trade_cancel_session(trade)
        self.persist(p)
        self.world.remove_player(p)
        self.world.broadcast_around(p.map, {"type": "entityRemove", "id": p.id},
                                   p.x, p.y, SIGHT_RANGE)

    def find_by_session(self, session: Any) -> Player | None:
        return next((p for p in self.world.players() if p.session is session), None)

    # ---- 账号 / 角色 ----------------------------------------------------

    @staticmethod
    def _ping(session: Any, msg: dict) -> None:
        session.send_text('{"type":"pong"}')

    def _register(self, session: Any, msg: dict) -> None:
        username, password = _str(msg, "username"), _str(msg, "password")
        if username is None or len(username) < 2 or password is None or len(password) < 4:
            session.send_json(_error("registerResult", "账号至少2位，密码至少4位。"))
            return
        account = self.db.register(username, password)
        if account is None:
            session.send_json(_error("registerResult", "账号已存在。"))
        else:
            session.send_json({"type": "registerResult", "ok": True})

    def _login(self, session: Any, msg: dict) -> None:
        username, password = _str(msg, "username"), _str(msg, "password")
        if username is None or password is None:
            session.send_json(_error("loginResult", "请输入账号和密码。"))
            return
        account = self.db.authenticate(username, password)
        if account is None:
            session.send_json(_error("loginResult", "账号或密码错误。"))
        else:
            session.account_id = account.id
            session.account_name = account.username
            session.send_json({"type": "loginResult", "ok": True})

    def _list_characters(self, session: Any, msg: dict) -> None:
        if session.account_id <= 0:
            return
        # 附带角色上次下线的地图坐标：客户端在选角界面就能预取该图首屏纹理，
        # 避免 enterGame 之后才临时下载几十个 WIL 分片（公网卡 6 秒超时兜底）。
        chars = []
        for c in self.db.list_characters(session.account_id):
            x, y = c.get("x"), c.get("y")
            chars.append({"name": c["name"], "sex": c["sex"], "hair": c["hair"],
                          "job": c["job"], "level": c["level"],
                          "map": c.get("map_name") or self.world.default_map.name,
                          "x": self.world.start_x if x is None else x,
                          "y": self.world.start_y if y is None else y})
        session.send_json({"type": "characterList", "characters": chars})

    def _create_character(self, session: Any, msg: dict) -> None:
        if session.account_id <= 0:
            return
        result = "createCharacterResult"
        name = _str(msg, "name")
        name = name.strip() if name else ""
        sex = _int(msg, "sex", 0)
        hair = _int(msg, "hair", 0)
        job = max(0, min(2, _int(msg, "job", 0)))
        if not name or len(name) > 14:
            session.send_json(_error(result, "名字长度需为1-14个字符。"))
            return
        if self.db.name_exists(name):
            session.send_json(_error(result, "该名字已被使用。"))
            return
        if len(self.db.list_characters(session.account_id)) >= MAX_CHARS_PER_ACCOUNT:
            session.send_json(_error(result, "角色数量已达上限。"))
            return

        base_hp, base_mp = (26, 16, 18), (12, 20, 16)
        world = self.world
        # 原版 ObjBase.pas:16400-16425（m_boNewHuman 分支）：新角色发
        # 蜡烛 / 金创药(小量) / 木剑 / 布衣(男|女)，用的是 !Setup.txt 的
        # Candle / BasicDrug / WoodenSword / ClothsMan / ClothsWoman 五个名字。
        # 原版只是塞进背包让玩家自己穿；这里把布衣与木剑直接落到装备槽，
        # 否则 1 级角色一进游戏就是光身子（用户反馈）。
        outfit = "布衣(男)" if sex == 0 else "布衣(女)"
        starter = (("蜡烛", -1), ("金创药(小量)", -1),
                   ("木剑", U_WEAPON), (outfit, U_DRESS))
        items: list[dict] = []
        for item_name, slot in starter:
            t = world.item_template_by_name(item_name)
            if t is None:
                continue
            items.append({
                "makeIndex": world.next_item_make_index(), "index": t.index,
                "durability": t.duraMax, "durabilityMax": t.duraMax,
                "lucky": 0, "slot": slot,
            })
        starter_magic = 3 if job == 0 else (1 if job == 1 else 2)
        rec = {
            "account_id": session.account_id, "name": name,
            "sex": sex, "hair": hair, "job": job, "level": 1, "exp": 0,
            "map_name": world.default_map.name, "x": world.start_x, "y": world.start_y,
            "dir": 4, "hp": base_hp[job], "mp": base_mp[job], "gold": 0,
            "home_map": world.default_map.name, "home_x": world.start_x,
            "home_y": world.start_y,
            "items": items, "storage": [], "quick_slots": [0] * 6,
            "skills": dumps({str(starter_magic): {"level": 0, "train": 0}}),
            "created_at": now_ms(),
        }
        self.db.insert_character(rec)
        session.send_json({"type": result, "ok": True})

    def _delete_character(self, session: Any, msg: dict) -> None:
        if session.account_id <= 0:
            return
        name = _str(msg, "name")
        if name is None or self.world.player(name) is not None:
            session.send_json(_error("deleteCharacterResult", "无法删除（角色在线或名字无效）。"))
            return
        self.db.delete_character(session.account_id, name)
        session.send_json({"type": "deleteCharacterResult", "ok": True})

    # ---- 进入游戏 -------------------------------------------------------

    def _validate_enter(self, session: Any, name: str) -> str | None:
        if session.account_id <= 0:
            return "未登录。"
        rec = self.db.find_character(name)
        if rec is None or rec["account_id"] != session.account_id:
            return "角色不存在。"
        if self.world.player(name) is not None:
            return "该角色已在线。"
        return None

    def _enter_game(self, session: Any, msg: dict) -> None:
        name = _str(msg, "name")
        if name is None:
            return
        err = self._validate_enter(session, name)
        if err is not None:
            session.send_json(_error("enterGameResult", err))
            return
        session.send_json({"type": "notice", "lines": list(self.world.notice_lines)})

    def _notice_ok(self, session: Any, msg: dict) -> None:
        name = _str(msg, "name")
        if name is None or self._validate_enter(session, name) is not None:
            return
        self._do_enter(session, name)

    def _do_enter(self, session: Any, name: str) -> None:
        world = self.world
        rec = self.db.find_character(name)
        if rec is None:
            return
        target_map = world.get_or_load_map(rec["map_name"])
        if target_map is None:
            rec["map_name"] = world.default_map.name
            rec["x"], rec["y"] = world.start_x, world.start_y
            rec["home_map"] = world.default_map.name
            rec["home_x"], rec["home_y"] = world.start_x, world.start_y
            target_map = world.default_map

        # 旧存档的 exp 是累计值，先换算成原版的「本级内经验」再建 Player
        lv, ex = normalize_exp(rec["level"], rec["exp"])
        # 发型：原版建角发送 1 + Random(5)（IntroScn.pas:1427），客户端只在
        # hair*2 > 1（即 hair >= 1）时才画头发（Actor.pas:3186）——hair 0 = 光头。
        # 旧版我们默认发 0，老存档里 hair==0 的角色迁移成随机 1..5。
        if rec["hair"] <= 0:
            rec["hair"] = random.randint(1, 5)
        p = Player(id=world.new_id(), name=rec["name"], x=rec["x"], y=rec["y"],
                   dir=rec["dir"], hp=rec["hp"], mp=rec["mp"], level=lv,
                   exp=ex, account_id=rec["account_id"],
                   character_id=rec["id"], sex=rec["sex"], hair=rec["hair"],
                   job=rec["job"], gold=rec["gold"], home_map=rec["home_map"],
                   home_x=rec["home_x"], home_y=rec["home_y"],
                   map=target_map, session=session, world_ref=world,
                   bag=list(rec["items"]), storage=list(rec["storage"]),
                   skills=_load_skills(rec["skills"]),
                   quick_slots=(list(rec["quick_slots"]) + [0] * 6)[:6],
                   is_gm=getattr(session, "account_name", "") in GM_ACCOUNTS)
        # NPC 脚本任务变量（SET/CHECK [n]）跨登录持久：不读库则领过一次性
        # 奖励的角色重登后标记归零，可无限重领（玛法领路人 5000 金币）。
        p.quest_params = self.db.load_quest_params(rec["id"])
        # PK 恶意值跨登录持久（原版存库，重登不归零）。
        p.pk_point = self.db.load_pk_point(rec["id"])
        p.distribute_items(world)
        p.recompute(world)
        if 12 in p.skills:
            p.long_hit_on = True       # 刺杀剑术登录自动开启（ObjBase.pas）
        # 原版 UsrEngn.pas:576-600：登录时 m_Abil.HP <= 0（上次死亡）会
        # ClearStatusTime、按 PK 状态切 home/红名死亡地图，落点
        # ``m_nHomeX - 2 + Random(5)``，并把 HP := 14（不是满血复活）。
        # 正常登录则沿用存档 HP/MP（原版 HP 是持久化的），只钳到上限。
        if p.hp <= 0:
            p.dead = False
            p.alive = True          # 与 m_boDeath 一并复位，否则复活后仍是「目标不可选」
            # 原版 UsrEngn.pas:576-600 + ObjBase.pas:3170-3177：红名（PKLevel>=2）
            # 复活到红名村 BADMANHOMEMAP='3' / (845,674)，落点同样 ±2 随机。
            if p.pk_point >= RED_NAME_PK_POINT:
                home = world.get_or_load_map("3") or world.default_map
                hx, hy = 845, 674
            else:
                home = world.get_or_load_map(p.home_map) or world.default_map
                hx, hy = p.home_x, p.home_y
            p.map = home
            p.x = max(0, min(home.width - 1, hx - 2 + random.randint(0, 4)))
            p.y = max(0, min(home.height - 1, hy - 2 + random.randint(0, 4)))
            p.dir = 4
            p.hp, p.mp = 14, 0
        else:
            p.hp = min(p.hp, p.max_hp)
            p.mp = min(p.mp, p.max_mp)
        session.player_name = p.name
        world.add_player(p)
        world.player_entered(p)   # 登录进图：休眠过的图重新刷怪

        session.send_json({"type": "enterGameResult", "ok": True})
        session.send_json(world.map_snapshot(p, "enterGame"))
        world.send_stats(p)
        world.broadcast_entity_add(p)

    # ---- 场景内 ---------------------------------------------------------

    def _move(self, session: Any, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        # 换图短锁：changeMap 到达客户端前发出的 move 仍在途，若不丢弃，
        # 会落在新地图被当成继续走，紧邻落点的回程门会把玩家立刻弹回原图
        # （表现为「进了下一层但地图名还是上一层」）。见 world.change_map。
        if now_ms() - p.map_change_at < 350:
            return
        direction = _int(msg, "dir", -1)
        if direction < 0 or direction > 7:
            return
        world = self.world
        p.running = bool(msg.get("run", False))
        old_x, old_y = p.x, p.y
        world.try_move(p, direction)
        if p.hide_until > now_ms() and (p.x != old_x or p.y != old_y):
            p.hide_until = 0
            world.send_stats(p)
            world.send_system(p, "你移动了，隐身状态解除。")
        world.broadcast_move(p)
        if p.x == old_x and p.y == old_y:
            return
        world.update_sight(p)
        gate = world.gate_at(p.map.name if p.map else "", p.x, p.y)
        if gate is not None:
            world.broadcast_around(p.map, {"type": "entityRemove", "id": p.id},
                                   p.x, p.y, SIGHT_RANGE)
            target = world.change_map(p, gate.dst_map, gate.dst_x, gate.dst_y)
            if target is not None:
                session.send_json(world.map_snapshot(p, "changeMap"))
                world.broadcast_entity_add(p)

    # ---- 在线托管（一期：策略档开关，无 LLM）----------------------------
    def _autopilot(self, session: Any, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
            return
        action = str(msg.get("action") or "status").lower()
        raw = msg.get("preset")
        preset = normalize_preset(raw) if raw is not None else None
        no_butch = (bool(msg.get("noButch")) if "noButch" in msg else None)
        cur = self.autopilot.get(p)
        if action in ("off", "stop") or (action == "toggle" and cur is not None
                                         and cur.active):
            self.autopilot.disable(p, "已手动关闭托管")
            return
        if action == "pause":
            self.autopilot.pause(p)
            return
        if action == "status":
            self.autopilot.push_state(p)
            return
        if action == "option":
            # 运行中即时切换「不挖肉」开关，不动档位/其他状态
            if cur is not None:
                if no_butch is not None:
                    cur.no_butch = no_butch
                cur.set_status(
                    f"已关闭挖肉 · 专注练级" if cur.no_butch
                    else f"已开启挖肉 · 顺手赚钱", now_ms(), force=True)
                self.world.send_system(
                    p, "[托管] 已切换为" + ("不挖肉" if cur.no_butch else "挖肉") + "模式。")
            self.autopilot.push_state(p)
            return
        if action == "preset":
            key = preset or (cur.preset if cur else DEFAULT_PRESET)
            if cur is not None and cur.active:
                cur.preset = key
                cur.set_status(f"策略切换 · {PRESETS[key]['name']}档",
                               now_ms(), force=True)
                self.world.send_system(p, f"[托管] 已切换为{PRESETS[key]['name']}档。")
            else:
                # 未开启时只回显档位偏好（偏好由前端 localStorage 保存，服务端不落库）
                self.autopilot.push_state(p, preset=key)
            return
        # action == on / toggle（当前未开启）
        key = preset or (cur.preset if cur else DEFAULT_PRESET)
        ok, why = self.autopilot.enable(p, key, no_butch=no_butch)
        if not ok:
            self.autopilot.push_state(p, preset=key, reason=why, ok=False)

    def _say(self, session: Any, msg: dict) -> None:
        p = self.find_by_session(session)
        text = _str(msg, "text")
        if p is None or not text:
            return
        if text.startswith("@"):
            # 原版 TPlayObject.ProcessUserLineMsg：@ 开头走命令通道，不广播。
            if not p.is_gm:
                self.world.send_system(p, "你没有使用该命令的权限。")
                return
            self._gm_command(p, text[1:].strip())
            return
        # 原版 ClMain.pas:3148-3153 / ObjBase.pas:11680-11691：
        # "/" 开头 = 密语，格式 "/玩家名 内容"，服务端 Say()→Whisper()→SM_WHISPER(103)。
        # 接收方看到「发送者=> 内容」，发送方本地回显「=>目标: 内容」。
        if text.startswith("/"):
            self._whisper(p, text[1:])
            return
        self.world.broadcast_all({"type": "chat", "id": p.id, "name": p.name,
                                  "text": text[:500]})

    def _whisper(self, p: Player, body: str) -> None:
        """密语（原版 TUserHuman.Whisper，ObjBase.pas:10670-10692）。

        只定向发给双方，不进世界频道；目标不在线/无连接时给发送方系统提示。
        """
        body = body.strip()
        if not body:
            self.world.send_system(p, "密语格式：/玩家名 内容")
            return
        parts = body.split(None, 1)
        target_name = parts[0]
        words = parts[1].strip() if len(parts) > 1 else ""
        if not words:
            self.world.send_system(p, "密语格式：/玩家名 内容")
            return
        target = self.world.player(target_name)
        if target is None or target.session is None:
            self.world.send_system(p, f"玩家 {target_name} 不在线。")
            return
        # 原版 Whisper 校验 BoHearWhisper/IsBlockWhisper：本项目无屏蔽名单，
        # 仅对齐「在线才可达 + 双方定向可见」核心语义。
        target.session.send_json({
            "type": "whisper", "fromId": p.id, "fromName": p.name,
            "toName": target.name, "text": words[:500], "incoming": True,
        })
        p.session.send_json({
            "type": "whisper", "fromId": p.id, "fromName": p.name,
            "toName": target.name, "text": words[:500], "incoming": False,
        })

    # ---- GM @命令（原版 ObjBase.pas CmdXXX 系列，权限 >= 6）-------------

    _GM_HELP = ("GM命令: @移动 [地图] x y  @金 数量  @等级 n  "
                "@制造 物品名 [数量]  @刷怪 怪名 [数量]  @满  "
                "@托管 [安全|均衡|效率]  @帮助")

    def _gm_command(self, p, line: str) -> None:
        w = self.world
        parts = line.split()
        if not parts:
            return
        cmd, args = parts[0].lower(), parts[1:]

        def arg_int(i: int, d: int = 0) -> int:
            try:
                return int(args[i])
            except (IndexError, ValueError):
                return d

        if cmd in ("帮助", "help"):
            w.send_system(p, self._GM_HELP)
            return
        if cmd in ("移动", "move"):
            # @移动 x y = 当前地图；@移动 地图名 x y = 跨图
            if len(args) == 2:
                map_id, x, y = (p.map.name if p.map else w.default_map.name), arg_int(0), arg_int(1)
            elif len(args) == 3:
                map_id, x, y = args[0], arg_int(1), arg_int(2)
            else:
                w.send_system(p, "用法: @移动 [地图] x y")
                return
            gm = w.get_or_load_map(map_id)
            if gm is None:
                w.send_system(p, f"地图 {map_id} 不存在。")
                return
            x = max(0, min(gm.width - 1, x))
            y = max(0, min(gm.height - 1, y))
            # 原版 CmdUserMoveXY/CmdPositionMove/CmdMapMove（ObjBase.pas:13472/15212）
            # 都是 RM_SPACEMOVE_FIRE + SpaceMove → 带传送特效（260），过门才没有。
            if w.space_move(p, gm.name, x, y) is not None:
                if p.session is not None:
                    p.session.send_json(w.map_snapshot(p, "changeMap"))
                w.broadcast_entity_add(p)
                w.space_move_show(p)
            return
        if cmd in ("金", "金币", "gold"):
            n = arg_int(0, 0)
            if n:
                p.gold = max(0, p.gold + n)
                # gold 不在常规 stats 里（原版是 GoldChanged → SM_GOLDCHANGED 单独下发）
                w.send_stats(p, {"gold": p.gold})
                w.send_system(p, f"金币 {'+' if n > 0 else ''}{n}，现有 {p.gold}。")
            return
        if cmd in ("等级", "level"):
            lv = max(1, min(100, arg_int(0, 1)))
            p.level, p.exp = lv, 0
            p.recompute(w)
            w.send_stats(p)
            w.send_system(p, f"等级已设为 {lv}。")
            return
        if cmd in ("制造", "make", "item"):
            if not args:
                w.send_system(p, "用法: @制造 物品名 [数量]")
                return
            count = 1
            if len(args) >= 2 and args[-1].isdigit():
                count = max(1, min(100, int(args[-1])))
                args = args[:-1]
            name = "".join(args)
            if w.give_item(p, name, count):
                self.send_bag_update(p)
                w.send_system(p, f"获得了 {name} ×{count}。")
            else:
                w.send_system(p, f"找不到物品 {name}。")
            return
        if cmd in ("刷怪", "spawn"):
            if not args:
                w.send_system(p, "用法: @刷怪 怪名 [数量]")
                return
            count = arg_int(len(args) - 1, 1) if args[-1].isdigit() else 1
            name = args[0] if not (len(args) >= 2 and args[-1].isdigit()) else " ".join(args[:-1])
            t = w.monster_templates.get(name)
            if t is None or p.map is None:
                w.send_system(p, f"找不到怪物 {name}。")
                return
            for _ in range(max(1, count)):
                pos = w._free_near(p.map, p.x, p.y, 3)
                if pos is None:
                    break
                m = Monster(id=w.new_id(), name=t.name,
                            x=pos[0], y=pos[1], dir=4,
                            hp=t.hp, max_hp=t.hp, mp=t.mp, max_mp=t.mp,
                            level=t.level, race=t.race, race_img=t.raceImg, appr=t.appr,
                            template=t, spawn_x=pos[0], spawn_y=pos[1],
                            spawn_map=p.map)
                m.exp = t.exp
                w.add_actor(m, p.map)
                w.broadcast_entity_add(m)
            w.send_system(p, f"已在附近刷出 {name} ×{count}。")
            return
        if cmd in ("满", "full"):
            p.hp, p.mp = p.max_hp, p.max_mp
            w.send_stats(p)
            return
        if cmd in ("托管", "auto", "autopilot"):
            cur = self.autopilot.get(p)
            if cur is not None and cur.active:
                self.autopilot.disable(p, "已通过命令关闭托管")
            else:
                key = (normalize_preset(args[0]) if args else
                       (cur.preset if cur else DEFAULT_PRESET))
                self.autopilot.enable(p, key)
            return
        w.send_system(p, "未知命令。" + self._GM_HELP)

    def _logout(self, session: Any, msg: dict) -> None:
        self.on_disconnect(session)
        session.close()

    def _back_to_select(self, session: Any, msg: dict) -> None:
        """原版 Alt-X「退出角色」：离开游戏世界、保留登录态与连接，回到人物选择。

        旧实现只有 ``_logout``（直接 ``session.close()``），客户端收到 close 后
        没有任何场景切换，主菜单「返回角色选择」点了等于没反应。
        """
        self.on_disconnect(session)
        session.send_json({"type": "backToSelectResult", "ok": True})
        self._list_characters(session, msg)

    def _butch(self, session: Any, msg: dict) -> None:
        """原版 ``CM_BUTCH``（Grobal2.pas:1007）：Alt+左键点动物尸体挖肉。

        客户端发 ``MakeDefaultMsg(CM_BUTCH, actorid, x, y, dir)``，带 actor id；
        服务端 ``ClientGetButchItem``（ObjBase.pas:17458-17504）要求目标
        ``m_boDeath and not m_boSkeleton and m_boAnimal``、距离 ≤2，挖够次数后
        ``RM_SKELETON`` + ``TakeBagItems`` 把「肉」放进背包并广播 ``RM_BUTCH``。
        """
        p = self.find_by_session(session)
        if p is None:
            return
        w = self.world
        m = w.actors.get(_int(msg, "actorId", -1))
        if (not isinstance(m, Monster) or m.alive or not w.butchable(m)
                or m.skeleton or m.corpse_until <= now_ms() or m.map is not p.map):
            return
        if max(abs(p.x - m.x), abs(p.y - m.y)) > 2:
            w.send_system(p, "离尸体太远了。")
            return
        now = now_ms()
        # 原版 ObjBase.pas:17465-17470：两次挖肉之间必须隔 dwTurnIntervalTime
        # （M2Share.pas:2121 默认 600ms），不足直接 Exit，连点无效。
        if now - p.last_butch_at < BUTCH_INTERVAL_MS:
            return
        p.last_butch_at = now
        p.dir = direction_toward(p.x, p.y, m.x, m.y, p.dir)
        m.butch_count += 1
        # 原版每次挖肉扣 m_nBodyLeathery（Random(16)+5 = 5~20），初值 50，
        # 扣到 ≤0 才出肉并变骷髅。所以次数是随机的 3~10 次（均值约 4），
        # 不是固定 2 次。
        m.body_leathery -= random.randint(BUTCH_WEAR_MIN, BUTCH_WEAR_MAX)
        # 原版 ObjBase.pas:17479-17482：每挖一刀也扣 m_nMeatQuality
        # （Random(201)+100 = 100~300）——拖太久才挖完肉品质会变差。
        m.meat_quality = max(0, m.meat_quality - random.randint(100, 300))
        # 原版每次挖都把 m_dwDeathTick 刷新成当前时间（ObjBase.pas:17497）→
        # 持续挖肉会延长尸体的保留时间。
        m.corpse_until = now + CORPSE_MS
        if m.body_leathery > 0:
            w.broadcast_around(m.map, {"type": "butch", "id": m.id, "meat": False,
                                       "actorId": p.id, "dir": p.dir,
                                       "x": m.x, "y": m.y}, m.x, m.y, SIGHT_RANGE)
            w.send_system(p, "正在挖肉……")
            return
        # 原版 ObjBase.pas:17479-17495：挖开（leathery ≤ 0）后
        # ① race ∈ [50,80) 才变骷髅 + ApplyMeatQuality + RM_SKELETON；
        # ② race ≥ 80（毒蜘蛛/蝎子/食人花/洞蛆）**不变骷髅**，尸体原样留着；
        # ③ 无论哪种都 TakeBagItems 清空尸体背包，然后 leathery := 50。
        m.body_leathery = BODY_LEATHERY
        skeleton = w.butch_skeleton(m)
        if skeleton:
            m.skeleton = True
        # 原版 TakeBagItems（ObjBase.pas:17491）：动物死亡不散落物品，MonItems
        # 掉落表留在「尸体背包」，挖空后一次性全给玩家；背包空才是
        # 「未发现任何物品！！！」（SysMsg sYouFoundNothing，红色）。
        # 食人花叶 1/1、食人花果实 1/5、毒蜘蛛牙齿 1/3 都走这条链。
        loot: list[dict] = []
        if not m.bag_looted:
            m.bag_looted = True                  # 背包已清空，再挖只剩空手
            loot = w.roll_butch_loot(m, p)
            if not loot and skeleton:
                # 无掉落表的动物（鹿等）原版由类直接给肉：鸡 → 鸡肉（index 57），
                # 狼/羊 → 肉（index 3）。
                name = "鸡肉" if "鸡" in (m.name or "") else "肉"
                t = w.item_template_by_name(name)
                if t is not None:
                    loot = [w.new_item_instance(t, max(1, t.duraMax))]
        if not loot:
            w.send_system(p, "未发现任何物品！！！", "#ff0000")
        else:
            # 原版 ApplyMeatQuality（ObjBase.pas:20517-20535）只在变骷髅的分支里
            # 调用：把尸体背包里全部 StdMode 40 物品的耐久设为 m_nMeatQuality
            # —— 这就是「肉品质」的载体，卖价随品质变化（GetUserItemPrice StdMode 40）。
            if skeleton:
                for it in loot:
                    it_t = w.item_templates.get(int(it.get("index", 0)))
                    if it_t is not None and it_t.stdMode == 40:
                        it["durability"] = m.meat_quality
            for it in loot:
                if len(p.bag) < BAG_SIZE:
                    p.bag.append(it)
                    w.send_system(p, f"挖到了{it.get('name', '物品')}。")
                else:
                    w.drop_instance(p, it)
                    w.send_system(p, f"背包已满，{it.get('name', '物品')}掉在地上。")
            self.send_bag_update(p)
        # meat=True → 客户端把尸体改画成骷髅（原版 RM_SKELETON）；race ≥ 80
        # 的蜘蛛/食人花挖空后尸体保持原样，只播挖尸动作。
        w.broadcast_around(m.map, {"type": "butch", "id": m.id, "meat": skeleton,
                                   "actorId": p.id, "dir": p.dir,
                                   "x": m.x, "y": m.y}, m.x, m.y, SIGHT_RANGE)

    def send_bag_update(self, p: Player) -> None:
        """``GameService.sendBagUpdate``：全量背包 + 金币 + 快捷栏 + 负重。
        （实现在 GameWorld.send_bag_update——装备磨损/损毁在 world 层也要推送。）"""
        self.world.send_bag_update(p)

    # ---- 持久化 ---------------------------------------------------------

    def persist(self, p: Player) -> None:
        items: list[dict] = []
        for it in p.bag:
            d = dict(it)
            d["slot"] = -1
            items.append(d)
        for slot, it in enumerate(p.equipment):
            if it is not None:
                d = dict(it)
                d["slot"] = slot
                items.append(d)
        skills = {str(mid): {"level": s.get("level", 0), "train": s.get("train", 0),
                            "key": s.get("key", 0)} for mid, s in p.skills.items()}
        rec = {
            "id": p.character_id, "account_id": p.account_id, "name": p.name,
            "sex": p.sex, "hair": p.hair, "job": p.job, "level": p.level,
            "exp": p.exp, "map_name": p.map.name if p.map else "default",
            "x": p.x, "y": p.y, "dir": p.dir, "hp": p.hp, "mp": p.mp,
            "gold": p.gold, "home_map": p.home_map, "home_x": p.home_x,
            "home_y": p.home_y, "items": items, "storage": list(p.storage),
            "skills": dumps(skills), "quick_slots": list(p.quick_slots),
        }
        self.db.update_character(rec)
        # 任务变量独立于 characters 表存（Java 侧共用表结构，不能加列）。
        # 空 dict 也写一行，避免重登时读到缺省空值；无角色 id 时跳过。
        if p.character_id:
            self.db.save_quest_params(p.character_id, p.quest_params)
            self.db.save_pk_point(p.character_id, p.pk_point)

    def save_all_players(self) -> None:
        for p in self.world.players():
            self.persist(p)

    def tick(self) -> None:
        now = now_ms()
        self.world.tick(now)
        # 托管与共用同一个 now：动作节拍与怪物 AI / 回血在同一个 tick 内对齐。
        self.autopilot.tick(now)

    # ---- REST -----------------------------------------------------------

    def handle_admin(self, method: str, path: str, query: dict[str, str],
                     body: bytes) -> str | None:
        world = self.world
        if path == "/api/version":
            return encode(load_version())
        if path == "/api/status":
            return encode({
                "onlinePlayers": world.online_count(),
                "monsters": world.monster_count(),
                "uptimeMs": now_ms() - self.started_at,
                "playerSpawnX": self.config.player_spawn_x,
                "playerSpawnY": self.config.player_spawn_y,
            })
        if path == "/api/players":
            return encode({"players": [
                {"id": p.id, "name": p.name, "level": p.level, "job": p.job,
                 "x": p.x, "y": p.y, "hp": p.hp, "maxHp": p.max_hp}
                for p in world.players()
            ]})
        if path == "/api/broadcast" and method.upper() == "POST":
            text = ""
            try:
                obj = json.loads(body.decode("utf-8"))
                if isinstance(obj, dict) and isinstance(obj.get("text"), str):
                    text = obj["text"]
            except (ValueError, UnicodeDecodeError):
                pass
            if text:
                world.broadcast_all({"type": "systemMessage", "text": "[公告] " + text})
            return encode({"ok": True})
        if path == "/api/autopilot":
            pilots = self.autopilot.snapshot()
            return encode({"count": len(pilots), "pilots": pilots,
                           "presets": [{"id": k, "name": v["name"],
                                        "desc": v["desc"]}
                                       for k, v in PRESETS.items()]})
        if path == "/api/migration":
            return encode({
                "implementation": "python-stdlib",
                "totalTypes": len(ALL_TYPES),
                "migrated": sorted(MIGRATED_TYPES),
                "migratedCount": len(MIGRATED_TYPES),
                "pendingCount": len(PENDING_TYPES),
                "pending": sorted(PENDING_TYPES),
                "pendingHitCounts": dict(self.pending_hits),
                "stage": "all-46-types",
            })
        return None
