"""引擎 B：无界面世界模拟。

真实地图 + 真实 MonGen 刷怪/重生 + N 个 AutoPilot 角色（与在线托管同一内核：A* 寻路、
找怪、抢怪、喝药、捡钱）在虚拟时钟下同图练级。测的是「真实环境效率」：
每小时经验/金币/击杀、以及相对竞技场（纯战斗）上限的效率系数 η。
"""
from __future__ import annotations

import contextlib
import io
import random
from collections import Counter, defaultdict

from . import boot, clock


def _spawn_points(w, map_id, names):
    pts = []
    for e in w.mon_gen_entries:
        if e.map_id == map_id and (not names or any(k in e.monster_name for k in names)):
            pts.append((e.x, e.y, max(1, e.count)))
    return pts


def _place(gm, x, y, rnd):
    for r in range(0, 30):
        for _ in range(30):
            nx, ny = x + rnd.randint(-r, r), y + rnd.randint(-r, r)
            if gm.inside(nx, ny) and gm.can_walk(nx, ny) and not gm.occupied(nx, ny):
                return nx, ny
    return None


def run(tree, map_id, level, n_players, minutes=20, jobs=(0, 1, 2), tier="shop",
        monsters=None, seed=3, warmup_s=60, log=None):
    from pyserver.world import Monster, BAG_SIZE
    from .arena import pick_potions, _set_skills, _refill
    from .data import DataSet
    from .gear import GearPicker

    rnd = random.Random(seed)
    random.seed(seed)
    cfg, srv = boot.make_service(tree)
    w = srv.world
    ds = DataSet(srv, tree or "main")
    gear = GearPicker(ds)
    gm = w.get_or_load_map(map_id)
    # 只保留目标图的怪，其余图清空（节省 tick）
    for a in list(w.actors.values()):
        if isinstance(a, Monster) and a.map is not gm:
            a.map.remove(a.id)
            w.actors.pop(a.id, None)
    if not gm.mon_spawned:
        w.spawn_monsters(gm)
    pts = _spawn_points(w, map_id, monsters)
    if not pts:
        raise ValueError(f"地图 {map_id} 没有刷怪点")
    # 选刷怪最密的一个点作为大家的集合点（模拟「热门练级点」）
    pts.sort(key=lambda t: -t[2])
    hot = pts[0]
    exp_gain = defaultdict(int)
    kills = defaultdict(Counter)
    real_add_exp = w.add_exp

    def add_exp(p, exp):            # 记账不升级：保持场景等级
        exp_gain[p.id] += exp
    w.add_exp = add_exp
    real_kill = w.kill_monster

    def kill_monster(m, now, killer=None):
        if killer is not None:
            kid = killer.id
            owner = getattr(killer, "master_id", -1)
            if owner is not None and owner >= 0:
                kid = owner
            kills[kid][m.template.name if m.template else m.name] += 1
        return real_kill(m, now, killer)
    w.kill_monster = kill_monster

    players = []
    with contextlib.redirect_stdout(io.StringIO()):
        for i in range(n_players):
            job = jobs[i % len(jobs)]
            p = boot.new_player(srv, job)
            p.level = level
            gear.equip(p, tier)
            _set_skills(w, p)
            hpn, mpn = pick_potions(ds, level)
            amulet = ds.item_by_name.get("护身符") if job == 2 else None
            p.bag.clear()
            _refill(w, p, ds, hpn, mpn, amulet)
            p.recompute(w)
            p.hp, p.mp = p.max_hp, p.max_mp
            if p.map is not gm:
                w.change_map(p, map_id, hot[0], hot[1])
            pos = _place(gm, hot[0], hot[1], rnd)
            if pos:
                gm.reposition(p, *pos)
            p.gold = 0
            srv.autopilot.enable(p, "balanced", clock.now_ms())
            players.append((p, hpn, mpn, amulet))
    total_ticks = int((warmup_s + minutes * 60) * 10)
    t_real = clock.real()
    gold0 = {}
    loot_val = defaultdict(int)
    deaths = Counter()
    errors = Counter()
    for t in range(total_ticks):
        now = clock.advance_ms(100)
        with contextlib.redirect_stdout(io.StringIO()):
            try:
                srv.tick()
            except Exception as e:
                errors[f"{type(e).__name__}: {e}"] += 1
        if t == warmup_s * 10:
            exp_gain.clear()
            kills.clear()
            loot_val.clear()
            deaths.clear()
            gold0 = {p.id: p.gold for p, *_ in players}
        if t % 50 == 0:
            for p, hpn, mpn, amulet in players:
                if not p.alive or getattr(p, "dead", False):
                    deaths[p.id] += 1
                    p.alive, p.dead = True, False
                    p.hp, p.mp = p.max_hp, p.max_mp
                    pos = _place(gm, hot[0], hot[1], rnd)
                    if p.map is not gm:
                        w.change_map(p, map_id, hot[0], hot[1])
                    if pos:
                        gm.reposition(p, *pos)
                pilot = srv.autopilot.get(p)
                if pilot is None or not pilot.active:
                    with contextlib.redirect_stdout(io.StringIO()):
                        srv.autopilot.enable(p, "balanced", now)
                keep = {ds.item_by_name[n].index for n in (hpn, mpn) if n}
                for it in list(p.bag):
                    idx = int(it.get("index", -1))
                    if idx not in keep:
                        tt = w.item_templates.get(idx)
                        if tt is not None:
                            loot_val[p.id] += ds.sell_value(tt.name, 1.0) if t > warmup_s * 10 else 0
                        p.bag.remove(it)
                _refill(w, p, ds, hpn, mpn, amulet)
    hours = minutes / 60.0
    res = []
    for p, *_ in players:
        k = kills.get(p.id, Counter())
        res.append(dict(job=p.job, exp_h=exp_gain.get(p.id, 0) / hours,
                        gold_h=(p.gold - gold0.get(p.id, 0)) / hours,
                        loot_h=loot_val.get(p.id, 0) / hours,
                        kills_h=sum(k.values()) / hours, deaths=deaths.get(p.id, 0),
                        kills=dict(k)))
    alive = sum(1 for a in gm.actors.values() if isinstance(a, Monster) and a.alive and a.master_id < 0)
    w.add_exp, w.kill_monster = real_add_exp, real_kill
    return dict(map=map_id, map_title=w.map_titles.get(map_id, map_id), level=level, n=n_players,
                minutes=minutes, tier=tier, hot=hot[:2], monsters_alive_end=alive,
                real_s=round(clock.real() - t_real, 1), players=res,
                server_errors=dict(errors))
