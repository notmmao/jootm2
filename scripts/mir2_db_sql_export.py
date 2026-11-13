#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Mir2 第三方 DB 的 SQL dump（SQLite 方言）导出成 CSV，供 compare_mir2_db.py 使用。

    D:/Python310/python.exe scripts/mir2_db_sql_export.py <dump.sql> -o data/db_compare/<tag>

dump 里 `CREATE TABLE IF NOT EXISTS` + `INSERT INTO "T" VALUES (...)` 是标准 SQLite
方言，直接用标准库 sqlite3 灌进内存库再逐表导出，表头取建表语句的字段名。
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='Mir2 SQL dump -> CSV')
    ap.add_argument('sql', help='SQL dump 文件（SQLite 方言）')
    ap.add_argument('-o', '--out', required=True, help='输出目录')
    args = ap.parse_args(argv)

    text = Path(args.sql).read_text(encoding='utf-8-sig')
    con = sqlite3.connect(':memory:')
    con.executescript(text)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tables = [r[0] for r in con.execute(
        "select name from sqlite_master where type='table' order by name")]
    for name in tables:
        cur = con.execute(f'select * from "{name}"')
        header = [d[0] for d in cur.description]
        rows = cur.fetchall()
        path = out / f'{name}.csv'
        with open(path, 'w', encoding='utf-8', newline='') as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        print(f'[{name}] {len(rows)} rows -> {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
