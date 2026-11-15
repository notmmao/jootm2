"""入口：``python -m pyserver [--port 8081]``。

默认读取与 Java 服务端相同的 ``config/server.properties``，因此两个服务端
会抢同一个 8080 端口；双栈并行时给 Python 侧显式指定 ``--port`` 与 ``--db``：

    python -m pyserver --port 8081 --db data/jootm2-py.db
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import replace

from . import __version__
from .config import load
from .db import Database
from .gamedata import generate_resources_index
from .net import Server
from .service import ALL_TYPES, MIGRATED_TYPES, GameService


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m pyserver",
        description="jootm2 Python 服务端（纯标准库，协议兼容 Java/Netty 版）",
    )
    ap.add_argument("--config", help="server.properties 路径")
    ap.add_argument("--host", help="监听地址（默认 0.0.0.0，可用环境变量 JOOTM2_HOST）")
    ap.add_argument("--port", type=int, help="监听端口（默认读取 properties 的 8080）")
    ap.add_argument("--db", help="SQLite 文件（默认 data/jootm2.db，与 Java 共用）")
    ap.add_argument("--gamedata", help="gamedata 根目录")
    ap.add_argument("--web", dest="web", help="frontend 根目录")
    ap.add_argument("--resources", help="客户端资源根目录（Data/Map/Wav）")
    ap.add_argument("--website", help="数据库静态站点根目录（默认 data/website，挂载在 /site）")
    ap.add_argument("--tick-ms", dest="tick_ms", type=int, help="游戏 tick 间隔（毫秒）")
    ap.add_argument("--no-resource-index", action="store_true",
                    help="启动时不重新生成 resources.json")
    ap.add_argument("--version", action="version", version=f"pyserver {__version__}")
    return ap


async def amain(args: argparse.Namespace) -> int:
    overrides = {k: v for k, v in vars(args).items()
                 if k not in ("no_resource_index", "tick_ms") and v is not None}
    cfg = load(**overrides)
    if args.tick_ms is not None:
        cfg = replace(cfg, tick_ms=max(10, args.tick_ms))

    db = Database(cfg.db_file)
    db.seed_game_data(cfg.mud2_dir)
    if not args.no_resource_index:
        generate_resources_index(cfg.resources_root)
    service = GameService(cfg, db)
    server = Server(cfg, service)

    print(f"[main] jootm2 (python/{__version__}) starting...\n{cfg.describe()}")
    await server.start()
    print(f"[main] jootm2 (python) started on port {cfg.port}")
    print(f"[main]   client   : http://localhost:{cfg.port}/")
    site_hint = "" if cfg.website_root.is_dir() else "  (未生成：先跑 python scripts/gen_website.py)"
    print(f"[main]   website  : http://localhost:{cfg.port}{cfg.website_prefix}/{site_hint}")
    print(f"[main]   ws       : ws://localhost:{cfg.port}/ws")
    print(f"[main]   migration: {len(MIGRATED_TYPES)}/{len(ALL_TYPES)} message types ported")
    print("[main]   press Ctrl+C to stop", flush=True)

    stop = asyncio.Event()
    try:
        await stop.wait()
    except KeyboardInterrupt:
        print("\n[main] shutting down, saving players...")
    finally:
        await server.close()
        db.close()
    return 0


def main() -> int:
    return asyncio.run(amain(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
