"""SQLite 持久层：与 Java 侧共用同一个库文件、同一套表结构与密码哈希。

- 表定义逐条对齐 ``Database.initSchema()``（含旧库 ALTER 容错）。
- 密码 ``SHA-256(salt + password)`` 小写 hex，与 ``DbUtil.hashPassword`` 等价，
  因此 Java 建的老账号可以直接在 Python 服务端登录，反之亦可。
- 模板表里的 ``data`` 列是 Gson JSON，读写都经 :mod:`pyserver.templates`。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .templates import (
    ItemTemplate, MagicTemplate, MonsterTemplate,
    dumps, item_from_csv, magic_from_csv, monster_from_csv, num, read_csv_rows,
)

CHAR_COLUMNS = (
    "id, account_id, name, sex, hair, job, level, exp, map_name, x, y, dir, "
    "hp, mp, gold, home_map, home_x, home_y, items, storage, skills, quick_slots, created_at"
)

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS accounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        last_login INTEGER)""",
    """CREATE TABLE IF NOT EXISTS characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account_id INTEGER NOT NULL,
        name TEXT NOT NULL UNIQUE,
        sex INTEGER NOT NULL DEFAULT 0,
        hair INTEGER NOT NULL DEFAULT 0,
        job INTEGER NOT NULL DEFAULT 0,
        level INTEGER NOT NULL DEFAULT 1,
        exp INTEGER NOT NULL DEFAULT 0,
        map_name TEXT NOT NULL DEFAULT 'default',
        x INTEGER NOT NULL DEFAULT 0,
        y INTEGER NOT NULL DEFAULT 0,
        dir INTEGER NOT NULL DEFAULT 0,
        hp INTEGER NOT NULL DEFAULT 0,
        mp INTEGER NOT NULL DEFAULT 0,
        gold INTEGER NOT NULL DEFAULT 0,
        home_map TEXT NOT NULL DEFAULT 'default',
        home_x INTEGER NOT NULL DEFAULT 0,
        home_y INTEGER NOT NULL DEFAULT 0,
        items TEXT NOT NULL DEFAULT '[]',
        storage TEXT NOT NULL DEFAULT '[]',
        skills TEXT NOT NULL DEFAULT '[]',
        quick_slots TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL,
        FOREIGN KEY (account_id) REFERENCES accounts(id))""",
    "CREATE TABLE IF NOT EXISTS std_items (idx INTEGER PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS monsters (name TEXT PRIMARY KEY, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS magics (mag_id INTEGER PRIMARY KEY, data TEXT NOT NULL)",
    # 任务变量（NPC 脚本 SET/CHECK [n]）。独立于 characters 表：Java 侧共用
    # characters 表结构，绝不往里加列；这张表只由 Python 端读写。
    "CREATE TABLE IF NOT EXISTS character_quests ("
    "character_id INTEGER PRIMARY KEY, params TEXT NOT NULL DEFAULT '{}', "
    "FOREIGN KEY (character_id) REFERENCES characters(id) ON DELETE CASCADE)",
)


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    username: str
    password_hash: str
    salt: str
    created_at: int
    last_login: int


def new_salt() -> str:
    return secrets.token_hex(16)


def hash_password(salt: str, password: str) -> str:
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            for sql in _SCHEMA:
                self.conn.execute(sql)
        # 旧库补列（Java 侧同样用「尝试 ALTER + 忽略已存在」的方式迁移）
        for col in ("storage", "quick_slots"):
            try:
                self.conn.execute(
                    f"ALTER TABLE characters ADD COLUMN {col} TEXT NOT NULL DEFAULT '[]'"
                )
            except sqlite3.OperationalError:
                pass

    def close(self) -> None:
        self.conn.close()

    def scalar(self, sql: str, args: tuple = ()) -> int:
        row = self.conn.execute(sql, args).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------ 模板导入

    def seed_game_data(self, mud2_dir: Path) -> None:
        """模板三表（std_items/monsters/magics）**每次启动都从 gamedata CSV 重灌**。

        CSV 是唯一事实源，表只是它的镜像：改 CSV → 重启即生效，不再出现
        「旧库把新值压住」的问题（HANDOFF §73 修炼点、§79 商品数值都踩过）。
        只重建模板表，`accounts/characters` 等玩家数据绝不影响。
        CSV 缺失或读取失败：保留现有表；表也为空才落内置 defaults（冒烟场景）。
        """
        if self._import_csv(mud2_dir):
            return
        if self.scalar("SELECT COUNT(*) FROM std_items") == 0:
            self._seed_defaults()

    def _import_csv(self, mud2_dir: Path) -> bool:
        try:
            items = read_csv_rows(mud2_dir / "StdItems.csv")
            monsters = read_csv_rows(mud2_dir / "Monster.csv")
            magics = read_csv_rows(mud2_dir / "Magic.csv")
            if not items:
                return False
            with self.conn:
                self.conn.execute("DELETE FROM std_items")
                self.conn.execute("DELETE FROM monsters")
                self.conn.execute("DELETE FROM magics")
                for row in items:
                    t = item_from_csv(row)
                    self.conn.execute(
                        "INSERT OR REPLACE INTO std_items (idx, data) VALUES (?, ?)",
                        (num(row[0]), dumps(t.to_storage())),
                    )
                for row in monsters:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO monsters (name, data) VALUES (?, ?)",
                        (row[0], dumps(monster_from_csv(row).to_storage())),
                    )
                for row in magics:
                    # Magic.csv 里 GM 变体与正式技能 ID 重复：保留先出现的正式技能。
                    self.conn.execute(
                        "INSERT OR IGNORE INTO magics (mag_id, data) VALUES (?, ?)",
                        (row[0], dumps(magic_from_csv(row).to_storage())),
                    )
            print(f"[db] imported CSV: items={len(items)}, monsters={len(monsters)}, "
                  f"magics={len(magics)}")
            return True
        except (OSError, ValueError, sqlite3.Error) as e:
            print(f"[db] failed to import CSV tables: {e}")
            return False

    def _seed_defaults(self) -> None:
        """CSV 缺失时的最小可玩模板，与 ``DbUtil`` 的默认值一致。"""
        defaults = [
            (0, dict(name="木剑", stdMode=5, shape=1, weight=6, looks=117,
                     duraMax=3000, dcMin=2, dcMax=5, price=100, needLevel=1)),
            (1, dict(name="布衣", stdMode=10, shape=1, weight=8,
                     duraMax=3000, acMin=2, acMax=2, macMax=1, price=50, needLevel=1)),
            (2, dict(name="金创药(小)", stdMode=0, looks=81, duraMax=1, hpAdd=30, price=20)),
            (3, dict(name="魔法药(小)", stdMode=0, looks=82, duraMax=1, mpAdd=20, price=20)),
        ]
        mons = [
            dict(name="鸡", race=80, appr=46, level=1, exp=10, hp=8, dc=1, maxDc=2,
                 speed=3, hit=5, walkSpeed=800, attackSpeed=1000),
            dict(name="鹿", race=52, appr=24, level=2, exp=20, hp=15, ac=1, dc=1, maxDc=3,
                 speed=3, hit=5, walkSpeed=700, attackSpeed=1000),
            dict(name="稻草人", race=80, appr=20, level=3, exp=40, hp=30, dc=2, maxDc=5,
                 speed=3, hit=6, walkSpeed=800, attackSpeed=1200),
        ]
        with self.conn:
            for idx, fields in defaults:
                self.conn.execute("INSERT OR REPLACE INTO std_items VALUES (?, ?)",
                                  (idx, dumps(ItemTemplate(index=idx, **fields).to_storage())))
            for m in mons:
                self.conn.execute("INSERT OR REPLACE INTO monsters VALUES (?, ?)",
                                  (m["name"], dumps(MonsterTemplate(**m).to_storage())))
        print("[db] seeded fallback templates (CSV not found)")

    def load_item_templates(self) -> dict[int, ItemTemplate]:
        out: dict[int, ItemTemplate] = {}
        for row in self.conn.execute("SELECT idx, data FROM std_items ORDER BY idx"):
            t = ItemTemplate.from_storage(json.loads(row["data"]))
            t.index = int(row["idx"])
            out[t.index] = t
        return out

    def load_monster_templates(self) -> dict[str, MonsterTemplate]:
        return {
            row["name"]: MonsterTemplate.from_storage(json.loads(row["data"]))
            for row in self.conn.execute("SELECT name, data FROM monsters")
        }

    def load_magic_templates(self) -> dict[int, MagicTemplate]:
        return {
            int(row["mag_id"]): MagicTemplate.from_storage(json.loads(row["data"]))
            for row in self.conn.execute("SELECT mag_id, data FROM magics ORDER BY mag_id")
        }

    # ------------------------------------------------------------ 账号

    def find_account(self, username: str) -> Account | None:
        row = self.conn.execute(
            "SELECT id, username, password_hash, salt, created_at, last_login "
            "FROM accounts WHERE username = ?", (username,)
        ).fetchone()
        return Account(*row) if row else None

    def register(self, username: str, password: str) -> Account | None:
        if self.find_account(username) is not None:
            return None
        salt, now = new_salt(), int(time.time() * 1000)
        digest = hash_password(salt, password)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO accounts (username, password_hash, salt, created_at, last_login) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, digest, salt, now, now),
            )
        return Account(int(cur.lastrowid), username, digest, salt, now, now)

    def authenticate(self, username: str, password: str) -> Account | None:
        a = self.find_account(username)
        if a is None or not hmac.compare_digest(hash_password(a.salt, password), a.password_hash):
            return None
        with self.conn:
            self.conn.execute("UPDATE accounts SET last_login = ? WHERE id = ?",
                              (int(time.time() * 1000), a.id))
        return a

    # ------------------------------------------------------------ 角色

    @staticmethod
    def _char(row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("items", "storage", "quick_slots"):
            try:
                d[key] = json.loads(d.get(key) or "[]")
            except (ValueError, TypeError):
                d[key] = []
        return d

    def list_characters(self, account_id: int) -> list[dict]:
        return [self._char(r) for r in self.conn.execute(
            f"SELECT {CHAR_COLUMNS} FROM characters WHERE account_id = ? ORDER BY id",
            (account_id,))]

    def find_character(self, name: str) -> dict | None:
        row = self.conn.execute(
            f"SELECT {CHAR_COLUMNS} FROM characters WHERE name = ?", (name,)
        ).fetchone()
        return self._char(row) if row else None

    def name_exists(self, name: str) -> bool:
        return self.scalar("SELECT COUNT(*) FROM characters WHERE name = ?", (name,)) > 0

    def insert_character(self, c: dict) -> int:
        cols = ("account_id, name, sex, hair, job, level, exp, map_name, x, y, dir, "
                "hp, mp, gold, home_map, home_x, home_y, items, storage, skills, "
                "quick_slots, created_at")
        values = (
            c["account_id"], c["name"], c["sex"], c["hair"], c["job"], c["level"],
            c["exp"], c["map_name"], c["x"], c["y"], c["dir"], c["hp"], c["mp"],
            c["gold"], c["home_map"], c["home_x"], c["home_y"],
            dumps(c["items"]), dumps(c["storage"]), c["skills"],
            dumps(c["quick_slots"]), c["created_at"],
        )
        with self.conn:
            cur = self.conn.execute(
                f"INSERT INTO characters ({cols}) VALUES ({','.join('?' * len(values))})", values
            )
        return int(cur.lastrowid)

    def update_character(self, c: dict) -> None:
        cols = ("sex=?, hair=?, job=?, level=?, exp=?, map_name=?, x=?, y=?, dir=?, "
                "hp=?, mp=?, gold=?, home_map=?, home_x=?, home_y=?, items=?, storage=?, "
                "skills=?, quick_slots=?")
        values = (
            c["sex"], c["hair"], c["job"], c["level"], c["exp"], c["map_name"],
            c["x"], c["y"], c["dir"], c["hp"], c["mp"], c["gold"], c["home_map"],
            c["home_x"], c["home_y"], dumps(c["items"]), dumps(c["storage"]),
            c["skills"], dumps(c["quick_slots"]), c["id"],
        )
        with self.conn:
            self.conn.execute(f"UPDATE characters SET {cols} WHERE id = ?", values)

    def delete_character(self, account_id: int, name: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM characters WHERE account_id = ? AND name = ?",
                              (account_id, name))

    # ------------------------------------------------------------ 任务变量
    # NPC 脚本的 SET/CHECK [n]（原版 QuestParams）必须跨登录持久：否则领过
    # 一次性奖励的角色重登后 [n] 归零，奖励可无限领（玛法领路人 5000 金币）。

    def load_quest_params(self, character_id: int) -> dict[int, int]:
        row = self.conn.execute(
            "SELECT params FROM character_quests WHERE character_id = ?",
            (character_id,),
        ).fetchone()
        if not row:
            return {}
        try:
            raw = json.loads(row["params"])
            return {int(k): int(v) for k, v in raw.items()}
        except (ValueError, TypeError, AttributeError):
            return {}

    def save_quest_params(self, character_id: int, params: dict[int, int]) -> None:
        payload = json.dumps({str(k): int(v) for k, v in params.items()},
                             sort_keys=True, separators=(",", ":"))
        with self.conn:
            self.conn.execute(
                "INSERT INTO character_quests(character_id, params) VALUES(?, ?) "
                "ON CONFLICT(character_id) DO UPDATE SET params=excluded.params",
                (character_id, payload),
            )

    def max_item_make_index(self) -> int:
        top = 0
        for row in self.conn.execute("SELECT items, storage FROM characters"):
            for raw in (row["items"], row["storage"]):
                try:
                    for it in json.loads(raw or "[]"):
                        top = max(top, int(it.get("makeIndex", 0)))
                except (ValueError, TypeError):
                    continue
        return top
