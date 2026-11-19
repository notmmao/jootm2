"""无界面启动 pyserver（临时 SQLite，不碰生效库），可切数据树。"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from . import clock

ROOT = Path(__file__).resolve().parents[2]          # 仓库根
BAL = ROOT / "balance"
clock.install()
sys.path.insert(0, str(ROOT))

from pyserver.config import load  # noqa: E402
from pyserver.db import Database  # noqa: E402
from pyserver.drop_rates import load_drop_rates  # noqa: E402


def tree_path(tree: str | None) -> Path | None:
    if not tree:
        return None
    if tree in ("main", "gamedata"):
        return ROOT / "gamedata"
    p = Path(tree)
    if p.is_dir():
        return p.resolve()
    return ROOT / "data" / "gamedata" / tree


def load_config(tree: str | None = None):
    cfg = load()
    tp = tree_path(tree)
    if tp is not None:
        cfg = replace(cfg, gamedata_root=tp)
    # 副本树的 Map 是 junction（clone 后要 switch_data use 才补建）；缺失时借主树地图（地图两树共享）
    if not (cfg.gamedata_root / "Mir200" / "Map").is_dir():
        main_map = ROOT / "gamedata" / "Mir200" / "Map"
        type(cfg).map_dir = property(lambda self: main_map)
    return cfg


class Session:
    """吞掉所有下发（只保留系统消息尾巴便于调试）。"""

    def __init__(self):
        self.account_id = 0
        self.account_name = ""
        self.player_name = ""
        self.closed = False
        self.last: list = []

    def send_json(self, obj):
        pass

    def send_text(self, text):
        pass

    def close(self):
        self.closed = True


def make_service(tree: str | None = None, quiet: bool = True, rates_override: dict | None = None):
    from pyserver.service import GameService

    cfg = load_config(tree)
    db_path = Path(tempfile.mkdtemp(prefix="mirbal-")) / "sim.db"
    buf = io.StringIO()
    ctx = contextlib.redirect_stdout(buf) if quiet else contextlib.nullcontext()
    with ctx:
        db = Database(db_path)
        db.seed_game_data(cfg.mud2_dir)
        srv = GameService(cfg, db)
    if rates_override is not None:
        srv.world.drop_rates = load_drop_rates(None).__class__(**rates_override)
    return cfg, srv


def rates_file() -> Path:
    return ROOT / "config" / "game_rates.json"


def current_rates_raw() -> dict:
    p = rates_file()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


_seq = [0]


def new_player(srv, job: int = 0, sex: int = 0, name: str | None = None):
    _seq[0] += 1
    name = name or f"Sim{job}x{_seq[0]}"
    s = Session()
    with contextlib.redirect_stdout(io.StringIO()):
        for msg in ({"type": "register", "username": name, "password": "sim12345"},
                    {"type": "login", "username": name, "password": "sim12345"},
                    {"type": "createCharacter", "name": name, "sex": sex, "hair": 1, "job": job},
                    {"type": "enterGame", "name": name},
                    {"type": "noticeOk", "name": name}):
            srv.on_message(s, json.dumps(msg, ensure_ascii=False))
    p = srv.find_by_session(s)
    if p is None:
        raise RuntimeError("登录链路失败")
    return p
