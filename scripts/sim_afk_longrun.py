#!/usr/bin/env python3
"""离线修炼长跑模拟器（虚拟时钟 + 加速 tick，零第三方依赖）。

目的：把「几小时挂机」压进几分钟跑完，观察离线代理会不会中途停机、
卡死、反复补给、背包塞死、无怪可打……，而不是只在真实服务端上等。

做法：
* 把 ``pyserver.*`` 各模块里的 ``now_ms`` 换成虚拟时钟，tick 间隔可调
  （默认 100ms = 真实节拍）。时间只前进不跳跃，所有动作锁/冷却照常生效。
* 复用真实 ``GameService`` + 真实地图/MonGen/NPC/掉落表，只放开离线门禁
  （等级/黑名单/时长余额），不伪造战斗结果。
* 每分钟采样一次角色状态，事件（补给进出、换图、死亡、停机）单独记录。

用法（务必 3.10）：
    D:/Python310/python.exe scripts/sim_afk_longrun.py --hours 4 --map 3 --level 22
    D:/Python310/python.exe scripts/sim_afk_longrun.py --hours 8 --map 0 --level 20 --preset efficient

注意：这是**模拟器**，不是回归断言脚本；输出用于人看，发现的问题再固化成
scripts/test_afk_*.py 的断言。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time as _time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pyserver  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.world import Monster, Player, now_ms  # noqa: E402


# ---------------------------------------------------------------- 虚拟时钟
class Clock:
    def __init__(self, start_ms: int):
        self.t = int(start_ms)

    def advance(self, ms: int) -> None:
        self.t += int(ms)

    def now_ms(self) -> int:
        return self.t


def patch_clock(clock: Clock) -> None:
    """把所有已加载 pyserver 模块的 now_ms 换成虚拟时钟。

    ``from .world import now_ms`` 是值拷贝，必须逐模块替换，只改
    ``pyserver.world.now_ms`` 覆盖不到 autopilot/afk/service。
    """
    patched = []
    for mod in list(sys.modules.values()):
        name = getattr(mod, "__name__", "")
        if not name.startswith("pyserver"):
            continue
        if getattr(mod, "now_ms", None) is not None:
            setattr(mod, "now_ms", clock.now_ms)
            patched.append(name)
    print(f"[sim] 虚拟时钟已接管 {len(patched)} 个模块")


class SimSession:
    def __init__(self):
        self.messages: list[dict] = []
        self.account_id = 0
        self.account_name = ""
        self.player_name = ""
        self.closed = False

    def send_json(self, obj: dict) -> None:
        self.messages.append(obj)

    def send_text(self, text: str) -> None:
        self.messages.append(json.loads(text))

    def close(self) -> None:
        self.closed = True


def login(srv, session, name, job: int) -> Player:
    for msg in ({"type": "register", "username": name, "password": "test12345"},
                {"type": "login", "username": name, "password": "test12345"},
                {"type": "createCharacter", "name": name, "sex": 0,
                 "hair": 1, "job": job},
                {"type": "enterGame", "name": name},
                {"type": "noticeOk", "name": name}):
        srv.on_message(session, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(session)
    assert p is not None, "登录链路失败"
    return p


# ---------------------------------------------------------------- 角色准备
def bag_item(t, mi: int, dura: int | None = None) -> dict:
    d = dura if dura is not None else max(1, t.duraMax or 1000)
    return {"makeIndex": mi, "index": t.index, "durability": d,
            "durabilityMax": max(1, t.duraMax or d), "lucky": 0}


def _pick_gear(w, slot: int, level: int, modes: tuple[int, ...]):
    """挑该槽位里等级达标、属性最好的一件（按 dcMax→acMax→macMax 排序）。"""
    from pyserver.util import slot_accepts
    best, best_key = None, None
    for t in w.item_templates.values():
        if t.stdMode not in modes or t.needLevel > level:
            continue
        if not slot_accepts(slot, t.stdMode):
            continue
        key = (t.dcMax, t.dcMin, t.acMax + t.macMax, -t.needLevel)
        if best_key is None or key > best_key:
            best, best_key = t, key
    return best


def dress_up(srv, p, w, level: int) -> None:
    """给模拟角色穿一套达标的武器/衣服/头盔/项链等，避免裸体挂机失真。"""
    from pyserver.util import (U_ARMRINGL, U_ARMRINGR, U_BUJUK, U_DRESS,
                               U_HELMET, U_NECKLACE, U_RINGL, U_RINGR, U_WEAPON)
    plans = [
        (U_WEAPON, (5, 6)),
        (U_DRESS, (10, 11)),
        (U_HELMET, (15,)),
        (U_NECKLACE, (19, 20, 21, 22)),
        (U_RINGL, (22, 23)),
        (U_RINGR, (22, 23)),
        (U_ARMRINGL, (24, 26)),
        (U_ARMRINGR, (24, 26)),
        (U_BUJUK, (25,)),
    ]
    mi = 9_000_000
    for slot, modes in plans:
        if p.equipment[slot] is not None:
            continue
        t = _pick_gear(w, slot, level, modes)
        if t is None:
            continue
        mi += 1
        p.bag.append(bag_item(t, mi))
        srv._take_on_item(p.session, {"type": "takeOnItem", "slot": slot,
                                      "makeIndex": mi})
    # 药水：血药 + 蓝药各几瓶，让第一段挂机不立刻断药
    from pyserver.util import U_BUJUK  # noqa: F401
    potions = [t for t in w.item_templates.values()
               if t.stdMode == 0 and t.acMin > 0 and t.price <= 200]
    potions.sort(key=lambda t: t.acMin)
    if potions:
        t = potions[min(len(potions) - 1, 2)]
        for _ in range(6):
            mi += 1
            p.bag.append(bag_item(t, mi, 1))
    if p.job == 1:
        manas = [t for t in w.item_templates.values()
                 if t.stdMode == 0 and t.macMin > 0 and t.price <= 200]
        manas.sort(key=lambda t: t.macMin)
        if manas:
            t = manas[min(len(manas) - 1, 2)]
            for _ in range(6):
                mi += 1
                p.bag.append(bag_item(t, mi, 1))
    p.recompute(w)


def prune_maps(w, keep: set[str]) -> int:
    """只保留本次模拟涉及的地图怪物，其余整图清空。

    ``world.tick`` 的 _respawn_dead / _tick_zilkin_revive / _expire_corpses
    每 tick 都遍历**全部 actor**（实测 3800 只怪时单 tick 11~13ms，其中三个
    函数占一半）。模拟只关心挂机图与补给路线图，其余地图在本局永远不会有
    玩家进入，清掉不影响行为，纯提速。
    """
    removed = 0
    for a in list(w.actors.values()):
        if not isinstance(a, Monster) or a.map is None:
            continue
        if a.map.name in keep:
            continue
        a.map.remove(a.id)
        w.actors.pop(a.id, None)
        removed += 1
    print(f"[sim] 清理无关地图怪物 {removed} 只，保留 {sorted(keep)}")
    return removed


def pick_hunting_spot(w, map_id: str):
    """在该图 MonGen 里挑一个怪最多的刷新点，返回其附近可走格。"""
    from collections import Counter
    spots = [(e.x, e.y, e.monster_name) for e in w.mon_gen_entries
             if e.map_id == map_id and e.count > 0]
    if not spots:
        return None
    gm = w.get_or_load_map(map_id)
    if gm is None:
        return None
    # 按「周围 20 格内刷新条目数」挑密度最高的点
    cnt = Counter()
    for x, y, _ in spots:
        cnt[(x // 20, y // 20)] += 1
    bx, by = max(cnt, key=lambda k: cnt[k])
    cands = [(x, y) for x, y, _ in spots if x // 20 == bx and y // 20 == by]
    cx = sum(x for x, _ in cands) // len(cands)
    cy = sum(y for _, y in cands) // len(cands)
    for r in range(0, 8):
        for dx in range(-r, r + 1):
            for dy in (-r, r):
                if gm.inside(cx + dx, cy + dy) and gm.can_walk(cx + dx, cy + dy) \
                        and not gm.occupied(cx + dx, cy + dy):
                    return cx + dx, cy + dy
        for dy in range(-r, r + 1):
            for dx in (-r, r):
                if gm.inside(cx + dx, cy + dy) and gm.can_walk(cx + dx, cy + dy) \
                        and not gm.occupied(cx + dx, cy + dy):
                    return cx + dx, cy + dy
    return cx, cy


# ---------------------------------------------------------------- 主模拟
def run(args) -> int:
    import random as _random
    _random.seed(args.seed)
    cfg = load()
    cfg = replace(
        cfg,
        afk_min_level=1,
        afk_excluded_maps=(),
        afk_daily_minutes=max(60, int(args.hours * 60) + 60),
        afk_pool_cap_minutes=max(600, int(args.hours * 60) + 60),
        afk_newbie_minutes=max(600, int(args.hours * 60) + 60),
        afk_max_agents=5,
        afk_per_map_limit=5,
        afk_death_stop=args.death_stop,
        afk_supply_enabled=1 if args.supply else 0,
        # 模拟器默认不写 JSONL：长跑模拟会产出大量数据，需要时用 --log 打开
        # （打开后落到 data/afk-logs，文件名带 SimAfk，和真实玩家日志可区分）。
        afk_log_enabled=1 if getattr(args, "log", False) else 0,
    )
    db_path = Path(tempfile.mkdtemp()) / "sim-afk.db"
    db = Database(db_path)
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    w = srv.world

    clock = Clock(now_ms())
    patch_clock(clock)

    sess = SimSession()
    p = login(srv, sess, "SimAfk", args.job)
    p.level = args.level
    p.recompute(w)
    p.gold = args.gold
    p.hp, p.mp = p.max_hp, p.max_mp
    dress_up(srv, p, w, args.level)

    spot = pick_hunting_spot(w, args.map)
    if spot is None:
        print(f"[sim] 地图 {args.map} 没有 MonGen 刷怪点")
        return 2
    if w.space_move(p, args.map, spot[0], spot[1]) is None:
        print(f"[sim] 无法把角色放到 {args.map}{spot}")
        return 2
    w.player_entered(p)
    p.hp, p.mp = p.max_hp, p.max_mp
    prune_maps(w, {args.map, cfg.afk_supply_town_map})
    print(f"[sim] 角色 lv{args.level} job{args.job} 放在 {args.map} {spot} "
          f"hp={p.max_hp} mp={p.max_mp} gold={p.gold} bag={len(p.bag)} "
          f"装备={sum(1 for e in p.equipment if e)}")

    ok, why, _ = srv.afk.request_start(p, int(args.hours * 60), args.preset)
    print(f"[sim] 设定计划: {ok} {why}")
    srv.on_disconnect(sess)
    pilot = srv.afk._pilots.get(p.id)
    if pilot is None:
        print("[sim] 离线代理未接管，退出")
        return 2

    start_ms = clock.t
    total_ms = int(args.hours * 3600 * 1000)
    step = int(args.tick_ms)

    if args.debug:
        from pyserver.afk import OfflinePilot
        orig_fail = OfflinePilot._supply_fail

        def traced_fail(self, srv, w, p, now, why):
            mm = (clock.t - start_ms) // 60_000
            print(f"[trace] {mm:4d}m 补给失败: {why} | state={self.supply_state} "
                  f"leg={self.supply_leg_try} at {p.map.name if p.map else '?'}"
                  f"({p.x},{p.y}) fails={self.supply_fails}")
            return orig_fail(self, srv, w, p, now, why)

        OfflinePilot._supply_fail = traced_fail
        orig_give = OfflinePilot._give_up_target

        def traced_give(self, key, now):
            if key and key[0] in ("supply", "anchor"):
                mm = (clock.t - start_ms) // 60_000
                pp = w.actor(self.player_id)
                around = ""
                if pp is not None and pp.map is not None:
                    lst = []
                    for dx in (-1, 0, 1):
                        for dy in (-1, 0, 1):
                            if dx == dy == 0:
                                continue
                            for a in pp.map.by_pos.get((pp.x + dx, pp.y + dy), ()):
                                if a.alive:
                                    lst.append(f"{type(a).__name__}:{getattr(a, 'name', '?')}")
                    around = ",".join(lst) or "-"
                print(f"[trace] {mm:4d}m 放弃目标 {key} blocked={self.blocked_steps} "
                      f"path={len(self.path)} 周围={around}")
            return orig_give(self, key, now)

        OfflinePilot._give_up_target = traced_give

        import pyserver.autopilot as AP
        orig_bfs = AP.bfs_path

        def traced_bfs(gm, sx, sy, tx, ty, exact=False):
            out = orig_bfs(gm, sx, sy, tx, ty, exact)
            if not out:
                mm = (clock.t - start_ms) // 60_000
                free = []
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == dy == 0:
                            continue
                        nx, ny = sx + dx, sy + dy
                        if gm.inside(nx, ny) and gm.can_walk(nx, ny) \
                                and not gm.occupied(nx, ny):
                            free.append((nx, ny))
                print(f"[trace] {mm:4d}m bfs空 {gm.name}({sx},{sy})→({tx},{ty}) "
                      f"exact={exact} h0={max(abs(sx-tx),abs(sy-ty))} "
                      f"可走邻格={len(free)} {free[:4]}")
            return out

        AP.bfs_path = traced_bfs

        orig_cross = OfflinePilot._supply_cross_map
        last_cross_log = [0]

        def traced_cross(self, srv, w, p, now, dest_map):
            if now - last_cross_log[0] >= 20_000:
                last_cross_log[0] = now
                mm = (clock.t - start_ms) // 60_000
                leg = self.supply_route[0] if self.supply_route else None
                print(f"[trace] {mm:4d}m 跨图→{dest_map} at "
                      f"{p.map.name if p.map else '?'}({p.x},{p.y}) route={len(self.supply_route)} "
                      f"leg={leg} legtry={self.supply_leg_try} path={len(self.path)}")
            return orig_cross(self, srv, w, p, now, dest_map)

        OfflinePilot._supply_cross_map = traced_cross
    samples: list[dict] = []
    events: list[str] = []
    last_sample = -1
    last_supply = ""
    last_status = ""
    last_map = p.map.name if p.map else ""
    stopped_at = None
    stop_reason = ""
    min_hp = p.hp
    t_wall = _time.perf_counter()

    while clock.t - start_ms < total_ms:
        clock.advance(step)
        try:
            srv.tick()
        except Exception as e:      # 服务端 tick 崩了就是头号问题
            print(f"[sim] !! tick 抛异常 {type(e).__name__}: {e}")
            events.append(f"{minute(clock.t - start_ms):5.0f}m TICK异常 "
                          f"{type(e).__name__}: {e}")
            break
        elapsed = clock.t - start_ms
        minute_i = int(elapsed // 60_000)
        if minute_i != last_sample:
            last_sample = minute_i
            live = srv.afk._pilots.get(p.id)
            cur = w.actor(p.id)
            if live is None:
                # 先看 pilot 还在不在：disable/_settle 会先摘 pilot 再移除角色，
                # 反过来判断会把「有明确停机原因」误报成「角色离场未见原因」。
                stopped_at = minute_i
                stop_reason = (pilot.reason or "未知停机") if pilot else "pilot 已丢失"
                if cur is None:
                    stop_reason = (pilot.reason or "未知停机") if pilot else "未知停机"
                break
            if cur is None:
                stopped_at, stop_reason = minute_i, "角色已离场（pilot 仍在）"
                break
            min_hp = min(min_hp, cur.hp)
            samples.append({
                "m": minute_i,
                "map": cur.map.name if cur.map else "",
                "x": cur.x, "y": cur.y,
                "hp": cur.hp, "max_hp": cur.max_hp,
                "exp": cur.exp, "level": cur.level,
                "bag": len(cur.bag), "gold": cur.gold,
                "supply": live.supply_state,
                "status": live.status,
                "deaths": live.deaths,
                "remaining": live.remaining,
                "kills": live.kills,
            })
            if live.supply_state != last_supply:
                events.append(f"{minute_i:5d}m 补给 {last_supply or '-'} → "
                              f"{live.supply_state or '-'} · {live.status}")
                last_supply = live.supply_state
            if live.status != last_status:
                if live.supply_state == last_supply:
                    events.append(f"{minute_i:5d}m 状态 {live.status}")
                last_status = live.status
            mnow = cur.map.name if cur.map else ""
            if mnow != last_map:
                events.append(f"{minute_i:5d}m 换图 {last_map} → {mnow} "
                              f"({cur.x},{cur.y})")
                last_map = mnow
            if minute_i % 10 == 0:
                rate = (clock.t - start_ms) / 1000 / max(1e-9, _time.perf_counter() - t_wall)
                dbg = ""
                if args.debug:
                    dbg = (f" | fails{live.supply_fails}"
                           f" since{(clock.t - live.supply_since) // 60_000}m"
                           f" retry{max(0, (live.supply_retry_until - clock.t) // 60_000)}m"
                           f" path{len(live.path)} act{int(live.active)}"
                           f" tgt{live.target_id} kills{live.kills}")
                print(f"[sim] {minute_i:4d}m {mnow}({cur.x},{cur.y}) "
                      f"lv{cur.level} exp{cur.exp} hp{cur.hp}/{cur.max_hp} "
                      f"bag{len(cur.bag)} gold{cur.gold} "
                      f"supply={live.supply_state or '-'} 剩{live.remaining}m "
                      f"[{rate:.0f}x]{dbg}")

    wall = _time.perf_counter() - t_wall
    sim_min = (clock.t - start_ms) / 60_000
    print("\n" + "=" * 72)
    print(f"[sim] 模拟 {sim_min:.0f} 分钟，实际耗时 {wall:.1f}s "
          f"（{sim_min * 60 / max(1e-9, wall):.0f}x）")
    if samples:
        s0, sl = samples[0], samples[-1]
        hours = max(1e-9, sim_min / 60)
        print(f"[sim] 起 lv{s0['level']} exp{s0['exp']} gold{s0['gold']} "
              f"bag{s0['bag']} @{s0['map']}({s0['x']},{s0['y']})")
        print(f"[sim] 终 lv{sl['level']} exp{sl['exp']} gold{sl['gold']} "
              f"bag{sl['bag']} @{sl['map']}({sl['x']},{sl['y']})")
        print(f"[sim] 击杀 {sl['kills']} · 死亡 {sl['deaths']} · 最低 HP {min_hp}"
              f" · 金币 {sl['gold'] - s0['gold']:+d}（{ (sl['gold']-s0['gold'])/hours:+.0f}/h）")
        supply_min = sum(1 for s in samples if s["supply"])
        print(f"[sim] 补给占时 {supply_min} 分钟（{supply_min / max(1, len(samples)) * 100:.0f}%）")
        # 位置停滞检测：连续 10 分钟同格
        stuck = 0
        worst = (0, "")
        for a, b in zip(samples, samples[1:]):
            if (a["x"], a["y"], a["map"]) == (b["x"], b["y"], b["map"]):
                stuck += 1
                if stuck > worst[0]:
                    worst = (stuck, f"{b['m']}m @{b['map']}({b['x']},{b['y']})")
            else:
                stuck = 0
        print(f"[sim] 最长原地不动 {worst[0]} 分钟 {worst[1]}")
    if stopped_at is not None:
        print(f"[sim] !! 在 {stopped_at} 分钟停机：{stop_reason}")
    else:
        print(f"[sim] 跑满 {sim_min:.0f} 分钟未停机")
    for rep in db.pending_afk_reports(p.character_id):
        print(f"[sim] 结算报告: {rep['reason']} / {rep['notice']} "
              f"· {rep['minutes']}m exp{rep['exp_gained']} "
              f"kills{rep['kills']} gold{rep['gold_delta']:+d} deaths{rep['deaths']}")
    print("-" * 72)
    for e in events:
        print("  " + e)
    db.close()
    return 0 if stopped_at is None else 1


def minute(ms: int) -> float:
    return ms / 60_000


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--map", default="3")
    ap.add_argument("--level", type=int, default=22)
    ap.add_argument("--job", type=int, default=0, choices=(0, 1, 2))
    ap.add_argument("--preset", default="balanced")
    ap.add_argument("--gold", type=int, default=20000)
    ap.add_argument("--tick-ms", type=int, default=100)
    ap.add_argument("--death-stop", type=int, default=3)
    ap.add_argument("--supply", type=int, default=1)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--log", action="store_true",
                    help="同时写 pyserver 的离线修炼 JSONL 日志（data/afk-logs）")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
