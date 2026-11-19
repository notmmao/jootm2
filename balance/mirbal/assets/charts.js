/* 极简图表库（零依赖）：line / bar（分组或正负堆叠）/ heat / scatter + 悬停提示 + 图例开关。 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const SER = n => css("--series-" + (((n) % 8) + 1));
  const el = (t, a, p) => { const e = document.createElementNS(NS, t); for (const k in a || {}) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };
  const fmtN = v => {
    if (v == null || !isFinite(v)) return "—";
    const a = Math.abs(v);
    if (Number.isInteger(v) && a < 1e4) return String(v);
    if (a >= 1e8) return (v / 1e8).toFixed(a >= 1e9 ? 0 : 1) + "亿";
    if (a >= 1e4) return (v / 1e4).toFixed(a >= 1e5 ? 0 : 1) + "万";
    if (a >= 100) return Math.round(v).toString();
    if (a >= 10) return v.toFixed(1);
    return v.toFixed(2);
  };
  let tip;
  function showTip(html, x, y) {
    if (!tip) { tip = document.createElement("div"); tip.className = "viz-tip"; document.body.appendChild(tip); }
    tip.innerHTML = html; tip.style.display = "block";
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    tip.style.left = left + "px"; tip.style.top = top + "px";
  }
  function hideTip() { if (tip) tip.style.display = "none"; }

  function niceTicks(lo, hi, n) {
    if (hi <= lo) hi = lo + 1;
    const span = hi - lo, step0 = Math.pow(10, Math.floor(Math.log10(span / n)));
    const step = [1, 2, 5, 10].map(m => m * step0).find(s => span / s <= n) || step0 * 10;
    const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(v);
    return out;
  }
  function scale(d0, d1, r0, r1, log) {
    if (log) { const a = Math.log10(Math.max(d0, 1e-9)), b = Math.log10(Math.max(d1, 1e-9)); return v => r0 + (Math.log10(Math.max(v, 1e-9)) - a) / (b - a || 1) * (r1 - r0); }
    return v => r0 + (v - d0) / ((d1 - d0) || 1) * (r1 - r0);
  }
  function frame(host, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 720, H = o.height || 300;
    const M = { l: 58, r: 16, t: 24, b: o.xRot ? 70 : 34 };
    const svg = el("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, class: "viz" }, host);
    return { svg, W, H, M, iw: W - M.l - M.r, ih: H - M.t - M.b };
  }
  function legend(host, series, hidden, redraw) {
    if (series.length < 2) return;
    const lg = document.createElement("div"); lg.className = "viz-legend";
    series.forEach((s, i) => {
      const b = document.createElement("button"); b.type = "button";
      b.className = "lg" + (hidden.has(s.name) ? " off" : "");
      b.innerHTML = `<i style="background:${s.color || SER(s.ci ?? i)}"></i>${s.name}`;
      b.onclick = () => { hidden.has(s.name) ? hidden.delete(s.name) : hidden.add(s.name); redraw(); };
      lg.appendChild(b);
    });
    host.prepend(lg);
  }
  function yAxis(g, f, ticks, y, fmt) {
    ticks.forEach(t => {
      el("line", { x1: f.M.l, x2: f.W - f.M.r, y1: y(t), y2: y(t), class: "grid" }, g);
      const tx = el("text", { x: f.M.l - 6, y: y(t) + 4, "text-anchor": "end", class: "tick" }, g); tx.textContent = (fmt || fmtN)(t);
    });
  }
  function logTicks(lo, hi) { const out = []; for (let e = Math.floor(Math.log10(Math.max(lo, 1e-9))); e <= Math.ceil(Math.log10(hi)); e++) out.push(Math.pow(10, e)); return out; }

  /* line: {x:[..], series:[{name, values, ci}], ylog, yLabel, xLabel, fmt} */
  function line(host, o) {
    const hidden = new Set(o.hidden || []);
    const draw = () => {
      const f = frame(host, o), g = el("g", {}, f.svg);
      const vis = o.series.filter(s => !hidden.has(s.name));
      const all = vis.flatMap(s => s.values).filter(v => v != null && isFinite(v) && (!o.ylog || v > 0));
      let lo = o.ymin ?? (o.ylog ? Math.min(...all) : Math.min(0, ...all)), hi = Math.max(...all, o.ymin ?? 0);
      if (!all.length) { lo = 0; hi = 1; }
      const ticks = o.ylog ? logTicks(lo, hi) : niceTicks(lo, hi, 5);
      if (o.ylog) { lo = ticks[0]; hi = ticks[ticks.length - 1]; } else { lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]); }
      const x = scale(0, o.x.length - 1, f.M.l, f.W - f.M.r), y = scale(lo, hi, f.H - f.M.b, f.M.t, o.ylog);
      yAxis(g, f, ticks, y, o.fmt);
      const every = Math.ceil(o.x.length / 14);
      o.x.forEach((v, i) => { if (i % every) return; const t = el("text", { x: x(i), y: f.H - f.M.b + 16, "text-anchor": "middle", class: "tick" }, g); t.textContent = v; });
      if (o.xLabel) { const t = el("text", { x: f.W - f.M.r, y: f.H - 4, "text-anchor": "end", class: "axl" }, g); t.textContent = o.xLabel; }
      if (o.yLabel) { const t = el("text", { x: 4, y: 11, class: "axl" }, g); t.textContent = o.yLabel; }
      (o.marks || []).forEach(mk => { const xi = o.x.indexOf(mk.x); if (xi < 0) return; el("line", { x1: x(xi), x2: x(xi), y1: f.M.t, y2: f.H - f.M.b, class: "mark" }, g); const t = el("text", { x: x(xi) + 3, y: f.M.t + 10, class: "axl" }, g); t.textContent = mk.label; });
      vis.forEach(s => {
        const idx = o.series.indexOf(s), col = s.color || SER(s.ci ?? idx);
        let d = "", pen = false;
        s.values.forEach((v, i) => { if (v == null || !isFinite(v) || (o.ylog && v <= 0)) { pen = false; return; } d += (pen ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1); pen = true; });
        el("path", { d, fill: "none", stroke: col, "stroke-width": 2, "stroke-dasharray": s.dash ? "5 4" : "", "stroke-linejoin": "round" }, g);
      });
      const cross = el("line", { y1: f.M.t, y2: f.H - f.M.b, class: "cross", visibility: "hidden" }, g);
      const hit = el("rect", { x: f.M.l, y: f.M.t, width: f.iw, height: f.ih, fill: "transparent" }, g);
      hit.addEventListener("mousemove", ev => {
        const r = f.svg.getBoundingClientRect(); const i = Math.max(0, Math.min(o.x.length - 1, Math.round((ev.clientX - r.left - f.M.l) / (f.iw / Math.max(1, o.x.length - 1)))));
        cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
        let h = `<b>${o.xName || ""}${o.x[i]}</b>`;
        vis.forEach(s => { const idx = o.series.indexOf(s); h += `<div><i style="background:${s.color || SER(s.ci ?? idx)}"></i>${s.name}：${(o.fmt || fmtN)(s.values[i])}${s.notes && s.notes[i] ? " · " + s.notes[i] : ""}</div>`; });
        showTip(h, ev.clientX, ev.clientY);
      });
      hit.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
      legend(host, o.series, hidden, draw);
    };
    draw(); return draw;
  }

  /* bar: {x:[..], series:[{name, values}], stacked, fmt, notes} 正负值分开堆叠 */
  function bar(host, o) {
    const hidden = new Set();
    const draw = () => {
      const f = frame(host, o), g = el("g", {}, f.svg);
      const vis = o.series.filter(s => !hidden.has(s.name));
      let lo = 0, hi = 0;
      o.x.forEach((_, i) => {
        if (o.stacked) { let p = 0, n = 0; vis.forEach(s => { const v = s.values[i] || 0; v >= 0 ? p += v : n += v; }); hi = Math.max(hi, p); lo = Math.min(lo, n); }
        else vis.forEach(s => { const v = s.values[i] || 0; hi = Math.max(hi, v); lo = Math.min(lo, v); });
      });
      if (o.extraLine) o.extraLine.values.forEach(v => { if (v != null) { hi = Math.max(hi, v); lo = Math.min(lo, v); } });
      const ticks = niceTicks(lo, hi, 5); lo = Math.min(lo, ticks[0]); hi = Math.max(hi, ticks[ticks.length - 1]);
      const y = scale(lo, hi, f.H - f.M.b, f.M.t);
      yAxis(g, f, ticks, y, o.fmt);
      el("line", { x1: f.M.l, x2: f.W - f.M.r, y1: y(0), y2: y(0), class: "zero" }, g);
      const bw = f.iw / o.x.length, gap = 2;
      const every = Math.ceil(o.x.length / (o.xRot ? 40 : 16));
      o.x.forEach((v, i) => {
        if (!(i % every)) {
          const cx = f.M.l + bw * (i + .5);
          const t = el("text", o.xRot ? { x: cx, y: f.H - f.M.b + 10, "text-anchor": "end", transform: `rotate(-45 ${cx} ${f.H - f.M.b + 10})`, class: "tick" } : { x: cx, y: f.H - f.M.b + 16, "text-anchor": "middle", class: "tick" }, g); t.textContent = v;
        }
        let p = 0, n = 0;
        vis.forEach((s, k) => {
          const idx = o.series.indexOf(s), val = s.values[i] || 0, col = s.color || SER(s.ci ?? idx);
          let x0, w, y0, y1;
          if (o.stacked) { x0 = f.M.l + bw * i + gap; w = Math.max(1, bw - 2 * gap); if (val >= 0) { y0 = y(p + val); y1 = y(p); p += val; } else { y0 = y(n); y1 = y(n + val); n += val; } }
          else { const sw = (bw - 2 * gap) / vis.length; x0 = f.M.l + bw * i + gap + sw * k; w = Math.max(1, sw - 1); y0 = y(Math.max(0, val)); y1 = y(Math.min(0, val)); }
          const r = el("rect", { x: x0, y: y0, width: w, height: Math.max(0, y1 - y0 - (o.stacked ? 1 : 0)), fill: col, rx: Math.min(3, w / 3) }, g);
          r.addEventListener("mousemove", ev => showTip(`<b>${o.xName || ""}${o.x[i]}</b><div><i style="background:${col}"></i>${s.name}：${(o.fmt || fmtN)(val)}</div>${o.notes && o.notes[i] ? "<div class='mut'>" + o.notes[i] + "</div>" : ""}`, ev.clientX, ev.clientY));
          r.addEventListener("mouseleave", hideTip);
        });
      });
      if (o.extraLine) {
        let d = ""; o.extraLine.values.forEach((v, i) => { if (v == null) return; d += (d ? "L" : "M") + (f.M.l + bw * (i + .5)).toFixed(1) + "," + y(v).toFixed(1); });
        el("path", { d, fill: "none", stroke: css("--text-primary"), "stroke-width": 2 }, g);
        o.extraLine.values.forEach((v, i) => { if (v == null) return; const c = el("circle", { cx: f.M.l + bw * (i + .5), cy: y(v), r: 4, fill: css("--text-primary"), stroke: css("--surface-1"), "stroke-width": 2 }, g); c.addEventListener("mousemove", ev => showTip(`<b>${o.xName || ""}${o.x[i]}</b><div>${o.extraLine.name}：${(o.fmt || fmtN)(v)}</div>`, ev.clientX, ev.clientY)); c.addEventListener("mouseleave", hideTip); });
      }
      if (o.yLabel) { const t = el("text", { x: 4, y: 11, class: "axl" }, g); t.textContent = o.yLabel; }
      legend(host, o.series.concat(o.extraLine ? [{ name: o.extraLine.name, color: css("--text-primary") }] : []), hidden, draw);
    };
    draw(); return draw;
  }

  /* heat: {xs, ys, v:[[..]] (ys×xs), fmt, cellNote(i,j)} 单色顺序色阶（log） */
  function heat(host, o) {
    host.innerHTML = "";
    const W = host.clientWidth || 720, cell = Math.max(8, Math.min(26, (W - 120) / o.xs.length));
    const ch = o.cellH || 18, M = { l: 120, t: 26 };
    const H = M.t + ch * o.ys.length + 30;
    const svg = el("svg", { width: W, height: H, class: "viz" }, host);
    const vals = o.v.flat().filter(v => v != null && v > 0);
    const sorted = vals.slice().sort((a, b) => a - b);
    const lo = Math.log10(sorted[Math.floor(sorted.length * 0.05)] || 1), hi = Math.log10(sorted[sorted.length - 1] || 10);
    const ramp = ["--seq-100", "--seq-200", "--seq-300", "--seq-400", "--seq-500", "--seq-600", "--seq-700"].map(css);
    const col = v => { if (v == null || v <= 0) return css("--surface-2"); const t = Math.max(0, (Math.log10(v) - lo) / ((hi - lo) || 1)); return ramp[Math.min(ramp.length - 1, Math.floor(t * ramp.length))]; };
    const every = Math.ceil(o.xs.length / ((W - 120) / 26));
    o.xs.forEach((x, i) => { if (i % every) return; const t = el("text", { x: M.l + cell * (i + .5), y: M.t - 8, "text-anchor": "middle", class: "tick" }, svg); t.textContent = x; });
    o.ys.forEach((yv, j) => {
      const t = el("text", { x: M.l - 6, y: M.t + ch * (j + .5) + 4, "text-anchor": "end", class: "tick" }, svg); t.textContent = yv;
      o.xs.forEach((x, i) => {
        const v = o.v[j][i];
        const r = el("rect", { x: M.l + cell * i + 1, y: M.t + ch * j + 1, width: cell - 2, height: ch - 2, fill: col(v), rx: 2 }, svg);
        r.addEventListener("mousemove", ev => showTip(`<b>${yv} · ${o.xName || ""}${x}</b><div>${o.label || "值"}：${(o.fmt || fmtN)(v)}</div>${o.note ? "<div class='mut'>" + (o.note(j, i) || "") + "</div>" : ""}`, ev.clientX, ev.clientY));
        r.addEventListener("mouseleave", hideTip);
      });
    });
    const lg = el("g", {}, svg); const lx = M.l, ly = H - 18;
    ramp.forEach((c, k) => el("rect", { x: lx + k * 28, y: ly, width: 26, height: 8, fill: c, rx: 2 }, lg));
    const a = el("text", { x: lx - 6, y: ly + 8, "text-anchor": "end", class: "tick" }, lg); a.textContent = fmtN(Math.pow(10, lo));
    const b = el("text", { x: lx + ramp.length * 28 + 6, y: ly + 8, class: "tick" }, lg); b.textContent = fmtN(Math.pow(10, hi)) + "（对数色阶，灰 = 无数据/打不过）";
  }

  /* scatter: {points:[{x,y,label,g}], groups:[name..], xlog, ylog, xLabel, yLabel} */
  function scatter(host, o) {
    const hidden = new Set();
    const draw = () => {
      const f = frame(host, o), g = el("g", {}, f.svg);
      const pts = o.points.filter(p => !hidden.has(o.groups[p.g]) && p.x > 0 && p.y > 0);
      const xs = pts.map(p => p.x), ys = pts.map(p => p.y);
      const xt = o.xlog ? logTicks(Math.min(...xs), Math.max(...xs)) : niceTicks(Math.min(0, ...xs), Math.max(...xs), 6);
      const yt = o.ylog ? logTicks(Math.min(...ys), Math.max(...ys)) : niceTicks(Math.min(0, ...ys), Math.max(...ys), 5);
      const x = scale(xt[0], xt[xt.length - 1], f.M.l, f.W - f.M.r, o.xlog), y = scale(yt[0], yt[yt.length - 1], f.H - f.M.b, f.M.t, o.ylog);
      yAxis(g, f, yt, y);
      xt.forEach(t => { const tx = el("text", { x: x(t), y: f.H - f.M.b + 16, "text-anchor": "middle", class: "tick" }, g); tx.textContent = fmtN(t); });
      if (o.xLabel) { const t = el("text", { x: f.W - f.M.r, y: f.H - 4, "text-anchor": "end", class: "axl" }, g); t.textContent = o.xLabel; }
      if (o.yLabel) { const t = el("text", { x: 4, y: 11, class: "axl" }, g); t.textContent = o.yLabel; }
      pts.forEach(p => {
        const c = el("circle", { cx: x(p.x), cy: y(p.y), r: p.r || 5, fill: SER(p.g), "fill-opacity": .8, stroke: css("--surface-1"), "stroke-width": 1.5 }, g);
        c.addEventListener("mousemove", ev => showTip(`<b>${p.label}</b><div>${o.xLabel}：${fmtN(p.x)}</div><div>${o.yLabel}：${fmtN(p.y)}</div>${p.note ? "<div class='mut'>" + p.note + "</div>" : ""}`, ev.clientX, ev.clientY));
        c.addEventListener("mouseleave", hideTip);
        if (p.tag) { const t = el("text", { x: x(p.x) + 7, y: y(p.y) - 6, class: "lbl" }, g); t.textContent = p.label; }
      });
      legend(host, o.groups.map((n, i) => ({ name: n, ci: i })), hidden, draw);
    };
    draw(); return draw;
  }
  window.Viz = { line, bar, heat, scatter, fmtN };
})();
