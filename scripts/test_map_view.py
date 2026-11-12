"""第 12 轮永久回归：MISS 之外的地图/显示名/视野/换图锁。

用 ``D:/Python310/python.exe scripts/test_map_view.py`` 运行（纯离线，不连 8080）。
覆盖的原版依据：
  - M2Share.pas:3630  FilterShowName（怪物显示名截断数字后缀）
  - FrnEngn.pas:85-91 g_nGameTime 分档
  - ObjBase.pas:4283  DayBright()
  - ObjBase.pas:5618/5796 SM_NEWMAP/SM_CHANGEMAP 的 cdir = DayBright()
  - PlayScn.pas:2388 客户端 DarkLevel / g_boViewFog
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load                     # noqa: E402
from pyserver.db import Database                    # noqa: E402
from pyserver.service import GameService             # noqa: E402
from pyserver.world import (Monster, Player, day_bright_for_hour,  # noqa: E402
                            filter_show_name, now_ms)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  -> {detail}")


def main() -> int:
    # 1. FilterShowName
    for src, want in [("僵尸3", "僵尸"), ("僵尸20", "僵尸"), ("骷髅0", "骷髅"),
                      ("稻草人1", "稻草人"), ("洞蛆", "洞蛆"), ("尸王3", "尸王"),
                      ("多钩猫0", "多钩猫"), ("祖玛卫士-1", "祖玛卫士")]:
        check(f"FilterShowName({src})={want}", filter_show_name(src) == want,
              filter_show_name(src))

    # 2. FrnEngn.pas 的 g_nGameTime 分档
    for hour, want in [(8, 1), (17, 1), (23, 2), (11, 2),
                       (4, 0), (15, 0), (0, 3), (12, 3)]:
        check(f"day_bright_for_hour({hour})={want}",
              day_bright_for_hour(hour) == want, day_bright_for_hour(hour))

    cfg = load()
    tmp = Path(tempfile.mkdtemp()) / "view.db"
    db = Database(tmp)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    w = service.world

    # 3. MapInfo 标志解析
    check("D401 带 MINE 标志", "MINE" in w.map_flags.get("D401", ()),
          w.map_flags.get("D401"))
    check("G001 带 DAY 标志", "DAY" in w.map_flags.get("G001", ()),
          w.map_flags.get("G001"))
    check("T0001 带 DARK 标志", "DARK" in w.map_flags.get("T0001", ()),
          w.map_flags.get("T0001"))

    # 4. DayBright 映射：地下恒暗、DAY 恒亮、普通地图随昼夜
    check("D401 地下 → dark=1", w.is_underground("D401") and w.dark_level("D401") == 1)
    check("D011 天然洞穴（无标志，按标题）→ 地下",
          w.is_underground("D011") and w.dark_level("D011") == 1)
    check("G001 DAY → dark=0", w.dark_level("G001") == 0, w.dark_level("G001"))
    old = w.day_bright
    try:
        w.day_bright = 1
        check("白天地表 dark=0", w.dark_level("0") == 0, w.dark_level("0"))
        check("白天矿区仍 dark=1", w.dark_level("D401") == 1, w.dark_level("D401"))
        w.day_bright = 3
        check("深夜地表 dark=1", w.dark_level("0") == 1, w.dark_level("0"))
        w.day_bright = 2
        check("过渡时段地表 dark=2", w.dark_level("0") == 2, w.dark_level("0"))
    finally:
        w.day_bright = old

    # 5. 名字下发：怪物剥、玩家不剥
    m = Monster(id=1, name="僵尸20", x=1, y=1, appr=50, race=95)
    check("Monster.brief_json 显示名=僵尸", m.brief_json()["name"] == "僵尸",
          m.brief_json()["name"])
    pl = Player(id=2, name="Hero7", x=1, y=1)
    check("Player.brief_json 保留名字中的数字", pl.brief_json()["name"] == "Hero7",
          pl.brief_json()["name"])

    # 6. 换图短锁：拦下在途 move，避免踩中相邻回程门弹回
    class Session:
        def __init__(self):
            self.messages: list[dict] = []
            self.account_id = 0
            self.account_name = "viewer"
            self.player_name = ""

        def send_json(self, obj): self.messages.append(obj)

        def send_text(self, raw): self.messages.append(json.loads(raw))

    sess = Session()
    for msg in ({"type": "register", "username": "viewer", "password": "x12345678"},
                {"type": "login", "username": "viewer", "password": "x12345678"},
                {"type": "createCharacter", "name": "ViewHero", "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": "ViewHero"},
                {"type": "noticeOk", "name": "ViewHero"}):
        service.on_message(sess, json.dumps(msg))
    hero = w.players_by_name.get("ViewHero")
    check("测试角色已进场", hero is not None and hero.session is sess)
    if hero is None:
        return 1

    w.change_map(hero, "D401", 76, 14)
    check("change_map 写入 map_change_at", hero.map_change_at > 0, hero.map_change_at)
    service.on_message(sess, json.dumps({"type": "move", "dir": 4}))
    check("换图瞬间在途 move 被拦（仍在 76,14）",
          (hero.x, hero.y, hero.map.name) == (76, 14, "D401"),
          (hero.x, hero.y, hero.map.name))

    # 短锁过后走一步踩门 D401 76,15 -> D411 59,8
    hero.map_change_at = now_ms() - 1000
    sess.messages.clear()
    service.on_message(sess, json.dumps({"type": "move", "dir": 4}))
    check("短锁过后可踩门进 D411", (hero.map.name, hero.x, hero.y) == ("D411", 59, 8),
          (hero.map.name, hero.x, hero.y))
    cm = [x for x in sess.messages if x.get("type") == "changeMap"]
    check("changeMap 带新标题与 miniMapIndex",
          bool(cm) and cm[0]["map"]["title"] == "北部矿区"
          and cm[0]["miniMapIndex"] == 14 and cm[0]["darkLevel"] >= 1,
          cm[0]["map"] if cm else None)
    check("换图后再次上锁", hero.map_change_at > 0, hero.map_change_at)

    print("\n" + "=" * 64)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
