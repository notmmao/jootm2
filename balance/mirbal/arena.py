"""引擎 A：单挑竞技场。

真实 GameService + 真实 AutoPilot（与在线托管/离线修炼/机器人同一内核）在虚拟时钟下
打一只指定怪；伤害、命中、技能、喝药、风筝、磨损、技能修炼全部走服务端原逻辑。
只排除「找怪/走路/抢怪」——那部分由引擎 B（worldsim）测量。
"""
from __future__ import annotations

import contextlib
import io
import random
import statistics
from pathlib import Path

from . import boot, clock
from .gear import GearPicker

FIGHT_TIMEOUT_S = 150
_ctx: dict = {}
SERVER_ERRORS: dict = {}


def _note_error(e, where):
    import traceback
    tb = traceback.extract_tb(e.__traceback__)
    loc = next((f"{Path(fr.filename).name}:{fr.lineno} {fr.name}" for fr in reversed(tb)
                if "pyserver" in fr.filename), "?")
    key = f"{type(e).__name__}: {e} @ {loc}"
    d = SERVER_ERRORS.setdefault(key, {"count": 0, "where": set()})
    d["count"] += 1
    d["where"].add(where)


def _setup(tree: str | None):
    from pyserver import autopilot as ap
    from pyserver.world import Monster

    if _ctx.get("tree") == tree and "srv" in _ctx:
        return _ctx
    cfg, srv = boot.make_service(tree)
    w = srv.world
    # 竞技场托管档：不挑怪、不撤退、不捡不挖（掉落用解析期望值计）
    ap.PRESETS["arena"] = dict(ap.PRESETS["balanced"], name="arena", level_delta=999,
                               max_target_dc=10 ** 6, retreat_hp=0.0, butch=False,
                               thrust=True, sight=14, loot_sight=0,
                               pickup_min_price=10 ** 9)
    ap.PRESET_ALIASES["arena"] = "arena"
    for a in list(w.actors.values()):
        if isinstance(a, Monster):
            a.map.remove(a.id)
            w.actors.pop(a.id, None)
    from .data import DataSet
    ds = DataSet(srv, tree or "main")   # 必须在清空 MonGen 之前建（掉落来源依赖刷怪表）
    w.mon_gen_entries = []            # 竞技场不需要真实刷怪
    _ctx.clear()
    _ctx.update(tree=tree, srv=srv, w=w, ds=ds, gear=GearPicker(ds), players={})
    return _ctx


def _open_spot(gm, avoid, r=6):
    rnd = random.Random(7)
    for _ in range(200000):
        x = rnd.randrange(r, gm.width - r)
        y = rnd.randrange(r, gm.height - r)
        if abs(x - avoid[0]) + abs(y - avoid[1]) < 80:
            continue
        if all(gm.can_walk(x + dx, y + dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)):
            return x, y
    raise RuntimeError("找不到空地")


def _player(ctx, job):
    pl = ctx["players"].get(job)
    if pl is None:
        p = boot.new_player(ctx["srv"], job)
        spot = _open_spot(p.map, (p.x, p.y))
        pl = ctx["players"][job] = (p, spot)
    return pl


def _set_skills(w, p, lv_policy="trained"):
    p.skills.clear()
    for mid, t in w.magic_templates.items():
        if t.job != p.job or t.needLevel[0] <= 0 or t.needLevel[0] > p.level:
            continue
        lv = sum(1 for n in t.needLevel if 0 < n <= p.level) if lv_policy == "trained" else 0
        p.skills[mid] = {"level": min(3, lv), "train": 0, "key": 0}


POTIONS_HP = ("金创药(小量)", "金创药(中量)", "强效金创药", "超强金创药")
POTIONS_MP = ("魔法药(小量)", "魔法药(中量)", "强效魔法药", "超强魔法药")


def pick_potions(ds, level):
    """按等级选商店里买得到的药（等级越高用越大瓶）。"""
    def pick(names):
        avail = [n for n in names if n in ds.shop_price]
        if not avail:
            return None
        idx = 0 if level < 12 else 1 if level < 22 else 2 if level < 35 else 3
        return avail[min(idx, len(avail) - 1)]
    return pick(POTIONS_HP), pick(POTIONS_MP)


def _refill(w, p, ds, hp_name, mp_name, amulet):
    from pyserver.world import BAG_SIZE
    names = [n for n in (hp_name, mp_name) if n]
    for nm in names:
        t = ds.item_by_name[nm]
        have = sum(1 for it in p.bag if int(it.get("index", -1)) == t.index)
        while have < 12 and len(p.bag) < BAG_SIZE - 2:
            p.bag.append(w.new_item_instance(t))
            have += 1
    if amulet is not None:
        from pyserver.util import U_BUJUK
        cur = p.equipment[U_BUJUK]
        if cur is None or int(cur.get("durability", 0)) < 50:
            p.equipment[U_BUJUK] = w.new_item_instance(amulet)


def _dura_snapshot(p):
    return [int(e.get("durability", 0)) if e else None for e in p.equipment]


def _ensure_summon(srv, w, p, gm):
    """道士：托管内核不会主动召唤，竞技场在每场开战前补召（真实 _cast_magic，耗符耗蓝照扣）。
    有神兽(30)用神兽，否则骷髅(17)。宝宝跨场保留，死了下一场再召。"""
    from pyserver.world import Monster
    if p.job != 2:
        return
    mid = 30 if 30 in p.skills else 17 if 17 in p.skills else None
    if mid is None:
        return
    alive = [w.actor(i) for i in list(p.summon_ids)]
    if any(isinstance(a, Monster) and a.alive and a.hp > 0 for a in alive):
        return
    p.summon_ids.clear()
    p.mp = p.max_mp
    p.skill_cooldown.pop(mid, None)
    with contextlib.redirect_stdout(io.StringIO()):
        srv._cast_magic(p.session, {"type": "castMagic", "magicId": mid,
                                    "targetX": p.x, "targetY": p.y, "targetId": -1})
        for _ in range(15):
            now = clock.advance_ms(100)
            try:
                w.tick(now)
            except Exception as e:
                _note_error(e, "召唤")


def run_combo(tree, job, level, tier, monsters, fights=10, seed=1):
    """一个（职业, 等级, 装备档）对一组怪各打 fights 场，返回逐怪统计。"""
    from pyserver.util import U_BUJUK, U_WEAPON
    from pyserver.world import Monster

    random.seed(seed * 7919 + job * 131 + level)
    ctx = _setup(tree)
    srv, w, ds = ctx["srv"], ctx["w"], ctx["ds"]
    p, (sx, sy) = _player(ctx, job)
    gm = p.map
    p.level = level
    with contextlib.redirect_stdout(io.StringIO()):
        equip = ctx["gear"].equip(p, tier)
    _set_skills(w, p)
    hp_name, mp_name = pick_potions(ds, level)
    amulet = ds.item_by_name.get("护身符") if job == 2 else None
    p.bag.clear()
    _refill(w, p, ds, hp_name, mp_name, amulet)
    p.recompute(w)
    p.hp, p.mp = p.max_hp, p.max_mp
    base_dura = _dura_snapshot(p)
    stats = dict(maxHp=p.max_hp, maxMp=p.max_mp, **{k: p.combat.get(k, 0) for k in (
        "dcMin", "dcMax", "mcMin", "mcMax", "scMin", "scMax", "acMin", "acMax", "macMin", "macMax")})
    out = []
    for mname in monsters:
        mt = w.monster_templates.get(mname)
        if mt is None:
            continue
        ttks, deaths, hp_used, mp_used, wpn, arm, amu = [], 0, 0, 0, 0, 0, 0
        train = {}
        timeouts = 0
        p.hp, p.mp = p.max_hp, p.max_mp
        for _f in range(fights):
            gm.reposition(p, sx, sy)
            if not p.alive or getattr(p, "dead", False):
                p.alive, p.dead = True, False
                p.hp, p.mp = p.max_hp, p.max_mp
            pilot = srv.autopilot.get(p)
            if pilot is None or not pilot.active:
                with contextlib.redirect_stdout(io.StringIO()):
                    srv.autopilot.enable(p, "arena", clock.now_ms())
            _refill(w, p, ds, hp_name, mp_name, amulet)
            _ensure_summon(srv, w, p, gm)
            base_dura = _dura_snapshot(p)
            bag_before = {}
            for it in p.bag:
                bag_before[int(it["index"])] = bag_before.get(int(it["index"]), 0) + 1
            skill_before = {k: (v["level"], v.get("train", 0)) for k, v in p.skills.items()}
            mx, my = sx + 3, sy
            m = Monster(id=w.new_id(), name=mt.name, x=mx, y=my, dir=4, hp=mt.hp, max_hp=mt.hp,
                        mp=mt.mp, max_mp=mt.mp, level=mt.level, race=mt.race,
                        race_img=mt.raceImg, appr=mt.appr, template=mt, spawn_x=mx,
                        spawn_y=my, spawn_map=gm, respawn_interval_ms=10 ** 12)
            m.exp = mt.exp
            w._init_race_traits(m, mt)
            w.add_actor(m, gm)
            start = clock.now_ms()
            p.exp = 0
            limit = start + FIGHT_TIMEOUT_S * 1000
            died = False
            with contextlib.redirect_stdout(io.StringIO()):
                while m.alive and m.hp > 0:
                    now = clock.advance_ms(100)
                    try:
                        w.tick(now)
                        srv.autopilot.tick(now)
                    except Exception as e:      # 服务端异常：记录并继续（见 SERVER_ERRORS）
                        _note_error(e, mt.name)
                    if not p.alive or getattr(p, "dead", False):
                        died = True
                        break
                    if now >= limit:
                        break
            dur = (clock.now_ms() - start) / 1000.0
            if died:
                deaths += 1
            elif m.alive and m.hp > 0:
                timeouts += 1
            else:
                ttks.append(dur)
            # 消耗统计
            after = {}
            for it in p.bag:
                after[int(it["index"])] = after.get(int(it["index"]), 0) + 1
            for nm, acc in ((hp_name, "hp"), (mp_name, "mp")):
                if nm:
                    idx = ds.item_by_name[nm].index
                    used = bag_before.get(idx, 0) - after.get(idx, 0)
                    if acc == "hp":
                        hp_used += max(0, used)
                    else:
                        mp_used += max(0, used)
            cur = _dura_snapshot(p)
            for s, (b, c) in enumerate(zip(base_dura, cur)):
                if b is None:
                    continue
                loss = b - (c if c is not None else 0)
                if s == U_WEAPON:
                    wpn += loss
                elif s == U_BUJUK:
                    amu += loss
                else:
                    arm += loss
            # 恢复耐久（保持装备档不变，只记录磨损量）
            for s, e in enumerate(p.equipment):
                if base_dura[s] is not None:
                    if e is None:
                        with contextlib.redirect_stdout(io.StringIO()):
                            ctx["gear"].equip(p, tier) if s != U_BUJUK else None
                    elif s != U_BUJUK:
                        e["durability"] = base_dura[s]
            base_dura = _dura_snapshot(p)
            for k, v in p.skills.items():
                b = skill_before.get(k, (v["level"], 0))
                gained = v.get("train", 0) - b[1] if v["level"] == b[0] else v.get("train", 0)
                train[k] = train.get(k, 0) + max(0, gained)
                p.skills[k]["level"], p.skills[k]["train"] = b[0], b[1]
            for a in list(gm.actors.values()):
                if isinstance(a, Monster) and a.master_id < 0:
                    gm.remove(a.id)
                    w.actors.pop(a.id, None)
            if deaths >= 3 or timeouts >= 2:
                break
        n = len(ttks)
        tries = n + deaths + timeouts
        out.append(dict(
            job=job, level=level, tier=tier, monster=mname, kills=n, tries=tries,
            deaths=deaths, timeouts=timeouts,
            ttk=statistics.mean(ttks) if ttks else None,
            ttk_p90=sorted(ttks)[int(0.9 * (n - 1))] if ttks else None,
            hp_pot=hp_used / max(1, tries), mp_pot=mp_used / max(1, tries),
            wpn_dura=wpn / max(1, tries), arm_dura=arm / max(1, tries),
            amulet=amu / max(1, tries),
            train={w.magic_templates[k].name: v / max(1, tries) for k, v in train.items() if v},
        ))
    errs = {k: dict(count=v["count"], where=sorted(v["where"])) for k, v in SERVER_ERRORS.items()}
    SERVER_ERRORS.clear()
    return dict(job=job, level=level, tier=tier, equip=list(equip.values()), stats=stats,
                hp_potion=hp_name, mp_potion=mp_name, fights=out, server_errors=errs)
