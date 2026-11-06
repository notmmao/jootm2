"""游戏数据解析：GBK 文本配置、老式 ``.map`` 栅格、``resources.json`` 索引。

与 Java 侧 ``WorldData`` / ``MapFileReader`` / ``ResourcesIndexGenerator`` 对齐；
区别只在两处有意为之：
1. ``.map`` 的 tile 步进按文件长度自适应 12/14 字节（修掉 Java 侧写死 12 的错格隐患）；
2. 索引只收 WIL/WIX，遇到 WZL/WZX 明确报错（修掉「索引生成成功但画面全空」的假兼容）。
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

GBK = "gbk"

# Java 用 ``getShort() & 0xFFFF``，三个图层号都是无符号 16 位。
_TILE_12 = struct.Struct("<HHHB5x")   # 6 + 1 + 5 = 12 字节
_TILE_14 = struct.Struct("<HHHB7x")   # 14 字节变体（多 bngFileIdx/midFileIdx）


def read_lines(path: Path, encoding: str = GBK) -> list[str]:
    if not path.is_file():
        return []
    try:
        return path.read_bytes().decode(encoding, errors="ignore").splitlines()
    except OSError as e:
        print(f"[data] cannot read {path}: {e}")
        return []


def _int(v: str) -> int:
    try:
        return int(v.strip())
    except (ValueError, AttributeError):
        return 0


def _rows(path: Path) -> list[str]:
    return [t for line in read_lines(path) if (t := line.strip()) and not t.startswith(";")]


@dataclass(frozen=True, slots=True)
class StartPoint:
    map_id: str
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class Gate:
    src_map: str
    src_x: int
    src_y: int
    dst_map: str
    dst_x: int
    dst_y: int


@dataclass(frozen=True, slots=True)
class MonGen:
    map_id: str
    x: int
    y: int
    monster_name: str
    range_: int
    count: int
    interval_min: int = 0


@dataclass(frozen=True, slots=True)
class MapInfo:
    titles: dict[str, str] = field(default_factory=dict)
    gates: list[Gate] = field(default_factory=list)


def parse_start_point(path: Path) -> StartPoint:
    for line in _rows(path):
        parts = line.split()
        if len(parts) >= 3:
            return StartPoint(parts[0], _int(parts[1]), _int(parts[2]))
    return StartPoint("0", 300, 300)


def _xy(text: str) -> tuple[int, int]:
    parts = text.split(",", 1)
    return _int(parts[0]), (_int(parts[1]) if len(parts) > 1 else 0)


def parse_map_info(path: Path) -> MapInfo:
    info = MapInfo()
    for line in _rows(path):
        if line.startswith("["):
            close = line.find("]")
            if close < 0:
                continue
            kv = line[1:close].strip().split(None, 1)
            info.titles[kv[0].strip()] = kv[1].strip() if len(kv) > 1 else kv[0].strip()
        elif "->" in line:
            left, right = line.split("->", 1)
            src, dst = left.strip().split(), right.strip().split()
            if len(src) < 2 or len(dst) < 2:
                continue
            sx, sy = _xy(src[1])
            dx, dy = _xy(dst[1])
            info.gates.append(Gate(src[0].strip(), sx, sy, dst[0].strip(), dx, dy))
    return info


def parse_mon_gen(path: Path) -> list[MonGen]:
    out: list[MonGen] = []
    for line in _rows(path):
        p = line.split()
        if len(p) >= 6:
            out.append(MonGen(p[0], _int(p[1]), _int(p[2]), p[3],
                              _int(p[4]), _int(p[5]), _int(p[6]) if len(p) >= 7 else 0))
    return out


def parse_mini_map(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in _rows(path):
        p = line.split()
        if len(p) >= 2:
            out[p[0]] = _int(p[1])
    return out


def parse_notice(path: Path) -> list[str]:
    return [line.strip() for line in read_lines(path) if line.strip()]


# ---------------------------------------------------------------- .map

@dataclass(slots=True)
class MapGrid:
    name: str
    width: int
    height: int
    flags: bytearray            # 0=可走 1=普通阻挡 2=高墙（挡飞行/视线）
    doors: dict[tuple[int, int], int] = field(default_factory=dict)
    from_file: bool = False

    def inside(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def flag(self, x: int, y: int) -> int:
        return self.flags[x * self.height + y] if self.inside(x, y) else 1

    def can_walk(self, x: int, y: int) -> bool:
        return self.flag(x, y) == 0

    def is_high_wall(self, x: int, y: int) -> bool:
        return self.flag(x, y) == 2


def read_map(map_id: str, map_dir: Path) -> MapGrid:
    """读 52 字节头 + tile 栅格；tile 按「列优先」(x 外层、y 内层) 存储。

    前端按 ``(文件长-52)/w/h`` 自适应 12/14 字节，这里同样自适应，
    避免 14 字节地图包在服务端被错格解析（Java 侧写死 12）。
    """
    path = next((p for p in (map_dir / f"{map_id}.map", map_dir / f"{map_id}.MAP")
                 if p.is_file()), None)
    if path is None:
        print(f"[map] not found: {map_id}")
        return MapGrid(map_id, 256, 256, bytearray(256 * 256))
    try:
        data = path.read_bytes()
        if len(data) < 52:
            raise ValueError("map header too short")
        width = struct.unpack_from("<H", data, 0)[0]
        height = struct.unpack_from("<H", data, 2)[0]
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid map size {width}x{height}")
        body = memoryview(data)[52:]
        tile_size = 14 if len(body) >= width * height * 14 else 12
        tile = _TILE_14 if tile_size == 14 else _TILE_12
        usable = (len(body) // tile.size) * tile.size
        grid = MapGrid(map_id, width, height, bytearray(width * height), from_file=True)
        flags = grid.flags
        # iter_unpack 一次 C 层解包全部 tile，避免 49 万次 Python 循环。
        for i, (bng, mid, obj, door_index) in enumerate(tile.iter_unpack(body[:usable])):
            if i >= width * height:
                break
            if obj & 0x8000:
                flags[i] = 2
            elif (bng & 0x8000) or (mid & 0x8000):
                flags[i] = 1
            if (door_index & 0x80) and (door_index & 0x7F) > 0:
                x, y = divmod(i, height)   # 列优先：i = x*height + y
                grid.doors[(x, y)] = door_index & 0x7F
        return grid
    except (OSError, ValueError, struct.error) as e:
        print(f"[map] failed to read {path}: {e}")
        return MapGrid(map_id, 256, 256, bytearray(256 * 256))


def scan_available_maps(map_dir: Path) -> set[str]:
    if not map_dir.is_dir():
        return set()
    return {p.name[:-4] for p in map_dir.iterdir() if p.name.lower().endswith(".map")}


# ---------------------------------------------------------------- 资源索引

def _find_dir(root: Path, name: str) -> Path | None:
    if not root.is_dir():
        return None
    return next((p for p in root.iterdir() if p.is_dir() and p.name.lower() == name.lower()), None)


def _base(name: str) -> str:
    return name.split(".", 1)[0].lower()


def generate_resources_index(root: Path) -> dict | None:
    """扫 Data/Map/Wav 生成 resources.json；只索引 WIL/WIX，明确拒绝 WZL/WZX。"""
    if not root.is_dir():
        print(f"[resources] resource root not found: {root}")
        return None
    data_dir, map_dir, wav_dir = (_find_dir(root, n) for n in ("data", "map", "wav"))
    if data_dir is None or map_dir is None or wav_dir is None:
        print(f"[resources] Data/Map/Wav not found under {root}, skip index generation")
        return None

    files = sorted(p.name for p in data_dir.iterdir() if p.is_file())
    lower = {f.lower(): f for f in files}
    images: dict[str, dict] = {}
    for f in files:
        if not f.lower().endswith(".wix"):
            continue
        base = _base(f)
        lib = lower.get(base + ".wil")
        if lib:
            images[base] = {"x": f"{data_dir.name}/{f}", "l": f"{data_dir.name}/{lib}"}
    modern = [f for f in files if f.lower().endswith((".wzl", ".wzx"))]
    if modern:
        print(f"[resources] WARNING: {len(modern)} WZL/WZX 文件无法被前端 wil.mjs 解码，"
              f"已排除（例：{modern[0]}）；请换用 WIL/WIX 老客户端")

    maps = {_base(p.name): f"{map_dir.name}/{p.name}"
            for p in sorted(map_dir.iterdir()) if p.name.lower().endswith(".map")}
    wavs = {_base(p.name): f"{wav_dir.name}/{p.name}"
            for p in sorted(wav_dir.iterdir()) if p.name.lower().endswith(".wav")}

    index = {"images": images, "maps": maps, "wavs": wavs}
    out = root / "resources.json"
    out.write_text(json.dumps(index, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[resources] generated {out} (images={len(images)}, maps={len(maps)}, wavs={len(wavs)})")
    return index
