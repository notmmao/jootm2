"""游戏服务门面：WebSocket JSON 消息分派 + REST 管理接口。

对应 Java 侧 ``GameService``。Java switch 的 46 个分支已全部迁移，按领域拆分到
``handlers_items / handlers_npc / handlers_combat / handlers_social`` 四个 mixin，
本文件保留账号/角色/进图/移动/聊天/登出这些主干与统一分派。

所有 handler 都在 asyncio 单事件循环内同步执行，等价于 Java 的
``synchronized (world.lock)``，无需额外加锁。
"""
from __future__ import annotations

import json
import time
from collections import Counter
from typing import Any, Callable

from .config import ServerConfig
from .db import Database
from .handlers_combat import COMBAT_TYPES, CombatHandlers
from .handlers_items import ITEM_TYPES, ItemHandlers
from .handlers_npc import NPC_TYPES, NpcHandlers
from .handlers_social import SOCIAL_TYPES, SocialHandlers
from .templates import dumps
from .world import BAG_SIZE, GameWorld, Monster, Player, SIGHT_RANGE, encode, now_ms

MAX_CHARS_PER_ACCOUNT = 2

# 46 个 Java switch 分支全部有对应实现。
MIGRATED_TYPES = frozenset({
    "ping", "login", "register", "listCharacters", "createCharacter",
    "deleteCharacter", "enterGame", "noticeOk", "move", "say", "logout",
    "backToSelect", "butch",
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
            # 物品 / 背包 / 装备 / 快捷栏
            "pickup": self._pickup,
            "useItem": self._use_item,
            "takeOnItem": self._take_on_item,
            "takeOffItem": self._take_off_item,
            "quickSlot": self._quick_slot,
            "drop": self._drop,
            "moveItem": self._move_item,
            "magicKeyChange": self._magic_key_change,
            # NPC / 商店
            "npcTalk": self._npc_talk,
            "npcSelect": self._npc_select,
            "buyItem": self._buy_item,
            "sellItem": self._sell_item,
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
            "partyLeave": self._party_leave,
            "partyChat": self._party_chat,
            # 仓库
            "storageOpen": self._storage_open,
            "storageStore": self._storage_store,
            "storageTake": self._storage_take,
            # 交易
            "tradeRequest": self._trade_request,
            "tradeAccept": self._trade_accept,
            "tradeCancel": self._trade_cancel,
            "tradeAddItem": self._trade_add_item,
            "tradeAddGold": self._trade_add_gold,
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
            session.send_json({"type": "loginResult", "ok": True})

    def _list_characters(self, session: Any, msg: dict) -> None:
        if session.account_id <= 0:
            return
        chars = [{"name": c["name"], "sex": c["sex"], "hair": c["hair"],
                  "job": c["job"], "level": c["level"]}
                 for c in self.db.list_characters(session.account_id)]
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
        items: list[dict] = []
        for idx, count in ((0, 1), (1, 1), (2, 2), (3, 2)):
            t = world.item_templates.get(idx)
            if t is None:
                continue
            for _ in range(count):
                items.append({
                    "makeIndex": world.next_item_make_index(), "index": idx,
                    "durability": t.duraMax, "durabilityMax": t.duraMax,
                    "lucky": 0, "slot": -1,
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

        p = Player(id=world.new_id(), name=rec["name"], x=rec["x"], y=rec["y"],
                   dir=rec["dir"], hp=rec["hp"], mp=rec["mp"], level=rec["level"],
                   exp=rec["exp"], account_id=rec["account_id"],
                   character_id=rec["id"], sex=rec["sex"], hair=rec["hair"],
                   job=rec["job"], gold=rec["gold"], home_map=rec["home_map"],
                   home_x=rec["home_x"], home_y=rec["home_y"],
                   map=target_map, session=session, world_ref=world,
                   bag=list(rec["items"]), storage=list(rec["storage"]),
                   skills=_load_skills(rec["skills"]),
                   quick_slots=(list(rec["quick_slots"]) + [0] * 6)[:6])
        p.distribute_items(world)
        p.recompute(world)
        if 12 in p.skills:
            p.long_hit_on = True       # 刺杀剑术登录自动开启（ObjBase.pas）
        p.hp, p.mp = p.max_hp, p.max_mp
        session.player_name = p.name
        world.add_player(p)

        session.send_json({"type": "enterGameResult", "ok": True})
        session.send_json(world.map_snapshot(p, "enterGame"))
        world.send_stats(p)
        world.broadcast_entity_add(p)

    # ---- 场景内 ---------------------------------------------------------

    def _move(self, session: Any, msg: dict) -> None:
        p = self.find_by_session(session)
        if p is None:
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

    def _say(self, session: Any, msg: dict) -> None:
        p = self.find_by_session(session)
        text = _str(msg, "text")
        if p is None or not text:
            return
        self.world.broadcast_all({"type": "chat", "id": p.id, "name": p.name,
                                  "text": text[:500]})

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
        m.butch_count += 1
        # 原版每次挖削减 m_nBodyLeathery / m_nMeatQuality，挖够才出肉；
        # 这里用两次近似（m_nBodyLeathery 初值 50~150，本移植没有该字段）。
        if m.butch_count < 2:
            w.broadcast_around(m.map, {"type": "butch", "id": m.id, "meat": False,
                                       "x": m.x, "y": m.y}, m.x, m.y, SIGHT_RANGE)
            w.send_system(p, "正在挖肉……")
            return
        m.skeleton = True
        m.corpse_until = 0
        # 肉名依据本地 MonItems：鸡.txt → 鸡肉（StdItems index 57），
        # 狼.txt / 羊.txt → 肉（index 3）；鹿无掉落表，原版由类直接给肉。
        name = "鸡肉" if "鸡" in (m.name or "") else "肉"
        t = w.item_template_by_name(name)
        if t is not None:
            it = w.new_item_instance(t, max(1, t.duraMax))
            if len(p.bag) < BAG_SIZE:
                p.bag.append(it)
                self.send_bag_update(p)
                w.send_system(p, f"挖到了{t.name}。")
            else:
                w.drop_instance(p, it)
                w.send_system(p, f"背包已满，{t.name}掉在地上。")
        w.broadcast_around(m.map, {"type": "butch", "id": m.id, "meat": True,
                                   "x": m.x, "y": m.y}, m.x, m.y, SIGHT_RANGE)
        w.broadcast_around(m.map, {"type": "entityRemove", "id": m.id},
                           m.x, m.y, SIGHT_RANGE)

    def send_bag_update(self, p: Player) -> None:
        """``GameService.sendBagUpdate``：全量背包 + 金币 + 快捷栏 + 负重。"""
        if p.session is None:
            return
        w = self.world
        p.session.send_json({
            "type": "bagUpdate", "gold": p.gold,
            "dress": p.dress_looks(w), "weapon": p.weapon_shape(w),
            "wearWeight": p.wear_weight(w), "handWeight": p.hand_weight(w),
            "maxWearWeight": p.max_wear_weight(), "maxHandWeight": p.max_hand_weight(),
            "quickSlots": [p.quick_slots[i] if p.quick_slot_valid(i) else 0
                           for i in range(len(p.quick_slots))],
            "items": p.items_json(w),
        })

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

    def save_all_players(self) -> None:
        for p in self.world.players():
            self.persist(p)

    def tick(self) -> None:
        self.world.tick(now_ms())

    # ---- REST -----------------------------------------------------------

    def handle_admin(self, method: str, path: str, query: dict[str, str],
                     body: bytes) -> str | None:
        world = self.world
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
