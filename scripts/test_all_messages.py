#!/usr/bin/env python3
"""全 46 种 WebSocket 消息的 handler 覆盖测试（离线，零第三方依赖）。

目的：防止「消息类型注册了、handler 从没被执行过」这类假完成。
构造一个带真实 gamedata 的临时世界与两个在线角色，逐条直接调用每个
handler，捕获任何异常，并核对关键消息的响应类型。

用法：
    python scripts/test_all_messages.py
    python scripts/test_all_messages.py --live 8081   # 追加真实 WS 链路冒烟
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import ALL_TYPES, MIGRATED_TYPES, PENDING_TYPES  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, Player  # noqa: E402
from smoke_ws import WS  # noqa: E402


class FakeSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.account_id = 0
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True

    def drain(self) -> list[str]:
        out = [m.get("type", "?") for m in self.messages]
        self.messages.clear()
        return out


def build_world():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-allmsg-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    return cfg, db, db_path, service


def prepare(service: GameService):
    """登录两个角色，返回一个会话、玩家、同伴、怪物、NPC 与装备/消耗品索引。"""
    s1, s2 = FakeSession(), FakeSession()
    tag = str(int(time.time()) % 100000)
    for sess, name in ((s1, "A" + tag), (s2, "B" + tag)):
        for msg in ({"type": "register", "username": name, "password": "test12345"},
                    {"type": "login", "username": name, "password": "test12345"},
                    {"type": "createCharacter", "name": name, "sex": 0,
                     "hair": 1, "job": 0},
                    {"type": "enterGame", "name": name},
                    {"type": "noticeOk", "name": name}):
            service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(s1)
    mate = service.find_by_session(s2)
    assert p is not None and mate is not None, "登录链路失败"

    w = service.world
    # 保证同伴在同一地图且能互相找到
    mate.x, mate.y = p.x + 1, p.y
    monster = next((a for a in w.actors.values()
                    if isinstance(a, Monster) and a.alive), None)
    if monster is not None:
        monster.x, monster.y = p.x + 1, p.y + 1
    npc = next((n for n in w.npcs.values() if n.map is p.map), None)
    if npc is None:
        npc = next(iter(w.npcs.values()))
        npc.map = p.map
    npc.x, npc.y = p.x, p.y

    equip = next((t for t in w.item_templates.values()
                  if t.stdMode == 5 and t.weight < 20), None)
    consumable = next((t for t in w.item_templates.values()
                       if 0 <= t.stdMode <= 2 and (t.acMin > 0 or t.macMin > 0)), None)
    equip_inst = w.new_item_instance(equip) if equip is not None else None
    consumable_inst = w.new_item_instance(consumable) if consumable is not None else None
    if equip_inst is not None:
        p.bag.append(equip_inst)
    if consumable_inst is not None:
        p.bag.append(consumable_inst)
    p.gold = 100000
    p.mp = p.max_mp = 9999
    p.hp = p.max_hp = 9999
    # 学会全部技能以覆盖 castMagic 的所有分支
    for mid in w.magic_templates:
        p.skills[mid] = {"level": 0, "train": 0, "key": 0}

    equip_mi = int(equip_inst["makeIndex"]) if equip_inst is not None else 0
    consumable_mi = (int(consumable_inst["makeIndex"])
                     if consumable_inst is not None else equip_mi)
    return s1, s2, p, mate, monster, npc, equip_mi, consumable_mi


def messages_for(p, mate, monster, npc, equip_mi, consumable_mi) -> dict[str, dict]:
    tid = monster.id if monster is not None else -1
    npc_id = npc.id if npc is not None else -1
    return {
        "ping": {"type": "ping"},
        "register": {"type": "register", "username": "x", "password": "y"},
        "login": {"type": "login", "username": "x", "password": "y"},
        "listCharacters": {"type": "listCharacters"},
        "createCharacter": {"type": "createCharacter", "name": "ZZZ", "job": 1},
        "deleteCharacter": {"type": "deleteCharacter", "name": "ZZZ"},
        "enterGame": {"type": "enterGame", "name": p.name},
        "noticeOk": {"type": "noticeOk", "name": p.name},
        "move": {"type": "move", "dir": 2},
        "say": {"type": "say", "text": "全消息覆盖测试"},
        "attack": {"type": "attack", "targetId": tid},
        "dirAttack": {"type": "dirAttack", "dir": 2},
        "castMagic": {"type": "castMagic", "magicId": 2, "targetX": p.x,
                      "targetY": p.y, "targetId": -1},
        "magicKeyChange": {"type": "magicKeyChange", "magicId": 2, "key": 1},
        "pickup": {"type": "pickup", "itemId": -1},
        "useItem": {"type": "useItem", "makeIndex": consumable_mi},
        "takeOnItem": {"type": "takeOnItem", "makeIndex": equip_mi, "slot": 1},
        "takeOffItem": {"type": "takeOffItem", "slot": 1, "makeIndex": equip_mi},
        "quickSlot": {"type": "quickSlot", "slot": 0, "makeIndex": consumable_mi},
        "drop": {"type": "drop", "makeIndex": equip_mi},
        "moveItem": {"type": "moveItem", "makeIndex": equip_mi,
                     "targetIndex": consumable_mi},
        "npcTalk": {"type": "npcTalk", "npcId": npc_id},
        "npcSelect": {"type": "npcSelect", "npcId": npc_id, "value": "@main"},
        "buyItem": {"type": "buyItem", "npcId": npc_id, "index": 0, "count": 1},
        "sellItem": {"type": "sellItem", "npcId": npc_id, "makeIndex": -1},
        "querySellPrice": {"type": "querySellPrice", "npcId": npc_id, "makeIndex": -1},
        "createGuild": {"type": "createGuild", "name": "测试行会"},
        "joinGuild": {"type": "joinGuild", "name": "测试行会"},
        "guildInfo": {"type": "guildInfo"},
        "guildChat": {"type": "guildChat", "text": "行会频道"},
        "siege": {"type": "siege"},
        "guildWar": {"type": "guildWar", "name": "不存在"},
        "guildAlly": {"type": "guildAlly", "name": "不存在"},
        "leaveGuild": {"type": "leaveGuild"},
        "partyInvite": {"type": "partyInvite", "name": mate.name},
        "partyChat": {"type": "partyChat", "text": "队伍频道"},
        "partyLeave": {"type": "partyLeave"},
        "storageOpen": {"type": "storageOpen"},
        "storageStore": {"type": "storageStore", "makeIndex": equip_mi},
        "storageTake": {"type": "storageTake", "makeIndex": equip_mi},
        "tradeRequest": {"type": "tradeRequest", "name": mate.name},
        "tradeAccept": {"type": "tradeAccept"},
        "tradeAddItem": {"type": "tradeAddItem", "makeIndex": consumable_mi},
        "tradeAddGold": {"type": "tradeAddGold", "amount": 10},
        "tradeConfirm": {"type": "tradeConfirm"},
        "tradeCancel": {"type": "tradeCancel"},
        "butch": {"type": "butch", "actorId": -1, "x": p.x, "y": p.y, "dir": 0},
        "backToSelect": {"type": "backToSelect"},
        "logout": {"type": "logout"},
    }


def check(name: str, cond: bool, results: list[tuple[str, bool]]) -> None:
    results.append((name, bool(cond)))


def run_success_paths() -> int:
    """用独立世界验证关键功能真的生效（不只是没抛异常）。"""
    cfg, db, db_path, service = build_world()
    results: list[tuple[str, bool]] = []
    try:
        s1, s2, p, mate, monster, npc, eq_mi, con_mi = prepare(service)
        w = service.world
        h = service._handlers
        p.level = 50
        p.recompute(w)
        p.hp = 1

        # 穿戴 / 卸下
        s1.messages.clear()
        h["takeOnItem"](s1, {"type": "takeOnItem", "makeIndex": eq_mi, "slot": 1})
        check("穿戴武器进入 U_WEAPON 槽", p.equipment[1] is not None, results)
        check("穿戴后下发 bagUpdate",
              any(m.get("type") == "bagUpdate" for m in s1.messages), results)
        s1.messages.clear()
        h["takeOffItem"](s1, {"type": "takeOffItem", "slot": 1, "makeIndex": eq_mi})
        check("卸下后槽位清空且物品回背包",
              p.equipment[1] is None and p.bag_item(eq_mi) is not None, results)

        # 消耗品
        s1.messages.clear()
        h["useItem"](s1, {"type": "useItem", "makeIndex": con_mi})
        check("消耗品使用后离开背包", p.bag_item(con_mi) is None, results)
        # 原版药水不即时回血：AC 写入回血池 m_nIncHealth，由 regen tick 慢慢消耗
        check("消耗品写入回血池", p.inc_health > 0, results)

        # 丢弃 → 拾取
        s1.messages.clear()
        h["drop"](s1, {"type": "drop", "makeIndex": eq_mi})
        ground = [g for g in w.ground_items.values() if g.item is not None]
        check("丢弃生成地面物品", len(ground) > 0, results)
        if ground:
            gi = ground[0]
            s1.messages.clear()
            h["pickup"](s1, {"type": "pickup", "itemId": gi.id})
            check("拾取后物品回背包且地面项消失",
                  gi.id not in w.ground_items and p.bag_item(eq_mi) is not None, results)

        # 购买：原版 TMerchant.ClientBuyItem 只在 m_GoodsList（[goods] 货单）
        # 里找商品，货单外的 StdItems 模板一律不能买。
        buy_npc = next((n for n in w.npcs.values() if w.goods(n)), None)
        if buy_npc is not None:
            buy_goods = w.goods(buy_npc)
            target = min(buy_goods, key=lambda g: g["price"])
            p.gold = max(p.gold, target["price"] + 100)
            gold_before, bag_before = p.gold, len(p.bag)
            h["buyItem"](s1, {"type": "buyItem", "npcId": buy_npc.id,
                              "index": target["index"], "count": 1})
            check("购买货单内商品扣金币并生成物品",
                  p.gold < gold_before and len(p.bag) > bag_before, results)
            outside = next((i for i in w.item_templates
                            if all(g["index"] != i for g in buy_goods)), None)
            if outside is not None:
                g0 = p.gold
                h["buyItem"](s1, {"type": "buyItem", "npcId": buy_npc.id,
                                  "index": outside, "count": 1})
                check("货单外物品不可购买", p.gold == g0, results)

        # NPC 对话
        s1.messages.clear()
        h["npcTalk"](s1, {"type": "npcTalk", "npcId": npc.id})
        check("NPC 脚本返回 npcSay",
              any(m.get("type") == "npcSay" for m in s1.messages), results)

        # 商店价格：M2 [goods] 第二列是库存，价格应为 StdItems.Price × 脚本头 % 倍率，
        # 不能像 Java 版那样把库存 10 当单价（用户反馈「所有商店都是 10 块钱」）。
        merchant = next((n for n in w.npcs.values() if w.goods(n)), None)
        if merchant is not None:
            merchant.x, merchant.y = p.x, p.y
            s1.messages.clear()
            h["npcSelect"](s1, {"type": "npcSelect", "npcId": merchant.id, "value": "@buy"})
            say = next((m for m in s1.messages if m.get("type") == "npcSay"), None)
            goods = (say or {}).get("goods") or []
            prices = [g["price"] for g in goods]
            check("商店返回多种价格，不再是清一色 10",
                  len(goods) > 0 and len(set(prices)) > 1 and max(prices) > 100,
                  results)
            if goods:
                t0 = w.item_templates.get(goods[0]["index"])
                from pyserver.util import delphi_round
                rate = merchant.script.price_rate or 100
                want = max(1, delphi_round(delphi_round(t0.price * 1.1) / 100 * rate))
                check("首个商品价格 = Round(Round(Price*1.1)/100*%倍率)",
                      t0 is not None and goods[0]["price"] == want,
                      results)

        # 行会 / 组队 / 仓库
        h["createGuild"](s1, {"type": "createGuild", "name": "验收行会"})
        check("创建行会成功", w.guild_of(p.name) is not None, results)
        h["partyInvite"](s1, {"type": "partyInvite", "name": mate.name})
        check("组队成功", w.party_of(p.id) is not None, results)
        h["storageStore"](s1, {"type": "storageStore", "makeIndex": eq_mi})
        stored = len(p.storage) > 0
        h["storageTake"](s1, {"type": "storageTake", "makeIndex": eq_mi})
        check("仓库存/取闭环", stored and len(p.storage) == 0, results)

        # 交易完整闭环（双方 session 都参与）
        h["tradeRequest"](s1, {"type": "tradeRequest", "name": mate.name})
        h["tradeAccept"](s2, {"type": "tradeAccept"})
        check("交易会话建立", p.id in w.trades, results)
        if p.id in w.trades:
            s1.messages.clear()
            s2.messages.clear()
            h["tradeAddItem"](s1, {"type": "tradeAddItem", "makeIndex": con_mi})
            h["tradeConfirm"](s2, {"type": "tradeConfirm"})
            h["tradeConfirm"](s1, {"type": "tradeConfirm"})
            check("双方确认后交易完成",
                  any(m.get("text") == "交易完成。" for m in s1.messages), results)
            check("交易结束后会话清理", p.id not in w.trades, results)

        # 战斗：反复攻击直到命中
        if monster is not None:
            hp0 = monster.hp
            for _ in range(30):
                if monster.hp < hp0 or not monster.alive:
                    break
                monster.x, monster.y = p.x + 1, p.y
                h["attack"](s1, {"type": "attack", "targetId": monster.id})
            check("普通攻击造成伤害", monster.hp < hp0 or not monster.alive, results)

        # 施法：消耗 MP 且广播 spellCast
        p.mp = 9999
        p.skill_cooldown.clear()
        s1.messages.clear()
        h["castMagic"](s1, {"type": "castMagic", "magicId": 2,
                            "targetX": p.x, "targetY": p.y, "targetId": -1})
        check("施法广播 spellCast",
              any(m.get("type") == "spellCast" for m in s1.messages), results)
        check("施法消耗 MP", p.mp < 9999, results)

        # 掉落位置：原版 GetDropPosition 从半径 1 起环形扩散找空格
        pos = w.scatter_pos(p.map, p.x, p.y)
        check("掉落从尸体周围一圈开始扩散（不再全图随机撒 ±3）",
              pos is not None and max(abs(pos[0] - p.x), abs(pos[1] - p.y)) <= 3,
              results)

        # 动物尸体 / 挖肉：原版 SM_DEATH 保留 actor、CM_BUTCH 带 actor id、
        # 动物死亡不 ScatterBagItems（ObjBase.pas:20983）
        animal = next((a for a in w.actors.values()
                       if isinstance(a, Monster) and a.alive and w.butchable(a)), None)
        if animal is not None:
            animal.map = p.map
            p.map.actors[animal.id] = animal
            animal.x, animal.y = p.x + 1, p.y
            before = len(w.ground_items)
            w.kill_monster(animal, int(time.time() * 1000))
            check("可挖肉动物死后保留尸体、不掉物品",
                  animal.corpse_until > 0
                  and animal.brief_json().get("corpse") is True
                  and len(w.ground_items) == before, results)
            check("尸体仍下发给视野内玩家（原版保留 actor）",
                  animal.id in {a.id for a in w._sight_actors(p.map, p.x, p.y)},
                  results)
            check("战斗目标筛选不含尸体",
                  animal.id not in {a.id for a in w.nearby(p.map, p.x, p.y)},
                  results)
            s1.messages.clear()
            # 原版挖肉是随机次数（BodyLeathery 50，每次扣 Random(16)+5 → 3~10 次），
            # 且两次之间要隔 600ms（ObjBase.pas:17465）。这里按上限 12 次循环，
            # 每次把 last_butch_at 归零绕开真实等待。
            for _ in range(12):
                p.last_butch_at = 0
                h["butch"](s1, {"type": "butch", "actorId": animal.id,
                                "x": animal.x, "y": animal.y})
                if animal.skeleton:
                    break
            got_meat = False
            meat_dura = None
            for it in p.bag:
                t = w.item_templates.get(int(it.get("index", 0)))
                if t is not None and t.name in ("肉", "鸡肉"):
                    got_meat = True
                    meat_dura = int(it.get("durability", -1))
            check("Alt+左键挖够次数得到肉（原版 TakeBagItems）",
                  animal.skeleton and got_meat, results)
            # 第 16 轮：肉品质 = m_nMeatQuality 写进肉耐久（ApplyMeatQuality），
            # 出生按 UsrEngn.pas:1835-1864 初始化（鸡 3000~6500 / 鹿狼 8000~16000），
            # 受击/中毒/挖肉都会降低。
            check(f"肉耐久 = 尸体肉品质（ApplyMeatQuality，dura={meat_dura} "
                  f"quality={animal.meat_quality}）",
                  meat_dura is not None and 0 <= meat_dura <= 16000
                  and meat_dura == animal.meat_quality, results)
            check("挖肉后尸体变骷髅但保留（原版 RM_SKELETON 不删 actor）",
                  animal.skeleton and animal.id in w.actors, results)
            check("挖肉后下发 bagUpdate",
                  any(m.get("type") == "bagUpdate" for m in s1.messages), results)

        # 第 16 轮：挖矿（原版 PileStones ObjBase.pas:21881 + MakeMine:24351）。
        # 需要 MINE 标志地图 + 鹤嘴锄（Shape 19）+ 前方不可走格。
        w.map_flags[p.map.name] = ["MINE"]
        real_map = p.map

        class _MineMapShim:
            """把当前地图伪装成「四面石壁」的矿区（GameMap 是 slots，不能改方法）。"""

            def __init__(self, src):
                self.name, self.actors = src.name, src.actors
                self.width, self.height = src.width, src.height

            def inside(self, x, y):
                return True

            def can_walk(self, x, y):
                return False

            def occupied(self, x, y):
                return False

        p.map = _MineMapShim(real_map)
        pick = w.item_template_by_name("鹤嘴锄")
        if pick is not None:
            import random as _random
            from pyserver.util import U_WEAPON, random_mine_dura
            it = w.new_item_instance(pick, pick.duraMax)
            p.equipment[U_WEAPON] = it
            p.bag.clear()   # 原版背包满挖不到矿（MakeMine:24364 Exit），先清包
            bag_before = len(p.bag)
            dura_before = int(it.get("durability", 0))
            from pyserver.util import direction_delta
            _dx, _dy = direction_delta(2)
            _key = (p.map.name, p.x + _dx, p.y + _dy)
            _saw_decrement = False
            _random.seed(20261113)
            for _ in range(400):
                # 矿点存量每锄 -1（不管命中），这里持续补满保证 400 锄全部有效
                if _key in w.mine_state:
                    _saw_decrement = _saw_decrement or w.mine_state[_key][0] < 999
                    w.mine_state[_key][0] = 999
                h["dirAttack"](s1, {"type": "dirAttack", "dir": 2})
            ore_names = ("金矿", "银矿", "铁矿", "铜矿", "黑铁矿石")
            got_ore = [x for x in p.bag[bag_before:]
                       if w.item_templates.get(int(x.get("index", 0))) is not None
                       and w.item_templates[int(x.get("index", 0))].name in ore_names]
            check(f"挖矿 400 锄能挖到矿石（命中率 1/4 × 出矿率 1/12，"
                  f"ore={[w.item_templates[int(g['index'])].name for g in got_ore]}）",
                  len(got_ore) > 0, results)
            if got_ore:
                d = int(got_ore[0].get("durability", 0))
                check(f"矿石耐久（纯度）在原版 RandomDrua 区间 3000~23000（dura={d}）",
                      3000 <= d <= 23000, results)
            check("挖矿磨损鹤嘴锄（DoDamageWeapon Random(15)+5）",
                  int(it.get("durability", 0)) < dura_before, results)
            check("矿点存量被消耗（TStoneMineEvent m_nMineCount 每锄 -1）",
                  _saw_decrement, results)
            # 矿点挖空 10 分钟后补矿（AddStoneMine Random(80)）
            key = next(iter(w.mine_state))
            w.mine_state[key] = [0, int(time.time() * 1000) - 601_000]
            h["dirAttack"](s1, {"type": "dirAttack", "dir": 2})
            check(f"挖空矿点 10 分钟后补矿（AddStoneMine，count={w.mine_state[key][0]}）",
                  w.mine_state[key][1] > int(time.time() * 1000) - 1_000, results)
            del w.map_flags[p.map.name]
            p.map = real_map   # 还原真实地图

        # 返回人物选择：保留登录态与连接，不能像 logout 那样断开
        s1.messages.clear()
        h["backToSelect"](s1, {"type": "backToSelect"})
        types = [m.get("type") for m in s1.messages]
        check("返回角色选择下发结果 + 角色列表且不断线",
              "backToSelectResult" in types and "characterList" in types
              and not s1.closed, results)
        cl = next((m for m in s1.messages if m.get("type") == "characterList"), {})
        chars = cl.get("characters") or []
        check("角色列表附带上次地图坐标（供选角界面预载纹理）",
              bool(chars) and all(set(("map", "x", "y")) <= set(c) for c in chars),
              results)

        # 第 13 轮 25：死亡存档（HP<=0）重登，原版 UsrEngn.pas:576-600
        # 回出生点附近并给 HP := 14，不是满血、也不是留在死亡地图。
        rec = service.db.find_character(p.name)
        if rec is not None:
            rec = dict(rec)
            rec["hp"] = 0
            service.db.update_character(rec)
            s3 = FakeSession()
            for m in ({"type": "login", "username": p.name, "password": "test12345"},
                      {"type": "enterGame", "name": p.name},
                      {"type": "noticeOk", "name": p.name}):
                service.on_message(s3, json.dumps(m, ensure_ascii=False))
            p3 = service.find_by_session(s3)
            check("死亡存档重登：回出生点且 HP=14（原版 UsrEngn）",
                  p3 is not None and p3.hp == 14 and not p3.dead
                  and p3.map.name == p3.home_map, results)
    finally:
        db.close()
        for suffix in ("", "-wal", "-shm"):
            q = Path(str(db_path) + suffix)
            if q.exists():
                q.unlink()

    print("\n=== 成功路径断言 ===")
    for name, ok in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    failed = sum(1 for _, ok in results if not ok)
    print(f"  小计: {len(results) - failed} PASS / {failed} FAIL")
    return failed


def run_offline() -> int:
    cfg, db, db_path, service = build_world()
    failed = 0
    try:
        s1, s2, p, mate, monster, npc, eq_mi, con_mi = prepare(service)
        msgs = messages_for(p, mate, monster, npc, eq_mi, con_mi)
        missing = ALL_TYPES - set(msgs)
        if missing:
            print(f"FAIL 测试消息未覆盖: {sorted(missing)}")
            failed += 1
        print(f"消息类型: {len(ALL_TYPES)}  registered={len(service._handlers)} "
              f"pending={len(PENDING_TYPES)}")

        # logout / backToSelect 放最后，否则会话被清空导致后续 handler 空跑
        order = [t for t in msgs if t not in ("logout", "backToSelect")] \
            + ["backToSelect", "logout"]
        print("\n=== 逐条 handler 调用 ===")
        for t in order:
            s1.messages.clear()
            try:
                service._handlers[t](s1, msgs[t])
                types = [m.get("type", "?") for m in s1.messages]
                status = "OK"
            except Exception as e:  # noqa: BLE001
                types, status = [], f"FAIL {type(e).__name__}: {e}"
                failed += 1
            print(f"  {t:20s} {status:4s} -> {','.join(types) or '-'}")

        # 所有技能分支再跑一遍（castMagic 的 33 个 magicId）
        print("\n=== castMagic 全部技能分支 ===")
        mid_list = sorted(service.world.magic_templates)
        for mid in mid_list:
            s1.messages.clear()
            p.mp = 9999
            p.skill_cooldown.clear()
            try:
                service._handlers["castMagic"](s1, {
                    "type": "castMagic", "magicId": mid,
                    "targetX": p.x, "targetY": p.y,
                    "targetId": monster.id if monster is not None else -1})
            except Exception as e:  # noqa: BLE001
                print(f"  magicId={mid:<3d} FAIL {type(e).__name__}: {e}")
                failed += 1
        print(f"  共 {len(mid_list)} 个技能分支，异常 {failed} 处")
    finally:
        db.close()
        for suffix in ("", "-wal", "-shm"):
            q = Path(str(db_path) + suffix)
            if q.exists():
                q.unlink()
    print(f"\n结论: {'全部 handler 可执行' if failed == 0 else f'{failed} 处异常'}")
    return 0 if failed == 0 else 1


def run_live(port: int) -> int:
    try:
        ws = WS("127.0.0.1", port, "/ws", timeout=0.5)
    except (OSError, RuntimeError) as e:
        print(f"连接 127.0.0.1:{port} 失败: {e}")
        return 2
    try:
        ws.send({"type": "ping"})
        got = ws.drain(4)
        ok = any(m.get("type") == "pong" for m in got)
        print(f"实时 WS ping→pong: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    finally:
        ws.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="全消息 handler 覆盖测试")
    ap.add_argument("--live", type=int, metavar="PORT", help="追加真实 WS 冒烟")
    a = ap.parse_args()
    failed = run_offline() + run_success_paths()
    code = 0 if failed == 0 else 1
    if a.live and code == 0:
        code = run_live(a.live)
    return code


if __name__ == "__main__":
    sys.exit(main())
