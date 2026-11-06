"""jootm2 Python 服务端（协议兼容实现）。

设计目标：与 Java/Netty 服务端共用同一份 ``config/server.properties``、
``gamedata/`` 与 SQLite 数据库，前端一行不改即可切换到本服务端。

仅使用 Python 标准库（asyncio + sqlite3 + struct），零第三方依赖。
所有游戏状态只在单个 asyncio 事件循环里变更，等价于 Java 侧
``synchronized (world.lock)`` 的串行语义，因此本实现不需要锁。

迁移进度见 :mod:`pyserver.service` 的 ``MIGRATED_TYPES`` / ``PENDING_TYPES``。
"""

__version__ = "0.1.0"
