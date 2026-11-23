#!/usr/bin/env python3
"""高级武士/法师/道士之家测验任务的离线回归（纯标准库）。

对齐原版依据：
- ``MonGen``（ObjNpc.pas:7856-7862）：x = Random(range*2+1) + (Param2-range)，
  逐只 ``RegenMonsterByName(Param1 地图, x, y, 怪名)``；模板不存在静默失败
  （沉默版 9Ehw-0114 的「暗之沃玛护卫」是死引用，Monster DB 无此模板）。
- ``MonClear``：清图怪；``CheckMonMap``（:7153-7159）图上怪数 >= n 才通过；
  ``CheckHum``（:7163-7166）图上玩家数 >= n；``CheckBaggage``（:7168）
  ``IsEnoughBag`` 背包有空位。
- ``TimeRecall``（ObjNpc.pas:7800-7806 + ObjBase.pas:6440-6443）：记录当前
  map/x/y，n 分钟后 SpaceMove 送回；到期或重登才取消，脚本 map 换图不取消
  （原版取消点在 BaseObjectMove:23614-23616，回城类传送才走它）。
- 入口旗标 756 在本数据集无任何设置点（新手引导 NPC 缺失），按原版
  设计意图补在本职业之家 @main 末尾：本职业未做完测验的玩家对话即
  ``set [756] 1 + goto @Q706``；命运链旗标 204-210 玩家优先走原分支，
  203 阶段（卫家店→铁匠铺途中）维持原版 busy 文本。

用法：
    python scripts/test_npc_home_quest.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import BAG_SIZE  # noqa: E402
from pyserver.world import now_ms  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f"  {detail}" if detail else ""))


class Session:
    def __init__(self):
        self.messages: list[dict] = []

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def drain(self) -> None:
        self.messages.clear()

    def close(self) -> None:
        pass


def make_player(service, tag: str, job: int) -> object:
    sess = Session()
    name = "Q" + tag + str(job)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    return p


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-npcquest-test.db"
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    return service


def npc_by_script(w, script_name: str, map_name: str = ""):
    # NPC 实际 script_name 带中文后缀（如「9Eht-高级道士」），测试传的是短前缀。
    for n in w.npcs.values():
        if n.script_name.startswith(script_name) and (not map_name or n.map_name == map_name):
            return n
    return None


def bag_count(w, p, book_name: str) -> int:
    return sum(1 for it in p.bag
               if (w.item_templates.get(it["index"]) is not None
                   and w.item_templates[it["index"]].name == book_name))


def main() -> None:
    service = build()
    w = service.world
    engine = service._engine()

    print("\n[1] 道士之家：入口自动置 756，7 级出测验选项")
    p = make_player(service, str(int(time.time()) % 100000), job=2)
    p.level = 7
    npc_tao = npc_by_script(w, "9Eht", "0113")
    assert npc_tao is not None, "找不到高级道士 NPC"
    home_map, home_x, home_y = p.map.name, p.x, p.y
    r = engine.execute(p, npc_tao, "main")
    check("对话自动 set [756]", p.quest_params.get(756, 0) == 1)
    check("出现测验文案（羊鹿鸡）", "羊" in r.say_text and "鸡" in r.say_text)
    check("测验选项 @Q706_1", "@Q706_1" in r.say_text)

    # 2026-11-22 修正：三张任务图（01142/01152/01132）无出口门 + 回城卷无效，
    # 唯一出路 TimeRecall 定时器不写存档，断线/下线重登后丢失 → 角色永久卡死
    # （Garden 卡 01142 实例）。用户选定「去掉传送+刷怪」，导师处直接判定发书。
    print("\n[2] 测验通过：直接送《治愈术》+ 置 757，不传送/不刷怪/无定时器")
    r = engine.execute(p, npc_tao, "Q706_1")
    check("送《治愈术》书", bag_count(w, p, "治愈术") == 1, f"bag={len(p.bag)}")
    check("set [757]", p.quest_params.get(757, 0) == 1)
    check("不传送（留在原地）", not r.moved and p.map.name == home_map,
          f"map={p.map.name}")
    check("无刷怪", w.count_map_monsters("01132") == 0)
    check("无 TimeRecall 残留", p.time_recall is None)
    check("通过文案", "考验" in r.say_text or "祝贺" in r.say_text)

    print("\n[3] 再做一次：757 已置 → 走 @Q707 提示双击书，不重复发")
    r2 = engine.execute(p, npc_tao, "main")
    check("不再重复发书", bag_count(w, p, "治愈术") == 1)
    check("提示双击书", "双击" in r2.say_text or "火球" in r2.say_text
          or "治愈" in r2.say_text)

    print("\n[4] 包满 → checkbaggage 拒发")
    p.quest_params[757] = 0
    fill = w.item_templates[next(iter(w.item_templates))]
    while len(p.bag) < BAG_SIZE:
        p.bag.append({"makeIndex": 800000 + len(p.bag), "index": fill.index,
                      "durability": 1000, "durabilityMax": 1000, "lucky": 0})
    r = engine.execute(p, npc_tao, "Q706_1")
    check("包满提示", "清理" in r.say_text or "腾出" in r.say_text)
    check("不再发书", bag_count(w, p, "治愈术") == 1)

    print("\n[5] 法师之家：火球术直通发书")
    pm = make_player(service, str(int(time.time()) % 100000), job=1)
    pm.level = 7
    npc_mage = npc_by_script(w, "9Ewi", "0115")
    assert npc_mage is not None, "找不到高级魔法师 NPC"
    r = engine.execute(pm, npc_mage, "main")
    check("法师自动 set [756]", pm.quest_params.get(756, 0) == 1)
    r = engine.execute(pm, npc_mage, "Q706_1")
    check("送《火球术》书", bag_count(w, pm, "火球术") == 1)
    check("法师不传送", not r.moved and pm.map.name != "01152")
    check("法师 set [757]", pm.quest_params.get(757, 0) == 1)

    print("\n[6] 战士之家：基本剑术直通发书（暗之沃玛护卫死引用已不复存在）")
    pw = make_player(service, str(int(time.time()) % 100000), job=0)
    pw.level = 7
    npc_war = npc_by_script(w, "9Ehw", "0114")
    assert npc_war is not None, "找不到高级武士 NPC"
    r = engine.execute(pw, npc_war, "main")
    check("战士自动 set [756]", pw.quest_params.get(756, 0) == 1)
    r = engine.execute(pw, npc_war, "Q706_1")
    check("送《基本剑术》书", bag_count(w, pw, "基本剑术") == 1)
    check("战士不传送", not r.moved and pw.map.name != "01142")
    check("战士 set [757]", pw.quest_params.get(757, 0) == 1)

    print("\n[9] 职业守卫与命运链守卫")
    pw.quest_params[756] = 0
    pw.quest_params[757] = 0
    r = engine.execute(pw, npc_tao, "main")          # 战士闯道士之家
    check("非本职业不给测验", pw.quest_params.get(756, 0) == 0
          and "圣僧" in r.say_text)
    pw.quest_params[203] = 1                          # 卫家店→铁匠铺途中
    r = engine.execute(pw, npc_war, "main")
    check("命运链 203 阶段维持 busy 文本", "很忙" in r.say_text
          and pw.quest_params.get(756, 0) == 0)

    print(f"\n===== PASS {PASS} / FAIL {FAIL} =====")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
