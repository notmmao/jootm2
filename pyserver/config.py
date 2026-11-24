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
    # /site 访问跳转到 list2（Vercel CDN）。设为空字符串则回退到本地静态服务。
    "website.redirect": "https://list.notmmao.com/mir2-wiki/",
    "gamedata.root": "gamedata",
    "world.width": "200",
    "world.height": "200",
    "world.playerSpawnX": "100",
    "world.playerSpawnY": "100",
    # ---- 离线修炼（docs/OFFLINE-AFK-PLAN.md §3.3/§3.6，全部可配）----
    # 测试期默认值调低方便体验；正式服建议 minLevel>=30、excludedMaps 加长。
    "afk.minLevel": "15",              # 开启离线修炼的等级门槛
    "afk.excludedMaps": "0",           # 黑名单地图（逗号分隔），默认只禁比奇省
    "afk.perMapLimit": "2",            # 每张图离线代理并发上限，超出拒绝
    "afk.maxAgents": "10",             # 全服离线代理总量上限
    "afk.dailyMinutes": "360",         # 每角色每日修炼总时长（分钟）
    "afk.expRate": "0.7",              # 离线经验系数（掉落/金币不打折）
    "afk.lootSightCut": "0.5",         # 离线代理拾取视野系数（给在线玩家留捡漏）
    "afk.deathStop": "3",              # 单次修炼内累计死亡上限（防药尽送死）
    "afk.claimMinutes": "240",         # 玛法领路人单次领取时长（分钟）
    "afk.claimPerDay": "1",            # 每日限领次数
    "afk.poolCapMinutes": "720",       # 余额上限（分钟）
    "afk.newbieMinutes": "240",        # 新号赠送时长（分钟）
    "afk.restartBonusMinutes": "60",   # 重启续挂补偿时长（分钟）
    "afk.supplyEnabled": "1",          # 补给闭环开关（回城卖货买药，§3.4 v2）
    "afk.supplyTownMap": "0",          # 补给城地图 id（默认比奇省）
    "afk.supplyMaxFails": "5",         # 连续补给失败上限（0=永不因补给失败停挂）
    "afk.supplyRepair": "1",           # 补给时修理背包低持久装备（用户 2026-11-23）
    "afk.supplyUnbind": "1",           # 补给时拆药水捆（用户 2026-11-23）
    # ---- 离线修炼运行日志（JSONL，用户 2026-11-24 要求）----
    # 一行一事件，路径 <logDir>/<日期>_<角色名>.jsonl；关掉只需 logEnabled=0。
    "afk.logEnabled": "1",
    "afk.logDir": "data/afk-logs",
    "afk.logSampleMinutes": "1",       # 运行采样间隔（分钟），0=不采样只记关键事件
    # ---- 周末双倍活动（用户新增功能，全部可配）----
    "weekend.enabled": "1",            # 总开关
    "weekend.expRate": "2.0",          # 击杀经验倍率
    "weekend.dropRate": "2.0",         # 掉落概率倍率（封顶 100%）
    "weekend.days": "6,7",             # 生效星期：1=周一 .. 7=周日
    "weekend.startHour": "0",          # 起始小时（0~24，可含小数如 20.5）
    "weekend.endHour": "24",           # 结束小时（不含；小于起始小时=跨午夜）
    "weekend.force": "0",              # 1=强制开启（临时活动/测试）
    # ---- 法定节假日双倍经验（用户新增功能）----
    "holiday.enabled": "1",            # 总开关
    "holiday.expRate": "2.0",          # 击杀经验倍率（只加经验，不加爆率）
    "holiday.force": "0",              # 1=强制开启（临时活动/测试）
    "holiday.dates": "",               # 自定义日期 YYYY-MM-DD 逗号分隔；空=用内置2026表
    # ---- 精英怪（用户新增功能，原版无此机制）----
    "elite.rate": "0.05",              # 天然刷怪成为精英的概率（0=关闭）
    "elite.hpMult": "2.0",             # 精英 HP 倍率
    "elite.dcMult": "1.5",             # 精英攻击(DC)倍率
    "elite.expMult": "3.0",            # 精英经验倍率
    "elite.dropMult": "2.0",           # 精英掉落概率倍率（判定时乘，不改 MonItems 表）
}

_ENV_KEYS = {
    "port": "JOOTM2_PORT",
    "host": "JOOTM2_HOST",
    "db": "JOOTM2_DB",
    "web": "JOOTM2_WEB_ROOT",
    "resources": "JOOTM2_RESOURCES",
    "website": "JOOTM2_WEBSITE",
    "website_redirect": "JOOTM2_WEBSITE_REDIRECT",
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
    website_redirect: str = "https://list.notmmao.com/mir2-wiki/"
    gamedata_root: Path = PROJECT_ROOT / "gamedata"
    world_width: int = 200
    world_height: int = 200
    player_spawn_x: int = 100
    player_spawn_y: int = 100
    backlog: int = 256
    # ---- 离线修炼 ----
    afk_min_level: int = 15
    afk_excluded_maps: tuple[str, ...] = ("0",)
    afk_per_map_limit: int = 2
    afk_max_agents: int = 10
    afk_daily_minutes: int = 360
    afk_exp_rate: float = 0.7
    afk_loot_sight_cut: float = 0.5
    afk_death_stop: int = 3
    afk_claim_minutes: int = 240
    afk_claim_per_day: int = 1
    afk_pool_cap_minutes: int = 720
    afk_newbie_minutes: int = 240
    afk_restart_bonus_minutes: int = 60
    afk_supply_enabled: int = 1
    afk_supply_town_map: str = "0"
    afk_supply_max_fails: int = 5      # 连续补给失败上限，达上限转长冷却而非停机
    afk_gold_per_day: int = 5000       # 玛法领路人每日金币补助（用户 2026-11-18：金币紧张）
    afk_supply_repair: int = 1         # 补给时修理背包低持久装备（用户 2026-11-23）
    afk_supply_unbind: int = 1         # 补给时拆药水捆（用户 2026-11-23）
    # ---- 离线修炼运行日志（JSONL）----
    afk_log_enabled: int = 1           # 总开关（0=完全不写日志）
    afk_log_dir: Path = PROJECT_ROOT / "data" / "afk-logs"
    afk_log_sample_minutes: int = 1    # 运行采样间隔（分钟），0=只记关键事件
    # PK 点衰减间隔（毫秒）。原版 ObjBase.pas:7719 是 2 分钟/点（黄名 200 分钟、
    # 红名 400 分钟清白），用户 2026-11-22 认为偏长，主动偏离改默认 15 秒/点。
    # 设回 120000 即恢复原版行为。
    pk_decay_interval_ms: int = 15_000
    # 在线托管满包行为：0=继续打怪、停止拾取（新默认）；1=回城停机（旧行为）。
    autopilot_bag_full_stop: int = 0
    # ---- 周末双倍活动 ----
    weekend_enabled: int = 1
    weekend_exp_rate: float = 2.0
    weekend_drop_rate: float = 2.0
    weekend_days: tuple[int, ...] = (6, 7)
    weekend_start_hour: float = 0.0
    weekend_end_hour: float = 24.0
    weekend_force: int = 0
    # ---- 法定节假日双倍经验 ----
    holiday_enabled: int = 1
    holiday_exp_rate: float = 2.0
    holiday_force: int = 0
    holiday_dates: str = ""
    # ---- 精英怪 ----
    elite_rate: float = 0.05
    elite_hp_mult: float = 2.0
    elite_dc_mult: float = 1.5
    elite_exp_mult: float = 3.0
    elite_drop_mult: float = 2.0

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
    def website_redirect_enabled(self) -> bool:
        """配置了跳转地址时，``/site`` 请求 302 到该地址；空串则本地静态服务。"""
        return bool(self.website_redirect.strip())

    @property
    def notice_file(self) -> Path:
        return self.gamedata_root / "Mir200" / "Notice" / "Notice.txt"

    def describe(self) -> str:
        return (
            f"port={self.port} db={self.db_file} tick={self.tick_ms}ms\n"
            f"  web       = {self.web_root}\n"
            f"  gamedata  = {self.gamedata_root}\n"
            f"  resources = {self.resources_root}\n"
            f"  website   = {self.website_root}\n"
            f"  site jump = {self.website_redirect or '(本地静态服务)'}"
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

    def as_float(prop: str, env_key: str | None, over_key: str | None, default: float) -> float:
        try:
            return float(val(prop, env_key, over_key, str(default)))
        except ValueError:
            return default

    def _maps(prop: str, default: str) -> tuple[str, ...]:
        raw = val(prop, None, None, default).strip()
        return tuple(m.strip() for m in raw.split(",") if m.strip())

    def _weekday_tuple(prop: str, default: str) -> tuple[int, ...]:
        out: list[int] = []
        for part in _maps(prop, default):
            try:
                day = int(part)
            except ValueError:
                continue
            if 1 <= day <= 7 and day not in out:
                out.append(day)
        return tuple(out) or (6, 7)

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
        website_redirect=val("website.redirect", _ENV_KEYS["website_redirect"],
                             "website_redirect", "https://list.notmmao.com/mir2-wiki/").strip(),
        gamedata_root=_resolve(root, val("gamedata.root", _ENV_KEYS["gamedata"], "gamedata", "gamedata")),
        world_width=as_int("world.width", None, None, 200),
        world_height=as_int("world.height", None, None, 200),
        player_spawn_x=as_int("world.playerSpawnX", None, None, 100),
        player_spawn_y=as_int("world.playerSpawnY", None, None, 100),
        backlog=as_int("server.backlog", None, None, 256),
        afk_min_level=as_int("afk.minLevel", None, None, 15),
        afk_excluded_maps=_maps("afk.excludedMaps", "0"),
        afk_per_map_limit=as_int("afk.perMapLimit", None, None, 2),
        afk_max_agents=as_int("afk.maxAgents", None, None, 10),
        afk_daily_minutes=as_int("afk.dailyMinutes", None, None, 360),
        afk_exp_rate=max(0.0, as_float("afk.expRate", None, None, 0.7)),
        afk_loot_sight_cut=min(1.0, max(0.0, as_float("afk.lootSightCut", None, None, 0.5))),
        afk_death_stop=as_int("afk.deathStop", None, None, 3),
        afk_claim_minutes=as_int("afk.claimMinutes", None, None, 240),
        afk_claim_per_day=as_int("afk.claimPerDay", None, None, 1),
        afk_pool_cap_minutes=as_int("afk.poolCapMinutes", None, None, 720),
        afk_newbie_minutes=as_int("afk.newbieMinutes", None, None, 240),
        afk_restart_bonus_minutes=as_int("afk.restartBonusMinutes", None, None, 60),
        afk_supply_enabled=max(0, as_int("afk.supplyEnabled", None, None, 1)),
        afk_supply_town_map=(val("afk.supplyTownMap", None, None, "0").strip() or "0"),
        afk_supply_max_fails=max(0, as_int("afk.supplyMaxFails", None, None, 5)),
        afk_gold_per_day=as_int("afk.goldPerDay", None, None, 5000),
        afk_supply_repair=max(0, as_int("afk.supplyRepair", None, None, 1)),
        afk_supply_unbind=max(0, as_int("afk.supplyUnbind", None, None, 1)),
        afk_log_enabled=max(0, as_int("afk.logEnabled", None, None, 1)),
        afk_log_dir=_resolve(root, val("afk.logDir", None, None, "data/afk-logs")),
        afk_log_sample_minutes=max(0, as_int("afk.logSampleMinutes", None, None, 1)),
        pk_decay_interval_ms=max(1, as_int("pk.decayIntervalSec", None, None, 15)) * 1000,
        autopilot_bag_full_stop=as_int("autopilot.bagFullStop", None, None, 0),
        weekend_enabled=max(0, as_int("weekend.enabled", None, None, 1)),
        weekend_exp_rate=max(0.0, as_float("weekend.expRate", None, None, 2.0)),
        weekend_drop_rate=max(0.0, as_float("weekend.dropRate", None, None, 2.0)),
        weekend_days=_weekday_tuple("weekend.days", "6,7"),
        weekend_start_hour=as_float("weekend.startHour", None, None, 0.0),
        weekend_end_hour=as_float("weekend.endHour", None, None, 24.0),
        weekend_force=max(0, as_int("weekend.force", None, None, 0)),
        holiday_enabled=max(0, as_int("holiday.enabled", None, None, 1)),
        holiday_exp_rate=max(0.0, as_float("holiday.expRate", None, None, 2.0)),
        holiday_force=max(0, as_int("holiday.force", None, None, 0)),
        holiday_dates=val("holiday.dates", None, None, "").strip(),
        elite_rate=max(0.0, as_float("elite.rate", None, None, 0.05)),
        elite_hp_mult=max(0.0, as_float("elite.hpMult", None, None, 2.0)),
        elite_dc_mult=max(0.0, as_float("elite.dcMult", None, None, 1.5)),
        elite_exp_mult=max(0.0, as_float("elite.expMult", None, None, 3.0)),
        elite_drop_mult=max(0.0, as_float("elite.dropMult", None, None, 2.0)),
    )
