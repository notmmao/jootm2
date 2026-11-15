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
    for n in w.npcs.values():
        if n.script_name == script_name and (not map_name or n.map_name == map_name):
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

    print("\n[2] 第二人在测验房 → checkhum 拦截，不传送")
    p2 = make_player(service, str(int(time.time()) % 100000), job=0)
    gm_room = w.get_or_load_map("01132")
    x, y = w.random_walk_pos(gm_room)
    w.space_move(p2, "01132", x, y)
    r = engine.execute(p, npc_tao, "Q706_1")
    check("checkhum 命中 → 提示有人试练", "试练" in r.say_text and not r.moved,
          f"map={p.map.name}")
    w.remove_player(p2)   # 挪走占用者

    print("\n[3] 空房进入：MonGen 羊鹿鸡 + TimeRecall 记录原点 + map 传送")
    r = engine.execute(p, npc_tao, "Q706_1")
    n_mon = w.count_map_monsters("01132")
    check("传送到 01132", r.moved and p.map.name == "01132", f"map={p.map.name}")
    check("刷出 鹿10+鸡20+羊10（小屋可走格有限，≥35）", n_mon >= 35, f"monsters={n_mon}")
    tr = p.time_recall
    check("TimeRecall 记录原点 5 分钟", bool(tr) and tr["map"] == home_map
          and tr["expire"] > now_ms() + 4 * 60_000,
          f"from={tr['map'] if tr else None}")

    print("\n[4] 屋内 NPC：有怪时 checkmonmap 拦截不给书")
    npc_room = npc_by_script(w, "9Eht", "01132")
    assert npc_room is not None, "找不到 01132 屋内 NPC"
    r = engine.execute(p, npc_room, "main")
    check("有怪提示继续打", "怪" in r.say_text and not r.moved)
    check("未置 757", p.quest_params.get(757, 0) == 0)

    print("\n[5] 清完怪 → 免费送《治愈术》+ 置 757")
    w.clear_map_monsters("01132")
    check("清怪后 0 只", w.count_map_monsters("01132") == 0)
    r = engine.execute(p, npc_room, "main")
    check("送《治愈术》书", bag_count(w, p, "治愈术") == 1,
          f"bag={len(p.bag)}")
    check("set [757]", p.quest_params.get(757, 0) == 1)

    print("\n[6] 包满 → checkbaggage 拒发")
    p.quest_params[757] = 0
    w.clear_map_monsters("01132")
    fill = w.item_templates[next(iter(w.item_templates))]
    while len(p.bag) < BAG_SIZE:
        p.bag.append({"makeIndex": 800000 + len(p.bag), "index": fill.index,
                      "durability": 1000, "durabilityMax": 1000, "lucky": 0})
    r = engine.execute(p, npc_room, "main")
    check("包满提示", "清理" in r.say_text or "腾出" in r.say_text)
    check("不再发书", bag_count(w, p, "治愈术") == 1)

    print("\n[7] TimeRecall 到期 → 送回原点（脚本 map 换图不取消）")
    p.time_recall["expire"] = now_ms() - 1
    w.tick(now_ms())
    check("回到记录点", p.map.name == home_map and p.time_recall is None,
          f"map={p.map.name}, xy=({p.x},{p.y}) vs ({home_x},{home_y})")

    print("\n[8] 战士之家：暗之沃玛护卫死引用 → 空房直通送书（原版兜底行为）")
    pw = make_player(service, str(int(time.time()) % 100000), job=0)
    pw.level = 7
    npc_war = npc_by_script(w, "9Ehw", "0114")
    assert npc_war is not None, "找不到高级武士 NPC"
    r = engine.execute(pw, npc_war, "main")
    check("战士自动 set [756]", pw.quest_params.get(756, 0) == 1)
    r = engine.execute(pw, npc_war, "Q706_1")
    check("传送到 01142（怪模板缺失 → 0 只）",
          r.moved and p.map.name != "01142" and pw.map.name == "01142"
          and w.count_map_monsters("01142") == 0,
          f"monsters={w.count_map_monsters('01142')}")
    npc_room_w = npc_by_script(w, "9Ehw", "01142")
    assert npc_room_w is not None, "找不到 01142 屋内 NPC"
    r = engine.execute(pw, npc_room_w, "main")
    check("checkmonmap 不过 → 直接送《基本剑术》", bag_count(w, pw, "基本剑术") == 1)
    check("set [757]", pw.quest_params.get(757, 0) == 1)

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
