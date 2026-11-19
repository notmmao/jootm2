(function () {
  const M = DATA.meta, D = DATA.d, C = DATA.cmp, f = Viz.fmtN;
  const $ = id => document.getElementById(id);
  const JOBS = M.jobs, TIERS = M.tiers;
  const opt = (sel, items, val) => { sel.innerHTML = items.map(([v, t]) => `<option value="${v}">${t}</option>`).join(""); if (val != null) sel.value = val; };
  const jobOpts = Object.entries(JOBS), tierOpts = Object.entries(TIERS);
  const esc = s => String(s ?? "").replace(/[&<>]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  function table(host, cols, rows) {
    let sortK = null, asc = false;
    const draw = () => {
      let r = rows.slice();
      if (sortK != null) r.sort((a, b) => { const x = a[sortK], y = b[sortK]; return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * (asc ? 1 : -1); });
      host.innerHTML = `<table><thead><tr>${cols.map((c, i) => `<th data-i="${i}">${c[1]}</th>`).join("")}</tr></thead><tbody>${r.map(row => `<tr>${cols.map(c => { const v = row[c[0]]; const num = typeof v === "number"; return `<td class="${num ? "n" : ""}">${c[2] ? c[2](v, row) : num ? f(v) : esc(v)}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
      host.querySelectorAll("th").forEach(th => th.onclick = () => { const k = cols[+th.dataset.i][0]; asc = sortK === k ? !asc : false; sortK = k; draw(); });
    };
    draw();
  }
  const resizers = [];
  const on = (fn, ...ids) => { ids.forEach(id => $(id).addEventListener("change", fn)); fn(); resizers.push(fn); };

  // ---- header ----
  $("hdr").textContent = `· 数据树 ${M.tree}`;
  $("meta").innerHTML = `生成 ${M.generated} · 指纹 <code>${M.fp}</code> · 倍率 exp×${M.rates.exp} gold×${M.rates.gold} item×${M.rates.item} 金额×${M.rates.goldAmount} 修炼×${M.rates.magicTrain}（按怪覆盖 ${M.rates.monsters}、按物品覆盖 ${M.rates.items}）· 周末 ${M.weekend.enabled ? "开" : "关"}(经验×${M.weekend.exp}/爆率×${M.weekend.drop}，报告按平日算) · 离线经验×${M.afk.exp} · 单挑 ${M.counts.arena} 组 · 世界模拟 ${M.counts.world} 场`;

  // ---- KPI ----
  const prog = (j, t, n) => D.prog[j][t][String(n)];
  const cumAt = (j, t, n, L) => { const r = prog(j, t, n)[L - 2]; return r ? r.cum_hours : null; };
  let k = "";
  for (const j of ["0", "1", "2"]) k += `<div class="kpi"><span>${JOBS[j]} 1→30 级（商店装·单人）</span><b>${f(cumAt(j, "shop", 1, 30))} 小时</b><span>1→40：${f(cumAt(j, "shop", 1, 40))} 小时</span></div>`;
  const sev = s => D.findings.filter(x => x.sev === s).length;
  k += `<div class="kpi"><span>GM 体检</span><b>${D.findings.length} 条</b><span>严重 ${sev(3)} · 注意 ${sev(2)} · 观察 ${sev(1)}</span></div>`;
  k += `<div class="kpi"><span>可刷怪 / 刷怪行</span><b>${M.counts.farm} / ${M.counts.mongen}</b><span>怪物模板 ${M.counts.monsters} · 物品 ${M.counts.items}</span></div>`;
  const e1 = D.eta["1"]; k += `<div class="kpi"><span>真实地图效率 η（单人）</span><b>${e1 != null ? Math.round(e1 * 100) + "%" : "—"}</b><span>其余时间在走路/找怪/喝药</span></div>`;
  $("kpis").innerHTML = k;

  // ---- GM ----
  const kinds = ["全部", ...new Set(D.findings.map(x => x.kind))];
  opt($("gmKind"), kinds.map(x => [x, x]));
  on(() => {
    const kk = $("gmKind").value;
    const rows = D.findings.filter(x => kk === "全部" || x.kind === kk);
    table($("gmTable"), [["sev", "级别", (v, r) => `<span class="sev sev${v}">${r.sevName}</span>`], ["kind", "类别"], ["title", "现象"], ["detail", "证据"], ["scale", "影响规模"], ["remedy", "方向", v => esc(v)], ["chart", "图", v => `<a href="#${{ exp: "exp", prog: "prog", heat: "heat", world: "world", econ: "econ", gear: "gear", skill: "skill", data: "gear", skillbook: "skillbook", monsters: "mons", server: "server" }[v] || v}">→</a>`]], rows);
  }, "gmKind");

  // ---- 经验表 ----
  const lv = M.need.map((_, i) => i + 1);
  Viz.line($("cExp"), { x: lv, xName: "Lv", series: [{ name: "本级升级所需经验", values: M.need }], ylog: true, yLabel: "经验（对数）", height: 260 });
  const ratio = M.need.map((v, i) => i ? v / M.need[i - 1] : null);
  Viz.bar($("cExpRatio"), { x: lv.map(x => x), xName: "Lv", series: [{ name: "相对上一级 ×", values: ratio }], fmt: v => v == null ? "—" : "×" + v.toFixed(2), yLabel: "相邻级增幅 ×", height: 260 });

  // ---- 升级时间线 ----
  opt($("pTier"), tierOpts, "shop"); opt($("pN"), M.scales.map(n => [n, n + " 人"]), "1"); opt($("bJob"), jobOpts, "0");
  on(() => {
    const t = $("pTier").value, n = $("pN").value, mt = $("pMetric").value;
    const rows0 = prog("0", t, n);
    const series = ["0", "1", "2"].map((j, i) => ({ name: JOBS[j], ci: i, values: prog(j, t, n).map(r => r[mt]), notes: prog(j, t, n).map(r => r.monster) }));
    if (C && mt !== "exp_h") ["0", "1", "2"].forEach((j, i) => series.push({ name: JOBS[j] + "（" + C.label + "）", ci: i, dash: true, values: C.prog[j][t][n].map(r => r[mt]) }));
    Viz.line($("cProg"), { x: rows0.map(r => r.level + 1), xName: "升到 Lv", series, ylog: true, yLabel: { hours: "小时（对数）", cum_hours: "累计小时（对数）", exp_h: "经验/小时（对数）" }[mt], height: 320 });
  }, "pTier", "pN", "pMetric");
  on(() => {
    const j = $("bJob").value, t = $("pTier").value, b = D.best[j][t];
    const rows = [];
    Object.keys(b).forEach(L => (b[L] || []).slice(0, 3).forEach((r, i) => rows.push({ L: +L, rank: i + 1, ...r, maps: (r.maps || []).join("/"), inc: r.gold_h + r.loot_h - r.cost_h })));
    table($("bestTable"), [["L", "等级"], ["rank", "名次"], ["monster", "怪物"], ["mlevel", "怪等级"], ["maps", "地图"], ["ttk", "单挑秒"], ["exp_h", "经验/h"], ["kills_h", "击杀/h"], ["supply_h", "全服供给/h"], ["inc", "净收入/h"], ["death", "死亡率", v => Math.round(v * 100) + "%"]], rows);
  }, "bJob", "pTier");

  // ---- 热力 ----
  opt($("hJob"), jobOpts, "0"); opt($("hTier"), tierOpts, "shop");
  on(() => { const h = D.heat[$("hJob").value][$("hTier").value]; Viz.heat($("cHeat"), { xs: h.xs, ys: h.ys, v: h.v, xName: "Lv", label: "经验/小时", note: (j, i) => h.notes[j][i] }); }, "hJob", "hTier");

  // ---- 怪物 ----
  const bands = ["1-19", "20-39", "40-59", "60+"];
  const bandOf = l => l < 20 ? 0 : l < 40 ? 1 : l < 60 ? 2 : 3;
  Viz.scatter($("cMon"), { points: D.monsters.map(m => ({ x: m.level, y: m.exp_hp, label: m.name, g: bandOf(m.level), r: 3 + Math.min(7, Math.sqrt(m.count) / 2), note: `HP ${m.hp} · 经验 ${m.exp} · 全服 ${m.count} 只 · ${m.maps}` })), groups: bands, ylog: true, xLabel: "怪物等级", yLabel: "经验/血量", height: 300 });
  table($("monTable"), [["name", "怪物"], ["level", "等级"], ["hp", "HP"], ["exp", "经验"], ["exp_hp", "经验/HP"], ["ac", "AC"], ["dc", "攻击"], ["count", "全服数量"], ["supply", "供给/h"], ["gold", "金币/只"], ["loot", "掉落价值/只"], ["maps", "地图"]], D.monsters);

  // ---- 经济 ----
  opt($("eJob"), jobOpts, "0"); opt($("eTier"), tierOpts, "shop");
  on(() => {
    const rows = D.econ[$("eJob").value][$("eTier").value][$("eStrat").value];
    Viz.bar($("cEcon"), { x: rows.map(r => r.level), xName: "Lv", stacked: true, notes: rows.map(r => "刷 " + r.monster + " · 经验 " + f(r.exp_h) + "/h"), series: [
      { name: "爆金", ci: 3, values: rows.map(r => r.gold_h) }, { name: "掉落回收", ci: 2, values: rows.map(r => r.loot_h) },
      { name: "药水", ci: 1, values: rows.map(r => -r.pot_h) }, { name: "修理", ci: 6, values: rows.map(r => -r.repair_h) }, { name: "护身符", ci: 4, values: rows.map(r => -r.amulet_h) }],
      extraLine: { name: "净收入", values: rows.map(r => r.net_h) }, yLabel: "金币/小时", height: 320 });
  }, "eJob", "eTier", "eStrat");
  on(() => {
    const t = $("eTier").value, s = $("eStrat").value;
    const rows0 = D.econ["0"][t][s];
    Viz.line($("cEconJobs"), { x: rows0.map(r => r.level), xName: "Lv", series: ["0", "1", "2"].map((j, i) => ({ name: JOBS[j], ci: i, values: rows0.map(r0 => { const r = D.econ[j][t][s].find(x => x.level === r0.level); return r ? r.net_h : null; }) })), yLabel: "净收入 金币/小时", height: 260 });
  }, "eTier", "eStrat");

  // ---- 装备 ----
  opt($("gJob"), jobOpts, "0");
  const glv = [...new Set(D.gear["0"].map(r => r.level))].sort((a, b) => a - b);
  opt($("gLv"), glv.map(l => [l, l + " 级"]), "29");
  on(() => {
    const j = $("gJob").value, L = +$("gLv").value;
    const rows = D.gear[j].filter(r => r.level === L);
    Viz.bar($("cGear"), { x: rows.map(r => r.item), series: [{ name: "P50 小时", ci: 0, values: rows.map(r => r.hours) }, { name: "P90 小时", ci: 1, values: rows.map(r => r.p90) }], notes: rows.map(r => r.src + " · " + (r.via || "无稳定来源")), height: 260, yLabel: "小时" });
    const inf = v => v == null ? "∞" : f(v);
    table($("gearTable"), [["item", "物品"], ["src", "来源"], ["via", "途径/专刷怪"], ["price", "价格", v => v ? f(v) : "—"], ["hours", "P50 小时", inf], ["p90", "P90 小时", inf]], rows);
    const best = D.equip[`${j}-${L}-best`], shop = D.equip[`${j}-${L}-shop`], none = D.equip[`${j}-${L}-none`];
    const st = s => s ? `HP ${s.maxHp} · DC ${s.dcMin}-${s.dcMax} · MC ${s.mcMin}-${s.mcMax} · SC ${s.scMin}-${s.scMax} · AC ${s.acMin}-${s.acMax} · MAC ${s.macMin}-${s.macMax}` : "";
    $("equipBox").innerHTML = [["新手装", none], ["商店装", shop], ["本级最强", best]].map(([n, e]) => e ? `<div><b>${n}</b>：${e.equip.join("、")}<br><span class="mut">${st(e.stats)} · 药 ${e.hp || "-"} / ${e.mp || "-"}</span></div>` : "").join("");
  }, "gJob", "gLv");

  // ---- 技能 ----
  opt($("sJob"), jobOpts.map(([v, t]) => [t, t]), "战士");
  on(() => {
    const rows = D.skills.filter(s => s.job === $("sJob").value);
    const names = [...new Set(rows.map(r => r.skill))];
    Viz.bar($("cSkill"), { x: names, series: [1, 2, 3].map((l, i) => ({ name: l + " 级", ci: i, values: names.map(n => { const r = rows.find(x => x.skill === n && x.lv === l); return r ? r.hours : 0; }) })), notes: names.map(n => rows.filter(x => x.skill === n).map(x => `${x.lv}级需人物${x.need_level}级/${x.points}点`).join(" · ")), yLabel: "纯修炼小时", height: 280, xRot: names.length > 8 });
  }, "sJob");

  // ---- 技能书获取 ----
  const BK = D.skillbooks || [];
  opt($("bkJob"), [["all", "全部职业"], ...jobOpts], "all");
  const flag = (v, on, text) => on ? `<span class="sev sev${v}">${text}</span>` : "—";
  on(() => {
    const j = $("bkJob").value, flt = $("bkFilter").value;
    let rows = BK.filter(b => j === "all" || b.job === j);
    if (flt === "dead") rows = rows.filter(b => b.dead);
    else if (flt === "mismatch") rows = rows.filter(b => b.mismatch);
    else if (flt === "noshop") rows = rows.filter(b => !b.shop);
    else if (flt === "orphan") rows = rows.filter(b => b.orphan);
    const bands = b => Object.entries(b.level_bands || {}).map(([lv, n]) => `${lv}+级×${n}`).join(" ") || "—";
    table($("bkTable"), [
      ["book", "技能书"],
      ["job", "职业"],
      ["need_level", "学习级", v => v == null ? "—" : v],
      ["price", "标价", v => f(v)],
      ["shop", "商店", (v, r) => v ? `有 ${f(r.shop_price)}（${(r.shop_npc || []).length} 处）` : "—"],
      ["sources", "掉源数"],
      ["mon_level_min", "掉落怪等级", (v, r) => v == null ? "—" : `${v}-${r.mon_level_max}`],
      ["level_bands", "怪等级分布", (v, r) => bands(r)],
      ["out_h", "全服产出/h", v => f(v)],
      ["p50_hours", "单人P50(h)", v => v == null ? "—" : f(v)],
      ["dead", "死书", (v, r) => flag(3, r.dead, r.orphan ? "无技能" : "死书")],
      ["mismatch", "等级错配", (v, r) => flag(2, !!v, "错配")],
      ["monsters", "掉落怪", (v) => (v || []).slice(0, 6).join("、") + ((v || []).length > 6 ? ` 等${v.length}种` : "")],
    ], rows);
  }, "bkJob", "bkFilter");

  // ---- 世界 ----
  const W = D.world;
  const keys = [...new Set(W.map(r => r.map + " Lv" + r.level))];
  const ns = [...new Set(W.map(r => r.n))].sort((a, b) => a - b);
  const avg = (arr, k) => arr.length ? arr.reduce((s, r) => s + r[k], 0) / arr.length : null;
  Viz.line($("cWorldExp"), { x: ns, xName: "同图人数 ", series: keys.map((kk, i) => ({ name: kk, ci: i, values: ns.map(n => avg(W.filter(r => r.map + " Lv" + r.level === kk && r.n === n), "exp_h")) })), yLabel: "人均经验/小时", height: 260 });
  Viz.line($("cWorldDeath"), { x: ns, xName: "同图人数 ", series: keys.map((kk, i) => ({ name: kk, ci: i, values: ns.map(n => avg(W.filter(r => r.map + " Lv" + r.level === kk && r.n === n), "deaths_h")) })), yLabel: "人均死亡/小时", height: 260 });
  const wrows = [];
  keys.forEach(kk => ns.forEach(n => { const a = W.filter(r => r.map + " Lv" + r.level === kk && r.n === n); if (a.length) wrows.push({ k: kk, n, eta: avg(a, "eta"), exp_h: avg(a, "exp_h"), kills_h: avg(a, "kills_h"), gold_h: avg(a, "gold_h"), deaths_h: avg(a, "deaths_h") }); }));
  table($("worldTable"), [["k", "场景"], ["n", "人数"], ["eta", "η 战斗占比", v => Math.round(v * 100) + "%"], ["exp_h", "人均经验/h"], ["kills_h", "人均击杀/h"], ["gold_h", "人均金币+掉落/h"], ["deaths_h", "人均死亡/h"]], wrows);

  // ---- 全服 ----
  const S = D.server;
  Viz.bar($("cSrvGold"), { x: S.map(r => r.n + " 人"), series: [{ name: "全服每日金币净流入", ci: 3, values: S.map(r => r.gold_day) }], height: 240, yLabel: "金币/天" });
  Viz.bar($("cSrvExp"), { x: S.map(r => r.n + " 人"), series: [{ name: "人均每小时经验（各等级平均）", ci: 0, values: S.map(r => r.exp_h) }], height: 240, yLabel: "经验/小时" });
  opt($("srvN"), S.map(r => [r.n, r.n + " 人"]), String(S[S.length - 1].n));
  on(() => { const r = S.find(x => String(x.n) === $("srvN").value); table($("srvItems"), [["item", "物品"], ["day", "全服每日产出（件）"]], Object.entries(r.items).map(([item, day]) => ({ item, day }))); }, "srvN");

  // ---- 对比 ----
  if (C) {
    $("cmpBox").innerHTML = `主数据树 <b>${M.tree}</b>（实线）对比 <b>${C.label}</b>（虚线）：商店装、单人累计升级小时。对比树 GM 体检 ${C.findings.length} 条。`;
    opt($("cJob"), jobOpts, "0");
    on(() => { const j = $("cJob").value; const a = D.prog[j].shop["1"], b = C.prog[j].shop["1"]; Viz.line($("cCmp"), { x: a.map(r => r.level + 1), xName: "升到 Lv", series: [{ name: M.tree, ci: 0, values: a.map(r => r.cum_hours) }, { name: C.label, ci: 1, dash: true, values: b.map(r => r.cum_hours) }], ylog: true, yLabel: "累计小时（对数）", height: 300 }); }, "cJob");
  } else { $("cmpBox").innerHTML = "未指定对比树。运行 <code>python balance/run.py report --compare 176</code> 叠加另一棵数据树。"; $("cJob").parentElement.style.display = "none"; }

  $("methodBox").innerHTML = `<ul>
<li><b>引擎 A（单挑）</b>：无界面启动真实 GameService，虚拟时钟下由在线托管 AutoPilot（与离线修炼/机器人同一内核）对单只怪战斗；伤害、命中、技能、喝药、风筝、武器/装备磨损、技能修炼点全部走服务端代码。每个「职业×等级×装备档×怪」8 场，取平均击杀秒数。</li>
<li><b>引擎 B（真实地图）</b>：真实地图 + MonGen 刷怪与重生 + N 个托管角色集中在该等级单挑最优怪的最密刷怪点，跑 10 游戏分钟。η = Σ(击杀×单挑秒数) / 总时长，用于折算寻路/找怪损耗；被围殴致死频繁的场景不参与 η 校准。</li>
<li><b>升级</b>：每级在死亡率 ≤10% 的怪里取经验/小时最高者；经验含等级惩罚（玩家 ≥ 怪+10 级每级扣 1/15）与 game_rates.json 倍率；不含组队、周末、离线修炼。</li>
<li><b>供给上限</b>：N 人在线时同等级段按 25% 计；一种怪全服每小时最多被杀 Σ(数量×3600/刷新秒)，按人头均分。</li>
<li><b>经济</b>：收入 = 爆金期望 + 掉落按 NPC 回收价（耐久 60% 期望）；支出 = 实测药水×商店价 + 实测磨损×真实修理价 + 护身符。未计回城卷、技能书、死亡掉落。</li>
<li><b>装备档</b>：用真实 recompute 逐槽贪心选「穿得上的」最高分装备（职业主属性 + 防御）；商店 = NPC 可买；最强 = 商店 ∪ 有刷新怪可掉落。</li>
<li><b>局限</b>：法师/道士的走位、宝宝、组队配合以托管内核水平为准（偏保守）；真实玩家可能更强也可能更弱。人数场景是放大压力测试，不是在线预测。</li></ul>`;
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => resizers.forEach(fn => fn()), 150); });
})();
