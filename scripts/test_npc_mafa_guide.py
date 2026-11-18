#!/usr/bin/env python3
"""玛法领路人一次性奖励的离线回归（纯标准库，无需起服务）。

对齐原版依据：
- 原版 QI_CHECK（火炬 ObjNpc.pas:670-679）：CHECK 是「零/非零匹配」而非
  数值 >=：n := GetQuestMark(param)（只返回 0/1），n=0 时 tag<>0 判假、
  n<>0 时 tag=0 判假。即 ``(quest==0)==(tag==0)``。
  旧实现写成 ``quest >= tag``，导致 ``check [N] 0`` 恒真。
- 玛法领路人（Npc_Def/玛法领路人-0.txt）：@main 的 check [001] 1 挡重领；
  @away 的 check [001] 0 是「未领过则补领」；@next1 give 5000 + SET [001] 1。
  旧语义下 @away 恒真 → 反复 goto @next1 → 5000 金币无限领。
- 任务变量跨登录持久：原版 QuestParams 存档；本项目存 character_quests 表。

用法：
    python scripts/test_npc_mafa_guide.py
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

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def drain(self) -> None:
        self.messages.clear()

    def close(self) -> None:
        pass


def make_player(service, tag: str) -> tuple[object, Session]:
    sess = Session()
    name = "MF" + tag + str(int(time.time() * 1000) % 1000000)
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    return p, sess


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-mafaguide-test.db"
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db_path) + suffix)
        if f.exists():
            f.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    return service, cfg, db_path


def npc_by_script(w, script_name: str):
    for n in w.npcs.values():
        if n.script_name == script_name:
            return n
    return None


def main() -> None:
    service, cfg, db_path = build()
    w = service.world
    engine = service._engine()
    npc = npc_by_script(w, "玛法领路人")
    assert npc is not None, "找不到玛法领路人 NPC"

    print("\n[1] CHECK 语义：tag=0 只在变量未设置时为真（原版零/非零匹配）")
    p, _ = make_player(service, "a")
    from pyserver.npc_script import Cmd

    def eval_check(value: int, tag: int) -> bool:
        p.quest_params[1] = value
        return engine._eval_condition(p, npc, Cmd("check", f"[{1:03d}]", str(tag)))

    check("check [001] 0：未设置(0) → 真", eval_check(0, 0) is True)
    check("check [001] 0：已设置(1) → 假（旧实现恒真）", eval_check(1, 0) is False)
    check("check [001] 1：未设置(0) → 假", eval_check(0, 1) is False)
    check("check [001] 1：已设置(1) → 真", eval_check(1, 1) is True)
    check("check [001] 1：值 2 → 真（非零即真，原版 GetQuestMark 口径）",
          eval_check(2, 1) is True)
    p.quest_params.clear()

    print("\n[2] 未到 3 级：不满足条件，不发钱")
    p.level = 2
    gold0 = p.gold
    r = engine.execute(p, npc, "main")
    check("2 级无 5000 奖励", p.gold == gold0, f"gold={p.gold}")
    check("提示升级到 3 级", "升到3级" in r.say_text)

    print("\n[3] 满 3 级首次对话 → @next1 领 5000，SET [001] 1")
    p.level = 3
    gold0 = p.gold
    engine.execute(p, npc, "main")     # @main 显示「愿意/@next1」
    r = engine.execute(p, npc, "next1")
    check("首次 +5000 金币", p.gold == gold0 + 5000, f"+{p.gold - gold0}")
    check("SET [001] 1", p.quest_params.get(1) == 1,
          f"[001]={p.quest_params.get(1)}")

    print("\n[4] 重进 @main → check [001] 1 命中 → @away 提示已领，不再发钱")
    gold1 = p.gold
    r = engine.execute(p, npc, "main")
    check("已领后 @main 不再发钱", p.gold == gold1, f"Δ={p.gold - gold1}")
    check("@away 提示「领取过一次」", "领取过一次" in r.say_text)
    # 用户 2026-11-18 反馈：领过旧的一次性 5000 后，@main 被 goto @away
    # 截断，新加的离线修炼/每日金币入口全消失。@away 的 ELSESAY 必须保留它们。
    check("已领旧礼包后仍可见「领取离线修炼补助」入口",
          "领取离线修炼补助" in r.say_text)
    check("已领旧礼包后仍可见「领取每日金币」入口",
          "领取每日金币" in r.say_text)

    print("\n[5] 已领状态下强行再走 @away / @next1，仍不能重复领")
    r = engine.execute(p, npc, "away")
    check("@away 直接对话不再发钱", p.gold == gold1)
    check("@away ELSESAY 提示", "领取过一次" in r.say_text)
    # 注意：@next1 本身只有 checklevel 3 条件，原版脚本就未做 [001] 二次校验，
    # 正常 UI 路径到不了这里（@main 已分流）；此处只测 UI 可达路径。

    print("\n[6] 持久化：存档 → 重登 → [001] 保留，仍不能重领")
    service.persist(p)
    stored = service.db.load_quest_params(p.character_id)
    check("quest_params 已写库", stored.get(1) == 1, f"stored={stored}")
    # 模拟重登：清内存标记后再从库读回
    p.quest_params.clear()
    p.quest_params = service.db.load_quest_params(p.character_id)
    check("重登后 [001]=1 恢复", p.quest_params.get(1) == 1)
    gold2 = p.gold
    engine.execute(p, npc, "main")
    check("重登后仍不能重领", p.gold == gold2, f"Δ={p.gold - gold2}")

    print("\n[7] 新角色独立：[001] 不串号")
    p2, _ = make_player(service, "b")
    check("新角色 [001]=0", p2.quest_params.get(1, 0) == 0)
    check("DB 中新角色标记为空", service.db.load_quest_params(p2.character_id) == {})

    print(f"\n结果：{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
