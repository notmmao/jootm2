# -*- coding: utf-8 -*-
"""gamedata 多树并存 + 生效目录切换（pyserver 原生支持 gamedata.root，见 config.py）。

布局：
  gamedata/                    主树（受 git 跟踪，当前私服数据，切换时永不改动）
  data/gamedata/<name>/        完整副本树（入库分发；Map 是指回 resources/Map 的 junction，
                               被 gitignore 排除，clone 后 use 时自动补建）
  config/server.properties     生效指针两行：gamedata.root= 与 db.file=（切树同步换库，互不混数据）

命令：
  build-176 [--redm2 --src176]     从主树复制出 data/gamedata/176：
                                   CSV 用 REDM2.DB 合并（同名覆盖、保留本地 Idx、独有插入），
                                   Envir 五件套照抄 176-mir2-red 包
  use <name|main>                  改两行指针（main: gamedata + jootm2.db；176: 副本树 + jootm2-176.db）
                                   + 清该库三张模板表（下次启动重灌）+ 补 Map junction
  list                             列出所有树与当前生效项

注意：
- 切换只动 server.properties 两行；主树内容绝不会被本工具改写。
- 副本树里的 MerChant 脚本列含 `传送员\\传送员` 反斜杠子目录——176 树只能本机 Windows 用。
- 删除副本树走 junction-aware 逻辑，绝不穿透 resources/Map。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_TREE = ROOT / "gamedata"
TREES = ROOT / "data" / "gamedata"
PROPS = ROOT / "config" / "server.properties"

REDM2_DEFAULT = ROOT / "data" / "db_compare" / "REDM2.DB"
SRC176_DEFAULT = Path("D:/work/me/game/传奇/176-mir2-red")

ENVIR_FILES = ("MonGen.txt", "Npcs.txt", "MerChant.txt")
ENVIR_DIRS = ("MonItems", "Market_Def")
CSV_FILES = ("StdItems.csv", "Monster.csv", "Magic.csv")


# ---------------------------------------------------------------- 指针

def get_prop(key: str, default: str) -> str:
    for ln in PROPS.read_text(encoding="utf-8").splitlines():
        if ln.strip().startswith(key):
            return ln.split("=", 1)[1].strip()
    return default


def set_props(pairs: dict[str, str]) -> None:
    """按键改 server.properties，保住原换行风格（避免整文件行尾 diff）。"""
    text = PROPS.read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    for key, val in pairs.items():
        for i, ln in enumerate(lines):
            if ln.strip().startswith(key):
                lines[i] = f"{key}={val}"
                break
        else:
            lines.append(f"{key}={val}")
    tail = nl if text.endswith(("\n", "\r\n")) else ""   # 原文件末尾无换行则保持无
    with PROPS.open("w", encoding="utf-8", newline="") as f:
        f.write(nl.join(lines) + tail)


# ---------------------------------------------------------------- junction 安全 IO

def _is_reparse(p: Path) -> bool:
    """Python 3.10 Windows 实测：junction 的 is_symlink()/os.path.islink() 均 False，
    is_dir(follow_symlinks=False)=True（会穿透遍历）——唯一可靠判定是 lstat 的
    FILE_ATTRIBUTE_REPARSE_POINT（0x400）。"""
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400)
    except (OSError, AttributeError):
        return False


def _safe_rmtree(p: Path) -> None:
    """递归删除，但遇到 junction（如 Mir200/Map）只 rmdir 摘链接，绝不进入内容。"""
    for child in p.iterdir():
        try:
            if child.is_dir():
                if _is_reparse(child):
                    child.rmdir()
                else:
                    _safe_rmtree(child)
                    try:
                        child.rmdir()   # 递归尾部已删过，二次 rmdir 属正常竞态
                    except FileNotFoundError:
                        pass
            else:
                child.unlink()
        except FileNotFoundError:
            pass  # 清理容忍：条目已被前一步带走
    try:
        p.rmdir()
    except FileNotFoundError:
        pass


def _mk_junction(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                   check=True, capture_output=True)


# ---------------------------------------------------------------- build-176

def _s(v) -> str:
    if v is None:
        return "0"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip() or "0"


def _csv_read(path: Path) -> tuple[list[str], list[list[str]]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], [r for r in rows[1:] if r and any(c.strip() for c in r)]


def _csv_write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    bad = [i for i, r in enumerate(rows) if len(r) != len(header)]
    if bad:
        sys.exit(f"{path.name}: {len(bad)} 行列数 != {len(header)}（首行 {bad[0]}），拒绝写出")
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\r\n")
        w.writerow(header)
        w.writerows(rows)


def _merge_csv(tree_db: Path, con: sqlite3.Connection) -> None:
    """三张 CSV 就地合并：同名覆盖数值（StdItems 保留本地 Idx）、独有行插入。"""
    cols24 = ["Idx", "Name", "Stdmode", "Shape", "Weight", "Anicount", "Source", "Reserved",
              "Looks", "DuraMax", "Ac", "Ac2", "Mac", "Mac2", "Dc", "Dc2", "Mc", "Mc2",
              "Sc", "Sc2", "Need", "NeedLevel", "Price", "Stock"]
    cols22 = ["Name", "Race", "RaceImg", "Appr", "Lvl", "Undead", "CoolEye", "Exp", "HP", "MP",
              "AC", "MAC", "DC", "DCMAX", "MC", "SC", "SPEED", "HIT", "WALK_SPD", "WalkStep",
              "WaLkWait", "ATTACK_SPD"]
    cols19 = ["MagID", "MagName", "EffectType", "Effect", "Spell", "Power", "MaxPower",
              "DefSpell", "DefPower", "DefMaxPower", "Job", "NeedL1", "L1Train",
              "NeedL2", "L2Train", "NeedL3", "L3Train", "Delay", "Descr"]

    h, rows = _csv_read(tree_db / "StdItems.csv")
    red = [[_s(v) for v in r] for r in con.execute(
        "SELECT " + ",".join(cols24) + " FROM StdItems ORDER BY Idx")]
    by_name = {r[1]: i for i, r in enumerate(rows)}
    used_idx = {r[0] for r in rows}
    max_idx = max((int(r[0]) for r in rows if r[0].lstrip("-").isdigit()), default=0)
    upd = app = 0
    for rr in red:
        if rr[1] in by_name:
            i = by_name[rr[1]]
            rows[i] = [rows[i][0]] + rr[1:]   # 保住本地 Idx：角色 storage 按 idx 引用
            upd += 1
        else:
            idx = rr[0]
            if idx in used_idx:
                max_idx += 1
                idx = str(max_idx)
            used_idx.add(idx)
            rows.append([idx] + rr[1:])
            app += 1
    _csv_write(tree_db / "StdItems.csv", h, rows)
    print(f"[build] StdItems 覆盖 {upd} 新增 {app} 共 {len(rows)}")

    for table, cols in (("Monster", cols22), ("Magic", cols19)):
        h, rows = _csv_read(tree_db / f"{table}.csv")
        red = [[_s(v) for v in r] for r in con.execute(
            f"SELECT {','.join(cols)} FROM {table}")]
        by_key = {r[0]: i for i, r in enumerate(rows)}
        upd = app = 0
        for rr in red:
            if rr[0] in by_key:
                rows[by_key[rr[0]]] = rr
                upd += 1
            else:
                rows.append(rr)
                app += 1
        _csv_write(tree_db / f"{table}.csv", h, rows)
        print(f"[build] {table} 覆盖 {upd} 新增 {app} 共 {len(rows)}")


def cmd_build(name: str, redm2: Path, src176: Path) -> None:
    if not redm2.exists():
        sys.exit(f"找不到 1.76 参照库 {redm2}")
    e176 = src176 / "Mir200" / "Envir"
    if not e176.is_dir():
        sys.exit(f"找不到 176 服务端包 {e176}")
    tree = TREES / name
    if tree.exists():
        print(f"[build] 重建：先安全删除 {tree}")
        _safe_rmtree(tree)
    TREES.mkdir(parents=True, exist_ok=True)
    shutil.copytree(MAIN_TREE, tree,
                    ignore=shutil.ignore_patterns("Map", ".gbk-backup"))
    print(f"[build] 主树复制完成（Map junction 与 .gbk-backup 除外）")
    _mk_junction(tree / "Mir200" / "Map", ROOT / "resources" / "Map")

    con = sqlite3.connect(redm2)
    _merge_csv(tree / "mud2" / "DB", con)
    con.close()

    tenv = tree / "Mir200" / "Envir"
    for d in ENVIR_DIRS:  # 主树旧副本整目录替换（路径在副本树内，_safe_rmtree 不会碰到 junction）
        old = tenv / d
        if old.exists():
            _safe_rmtree(old)
        shutil.copytree(e176 / d, old)
    for f in ENVIR_FILES:
        shutil.copy2(e176 / f, tenv / f)
    (tree / "profile.json").write_text(json.dumps({
        "name": name,
        "note": "CSV=REDM2 数值合并；Envir 五件套=176-mir2-red 整抄；其余文件同主树；"
                "生效时 db 独立为 data/jootm2-<name>.db"},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[build] 副本树就绪: {tree}")


# ---------------------------------------------------------------- use / list

def _clear_template_tables(db_arg: str | None) -> None:
    """seed_game_data 只在 std_items 空表时导入 CSV，切树后必须清表。"""
    db = Path(db_arg) if db_arg else ROOT / get_prop("db.file", "data/jootm2.db")
    if not db.exists():
        print(f"[use] 无 {db}（首次启动自动建库导入），跳过清表")
        return
    con = sqlite3.connect(db)
    with con:
        for t in ("std_items", "monsters", "magics"):
            con.execute(f"DELETE FROM {t}")
    con.close()
    print(f"[use] 已清空模板表 {db}（下次启动从生效树 CSV 重灌）")


def cmd_use(name: str, db_arg: str | None) -> None:
    if name == "main":
        pairs = {"gamedata.root": "gamedata", "db.file": "data/jootm2.db"}
    else:
        tree = TREES / name
        if not tree.is_dir():
            sys.exit(f"副本树 '{name}' 不存在（build-176 或手工放置于 {TREES}）")
        if not (tree / "Mir200" / "Map").exists():
            _mk_junction(tree / "Mir200" / "Map", ROOT / "resources" / "Map")
            print("[use] 副本树缺 Map junction，已补建（git clone 后属正常）")
        pairs = {"gamedata.root": f"data/gamedata/{name}",
                 "db.file": f"data/jootm2-{name}.db"}
    set_props(pairs)
    _clear_template_tables(db_arg)
    print(f"[use] 生效树 {pairs['gamedata.root']} + 数据库 {pairs['db.file']}，重启 pyserver 换世界")


def cmd_list() -> None:
    cur = get_prop("gamedata.root", "gamedata")
    print(f"当前生效树: {cur}    当前 db: {get_prop('db.file', 'data/jootm2.db')}")
    print(f"  主树 gamedata" + ("   <= 生效" if cur == "gamedata" else ""))
    if TREES.exists():
        for d in sorted(p for p in TREES.iterdir() if p.is_dir()):
            pj = d / "profile.json"
            note = json.loads(pj.read_text(encoding="utf-8")).get("note", "") if pj.exists() else "(手工放置的树)"
            mark = "   <= 生效" if cur == d.as_posix().replace("\\", "/") else ""
            print(f"  {d.name:12} {note[:56]}{mark}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build-176"); p.add_argument("--name", default="176")
    p.add_argument("--redm2", type=Path, default=REDM2_DEFAULT)
    p.add_argument("--src176", type=Path, default=SRC176_DEFAULT)
    p = sub.add_parser("use"); p.add_argument("name"); p.add_argument("--db", default=None)
    sub.add_parser("list")
    a = ap.parse_args()
    if a.cmd == "build-176":
        cmd_build(a.name, a.redm2, a.src176)
    elif a.cmd == "use":
        cmd_use(a.name, a.db)
    else:
        cmd_list()


if __name__ == "__main__":
    main()
