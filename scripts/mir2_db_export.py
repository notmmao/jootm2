#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Mir2 Paradox DB (Monster.DB / StdItems.DB / Magic.DB) -> CSV.

来源：notmmao_python/pypxlib/mir2_db_export.py（用户提供的第三方 DB 导出工具），
纳入本仓库作为对照工具用。与 ``scripts/paradox_to_csv.py`` 的区别：
- 本脚本用 ``pypxlib`` 读 Paradox 表，**表头取自文件本身**（字段名/顺序不用猜），
  因此可以导出任意第三方 DB（不同版本字段可能有增删）；
- ``paradox_to_csv.py`` 是零依赖手写解析器，只用在本项目自带的三张表上（字段写死）。

依赖::

    D:/Python310/python.exe -m pip install pypxlib

用法::

    # 单文件
    D:/Python310/python.exe scripts/mir2_db_export.py path/to/Monster.DB -o out/Monster.csv

    # 整个目录（同目录下所有 .DB）
    D:/Python310/python.exe scripts/mir2_db_export.py path/to/DB -o out

    # 递归 + GBK 源文本
    D:/Python310/python.exe scripts/mir2_db_export.py path/to/DB -o out --recursive --encoding gbk
"""

import argparse
import csv
import sys
from pathlib import Path

try:
    from pypxlib import Table
except ImportError:  # pragma: no cover - 环境缺依赖时给一句人话
    sys.exit("缺少依赖 pypxlib：D:/Python310/python.exe -m pip install pypxlib")


def value_to_csv(value):
    """将数据库字段转换成适合 CSV 的值。"""
    if value is None:
        return ""

    if isinstance(value, bytes):
        # Blob / Memo 等二进制字段暂时以 HEX 保存
        return value.hex()

    return value


def export_table(db_path, csv_path, encoding="cp936",
                 csv_encoding="utf-8-sig",
                 errors="replace", limit=None):

    db_path = Path(db_path)
    csv_path = Path(csv_path)

    # pypxlib 会自动寻找同目录同名的 .MB 文件
    with Table(
        str(db_path),
        encoding=encoding,
        px_decode_errors=errors
    ) as table:

        fields = list(table.fields.keys())
        total = len(table)

        if limit is not None:
            total = min(total, limit)

        csv_path.parent.mkdir(parents=True, exist_ok=True)

        with open(
            str(csv_path),
            "w",
            encoding=csv_encoding,
            newline=""
        ) as f:

            writer = csv.writer(f)
            writer.writerow(fields)

            for index in range(total):
                row = table[index]
                values = [
                    value_to_csv(row[name])
                    for name in fields
                ]
                writer.writerow(values)

        print(
            "[OK] {} -> {} ({} / {} records)".format(
                db_path.name, csv_path, total, len(table)
            )
        )


def find_databases(path, recursive=False):
    if path.is_file():
        return [path] if path.suffix.lower() == ".db" else []

    if recursive:
        found = list(path.rglob("*"))
        return sorted(p for p in found
                      if p.is_file() and p.suffix.lower() == ".db")

    return sorted(
        p for p in path.iterdir()
        if p.is_file() and p.suffix.lower() == ".db"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Mir2 Paradox DB to CSV exporter"
    )

    parser.add_argument(
        "input",
        help="DB file or directory"
    )

    parser.add_argument(
        "-o", "--output",
        help="Output CSV file or directory"
    )

    parser.add_argument(
        "--encoding",
        default="cp936",
        help="Source string encoding (default: cp936)"
    )

    parser.add_argument(
        "--csv-encoding",
        default="utf-8-sig",
        help="Output CSV encoding (default: utf-8-sig)"
    )

    parser.add_argument(
        "--errors",
        choices=["strict", "replace", "ignore"],
        default="replace",
        help="Decoding error handling (default: replace)"
    )

    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively search DB files"
    )

    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum records per table"
    )

    args = parser.parse_args()

    input_path = Path(args.input)

    if not input_path.exists():
        parser.error("Input does not exist: {}".format(input_path))

    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be >= 0")

    if input_path.is_file():
        output_path = (
            Path(args.output) if args.output
            else input_path.with_suffix(".csv")
        )

        if output_path.is_dir():
            output_path = output_path / (
                input_path.stem + ".csv"
            )

        jobs = [(input_path, output_path)]

    else:
        output_dir = Path(args.output or "./csv")
        databases = find_databases(
            input_path, args.recursive
        )

        jobs = [
            (db, output_dir / (db.stem + ".csv"))
            for db in databases
        ]

    if not jobs:
        print("No DB files found.")
        return 1

    success = 0
    failed = 0

    for db_path, csv_path in jobs:
        try:
            export_table(
                db_path,
                csv_path,
                encoding=args.encoding,
                csv_encoding=args.csv_encoding,
                errors=args.errors,
                limit=args.limit
            )
            success += 1

        except Exception as exc:
            print(
                "[ERROR] {}: {}".format(db_path, exc),
                file=sys.stderr
            )
            failed += 1

    print("\nFinished: {} success, {} failed".format(
        success, failed
    ))

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
