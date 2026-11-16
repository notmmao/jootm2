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
    # 护身符 / 毒药粉夹具：原版 6（施毒术）要毒药粉、13~19 要护身符，而且只认
    # **已装备**的 U_ARMRINGL(5) / U_BUJUK(9) 槽（Magic.pas:81-144 CheckAmulet）。
    # 不给夹具，这些分支只会走到「没有护身符。」就返回，等于没测到伤害/时长。
    from pyserver.util import U_ARMRINGL, U_BUJUK
    amulet = next((t for t in w.item_templates.values()
                   if t.stdMode == 25 and t.shape == 5), None)
    if amulet is not None:
        p.equipment[U_ARMRINGL] = w.new_item_instance(amulet, 5000)
    powder = next((t for t in w.item_templates.values()
                   if t.stdMode == 25 and t.shape <= 2), None)
    if powder is not None:
        p.equipment[U_BUJUK] = w.new_item_instance(powder, 5000)

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
        "dropGold": {"type": "dropGold", "gold": 1},
        "moveItem": {"type": "moveItem", "makeIndex": equip_mi,
                     "targetIndex": consumable_mi},
        "npcTalk": {"type": "npcTalk", "npcId": npc_id},
        "npcSelect": {"type": "npcSelect", "npcId": npc_id, "value": "@main"},
        "buyItem": {"type": "buyItem", "npcId": npc_id, "index": 0, "count": 1},
        "sellItem": {"type": "sellItem", "npcId": npc_id, "makeIndex": -1},
        "querySellPrice": {"type": "querySellPrice", "npcId": npc_id, "makeIndex": -1},
        "queryRepairCost": {"type": "queryRepairCost", "npcId": npc_id,
                            "makeIndex": -1, "mode": "repair"},
        "repairItem": {"type": "repairItem", "npcId": npc_id,
                       "makeIndex": -1, "mode": "repair"},
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
        "autopilot": {"type": "autopilot", "action": "status"},
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

        # 技能书：原版 TPlayObject.ReadBook 加入 m_MagicList 后立刻 SendAddMagic
        # （SM_ADDMAGIC），本项目对应 stats.skills 全量快照——缺这条消息就会出现
        # 「书用掉了、技能栏没技能、重登才看到」。
        book_t, book_mt = None, None
        for t in w.item_templates.values():
            if t.stdMode != 4:
                continue
            mt = next((m for m in w.magic_templates.values() if m.name == t.name), None)
            if mt is None or (mt.job not in (99, p.job)):
                continue
            if p.level < (mt.needLevel[0] if mt.needLevel else 0):
                continue
            book_t, book_mt = t, mt
            break
        if book_t is not None:
            p.skills.pop(book_mt.magId, None)      # 先遗忘，制造「未学」状态
            book_inst = w.new_item_instance(book_t)
            p.bag.append(book_inst)
            book_mi = int(book_inst["makeIndex"])
            s1.messages.clear()
            h["useItem"](s1, {"type": "useItem", "makeIndex": book_mi})
            check("技能书学习后进入技能列表", book_mt.magId in p.skills, results)
            check("技能书学习后书本离开背包", p.bag_item(book_mi) is None, results)
            check("技能书学习后下发 stats.skills（原版 SM_ADDMAGIC）",
                  any(m.get("type") == "stats"
                      and str(book_mt.magId) in (m.get("skills") or {})
                      for m in s1.messages), results)
        else:
            check("找到可学的技能书模板", False, results)

        # 被动技能修炼（原版 ObjBase.pas:22283-22312）：基本剑术每次物理命中
        # +Random(3)+1 点；攻杀剑术只在 bo21（攻杀/烈火发动）那一击加点。
        if monster is not None:
            monster.x, monster.y = p.x + 1, p.y
            monster.hp = monster.max_hp = 999999
            monster.template.speed = 1        # 敏捷 1 → 近战必中，排除随机 miss 干扰
            p.skills[3] = {"level": 0, "train": 0, "key": 0}
            p.skills[7] = {"level": 0, "train": 0, "key": 0}
            p.fire_hit_on = False
            p.power_hit_count, p.power_hit_point = 1, 0   # 下一击必发动攻杀
            s1.messages.clear()
            h["attack"](s1, {"type": "attack", "targetId": monster.id})
            check("基本剑术命中后累积修炼点",
                  p.skills[3]["train"] in (1, 2, 3), results)
            check("攻杀剑术在攻杀发动时累积修炼点",
                  p.skills[7]["train"] in (1, 2, 3), results)

            t7_before = p.skills[7]["train"]
            p.power_hit_count, p.power_hit_point = 99, -1  # 本次不发动攻杀
            s1.messages.clear()
            h["attack"](s1, {"type": "attack", "targetId": monster.id})
            check("未发动攻杀时不加攻杀修炼点",
                  p.skills[7]["train"] == t7_before, results)

            # 练满点数 → 升级 + 下发 skills（原版 CheckMagicLevelup）
            t3 = w.magic_templates[3]
            p.skills[3] = {"level": 1, "train": t3.maxTrain[1] - 1, "key": 0}
            p.power_hit_count, p.power_hit_point = 99, -1
            s1.messages.clear()
            h["attack"](s1, {"type": "attack", "targetId": monster.id})
            check("修炼点到位后技能升级", p.skills[3]["level"] == 2, results)
            check("技能升级后下发 stats.skills",
                  any(m.get("type") == "stats"
                      and (m.get("skills") or {}).get("3", {}).get("level") == 2
                      for m in s1.messages), results)

            # TrainLevel 门槛：人物等级低于 needLevel[0] 时命中不加点（基本剑术 7 级）
            saved_lv = p.level
            p.level = 1
            p.skills[3] = {"level": 0, "train": 0, "key": 0}
            p.power_hit_count, p.power_hit_point = 99, -1
            h["attack"](s1, {"type": "attack", "targetId": monster.id})
            check("人物等级不足 TrainLevel 时不加点",
                  p.skills[3]["train"] == 0, results)
            p.level = saved_lv

        # 快捷键：原版 ClientChangeMagicKey 不校验技能类型，被动技能也能绑键
        s1.messages.clear()
        h["magicKeyChange"](s1, {"type": "magicKeyChange", "magicId": 7, "key": 3})
        check("被动技能（攻杀）可以设置快捷键", p.skills[7]["key"] == 3, results)
        check("设置快捷键后下发 skills",
              any(m.get("type") == "stats" and "skills" in m for m in s1.messages),
              results)
        h["magicKeyChange"](s1, {"type": "magicKeyChange", "magicId": 7, "key": 0})

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
                       if isinstance(a, Monster) and a.alive and w.butchable(a)
                       and w.butch_skeleton(a)), None)
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
            # 原版挖肉是随机次数（BodyLeathery 鸡 50 / 鹿狼 150，每次扣
            # Random(16)+5 → 5~20），且两次之间要隔 600ms（ObjBase.pas:17465）。
            # 鹿最多要 30 刀，这里按 40 次循环，每次把 last_butch_at 归零绕开真实等待。
            for _ in range(40):
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

        # race ≥ 80（毒蜘蛛/蝎子/食人花/洞蛆）：原版 ObjBase.pas:17485 的骷髅分支
        # 只在 race ∈ [50,80) 走，这些怪挖空只清尸体背包、尸体原样保留。
        bug = next((a for a in w.actors.values()
                    if isinstance(a, Monster) and a.alive and w.butchable(a)
                    and not w.butch_skeleton(a)), None)
        if bug is not None:
            bug.map = p.map
            p.map.actors[bug.id] = bug
            bug.x, bug.y = p.x + 1, p.y
            w.kill_monster(bug, int(time.time() * 1000))
            for _ in range(20):
                p.last_butch_at = 0
                h["butch"](s1, {"type": "butch", "actorId": bug.id,
                                "x": bug.x, "y": bug.y})
                if bug.bag_looted:
                    break
            check(f"race≥80（{bug.name}/{bug.race}）挖空后不变骷髅",
                  bug.bag_looted and not bug.skeleton, results)
            check("race≥80 挖空后尸体仍保留可再挖（不删 actor）",
                  bug.id in w.actors and bug.corpse_until > 0, results)

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

        # 第 19 轮：传送两段特效（原版 SpaceMove：HIDE 250 / SHOW 260，
        # 瞬息移动技能专用 2 号 1590/1600；过门 EnterAnotherMap 无特效）
        # 1) GM @移动（原版 CmdUserMoveXY 等）→ variant=0 两段
        s1.messages.clear()
        gm_map = w.get_or_load_map(w.default_map.name)
        tx, ty = p.x, p.y
        for dx, dy in ((1, 0), (0, 1), (1, 1), (-1, 0), (0, -1)):
            if gm_map.can_walk(p.x + dx, p.y + dy) and not gm_map.occupied(p.x + dx, p.y + dy):
                tx, ty = p.x + dx, p.y + dy
                break
        service._gm_command(p, f"移动 {tx} {ty}")
        seq = [m.get("type") for m in s1.messages]
        check("GM @移动下发 spaceMoveHide（SM_SPACEMOVE_HIDE）",
              "spaceMoveHide" in seq, results)
        check("GM @移动下发 spaceMoveShow（SM_SPACEMOVE_SHOW）",
              "spaceMoveShow" in seq, results)
        check("GM @移动消息顺序 hide→changeMap→show（对移动者本人）",
              seq.index("spaceMoveHide") < seq.index("changeMap") < seq.index("spaceMoveShow")
              if all(t in seq for t in ("spaceMoveHide", "changeMap", "spaceMoveShow"))
              else False, results)
        hide_msg = next((m for m in s1.messages if m.get("type") == "spaceMoveHide"), {})
        check("hide 带 variant=0（非瞬息移动走 1 号特效 250/260）",
              hide_msg.get("variant") == 0, results)

        # 2) 瞬息移动（magicId 21）→ variant=1 两段（HIDE2 1590 / SHOW2 1600）
        p.skills[21] = {"level": 0}
        p.mp = 9999
        p.skill_cooldown.clear()
        s1.messages.clear()
        h["castMagic"](s1, {"type": "castMagic", "magicId": 21,
                            "targetX": p.x, "targetY": p.y, "targetId": -1})
        tele = [m for m in s1.messages if m.get("type") in ("spaceMoveHide", "spaceMoveShow")]
        check("瞬息移动下发两段特效（原版 Magic.pas:959 FIRE2 + SpaceMove nInt=1）",
              len(tele) == 2, results)
        check("瞬息移动特效 variant=1（1590/1600，非卷轴 250/260）",
              all(m.get("variant") == 1 for m in tele), results)

        # 3) 过门不带特效（原版 EnterAnotherMap ObjBase.pas:20266 只有 RM_CHANGEMAP）
        import inspect
        move_src = inspect.getsource(type(service)._move)
        check("过门路径不带传送特效（原版 EnterAnotherMap 无特效）",
              "spaceMove" not in move_src, results)

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

        # 第 20 轮：TDigOutZombi（race 95）出土特效（ObjMon.pas:1193-1260）
        # （前面 backToSelect 已把 p 移出世界，这里重新注册回来）
        w.add_player(p)
        from types import SimpleNamespace
        from pyserver.util import now_ms
        zom = Monster(id=w.new_id(), name="僵尸2", x=p.x + 2, y=p.y,
                      hp=155, max_hp=155, level=30, race=95,
                      template=SimpleNamespace(name="僵尸2", race=95, appr=50,
                                               walkSpeed=800, attackSpeed=1000),
                      spawn_x=p.x + 2, spawn_y=p.y)
        zom.hidden = True
        w.add_actor(zom, p.map)
        try:
            s1.messages.clear()
            w._monster_ai(zom, now_ms())
            # 出土判定：3×3 内的活玩家触发 ComeOut
            dig = next((m for m in s1.messages if m.get("type") == "digUp"), None)
            check("钻地僵尸 3 格内玩家触发 digUp 广播",
                  dig is not None and not zom.hidden and dig.get("entity", {}).get("id") == zom.id,
                  results)
            check("出土后 1 秒内不移动（原版 m_dwWalkTick := +1000）",
                  zom.next_move_at > 0, results)
            s1.messages.clear()
            w._monster_ai(zom, now_ms() + 2000)
            check("出土后走普通 AI（不再重复 digUp）",
                  not any(m.get("type") == "digUp" for m in s1.messages), results)
        finally:
            w.remove_actor(zom.id)

        # 第 21 轮：TZilKinZombi（race 96）复活僵尸（ObjMon.pas:1263-1313）
        zil = Monster(id=w.new_id(), name="僵尸3", x=p.x + 2, y=p.y,
                      hp=400, max_hp=400, level=30, race=96,
                      template=SimpleNamespace(name="僵尸3", race=96, appr=51,
                                               walkSpeed=800, attackSpeed=1000),
                      spawn_x=p.x + 2, spawn_y=p.y)
        zil.exp = 200
        zil.zil_revives = 2          # 直接指定剩余复活次数（跳过 1/3 随机）
        w.add_actor(zil, p.map)
        try:
            s1.messages.clear()
            now = now_ms()
            w.kill_monster(zil, now)
            # Die：count>0 → 记复活时间（4~23 秒后）并 Dec
            check("复活僵尸死亡安排 revive_at（4~23 秒窗口）且 zil_revives 2→1",
                  zil.zil_revive_at > now and zil.zil_revives == 1
                  and not zil.alive and zil.pending_respawn, results)
            check("复活窗口未到不广播 alive",
                  not any(m.get("type") == "alive" for m in s1.messages), results)
            check("重生点被顺延到复活窗口之后（避免原地复活与补刷重复）",
                  zil.respawn_at >= zil.zil_revive_at, results)
            s1.messages.clear()
            w._tick_zilkin_revive(now + 1_000)
            check("复活窗口未到（1 秒 < 最短 4 秒）不复活",
                  not zil.alive
                  and not any(m.get("type") == "alive" for m in s1.messages),
                  results)
            w._tick_zilkin_revive(now + 30_000)
            alive = next((m for m in s1.messages if m.get("type") == "alive"), None)
            check("窗口到达且附近有玩家 → 原地复活广播 alive",
                  alive is not None and zil.alive
                  and alive.get("entity", {}).get("id") == zil.id, results)
            check("复活时 MaxHP/经验减半（MaxHP shr 1 / FightExp div 2）",
                  zil.max_hp == 200 and zil.exp == 100 and zil.hp == 200, results)
            check("复活后不再安排重生补刷（pending/respawn 清零）",
                  not zil.pending_respawn and zil.respawn_at == 0
                  and zil.zil_revive_at == 0, results)
            # 第二次死亡：revives=1 → 还能再复活一次
            s1.messages.clear()
            now2 = now_ms()
            w.kill_monster(zil, now2)
            check("第二次死亡 zil_revives 1→0，仍安排复活",
                  zil.zil_revive_at > now2 and zil.zil_revives == 0, results)
            s1.messages.clear()
            w._tick_zilkin_revive(now2 + 30_000)
            check("第二次复活成功", zil.alive and zil.max_hp == 100, results)
            # 第三次死亡：Die 里 count=0 不设时间（无条件 Dec → -1），彻底死亡
            s1.messages.clear()
            now3 = now_ms()
            w.kill_monster(zil, now3)
            check("复活次数耗尽：死亡后 revive_at=0（不安排复活）、zil_revives=-1",
                  zil.zil_revive_at == 0 and zil.zil_revives == -1
                  and not zil.alive, results)
            s1.messages.clear()
            w._tick_zilkin_revive(now3 + 60_000)
            check("耗尽后永不复活", not zil.alive
                  and not any(m.get("type") == "alive" for m in s1.messages), results)
            # 观察者条件：m_VisibleActors.Count > 0。这里临时清空 world.players()
            # （测试环境里可能有其他重登玩家在场，用距离判定不可靠）。
            zil2 = Monster(id=w.new_id(), name="僵尸30", x=zil.x + 1, y=zil.y,
                           hp=100, max_hp=100, level=30, race=96,
                           template=zil.template,
                           spawn_x=zil.x + 1, spawn_y=zil.y)
            zil2.zil_revives = 1
            w.add_actor(zil2, zil.map)
            real_players = w.players
            try:
                w.players = lambda: []
                w.kill_monster(zil2, now_ms())
                s1.messages.clear()
                w._tick_zilkin_revive(now_ms() + 30_000)
                check("附近无活玩家不复活（m_VisibleActors.Count = 0）",
                      not zil2.alive
                      and not any(m.get("type") == "alive" for m in s1.messages),
                      results)
                # 有观察者则复活正常发生
                w.players = real_players
                s1.messages.clear()
                w._tick_zilkin_revive(now_ms() + 30_000)
                check("观察者回到视野内后下一轮复活照常",
                      zil2.alive
                      and any(m.get("type") == "alive" for m in s1.messages),
                      results)
            finally:
                w.players = real_players
                w.remove_actor(zil2.id)
        finally:
            w.remove_actor(zil.id)

        # 第 22 轮：经验惩罚（CalcGetExp）+ 组队加成/按等级分配 + 大刀守卫
        from pyserver.util import calc_get_exp, RED_NAME_PK_POINT
        check("经验惩罚：玩家等级 < 怪等级+10 全额",
              calc_get_exp(20, 15, 100) == 100, results)
        check("经验惩罚：玩家 30 杀 10 级怪（超 10 级）扣 10/15 → 33",
              calc_get_exp(30, 10, 100) == 33, results)
        check("经验惩罚：扣到 ≤0 保底 1",
              calc_get_exp(80, 10, 3) == 1, results)
        # 单人杀怪走 _grant_exp：全额套惩罚
        victim = Monster(id=w.new_id(), name="骷髅", x=p.x + 1, y=p.y,
                         hp=10, max_hp=10, level=10, race=86,
                         template=SimpleNamespace(name="骷髅", race=86, appr=8,
                                                  walkSpeed=800, attackSpeed=1000,
                                                  level=10, exp=100, ac=0, dc=1, maxDc=2),
                         spawn_x=p.x + 1, spawn_y=p.y)
        victim.hp = 0
        w.add_actor(victim, p.map)
        try:
            lv0, exp0 = p.level, p.exp
            service._grant_exp(p, victim)
            # 击杀经验还要乘统一倍率（config/game_rates.json），期望值同源计算
            want = w.kill_exp("骷髅", calc_get_exp(p.level, 10, 100))
            check("单人击杀经验带等级惩罚（原版 CalcGetExp）",
                  p.exp == exp0 + want, results)
        finally:
            w.remove_actor(victim.id)
        # 实例经验（原版 m_dwFightExp）：出生从模板写入实例，杀怪结算读实例值。
        # race 96 复活僵尸每复活一次 ``m_dwFightExp div 2``（ObjMon.pas:1305），
        # 读模板值会让复活过的僵尸仍按原始经验给——这里覆盖这个回归。
        fresh = Monster(name="经验测试", hp=1, max_hp=1,
                        template=SimpleNamespace(exp=77))
        check("怪物出生即把模板 Exp 写入实例（m_dwFightExp）",
              fresh.exp == 77, results)
        zilx = Monster(id=w.new_id(), name="僵尸4", x=p.x + 1, y=p.y,
                       hp=400, max_hp=400, level=10, race=96,
                       template=SimpleNamespace(name="僵尸4", race=96, appr=51,
                                                walkSpeed=800, attackSpeed=1000,
                                                level=10, exp=200, ac=0, dc=1, maxDc=2),
                       spawn_x=p.x + 1, spawn_y=p.y)
        zilx.max_hp //= 2            # 模拟一次复活后的状态
        zilx.exp //= 2
        zilx.hp = 0
        w.add_actor(zilx, p.map)
        try:
            lv, e0 = p.level, p.exp
            service._grant_exp(p, zilx)
            want = w.kill_exp("僵尸4", calc_get_exp(lv, 10, 100))
            check("复活僵尸击杀经验取实例值（200→100，非模板 200）",
                  zilx.exp == 100 and p.exp - e0 == want, results)
        finally:
            w.remove_actor(zilx.id)
        # 组队：2 人同图 ≤12 格，经验 ×1.3 后按等级比例分（ObjBase.pas:15564/15594）
        mate = Player(id=w.new_id(), name="队友", x=p.x + 2, y=p.y,
                      hp=50, max_hp=50, map=p.map)
        w.add_player(mate)
        try:
            old = {a.id: (a.level, a.exp) for a in (p, mate)}
            w.create_party(p.id, mate.id)
            victim2 = Monster(id=w.new_id(), name="骷髅", x=p.x + 1, y=p.y,
                              hp=10, max_hp=10, level=10, race=86,
                              template=victim.template,
                              spawn_x=p.x + 1, spawn_y=p.y)
            w.add_actor(victim2, p.map)
            try:
                service._grant_exp(p, victim2)
                # 组队期望动态计算：先 CalcGetExp 惩罚 × 统一倍率，再 ×1.3、按等级比例分
                exp_after = w.kill_exp("骷髅", calc_get_exp(p.level, 10, 100))
                total = round(exp_after * 1.3)
                check("组队 2 人：总经验 ×1.3 后按等级比例分配（非平分）",
                      p.exp - old[p.id][1] == round(total / (p.level + mate.level) * p.level)
                      and mate.exp - old[mate.id][1] == round(total / (p.level + mate.level) * mate.level),
                      results)
            finally:
                w.remove_actor(victim2.id)
                w.leave_party(p.id)
        finally:
            w.remove_player(mate)

        # 大刀守卫：主动攻击视野内怪物（ObjGuard.pas:87-126，race>=RC_MONSTER 80）。
        # 测试图上还有别的怪（原版语义 = 锁定扫描到的第一只），所以要挑一块
        # 距所有已有怪 >10 格的空地摆放，保证锁定目标就是测试造的怪。
        spot = None
        for dx in range(15, 90):
            x0 = p.x + dx
            if x0 >= p.map.width - 2:
                break
            if all(max(abs(a.x - x0), abs(a.y - p.y)) > 10
                   for a in p.map.actors.values() if a is not p):
                spot = x0
                break
        if spot is None:
            spot = p.x + 15
        guard = Monster(id=w.new_id(), name="卫士", x=spot + 6, y=p.y,
                        hp=500, max_hp=500, level=40, race=11,
                        template=SimpleNamespace(name="卫士", race=11, appr=12,
                                                 walkSpeed=800, attackSpeed=1000,
                                                 level=40, dc=20, maxDc=40,
                                                 hit=30, speed=15),
                        spawn_x=spot + 6, spawn_y=p.y)
        mob = Monster(id=w.new_id(), name="骷髅", x=guard.x + 1, y=guard.y,
                      hp=30, max_hp=30, level=10, race=86,
                      template=victim.template, spawn_x=guard.x + 1, spawn_y=guard.y)
        gx, gy = guard.x, guard.y
        w.add_actor(guard, p.map)
        w.add_actor(mob, p.map)
        try:
            w._monster_ai(guard, now_ms())
            check("大刀守卫主动攻击视野内怪物（不移动）",
                  mob.hp < mob.max_hp and (guard.x, guard.y) == (gx, gy), results)
            # 打玩家召唤物（race>=80 的有主对象，m_boAttackPet=True 照打）
            # 守卫每次按 actors 插入顺序锁定「扫描到的第一只」（ObjGuard.pas:87-126），
            # 上一段的骷髅还在场就会被一直锁着打，先清场保证目标是召唤物。
            w.remove_actor(mob.id)
            slave = Monster(id=w.new_id(), name="骷髅宝宝", x=guard.x + 1, y=guard.y + 1,
                            hp=30, max_hp=30, level=10, race=100,
                            template=victim.template, master_id=p.id,
                            spawn_x=guard.x + 1, spawn_y=guard.y + 1)
            w.add_actor(slave, p.map)
            try:
                w._monster_ai(guard, now_ms() + 1500)
                check("大刀守卫攻击玩家召唤物（m_boAttackPet=True）",
                      slave.hp < slave.max_hp, results)
            finally:
                w.remove_actor(slave.id)
            # 红名玩家被锁定攻击（PKLevel = PkPoint div 100 >= 2）
            p.pk_point = RED_NAME_PK_POINT
            p.hp = 100
            p.x, p.y = guard.x + 1, guard.y   # 原版守卫站桩，近战 _Attack 1 格内才命中
            try:
                w._monster_ai(guard, now_ms() + 3000)
                check("大刀守卫攻击深红名玩家（PKLevel>=2，近战 1 格）",
                      p.hp < 100, results)
            finally:
                p.pk_point = 0
                p.x, p.y = gx, gy - 1
            # race 12 宠物卫士不打有主的召唤物（m_boAttackPet=False，ObjGuard.pas:130-136）
            guard12 = Monster(id=w.new_id(), name="卫士1", x=p.x + 6, y=p.y + 6,
                              hp=500, max_hp=500, level=40, race=12,
                              template=SimpleNamespace(name="卫士1", race=12, appr=12,
                                                       walkSpeed=800, attackSpeed=1000,
                                                       level=40, dc=20, maxDc=40),
                              spawn_x=p.x + 6, spawn_y=p.y + 6)
            w.add_actor(guard12, p.map)
            slave2 = Monster(id=w.new_id(), name="骷髅宝宝", x=guard12.x + 1, y=guard12.y,
                             hp=30, max_hp=30, level=10, race=100,
                             template=victim.template, master_id=p.id,
                             spawn_x=guard12.x + 1, spawn_y=guard12.y)
            w.add_actor(slave2, p.map)
            try:
                w._monster_ai(guard12, now_ms() + 4500)
                check("宠物卫士(race12)不打有主召唤物（m_boAttackPet=False）",
                      slave2.hp == slave2.max_hp, results)
            finally:
                w.remove_actor(slave2.id)
                w.remove_actor(guard12.id)
        finally:
            w.remove_actor(mob.id)
            w.remove_actor(guard.id)

        # 空图休眠：无人在线的图怪物 AI 冻结（第 20 轮性能优化）
        far_map = next((m for m in w.maps.values()
                        if all(pl.map is not m for pl in w.players())
                        and any(isinstance(a, Monster) and a.alive
                                for a in m.actors.values())), None)
        if far_map is not None:
            far_mon = next(a for a in far_map.actors.values()
                           if isinstance(a, Monster) and a.alive)
            x0, y0 = far_mon.x, far_mon.y
            w.tick(now_ms())
            check("无人地图怪物 AI 冻结（空图休眠）",
                  (far_mon.x, far_mon.y) == (x0, y0), results)

        # 仓库：@storage/@getback 标签 → storageOpen/storageList；存取闭环
        # （原版 ObjNpc.pas:1560-1566 + SM_SENDUSERSTORAGEITEM/SM_SAVEITEMLIST）
        wh = next((n for n in w.npcs.values()
                   if n.script is not None and n.script.section("storage")), None)
        if wh is not None:
            wh.map = p.map
            wh.x, wh.y = p.x + 1, p.y
            item = w.new_item_instance(next(t for t in w.item_templates.values()
                                            if 0 <= t.stdMode <= 2))
            item["durability"] = 2000
            item["durabilityMax"] = 3000
            p.bag.append(item)
            mi = int(item["makeIndex"])
            try:
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "npcSelect", "npcId": wh.id, "value": "@storage"}))
                check("@storage → storageOpen（原版 SM_SENDUSERSTORAGEITEM）",
                      any(m.get("type") == "storageOpen"
                          and m.get("npcId") == wh.id for m in s1.messages),
                      results)
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "storageStore", "npcId": wh.id, "makeIndex": mi}))
                stored = any(int(it.get("makeIndex", 0)) == mi for it in p.storage)
                check("storageStore → 物品入仓（容量口径 39）",
                      stored and not p.bag_item(mi), results)
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "npcSelect", "npcId": wh.id, "value": "@getback"}))
                lst = next((m for m in s1.messages if m.get("type") == "storageList"), None)
                check("@getback → storageList 含刚存的物品（SM_SAVEITEMLIST）",
                      lst is not None
                      and any(int(i.get("makeIndex", 0)) == mi for i in lst.get("items", [])),
                      results)
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "storageTake", "npcId": wh.id, "makeIndex": mi}))
                check("storageTake → 物品回背包 + 列表刷新（DelStorageItem）",
                      p.bag_item(mi) is not None and not p.storage
                      and any(m.get("type") == "storageList" for m in s1.messages),
                      results)
            finally:
                if p.bag_item(mi) is None:
                    p.bag.append(item)
                p.storage.clear()
        else:
            check("仓库流程（环境无仓库 NPC，跳过）", True, results)

        # 玛法领路人新手补助：脚本 #ACT give 金币 后必须下发 bagUpdate（金币+背包），
        # 否则服务端发了奖励、客户端却看不到（原版 ActionOfGiveItem 给金 GoldChanged
        # ObjNpc.pas:9001 / 给物 SendAddItem :9028；Java/Python 旧实现都漏了同步）。
        guide = next((n for n in w.npcs.values() if n.name == "玛法领路人"), None)
        if guide is not None and guide.script is not None:
            guide.map = p.map
            guide.x, guide.y = p.x + 1, p.y
            old_gold, old_level = p.gold, p.level
            old_flag = p.quest_params.get(1)
            p.level, p.gold = 3, 0
            p.quest_params.pop(1, None)   # 清一次性标记 [001]
            try:
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "npcTalk", "npcId": guide.id}))
                talk = next((m for m in s1.messages if m.get("type") == "npcSay"), None)
                check("玛法领路人 3 级主对话含 @next1 领取入口",
                      talk is not None and "@next1" in talk.get("text", ""), results)
                s1.messages.clear()
                service.on_message(s1, json.dumps(
                    {"type": "npcSelect", "npcId": guide.id, "value": "@next1"}))
                bag = next((m for m in s1.messages if m.get("type") == "bagUpdate"), None)
                check("玛法领路人 @next1 给 5000 金（服务端到账）", p.gold == 5000, results)
                check("玛法领路人领取后下发 bagUpdate 同步金币（客户端可见）",
                      bag is not None and bag.get("gold") == 5000, results)
                check("玛法领路人一次性标记 [001] 置位", p.quest_params.get(1) == 1, results)
            finally:
                p.gold, p.level = old_gold, old_level
                if old_flag is None:
                    p.quest_params.pop(1, None)
                else:
                    p.quest_params[1] = old_flag
        else:
            check("玛法领路人补助同步（环境无该 NPC，跳过）", True, results)

        # 挖空变骨架：brief_json 必须带 skeleton（否则重进地图尸体「变回」未挖帧）
        sk = Monster(id=w.new_id(), name="鸡", x=p.x + 3, y=p.y,
                     hp=1, max_hp=1, level=5, race=51,
                     template=victim.template, spawn_x=p.x + 3, spawn_y=p.y)
        w.add_actor(sk, p.map)
        try:
            sk.alive = False
            sk.corpse_until = now_ms() + 60_000
            sk.skeleton = True
            check("brief_json 下发 skeleton=True（重进地图恢复骨架帧）",
                  sk.brief_json().get("skeleton") is True, results)
            sk.skeleton = False
            check("未挖尸体 brief_json skeleton=False",
                  sk.brief_json().get("skeleton") is False, results)
        finally:
            w.remove_actor(sk.id)
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
