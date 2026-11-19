"""GM 视角自动体检：每条规则只看当前数据跑出来的指标，输出「现象 + 证据 + 影响规模」。

补救方案文本只给方向（具体方案见 GM-ANALYSIS.md），绝不改任何数据。
"""
from __future__ import annotations

import statistics
from collections import defaultdict

from .data import JOBS

SEV = {3: "严重", 2: "注意", 1: "观察"}


def _f(kind, sev, title, detail, scale, chart, remedy):
    return dict(kind=kind, sev=sev, sevName=SEV[sev], title=title, detail=detail,
                scale=scale, chart=chart, remedy=remedy)


def run(ds, an, prog, econ, gear, skills, world_rows, server, errors=(), skillbooks=()):
    out = []
    for e in errors:
        out.append(_f("代码", 3, "模拟中服务端抛异常：" + e["error"].split(" @ ")[0][:80],
                      f"{e['error']}；触发 {e['count']} 次，场景：{'、'.join(e['where'])}。线上同样会在 tick 内抛出。",
                      "全规模", "data", "修复服务端代码（本工具不改游戏代码，只报告）"))
    need = ds.need_exps
    # 1. 经验表断崖
    for L in range(1, 60):
        r = need[L] / max(1, need[L - 1])
        if r >= 1.9:
            out.append(_f("经验", 2 if r < 2.6 else 3, f"{L}→{L + 1} 级经验需求跳 ×{r:.1f}",
                          f"{need[L - 1]:,} → {need[L]:,}；相邻级平均增幅远小于此。",
                          "全规模", "exp", "平滑经验表或在该段补一档更高经验的怪"))
    big = [L + 1 for L, v in enumerate(need) if v >= 1_500_000_000]
    if big:
        out.append(_f("经验", 1, f"{big[0]} 级起单级需求 ≥15 亿，接近 int32 上限（21.4 亿）",
                      "NEED_EXPS 尾段 2.0e9~2.14e9；经验/等级计算若有 32 位溢出路径会出问题，实际玩法上也等于封顶。",
                      "全规模", "exp", "明确设计上限（如 60 级）并把尾段改为可达值"))
    # 2. 单级用时跳变 / 练级断层（按战士商店装 N=1）
    for job in (0, 1, 2):
        rows = prog[str(job)]["shop"]["1"]
        for a, b in zip(rows, rows[1:]):
            if a["hours"] > 0.05 and b["hours"] / a["hours"] >= 3 and b["hours"] >= 1:
                out.append(_f("升级", 2, f"{JOBS[job]} {b['level']} 级单级用时陡增 ×{b['hours'] / a['hours']:.1f}",
                              f"{a['level']}级 {a['hours']:.1f}h → {b['level']}级 {b['hours']:.1f}h（最优怪 {b['monster']}）。",
                              "1-10 人最痛（无人带）", "prog", "该段加过渡怪/经验倍率，或降低该级经验需求"))
    # 3. 职业差
    for L in (20, 30, 40):
        cum = {}
        for job in (0, 1, 2):
            rows = prog[str(job)]["shop"]["1"]
            if len(rows) >= L - 1:
                cum[job] = rows[L - 2]["cum_hours"]
        if len(cum) == 3 and min(cum.values()) > 0:
            ratio = max(cum.values()) / min(cum.values())
            if ratio >= 1.5:
                slow, fast = max(cum, key=cum.get), min(cum, key=cum.get)
                out.append(_f("职业", 3 if ratio >= 2.5 else 2, f"1→{L} 级：{JOBS[slow]}比{JOBS[fast]}慢 ×{ratio:.1f}",
                              "，".join(f"{JOBS[j]} {h:.0f}h" for j, h in cum.items()) + "（商店装、单人）。",
                              "全规模", "prog", "按职业调整对应怪物经验/技能修炼倍率，或补职业专属练级点"))
    # 4. 独占最优点 + 供给饱和
    for job in (0, 1, 2):
        table = an.fight_rows(job, "shop", 1)
        for L in sorted(table):
            ok = sorted([r for r in table[L] if r["death"] <= 0.1], key=lambda r: -r["exp_h"])
            if len(ok) < 2:
                continue
            a, b = ok[0], ok[1]
            base = lambda n: n.rstrip("0123456789")
            nb = next((r for r in ok[1:] if base(r["monster"]) != base(a["monster"])), b)
            if a["exp_h"] >= 2.0 * nb["exp_h"] and L >= 7:
                out.append(_f("怪物", 2, f"{JOBS[job]} {L} 级：{a['monster']} 一家独大（×{a['exp_h'] / nb['exp_h']:.1f}）",
                              f"{a['monster']} 每小时 {a['exp_h']:,.0f} 经验，次优 {nb['monster']} {nb['exp_h']:,.0f}；全服只刷 {ds.monsters[a['monster']].total_count} 只（{'/'.join(a['maps'])}）。",
                              "10-100 人起抢怪", "heat", "给次优怪补经验或给该怪加刷新/降经验，让人群分流"))
                break
    # 5. 人数对效率的影响（引擎 B）
    byk = defaultdict(dict)
    for r in world_rows:
        byk[(r["map"], r["level"])].setdefault(r["n"], []).append(r["exp_h"])
    for (mp, L), d in byk.items():
        if 1 in d and max(d) >= 15:
            e1 = statistics.mean(d[1]) or 1
            en = statistics.mean(d[max(d)])
            if e1 > 0 and en / e1 <= 0.6:
                out.append(_f("刷怪", 2, f"{mp}（{L} 级段）{max(d)} 人同图人均经验跌到 {en / e1:.0%}",
                              f"单人 {e1:,.0f}/h → {max(d)} 人 {en:,.0f}/h（真实地图 + 刷新 + 寻路模拟）。",
                              "10-100 人", "world", "按地图人数动态缩短刷新/增加怪量，或开分线"))
    deadly = [r for r in world_rows if r.get("deaths_h", 0) >= 12]
    if deadly:
        mp = sorted({(r["map"], r["level"]) for r in deadly})
        out.append(_f("刷怪", 2, "热门练级点围殴致死频繁",
                      "；".join(f"{m} {L}级" for m, L in mp) + "：单挑最优怪所在的密集刷怪点，被成群怪围攻导致每小时死亡 ≥12 次——单挑数据看着好，实际刷不动。",
                      "1-10 人最痛", "world", "降低刷怪点密度/拆分刷新点，或给新人段怪降攻击"))
    # 6. 经济收支
    for job in (0, 1, 2):
        for strat, nm in (("exp_h", "练级"),):
            neg = [r for r in econ[str(job)]["shop"][strat] if r["net_h"] < 0]
            if neg:
                out.append(_f("经济", 3 if len(neg) >= 4 else 2, f"{JOBS[job]}{nm}在 {len(neg)} 个等级点收支为负",
                              "亏损等级：" + "、".join(f"{r['level']}级({r['net_h']:,.0f}/h)" for r in neg[:8]) + "。",
                              "1-10 人（没人收货，只能卖 NPC）", "econ", "调药价/修理费，或提高该段金币爆率"))
    # 7. 药水性价比倒挂
    pots = []
    for t in ds.items.values():
        if t.stdMode == 0 and t.shape == 0 and t.name in ds.shop_price and (t.acMin > 0 or t.macMin > 0):
            pots.append((t.name, (t.acMin or t.macMin), ds.shop_price[t.name], "HP" if t.acMin else "MP"))
    for kind in ("HP", "MP"):
        lst = sorted([p for p in pots if p[3] == kind], key=lambda p: p[1])
        for a, b in zip(lst, lst[1:]):
            if b[2] / b[1] < a[2] / a[1] * 0.8:
                out.append(_f("经济", 1, f"药价倒挂：{b[0]} 每点{kind}比 {a[0]} 便宜",
                              f"{a[0]} {a[2] / a[1]:.2f} 金/点，{b[0]} {b[2] / b[1]:.2f} 金/点：大瓶更便宜，小瓶失去存在意义。",
                              "全规模", "econ", "按每点价格单调递增重定价"))
    # 8. 装备获取
    for job in (0, 1, 2):
        rows = [r for r in gear[str(job)] if r["src"] == "掉落" and r["level"] in (21, 29, 35, 41)]
        hard = [r for r in rows if r["p90"] >= 300]
        if hard:
            out.append(_f("掉落", 2, f"{JOBS[job]}最强装中 {len(hard)} 件 P90 获取 ≥300 小时",
                          "；".join(f"{r['level']}级 {r['item']}（{r['via']}" + (f"，P90 {r['p90']:,.0f}h）" if r['p90'] != float('inf') else "）") for r in hard[:6]),
                          "1-10 人：几乎拿不到；1000 人：总会有人拿到", "gear", "按全服存量动态爆率 + 个人保底"))
    # 9. 技能修炼
    for s in skills:
        if s["hours"] >= 10:
            out.append(_f("技能", 1, f"{s['job']}「{s['skill']}」{s['lv']} 级纯修炼需 {s['hours']:.0f} 小时",
                          f"需求人物 {s['need_level']} 级，{s['points']} 点。", "全规模", "skill", "调 magicTrain 或该技能 Train 点数"))
    # 9b. 技能书获取：死书 / 等级错配 / 获取断崖
    if skillbooks:
        dead = [b for b in skillbooks if b["dead"]]
        if dead:
            out.append(_f("技能书", 3, f"{len(dead)} 本技能书「死书」：无怪掉落且无商店出售",
                          "、".join(f"{b['book']}({b['job'] or '?'}需{b['need_level']}级)" for b in dead[:10])
                          + "。玩家没有任何正规途径拿到，技能等于不可学。",
                          "全规模", "skillbook", "核对 MonItems / NPC 脚本：给该技能书补一个掉落源或商店条目"))
        real = [b for b in skillbooks if not b["orphan"]]
        mism = [b for b in real if b["mismatch"]]
        if mism:
            noshop = [b for b in mism if not b["shop"]]
            sev = 2 if noshop else 1
            out.append(_f("技能书", sev, f"{len(mism)} 本技能书「等级错配」：掉落怪高于学习等级（其中 {len(noshop)} 本无商店兜底）",
                          "；".join(f"{b['book']}(需{b['need_level']}级,掉自{b['mon_level_min']}-{b['mon_level_max']}级怪"
                                   + ("" if b["shop"] else ",无商店") + ")" for b in (noshop or mism)[:8])
                          + "。玩家到学习等级时打不到掉书的怪；有商店的可买，无商店的只能越级。",
                          "1-30 人（前期最痛）", "skillbook", "下放掉落怪等级或让低级怪也掉该书"))
        hard = [b for b in real if b["p50_hours"] and b["p50_hours"] >= 40 and not b["shop"]]
        if hard:
            out.append(_f("技能书", 2, f"{len(hard)} 本技能书无商店且单人刷到 P50 ≥40 小时",
                          "；".join(f"{b['book']}(需{b['need_level']}级,P50 {b['p50_hours']:.0f}h,{b['sources']}种怪)"
                                   for b in hard[:8])
                          + "。纯打怪玩家在该技能解锁窗口内大概率拿不到书。",
                          "1-10 人（无人带）", "skillbook", "提高掉率/降怪等级，或加入商店/任务保底"))
        orphans = [b for b in skillbooks if b["orphan"] and not b["dead"]]
        if orphans:
            out.append(_f("技能书", 1, f"{len(orphans)} 个 StdMode=4 物品无同名技能（疑似私服残留）",
                          "、".join(b["book"] for b in orphans[:10])
                          + "。按名字学习与魔法匹配，这些书点下去只会提示「无效的技能书」。",
                          "全规模", "skillbook", "核对是否私服内容，按需剥离或补对应魔法"))
    # 10. 数据异常：武器上下限颠倒
    bad = [f"{t.name}(DC {t.dcMin}-{t.dcMax})" for t in ds.items.values()
           if t.stdMode in (5, 6) and (t.dcMin > t.dcMax or t.mcMin > t.mcMax or t.scMin > t.scMax)]
    if bad:
        out.append(_f("数据", 2, f"{len(bad)} 把武器攻击/魔法/道术下限>上限",
                      "、".join(bad[:12]) + "。服务端 roll 时 hi<=lo 直接取 lo，等于固定伤害且可能被配装逻辑高估。", "全规模", "data", "核对 StdItems.csv 对应列（疑似上下限写反）"))
    # 11. 怪物经验离群
    farm = ds.farmable()
    byl = defaultdict(list)
    for m in farm:
        byl[m.level // 5].append(m.exp / max(1, m.hp))
    for m in farm:
        med = statistics.median(byl[m.level // 5])
        if med > 0 and m.exp / max(1, m.hp) >= 6 * med and m.total_count >= 3:
            out.append(_f("怪物", 1, f"{m.name}（{m.level}级）经验/血量是同段中位数的 {m.exp / m.hp / med:.0f} 倍",
                          f"HP {m.hp} 经验 {m.exp}，刷在 {'/'.join(m.maps[:3])}。", "10-100 人起抢怪", "monsters", "核对数值或作为刻意设计的精英点"))
    # 11b. 产金离群怪（掉落回收价 + 爆金），合并成一条
    byl2 = defaultdict(list)
    for m in farm:
        byl2[m.level // 5].append(m.gold_per_kill + m.item_value_per_kill)
    rich = []
    for m in farm:
        v = m.gold_per_kill + m.item_value_per_kill
        med = statistics.median(byl2[m.level // 5]) or 1
        if v >= 2000 and v >= 8 * med:
            rich.append((v / med, v, med, m))
    if rich:
        rich.sort(key=lambda t: -t[0] * t[3].total_count)
        sure = sorted({nm for _r, _v, _md, m in rich for nm, p, _c in m.drops if p >= 0.99 and nm != "金币"})
        out.append(_f("掉落", 3 if len(rich) >= 10 else 2, f"{len(rich)} 种怪是「产金怪」（每只期望产出 ≥8 倍同段中位）",
                      "；".join(f"{m.name}({m.level}级,{m.total_count}只) {v:,.0f}金/只 vs 中位{md:,.0f}" for _r, v, md, m in rich[:10])
                      + (f"。100% 必掉物品：{'、'.join(sure[:8])}" if sure else ""),
                      "1-10 人：一个人就能把 NPC 回收刷成主要收入；100 人起：药水/装备泛滥", "econ",
                      "核对 MonItems（多条 1/1 强效太阳水等疑似调试残留），或按全服存量动态降爆率"))
    # 12. 全服规模：金币与物品
    if server:
        last = server[-1]
        out.append(_f("规模", 1, f"{last['n']} 人同时在线：全服每日金币净流入 {last['gold_day'] / 1e8:.1f} 亿",
                      f"按商店装、最优练级点、24 小时在线估算；同段每 {last['band']:.0f} 人抢同一种怪（供给上限已计入）。",
                      "100-1000 人", "server", "金币回收：修理/传送/交易税或高价消耗品"))
    out.sort(key=lambda f: -f["sev"])
    return out
