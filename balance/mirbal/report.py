"""生成单文件 HTML 报告（数据内嵌 JSON + 零依赖图表）。"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pyserver.util import calc_get_exp

from . import gm
from .analysis import SCALES, Analyzer
from .batch import fingerprint
from .data import JOBS
from .gear import TIER_NAMES, TIERS

ASSETS = Path(__file__).parent / "assets"


def _r(v, n=2):
    if v is None:
        return None
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(v, n)
    return v


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return _r(o)


def collect(ds, arena, world, label=None):
    an = Analyzer(ds, arena, world)
    prog, econ, gear, heat, best = {}, {}, {}, {}, {}
    for job in (0, 1, 2):
        prog[job], econ[job], heat[job], best[job] = {}, {}, {}, {}
        for tier in TIERS:
            prog[job][tier] = {}
            for N in SCALES:
                band = max(1.0, N * an.band_share["default"])
                rows, _pick = an.progression(job, tier, band)
                prog[job][tier][N] = rows
            econ[job][tier] = {s: an.economy(job, tier, s) for s in ("exp_h", "net_h")}
            table = an.fight_rows(job, tier, 1)
            levels = sorted(table)
            score = {}
            for L in levels:
                for r in table[L]:
                    if r["death"] <= 0.1:
                        score[r["monster"]] = max(score.get(r["monster"], 0), r["exp_h"])
            top = sorted(score, key=lambda k: -score[k])[:32]
            top.sort(key=lambda n: ds.monsters[n].level)
            v, notes = [], []
            for name in top:
                row, nrow = [], []
                for L in levels:
                    r = next((x for x in table[L] if x["monster"] == name), None)
                    ok = r is not None and r["death"] <= 0.1
                    row.append(r["exp_h"] if ok else None)
                    nrow.append(f"单挑 {r['ttk']:.1f}s · 死亡率 {r['death']:.0%} · 金币 {r['gold_h'] + r['loot_h']:,.0f}/h" if r else "不在等级窗口")
                v.append(row)
                notes.append(nrow)
            heat[job][tier] = dict(xs=levels, ys=[f"{n}({ds.monsters[n].level})" for n in top], v=v, notes=notes)
            best[job][tier] = {L: sorted([r for r in table[L] if r["death"] <= 0.1], key=lambda r: -r["exp_h"])[:6]
                               for L in levels}
        gear[job] = an.gear_acquire(job)
    skills = an.skill_training()
    server = an.server_scale()
    equip = {f"{c['job']}-{c['level']}-{c['tier']}": dict(equip=c["equip"], stats=c["stats"],
                                                          hp=c["hp_potion"], mp=c["mp_potion"]) for c in arena}
    mons = []
    for m in ds.farmable():
        mons.append(dict(name=m.name, level=m.level, hp=m.hp, exp=m.exp, ac=m.ac, mac=m.mac,
                         dc=f"{m.dc}-{m.dcmax}", count=m.total_count, maps="/".join(m.maps[:3]),
                         gold=m.gold_per_kill, loot=m.item_value_per_kill,
                         supply=m.supply_per_hour(), exp_hp=m.exp / max(1, m.hp)))
    jprog = {str(j): {t: {str(n): v for n, v in d.items()} for t, d in td.items()} for j, td in prog.items()}
    jecon = {str(j): v for j, v in econ.items()}
    jgear = {str(j): v for j, v in gear.items()}
    errors = {}
    for c in arena:
        for k, v in (c.get("server_errors") or {}).items():
            e = errors.setdefault(k, {"count": 0, "where": set()})
            e["count"] += v["count"]
            e["where"].update(v["where"])
    for r in world:
        for k, v in (r.get("server_errors") or {}).items():
            e = errors.setdefault(k, {"count": 0, "where": set()})
            e["count"] += v
            e["where"].add(f"{r.get('map_title')} N={r.get('n')}")
    errors = [dict(error=k, count=v["count"], where=sorted(v["where"])[:12]) for k, v in errors.items()]
    findings = gm.run(ds, an, jprog, jecon, jgear, skills, an.eta_rows, server, errors)
    return dict(label=label or ds.tree, prog=jprog, econ=jecon, gear=jgear, heat=heat, best=best,
                skills=skills, server=server, equip=equip, monsters=mons, world=an.eta_rows,
                eta=an.eta, findings=findings, errors=errors)


def build(ds, arena, world, compare=None, out_dir: Path = Path("out"), log=print):
    log("[report] 汇总指标 …")
    data = collect(ds, arena, world)
    cmp = None
    if compare is not None:
        cds, carena, cworld = compare
        log(f"[report] 汇总对比树 {cds.tree} …")
        c = collect(cds, carena, cworld)
        cmp = dict(label=c["label"], prog=c["prog"], econ=c["econ"], findings=c["findings"])
    w = ds.w
    rates = ds.rates
    cfg = w.config
    meta = dict(
        tree=ds.tree, root=str(cfg.gamedata_root), fp=fingerprint(None if ds.tree == "main" else ds.tree),
        generated=time.strftime("%Y-%m-%d %H:%M"),
        rates=dict(exp=rates.exp, gold=rates.gold, item=rates.item, goldAmount=rates.gold_amount,
                   magicTrain=rates.magic_train, monsters=len(rates.monsters), items=len(rates.items)),
        weekend=dict(enabled=cfg.weekend_enabled, exp=cfg.weekend_exp_rate,
                     drop=getattr(cfg, "weekend_drop_rate", None)),
        afk=dict(exp=cfg.afk_exp_rate), counts=dict(monsters=len(ds.monsters), farm=len(ds.farmable()),
                                                     items=len(ds.items), mongen=len(w.mon_gen_entries),
                                                     arena=sum(len(c["fights"]) for c in arena),
                                                     world=len(world)),
        jobs=JOBS, tiers=TIER_NAMES, scales=list(SCALES),
        need=ds.need_exps[:60], cum=ds.cum_exp()[:60],
    )
    payload = _clean(dict(meta=meta, d=data, cmp=cmp))
    html = (ASSETS / "report.html").read_text(encoding="utf-8")
    html = html.replace("/*CHARTS_JS*/", (ASSETS / "charts.js").read_text(encoding="utf-8"))
    html = html.replace("/*APP_JS*/", (ASSETS / "app.js").read_text(encoding="utf-8"))
    html = html.replace('"__DATA__"', json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    stamp = time.strftime("%Y%m%d-%H%M")
    out = out_dir / f"{ds.tree}-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "snapshot.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    latest = out_dir / "latest.html"
    latest.write_text(html, encoding="utf-8")
    return out / "index.html"
