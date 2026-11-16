"""纯标准库 asyncio 网络层：单端口同时提供静态文件、REST 与 WebSocket。

逐项对齐 ``HttpServerHandler`` / ``WebSocketFrameHandler`` 的行为：
- 静态文件 + 路径穿越防护 + MIME + CORS(``*``)
- Range 请求返回 206 + ``Content-Range: bytes s-e/total`` + ``Accept-Ranges``，
  且用流式分块发送（WIL 图库 10MB+、20 并发不整包入内存）
- 缓存：HTML = ``no-cache``，代码/JSON 短缓存 + ETag，大资源 = ``immutable`` 一年；
  ``/site`` 数据库站另按文件类型分档（图片长缓存，见 ``website_cache_control``）；
  支持 ``If-None-Match`` / ``If-Modified-Since`` → 304、HEAD、gzip 预压缩缓存
- WebSocket：RFC6455 握手、文本帧/分片/ping-pong/close、客户端掩码校验，
  只走纯文本 JSON，与 Java 侧一致（BinaryFrame 不使用）
- 每个连接一个发送队列做背压，慢连接不会阻塞游戏 tick
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import os
import struct
import time
from dataclasses import dataclass, field
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .config import ServerConfig
from .service import GameService
from .world import encode

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME_BYTES = 1 << 20
MAX_BODY_BYTES = 1 << 20
CHUNK = 64 * 1024

# 半开连接探活：服务端主动发 ping，浏览器 WebSocket 原生自动回 pong（前端零改动）。
# 连续 WS_IDLE_TIMEOUT 秒收不到任何帧即视为掉线，走 on_disconnect 清场；
# 托管时客户端本来不发消息，靠 ping/pong 才不会被误踢。
WS_PING_INTERVAL = float(os.environ.get("JOOTM2_WS_PING_INTERVAL", "15"))
WS_IDLE_TIMEOUT = float(os.environ.get("JOOTM2_WS_IDLE_TIMEOUT", "45"))
WS_FRAME_TIMEOUT = float(os.environ.get("JOOTM2_WS_FRAME_TIMEOUT", "10"))

STATUS_TEXT = {
    200: "OK", 204: "No Content", 206: "Partial Content",
    301: "Moved Permanently", 304: "Not Modified", 400: "Bad Request", 404: "Not Found",
    416: "Range Not Satisfiable", 500: "Internal Server Error",
}

# 只对高重复度的文本/表格/地图做完整响应的 gzip；WIL/WIX/MP3 已压过的不碰。
# Range 分片用「应用层协商压缩」：前端带 X-Jootm2-Gzip 时服务端把该分片的
# 原始字节 gzip 后放在 body 里，Content-Range 仍描述解压后的真实文件偏移。
# 故意不使用 Content-Encoding（避免 206 + Content-Range 在代理上的语义歧义），
# 由前端 DecompressionStream 解压；旧客户端不带此头则原样返回，完全兼容。
GZIP_EXTS = (".html", ".mjs", ".js", ".css", ".json", ".txt", ".csv",
             ".svg", ".xml", ".map")
RANGE_GZIP_EXTS = (".wil", ".wix", ".map")
RANGE_GZIP_MAX = 2 << 20        # 单个分片最多压 2MB，避免大请求拖住事件循环
GZIP_MAX_BYTES = 8 << 20        # 超过 8MB 不压（CPU / 内存）
GZIP_CACHE_MAX = 64 << 20       # 进程内压缩结果上限 64MB
GZIP_LEVEL = 1                  # 低压缩级别：首次请求延迟优先，压缩率次优

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "*",
}


def ws_frame(payload: bytes, opcode: int = 1) -> bytes:
    """服务器发送帧（不掩码）。opcode: 1=text 8=close 9=ping 10=pong。"""
    n = len(payload)
    out = bytearray([0x80 | opcode])
    if n < 126:
        out.append(n)
    elif n < 65536:
        out.append(126)
        out += struct.pack(">H", n)
    else:
        out.append(127)
        out += struct.pack(">Q", n)
    return bytes(out) + payload


class WsSession:
    """``PlayerSession`` 的 asyncio 实现；service 只依赖 send_text/send_json/close。"""

    def __init__(self, writer: asyncio.StreamWriter):
        self.writer = writer
        self.account_id: int = -1
        self.player_name: str | None = None
        self.dropped_frames = 0
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=512)
        self._closed = False
        self._pump_task = asyncio.create_task(self._pump())

    def send_text(self, text: str) -> None:
        self._put(ws_frame(text.encode("utf-8")))

    def send_json(self, obj: Any) -> None:
        self.send_text(encode(obj))

    def _put(self, data: bytes | None) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            self.dropped_frames += 1

    async def _pump(self) -> None:
        while True:
            data = await self._queue.get()
            if data is None:
                return
            try:
                self.writer.write(data)
                await self.writer.drain()
            except (ConnectionError, OSError):
                return

    def close(self) -> None:
        """同步接口（service._logout 调用）：排队一个 close 帧并收尾。"""
        if self._closed:
            return
        self._closed = True
        self._put(ws_frame(b"", opcode=8))
        self._put(None)

    async def aclose(self) -> None:
        self._closed = True
        self._put(None)
        try:
            await asyncio.wait_for(self._pump_task, timeout=2.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._pump_task.cancel()
        try:
            self.writer.close()
        except OSError:
            pass


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    body: bytes
    keep_alive: bool = True


@dataclass(slots=True)
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    file_path: Path | None = None
    file_start: int = 0
    file_count: int = 0
    head_only: bool = False     # HEAD 请求 / 304：只发响应头

    async def send(self, writer: asyncio.StreamWriter, keep_alive: bool) -> None:
        head = [f"HTTP/1.1 {self.status} {STATUS_TEXT.get(self.status, '')}"]
        headers = dict(self.headers)
        headers.update(CORS_HEADERS)
        headers["Connection"] = "keep-alive" if keep_alive else "close"
        for k, v in headers.items():
            head.append(f"{k}: {v}")
        writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        if self.status not in (204, 304) and not self.head_only:
            if self.file_path is not None:
                try:
                    with self.file_path.open("rb") as f:
                        f.seek(self.file_start)
                        left = self.file_count
                        while left > 0:
                            chunk = f.read(min(CHUNK, left))
                            if not chunk:
                                break
                            writer.write(chunk)
                            await writer.drain()
                            left -= len(chunk)
                except OSError as e:
                    print(f"[http] stream failed {self.file_path}: {e}")
            elif self.body:
                writer.write(self.body)
        await writer.drain()


def content_type(name: str) -> str:
    n = name.lower()
    if n.endswith(".html"):
        return "text/html; charset=utf-8"
    if n.endswith(".css"):
        return "text/css; charset=utf-8"
    if n.endswith((".js", ".mjs")):
        return "application/javascript; charset=utf-8"
    if n.endswith(".json"):
        return "application/json; charset=utf-8"
    for ext, ct in ((".svg", "image/svg+xml"), (".png", "image/png"),
                    (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"),
                    (".gif", "image/gif"), (".ico", "image/x-icon"),
                    (".wav", "audio/wav"), (".mp3", "audio/mpeg"),
                    (".ogg", "audio/ogg")):
        if n.endswith(ext):
            return ct
    return "application/octet-stream"


def cache_control(name: str) -> str:
    """入口 HTML 必须保持新鲜；代码/索引短缓存 + ETag；素材资源长缓存一年。

    素材（WIL/WIX/MP3/MAP）不会改动，``immutable`` 让浏览器跳过重新校验。
    注意 206 Partial Content 在 Chromium 里不进 HTTP 缓存，光靠这个头不够，
    WIL 分片还要靠前端 IndexedDB 持久缓存（见 frontend/gfx/assetCache.mjs）。
    """
    n = name.lower()
    if n.endswith(".html"):
        return "no-cache"
    if n.endswith((".mjs", ".js", ".css", ".json")):
        return "public, max-age=300, must-revalidate"
    return "public, max-age=31536000, immutable"


# 数据库静态站（/site）的图片缓存时长。地图 PNG / 图标体积大、更新极少，
# 60 秒缓存会让每次开地图页都对所有图片发一遍条件请求（公网逐张一个 RTT）。
# 7 天是「有效期内零请求」与「重新生成后最多一周可见」的折中；不用 immutable
# 是因为站点重生成时文件名不变，immutable 会让浏览器一年内拿不到新图。
WEBSITE_STATIC_MAX_AGE = 7 * 24 * 3600
WEBSITE_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp")


def website_cache_control(name: str) -> str:
    """数据库静态站按文件类型分档：图片长缓存 + 可校验，页面/脚本保持新鲜。

    与 ``cache_control`` 的区别：素材目录里的 WIL 等从不改内容，可以 immutable
    一年；站点图片会随 wiki 重新生成而变化，所以只用 ``must-revalidate``，
    到期后靠 ETag/Last-Modified 走 304 校验，内容变了也能自动更新。
    """
    n = name.lower()
    if n.endswith(".html"):
        return "no-cache"
    if n.endswith(WEBSITE_IMAGE_EXTS):
        return f"public, max-age={WEBSITE_STATIC_MAX_AGE}, must-revalidate"
    # JS/CSS/JSON 及未知类型走短缓存，保证生成器改动后能尽快生效。
    return "public, max-age=300, must-revalidate"


class GzipCache:
    """按 (路径, size, mtime_ns) 缓存 gzip 结果，避免每次请求重复压缩。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, int, int], tuple[int, bytes]] = {}
        self._bytes = 0

    def get(self, path: Path, st: os.stat_result) -> bytes | None:
        if not path.name.lower().endswith(GZIP_EXTS) or st.st_size > GZIP_MAX_BYTES:
            return None
        key = (str(path), st.st_size, st.st_mtime_ns)
        hit = self._data.get(key)
        if hit is not None:
            return hit[1]
        try:
            raw = path.read_bytes()
            zipped = gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)
        except OSError:
            return None
        # 压缩后反而更大（已经压过的二进制）就别用
        if len(zipped) >= len(raw):
            return None
        self._data[key] = (len(zipped), zipped)
        self._bytes += len(zipped)
        if self._bytes > GZIP_CACHE_MAX:
            # 简单 LRU 近似：整体清掉一半最早插入的项
            for k in list(self._data)[: len(self._data) // 2]:
                self._bytes -= self._data.pop(k)[0]
        return zipped


def _etag_matches(header: str, etag: str) -> bool:
    h = header.strip()
    if not h:
        return False
    if h == "*":
        return True
    return etag in (part.strip().removeprefix("W/") for part in h.split(","))


def _not_modified_since(header: str, mtime: float) -> bool:
    if not header:
        return False
    try:
        return parsedate_to_datetime(header).timestamp() >= int(mtime)
    except (TypeError, ValueError, OverflowError):
        return False


def parse_range(header: str, file_size: int) -> tuple[int, int] | None:
    """与 Java ``parseRange`` 一致：``bytes=s-e`` / ``s-`` / ``-suffix``。"""
    if not header.startswith("bytes=") or file_size <= 0:
        return None
    spec = header[len("bytes="):].strip()
    dash = spec.find("-")
    if dash < 0:
        return None
    a, b = spec[:dash].strip(), spec[dash + 1:].strip()
    try:
        if not a:
            suffix = int(b)
            if suffix <= 0:
                return None
            start, end = max(0, file_size - suffix), file_size - 1
        else:
            start = int(a)
            end = file_size - 1 if not b else int(b)
    except ValueError:
        return None
    if start < 0 or start >= file_size or end < start:
        return None
    return start, min(end, file_size - 1)


def _parse_request(head: bytes, body: bytes) -> Request | None:
    try:
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split()
        if len(parts) < 2:
            return None
        method, target = parts[0].upper(), parts[1]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        split = urlsplit(target)
        path = unquote(split.path) or "/"
        query = {k: v[0] for k, v in parse_qs(split.query, keep_blank_values=True).items() if v}
        keep_alive = headers.get("connection", "").lower() != "close" and parts[-1] == "HTTP/1.1"
        return Request(method, path, query, headers, body, keep_alive)
    except (UnicodeDecodeError, ValueError):
        return None


def _is_under(path: str, prefix: str) -> bool:
    return bool(prefix) and (path == prefix or path.startswith(prefix + "/"))


class Server:
    def __init__(self, config: ServerConfig, service: GameService):
        self.config = config
        self.service = service
        self._server: asyncio.AbstractServer | None = None
        self._tasks: list[asyncio.Task] = []
        self._gzip = GzipCache()
        self._asset_ver: tuple[str, float] = ("", 0.0)

    # ---- HTTP 路由 ------------------------------------------------------

    def _error(self, status: int) -> Response:
        text = STATUS_TEXT.get(status, "Error").encode("utf-8")
        return Response(status, {"Content-Type": "text/plain; charset=utf-8",
                                 "Content-Length": str(len(text))}, body=text)

    def asset_version(self) -> str:
        """客户端素材指纹：前端 IndexedDB 缓存用它做版本命名空间。

        只覆盖真正会被持久缓存的 WIL/WIX/MAP（排除每次启动重写的
        resources.json / audio_map.json，否则一重启就全量失效）。
        结果缓存 60 秒，避免每次请求都 stat 两千多个文件。
        """
        ver, at = self._asset_ver
        if ver and time.monotonic() - at < 60:
            return ver
        h = hashlib.sha1()
        root = self.config.resources_root
        try:
            paths = sorted(p for p in root.rglob("*")
                           if p.is_file() and p.suffix.lower() in
                           (".wil", ".wix", ".map"))
        except OSError:
            paths = []
        for p in paths:
            try:
                st = p.stat()
                h.update(str(p.relative_to(root)).encode("utf-8", "replace"))
                h.update(f":{st.st_size}:{st.st_mtime_ns};".encode("ascii"))
            except OSError:
                continue
        ver = h.hexdigest()[:16]
        self._asset_ver = (ver, time.monotonic())
        return ver

    def _serve_file(self, req: Request, path: str, root: Path,
                    prefix: str, default_name: str,
                    allow_gzip: bool = True,
                    cache_override: str | None = None,
                    cache_fn: Callable[[str], str] | None = None) -> Response:
        """静态文件响应。``allow_gzip=False`` 用于静态站点：zlib 压缩在事件
        循环里同步执行，单次冷压最慢 13.63ms（drops.html），并发首访会在同一个
        loop iteration 里串行叠加；关掉后单次同步阻塞上限 4.21ms（实测见
        HANDOFF-PYSERVER §60），不值得为省流量挤占游戏 tick。"""
        if not prefix:
            rel = default_name if path in ("/", "") else path
        else:
            rel = default_name if path == prefix else path[len(prefix):]
        rel = unquote(rel).lstrip("/")
        if not rel:                      # "/site/" 这类目录路径 → 目录下的 index
            rel = default_name
        try:
            root_abs = root.resolve()
            file = (root_abs / rel).resolve()
            if not file.is_relative_to(root_abs) or not file.is_file():
                return self._error(404)
        except (OSError, ValueError):
            return self._error(404)

        st = file.stat()
        size = st.st_size
        etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
        ct = content_type(file.name)
        cache = (cache_fn(file.name) if cache_fn is not None
                 else cache_override or cache_control(file.name))
        common = {
            "Content-Type": ct,
            "Accept-Ranges": "bytes",
            "Cache-Control": cache,
            "ETag": etag,
            "Last-Modified": formatdate(st.st_mtime, usegmt=True),
            "Vary": "Accept-Encoding",
        }
        head_only = req.method == "HEAD"

        # 条件请求 → 304（响应体为空；公网只花一个 RTT，不再重复传文件内容）
        inm = req.headers.get("if-none-match", "")
        ims = req.headers.get("if-modified-since", "")
        if _etag_matches(inm, etag) or (not inm and _not_modified_since(ims, st.st_mtime)):
            return Response(304, common)

        rng = parse_range(req.headers.get("range", ""), size)
        if rng is not None:
            start, end = rng
            count = end - start + 1
            headers = dict(common)
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            zipped = None
            if (allow_gzip
                    and req.headers.get("x-jootm2-gzip") == "1"
                    and file.suffix.lower() in RANGE_GZIP_EXTS
                    and count <= RANGE_GZIP_MAX):
                try:
                    with file.open("rb") as f:
                        f.seek(start)
                        raw = f.read(count)
                    if len(raw) == count:
                        z = gzip.compress(raw, compresslevel=GZIP_LEVEL, mtime=0)
                        if len(z) < len(raw) * 0.9:   # 压不动就别压
                            zipped = z
                except OSError:
                    zipped = None
            if zipped is not None:
                headers["X-Jootm2-Gzip"] = "1"
                headers["Vary"] = "X-Jootm2-Gzip, Accept-Encoding"
                headers["Content-Length"] = str(len(zipped))
                return Response(206, headers, body=zipped, head_only=head_only)
            headers["Content-Length"] = str(count)
            return Response(206, headers, file_path=file, file_start=start,
                            file_count=count, head_only=head_only)

        # 完整响应：可压缩类型走进程内 gzip 缓存
        zipped = None
        if allow_gzip and "gzip" in req.headers.get("accept-encoding", "").lower():
            zipped = self._gzip.get(file, st)   # HEAD 也要算出同样的 Content-Length
        headers = dict(common)
        if zipped is not None:
            headers["Content-Encoding"] = "gzip"
            headers["Content-Length"] = str(len(zipped))
            return Response(200, headers, body=zipped, head_only=head_only)
        headers["Content-Length"] = str(size)
        return Response(200, headers, file_path=file, file_start=0,
                        file_count=size, head_only=head_only)

    def handle_http(self, req: Request) -> Response:
        if req.method == "OPTIONS":
            return Response(204, {"Content-Length": "0"})
        if req.path == "/api/asset-version":
            # 附带默认出生点：客户端在 Loading 阶段就能按 (map,x,y) 精确预取首屏
            # 地图纹理，不必等进游戏快照到达才现下 7~8MB 的 WIL 分片。
            start = {}
            try:
                w = self.service.world
                name = w.default_map.name
                start = {"map": name, "x": w.start_x, "y": w.start_y,
                         "miniMapIndex": w.map_mini_index.get(name, -1)}
            except (AttributeError, TypeError):
                pass
            body = encode({"version": self.asset_version(), "start": start}).encode("utf-8")
            return Response(200, {
                "Content-Type": "application/json; charset=utf-8",
                "Content-Length": str(len(body)),
                "Cache-Control": "no-store",
            }, body=body, head_only=req.method == "HEAD")
        if req.path.startswith("/api/"):
            text = self.service.handle_admin(req.method, req.path, req.query, req.body)
            if text is None:
                return self._error(404)
            body = text.encode("utf-8")
            return Response(200, {"Content-Type": "application/json; charset=utf-8",
                                  "Content-Length": str(len(body))}, body=body,
                            head_only=req.method == "HEAD")
        if _is_under(req.path, self.config.resources_prefix):
            return self._serve_file(req, req.path, self.config.resources_root,
                                    self.config.resources_prefix, "resources.json")
        if _is_under(req.path, self.config.website_prefix):
            # 数据库静态站点（scripts/gen_website.py 生成到 data/website）
            if req.path == self.config.website_prefix:
                # 页面里的链接都是相对路径；补斜杠，否则 base 变成站点根
                return Response(301, {"Location": self.config.website_prefix + "/",
                                      "Content-Length": "0"})
            return self._serve_file(
                req, req.path, self.config.website_root, self.config.website_prefix,
                "index.html",
                allow_gzip=False,
                # 站点重新生成时文件名不变，不能用 immutable；按类型分档，
                # 图片长缓存避免每次开地图页都逐张发条件请求（见 website_cache_control）
                cache_fn=website_cache_control)
        return self._serve_file(req, req.path, self.config.web_root, "", "index.html")

    # ---- WebSocket ------------------------------------------------------

    async def _ws_loop(self, reader: asyncio.StreamReader,
                       writer: asyncio.StreamWriter, session: WsSession) -> None:
        fragments = bytearray()
        idle = 0.0

        async def read(n: int) -> bytes:
            # 帧体读到一半卡住时放弃整个连接：wait_for 取消 readexactly 后，
            # 已消费的字节不会保留，继续解析只会错位。
            return await asyncio.wait_for(reader.readexactly(n), WS_FRAME_TIMEOUT)

        try:
            while True:
                try:
                    head = await asyncio.wait_for(
                        reader.readexactly(2), WS_PING_INTERVAL)
                except asyncio.TimeoutError:
                    idle += WS_PING_INTERVAL
                    if idle >= WS_IDLE_TIMEOUT:
                        return          # 半开连接当作掉线，交给 finally 清场
                    session._put(ws_frame(b"", opcode=9))
                    continue
                idle = 0.0            # pong / 任意客户端帧都算活着
                fin, opcode = bool(head[0] & 0x80), head[0] & 0x0F
                masked, n = bool(head[1] & 0x80), head[1] & 0x7F
                if n == 126:
                    n = struct.unpack(">H", await read(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", await read(8))[0]
                if n > MAX_FRAME_BYTES:
                    raise ValueError("frame too large")
                mask = await read(4) if masked else b""
                payload = bytearray(await read(n)) if n else bytearray()
                if mask:
                    for i in range(len(payload)):
                        payload[i] ^= mask[i & 3]
                if opcode == 8:
                    session._put(ws_frame(b"", opcode=8))
                    return
                if opcode == 9:
                    session._put(ws_frame(bytes(payload), opcode=10))
                    continue
                if opcode == 10:
                    continue
                if opcode not in (0, 1):
                    continue       # BinaryWebSocketFrame 与本协议无关
                if opcode == 1:
                    fragments = payload
                else:
                    fragments += payload
                if not fin:
                    continue
                text = fragments.decode("utf-8", errors="replace")
                fragments = bytearray()
                self.service.on_message(session, text)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError,
                ConnectionError, OSError, ValueError):
            pass
        finally:
            self.service.on_disconnect(session)
            await session.aclose()

    async def _handle_connection(self, reader: asyncio.StreamReader,
                                 writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                try:
                    head = await reader.readuntil(b"\r\n\r\n")
                except (asyncio.IncompleteReadError, asyncio.LimitOverrunError,
                        ConnectionError, OSError):
                    break
                try:
                    length = 0
                    for line in head.decode("latin-1").split("\r\n")[1:]:
                        if line.lower().startswith("content-length:"):
                            length = int(line.split(":", 1)[1].strip())
                            break
                except (ValueError, UnicodeDecodeError):
                    break
                if length < 0 or length > MAX_BODY_BYTES:
                    break
                body = await reader.readexactly(length) if length else b""
                req = _parse_request(head, body)
                if req is None:
                    break

                upgrade = req.headers.get("upgrade", "").lower()
                key = req.headers.get("sec-websocket-key", "")
                if req.path == "/ws" and upgrade == "websocket" and key:
                    accept = base64.b64encode(
                        hashlib.sha1((key + WS_GUID).encode("ascii")).digest()
                    ).decode("ascii")
                    writer.write((
                        "HTTP/1.1 101 Switching Protocols\r\n"
                        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
                    ).encode("latin-1"))
                    await writer.drain()
                    session = WsSession(writer)
                    await self._ws_loop(reader, writer, session)
                    return

                resp = self.handle_http(req)
                await resp.send(writer, req.keep_alive)
                if not req.keep_alive:
                    break
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except OSError:
                pass

    # ---- 生命周期 -------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, self.config.host, self.config.port,
            backlog=self.config.backlog,
        )
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="game-tick"),
            asyncio.create_task(self._save_loop(), name="save-players"),
        ]

    async def _tick_loop(self) -> None:
        while True:
            try:
                self.service.tick()
            except Exception as e:
                print(f"[tick] failed: {type(e).__name__}: {e}")
            await asyncio.sleep(self.config.tick_ms / 1000.0)

    async def _save_loop(self) -> None:
        while True:
            await asyncio.sleep(max(1, self.config.save_period_sec))
            try:
                self.service.save_all_players()
            except Exception as e:
                print(f"[save] failed: {type(e).__name__}: {e}")

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        try:
            self.service.save_all_players()
        except Exception as e:
            print(f"[shutdown] save failed: {e}")
