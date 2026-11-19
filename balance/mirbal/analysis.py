"""把引擎 A（单挑）+ 引擎 B（真实地图效率 η）+ 静态数据合成为各类指标表。"""
from __future__ import annotations

import math
from collections import defaultdict

from pyserver.util import calc_get_exp

from .data import JOBS

DEATH_LIMIT = 0.10          # 单挑死亡率 >10% 的怪不算「能稳定刷」
POTION_LIMIT_H = 240        # 每小时喝药 >240 瓶（每 15 秒一瓶）也不算「稳定可刷」
SCALES = (1, 10, 100, 1000)


def _interp(points: dict, x: float) -> float:
    """分段线性插值（points: {x: y}）。"""
    ks = sorted(points)
    if not ks:
        return 0.0
    if x <= ks[0]:
        return points[ks[0]]
    if x >= ks[-1]:
        return points[ks[-1]]
    for a, b in zip(ks, ks[1:]):
        if a <= x <= b:
            t = (x - a) / (b - a)
            return points[a] * (1 - t) + points[b] * t
    return points[ks[-1]]


class Analyzer:
    def __init__(self, ds, arena: list, world: list | None = None, scale_share: dict | None = None):
        self.ds = ds
        self.arena = arena
        self.world = world or []
        self._repair_cache = {}
        self._repair_npc = self._find_repair_npc()
        self.eta = self._calibrate_eta()
        # 同等级段在线人数占全服比例（N 人服里与你同段抢同一种怪的人数 = N × share）
        self.band_share = scale_share or {"default": 0.25}

    # ---------- 价格 ----------
    def _find_repair_npc(self):
        srv = self.ds.srv
        for n in self.ds.w.npcs.values():
            try:
                if getattr(n, "script", None) is not None and srv._repair_allowed(n, "repair"):
                    return n
            except Exception:
                continue
        return None

    def repair_per_dura(self, item_name: str) -> float:
        """修理每 1 点耐久（内部单位，1000 = 显示 1 点）的金币（真实 _repair_price）。"""
        if item_name in self._repair_cache:
            return self._repair_cache[item_name]
        t = self.ds.item_by_name.get(item_name)
        v = 0.0
        if t is not None and t.duraMax > 0:
            loss = max(1, t.duraMax // 2)
            it = {"durability": t.duraMax - loss, "durabilityMax": t.duraMax}
            prices = []
            for n in self.ds.merchants:          # 取全服最便宜的能修该物品的 NPC
                try:
                    if self.ds.srv._repair_allowed(n, "repair"):
                        pr = self.ds.srv._repair_price(n, t, it, "repair")
                        if pr > 0:
                            prices.append(pr)
                except Exception:
                    pass
            v = min(prices) / loss if prices else 0.0
        self._repair_cache[item_name] = v
        return v

    def price(self, name):
        if not name:
            return 0
        return self.ds.shop_price.get(name, self.ds.item_by_name[name].price if name in self.ds.item_by_name else 0)

    # ---------- η 校准 ----------
    def _arena_ttk(self, job, level, tier, monster):
        best = None
        for c in self.arena:
            if c["job"] != job or c["tier"] != tier:
                continue
            for f in c["fights"]:
                if f["monster"] == monster and f["ttk"]:
                    d = abs(c["level"] - level)
                    if best is None or d < best[0]:
                        best = (d, f["ttk"])
        return best[1] if best else None

    def _calibrate_eta(self):
        """η = 真实地图上「纯战斗时间」占比 = Σ 击杀×单挑ttk / 总时长。按人数 n 汇总。"""
        by_n = defaultdict(list)
        self.eta_rows = []
        for r in self.world:
            for p in r["players"]:
                busy = 0.0
                for mon, k in p["kills"].items():
                    ttk = self._arena_ttk(p["job"], r["level"], r["tier"], mon)
                    if ttk:
                        busy += k * ttk
                eta = busy / (r["minutes"] * 60.0) if r["minutes"] else 0
                eta = max(0.02, min(1.0, eta))
                # 被围殴致死的场景不拿来校准；道士在引擎 A 里补召了宝宝、引擎 B（托管内核原样）不召，口径不一致也不拿
                if p["deaths"] <= 3 and p["job"] != 2:
                    by_n[r["n"]].append(eta)
                self.eta_rows.append(dict(map=r["map_title"], level=r["level"], n=r["n"],
                                          job=p["job"], eta=eta, exp_h=p["exp_h"],
                                          kills_h=p["kills_h"], gold_h=p["gold_h"] + p["loot_h"],
                                          deaths_h=p["deaths"] / (r["minutes"] / 60.0)))
        eta = {n: sum(v) / len(v) for n, v in by_n.items() if v}
        return eta or {1: 0.55}

    def eta_for(self, n):
        return _interp(self.eta, n)

    # ---------- 每个组合 × 每只怪的收益 ----------
    def fight_rows(self, job, tier, n_same_band=1):
        """返回 {level: [row...]}，row 含 exp_h/gold_h/cost_h 等（已含 η 与刷怪供给上限）。"""
        ds, rates = self.ds, self.ds.rates
        eta1 = self.eta_for(1)
        out = {}
        for c in self.arena:
            if c["job"] != job or c["tier"] != tier:
                continue
            L = c["level"]
            rows = []
            wpn = c["equip"][1] if len(c["equip"]) > 1 else None
            wpn_name = None
            for nm in c["equip"]:
                t = ds.item_by_name.get(nm)
                if t is not None and t.stdMode in (5, 6):
                    wpn_name = nm
            armor = [nm for nm in c["equip"] if nm != wpn_name]
            arm_rep = (sum(self.repair_per_dura(n) for n in armor) / len(armor)) if armor else 0
            wpn_rep = self.repair_per_dura(wpn_name) if wpn_name else 0
            hp_pr, mp_pr = self.price(c["hp_potion"]), self.price(c["mp_potion"])
            amu_pr = self.price("护身符") / max(1, ds.item_by_name["护身符"].duraMax) if "护身符" in ds.item_by_name else 0
            for f in c["fights"]:
                m = ds.monsters.get(f["monster"])
                if m is None or not f["ttk"]:
                    continue
                death_rate = f["deaths"] / max(1, f["tries"])
                exp_k = calc_get_exp(L, m.level, m.exp) * rates.exp_for(m.name)
                cap = 3600.0 / f["ttk"] * eta1
                supply = m.supply_per_hour() / max(1.0, n_same_band)
                k_h = min(cap, supply)          # 单人也受刷新上限约束（稀有精英怪尤其明显）
                cost_k = (f["hp_pot"] * hp_pr + f["mp_pot"] * mp_pr + f["wpn_dura"] * wpn_rep
                          + f["arm_dura"] * arm_rep + f["amulet"] * amu_pr)
                rows.append(dict(
                    monster=m.name, mlevel=m.level, maps=m.maps[:3], ttk=f["ttk"],
                    death=death_rate, exp_k=exp_k, kills_h=k_h, cap_h=cap, supply_h=supply,
                    exp_h=k_h * exp_k, gold_h=k_h * m.gold_per_kill,
                    loot_h=k_h * m.item_value_per_kill, cost_h=k_h * cost_k,
                    pot_h=k_h * (f["hp_pot"] * hp_pr + f["mp_pot"] * mp_pr),
                    repair_h=k_h * (f["wpn_dura"] * wpn_rep + f["arm_dura"] * arm_rep),
                    amulet_h=k_h * f["amulet"] * amu_pr,
                    wpn_dura_h=k_h * f["wpn_dura"], hp_pot_h=k_h * f["hp_pot"], mp_pot_h=k_h * f["mp_pot"],
                    train=f.get("train", {}),
                ))
            out[L] = rows
        return out

    @staticmethod
    def best(rows, key="exp_h"):
        net = lambda r: r["gold_h"] + r["loot_h"] - r["cost_h"]
        stable = lambda r: r["death"] <= DEATH_LIMIT and r["hp_pot_h"] + r["mp_pot_h"] <= POTION_LIMIT_H
        if key == "net_h":
            ok = [r for r in rows if stable(r)]
            return max(ok, key=net) if ok else None
        ok = [r for r in rows if stable(r) and r[key] > 0]
        if not ok:
            return None
        return max(ok, key=lambda r: r[key])

    # ---------- 成长时间线 ----------
    def progression(self, job, tier, n_same_band=1, max_level=60):
        table = self.fight_rows(job, tier, n_same_band)
        exp_pts, pick = {}, {}
        for L, rows in table.items():
            b = self.best(rows)
            if b:
                exp_pts[L] = b["exp_h"]
                pick[L] = b
        cum, per = 0.0, []
        for L in range(1, max_level):
            eh = _interp(exp_pts, L) if exp_pts else 0
            need = self.ds.need_exps[L - 1]
            hrs = need / eh if eh > 0 else float("inf")
            cum += hrs
            near = min(pick, key=lambda x: abs(x - L)) if pick else None
            per.append(dict(level=L, need=need, exp_h=eh, hours=hrs, cum_hours=cum,
                            monster=pick[near]["monster"] if near else None))
        return per, pick

    # ---------- 经济 ----------
    def economy(self, job, tier, strategy="exp_h", n_same_band=1):
        table = self.fight_rows(job, tier, n_same_band)
        out = []
        for L in sorted(table):
            b = self.best(table[L], strategy)
            if b is None:
                continue
            out.append(dict(level=L, monster=b["monster"], gold_h=b["gold_h"], loot_h=b["loot_h"],
                            pot_h=b["pot_h"], repair_h=b["repair_h"], amulet_h=b["amulet_h"],
                            net_h=b["gold_h"] + b["loot_h"] - b["cost_h"], exp_h=b["exp_h"]))
        return out

    # ---------- 装备获取 ----------
    def gear_acquire(self, job, n_same_band=1):
        """「本级最强」里商店买不到的件：专刷最优怪的期望/P90 小时数。"""
        ds = self.ds
        table = self.fight_rows(job, "shop", n_same_band)       # 用商店装去刷
        econ = {r["level"]: r for r in self.economy(job, "shop", "net_h", n_same_band)}   # 攒钱按「打钱」策略
        out = []
        for c in self.arena:
            if c["job"] != job or c["tier"] != "best":
                continue
            L = c["level"]
            rows = {r["monster"]: r for r in table.get(L, []) if r["death"] <= DEATH_LIMIT}
            net = econ.get(L, {}).get("net_h", 0.0)
            for item in c["equip"]:
                if item in ds.shop_price:
                    hrs = ds.shop_price[item] / net if net > 0 else float("inf")
                    out.append(dict(level=L, item=item, src="商店", price=ds.shop_price[item],
                                    hours=hrs, p90=hrs,
                                    via=f"打钱 {econ.get(L, {}).get('monster', '?')} 攒钱购买" if net > 0 else "该级打钱也不赚钱"))
                    continue
                lam_best, via = 0.0, None
                for mon, p in ds.drop_sources(item):
                    r = rows.get(mon)
                    if r is None:
                        continue
                    lam = r["kills_h"] * p
                    if lam > lam_best:
                        lam_best, via = lam, mon
                if via is None:
                    srcs = sorted(ds.drop_sources(item), key=lambda t: -t[1])[:3]
                    via = ("打不动/不在等级窗口：" + "、".join(f"{n}({ds.monsters[n].level}级 {p:.2%})" for n, p in srcs)) if srcs else "无任何刷新怪掉落"
                hrs = math.log(2) / lam_best if lam_best > 0 else float("inf")
                p90 = math.log(10) / lam_best if lam_best > 0 else float("inf")
                out.append(dict(level=L, item=item, src="掉落", price=0, hours=hrs, p90=p90, via=via))
        return out

    # ---------- 技能修炼 ----------
    def skill_training(self):
        """纯修炼用时：持续使用该技能（主动技能每个施法节拍一次 / 剑术每次命中）达到每级所需点数。"""
        w = self.ds.w
        from pyserver.autopilot import attack_interval_ms, SPELL_INPUT_BASE_MS
        out = []
        for mid, t in sorted(w.magic_templates.items()):
            if t.needLevel[0] <= 0:
                continue
            for lv in range(3):
                need = t.maxTrain[lv]
                lvl_req = t.needLevel[lv]
                if mid in (3, 12, 25, 26, 7):        # 剑术：按命中次数
                    per_use = 1 if mid in (12, 25, 26) else 2
                    if mid == 7:
                        per_use = 2 * 0.15           # 攻杀约 1/7 概率发动（近似）
                    beat = attack_interval_ms(lvl_req) / 0.85
                elif t.delay <= 0:
                    per_use, beat = 2, attack_interval_ms(lvl_req)
                else:
                    per_use = 2
                    beat = max(t.delay, SPELL_INPUT_BASE_MS + t.delay / 10)
                uses_h = 3_600_000 / beat
                hrs = need / (per_use * uses_h) if need > 0 else 0
                out.append(dict(skill=t.name, job=JOBS.get(t.job, t.job), lv=lv + 1,
                                need_level=lvl_req, points=need, hours=hrs))
        return out

    # ---------- 技能书获取难度（GM 分析技能爆率是否合理） ----------
    def skill_books(self):
        """每本技能书（StdMode=4）的获取途径与难度，供 GM 判断技能爆率合理性。

        三个核心维度：
          1. 获取难度：全服每小时产出、单人 P50 刷到小时；死书 = 无怪掉落且无商店出售。
          2. 怪物分布：书由哪些等级段的怪掉落、覆盖多少种怪、掉落怪等级区间。
          3. 等级错配：掉落怪的最低等级 vs 该技能学习等级——若「学技能的等级还打不到掉书的怪」，
             说明玩家在需要该技能的时点拿不到书，只能越级打怪或靠商店，记为 mismatch。
        P50 = ln2 / (单人可独占的每小时掉率)；单人独占按 band_share 折算（同段抢怪）。
        """
        import math as _m
        ds = self.ds
        w = ds.w
        share = max(1.0, self.band_share.get("default", 0.25))
        magic_by_name = {m.name: m for m in w.magic_templates.values()}
        books = [t for t in w.item_templates.values() if getattr(t, "stdMode", 0) == 4]
        out = []
        for t in books:
            name = t.name
            m = magic_by_name.get(name)
            need_level = m.needLevel[0] if m else None
            job = JOBS.get(m.job, m.job) if m else None
            # 商店出售：shop_price 键为物品名，shop_where 记录「地图·NPC」
            shops = sorted(set(ds.shop_where.get(name, [])))
            shop_price = ds.shop_price.get(name)
            has_shop = name in ds.shop_price
            # 怪物掉落来源
            sources = ds.drop_sources(name)
            src_rows = []
            for monster, p in sources:
                mi = ds.monsters.get(monster)
                if mi is None or p <= 0:
                    continue
                sp = mi.supply_per_hour()
                src_rows.append(dict(monster=monster, level=mi.level, p=p,
                                     supply_h=sp, out_h=sp * p))
            # 全服产出与单人独占掉率
            total_rate_h = sum(r["out_h"] for r in src_rows)
            solo_rate_h = total_rate_h / share
            p50_hours = (_m.log(2) / solo_rate_h) if solo_rate_h > 0 else None
            levels = sorted({r["level"] for r in src_rows})
            dead = (not has_shop) and total_rate_h <= 0
            mismatch = None
            if need_level is not None and levels:
                # 学习等级时够不到最低级掉书怪 → 在需要技能的时点拿不到书
                mismatch = min(levels) > need_level
            # 怪等级分布（按 10 级一档聚合，便于看「20 级书掉自哪些等级怪」）
            band_dist = defaultdict(int)
            for r in src_rows:
                band_dist[(r["level"] // 10) * 10] += 1
            out.append(dict(
                book=name, job=job, need_level=need_level,
                price=t.price, shop=has_shop, shop_price=shop_price, shop_npc=shops,
                sources=len(src_rows), dead=dead,
                mon_levels=levels,
                mon_level_min=min(levels) if levels else None,
                mon_level_max=max(levels) if levels else None,
                level_bands=dict(sorted(band_dist.items())),
                out_h=total_rate_h, p50_hours=p50_hours,
                mismatch=mismatch,
                # 无对应技能：StdMode=4 但 magic_templates 里没有同名技能（如「天尊之兽」）
                orphan=(m is None),
                monsters=sorted({r["monster"] for r in src_rows}),
            ))
        out.sort(key=lambda r: (r["need_level"] is None, r["need_level"] or 0, r["book"]))
        return out

    # ---------- 全服规模：稀有物品与金币总量 ----------
    def server_scale(self, job_mix=(0.34, 0.33, 0.33), tier="shop"):
        """N 人在线（按等级段 4 段均分）时：全服每日产出的金币净流入、主要物品产出量。"""
        ds = self.ds
        out = []
        for N in SCALES:
            band = max(1.0, N * self.band_share["default"])
            gold_day = 0.0
            item_day = defaultdict(float)
            exp_h_avg = []
            for job, share in zip((0, 1, 2), job_mix):
                for lv_rows in self.fight_rows(job, tier, band).values():
                    b = self.best(lv_rows)
                    if b is None:
                        continue
                    exp_h_avg.append(b["exp_h"])
                lv_tables = self.fight_rows(job, tier, band)
                # 等级分布：均匀落在已计算的等级上
                levels = sorted(lv_tables)
                for L in levels:
                    b = self.best(lv_tables[L])
                    if b is None:
                        continue
                    weight = N * share / len(levels)
                    gold_day += weight * 24 * (b["gold_h"] + b["loot_h"] - b["cost_h"])
                    m = ds.monsters[b["monster"]]
                    for nm, p, _c in m.drops:
                        if nm != "金币":
                            item_day[nm] += weight * 24 * b["kills_h"] * p
            out.append(dict(n=N, band=band, gold_day=gold_day,
                             exp_h=sum(exp_h_avg) / max(1, len(exp_h_avg)),
                             items=dict(sorted(item_day.items(), key=lambda kv: -kv[1])[:40])))
        return out
