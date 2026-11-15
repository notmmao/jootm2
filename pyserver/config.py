"""配置加载：直读 Java 侧的 ``config/server.properties``，并支持环境变量/CLI 覆盖。

路径解析规则与 ``com.jootm2.server.Main.resolve`` 保持一致：绝对路径直接用；
相对路径优先取当前工作目录，不存在时回退到项目根目录（pyserver 的上一级）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# GM 账号（登录名）：可以使用 @命令（移动/金/等级/制造/刷怪）。
# 对齐原版 m_btPermission >= 6 的 GM 权限语义（ObjBase.pas CmdXXX 系列）。
GM_ACCOUNTS: frozenset[str] = frozenset({"player"})

DEFAULTS: dict[str, str] = {
    "server.port": "8080",
    "db.file": "data/jootm2.db",
    "game.tickMs": "100",
    "game.savePeriodSec": "30",
    "web.root": "frontend",
    "resources.root": "resources",
    "resources.prefix": "/resources",
    "website.root": "data/website",
    "gamedata.root": "gamedata",
    "world.width": "200",
    "world.height": "200",
    "world.playerSpawnX": "100",
    "world.playerSpawnY": "100",
}

_ENV_KEYS = {
    "port": "JOOTM2_PORT",
    "host": "JOOTM2_HOST",
    "db": "JOOTM2_DB",
    "web": "JOOTM2_WEB_ROOT",
    "resources": "JOOTM2_RESOURCES",
    "website": "JOOTM2_WEBSITE",
    "gamedata": "JOOTM2_GAMEDATA",
    "root": "JOOTM2_ROOT",
}


@dataclass(frozen=True, slots=True)
class ServerConfig:
    root: Path
    host: str = "0.0.0.0"
    port: int = 8080
    db_file: Path = PROJECT_ROOT / "data" / "jootm2.db"
    tick_ms: int = 100
    save_period_sec: int = 30
    web_root: Path = PROJECT_ROOT / "frontend"
    resources_root: Path = PROJECT_ROOT / "resources"
    resources_prefix: str = "/resources"
    website_root: Path = PROJECT_ROOT / "data" / "website"
    gamedata_root: Path = PROJECT_ROOT / "gamedata"
    world_width: int = 200
    world_height: int = 200
    player_spawn_x: int = 100
    player_spawn_y: int = 100
    backlog: int = 256

    @property
    def mud2_dir(self) -> Path:
        return self.gamedata_root / "mud2" / "DB"

    @property
    def envir_dir(self) -> Path:
        return self.gamedata_root / "Mir200" / "Envir"

    @property
    def map_dir(self) -> Path:
        return self.gamedata_root / "Mir200" / "Map"

    @property
    def website_prefix(self) -> str:
        """数据库静态站点固定挂在 ``/site``（与游戏客户端共用端口）。"""
        return "/site"

    @property
    def notice_file(self) -> Path:
        return self.gamedata_root / "Mir200" / "Notice" / "Notice.txt"

    def describe(self) -> str:
        return (
            f"port={self.port} db={self.db_file} tick={self.tick_ms}ms\n"
            f"  web       = {self.web_root}\n"
            f"  gamedata  = {self.gamedata_root}\n"
            f"  resources = {self.resources_root}\n"
            f"  website   = {self.website_root}"
        )


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    cwd = (Path.cwd() / p).resolve()
    if cwd.exists():
        return cwd
    alt = (root / p).resolve()
    return alt if alt.exists() else cwd


def load(config_path: str | Path | None = None, **overrides) -> ServerConfig:
    """加载配置。``overrides`` 的键：port/host/db/web/resources/gamedata/root/config。

    优先级：函数参数 > 环境变量 > properties > 内置默认值。
    """
    root = Path(overrides.get("root") or os.environ.get(_ENV_KEYS["root"]) or PROJECT_ROOT).resolve()
    cfg_file = Path(
        config_path
        or overrides.get("config")
        or os.environ.get("JOOTM2_CONFIG")
        or (root / "config" / "server.properties")
    )
    raw = dict(DEFAULTS)
    if cfg_file.is_file():
        for line in cfg_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")) or "=" not in line:
                continue
            key, value = line.split("=", 1)
            raw[key.strip()] = value.strip()

    def val(prop: str, env_key: str | None, over_key: str | None, default: str = "") -> str:
        if over_key and overrides.get(over_key) is not None:
            return str(overrides[over_key])
        if env_key and os.environ.get(env_key):
            return os.environ[env_key]
        return raw.get(prop, default)

    def as_int(prop: str, env_key: str | None, over_key: str | None, default: int) -> int:
        try:
            return int(val(prop, env_key, over_key, str(default)))
        except ValueError:
            return default

    return ServerConfig(
        root=root,
        host=val("server.host", _ENV_KEYS["host"], "host", "0.0.0.0"),
        port=as_int("server.port", _ENV_KEYS["port"], "port", 8080),
        db_file=_resolve(root, val("db.file", _ENV_KEYS["db"], "db", "data/jootm2.db")),
        tick_ms=max(10, as_int("game.tickMs", None, "tick_ms", 100)),
        save_period_sec=as_int("game.savePeriodSec", None, None, 30),
        web_root=_resolve(root, val("web.root", _ENV_KEYS["web"], "web", "frontend")),
        resources_root=_resolve(root, val("resources.root", _ENV_KEYS["resources"], "resources", "resources")),
        resources_prefix=val("resources.prefix", None, None, "/resources"),
        website_root=_resolve(root, val("website.root", _ENV_KEYS["website"], "website", "data/website")),
        gamedata_root=_resolve(root, val("gamedata.root", _ENV_KEYS["gamedata"], "gamedata", "gamedata")),
        world_width=as_int("world.width", None, None, 200),
        world_height=as_int("world.height", None, None, 200),
        player_spawn_x=as_int("world.playerSpawnX", None, None, 100),
        player_spawn_y=as_int("world.playerSpawnY", None, None, 100),
        backlog=as_int("server.backlog", None, None, 256),
    )
