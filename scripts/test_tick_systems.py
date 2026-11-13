#!/usr/bin/env python3
"""tick 子系统 / 装备属性 / MapQuest 的离线回归（纯标准库）。

覆盖 Java ``GameWorld.tick`` 里那些「状态被写进去但以前没人消费」的字段：
回血、buff 过期、红绿毒、火墙、远程怪延迟飞矢、守卫/隐身仇恨、宠物 AI 与
升级、玩家受击减伤与死亡流程，以及 ``Player.recompute`` 的装备特殊属性和
MapQuest 任务触发。

用法：
    python scripts/test_tick_systems.py
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.gamedata import MapQuest, parse_map_quest  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, Player, now_ms  # noqa: E402

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
        self.account_id = 0
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [m.get("type", "?") for m in self.messages]

    def drain(self) -> None:
        self.messages.clear()


def build():
    cfg = load()
    db_path = ROOT / "data" / "jootm2-tick-test.db"
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    service = GameService(cfg, db)
    sess = Session()
    tag = str(int(time.time()) % 100000)
    name = "T" + tag
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": 0},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        service.on_message(sess, json.dumps(msg, ensure_ascii=False))
    p = service.find_by_session(sess)
    assert p is not None, "登录链路失败"
    # StartPoint.txt 的坐标可能超出「无 .map 时的 256x256 回退地图」，
    # 钳回地图内，保证火墙/召唤/freeNear 等格子操作有合法目标。
    w = service.world
    gm = p.map or w.default_map
    p.x = min(max(20, p.x), gm.width - 20)
    p.y = min(max(20, p.y), gm.height - 20)
    return w, p, sess


def add_template(w, **kw):
    """基于任一真实模板造一件测试装备并注册进世界。"""
    base = next(iter(w.item_templates.values()))
    idx = 900000 + len(w.item_templates)
    t = replace(base, index=idx, **kw)
    w.item_templates[idx] = t
    return t


def equip(p, t, slot: int, **inst_kw) -> dict:
    it = {"makeIndex": 800000 + slot, "index": t.index,
          "durability": t.duraMax, "durabilityMax": t.duraMax,
          "lucky": 0, "slot": -1}
    it.update(inst_kw)
    p.equipment[slot] = it
    return it


def test_recompute(w, p):
    print("\n[1] Player.recompute 装备属性 / 套装 / 幸运 / buff")
    p.equipment = [None] * 13
    weapon = add_template(w, stdMode=5, shape=23, acMin=2, acMax=3,
                          macMin=1, macMax=12, dcMin=4, dcMax=9,
                          specialPwr=2, aniCount=0)
    equip(p, weapon, 1, lucky=3)
    p.recompute(w)
    check("麻痹武器 Shape=23 生效", p.paralysis_weapon)
    check("武器 StdMode=5 映射准确/幸运", p.combat["hit"] >= 3
          and p.combat["luck"] >= 2, f"hit={p.combat['hit']} luck={p.combat['luck']}")
    check("武器幸运 = specialPwr + 实例 lucky", p.luck == 5, f"luck={p.luck}")

    protect = add_template(w, stdMode=22, shape=133, aniCount=7)
    suck = add_template(w, stdMode=22, shape=136, aniCount=9)
    revive = add_template(w, stdMode=22, shape=114)
    teleport = add_template(w, stdMode=22, shape=112)
    equip(p, protect, 7)
    equip(p, suck, 8)
    equip(p, revive, 9)
    equip(p, teleport, 10)
    p.recompute(w)
    check("护身 Shape=133 累加 aniCount", p.mana_to_health == 7,
          f"mana_to_health={p.mana_to_health}")
    check("吸血 Shape=136 累加 aniCount", p.suck_health_rate == 9)
    check("复活/传送戒指 Shape=114/112", p.revival_ring and p.teleport_ring)
    p.equipment = [None] * 13
    equip(p, weapon, 1, lucky=0)
    p.recompute(w)
    check("recompute 重置特殊属性（不会残留）",
          p.mana_to_health == 0 and not p.revival_ring)

    # 战士三件套 153/154/155：命中 +1，DC 上限 +3
    p.equipment = [None] * 13
    equip(p, weapon, 1)
    p.recompute(w)
    plain_dc, plain_hit = p.combat["dcMax"], p.combat["hit"]
    for i, shape in enumerate((153, 154, 155)):
        equip(p, add_template(w, stdMode=22, shape=shape), 3 + i)
    p.recompute(w)
    check("战士套装 DC 上限 +3", p.combat["dcMax"] == plain_dc + 3,
          f"{plain_dc} -> {p.combat['dcMax']}")
    check("战士套装命中 +1", p.combat["hit"] == plain_hit + 1,
          f"{plain_hit} -> {p.combat['hit']}")

    p.equipment = [None] * 13
    p.recompute(w)
    plain_ac = p.combat["acMax"]
    p.ac_buff_until = now_ms() + 60_000
    p.ac_buff_bonus = 7
    p.mac_buff_bonus = 5
    p.recompute(w)
    check("幽灵盾/神圣战甲 buff 加防御上限",
          p.combat["acMax"] == plain_ac + 7)
    p.ac_buff_until = 0
    p.ac_buff_bonus = 0
    p.mac_buff_bonus = 0
    p.recompute(w)
    p.equipment = [None] * 13
    p.recompute(w)


def test_regen_and_buffs(w, p, sess):
    print("\n[2] 回血 / 治愈术 / buff 过期")
    p.max_hp, p.max_mp = 1000, 1000
    p.hp, p.mp = 980, 970
    p.inc_healing = 20
    p.last_healing_at = 0
    w.last_regen_at = 0
    now = now_ms()
    sess.drain()
    w._regen(p, now)
    check("治愈术每 500ms 回 5", p.inc_healing == 15, f"inc={p.inc_healing}")
    check("自然回复 HP/MP 各 +1", p.hp == 986 and p.mp == 971,
          f"hp={p.hp} mp={p.mp}")
    hp_after = p.hp
    w._regen(p, now)
    check("同帧不重复自然回复", p.hp == hp_after)

    p.ac_buff_until = now - 1
    p.ac_buff_bonus = 9
    p.mac_buff_bonus = 4
    p.shield_until = now - 1
    w._expire_buffs(p, now)
    check("buff 过期后字段清零", p.ac_buff_until == 0
          and p.ac_buff_bonus == 0 and p.shield_until == 0)
    check("buff 过期下发 stats", "stats" in sess.types())


def test_poison(w, p, sess):
    print("\n[3] 红毒持续伤害 / 绿毒到期 / 毒死走死亡流程")
    p.hp, p.max_hp = 100, 100
    p.poison_ticks, p.poison_damage, p.last_poison_at = 2, 7, 0
    p.poison_armor_until = now_ms() - 1
    p.revival_ring = False
    now = now_ms()
    w._apply_poison(p, now)
    check("红毒扣血", p.hp == 93, f"hp={p.hp}")
    check("红毒跳数递减", p.poison_ticks == 1)
    check("绿毒到期清除", p.poison_armor_until == 0)

    p.hp, p.poison_damage = 3, 10
    p.poison_ticks, p.last_poison_at = 1, 0
    p.revival_ring = True
    sess.drain()
    w._apply_poison(p, now + 3000)
    check("毒死触发 kill_player（复活戒指免死）",
          not p.revival_ring and p.hp == p.max_hp // 2, f"hp={p.hp}")
    p.poison_ticks = 0
    p.hp = p.max_hp
    p.revival_ring = False


def test_fire_wall(w, p, sess):
    print("\n[4] 火墙每秒伤害 / 过期 / 击杀")
    base_t = next(iter(w.monster_templates.values()))
    t = replace(base_t, name="火墙测试怪", race=1, dc=1, maxDc=1,
                ac=0, hp=100, level=1, walkSpeed=800, attackSpeed=1000)
    m = Monster(id=w.new_id(), name=t.name, x=p.x, y=p.y, dir=4,
                hp=100, max_hp=100, level=1, race=1, template=t,
                spawn_x=p.x, spawn_y=p.y)
    w.add_actor(m, p.map)
    w.combat_state["fire_walls"] = []
    made = w.make_fire_wall_at(p, p.x, p.y, 5, 10)
    check("十字 5 格生成火墙", made >= 1, f"made={made}")
    sess.drain()
    now = now_ms()
    w._tick_fire_walls(now)
    check("踩火墙的怪物每秒扣血", m.hp == 95, f"hp={m.hp}")
    hits = [x for x in sess.messages if x.get("type") == "attack"
            and x.get("magicId") == 22]
    check("火墙广播 attack(magicId=22)", len(hits) >= 1)
    m.hp = 3
    w._tick_fire_walls(now + 1100)
    check("火墙击杀进入重生队列", m.pending_respawn and not m.alive)
    for f in w.combat_state["fire_walls"]:
        f["expire"] = now - 1
    w._tick_fire_walls(now + 2000)
    check("过期火墙被清理", not w.combat_state["fire_walls"])


def test_ranged(w, p, sess):
    print("\n[5] 远程怪飞矢 / 延迟结算 / 魔法盾减伤")
    base_t = next(iter(w.monster_templates.values()))
    t = replace(base_t, name="远程测试怪", race=82, dc=20, maxDc=20,
                ac=0, hp=100, level=1, walkSpeed=800, attackSpeed=1000)
    m = Monster(id=w.new_id(), name=t.name, x=p.x + 2, y=p.y, dir=4,
                hp=100, max_hp=100, level=1, race=82, template=t,
                spawn_x=p.x + 2, spawn_y=p.y)
    w.add_actor(m, p.map)
    p.combat["acMax"] = 0
    p.max_hp, p.hp = 1000, 1000
    p.shield_until = 0
    p.mana_to_health = 0
    p.poison_armor_until = 0
    w.combat_state["pending_ranged"] = []
    sess.drain()
    now = now_ms()
    w._ranged_attack(m, p, now)
    check("广播 flyAxe 飞矢", "flyAxe" in sess.types())
    check("登记延迟结算队列", len(w.combat_state["pending_ranged"]) == 1)
    w._tick_pending_ranged_hits(now)
    check("未到时间不结算", p.hp == 1000)
    w._tick_pending_ranged_hits(now + 10_000)
    check("到点结算伤害", p.hp < 1000, f"hp={p.hp}")
    check("结算后队列清空", not w.combat_state["pending_ranged"])

    raw = max(1, m.roll_attack())
    p.hp = 1000
    p.shield_until = now + 60_000
    w._apply_hit_to_player(m, p, raw)
    expected = (raw * 7) // 10
    check("魔法盾减伤 30%", p.hp == 1000 - expected,
          f"raw={raw} expected={expected} hp={p.hp}")
    p.shield_until = 0
    p.hp = 1000
    p.mana_to_health = 1
    p.mp, p.max_mp = 30, 30
    w._apply_hit_to_player(m, p, 50)
    check("护身先由 MP 抵消伤害", p.mp == 0 and p.hp == 980,
          f"mp={p.mp} hp={p.hp}")
    p.mana_to_health = 0
    p.mp = p.max_mp

    p.hp = 10
    p.revival_ring = False
    sess.drain()
    home_before = (p.map, p.x, p.y)
    w._apply_hit_to_player(m, p, 999)
    # 原版 SM_NOWDEATH：死亡停在原地等黑白屏，服务端不回城、不满血；
    # 复活靠重登（UsrEngn.pas:576-600 给 HP=14）。用户反馈 25。
    check("玩家被打死：广播 death 且停在原地 HP=0",
          "death" in sess.types() and p.hp == 0 and p.dead
          and (p.map, p.x, p.y) == home_before,
          f"hp={p.hp} dead={p.dead}")
    p.dead = False
    p.hp, p.mp = p.max_hp, p.max_mp


def test_ai_targeting(w, p):
    print("\n[6] 守卫仇恨 / 隐身 / 麻痹定身")
    base_t = next(iter(w.monster_templates.values()))
    guard_t = replace(base_t, name="守卫测试怪", race=20, dc=1, maxDc=1,
                      hp=100, level=1, walkSpeed=800, attackSpeed=1000)
    guard = Monster(id=w.new_id(), name=guard_t.name, x=p.x + 2, y=p.y,
                    hp=100, max_hp=100, level=1, race=20, template=guard_t,
                    spawn_x=p.x + 2, spawn_y=p.y)
    w.add_actor(guard, p.map)
    now = now_ms()
    p.pk_point = 0
    guard.last_hiter_id = -1
    p.hide_until = 0
    check("守卫不打白名玩家", w._resolve_target(guard, now) is None)
    guard.last_hiter_id = p.id
    check("守卫反击打过自己的玩家", w._resolve_target(guard, now) is p)
    guard.last_hiter_id = -1
    p.pk_point = 1
    check("守卫打红名玩家", w._resolve_target(guard, now) is p)
    p.pk_point = 0
    p.hide_until = now + 60_000
    check("隐身中不成为仇恨目标", w._resolve_target(guard, now) is None)
    p.hide_until = 0

    normal_t = replace(base_t, name="普怪测试", race=1, dc=1, maxDc=1,
                       hp=100, level=1, walkSpeed=800, attackSpeed=1000)
    normal = Monster(id=w.new_id(), name=normal_t.name, x=p.x + 2, y=p.y,
                     hp=100, max_hp=100, level=1, race=1, template=normal_t,
                     spawn_x=p.x + 2, spawn_y=p.y)
    w.add_actor(normal, p.map)
    check("普通怪按最近玩家选目标", w._resolve_target(normal, now) is p)
    ox, oy = normal.x, normal.y
    normal.paralyzed_until = now + 60_000
    w._monster_ai(normal, now + 1)
    check("麻痹期间不移动", (normal.x, normal.y) == (ox, oy))
    normal.paralyzed_until = 0

    # 家畜：鸡(race 51)/鹿(52) 不锁玩家（M2Share.pas ANIMAL_CHICKEN/ANIMAL_DEER）
    chicken_t = replace(base_t, name="鸡测试", race=51, dc=1, maxDc=1,
                        hp=5, level=1, walkSpeed=1400, attackSpeed=3000)
    chicken = Monster(id=w.new_id(), name=chicken_t.name, x=p.x + 1, y=p.y,
                      hp=5, max_hp=5, level=1, race=51, template=chicken_t,
                      spawn_x=p.x + 1, spawn_y=p.y)
    w.add_actor(chicken, p.map)
    p.hide_until = 0
    p.pk_point = 0
    check("鸡(race 51)不主动锁定玩家", w._resolve_target(chicken, now) is None)
    deer_t = replace(chicken_t, name="鹿测试", race=52, dc=2, maxDc=4, hp=25)
    deer = Monster(id=w.new_id(), name=deer_t.name, x=p.x + 1, y=p.y + 1,
                   hp=25, max_hp=25, level=12, race=52, template=deer_t,
                   spawn_x=p.x + 1, spawn_y=p.y + 1)
    w.add_actor(deer, p.map)
    check("鹿(race 52)不主动锁定玩家", w._resolve_target(deer, now) is None)
    # 中立 ≠ 打不还手：原版 TMonster.Run 没有 SearchTarget，但 TAnimalObject.Struck
    # 会 SetTargetCreat(hiter)，所以被打之后要会追着还击。
    chicken.target_id = p.id
    check("鸡被打后会还击（Struck → SetTargetCreat）", w._resolve_target(chicken, now) is p)
    chicken.target_id = -1
    deer.target_id = p.id
    deer.x = p.x + 9
    check("中立怪目标跑出仇恨范围后放弃", w._resolve_target(deer, now) is None)
    deer.x = p.x + 1
    deer.target_id = -1
    from pyserver.handlers_combat import CombatHandlers
    CombatHandlers._hate(chicken, p)
    check("命中记仇同时写入 target_id 与 last_hiter_id",
          chicken.target_id == p.id and chicken.last_hiter_id == p.id)
    chicken.target_id = -1
    chicken.last_hiter_id = -1
    wolf_t = replace(chicken_t, name="狼测试", race=53, dc=6, maxDc=8, hp=48)
    wolf = Monster(id=w.new_id(), name=wolf_t.name, x=p.x + 2, y=p.y + 1,
                   hp=48, max_hp=48, level=16, race=53, template=wolf_t,
                   spawn_x=p.x + 2, spawn_y=p.y + 1)
    w.add_actor(wolf, p.map)
    check("狼(race 53)仍会锁定玩家", w._resolve_target(wolf, now) is p)
    for mon in (chicken, deer, wolf):
        w.remove_actor(mon.id) if hasattr(w, "remove_actor") else w.actors.pop(mon.id, None)
        if mon.map is not None:
            mon.map.remove(mon.id)

    # 伤害门槛：DC 掷点不破防时按原版直接 Exit（不再 max(1)）
    weak_t = replace(base_t, name="弱怪", race=1, dc=0, maxDc=0, hp=10)
    weak = Monster(id=w.new_id(), name=weak_t.name, x=p.x + 1, y=p.y,
                   hp=10, max_hp=10, level=1, race=1, template=weak_t,
                   spawn_x=p.x + 1, spawn_y=p.y)
    w.add_actor(weak, p.map)
    p.combat["acMax"] = 5
    check("DC 0 怪对 AC5 玩家伤害 <= 0", w._ranged_damage(weak, p) <= 0,
          f"dmg={w._ranged_damage(weak, p)}")
    w.actors.pop(weak.id, None)
    if weak.map is not None:
        weak.map.remove(weak.id)
    p.combat["acMax"] = 0


def test_summon(w, p, sess):
    print("\n[7] 宠物 AI / 升级")
    # 上一步的死亡流程会把玩家换到 homeMap，先放回合地图中央再测召唤。
    p.map = w.default_map
    p.x, p.y = 120, 120
    w.default_map.add(p)
    name = next((n for n in ("神兽", "变异骷髅") if n in w.monster_templates),
                next(iter(w.monster_templates)))
    pet = w.spawn_summon(p, name)
    check("spawn_summon 生成宠物并注册 pets", pet is not None
          and pet.id in w.combat_state.get("pets", {}))
    if pet is None:
        return
    p.summon_id = pet.id
    pet.slave_make_level = 3
    pet.slave_exp = 10000
    pet.slave_exp_level = 0
    before_hp = pet.max_hp
    w._gain_slave_exp(pet, pet)
    check("宠物获得经验后升级", pet.slave_exp_level == 1,
          f"lv={pet.slave_exp_level}")
    check("宠物升级提升 MaxHP", pet.max_hp > before_hp,
          f"{before_hp} -> {pet.max_hp}")
    check("宠物升级提升 DC 加成", pet.slave_dc_bonus > 0)
    check("宠物 brief_json 带 nameColor",
          "nameColor" in pet.brief_json())
    pet.race = 113
    pet.transformed = False
    pet.change_face_at = now_ms()
    sess.drain()
    w._summon_ai(pet, now_ms())
    check("summon_ai 执行不抛异常", True)
    w.remove_summon(pet)
    check("remove_summon 清空 owner.summon_id", p.summon_id == -1)


def test_map_quest(w, p, sess):
    print("\n[8] MapQuest 解析 / 加载 / 触发")
    quests = parse_map_quest(w.config.envir_dir / "MapQuest.txt")
    check("MapQuest.txt 解析出任务行", len(quests) > 0, f"{len(quests)} 条")
    check("任务旗标号与脚本名有效",
          all(q.set_number >= 0 and q.qfile for q in quests))
    check("世界已加载任务脚本", len(w.quest_scripts) > 0
          and any(s is not None for s in w.quest_scripts.values()),
          f"{len(w.quest_scripts)} 个脚本")
    qfile = next((k for k, v in w.quest_scripts.items() if v is not None), None)
    base_t = next(iter(w.monster_templates.values()))
    m = Monster(id=w.new_id(), name="任务怪", x=p.x, y=p.y,
                hp=10, max_hp=10, level=1, race=1,
                template=replace(base_t, name="任务怪"),
                spawn_x=p.x, spawn_y=p.y)
    w.add_actor(m, p.map)
    saved_quests = w.map_quests
    q = MapQuest(map_id=p.map.name, set_number=505, value=1,
                 mon_name="任务怪", item_name="", qfile=qfile or "",
                 enable_group=False)
    w.map_quests = [q]
    p.quest_params[505] = 0
    sess.drain()
    w.trigger_map_quest(p, m)
    check("旗标不满足时不触发", "systemMessage" not in sess.types()
          or all("任务" not in str(x.get("text", "")) for x in sess.messages))
    p.quest_params[505] = 1
    if qfile:
        w.trigger_map_quest(p, m)
        check("旗标满足时执行任务脚本不抛异常", True)
    w.map_quests = saved_quests
    p.quest_params.pop(505, None)


def test_hp_sync(w, p, sess):
    """旁观者血条：玩家扣血后要广播 entityHp，且按 HP_SYNC_MS 合批、无变化不重发。"""
    print("\n[9] 旁观者血条同步（entityHp）")
    gm = p.map or w.default_map
    other_sess = Session()
    other = Player(id=990001, name="Observer", hp=50, max_hp=50, session=other_sess)
    other.x, other.y = p.x + 2, p.y + 2
    other.map = gm
    gm.add(other)
    w.players_by_name[other.name] = other
    old_hp = p.hp
    try:
        w._hp_sync_at = 0
        w._hp_sync_cache.clear()
        other_sess.drain()

        p.hp = max(1, p.hp - 7)
        w._sync_player_hp(now_ms() + 10_000)
        got = [m for m in other_sess.messages
               if m.get("type") == "entityHp" and m.get("id") == p.id]
        check("扣血后向周围玩家广播 entityHp", bool(got) and got[0]["hp"] == p.hp,
              f"hp={p.hp} got={got[:1]}")
        check("entityHp 只带血量（不泄露其它属性）",
              bool(got) and set(got[0]) == {"type", "id", "hp", "maxHp"},
              f"{sorted(got[0]) if got else '-'}")

        other_sess.drain()
        w._hp_sync_at = 0
        w._sync_player_hp(now_ms() + 20_000)
        dup = [m for m in other_sess.messages if m.get("type") == "entityHp"]
        check("血量未变不重复广播", not dup, f"{dup[:1]}")

        p.hp = max(1, p.hp - 1)
        other_sess.drain()
        w._sync_player_hp(w._hp_sync_at + 100)     # 距上次 100ms < HP_SYNC_MS
        check("HP_SYNC_MS 内合批（本次不广播）",
              not [m for m in other_sess.messages if m.get("type") == "entityHp"])
    finally:
        p.hp = old_hp
        gm.remove(other.id)
        w.players_by_name.pop(other.name, None)
        w._hp_sync_at = 0
        w._hp_sync_cache.clear()


def test_drop_rates(w, p):
    """掉落倍率表：集中调爆率，不改 MonItems 单条概率。"""
    print("\n[10] 掉落倍率表")
    from types import SimpleNamespace

    from pyserver.drop_rates import DropRates

    check("默认倍率等价原版", DropRates().probability("鸡", "金币") == 1.0)
    r = DropRates(gold=2.0, item=0.5,
                  monsters={"鹿": {"gold": 3.0, "item": 2.0, "goldAmount": 2.5}},
                  items={"战神油": 0.25})
    check("按怪物覆盖与全局相乘", abs(r.probability("鹿", "金币") - 6.0) < 1e-9,
          str(r.probability("鹿", "金币")))
    check("按物品覆盖与类别相乘", abs(r.probability("鹿", "战神油") - 0.25) < 1e-9)
    check("数量倍率按怪物覆盖", r.amount("鹿") == 2.5 and r.amount("鸡") == 1.0)
    check("倍率 0 永不命中", not DropRates(item=0).hit("鸡", "鸡肉", 1, 10))
    check("倍率极大时封顶必中", DropRates(item=1e6).hit("鸡", "鸡肉", 1, 1000))

    gm = p.map or w.default_map
    t = SimpleNamespace(name="鸡", race=51, appr=160)
    m = Monster(id=w.new_id(), name="鸡", x=p.x + 1, y=p.y, hp=5, max_hp=5,
                level=5, race=51, template=t, spawn_x=p.x + 1, spawn_y=p.y)
    w.add_actor(m, gm)
    old = w.drop_rates
    added = []
    try:
        w.drop_rates = DropRates(item=0, gold=0)
        base = set(w.ground_items)
        w.drop_loot(m)                       # 鸡.txt 有 `1/1 鸡肉`
        check("倍率 0 时不产生任何掉落", set(w.ground_items) == base)
        w.drop_rates = DropRates()
        before = set(w.ground_items)
        w.drop_loot(m)
        added = list(set(w.ground_items) - before)
        check("默认倍率下 1/1 条目必掉", bool(added))
    finally:
        w.drop_rates = old
        for gid in added:
            w.ground_items.pop(gid, None)
        if hasattr(w, "remove_actor"):
            w.remove_actor(m.id)
        else:
            w.actors.pop(m.id, None)
        if m.map is not None:
            m.map.remove(m.id)


def test_spider_stick_gold(w, p):
    """金币图标分档 / 吐毒蜘蛛中毒 / 食人花 stick AI 与挖肉产出。"""
    print("\n[11.5] 金币图标 / 蜘蛛中毒 / 食人花")
    from types import SimpleNamespace

    from pyserver.world_extra import gold_shape

    # 1) GetGoldShape 五档（M2Share.pas:3568）
    check("金币 <30 → 112", gold_shape(1) == 112 and gold_shape(29) == 112)
    check("金币 ≥30 → 113", gold_shape(30) == 113)
    check("金币 ≥70 → 114", gold_shape(70) == 114)
    check("金币 ≥300 → 115", gold_shape(300) == 115)
    check("金币 ≥1000 → 116", gold_shape(1000) == 116)

    gm = p.map or w.default_map
    import random as _r
    # 2) 吐毒蜘蛛 race 82 命中可中毒（antiPoison=0 时概率 1/20）
    spider = Monster(id=w.new_id(), name="毒蜘蛛", x=p.x + 2, y=p.y,
                     hp=50, max_hp=50, level=16, race=82,
                     template=SimpleNamespace(name="毒蜘蛛", race=82, appr=163,
                                              dc=2, maxDc=9, acMax=9, walkSpeed=800,
                                              attackSpeed=1000),
                     spawn_x=p.x + 2, spawn_y=p.y)
    w.add_actor(spider, gm)
    p.combat["antiPoison"] = 0
    p.hp = p.max_hp = 5000
    p.poison_ticks = 0
    try:
        _r.seed(7)
        hit = False
        for _ in range(500):
            p.poison_ticks = 0
            w._apply_hit_to_player(spider, p, 5)
            if p.poison_ticks > 0:
                hit = True
                break
        check("race 82 命中会挂 POISON_DECHEALTH", hit,
              f"ticks={p.poison_ticks} damage={p.poison_damage}")
        check("蜘蛛毒每跳 1 点（point=1）", p.poison_damage == 1)
        check("蜘蛛毒时长 30s → 12 跳", p.poison_ticks == 12)
        # 118 天狼蜘蛛不毒：把 race 换掉再打
        spider.template.race = 118
        poisoned = False
        for _ in range(500):
            p.poison_ticks = 0
            w._apply_hit_to_player(spider, p, 5)
            if p.poison_ticks > 0:
                poisoned = True
                break
        check("race 118 天狼蜘蛛不毒（m_boUsePoison=False）", not poisoned)
    finally:
        p.poison_ticks = 0
        w.actors.pop(spider.id, None)
        if spider.map is not None:
            spider.map.remove(spider.id)

    # 3) 食人花：出生潜地 → 玩家靠近出土 → 离开潜回 → 永不移动
    flower = Monster(id=w.new_id(), name="食人花", x=p.x + 5, y=p.y,
                     hp=84, max_hp=84, level=20, race=85,
                     template=SimpleNamespace(name="食人花", race=85, appr=10,
                                              dc=2, maxDc=6, acMax=9, walkSpeed=800,
                                              attackSpeed=1000),
                     spawn_x=p.x + 5, spawn_y=p.y)
    flower.hidden = True
    w.add_actor(flower, gm)
    base_pos = (flower.x, flower.y)
    try:
        w._monster_ai(flower, now_ms())
        check("食人花玩家距离 5 → 保持潜地", flower.hidden)
        # 玩家挪到距离 2 → 出土
        p.x, p.y = base_pos[0] - 2, base_pos[1]
        w._monster_ai(flower, now_ms())
        check("食人花玩家距离 <4 → 出土", not flower.hidden)
        # 玩家站相邻格 → 会攻击（不移动）
        p.x, p.y = base_pos[0] - 1, base_pos[1]
        p.hp = p.max_hp = 5000
        w._monster_ai(flower, now_ms())
        check("食人花只站原地攻击，位置不变", (flower.x, flower.y) == base_pos,
              f"pos={(flower.x, flower.y)}")
        # 玩家跑远 → 潜回
        p.x, p.y = base_pos[0] - 9, base_pos[1]
        w._monster_ai(flower, now_ms())
        check("食人花玩家距离 >4 → 潜回", flower.hidden)
        check("潜地怪不进视野快照",
              all(a.id != flower.id for a in w._sight_actors(gm, p.x, p.y)))
    finally:
        w.actors.pop(flower.id, None)
        if flower.map is not None:
            flower.map.remove(flower.id)

    # 4) butchable：原版 m_boAnimal 白名单 = 51/52/53/82/84/85/90
    check("食人花(85) 可挖尸",
          w.butchable(SimpleNamespace(template=SimpleNamespace(race=85, appr=10))))
    check("毒蜘蛛(82) 可挖尸",
          w.butchable(SimpleNamespace(template=SimpleNamespace(race=82, appr=163))))
    check("蝎子(84) 可挖尸（TScorpion m_boAnimal=True）",
          w.butchable(SimpleNamespace(template=SimpleNamespace(race=84, appr=83))))
    check("洞蛆(90) 可挖尸（TGasAttackMonster m_boAnimal=True）",
          w.butchable(SimpleNamespace(template=SimpleNamespace(race=90, appr=24))))
    check("鸡(51)/鹿(52)/羊(52)/狼(53) 可挖尸",
          all(w.butchable(SimpleNamespace(template=SimpleNamespace(race=r, appr=a)))
              for r, a in ((51, 160), (52, 161), (52, 43), (53, 70))))
    check("护卫(52/appr71) 不在动物 appr 白名单",
          not w.butchable(SimpleNamespace(template=SimpleNamespace(race=52, appr=71))))
    check("天狼蜘蛛(118) 不可挖",
          not w.butchable(SimpleNamespace(template=SimpleNamespace(race=118, appr=119))))
    check("花吻蜘蛛(119) 不可挖",
          not w.butchable(SimpleNamespace(template=SimpleNamespace(race=119, appr=117))))
    check("黑锷蜘蛛(81) 不可挖",
          not w.butchable(SimpleNamespace(template=SimpleNamespace(race=81, appr=118))))
    # race ∈ [50,80) 挖空变骷髅；race ≥ 80 只清背包，尸体原样
    check("鸡/鹿/狼挖空变骷髅",
          all(w.butch_skeleton(SimpleNamespace(template=SimpleNamespace(race=r, appr=161)))
              for r in (51, 52, 53)))
    check("蜘蛛/蝎子/食人花/洞蛆挖空不变骷髅",
          all(not w.butch_skeleton(SimpleNamespace(template=SimpleNamespace(race=r, appr=1)))
              for r in (82, 84, 85, 90)))
    check("磨损初值：鸡 50 / 鹿 150 / 狼 150 / 蜘蛛 50",
          [w.base_leathery(SimpleNamespace(template=SimpleNamespace(race=r, appr=161)))
           for r in (51, 52, 53, 82)] == [50, 150, 150, 50])
    check("MonItems 文件名尾随空格（洞蛆1 .txt）也能匹配",
          "洞蛆1" in w.drop_tables and "蜈蚣1" in w.drop_tables,
          str([k for k in w.drop_tables if k.endswith(" ")]))

    # 5) 挖肉产出走 MonItems：食人花叶 1/1 必得
    flower2 = Monster(id=w.new_id(), name="食人花", x=p.x + 3, y=p.y,
                      hp=84, max_hp=84, level=20, race=85,
                      template=SimpleNamespace(name="食人花", race=85, appr=10),
                      spawn_x=p.x + 3, spawn_y=p.y)
    try:
        loot = w.roll_butch_loot(flower2, p)
        names = [it.get("name") for it in loot]
        check("挖食人花必得食人树叶（MonItems 1/1）", "食人树叶" in names, str(names))
        check("挖食人花产出仅树叶/果实（不散落地上）",
              all(n in ("食人树叶", "食人花果实") for n in names), str(names))
    finally:
        w.actors.pop(flower2.id, None)

    # 6) TChickenDeer：鹿/羊 1/30 变种（ObjMon.pas:531，UsrEngn.pas:1844）
    from pyserver.util import init_meat_quality
    rolls = 0
    qualities = []
    for _ in range(300):
        mm = Monster(id=w.new_id(), name="羊", race=52,
                     template=SimpleNamespace(name="羊", race=52, appr=43))
        init_meat_quality(mm)
        if mm.chicken_deer:
            rolls += 1
            qualities.append(mm.meat_quality)
    check("羊(52) 1/30 概率是 TChickenDeer 变种", 2 <= rolls <= 30, f"{rolls}/300")
    check("TChickenDeer 肉品质 10000~30000（比普通 8000~16000 高一档）",
          all(10000 <= q < 30000 for q in qualities), str(qualities[:5]))

    deer = Monster(id=w.new_id(), name="羊", x=p.x + 2, y=p.y,
                   hp=20, max_hp=20, level=1, race=52,
                   template=SimpleNamespace(name="羊", race=52, appr=43,
                                            walkSpeed=800, attackSpeed=1000),
                   spawn_x=p.x + 2, spawn_y=p.y)
    deer.chicken_deer = True
    w.add_actor(deer, gm)
    try:
        p.hp = p.max_hp
        d0 = abs(deer.x - p.x) + abs(deer.y - p.y)
        deer.next_move_at = 0
        w._monster_ai(deer, now_ms())
        d1 = abs(deer.x - p.x) + abs(deer.y - p.y)
        check("TChickenDeer 见人就跑（距离不缩小）", d1 >= d0, f"{d0} -> {d1}")
        check("TChickenDeer 永不攻击玩家", p.hp == p.max_hp, f"hp={p.hp}")
        check("TChickenDeer 记录逃跑目标", deer.target_id == p.id)
    finally:
        w.actors.pop(deer.id, None)
        if deer.map is not None:
            deer.map.remove(deer.id)

    # 7) 「同模型厚血怪」的真实机制：Monster.csv 的数字后缀模板 + 独立 MonItems
    #    （原版 MonInitialize 按名字精确查表，没有运行时加血/变异）
    wolf, wolf1 = w.monster_templates.get("狼"), w.monster_templates.get("狼1")
    check("狼1 与狼同外观但血量更厚",
          bool(wolf and wolf1) and wolf.appr == wolf1.appr and wolf1.hp > wolf.hp * 2,
          f"狼 {getattr(wolf, 'hp', '?')} / 狼1 {getattr(wolf1, 'hp', '?')}")
    loot0 = {e.item_name for e in w.drop_tables.get("狼", [])}
    loot1 = {e.item_name for e in w.drop_tables.get("狼1", [])}
    check("狼1 掉落表明显优于狼（传送戒指/战神油只在高血版）",
          {"传送戒指", "战神油"} <= loot1 and not ({"传送戒指", "战神油"} & loot0),
          f"狼={sorted(loot0)} 狼1={sorted(loot1)}")


def test_full_tick(w, p):
    print("\n[11] 完整 tick 与各子系统串联")
    try:
        w.tick(now_ms())
        ok = True
        detail = ""
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    check("world.tick 全链路无异常", ok, detail)
    check("火墙状态键存在", "fire_walls" in w.combat_state)
    check("昼夜亮度已初始化", 0 <= w.day_bright <= 3)


def test_corpse_lifecycle(w, p) -> None:
    """尸体 3 分钟保留 / 重生换新 actor / 到期广播移除（原版 ObjBase.pas:3769）。"""
    from pyserver.util import now_ms as _now
    from pyserver.world_extra import BODY_LEATHERY, CORPSE_MS

    tpl = next((t for t in w.monster_templates.values()
                if int(t.race) not in (51, 52, 53)), None)
    if tpl is None:
        check("尸体生命周期：有非动物模板可用", False)
        return
    m = Monster(id=w.new_id(), name=tpl.name, x=p.x + 3, y=p.y, dir=4,
                hp=tpl.hp, max_hp=tpl.hp, level=tpl.level, race=tpl.race,
                template=tpl, spawn_x=p.x + 3, spawn_y=p.y, spawn_map=p.map,
                respawn_interval_ms=15_000)
    w.add_actor(m, p.map)
    sent: list[dict] = []
    orig = w.broadcast_around
    w.broadcast_around = lambda mp, o, x, y, r=12: sent.append(o)
    try:
        t0 = _now()
        w.kill_monster(m, t0)
        check("非动物怪死亡也留尸体（dwMakeGhostTime 3 分钟）",
              m.corpse_until == t0 + CORPSE_MS, f"{m.corpse_until - t0}ms")
        check("death 消息带 corpse=True（客户端据此隐藏血条/名字）",
              any(o.get("type") == "death" and o.get("corpse") for o in sent))
        check("死亡时 body_leathery 重置为初值 50", m.body_leathery == BODY_LEATHERY)

        sent.clear()
        w._respawn_dead(t0 + 15_000)
        fresh = [a for a in w.actors.values()
                 if isinstance(a, Monster) and a.alive and a.id != m.id
                 and a.spawn_x == m.spawn_x and a.spawn_y == m.spawn_y]
        check("重生新建 actor（新 id），不复用尸体 id",
              bool(fresh) and fresh[0].id != m.id)
        check("旧尸体仍在 actors 里（等 makeGhost 时间）", m.id in w.actors)
        check("重生广播 entityAdd", any(o.get("type") == "entityAdd" for o in sent))

        sent.clear()
        w._expire_corpses(t0 + CORPSE_MS + 1)
        check("到点尸体被移除", m.id not in w.actors)
        check("移除广播 entityRemove",
              any(o.get("type") == "entityRemove" and o.get("id") == m.id for o in sent))
    finally:
        w.broadcast_around = orig


def test_exp_semantics(w, p) -> None:
    """经验语义：Exp 是**本级内**经验，升级扣 MaxExp（原版 ObjBase.pas:1844 GetExp）。

    旧实现把 exp 当累计总经验存，客户端拿它和本级的 MaxExp 比 →
    「当前经验 1183 / 升级经验 400」、EXP 条恒满。
    """
    from pyserver.world import exp_for_level, normalize_exp

    print("\n[12] 经验语义 / EXP 条数据源")
    # 1) 旧存档（累计）换算成本级内经验
    lv, ex = normalize_exp(4, 1183)
    check("累计 1183（旧存档）→ 等级 5 / 本级内 183", (lv, ex) == (5, 183))
    check("已是本级内经验时原样返回", normalize_exp(5, 183) == (5, 183))

    # 2) 加经验：本级内累加，未超阈值不升级
    p.level, p.exp = 1, 0
    w.add_exp(p, 30)
    check("加 30 经验：等级 1 / exp 30", (p.level, p.exp) == (1, 30))
    check("max_exp 跟随等级（1 级 = 100）", p.max_exp == 100)

    # 3) 跨阈值：扣掉 MaxExp 并升一级（一次最多升一级）
    w.add_exp(p, 80)
    check("加到 110：升级到 2 级 / 剩 10", (p.level, p.exp) == (2, 10))
    check("升级后 max_exp 变成 2 级的 200", p.max_exp == 200)
    check("本级内经验恒小于 max_exp", p.exp < p.max_exp)

    # 4) 一次给超多经验也只升一级（原版 Dec 只执行一次）
    w.add_exp(p, 10_000)
    check("一次给 10000 经验只升一级", p.level == 3)
    check("溢出的经验保留在本级内", p.exp == 10000 + 10 - 200)

    # 5) stats 必须带上 maxExp / weight / maxWeight
    sent = []
    orig = p.session
    p.session = type("S", (), {"send_json": staticmethod(lambda o: sent.append(o))})()
    try:
        w.send_stats(p)
    finally:
        p.session = orig
    st = sent[-1] if sent else {}
    check("stats 带 maxExp", st.get("maxExp") == p.max_exp)
    check("stats 带 weight / maxWeight",
          "weight" in st and st.get("maxWeight") == p.max_weight)
    check("weight = 背包重量", st.get("weight") == p.bag_weight(w))
    check("exp_for_level(101) 不越界", exp_for_level(101) > 0)

def test_drop_scatter_and_stack(w, p) -> None:
    """第 13 轮 26：原版 GetDropPosition 确定性铺格 + AddToMap 格上限。"""
    from pyserver.util import U_RIGHTHAND, now_ms
    from pyserver.world_extra import GroundItem

    class FakeMap:
        """只保留 scatter_pos / broadcast_around 需要的接口（players 索引）。"""
        def __init__(self):
            self.actors = {}
            self.players = {}
        def can_walk(self, x, y):
            return 0 <= x < 40 and 0 <= y < 40

    fm = FakeMap()
    saved = w.ground_items
    w.ground_items = {}
    cx, cy = 20, 20
    seq = []
    try:
        for _ in range(30):
            pos = w.scatter_pos(fm, cx, cy, 3)
            if pos is None:
                break
            gi = GroundItem(w.new_id(), {"index": 0}, 0, pos[0], pos[1], now_ms(), fm)
            w.add_ground_item(gi)
            seq.append(pos)
        # ObjBase.pas:1543-1550：i=1 时 ii=-1、III=-1 是第一个被检查的格
        check("GetDropPosition 第一格 = 左上 (-1,-1)",
              seq and seq[0] == (cx - 1, cy - 1), str(seq[:3]))
        check("完全确定性、无随机（重复调用同状态结果一致）",
              len(seq) > 1 and w.scatter_pos(fm, cx, cy, 3) == w.scatter_pos(fm, cx, cy, 3))
        check("30 件物品铺满 30 个不同格（俗称「爆了」）",
              len(set(seq)) >= 25, f"unique={len(set(seq))}")
        check("优先铺 3x3，第 10 件才扩到 5x5 外环",
              seq[9] == (cx - 2, cy - 2), str(seq[9] if len(seq) > 9 else None))
        check("不会散到半径 3 之外",
              all(max(abs(x - cx), abs(y - cy)) <= 3 for x, y in seq))

        # Envir.pas:230：同格非金币物品最多 5 件
        w.ground_items = {}
        made = sum(1 for _ in range(7)
                   if w.add_ground_item(GroundItem(w.new_id(), {"index": 0}, 0,
                                                  5, 5, now_ms(), fm)))
        check("同格非金币物品最多 5 件", made == 5, f"made={made}")

        # Envir.pas:205-225：同格金币 <=2000 合并成一堆
        w.ground_items = {}
        g1 = GroundItem(w.new_id(), None, 100, 6, 6, now_ms(), fm)
        g2 = GroundItem(w.new_id(), None, 200, 6, 6, now_ms(), fm)
        w.add_ground_item(g1)
        w.add_ground_item(g2)
        golds = [g for g in w.ground_items.values() if g.is_gold()]
        check("同格金币合并为一堆且数量相加",
              len(golds) == 1 and golds[0].gold == 300,
              str([(g.id, g.gold) for g in golds]))
    finally:
        w.ground_items = saved

    # 第 13 轮多光源：U_RIGHTHAND（蜡烛/火把，StdMode 30）→ m_nLight=3
    lamp = next((t for t in w.item_templates.values() if t.stdMode == 30), None)
    old = p.equipment[U_RIGHTHAND]
    try:
        check("无火把时基础微光 = 1（Hibyte(cdir)）", p.light_level() == 1)
        if lamp is not None:
            p.equipment[U_RIGHTHAND] = {"index": lamp.index, "durability": 100}
            check("装备火把（StdMode 30）→ m_nLight = 3", p.light_level() == 3)
            p.equipment[U_RIGHTHAND] = {"index": lamp.index, "durability": 0}
            check("火把耐久耗尽 → 退回 1", p.light_level() == 1)
    finally:
        p.equipment[U_RIGHTHAND] = old


def main() -> int:
    print("=" * 64)
    print("jootm2 tick 子系统 / 装备属性 / MapQuest 回归")
    print("=" * 64)
    w, p, sess = build()
    test_recompute(w, p)
    test_regen_and_buffs(w, p, sess)
    test_poison(w, p, sess)
    test_fire_wall(w, p, sess)
    test_ranged(w, p, sess)
    test_ai_targeting(w, p)
    test_summon(w, p, sess)
    test_map_quest(w, p, sess)
    test_hp_sync(w, p, sess)
    test_drop_rates(w, p)
    test_drop_scatter_and_stack(w, p)
    test_spider_stick_gold(w, p)
    test_corpse_lifecycle(w, p)
    test_exp_semantics(w, p)
    test_full_tick(w, p)
    print("\n" + "=" * 64)
    print(f"结果: {PASS} PASS / {FAIL} FAIL")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
