# -*- coding: utf-8 -*-
"""传奇数据库静态站点：样式表与前端脚本（gen_website.py 的配套资源）。"""

CSS = """
:root{--fg:#222;--dim:#777;--line:#e3e3e3;--bg:#faf9f7;--card:#fff;
  --accent:#8a4b2a;--accent2:#f3ece6;--chip:#f6f2ee}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.6 "Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{position:sticky;top:0;z-index:10;background:#fff;border-bottom:1px solid var(--line)}
.wrap{max-width:1180px;margin:0 auto;padding:0 18px}
nav{display:flex;gap:2px;align-items:center;height:48px;flex-wrap:wrap}
nav b{font-size:16px;margin-right:14px;color:var(--accent)}
nav a{padding:6px 10px;border-radius:6px;color:#444}
nav a.on,nav a:hover{background:var(--accent2);color:var(--accent);text-decoration:none}
main{padding:22px 0 60px}
h1{font-size:22px;margin:8px 0 4px}
h2{font-size:17px;margin:26px 0 10px;border-left:4px solid var(--accent);padding-left:9px}
h3{font-size:15px;margin:18px 0 8px}
.sub{color:var(--dim);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.card .n{font-size:26px;font-weight:700;color:var(--accent)}
.toolbar{display:flex;gap:10px;align-items:center;margin:12px 0;flex-wrap:wrap}
.toolbar input{flex:1;min-width:220px;padding:8px 12px;border:1px solid var(--line);
  border-radius:8px;font-size:14px;background:#fff}
.tabs{display:flex;gap:6px;flex-wrap:wrap}
.tab{padding:5px 13px;border:1px solid var(--line);border-radius:999px;cursor:pointer;
  font-size:13px;background:#fff;user-select:none}
.tab.on{background:var(--accent);color:#fff;border-color:var(--accent)}
/* 注意：table 上不能加 overflow:hidden —— 会让表格成为 sticky 的滚动容器，
   粘性表头被下推 48px 并盖住首行（Chrome 实测） */
table{width:100%;border-collapse:collapse;background:var(--card);
  border:1px solid var(--line);border-radius:10px;font-size:14px}
th{position:sticky;top:48px;background:#f1ede9;text-align:left;padding:8px 10px;
  font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}
thead th:first-child{border-top-left-radius:10px}
thead th:last-child{border-top-right-radius:10px}
th:hover{color:var(--accent)}
td{padding:6px 10px;border-top:1px solid var(--line);vertical-align:middle}
tbody tr:hover{background:#fcf7f2}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.icon{width:32px;height:32px;object-fit:contain;image-rendering:pixelated;
  vertical-align:middle;background:repeating-conic-gradient(#eee 0 25%,#fff 0 50%) 0 0/12px 12px;border-radius:4px}
.icon.sm{width:24px;height:24px}
.icon.lg{width:44px;height:44px}
.chip{display:inline-block;margin:2px 4px 2px 0;padding:1px 9px;border-radius:999px;
  background:var(--chip);border:1px solid var(--line);font-size:13px;white-space:nowrap}
.chip i{font-style:normal;color:var(--dim);font-size:12px;margin-left:4px}
.attr{font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:Consolas,Menlo,monospace;font-size:13px}
.warn{color:#b3541e}
.empty{color:var(--dim);padding:18px;text-align:center}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.two{grid-template-columns:1fr}}
footer{color:var(--dim);font-size:12px;border-top:1px solid var(--line);padding:16px 0;margin-top:30px}
.back{font-size:13px}
th.wico{width:46px}
#gresults{max-height:56vh;overflow:auto;border:1px solid var(--line);
  border-radius:10px;padding:6px 16px;background:var(--card);margin:0}
#gresults li{padding:3px 0}
"""

JS = """
function qs(name){const m=new URLSearchParams(location.search);return m.get(name)||''}
// 搜索框 + 分类 Tab 联合过滤（两态合一，互不覆盖）
function bindList(){
  const inp=document.querySelector('#filter');
  const rows=[...document.querySelectorAll('tbody[data-row] tr[data-row]')];
  if(!inp||!rows.length) return;
  let cat='all';
  const apply=()=>{
    const q=inp.value.trim().toLowerCase();
    rows.forEach(r=>{
      const okQ=!q||(r.dataset.search||'').includes(q);
      const okC=cat==='all'||r.dataset.cat===cat;
      r.style.display=(okQ&&okC)?'':'none';
    });
  };
  inp.addEventListener('input',apply);
  const bar=document.querySelector('.tabs');
  if(bar){
    const tabs=[...bar.querySelectorAll('.tab')];
    tabs.forEach(t=>t.addEventListener('click',()=>{
      tabs.forEach(x=>x.classList.toggle('on',x===t));
      cat=t.dataset.cat; apply();
    }));
  }
  const q=qs('q'); if(q){inp.value=q;}
  apply();
}
function bindSort(tableSel){
  const tb=document.querySelector(tableSel); if(!tb) return;
  tb.querySelectorAll('th[data-k]').forEach((th,i)=>{
    let asc=true;
    th.addEventListener('click',()=>{
      const body=tb.querySelector('tbody');
      const rows=[...body.rows];
      const k=th.dataset.k;
      const val=r=>{const c=r.cells[i];const v=c.dataset.v!==undefined?c.dataset.v:c.textContent.trim();
        const n=parseFloat(v.replace(/[^0-9.\\-]/g,''));return isNaN(n)?v:n;};
      rows.sort((a,b)=>{const x=val(a),y=val(b);
        const r=(typeof x==='number'&&typeof y==='number')?x-y:String(x).localeCompare(String(y));
        return asc?r:-r;});
      rows.forEach(r=>body.appendChild(r)); asc=!asc;
    });
  });
}
document.addEventListener('DOMContentLoaded',()=>{
  bindList();
  bindSort('table[data-sortable]');
  const gs=document.querySelector('#gsearch');
  if(gs){
    gs.addEventListener('input',()=>{
      const q=gs.value.trim().toLowerCase();
      document.querySelectorAll('#gresults li').forEach(li=>{
        li.style.display=(!q||li.dataset.k.includes(q))?'':'none';
      });
    });
  }
});
"""

NAV = [('index.html', '首页'), ('items.html', '物品'), ('skills.html', '技能'),
       ('monsters.html', '怪物'), ('maps.html', '地图'), ('npcs.html', 'NPC'),
       ('dist.html', '分布'), ('drops.html', '掉落')]


def page(title: str, body: str, page_file: str, depth: int = 0) -> str:
    """统一的页面外壳：导航 + 正文 + footer。depth=子目录层级（资源路径前缀）。"""
    pre = '../' * depth
    links = ''.join(
        '<a href="' + pre + f + ('" class="on"' if f == page_file else '"') + '>'
        + n + '</a>' for f, n in NAV)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · 传奇数据库</title>
<link rel="stylesheet" href="{pre}assets/style.css"></head>
<body>
<header><div class="wrap"><nav><b>传奇数据库</b>{links}</nav></div></header>
<main><div class="wrap">
{body}
<footer>数据由 scripts/gen_website.py 生成 · 静态站点，离线可用</footer>
</div></main>
<script src="{pre}assets/app.js"></script>
</body></html>"""