# jootm2 默认运行时：纯标准库 Python 服务端（asyncio，无第三方依赖）
# Java/Netty 版仍保留在 Dockerfile.java，仅用于双栈协议回归对照。
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JOOTM2_HOST=0.0.0.0

WORKDIR /app

# 服务端代码 + 前端静态 + 配置 + 服务端游戏数据（CSV / Envir / .map）
COPY pyserver/ /app/pyserver/
COPY frontend/ /app/frontend/
COPY config/ /app/config/
COPY gamedata/ /app/gamedata/

# data/ 放 SQLite，resources/ 由卷挂载（客户端素材不随镜像分发）
RUN mkdir -p /app/data /app/resources

EXPOSE 8080

# 不写死 --port/--config：默认读 /app/config/server.properties，
# 也可用 JOOTM2_PORT / JOOTM2_DB / JOOTM2_GAMEDATA 等环境变量覆盖。
ENTRYPOINT ["python", "-m", "pyserver"]
