# -*- coding: utf-8 -*-
"""用 ruchu8 站（原版 1.76 口径基线）重构 176 树的 MonGen.txt + MonItems/。

背景：176 树 Envir 六件套最初照抄 176-mir2-red（Gee 私服发行包），爆率与刷怪
密度私服化严重（MonItems 1/1=1172 条 vs 主树 778；0 系变体堆怪）。本脚本把失
真最重的两层换成站点数据（用户选型 2，2026-11-14）：

  MonGen.txt  <- data/db_compare/ruchu8/site_spawn.csv（Mob_*.html 刷怪表）
  MonItems/   <- data/db_compare/ruchu8/site_drop.csv  （同页掉落表）
  Npcs/MerChant/Market_Def/QuestDiary 不动（NPC 脚本保留 176 包）。

图名映射与数量口径：
- 站点 MapName 是中文图名，需映射回本地图号：MapInfo.txt 精确名 → ALIAS 手工
  别名 → difflib 前缀约束兜底（与轮9 r9_compare.py 同一套）。
- 一个站点图名可命中多个候选图号；用主树 MonGen 里"该怪出现在哪个图号"作 hint
  选一个，无 hint 取首个候选。
- range 列站点没有：取主树同怪 range 众数，缺省 50。interval 向上取整最少 1 分钟。
- 掉落物品/怪物名与模板库不匹配的行保留原样（服务端查不到模板自然不掉/不刷），
  只在报告里给数量。

用法：
  python scripts/build_176_envir.py [--tree data/gamedata/176] [--dry-run]
"""
from __future__ import annotations

import argparse
import collections
import csv
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "db_compare" / "ruchu8"
MAIN_ENVIR = ROOT / "gamedata" / "Mir200" / "Envir"

# 站点图名 -> 本地 MapInfo 图名（与轮9 r9_compare.py 同一张表）
ALIAS = {
    '苍月岛': ['月崕岛'],
    '幽名圣域': ['幽明圣域'],
    '矿物回收站': ['废矿回收站'],
    '狩猎场': ['狩猎房'],
    '热血足球场': ['东部足球场', '西部足球场'],
    '香石墓穴': ['香石古墓一层', '香石古墓二层', '香石古墓三层'],
    '兽人古墓一层': ['半兽古墓一层'], '兽人古墓二层': ['半兽古墓二层'], '兽人古墓三层': ['半兽古墓三层'],
    '洞穴一层': ['天然洞穴一层'], '洞穴二层': ['天然洞穴二层'],
    '山谷矿区': ['毒蛇矿区一层', '毒蛇矿区二层'],
    '矿区一层': ['比奇矿区'], '矿区A一层': ['东部矿区'], '矿区B一层': ['矿区B二层', '南部矿区'],
    '废矿入口': ['废矿区一层'], '废矿区东部': ['东部矿区'], '废矿区南部': ['南部矿区'],
    '沃玛寺庙': ['沃玛寺庙入口', '沃玛寺庙一层', '沃玛寺庙二层', '沃玛教主大殿'],
    '沃玛自然洞穴': ['沃玛自然洞穴一层', '沃玛自然洞穴二层'],
    '地牢一层东': ['石墓阵'], '地牢一层北': ['石墓阵'], '地牢一层西': ['石墓阵'],
    '左回廊': ['幽冥回廊'], '右回廊': ['光芒回廊'],
    '堕落坟场': ['封魔殿'], '恶灵之地': ['封魔道'], '坟地': ['封魔谷'],
}


def norm_item(s: str) -> str:
    return s.replace('（', '(').replace('）', ')').replace(' ', '')


# ---------------------------------------------------------------- 输入

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def map_name_to_ids(envir: Path) -> dict[str, list[str]]:
    """MapInfo.txt：图号 -> 中文名（反转为 名 -> [图号]）。"""
    text = (envir / 'MapInfo.txt').read_bytes()
    try:
        t = text.decode('utf-8')
    except UnicodeDecodeError:
        t = text.decode('gbk', 'replace')
    out: dict[str, list[str]] = collections.defaultdict(list)
    for mid, mname in re.findall(r'\[([0-9A-Za-z]+)[\s\t]*([^\]\r\n]+?)\]', t):
        mname = mname.strip()
        if mname and len(mname) < 20:
            out[mname].append(mid)
    return out


def main_tree_hints(envir: Path) -> tuple[dict[tuple[str, str], int], dict[str, collections.Counter]]:
    """主树 MonGen：(图号,怪)->出现行数、怪->range 众数。作站点行落位 hint。"""
    cnt: dict[tuple[str, str], int] = collections.Counter()
    rng: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for ln in (envir / 'MonGen.txt').read_bytes().decode('utf-8', 'replace').splitlines():
        ln = ln.strip()
        if not ln or ln.startswith(';'):
            continue
        p = ln.split()
        if len(p) >= 6:
            cnt[(p[0], p[3])] += 1
            rng[p[3]][p[4]] += 1
    return cnt, rng


# ---------------------------------------------------------------- MonGen

def build_monster_gen(tree: Path, log, write: bool = True) -> int:
    name2ids = map_name_to_ids(MAIN_ENVIR)
    hints, rng_modes = main_tree_hints(MAIN_ENVIR)
    spawn = read_csv(DB / 'site_spawn.csv')

    site_maps = {r['MapName'] for r in spawn}
    loc_names = list(name2ids)
    map_ids: dict[str, list[str]] = {}
    for n in sorted(site_maps):
        if n in name2ids:
            map_ids[n] = name2ids[n]
        elif n in ALIAS and all(a in name2ids for a in ALIAS[n]):
            map_ids[n] = [i for a in ALIAS[n] for i in name2ids[a]]
        else:
            cand = [c for c in difflib.get_close_matches(n, loc_names, n=3, cutoff=0.55)
                    if c[:2] == n[:2]]
            map_ids[n] = [i for c in cand for i in name2ids[c]]
    unmapped = sorted(n for n, v in map_ids.items() if not v)
    log(f'MonGen 站点行 {len(spawn)}，图名 {len(site_maps)}，未映射 {len(unmapped)}: {unmapped}')

    # 176 树 Monster.csv 名字集（决定要不要丢弃查无模板的怪）
    mons_csv = tree / 'mud2' / 'DB' / 'Monster.csv'
    known = {r['Name'].strip() for r in read_csv(mons_csv)}
    log(f'176 树 Monster.csv 唯一怪 {len(known)}')

    rows: list[str] = []
    covered: set[str] = set()
    drop_no_map = drop_no_mon = 0
    missed_mons: set[str] = set()
    for r in spawn:
        mon, mn = r['Monster'].strip(), r['MapName'].strip()
        ids = map_ids.get(mn) or []
        if not ids:
            drop_no_map += 1
            continue
        if mon not in known:
            drop_no_mon += 1
            missed_mons.add(mon)
            continue
        if len(ids) == 1:
            mid = ids[0]
        else:
            mid = max(ids, key=lambda i: hints.get((i, mon), 0))
        covered.add(mid)
        rng_mode = rng_modes.get(mon)
        range_ = int(rng_mode.most_common(1)[0][0]) if rng_mode else 50
        try:
            iv = int(float(r['IntervalMin']))
        except (TypeError, ValueError):
            iv = 0
        iv = max(iv, 1)
        cnt_ = int(r['Count'] or 1)
        x = int(float(r['X'])) if r['X'] else 0
        y = int(float(r['Y'])) if r['Y'] else 0
        rows.append(f'{mid}\t{x}\t{y}\t{mon}\t{range_}\t{cnt_}\t{iv}')
    # 站点没覆盖到的地图：回退主树行（本地自建/活动图站点没有口径）
    kept_main = 0
    main_path = MAIN_ENVIR / 'MonGen.txt'
    for ln in main_path.read_bytes().decode('utf-8', 'replace').splitlines():
        p = ln.split()
        if len(p) >= 6 and p[1].isdigit() and p[2].isdigit() and p[0] not in covered:
            rows.append('\t'.join(p[:7]))
            kept_main += 1
    log(f'生成刷怪行 {len(rows)}（站点 {len(rows)-kept_main} + 主树回退 {kept_main}，'
        f'站点覆盖图号 {len(covered)}）；弃图名行 {drop_no_map}；弃无模板怪行 {drop_no_mon}'
        f'（{len(missed_mons)} 种: {sorted(missed_mons)[:15]}）')

    out = tree / 'Mir200' / 'Envir' / 'MonGen.txt'
    header = (';重构自 ruchu8.github.io Mob 刷怪表（原版 1.76 口径，2026-11-14）\n'
              ';生成命令 python scripts/build_176_envir.py；列同原版：地图 x y 怪物 范围 数量 时间(m)\n')
    if write:
        out.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8')
        log(f'写出 {out}（{len(rows)} 行，总量 {sum(int(r.split(chr(9))[5]) for r in rows)} 只）')
    else:
        log(f'dry-run：将写 {len(rows)} 行，总量 {sum(int(r.split(chr(9))[5]) for r in rows)} 只')
    return len(rows)


# ---------------------------------------------------------------- MonItems

def build_mon_items(tree: Path, log, write: bool = True) -> int:
    drops = [r for r in read_csv(DB / 'site_drop.csv') if r['Count'] != 'RAW' and r['Rate']]
    by_mon: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for r in drops:
        by_mon[r['Monster'].strip()].append(r)
    log(f'MonItems 掉落行 {len(drops)}，怪 {len(by_mon)}')

    # 校验面：怪名 vs Monster.csv、物品名 vs StdItems.csv（只报告，不删行）
    known_m = {r['Name'].strip() for r in read_csv(tree / 'mud2' / 'DB' / 'Monster.csv')}
    known_i = {norm_item(r['Name'].strip()) for r in read_csv(tree / 'mud2' / 'DB' / 'StdItems.csv')}
    mons_no_tpl = sorted(set(by_mon) - known_m)
    item_miss = collections.Counter()
    for recs in by_mon.values():
        for r in recs:
            if norm_item(r['Item']) not in known_i:
                item_miss[r['Item']] += 1
    log(f'掉落表怪名无模板 {len(mons_no_tpl)}: {mons_no_tpl[:15]}')
    log(f'物品名查无（行级 {sum(item_miss.values())}，唯一 {len(item_miss)}）: '
        f'{[k for k, _ in item_miss.most_common(15)]}')

    d = tree / 'Mir200' / 'Envir' / 'MonItems'
    site_stems = {re.sub(r'[<>:"/\\|?*]', '_', mon) for mon in by_mon}
    n_files = 0
    lines_by_file: dict[str, str] = {}
    for mon, recs in sorted(by_mon.items()):
        safe = re.sub(r'[<>:"/\\|?*]', '_', mon)
        lines = [';ruchu8 原版口径掉落（build_176_envir.py 生成）']
        # 站点把多个必爆槽位列成重复 `1/1 X` 行；逐行判定=杀一只爆 k 件，
        # 合并为 `1/1 X k`（第三列数量）与原版语义数学等价
        ones: dict[str, int] = {}
        for r in recs:
            if r['Rate'] == '1/1' and not r['Count']:
                ones[r['Item']] = ones.get(r['Item'], 0) + 1
            else:
                qty = f"\t{r['Count']}" if r['Count'] else ''
                lines.append(f"{r['Rate']}\t{r['Item']}{qty}")
        for item, k in ones.items():
            lines.append(f"1/1\t{item}" + (f"\t{k}" if k > 1 else ''))
        lines_by_file[f'{safe}.txt'] = '\n'.join(lines) + '\n'
        n_files += 1
    # 站点未覆盖的怪：回退主树掉落文件（176 包旧文件的 Gee 爆率整体作废）
    kept_main = 0
    copy_files: dict[str, bytes] = {}
    main_d = MAIN_ENVIR / 'MonItems'
    for f in sorted(main_d.glob('*.txt')):
        if f.stem.strip() in known_m and f.stem.strip() not in site_stems:
            kept_main += 1
            copy_files[f.name] = f.read_bytes()
    if write:
        for old in list(d.glob('*.txt')):   # 清空旧包（含尾随空格文件名）
            old.unlink()
        for name, text in lines_by_file.items():
            (d / name).write_text(text, encoding='utf-8')
        for name, blob in copy_files.items():
            (d / name).write_bytes(blob)
    if write:
        log(f'写出 MonItems/：站点 {n_files} + 主树回退 {kept_main}，目录现存 {len(list(d.glob("*.txt")))}')
    else:
        log(f'dry-run：将写 MonItems 站点 {n_files} + 主树回退 {kept_main}')
    return n_files


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--tree', type=Path, default=ROOT / 'data' / 'gamedata' / '176')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if not (a.tree / 'Mir200' / 'Envir').is_dir():
        sys.exit(f'目标树 Envir 不存在: {a.tree}')
    lines: list[str] = []
    log = lines.append
    if a.dry_run:
        log('(dry-run：只统计不落盘)')
    build_monster_gen(a.tree, log, write=not a.dry_run)
    build_mon_items(a.tree, log, write=not a.dry_run)
    report = '\n'.join(lines)
    (ROOT / 'data' / 'db_compare' / 'r10_build_envir.txt').write_text(report + '\n', encoding='utf-8')
    print(report)


if __name__ == '__main__':
    main()
