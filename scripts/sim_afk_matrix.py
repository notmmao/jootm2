#!/usr/bin/env python3
"""离线修炼多场景对比：等级 × 地图 × 档位，产出收益/装备磨损/怪物密度指标。

回答用户 2026-11-24 的问题：「15-20 级挂骷髅洞或僵尸洞，装备磨损怎么样？
收益多少？怪物密度如何？」——单跑一组看不出差异，必须同参数横比。

对照组（默认 6 组）：
  A  15 级 骷髅洞 D011（天然洞穴一层）
  B  18 级 骷髅洞 D011
  C  20 级 骷髅洞 D011
  D  15 级 僵尸洞 D401（比奇矿区）
  E  18 级 僵尸洞 D401
  F  20 级 僵尸洞 D402（东部矿区）
  G  22 级 盟重省 3（对照组：当前线上默认图）

指标（每组一次跑完）：
* **收益**：经验/分钟、击杀/分钟、金币/小时、升级数
* **装备磨损**：每个槽位持久起→止、折损%、预计耗尽分钟、是否中途损毁
* **怪物密度**：初始怪物数、每千格密度、平均搜索半径、空转（无怪可打）分钟占比
* **补给**：轮数、占时%、失败次数
* **风险**：死亡次数、最低 HP%、停机原因

用法（务必 3.10）：
    D:/Python310/python.exe scripts/sim_afk_matrix.py                # 默认 7 组
    D:/Python310/python.exe scripts/sim_afk_matrix.py --hours 2
    D:/Python310/python.exe scripts/sim_afk_matrix.py --only A,E
    D:/Python310/python.exe scripts/sim_afk_matrix.py --json out.json

每组独立进程隔离不了虚拟时钟，所以**同进程顺序跑**：每组新建
Database + GameService + Clock，跑完关闭，互不影响。
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time as _time
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pyserver  # noqa: E402
from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.service import GameService  # noqa: E402
from pyserver.util import (U_ARMRINGL, U_ARMRINGR, U_BUJUK, U_DRESS,  # noqa: E402
                           U_HELMET, U_NECKLACE, U_RINGL, U_RINGR, U_WEAPON)
from pyserver.world import Monster, Player, now_ms  # noqa: E402

SLOT_NAMES = {
    U_DRESS: "衣服", U_WEAPON: "武器", U_NECKLACE: "项链",
    U_HELMET: "头盔", U_ARMRINGL: "左腕", U_ARMRINGR: "右腕",
    U_RINGL: "左戒", U_RINGR: "右戒", U_BUJUK: "护身符",
}


# ---------------------------------------------------------------- 虚拟时钟
class Clock:
    def __init__(self, start_ms: int):
        self.t = int(start_ms)

    def advance(self, ms: int) -> None:
        self.t += int(ms)

    def now_ms(self) -> int:
        return self.t


def patch_clock(clock: Clock) -> None:
    for mod in list(sys.modules.values()):
        if not getattr(mod, "__name__", "").startswith("pyserver"):
            continue
        if getattr(mod, "now_ms", None) is not None:
            setattr(mod, "now_ms", clock.now_ms)


# ---------------------------------------------------------------- 角色准备
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


def login(srv, session, name: str, job: int) -> Player:
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


def bag_item(t, mi: int) -> dict:
    d = max(1, t.duraMax or 1000)
    return {"makeIndex": mi, "index": t.index, "durability": d,
            "durabilityMax": d, "lucky": 0}


def _pick_gear(w, slot: int, level: int, modes: tuple[int, ...]):
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
    """穿该等级买得到的最好装备（满持久），并备药。

    磨损测试的前提是「起始满持久、全槽位有装备」，否则折损% 没意义。
    """
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
    potions = [t for t in w.item_templates.values()
               if t.stdMode == 0 and t.acMin > 0 and t.price <= 200]
    potions.sort(key=lambda t: t.acMin)
    if potions:
        t = potions[min(len(potions) - 1, 2)]
        for _ in range(12):
            mi += 1
            p.bag.append(bag_item(t, mi))
    if p.job == 1:
        manas = [t for t in w.item_templates.values()
                 if t.stdMode == 0 and t.macMin > 0 and t.price <= 200]
        manas.sort(key=lambda t: t.macMin)
        if manas:
            t = manas[min(len(manas) - 1, 2)]
            for _ in range(12):
                mi += 1
                p.bag.append(bag_item(t, mi))


def snapshot_durability(p) -> dict[int, tuple[int, int, str]]:
    """采当前各槽位 (持久, 上限, 名字)；槽位空返回 0。"""
    out = {}
    for slot in range(len(p.equipment)):
        it = p.equipment[slot]
        if it is None:
            out[slot] = (0, 0, "")
            continue
        out[slot] = (int(it.get("durability", 0) or 0),
                     int(it.get("durabilityMax", 0) or 0),
                     str(it.get("name", "") or ""))
    return out


def prune_maps(w, keep: set[str]) -> int:
    removed = 0
    for a in list(w.actors.values()):
        if not isinstance(a, Monster) or a.map is None:
            continue
        if a.map.name in keep:
            continue
        a.map.remove(a.id)
        w.actors.pop(a.id, None)
        removed += 1
    return removed


def pick_hunting_spot(w, map_id: str):
    from collections import Counter
    spots = [(e.x, e.y, e.monster_name) for e in w.mon_gen_entries
             if e.map_id == map_id and e.count > 0]
    if not spots:
        return None
    gm = w.get_or_load_map(map_id)
    if gm is None:
        return None
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
                nx, ny = cx + dx, cy + dy
                if gm.inside(nx, ny) and gm.can_walk(nx, ny) \
                        and not gm.occupied(nx, ny):
                    return nx, ny
        for dy in range(-r, r + 1):
            for dx in (-r, r):
                nx, ny = cx + dx, cy + dy
                if gm.inside(nx, ny) and gm.can_walk(nx, ny) \
                        and not gm.occupied(nx, ny):
                    return nx, ny
    return cx, cy


# ---------------------------------------------------------------- 场景定义
@dataclass
class Scene:
    key: str
    label: str
    level: int
    map_id: str
    job: int = 0
    preset: str = "balanced"


SCENES = [
    Scene("A", "15级 骷髅洞 D011", 15, "D011"),
    Scene("B", "18级 骷髅洞 D011", 18, "D011"),
    Scene("C", "20级 骷髅洞 D011", 20, "D011"),
    Scene("D", "15级 僵尸洞 D401", 15, "D401"),
    Scene("E", "18级 僵尸洞 D401", 18, "D401"),
    Scene("F", "20级 僵尸洞 D402", 20, "D402"),
    Scene("G", "22级 盟重省 3（对照组）", 22, "3"),
]


@dataclass
class Result:
    key: str
    label: str
    level: int
    map_id: str
    map_name: str = ""
    minutes: float = 0.0
    sim_wall_sec: float = 0.0
    # 怪物密度
    monsters0: int = 0
    cells: int = 0
    density_per_k: float = 0.0
    mon_levels: list = field(default_factory=list)
    mon_names: dict = field(default_factory=dict)
    # 收益
    exp_gained: int = 0
    kills: int = 0
    gold_delta: int = 0
    level_start: int = 0
    level_end: int = 0
    lv_ups: int = 0
    # 磨损
    dura_before: dict = field(default_factory=dict)
    dura_after: dict = field(default_factory=dict)
    # 槽位消失分两类，语义完全不同，不能混在一起统计：
    #   broken = 持久真被磨到 0（原版 do_damage_weapon 直接清槽）
    #   lost   = 死亡掉落整件丢了（_death_drops，与磨损无关）
    dura_broken: list = field(default_factory=list)
    dura_lost: list = field(default_factory=list)
    # 补给 / 风险 / 空转
    supply_rounds: int = 0
    supply_min_pct: float = 0.0
    supply_fails: int = 0
    deaths: int = 0
    min_hp_pct: float = 100.0
    idle_min_pct: float = 0.0
    stop_reason: str = ""


def run_scene(sc: Scene, hours: float, tick_ms: int, seed: int = 42) -> Result:
    # 组间必须固定种子：否则同参数两次跑出来的击杀数/死亡率差异全来自随机，
    # 用户拿这份表做决策时无法复现（第一次 F 组死了 1 次掉项链，第二次没死，
    # 正是没固定种子的表现）。
    import random as _random
    _random.seed(seed)
    cfg = load()
    cfg = replace(
        cfg,
        afk_min_level=1,
        afk_excluded_maps=(),
        afk_daily_minutes=max(120, int(hours * 60) + 60),
        afk_pool_cap_minutes=max(600, int(hours * 60) + 60),
        afk_newbie_minutes=max(600, int(hours * 60) + 60),
        afk_max_agents=5,
        afk_per_map_limit=5,
        # 记录补给轮数/失败：对比场景时补给策略必须一致
        afk_supply_enabled=1,
        # 磨损观察需要「装备不会被自动修理」，补给修理关掉以免污染折损%
        afk_supply_repair=0,
        afk_log_enabled=0,
    )
    db = Database(Path(tempfile.mkdtemp()) / f"matrix-{sc.key}.db")
    db.seed_game_data(cfg.mud2_dir)
    srv = GameService(cfg, db)
    w = srv.world

    clock = Clock(now_ms())
    patch_clock(clock)

    sess = SimSession()
    p = login(srv, sess, f"Sim{sc.key}", sc.job)
    p.level = sc.level
    p.recompute(w)
    p.gold = 20000
    p.hp, p.mp = p.max_hp, p.max_mp
    dress_up(srv, p, w, sc.level)

    spot = pick_hunting_spot(w, sc.map_id)
    if spot is None:
        db.close()
        r = Result(sc.key, sc.label, sc.level, sc.map_id)
        r.stop_reason = f"地图 {sc.map_id} 没有刷怪点"
        return r
    if w.space_move(p, sc.map_id, spot[0], spot[1]) is None:
        db.close()
        r = Result(sc.key, sc.label, sc.level, sc.map_id)
        r.stop_reason = f"无法放到 {sc.map_id}{spot}"
        return r
    w.player_entered(p)
    p.hp, p.mp = p.max_hp, p.max_mp

    gm = p.map
    r = Result(sc.key, sc.label, sc.level, sc.map_id)
    # gm.name 是地图 ID（D011），中文名在 world.map_titles
    r.map_name = getattr(w, "map_titles", {}).get(gm.name, "") or gm.name
    r.monsters0 = sum(1 for a in gm.actors.values() if isinstance(a, Monster))
    r.cells = gm.width * gm.height
    r.density_per_k = r.monsters0 / r.cells * 1000 if r.cells else 0
    r.mon_levels = sorted({a.level for a in gm.actors.values()
                           if isinstance(a, Monster)})
    from collections import Counter as _C
    r.mon_names = dict(_C(a.name for a in gm.actors.values()
                          if isinstance(a, Monster)).most_common(6))
    r.level_start = p.level
    r.dura_before = {k: v for k, v in snapshot_durability(p).items() if v[0]}

    prune_maps(w, {sc.map_id, cfg.afk_supply_town_map})

    ok, why, _ = srv.afk.request_start(p, int(hours * 60), sc.preset)
    if not ok:
        r.stop_reason = f"开启被拒：{why}"
        db.close()
        return r
    srv.on_disconnect(sess)
    pilot = srv.afk._pilots.get(p.id)
    if pilot is None:
        r.stop_reason = "离线代理未接管"
        db.close()
        return r

    start_ms = clock.t
    total_ms = int(hours * 3600 * 1000)
    step = int(tick_ms)
    t_wall = _time.perf_counter()
    samples: list[dict] = []
    min_hp = p.max_hp
    last_min = -1
    stop_at = None
    stop_reason = ""

    while clock.t - start_ms < total_ms:
        clock.advance(step)
        try:
            srv.tick()
        except Exception as e:
            stop_at = (clock.t - start_ms) / 60_000
            stop_reason = f"tick 异常：{type(e).__name__}: {e}"
            break
        # 最低 HP 必须按 tick 采：分钟整点采样会漏掉「3 分钟内被磨死」的
        # 短场景（D 组曾经显示最低 HP 100% 却因血量过低停机，自相矛盾）。
        cur0 = w.actor(p.id)
        if cur0 is not None:
            min_hp = min(min_hp, cur0.hp)
        mi = int((clock.t - start_ms) // 60_000)
        if mi == last_min:
            continue
        last_min = mi
        live = srv.afk._pilots.get(p.id)
        cur = w.actor(p.id)
        if live is None or cur is None:
            stop_at = mi
            stop_reason = (pilot.reason if pilot else "") or "pilot 丢失"
            break
        min_hp = min(min_hp, cur.hp)
        samples.append({
            "m": mi,
            "x": cur.x, "y": cur.y, "map": cur.map.name if cur.map else "",
            "hp": cur.hp, "max_hp": cur.max_hp,
            "level": cur.level, "bag": len(cur.bag), "gold": cur.gold,
            "supply": live.supply_state, "status": live.status,
            "kills": live.kills, "deaths": live.deaths,
        })

    r.sim_wall_sec = _time.perf_counter() - t_wall
    r.minutes = (clock.t - start_ms) / 60_000
    # 收尾：先取耐久再 disable（disable→_settle 会把角色移出世界）
    r.dura_after = {k: v for k, v in snapshot_durability(p).items() if v[0]}
    r.level_end = p.level
    r.lv_ups = max(0, p.level - r.level_start)
    r.kills = pilot.kills if pilot else 0
    r.deaths = pilot.deaths if pilot else 0
    r.supply_fails = pilot.supply_fails if pilot else 0
    r.exp_gained = srv.afk._exp_gained(w, pilot, p) if pilot else 0
    r.gold_delta = p.gold - pilot.gold_start if pilot else 0
    if pilot and pilot.log_supplies:
        r.supply_rounds = pilot.log_supplies
    for slot, (before, _mx, _n) in r.dura_before.items():
        after = r.dura_after.get(slot)
        if after is None or after[0] == 0:
            r.dura_broken.append(SLOT_NAMES.get(slot, str(slot)))
    if samples:
        num = len(samples)
        r.supply_min_pct = sum(1 for s in samples if s["supply"]) / num * 100
        r.idle_min_pct = sum(1 for s in samples
                             if "没有可打的怪" in s["status"]) / num * 100
        r.min_hp_pct = min_hp * 100 / max(1, p.max_hp)

    # 槽位消失要分因：死亡掉落 ≠ 磨损耗尽。原本一律记成「损毁」，导致
    # F 组（死亡 1 次）项链/戒指被标成 100% 磨损，其实是死了掉装备。
    # 判据：槽位空了且本组死过人 → 优先归因掉落；只有从未死亡才判磨损。
    for slot, (before, _mx, _nm) in r.dura_before.items():
        after = r.dura_after.get(slot)
        if after is None or after[0] == 0:
            name = SLOT_NAMES.get(slot, str(slot))
            if r.deaths > 0:
                r.dura_lost.append(name)
            else:
                r.dura_broken.append(name)
    r.stop_reason = stop_reason if stop_at is not None else ""

    srv.afk.disable(p, "模拟结束") if p.id in srv.afk._pilots else None
    db.close()
    return r


# ---------------------------------------------------------------- 输出
def fmt_dura(r: Result) -> str:
    """每个槽位的磨损明细。``预计耗尽`` 按本组实测磨损速率线性外推。

    磨损速率随「击杀频率 × 每击是否命中 × Random(2,6)」波动，所以短时长
    样本的 ETA 仅供参考；横向对比同一时长下各组的绝对折损量更可靠。
    """
    lines = []
    for slot in sorted(r.dura_before):
        b, bmax, name = r.dura_before[slot]
        a = r.dura_after.get(slot)
        av, amax, _nm = a if a else (0, bmax, "")
        nm = SLOT_NAMES.get(slot, str(slot))
        if av == 0:
            # 空了：分清是磨到 0 还是死了掉落，两者对玩家的含义完全不同
            why = "死亡掉落" if nm in r.dura_lost else "持久耗尽损毁"
            lines.append(f"    {nm:5s} {b:6d} → 空      （{why}）")
            continue
        used = b - av
        pct = used * 100 / max(1, b)
        per_min = used / max(1e-9, r.minutes)
        eta = (av / per_min) if per_min > 0 else float("inf")
        if per_min <= 0:
            eta_s = "不磨损"
        elif eta > 24 * 60:
            eta_s = f"{eta / 60:.0f} 小时"
        else:
            eta_s = f"{eta:.0f} 分"
        lines.append(f"    {nm:5s} {b:6d} → {av:6d} 折损 {pct:5.1f}% "
                     f"（{per_min:5.1f}/分）预计耗尽 {eta_s}")
    return "\n".join(lines)


def print_result(r: Result) -> None:
    print(f"\n{'=' * 78}")
    print(f"[{r.key}] {r.label}")
    print(f"{'-' * 78}")
    print(f"  地图 {r.map_id} {r.map_name} · 初始怪 {r.monsters0} "
          f"· 密度 {r.density_per_k:.1f}/千格 · 怪等级 {r.mon_levels}")
    if r.mon_names:
        print(f"  怪种 {r.mon_names}")
    print(f"  时长 {r.minutes:.0f} 分（实际耗时 {r.sim_wall_sec:.1f}s）")
    if r.stop_reason:
        print(f"  停机：{r.stop_reason}")
    m = max(1e-9, r.minutes)
    print(f"  收益 经验 {r.exp_gained}（{r.exp_gained / m:.0f}/分，"
          f"{r.exp_gained / m * 60:.0f}/时）"
          f" · 击杀 {r.kills}（{r.kills / m:.2f}/分）")
    print(f"      金币 {r.gold_delta:+d}（{r.gold_delta / m * 60:+.0f}/时）"
          f" · 等级 {r.level_start}→{r.level_end}（+{r.lv_ups}）")
    print(f"  补给 {r.supply_rounds} 轮 · 占时 {r.supply_min_pct:.0f}% "
          f"· 失败 {r.supply_fails} · 空转 {r.idle_min_pct:.0f}%")
    print(f"  风险 死亡 {r.deaths} · 最低 HP {r.min_hp_pct:.0f}%")
    if r.dura_before:
        print("  装备磨损：")
        print(fmt_dura(r))


def print_summary(results: list[Result]) -> None:
    print(f"\n\n{'#' * 100}")
    print("汇总对比")
    print(f"{'#' * 100}")
    hdr = (f"{'组':3s} {'场景':22s} {'怪':>4s} {'密度':>5s} {'经验/分':>8s} "
           f"{'击杀/分':>7s} {'金币/时':>8s} {'升级':>4s} {'补给%':>6s} "
           f"{'空转%':>6s} {'死亡':>4s} {'武器折损':>8s} {'衣服折损':>8s}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        m = max(1e-9, r.minutes)

        def pct(slot):
            if slot not in r.dura_before:
                return "-"
            b = r.dura_before[slot][0]
            a = r.dura_after.get(slot)
            av = a[0] if a else 0
            return f"{(b - av) * 100 / max(1, b):.1f}%"

        print(f"{r.key:3s} {r.label[:20]:22s} {r.monsters0:4d} "
              f"{r.density_per_k:5.1f} {r.exp_gained / m:8.0f} "
              f"{r.kills / m:7.2f} {r.gold_delta / m * 60:8.0f} "
              f"{r.lv_ups:4d} {r.supply_min_pct:6.0f} {r.idle_min_pct:6.0f} "
              f"{r.deaths:4d} {pct(U_WEAPON):>8s} {pct(U_DRESS):>8s}")

    print("\n武器/衣服耐久折损 → 每 100 分钟磨损量（越高越费装备）")
    for r in results:
        wb = r.dura_before.get(U_WEAPON, (0,))[0]
        wa = r.dura_after.get(U_WEAPON, (0,))[0]
        db_ = r.dura_before.get(U_DRESS, (0,))[0]
        da = r.dura_after.get(U_DRESS, (0,))[0]
        w100 = (wb - wa) / max(1e-9, r.minutes) * 100
        d100 = (db_ - da) / max(1e-9, r.minutes) * 100
        note = ""
        if r.dura_lost:
            note = f"  [死亡掉落: {'/'.join(r.dura_lost)}]"
        if r.dura_broken:
            note += f"  [磨到 0: {'/'.join(r.dura_broken)}]"
        print(f"  [{r.key}] {r.label[:22]:22s} 武器 {w100:6.0f}/100分 "
              f"（{wb:5d}→{wa:5d}）  衣服 {d100:6.0f}/100分 "
              f"（{db_:5d}→{da:5d}）{note}")

    # 结论从数据里生成，不写死——写死的文字一旦参数变化就会骗人
    print("\n关键观察（由上面的数据自动归纳）：")
    ok_runs = [r for r in results if r.minutes >= 20]
    if ok_runs:
        best_exp = max(ok_runs, key=lambda r: r.exp_gained / max(1e-9, r.minutes))
        best_gold = max(ok_runs, key=lambda r: r.gold_delta / max(1e-9, r.minutes))
        safest = min(ok_runs, key=lambda r: (r.deaths, -r.min_hp_pct))
        print(f"  · 经验效率最高：{best_exp.label}"
              f"（{best_exp.exp_gained / best_exp.minutes:.0f}/分）")
        print(f"  · 金币效率最高：{best_gold.label}"
              f"（{best_gold.gold_delta / best_gold.minutes * 60:+.0f}/时）")
        print(f"  · 最安全（死亡 {safest.deaths}、最低 HP {safest.min_hp_pct:.0f}%）："
              f"{safest.label}")
        # 磨损榜：武器按 /100 分折损量排序
        wear = sorted(
            ok_runs,
            key=lambda r: -(r.dura_before.get(U_WEAPON, (0,))[0]
                            - r.dura_after.get(U_WEAPON, (0,))[0]),
            reverse=True)
        top = wear[0]
        wb = top.dura_before.get(U_WEAPON, (0,))[0]
        wa = top.dura_after.get(U_WEAPON, (0,))[0]
        print(f"  · 最费武器：{top.label}"
              f"（{(wb - wa) / top.minutes * 100:.0f} 持久/100 分"
              f"≈ {wb / max(1, (wb - wa) / top.minutes):.0f} 分钟磨完一把 {wb} 持久的武器）")
    short_runs = [r for r in results if r.minutes < 20]
    if short_runs:
        print("  · 提前停机（时长不足 20 分钟，数据参考价值有限）：")
        for r in short_runs:
            print(f"      [{r.key}] {r.label} — {r.stop_reason}")


def print_dura_legend() -> None:
    print("\n说明：『折损%』= (起始持久 - 结束持久) / 起始持久；"
          "『预计耗尽』= 按本组实测速率线性外推。")
    print("      『死亡掉落』指死亡时装备被系统掉落，不是磨损耗尽，两者不可混算。")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=2.0,
                    help="每组模拟时长（小时），默认 2")
    ap.add_argument("--tick-ms", type=int, default=100)
    ap.add_argument("--only", default="",
                    help="只跑指定组，逗号分隔，如 A,C,E")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    args = ap.parse_args()

    scenes = SCENES
    if args.only:
        keys = {k.strip().upper() for k in args.only.split(",") if k.strip()}
        scenes = [s for s in SCENES if s.key in keys]
        if not scenes:
            print(f"没有匹配的组：{args.only}（可用 {[s.key for s in SCENES]}）")
            return 1

    print(f"[matrix] {len(scenes)} 组 × {args.hours} 小时 · tick {args.tick_ms}ms")
    results: list[Result] = []
    t0 = _time.perf_counter()
    for i, sc in enumerate(scenes, 1):
        print(f"\n[matrix] ({i}/{len(scenes)}) 跑 {sc.key} {sc.label} …")
        try:
            r = run_scene(sc, args.hours, args.tick_ms)
        except Exception as e:
            import traceback
            traceback.print_exc()
            r = Result(sc.key, sc.label, sc.level, sc.map_id)
            r.stop_reason = f"运行异常：{type(e).__name__}: {e}"
        results.append(r)
        print_result(r)
    print(f"\n[matrix] 全部完成，总耗时 {_time.perf_counter() - t0:.1f}s")
    print_summary(results)
    print_dura_legend()

    if args.json:
        out = []
        for r in results:
            d = {k: v for k, v in r.__dict__.items()}
            d["dura_before"] = {str(k): list(v) for k, v in r.dura_before.items()}
            d["dura_after"] = {str(k): list(v) for k, v in r.dura_after.items()}
            out.append(d)
        Path(args.json).write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[matrix] JSON 已写入 {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
