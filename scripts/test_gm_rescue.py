#!/usr/bin/env python3
"""GM 解卡三件套的服务端离线回归（纯标准库，Python 3.10）。

覆盖用户 2026-11-22 要求的 @解卡 角色名 / @卡死扫描 / @解卡 all：
- 卡死判定：地图未登记 / 坐标越界 / 坐标不可走
- 落点：优先 home，home 无效回退比奇出生点
- 离线改库：只更新 map_name/x/y，不碰物品/金币
- 在线传送：SpaceMove + 落库

用法：D:/Python310/python.exe scripts/test_gm_rescue.py
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

    def send_json(self, obj):
        self.messages.append(obj)

    def send_text(self, text):
        self.messages.append(json.loads(text))

    def drain(self):
        self.messages.clear()

    def close(self):
        pass


def make_player(service, tag: str, job: int = 0):
    sess = Session()
    name = "R" + tag + str(job)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    return service.find_by_session(sess), sess


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-gmrescue-test.db"
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    return service, db_path


def main() -> int:
    service, db_path = build()
    w = service.world

    print("\n[1] 卡死判定")
    check("地图未登记 → 卡死", service._is_stuck("99999", 10, 10) is not None)
    check("坐标越界 → 卡死", service._is_stuck("0", 99999, 99999) is not None)
    # 比奇省 (0,0) 通常是墙；用一个确定不可走格
    gm0 = w.get_or_load_map("0")
    wall = None
    for yy in range(min(20, gm0.height)):
        for xx in range(min(20, gm0.width)):
            if not gm0.can_walk(xx, yy):
                wall = (xx, yy)
                break
        if wall:
            break
    if wall:
        check("坐标在墙里 → 卡死",
              service._is_stuck("0", wall[0], wall[1]) is not None,
              f"wall={wall}")
    else:
        check("坐标在墙里 → 卡死（跳过：未找到墙格）", True)
    # 出生点可走 → 不卡
    check("出生点 → 正常", service._is_stuck("0", w.start_x, w.start_y) is None,
          f"spawn=({w.start_x},{w.start_y})")

    print("\n[2] 落点选择：home 有效 / home 无效回退")
    dmap, dx, dy = service._rescue_dest("0", w.start_x, w.start_y)
    check("home 有效 → 回家点", dmap == "0" and gm0.can_walk(dx, dy),
          f"{dmap}({dx},{dy})")
    dmap2, dx2, dy2 = service._rescue_dest("99999", 5, 5)
    check("home 未登记 → 回比奇出生点",
          dmap2 == w.default_map.name and w.default_map.can_walk(dx2, dy2),
          f"{dmap2}({dx2},{dy2})")
    dmap3, dx3, dy3 = service._rescue_dest("0", 99999, 99999)
    check("home 坐标越界 → 回退可走", w.get_or_load_map(dmap3).can_walk(dx3, dy3),
          f"{dmap3}({dx3},{dy3})")

    print("\n[3] 离线角色解卡：改库不碰其他字段")
    gm_acc = service.db.register("rescue_acc", "pw12345")
    cid = service.db.insert_character({
        "account_id": gm_acc.id, "name": "StuckHero", "sex": 0, "hair": 1,
        "job": 0, "level": 10, "exp": 0, "map_name": "99999", "x": 3, "y": 4,
        "dir": 0, "hp": 100, "mp": 50, "gold": 7777,
        "home_map": "0", "home_x": w.start_x, "home_y": w.start_y,
        "items": [], "storage": [], "skills": "{}", "quick_slots": [],
        "created_at": int(time.time()),
    })
    gm_sess = Session()
    for msg in ({"type": "login", "username": "player", "password": "123456"},):
        pass
    # 直接调 GM 命令（GM 自己在线）
    gm_p, _ = make_player(service, "gm", 0)
    gm_p.is_gm = True
    service._gm_command(gm_p, "解卡 StuckHero")
    rec = service.db.find_character("StuckHero")
    check("离线解卡后地图恢复", rec["map_name"] == "0", f"map={rec['map_name']}")
    check("离线解卡后坐标可走", gm0.can_walk(rec["x"], rec["y"]),
          f"xy=({rec['x']},{rec['y']})")
    check("金币未被改动", rec["gold"] == 7777, f"gold={rec['gold']}")
    check("home 未被改动", rec["home_map"] == "0")

    print("\n[4] 卡死扫描 / 批量解卡")
    # 再造两个卡死角色
    for nm in ("StuckA", "StuckB"):
        service.db.insert_character({
            "account_id": gm_acc.id, "name": nm, "sex": 0, "hair": 1,
            "job": 0, "level": 5, "exp": 0, "map_name": "88888", "x": 1, "y": 1,
            "dir": 0, "hp": 50, "mp": 20, "gold": 100,
            "home_map": "0", "home_x": w.start_x, "home_y": w.start_y,
            "items": [], "storage": [], "skills": "{}", "quick_slots": [],
            "created_at": int(time.time()),
        })
    stuck_names = [r["name"] for r in service.db.all_characters()
                   if service._is_stuck(r["map_name"], r["x"], r["y"]) is not None]
    check("扫描识别两个新卡死角色", "StuckA" in stuck_names and "StuckB" in stuck_names,
          f"stuck={stuck_names}")
    service._gm_command(gm_p, "解卡 all")
    rec_a = service.db.find_character("StuckA")
    rec_b = service.db.find_character("StuckB")
    check("批量解卡 A", rec_a["map_name"] == "0" and gm0.can_walk(rec_a["x"], rec_a["y"]),
          f"{rec_a['map_name']}({rec_a['x']},{rec_a['y']})")
    check("批量解卡 B", rec_b["map_name"] == "0" and gm0.can_walk(rec_b["x"], rec_b["y"]),
          f"{rec_b['map_name']}({rec_b['x']},{rec_b['y']})")

    print("\n[5] 在线角色解卡")
    live, live_sess = make_player(service, "live", 0)
    live_name = live.name
    # 先塞进未登记地图（用 GM 命令能跨图传送吗？直接改内存模拟）
    class FakeMap:
        name = "99999"
        actors = {}
        def remove(self, _id): pass
    live.map = FakeMap()  # 模拟卡在未登记地图（不进 map 容器）
    live.x, live.y = 5, 5
    service._gm_command(gm_p, f"解卡 {live_name}")
    check("在线解卡后地图有效", live.map is not None and live.map.name == "0"
          or (hasattr(live.map, 'name') and live.map.name == "0"),
          f"map={getattr(live.map, 'name', '?')}")
    check("在线解卡后坐标可走", gm0.can_walk(live.x, live.y),
          f"xy=({live.x},{live.y})")
    # 落库验证：重查 db
    rec_live = service.db.find_character(live_name)
    check("在线解卡立即落库", rec_live["map_name"] == "0", f"map={rec_live['map_name']}")

    print(f"\n结果: {PASS} PASS / {FAIL} FAIL")
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
