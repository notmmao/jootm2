# -*- coding: utf-8 -*-
"""一键生成传奇数据库静态站点（数据 = 任意 gamedata 树，默认当前生效树）。

用法：
  python scripts/gen_website.py                     # 用生效树，输出 data/website/
  python scripts/gen_website.py --tree 176 --out data/website-176   # 副本树各存一份
  python scripts/gen_website.py --open              # 生成后打开首页

产物：纯静态 HTML + assets/（style.css、app.js、图标/地图 PNG），file:// 直接可看。
图标全部本地生成：物品=Items.wil 帧（Looks）；技能=MagIcon.wil 帧（Effect×2，
依据 Delphi 客户端 FState.pas `icon := pm.Def.btEffect * 2`）；怪物=npc/Mon WIL
站姿帧（规则见 scripts/render_maps.py）；地图=scripts/render_maps.py 渲染的
地形图（含怪物刷新点标记）。
"""
from __future__ import annotations

import argparse
import collections
import csv
import html
import re
import struct
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import website_assets as A                      # noqa: E402
import render_maps as RM                        # noqa: E402
from wil_extract import WIL, _png               # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                   # 供 compute_npc_goods 导入 pyserver

ITEM_CAT: dict[int, str] = {5: '武器', 6: '武器', 4: '书'}
ITEM_SUB = {
    0: '药水', 1: '食物', 2: '特殊', 3: '卷轴', 4: '技能书', 5: '武器', 6: '武器',
    7: '传输', 10: '衣服(男)', 11: '衣服(女)', 15: '头盔', 16: '斗笠',
    19: '项链', 20: '项链', 21: '项链', 22: '戒指', 23: '戒指', 24: '手镯',
    25: '护身符', 26: '手镯', 30: '右手', 31: '药包', 36: '书信', 40: '肉类',
    41: '特殊', 42: '材料', 43: '矿石', 44: '材料', 45: '特殊', 46: '凭证',
    47: '货币', 50: '书信', 52: '靴子', 53: '护符', 54: '腰带',
}
for _m in (10, 11, 15, 16, 19, 20, 21, 22, 23, 24, 25, 26, 52, 53, 54):
    ITEM_CAT[_m] = '装备'
for _m in (0, 1, 2, 3, 7, 31, 40):
    ITEM_CAT[_m] = '消耗品'

JOB_NAME = {0: '战士', 1: '法师', 2: '道士'}
NEED_NAME = {0: '等级', 1: '攻击力', 2: '魔法力', 3: '道术力'}
E = html.escape


def norm_item(s: str) -> str:
    return s.replace('（', '(').replace('）', ')').replace(' ', '')


def safe_name(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', s).strip() or '_'


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def i(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return default


def decode(p: Path) -> str:
    raw = p.read_bytes()
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('gbk', 'replace')


# ---------------------------------------------------------------- 数据装载

class Data:
    def __init__(self, tree: Path):
        self.tree = tree
        self.envir = tree / 'Mir200' / 'Envir'
        self.items = load_csv(tree / 'mud2' / 'DB' / 'StdItems.csv')
        self.skills = load_csv(tree / 'mud2' / 'DB' / 'Magic.csv')
        self.monsters = load_csv(tree / 'mud2' / 'DB' / 'Monster.csv')
        self._load_maps()
        self._load_gen()
        self._load_drops()
        self._load_npcs()
        self.item_by_name = {norm_item(r['Name']): r for r in self.items}
        self.mon_by_name = {r['Name'].strip(): r for r in self.monsters}

    def _load_maps(self) -> None:
        self.map_title: dict[str, str] = {}
        self.gates: list[tuple[str, int, int, str, int, int]] = []
        for ln in decode(self.envir / 'MapInfo.txt').splitlines():
            ln = ln.strip()
            if ln.startswith(';'):                  # 注释行（含 ";->" 形式的废弃传送点）
                continue
            if ln.startswith('['):
                close = ln.find(']')
                if close < 0:
                    continue
                kv = ln[1:close].strip().split(None, 1)
                self.map_title[kv[0]] = kv[1].strip() if len(kv) > 1 else kv[0]
            elif '->' in ln:
                left, right = ln.split('->', 1)
                s, d = left.strip().split(), right.strip().split()
                if len(s) >= 2 and len(d) >= 2 and ',' in s[1] and ',' in d[1]:
                    sx, sy = (i(x) for x in s[1].split(',', 1))
                    dx, dy = (i(x) for x in d[1].split(',', 1))
                    self.gates.append((s[0], sx, sy, d[0], dx, dy))
        self.gates_from: dict[str, list] = collections.defaultdict(list)
        self.gates_to: dict[str, list] = collections.defaultdict(list)
        for g in self.gates:
            self.gates_from[g[0]].append(g)
            self.gates_to[g[3]].append(g)
        self.map_size: dict[str, tuple[int, int]] = {}
        mdir = ROOT / 'resources' / 'Map'
        if mdir.is_dir():
            lower = {p.name.lower(): p for p in mdir.glob('*.map')}
            for mid in self.map_title:
                p = lower.get(f'{mid}.map'.lower())
                if p is None:
                    continue
                with p.open('rb') as f:
                    head = f.read(4)
                if len(head) == 4:
                    w, h = struct.unpack_from('<HH', head, 0)
                    if w and h:
                        self.map_size[mid] = (w, h)

    def _load_gen(self) -> None:
        self.spawns: list[tuple[str, str, int, int, int]] = []   # map 怪 数量 x y
        for ln in decode(self.envir / 'MonGen.txt').splitlines():
            p = ln.split()
            if len(p) >= 6 and not ln.lstrip().startswith(';'):
                self.spawns.append((p[0], p[3], i(p[5]), i(p[1]), i(p[2])))
        self.mon_spawn: dict[str, list[tuple[str, int, int, int]]] = \
            collections.defaultdict(list)
        self.mon_map_total: dict[str, dict[str, int]] = collections.defaultdict(
            lambda: collections.defaultdict(int))
        self.map_spawn: dict[str, dict[str, int]] = collections.defaultdict(
            lambda: collections.defaultdict(int))
        for mid, mon, cnt, x, y in self.spawns:
            self.mon_spawn[mon].append((mid, x, y, cnt))
            self.mon_map_total[mon][mid] += cnt
            self.map_spawn[mid][mon] += cnt

    def _load_drops(self) -> None:
        self.drops: dict[str, list[tuple[str, str, int]]] = {}
        d = self.envir / 'MonItems'
        if d.is_dir():
            for p in sorted(d.glob('*.txt')):
                entries = []
                for ln in decode(p).splitlines():
                    t = ln.strip()
                    if not t or t.startswith(';'):
                        continue
                    tk = t.split()
                    if len(tk) >= 2 and '/' in tk[0]:
                        entries.append((tk[0], tk[1], i(tk[2], 1) if len(tk) > 2 else 1))
                if entries:
                    self.drops[p.stem.strip()] = entries
        self.item_dropped_by: dict[str, list[tuple[str, str]]] = \
            collections.defaultdict(list)
        for mon, entries in self.drops.items():
            for rate, item, _qty in entries:
                self.item_dropped_by[norm_item(item)].append((mon, rate))

    def _load_npcs(self) -> None:
        """NPC 全量列表（列义同 pyserver/world_extra.load_npc_spawns）。

        Npcs.txt:    名称 代码 地图 x y 范围 图标
        MerChant.txt: 脚本 地图 x y 名称 数量 图标 刷新时间
        """
        self.npcs: list[dict] = []
        self.map_npcs: dict[str, list[tuple[str, int, int]]] = \
            collections.defaultdict(list)

        def rows(name: str, merchant: bool) -> None:
            p = self.envir / name
            if not p.exists():
                return
            for ln in decode(p).splitlines():
                t = ln.strip()
                if not t or t.startswith(';'):
                    continue
                tk = t.split()
                if len(tk) < 7:
                    continue
                rec = dict(merchant=merchant, appr=i(tk[6]))
                if merchant:
                    rec.update(name=tk[4], script=tk[0], map=tk[1],
                               x=i(tk[2]), y=i(tk[3]))
                else:
                    rec.update(name=tk[0], script=tk[0], map=tk[2],
                               x=i(tk[3]), y=i(tk[4]))
                self.npcs.append(rec)
                self.map_npcs[rec['map']].append((rec['name'], rec['x'], rec['y']))
        rows('Npcs.txt', False)
        rows('MerChant.txt', True)
        self.npc_by_name: dict[str, list[dict]] = collections.defaultdict(list)
        for r in self.npcs:
            self.npc_by_name[r['name']].append(r)

    def compute_npc_goods(self) -> None:
        """按服务端同款逻辑算出 NPC 售卖物（含 Gee 子脚本回退与 1.1×倍率）。"""
        from types import SimpleNamespace
        from pyserver.npc_script import load_script, NpcScriptEngine

        def item_tpl(name):
            for r in self.items:
                if r['Name'] == name:
                    return SimpleNamespace(index=i(r['Idx']), name=name, price=i(r['Price']))
            return None

        world = SimpleNamespace(config=SimpleNamespace(envir_dir=self.envir),
                                item_template_by_name=item_tpl)
        engine = NpcScriptEngine(world)
        cache: dict[tuple[str, str, bool], list[dict]] = {}
        self.npc_goods: dict[str, list[dict]] = {}
        for r in self.npcs:
            key = (r['script'], r['map'], r['merchant'])
            if key not in cache:
                d = self.envir / ('Market_Def' if r['merchant'] else 'Npc_Def')
                script = load_script(d, r['script'], r['map'])
                cache[key] = engine.goods(SimpleNamespace(script=script)) if script else []
            if cache[key]:
                merged = self.npc_goods.setdefault(r['name'], [])
                seen = {(g['name'], g['price']) for g in merged}
                merged.extend(g for g in cache[key] if (g['name'], g['price']) not in seen)

    # -- 显示口径
    def item_cat(self, r: dict) -> tuple[str, str]:
        m = i(r['StdMode'])
        return ITEM_CAT.get(m, '杂项'), ITEM_SUB.get(m, f'类型{m}')

    @staticmethod
    def item_attr(r: dict) -> str:
        if i(r['StdMode']) in (0, 1):
            # 药水/食物：AC = 回血值，MAC = 回魔值（原版药水宏的定义，不是防御）
            heal, mana = i(r['AcMin']), i(r['MacMin'])
            return ' · '.join(x for x in
                              (f'回血 {heal}' if heal else '',
                               f'回魔 {mana}' if mana else '') if x) or '—'
        parts = []
        for label, a, b in (('防', 'AcMin', 'AcMax'), ('魔防', 'MacMin', 'MacMax'),
                            ('攻', 'DcMin', 'DcMax'), ('魔', 'McMin', 'McMax'),
                            ('道', 'ScMin', 'ScMax')):
            lo, hi = i(r[a]), i(r[b])
            if lo or hi:
                parts.append(f'{label} {lo}-{hi}' if lo != hi else f'{label} {lo}')
        return ' · '.join(parts) or '—'

    @staticmethod
    def item_need(r: dict) -> str:
        need, lv = i(r['Need']), i(r['NeedLevel'])
        return f'{NEED_NAME.get(need, "需求" + str(need))} {lv}' if lv else ''

    def map_name(self, mid: str) -> str:
        return self.map_title.get(mid) or f'图{mid}'


# ---------------------------------------------------------------- 图标

NPC_PLACEHOLDER = set()
_am = re.search(r'NPC_PLACEHOLDER_APPR = new Set\(\[(.*?)\]\)',
                (ROOT / 'frontend' / 'actor' / 'actorData.mjs').read_text(encoding='utf-8'),
                re.S)
if _am:
    NPC_PLACEHOLDER = {int(x) for x in re.findall(r'\d+', _am.group(1))}


def npc_frames(appr: int) -> tuple[int, int]:
    """NPC 帧：返回 (段首帧, 站姿 dir=4 帧号)。规则照 actorData.mjs getNpcOffset/npcAction。"""
    if appr in NPC_PLACEHOLDER:
        appr = 0
    if 0 <= appr <= 22:
        off = 60 * appr
    elif appr == 23:
        off = 1380
    else:
        off = 1470 + 60 * (appr - 24)
    ma = 'MA51' if appr == 23 else ('MA52' if appr in (24, 25) else 'MA50')
    st, fr, sk = RM.STANDS.get(ma, (0, 4, 6))
    return off, off + st + 4 * (fr + sk)


def sync_icons(d: Data, out: Path, log) -> None:
    ic = out / 'assets' / 'icons'
    for sub in ('items', 'skills', 'mobs', 'npc'):
        p = ic / sub
        p.mkdir(parents=True, exist_ok=True)
        for f in p.iterdir():                    # 清掉旧产物（含历史 ruchu8 图标）
            if f.is_file():
                f.unlink()
    if (ic / 'maps').is_dir():                   # 旧版地图缩略图目录，已被 assets/maps 取代
        for f in (ic / 'maps').iterdir():
            if f.is_file():
                f.unlink()
    d.icons: dict[str, set[int | str]] = {k: set() for k in ('items', 'skills', 'mobs', 'npc')}

    looks = sorted({i(r['Looks']) for r in d.items if i(r['Looks']) > 0})
    wil = WIL(ROOT / 'resources' / 'Data', 'Items')
    for lk in looks:
        r = wil.frame_png(lk)
        if r:
            (ic / 'items' / f'{lk}.png').write_bytes(r[0])
            d.icons['items'].add(lk)
    log(f'物品图标: Looks {len(looks)} 种 → 提取 {len(d.icons["items"])}')

    effects = sorted({i(r['Effect']) for r in d.skills})
    mag = WIL(ROOT / 'resources' / 'Data', 'MagIcon')
    for ef in effects:
        r = mag.frame_png(ef * 2)
        if r:
            (ic / 'skills' / f'{ef}.png').write_bytes(r[0])
            d.icons['skills'].add(ef)
    log(f'技能图标: Effect {len(effects)} 种 → 提取 {len(d.icons["skills"])}（帧=Effect×2）')

    # 怪物：appr → MonN.wil 站姿帧（raceImg 决定 stand 段，取同 appr 首个怪物）
    race_of: dict[int, int] = {}
    for r in d.monsters:
        ap = i(r['Appr'])
        if ap > 0:
            race_of.setdefault(ap, i(r['RaceImg']))
    for ap in sorted(race_of):
        icon = RM.monster_icon(ap, race_of[ap], 40, halo=False)
        if icon:
            (ic / 'mobs' / f'{ap}.png').write_bytes(_png(*icon))
            d.icons['mobs'].add(ap)
    log(f'怪物图标: Appr {len(race_of)} 种 → 本地提取 {len(d.icons["mobs"])}')

    # NPC：npc.wil 站姿帧（appr 占位图回退默认外观）
    npc_apprs = sorted({r['appr'] for r in d.npcs} | {0})
    for ap in npc_apprs:
        off, idx = npc_frames(ap)
        frame = RM.LIBS.frame_rgba('npc', idx) or RM.LIBS.frame_rgba('npc', off)
        if frame:
            (ic / 'npc' / f'{ap}.png').write_bytes(_png(*frame))
            d.icons['npc'].add(ap)
    log(f'NPC 图标: Appr {len(npc_apprs)} 种 → 提取 {len(d.icons["npc"])}')


def render_map_images(d: Data, out: Path, log, skip_existing: bool = False) -> None:
    """渲染 map_title 中每张有 .map 的地图（地形色块 + 怪物刷新点标记）。"""
    md = out / 'assets' / 'maps'
    md.mkdir(parents=True, exist_ok=True)
    by_id: dict[str, list[tuple[int, int, int, int]]] = collections.defaultdict(list)
    for mid, mon, _cnt, x, y in d.spawns:
        m = d.mon_by_name.get(mon)
        if m:
            by_id[mid].append((x, y, i(m['Appr']), i(m['RaceImg'])))
    d.maps_rendered: set[str] = set()
    for mid in d.map_title:
        target = md / f'{safe_name(mid)}.png'
        if skip_existing and target.exists():
            d.maps_rendered.add(mid)
            continue
        png = RM.render_map(mid, by_id.get(mid, []))
        if png:
            target.write_bytes(png)
            d.maps_rendered.add(mid)
    log(f'地图渲染图: {len(d.maps_rendered)} / {len(d.map_title)}')


# ---------------------------------------------------------------- 渲染辅助

NUM_HEAD = {'等级', 'HP', 'MP', '经验', '攻', '防', '魔防', '魔', '道', '重量', '耐久',
            '价格', '威力', '需求L1', '需求L2', '需求L3', '延迟', '数量', '刷怪',
            '序号', 'X', 'Y', '间隔'}


def ic_img(src, cls: str = 'icon') -> str:
    if not src:
        return '<span class="icon"></span>'
    return '<img class="' + cls + '" loading="lazy" src="' + src + '" alt="">'


def chip(text: str, note: str = '', href: str | None = None) -> str:
    inner = E(text) + ('<i>' + E(note) + '</i>' if note else '')
    tag = 'a href="' + href + '"' if href else 'span'
    close = 'a' if href else 'span'
    return f'<{tag} class="chip">{inner}</{close}>'


def table(headers: list[str], rows: list[str], row_attr: bool = True) -> str:
    ths = []
    for idx, h in enumerate(headers):
        cls = 'wico' if (idx == 0 and h == '') else ('num' if h in NUM_HEAD else '')
        ths.append('<th data-k="' + E(h) + '"'
                   + (f' class="{cls}"' if cls else '') + '>' + E(h) + '</th>')
    rtag = ' data-row' if row_attr else ''
    return ('<table data-sortable><thead><tr>' + ''.join(ths) + '</tr></thead>'
            '<tbody' + rtag + '>' + ''.join(rows) + '</tbody></table>')


def toolbar(placeholder: str, tabs: list[tuple[str, str]] | None = None) -> str:
    t = ''
    if tabs:
        t = '<div class="tabs">' + ''.join(
            '<span class="tab' + (' on' if k == 'all' else '') + '" data-cat="'
            + k + '">' + E(n) + '</span>' for k, n in tabs) + '</div>'
    return ('<div class="toolbar"><input id="filter" type="search" placeholder="'
            + E(placeholder) + '">' + t + '</div>')


def rel(path_from_root: str, depth: int) -> str:
    return '../' * depth + path_from_root


def map_link(d: Data, mid: str, prefix: str) -> str:
    """目标页存在才给链接（NPC/刷怪/传送表里常出现 MapInfo 未登记的图号）。"""
    if mid in d.map_title:
        return '<a href="' + prefix + safe_name(mid) + '.html">' + E(d.map_name(mid)) + '</a>'
    return E(d.map_name(mid))


def mon_link(d: Data, name: str, prefix: str) -> str:
    if name in d.mon_by_name:
        return '<a href="' + prefix + safe_name(name) + '.html">' + E(name) + '</a>'
    return E(name)


# ---------------------------------------------------------------- 页面

def page_index(d: Data, out: Path) -> None:
    counts = [('物品', len(d.items), 'items.html'), ('技能', len(d.skills), 'skills.html'),
              ('怪物', len(d.monsters), 'monsters.html'),
              ('地图', len(d.map_title), 'maps.html'),
              ('NPC', len(d.npc_by_name), 'npcs.html'),
              ('掉落表', len(d.drops), 'drops.html')]
    cards = ''.join('<a class="card" href="' + u + '"><div class="n">' + str(n)
                    + '</div><div>' + E(t) + '</div></a>' for t, n, u in counts)
    idx = []
    for r in d.items:
        cat, _ = d.item_cat(r)
        idx.append((r['Name'], cat + ' · 物品', 'items.html?q=' + r['Name']))
    for r in d.monsters:
        idx.append((r['Name'].strip(), '怪物', 'mon/' + safe_name(r['Name'].strip()) + '.html'))
    for mid, title in d.map_title.items():
        idx.append((title, '地图 · ' + mid, 'maps/' + safe_name(mid) + '.html'))
    for name in d.npc_by_name:
        idx.append((name, 'NPC', 'npc/' + safe_name(name) + '.html'))
    for r in d.skills:
        idx.append((r['MagName'], JOB_NAME.get(i(r['Job']), '通用') + ' · 技能',
                    'skills.html?q=' + r['MagName']))
    lis = ''.join('<li data-k="' + E(n.lower()) + '"><a href="' + u + '">' + E(n)
                  + '</a><span class="sub"> — ' + E(t) + '</span></li>'
                  for n, t, u in idx)
    body = f"""
<h1>传奇数据库</h1>
<p class="sub">由脚本从 gamedata 树生成 · 共 {len(idx)} 条可检索条目</p>
<div class="cards">{cards}</div>
<h2>快速检索</h2>
<div class="toolbar"><input id="gsearch" type="search"
  placeholder="输入名称，检索物品 / 怪物 / 地图 / 技能…"></div>
<ul id="gresults" style="list-style:none;padding:0">{lis}</ul>
"""
    (out / 'index.html').write_text(A.page('首页', body, 'index.html'), encoding='utf-8')


def page_items(d: Data, out: Path) -> None:
    rows = []
    for r in sorted(d.items, key=lambda x: i(x['Idx'])):
        cat, sub = d.item_cat(r)
        lk = i(r['Looks'])
        icon = rel('assets/icons/items/' + str(lk) + '.png', 0) if lk in d.icons['items'] else None
        rows.append(
            '<tr data-row data-cat="' + E(cat) + '" data-search="'
            + E((r['Name'] + ' ' + cat + ' ' + sub).lower()) + '">'
            + '<td>' + ic_img(icon, 'icon sm') + '</td>'
            + '<td data-v="' + str(i(r['Idx'])) + '"><a href="drops.html?q='
            + E(r['Name']) + '" title="查看掉落">' + E(r['Name']) + '</a></td>'
            + '<td>' + E(cat) + '<span class="sub"> / ' + E(sub) + '</span></td>'
            + '<td class="attr">' + E(d.item_attr(r)) + '</td>'
            + '<td class="attr">' + E(d.item_need(r)) + '</td>'
            + '<td class="num" data-v="' + str(i(r['Weight'])) + '">' + str(i(r['Weight'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['DuraMax'])) + '">' + str(i(r['DuraMax'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['Price'])) + '">' + str(i(r['Price'])) + '</td></tr>')
    tabs = [('all', '全部')] + [(c, c) for c in ('武器', '装备', '消耗品', '书', '杂项')]
    body = f"""
<h1>物品 <span class="sub">{len(d.items)} 件</span></h1>
<p class="sub">武器 = StdMode 5/6；装备 = 衣服/头盔/项链/戒指/手镯/护身符；
消耗品 = 药水/卷轴/食物；书 = 技能书；其余归杂项。点名称查该物品的全部掉落来源。</p>
{toolbar('搜索物品名 / 部位 / 分类…', tabs)}
{table(['', '名称', '分类', '属性', '需求', '重量', '耐久', '价格'], rows)}
"""
    (out / 'items.html').write_text(A.page('物品', body, 'items.html'), encoding='utf-8')


def page_skills(d: Data, out: Path) -> None:
    seen: set[tuple] = set()
    skills = []
    for r in d.skills:
        key = tuple(r.values())
        if key not in seen:
            seen.add(key)
            skills.append(r)
    show_descr = any(r['Descr'].strip() not in ('', '0') for r in skills)
    blocks = []
    for job in (0, 1, 2):
        sub = [r for r in skills if i(r['Job']) == job]
        if not sub:
            continue
        rows = []
        for r in sorted(sub, key=lambda x: i(x['MagId'])):
            ef = i(r['Effect'])
            icon = (rel('assets/icons/skills/' + str(ef) + '.png', 0)
                    if ef in d.icons['skills'] else None)
            rows.append(
                '<tr data-row data-cat="all" data-search="'
                + E(r['MagName'].lower()) + '">'
                + '<td>' + ic_img(icon) + '</td>'
                + '<td>' + E(r['MagName']) + '<span class="sub"> #' + str(i(r['MagId'])) + '</span></td>'
                + '<td class="num">' + str(i(r['NeedL1'])) + '</td>'
                + '<td class="num">' + str(i(r['L1Train'])) + '</td>'
                + '<td class="num">' + str(i(r['NeedL2'])) + '</td>'
                + '<td class="num">' + str(i(r['L2Train'])) + '</td>'
                + '<td class="num">' + str(i(r['NeedL3'])) + '</td>'
                + '<td class="num">' + str(i(r['L3Train'])) + '</td>'
                + '<td class="num">' + str(i(r['Power'])) + '-' + str(i(r['MaxPower'])) + '</td>'
                + '<td class="num">' + str(i(r['Delay'])) + '</td>'
                + ('<td>' + E(r['Descr']) + '</td>' if show_descr else '') + '</tr>')
        headers = ['', '名称', '需求L1', '修炼1', '需求L2', '修炼2', '需求L3',
                   '修炼3', '威力', '延迟'] + (['备注'] if show_descr else [])
        blocks.append('<h2>' + JOB_NAME[job] + '技能 <span class="sub">' + str(len(sub))
                      + ' 个</span></h2>' + table(headers, rows))
    body = f"""
<h1>技能 <span class="sub">{len(skills)} 个</span></h1>
<p class="sub">按职业分组（Job 0/1/2 = 战/法/道）；修炼点 = 升到该级所需累计修炼值；
威力 = 基础值-上限值。图标帧号 = Effect×2（Delphi 客户端 FState.pas 同款规则）。</p>
{toolbar('搜索技能名…')}
{''.join(blocks)}
"""
    (out / 'skills.html').write_text(A.page('技能', body, 'skills.html'), encoding='utf-8')


def page_monsters(d: Data, out: Path) -> None:
    rows = []
    for r in sorted(d.monsters, key=lambda x: (i(x['Level']), x['Name'])):
        name = r['Name'].strip()
        ap = i(r['Appr'])
        icon = rel('assets/icons/mobs/' + str(ap) + '.png', 0) if ap in d.icons['mobs'] else None
        rows.append(
            '<tr data-row data-cat="all" data-search="' + E(name.lower()) + '">'
            + '<td>' + ic_img(icon) + '</td>'
            + '<td><a href="mon/' + safe_name(name) + '.html">' + E(name) + '</a></td>'
            + '<td class="num" data-v="' + str(i(r['Level'])) + '">' + str(i(r['Level'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['HP'])) + '">' + str(i(r['HP'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['Exp'])) + '">' + str(i(r['Exp'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['AC'])) + '">' + str(i(r['AC'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['MAC'])) + '">' + str(i(r['MAC'])) + '</td>'
            + '<td class="num" data-v="' + str(i(r['DC'])) + '">' + str(i(r['DC'])) + '-'
            + str(i(r['DCMax'])) + '</td>'
            + '<td class="num">' + str(i(r['MC'])) + '/' + str(i(r['SC'])) + '</td>'
            + '<td class="num" data-v="' + str(len(d.mon_map_total.get(name, {}))) + '">'
            + str(len(d.mon_map_total.get(name, {}))) + '</td>'
            + '<td class="num" data-v="' + str(len(d.drops.get(name, []))) + '">'
            + str(len(d.drops.get(name, []))) + '</td></tr>')
    body = f"""
<h1>怪物 <span class="sub">{len(d.monsters)} 种</span></h1>
<p class="sub">属性来自怪物 CSV；分布图数 = 刷怪表中出现的图数；掉落种类 = 掉落行数。
点名称进入详情（属性 + 分布 + 掉落）。</p>
{toolbar('搜索怪物名…')}
{table(['', '名称', '等级', 'HP', '经验', '防', '魔防', '攻', '魔/道',
       '分布图数', '掉落种类'], rows)}
"""
    (out / 'monsters.html').write_text(A.page('怪物', body, 'monsters.html'), encoding='utf-8')


def mon_detail(d: Data, r: dict) -> str:
    name = r['Name'].strip()
    ap = i(r['Appr'])
    icon = rel('assets/icons/mobs/' + str(ap) + '.png', 1) if ap in d.icons['mobs'] else None
    agg = sorted(d.mon_map_total.get(name, {}).items(), key=lambda kv: -kv[1])
    total = sum(cnt for _, cnt in agg)
    sp_rows = []
    for mid, cnt in agg:
        pts = [s for s in d.mon_spawn.get(name, []) if s[0] == mid]
        sp_rows.append(
            '<tr data-row data-cat="all" data-search="'
            + E((d.map_name(mid) + ' ' + mid).lower()) + '">'
            + '<td>' + map_link(d, mid, '../maps/')
            + '<span class="sub"> ' + E(mid) + '</span></td>'
            + '<td class="num">' + str(cnt) + '</td>'
            + '<td class="num">' + str(len(pts)) + '</td></tr>')
    dep_rows = []
    for rate, item, qty in d.drops.get(name, []):
        it = d.item_by_name.get(norm_item(item))
        disp = ('<a href="../items.html?q=' + E(it['Name']) + '">' + E(item) + '</a>'
                if it else E(item) + '<span class="sub">（模板库无此物）</span>')
        dep_rows.append('<tr data-row data-cat="all" data-search="' + E(item.lower()) + '">'
                        + '<td>' + disp + '</td>'
                        + '<td class="num">' + E(rate) + '</td>'
                        + '<td class="num">' + str(qty) + '</td></tr>')
    attrs = [('等级', i(r['Level'])), ('HP', i(r['HP'])), ('MP', i(r['MP'])),
             ('经验', i(r['Exp'])), ('防', i(r['AC'])), ('魔防', i(r['MAC'])),
             ('攻', str(i(r['DC'])) + '-' + str(i(r['DCMax']))), ('魔攻', i(r['MC'])),
             ('道术', i(r['SC'])), ('速度', i(r['Speed'])), ('准确', i(r['Hit'])),
             ('攻击间隔', i(r['AttackSpeed'])), ('不死系', i(r['Undead']))]
    attr_html = ''.join('<span class="chip">' + E(k) + ' <i>' + E(str(v)) + '</i></span>'
                        for k, v in attrs)
    spawn_block = (toolbar('搜索地图…') + table(['地图', '数量', '刷新点'], sp_rows)
                   if sp_rows else '<div class="empty">本树刷怪表未配置该怪物</div>')
    drop_block = (toolbar('搜索物品…') + table(['物品', '概率', '数量'], dep_rows)
                  if dep_rows else '<div class="empty">无掉落配置</div>')
    return f"""
<p class="back"><a href="../monsters.html">← 怪物列表</a></p>
<h1>{ic_img(icon, 'icon lg')} {E(name)}</h1>
<div>{attr_html}</div>
<h2>分布 <span class="sub">{len(agg)} 张图 · 共 {total} 只</span></h2>
{spawn_block}
<h2>掉落 <span class="sub">{len(dep_rows)} 行</span></h2>
{drop_block}
"""


def page_mon_detail(d: Data, out: Path) -> None:
    md = out / 'mon'
    md.mkdir(exist_ok=True)
    for r in d.monsters:
        name = r['Name'].strip()
        (md / (safe_name(name) + '.html')).write_text(
            A.page(name, mon_detail(d, r), 'monsters.html', depth=1), encoding='utf-8')


def map_detail(d: Data, mid: str) -> str:
    size = d.map_size.get(mid)
    spawn = d.map_spawn.get(mid, {})
    total = sum(spawn.values())
    sp_rows = []
    for mon, cnt in sorted(spawn.items(), key=lambda kv: -kv[1]):
        m = d.mon_by_name.get(mon)
        ap = i(m['Appr']) if m else 0
        icon = rel('assets/icons/mobs/' + str(ap) + '.png', 1) if ap in d.icons['mobs'] else None
        lv = str(i(m['Level'])) if m else '—'
        sp_rows.append(
            '<tr data-row data-cat="all" data-search="' + E(mon.lower()) + '">'
            + '<td>' + ic_img(icon, 'icon sm') + '</td>'
            + '<td>' + mon_link(d, mon, '../mon/') + '</td>'
            + '<td class="num">' + lv + '</td><td class="num">' + str(cnt) + '</td></tr>')
    npc_rows = ''.join(
        '<tr data-row data-cat="all" data-search="' + E(nm.lower()) + '">'
        + '<td>' + ('<a href="../npc/' + safe_name(nm) + '.html">' + E(nm) + '</a>'
                    if nm in d.npc_by_name else E(nm))
        + '</td><td class="num">' + str(x) + '</td>'
        + '<td class="num">' + str(y) + '</td></tr>'
        for nm, x, y in d.map_npcs.get(mid, []))
    gate_rows = ''.join(
        '<tr data-row data-cat="all" data-search="' + E(d.map_name(g[3]).lower()) + '">'
        + '<td>出口</td><td class="num">' + f'{g[1]},{g[2]}' + '</td>'
        + '<td>' + map_link(d, g[3], '')
        + '<span class="sub"> ' + E(g[3]) + '</span></td>'
        + '<td class="num">' + f'{g[4]},{g[5]}' + '</td></tr>'
        for g in d.gates_from.get(mid, []))
    gate_in = ''.join(
        '<tr data-row data-cat="all" data-search="' + E(d.map_name(g[0]).lower()) + '">'
        + '<td>入口</td><td class="num">' + f'{g[4]},{g[5]}' + '</td>'
        + '<td>' + map_link(d, g[0], '')
        + '<span class="sub"> ' + E(g[0]) + '</span></td>'
        + '<td class="num">' + f'{g[1]},{g[2]}' + '</td></tr>'
        for g in d.gates_to.get(mid, []))
    return f"""
<p class="back"><a href="../maps.html">← 地图列表</a></p>
<h1>{E(d.map_name(mid))} <span class="sub">图号 {E(mid)}</span></h1>
<p class="sub">尺寸 {f'{size[0]}×{size[1]}' if size else '未知'} · 刷怪 {total} 只 ·
种类 {len(spawn)} · NPC {len(d.map_npcs.get(mid, []))}</p>
{('<a href="../assets/maps/' + safe_name(mid) + '.png" target="_blank">'
      + '<img src="../assets/maps/' + safe_name(mid) + '.png" alt="" loading="lazy" '
      + 'style="width:100%;max-width:720px;display:block;border:1px solid var(--line);'
      + 'border-radius:8px;image-rendering:pixelated;background:#282828"></a>'
      + '<p class="sub">渲染图：室内小图按客户端图层（Tiles/SmTiles/Objects）逐帧合成，'
      + '大区域图用 2×2 平均色近似；怪物图标 = MonGen 刷新点（站姿朝下）。</p>')
     if mid in d.maps_rendered else '<div class="empty">缺少 .map 文件，无法渲染</div>'}
<h2>刷怪分布</h2>
{toolbar('搜索怪物…') + table(['', '怪物', '等级', '数量'], sp_rows) if sp_rows else '<div class="empty">本图无刷怪配置</div>'}
<h2>NPC</h2>
{table(['名称', 'X', 'Y'], [npc_rows]) if npc_rows else '<div class="empty">无</div>'}
<h2>出入口</h2>
{table(['方向', '本图坐标', '目标地图', '目标坐标'], [gate_rows + gate_in]) if (gate_rows or gate_in) else '<div class="empty">无</div>'}
"""


def page_maps(d: Data, out: Path) -> None:
    rows = []
    for mid in d.map_title:
        size = d.map_size.get(mid)
        spawn = d.map_spawn.get(mid, {})
        thumb = rel('assets/maps/' + safe_name(mid) + '.png', 0)
        rows.append(
            '<tr data-row data-cat="all" data-search="'
            + E((d.map_name(mid) + ' ' + mid).lower()) + '">'
            + '<td>' + ic_img(thumb if mid in d.maps_rendered else None, 'icon lg') + '</td>'
            + '<td><a href="maps/' + safe_name(mid) + '.html">' + E(d.map_name(mid)) + '</a></td>'
            + '<td class="mono">' + E(mid) + '</td>'
            + '<td class="num" data-v="' + str(size[0] if size else 0) + '">'
            + (f'{size[0]}×{size[1]}' if size else '<span class="warn">缺 .map</span>')
            + '</td>'
            + '<td class="num">' + str(len(spawn)) + '</td>'
            + '<td class="num">' + str(sum(spawn.values())) + '</td>'
            + '<td class="num">' + str(len(d.map_npcs.get(mid, []))) + '</td>'
            + '<td class="num">' + str(len(d.gates_from.get(mid, []))) + '</td></tr>')
    missing = sum(1 for mid in d.map_title if mid not in d.map_size)
    body = f"""
<h1>地图 <span class="sub">{len(d.map_title)} 张</span></h1>
<p class="sub">尺寸读自 .map 文件头；点名称进入详情（渲染图 / 刷怪分布 / NPC / 出入口）。
其中 {missing} 张只有 MapInfo 登记、客户端资源里没有 .map 文件（标「缺 .map」），
是数据包本身缺文件，不是站点引用错误。</p>
{toolbar('搜索地图名 / 图号…')}
{table(['', '名称', '图号', '尺寸', '怪物种类', '刷怪总数', 'NPC', '出口'], rows)}
"""
    (out / 'maps.html').write_text(A.page('地图', body, 'maps.html'), encoding='utf-8')
    md = out / 'maps'
    md.mkdir(exist_ok=True)
    for mid in d.map_title:
        (md / (safe_name(mid) + '.html')).write_text(
            A.page(d.map_name(mid), map_detail(d, mid), 'maps.html', depth=1),
            encoding='utf-8')


def npc_detail(d: Data, name: str) -> str:
    locs = d.npc_by_name[name]
    goods = d.npc_goods.get(name, [])
    ap = locs[0]['appr']
    icon = rel('assets/icons/npc/' + str(ap) + '.png', 1) if ap in d.icons['npc'] else None
    kind = '商人' if any(l['merchant'] for l in locs) else '固定 NPC'
    loc_rows = ''.join(
        '<tr data-row data-cat="all" data-search="'
        + E(d.map_name(l['map']).lower() + ' ' + l['map']) + '">'
        + '<td>' + map_link(d, l['map'], '../maps/')
        + '<span class="sub"> ' + E(l['map']) + '</span></td>'
        + '<td class="num">' + str(l['x']) + '</td>'
        + '<td class="num">' + str(l['y']) + '</td>'
        + '<td class="mono">' + E(l['script']) + '</td></tr>'
        for l in sorted(locs, key=lambda r: (r['map'], r['x'], r['y'])))
    goods_rows = []
    for g in goods:
        it = d.item_by_name.get(norm_item(g['name']))
        if it:
            lk = i(it['Looks'])
            gicon = rel('assets/icons/items/' + str(lk) + '.png', 1) if lk in d.icons['items'] else None
            disp = ('<a href="../items.html?q=' + E(it['Name']) + '">' + E(g['name'])
                    + '</a>')
        else:
            gicon = None
            disp = E(g['name']) + '<span class="sub">（模板库无此物）</span>'
        goods_rows.append(
            '<tr data-row data-cat="all" data-search="' + E(g['name'].lower()) + '">'
            + '<td>' + ic_img(gicon, 'icon sm') + '</td>'
            + '<td>' + disp + '</td>'
            + '<td class="num" data-v="' + str(g['price']) + '">' + str(g['price']) + '</td>'
            + '<td class="num" data-v="' + str(g['stock']) + '">' + str(g['stock']) + '</td></tr>')
    goods_block = (table(['', '名称', '售价', '库存'], goods_rows)
                   if goods_rows else '<div class="empty">该 NPC 无商品出售（或脚本无 [goods] 段）</div>')
    return f"""
<p class="back"><a href="../npcs.html">← NPC 列表</a></p>
<h1>{ic_img(icon, 'icon lg')} {E(name)}</h1>
<div><span class="chip">类型 <i>{kind}</i></span>
<span class="chip">位置 <i>{len(locs)} 处</i></span>
<span class="chip">商品 <i>{len(goods)}</i></span></div>
<h2>位置</h2>
{table(['地图', 'X', 'Y', '脚本'], [loc_rows])}
<h2>售卖商品 <span class="sub">售价 = 原价 × 1.1 × 脚本倍率（Delphi ObjNpc.pas CheckItemPrice/GetUserPrice 同款链路）</span></h2>
{goods_block}
"""


def page_npcs(d: Data, out: Path) -> None:
    rows = []
    for name in sorted(d.npc_by_name):
        locs = d.npc_by_name[name]
        goods = d.npc_goods.get(name, [])
        ap = locs[0]['appr']
        icon = rel('assets/icons/npc/' + str(ap) + '.png', 0) if ap in d.icons['npc'] else None
        kind = '商人' if any(l['merchant'] for l in locs) else '固定 NPC'
        maps = sorted({l['map'] for l in locs})
        maplinks = ' '.join(map_link(d, m, 'maps/') for m in maps)
        rows.append(
            '<tr data-row data-cat="' + kind + '" data-search="'
            + E((name + ' ' + ' '.join(maps)).lower()) + '">'
            + '<td>' + ic_img(icon, 'icon sm') + '</td>'
            + '<td><a href="npc/' + safe_name(name) + '.html">' + E(name) + '</a></td>'
            + '<td>' + kind + '<span class="sub"> / ' + str(len(locs)) + ' 处</span></td>'
            + '<td>' + maplinks + '</td>'
            + '<td class="num" data-v="' + str(len(goods)) + '">'
            + (str(len(goods)) if goods else '—') + '</td></tr>')
    tabs = [('all', '全部'), ('商人', '商人'), ('固定 NPC', '固定 NPC')]
    body = f"""
<h1>NPC <span class="sub">{len(d.npc_by_name)} 个</span></h1>
<p class="sub">来源 Npcs.txt / MerChant.txt；商品来自 Market_Def 与 Npc_Def 脚本的
[goods] 段（Gee 风格 #CALL 子脚本自动回退）。点名称看位置与售卖物。</p>
{toolbar('搜索 NPC 名 / 地图…', tabs)}
{table(['', '名称', '类型', '所在地图', '商品数'], rows)}
"""
    (out / 'npcs.html').write_text(A.page('NPC', body, 'npcs.html'), encoding='utf-8')
    nd = out / 'npc'
    nd.mkdir(exist_ok=True)
    for name in d.npc_by_name:
        (nd / (safe_name(name) + '.html')).write_text(
            A.page(name, npc_detail(d, name), 'npcs.html', depth=1), encoding='utf-8')


def page_dist(d: Data, out: Path) -> None:
    rows = []
    for mid in d.map_title:
        spawn = d.map_spawn.get(mid, {})
        if not spawn:
            continue
        chips = ''.join(chip(mon, f'×{cnt}',
                             ('mon/' + safe_name(mon) + '.html')
                             if mon in d.mon_by_name else None)
                        for mon, cnt in sorted(spawn.items(), key=lambda kv: -kv[1]))
        rows.append(
            '<tr data-row data-cat="all" data-search="'
            + E((d.map_name(mid) + ' ' + mid + ' ' + ' '.join(spawn)).lower()) + '">'
            + '<td><a href="maps/' + safe_name(mid) + '.html">' + E(d.map_name(mid))
            + '</a><span class="sub"> ' + E(mid) + '</span></td>'
            + '<td class="num">' + str(sum(spawn.values())) + '</td>'
            + '<td>' + chips + '</td></tr>')
    body = f"""
<h1>怪物分布 <span class="sub">{len(rows)} 张有刷怪的图</span></h1>
<p class="sub">每张图的怪物种类与数量（MonGen.txt 汇总）；点怪物看详情。搜索框可搜图名或怪物名。</p>
{toolbar('搜索地图 / 怪物名…')}
{table(['地图', '刷怪总数', '怪物分布（×数量）'], rows)}
"""
    (out / 'dist.html').write_text(A.page('怪物分布', body, 'dist.html'), encoding='utf-8')


def page_drops(d: Data, out: Path) -> None:
    by_mon = []
    for mon, entries in sorted(d.drops.items(), key=lambda kv: -len(kv[1])):
        chips = ''.join(chip(item, rate + ('×' + str(qty) if qty != 1 else ''))
                        for rate, item, qty in entries)
        by_mon.append(
            '<tr data-row data-cat="all" data-search="'
            + E((mon + ' ' + ' '.join(e[1] for e in entries)).lower()) + '">'
            + '<td>' + mon_link(d, mon, 'mon/') + '</td>'
            + '<td class="num">' + str(len(entries)) + '</td>'
            + '<td>' + chips + '</td></tr>')
    by_item = []
    for item, recs in sorted(d.item_dropped_by.items()):
        it = d.item_by_name.get(item)
        name = it['Name'] if it else item
        chips = ''.join(chip(m, r, 'mon/' + safe_name(m) + '.html')
                        if m in d.mon_by_name else chip(m, r) for m, r in recs)
        disp = '<a href="items.html?q=' + E(it['Name']) + '">' + E(name) + '</a>' if it else E(name)
        by_item.append(
            '<tr data-row data-cat="all" data-search="'
            + E((name + ' ' + ' '.join(x[0] for x in recs)).lower()) + '">'
            + '<td>' + disp + '</td>'
            + '<td class="num">' + str(len(recs)) + '</td>'
            + '<td>' + chips + '</td></tr>')
    body = f"""
<h1>掉落</h1>
<p class="sub">来源 MonItems/*.txt；概率为原表写法（1/N 即平均 N 只掉 1 件）。</p>
{toolbar('搜索怪物 / 物品名…（同时过滤两张表）')}
<h2>按怪物 <span class="sub">{len(by_mon)} 种</span></h2>
{table(['怪物', '掉落种类', '掉落物（概率）'], by_mon)}
<h2>按物品 <span class="sub">{len(by_item)} 种</span></h2>
{table(['物品', '来源数', '掉落来源（概率）'], by_item)}
"""
    (out / 'drops.html').write_text(A.page('掉落', body, 'drops.html'), encoding='utf-8')


# ---------------------------------------------------------------- 主流程

def resolve_tree(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        cand = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
        if cand.is_dir():
            return cand
        alt = (ROOT / 'data' / 'gamedata' / arg).resolve()   # 副本树名，如 176
        return alt if alt.is_dir() else cand
    root = 'gamedata'
    for ln in (ROOT / 'config' / 'server.properties').read_text(encoding='utf-8').splitlines():
        if ln.strip().startswith('gamedata.root'):
            root = ln.split('=', 1)[1].strip()
    return (ROOT / root).resolve()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--tree', default=None, help='gamedata 树（默认读生效指针）')
    ap.add_argument('--out', default='data/website', help='输出目录（默认 data/website）')
    ap.add_argument('--skip-existing-maps', action='store_true',
                    help='已有渲染图的地图跳过（快速重建页面时用）')
    ap.add_argument('--open', action='store_true', help='生成后打开首页')
    a = ap.parse_args()

    tree = resolve_tree(a.tree)
    p = Path(a.out)
    out = p.resolve() if p.is_absolute() else (ROOT / p).resolve()
    if not (tree / 'mud2' / 'DB' / 'StdItems.csv').exists():
        sys.exit(f'不是有效的 gamedata 树: {tree}')
    out.mkdir(parents=True, exist_ok=True)
    logs: list[str] = []
    log = logs.append

    log(f'数据树: {tree}')
    d = Data(tree)
    log(f'物品 {len(d.items)} · 技能 {len(d.skills)} · 怪物 {len(d.monsters)} · '
        f'地图 {len(d.map_title)} · 刷怪行 {len(d.spawns)} · 掉落表 {len(d.drops)} · '
        f'NPC {sum(len(v) for v in d.map_npcs.values())}')

    (out / 'assets').mkdir(exist_ok=True)
    (out / 'assets' / 'style.css').write_text(A.CSS, encoding='utf-8')
    (out / 'assets' / 'app.js').write_text(A.JS, encoding='utf-8')
    d.compute_npc_goods()
    log(f'NPC 商品: {len(d.npc_goods)} 个 NPC 有 [goods] 配置，'
        f'共 {sum(len(v) for v in d.npc_goods.values())} 条')
    sync_icons(d, out, log)
    render_map_images(d, out, log, a.skip_existing_maps)

    page_index(d, out)
    page_items(d, out)
    page_skills(d, out)
    page_monsters(d, out)
    page_mon_detail(d, out)
    page_maps(d, out)
    page_npcs(d, out)
    page_dist(d, out)
    page_drops(d, out)

    log(f'页面 {sum(1 for _ in out.rglob("*.html"))} 个 · 输出 {out}')
    print('\n'.join(logs))
    if a.open:
        webbrowser.open((out / 'index.html').as_uri())


if __name__ == '__main__':
    main()