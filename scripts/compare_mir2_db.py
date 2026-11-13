#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对比两套 Mir2 DB（Paradox -> CSV）的差异，输出 Markdown 报告。

配合 ``scripts/mir2_db_export.py`` 使用：先把两套 ``.DB`` 各自导出成 CSV
（表头取文件自身字段名），再跑本脚本。

    D:/Python310/python.exe scripts/mir2_db_export.py <对方DB目录> -o data/db_compare/<tag>
    D:/Python310/python.exe scripts/compare_mir2_db.py \
        --ours gamedata/mud2/DB --theirs data/db_compare/<tag> --label <tag>

输出：表格统计 + 名称集合差异 + 同名字段差异（按字段计数并给样例），
可直接贴进文档。字段名不同的两套 schema 用 ``FIELD_ALIAS`` 对齐。
"""

import argparse
import collections
import csv
import io
import sys
from pathlib import Path

# 本项目 CSV 字段 -> 其它版本常见字段名（同一语义的不同写法）
FIELD_ALIAS = {
    'Name': ('Name', 'NAME'),
    'Race': ('Race',),
    'RaceImg': ('RaceImg',),
    'Appr': ('Appr',),
    'Level': ('Level', 'Lvl', 'LVL'),
    'Undead': ('Undead',),
    'CoolEye': ('CoolEye',),
    'Exp': ('Exp', 'EXP'),
    'HP': ('HP',),
    'MP': ('MP',),
    'AC': ('AC', 'Ac'),
    'MAC': ('MAC', 'Mac'),
    'DC': ('DC', 'Dc'),
    'DCMax': ('DCMax', 'DCMAX', 'Dc2'),
    'MC': ('MC', 'Mc'),
    'SC': ('SC', 'Sc'),
    'Speed': ('Speed', 'SPEED'),
    'Hit': ('Hit', 'HIT'),
    'WalkSpeed': ('WalkSpeed', 'WALK_SPD'),
    'WalkStep': ('WalkStep',),
    'WalkWait': ('WalkWait', 'WaLkWait', 'WALKWAIT'),
    'AttackSpeed': ('AttackSpeed', 'ATTACK_SPD'),
    'StdMode': ('StdMode',),
    'Shape': ('Shape',),
    'Weight': ('Weight',),
    'Looks': ('Looks',),
    'DuraMax': ('DuraMax',),
    'Price': ('Price',),
    'Need': ('Need',),
    'NeedLevel': ('NeedLevel',),
    'MagName': ('MagName',),
    'Job': ('Job',),
    'NeedL1': ('NeedL1',),
    'NeedL2': ('NeedL2',),
    'NeedL3': ('NeedL3',),
}

TABLES = ('Monster', 'StdItems', 'Magic')
KEY = {'Monster': 'Name', 'StdItems': 'Name', 'Magic': 'MagName'}


def read_csv(path):
    if not path.is_file():
        return None, None
    with io.open(str(path), encoding='utf-8-sig') as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], []
    return rows[0], rows[1:]


def pick(header, want):
    """按别名找到实际字段名；找不到返回 None。"""
    for cand in FIELD_ALIAS.get(want, (want,)):
        for h in header:
            if h.strip().lower() == cand.strip().lower():
                return h
    return None


def as_int(v):
    try:
        return int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        return None


def compare_table(name, ours_dir, theirs_dir):
    key = KEY[name]
    oh, orows = read_csv(ours_dir / (name + '.csv'))
    th, trows = read_csv(theirs_dir / (name + '.csv'))
    out = {'name': name, 'ok': oh is not None and th is not None}
    if not out['ok']:
        out['missing'] = 'ours' if oh is None else 'theirs'
        return out

    oi = {h: i for i, h in enumerate(oh)}
    ti = {h: i for i, h in enumerate(th)}
    ok = pick(oh, key)
    tk = pick(th, key)

    ours = {}
    for r in orows:
        k = r[oi[ok]].strip()
        ours.setdefault(k, r)
    theirs = {}
    for r in trows:
        k = r[ti[tk]].strip()
        theirs.setdefault(k, r)

    out.update({
        'our_rows': len(orows), 'our_uniq': len(ours),
        'their_rows': len(trows), 'their_uniq': len(theirs),
        'our_fields': oh, 'their_fields': th,
        'only_ours': sorted(set(ours) - set(theirs)),
        'only_theirs': sorted(set(theirs) - set(ours)),
        'common': sorted(set(ours) & set(theirs)),
    })

    # 同名字段差异：只比双方都有的语义字段
    fields = []
    for want in FIELD_ALIAS:
        if want == key:
            continue
        a = pick(oh, want)
        b = pick(th, want)
        if a is None or b is None:
            continue
        fields.append((want, a, b))

    stat = collections.OrderedDict()
    for want, a, b in fields:
        diff = []
        for k in out['common']:
            x = as_int(ours[k][oi[a]])
            y = as_int(theirs[k][ti[b]])
            if x is None or y is None:
                continue
            if x != y:
                diff.append((k, x, y))
        stat[want] = diff
    out['field_diff'] = stat
    return out


def fmt_fields(fields):
    return ', '.join(fields)


def render(results, ours_label, theirs_label):
    lines = []
    w = lines.append
    w('## 一、表规模与字段')
    w('')
    w('| 表 | %s 行数 | %s 行数 | 共有名 | 仅 %s | 仅 %s |'
      % (ours_label, theirs_label, ours_label, theirs_label))
    w('|---|---|---|---|---|---|')
    for r in results:
        if not r['ok']:
            w('| %s | - | - | - | - | - |' % r['name'])
            continue
        w('| %s | %d（唯一 %d） | %d（唯一 %d） | %d | %d | %d |'
          % (r['name'], r['our_rows'], r['our_uniq'],
             r['their_rows'], r['their_uniq'],
             len(r['common']), len(r['only_ours']), len(r['only_theirs'])))
    w('')
    for r in results:
        if not r['ok']:
            continue
        w('- **%s** 字段数：%s %d / %s %d'
          % (r['name'], ours_label, len(r['our_fields']),
             theirs_label, len(r['their_fields'])))
        only_o = [f for f in r['our_fields']
                  if f.lower() not in {x.lower() for x in r['their_fields']}]
        only_t = [f for f in r['their_fields']
                  if f.lower() not in {x.lower() for x in r['our_fields']}]
        if only_o:
            w('  - 仅 %s 有：%s' % (ours_label, fmt_fields(only_o)))
        if only_t:
            w('  - 仅 %s 有：%s' % (theirs_label, fmt_fields(only_t)))
    w('')
    w('## 二、同名字段的取值差异（只统计双方都存在的名字）')
    w('')
    for r in results:
        if not r['ok'] or not r['common']:
            continue
        w('### %s（共有 %d 条）' % (r['name'], len(r['common'])))
        w('')
        w('| 字段 | 不同条数 | 占比 | 样例（名字: 我们 / 对方） |')
        w('|---|---|---|---|')
        for f, diff in r['field_diff'].items():
            if not diff:
                continue
            samples = '；'.join('%s: %s / %s' % t for t in diff[:3])
            w('| %s | %d | %.0f%% | %s |'
              % (f, len(diff), 100.0 * len(diff) / len(r['common']), samples))
        w('')
    w('## 三、名称集合差异')
    w('')
    for r in results:
        if not r['ok']:
            continue
        w('### %s' % r['name'])
        w('')
        w('- 仅 %s 有（%d）：%s'
          % (ours_label, len(r['only_ours']), '、'.join(r['only_ours'][:120]) or '无'))
        w('- 仅 %s 有（%d）：%s'
          % (theirs_label, len(r['only_theirs']),
             '、'.join(r['only_theirs'][:120]) or '无'))
        w('')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='对比两套 Mir2 DB CSV')
    ap.add_argument('--ours', default='gamedata/mud2/DB', help='本项目 CSV 目录')
    ap.add_argument('--theirs', required=True, help='对方 CSV 目录')
    ap.add_argument('--label', default='theirs', help='对方标签（写进表格）')
    ap.add_argument('--out', help='把 Markdown 写到文件')
    args = ap.parse_args(argv)

    ours_dir = Path(args.ours)
    theirs_dir = Path(args.theirs)
    results = [compare_table(t, ours_dir, theirs_dir) for t in TABLES]
    md = render(results, '本项目', args.label)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(md + '\n', encoding='utf-8')
        print('[ok] -> %s' % args.out)
    else:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
