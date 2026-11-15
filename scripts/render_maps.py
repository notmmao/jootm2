# -*- coding: utf-8 -*-
""".map → PNG 渲染图（供静态站点用）：地形色块 + 怪物刷新点标记。

渲染口径（对齐 Delphi 客户端）：
- 背景 tile：``wBkImg & 0x7FFF`` - 1 → ``Tiles.wil`` 帧（客户端只在偶数坐标
  绘制、每帧覆盖 2×2 格 → 每 2×2 格采样一次）。
- 中层：``wMidImg`` - 1 → ``SmTiles.wil``（每格绘制，小物件：草、碎石）。
- 前景：``wFrImg & 0x7FFF`` - 1 → ``GetObjs(btArea, idx)``（每格绘制，室内图的
  地板/墙体也在这里）：``Objects.wil``（area=0）/ ``Objects{area+1}.wil``
  （MShare.pas:783-800）。彩块近似：整帧平均色按 α 加权。
- 输出 1 像素/格；边长 ≤320 格的小图按整数倍放大（最多 4×）。
- 刷新点：怪物图标 = ``getMonImgLib(appr)`` 选 MonN.wil + ``getMonOffset(appr)``
  偏移 + stand 段 ``dir*(frame+skip)``（规则照抄 frontend/actor/actorData.mjs，
  动作表与 raceImg 映射直接从该文件解析，避免手抄漂移），带 α 合成。

用法：
  python scripts/render_maps.py --out data/website/assets/maps          # 全部
  python scripts/render_maps.py --only 0 --out /tmp/maps                # 单张调试
  python scripts/render_maps.py --skip-if-exists                       # 增量
"""
from __future__ import annotations

import argparse
import array
import collections
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from wil_extract import WIL, _png                    # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'resources' / 'Data'
MAPDIR = ROOT / 'resources' / 'Map'


# ---------------------------------------------------------------- 动作表（解析 actorData.mjs）

def load_actor_tables() -> tuple[dict[int, str], dict[str, tuple[int, int, int]]]:
    text = (ROOT / 'frontend' / 'actor' / 'actorData.mjs').read_text(encoding='utf-8')
    stands: dict[str, tuple[int, int, int]] = {}
    for name, st, fr, sk in re.findall(
            r'(MA\d+):\s*\{\s*stand:\s*\{\s*start:\s*(\d+),\s*frame:\s*(\d+),\s*skip:\s*(\d+)',
            text):
        stands[name] = (int(st), int(fr), int(sk))
    ra = re.search(r'const RACE_ACTION = \{(.*?)\};', text, re.S)
    race = {int(k): v for k, v in re.findall(r'(\d+):\s*\'(MA\d+)\'', ra.group(1))}
    return race, stands


RACE_ACTION, STANDS = load_actor_tables()


def mon_lib(appr: int) -> str:
    n = appr // 10
    if n == 0:
        return 'Mon1'
    if 1 <= n <= 20:
        return f'Mon{n + 1}'
    if n == 90:
        return 'Effect'
    return 'Mon1'


def mon_offset(appr: int) -> int:
    nr, npos = appr // 10, appr % 10
    if nr == 0:
        return npos * 280
    if nr == 1:
        return npos * 230
    if nr in (2, 3, 7, 8, 9, 10, 11, 12, 14, 15, 16):
        return npos * 360
    if nr == 13:
        return {1: 360, 2: 440, 3: 550}.get(npos, npos * 360)
    if nr == 4:
        return 600 if npos == 1 else npos * 360
    if nr == 5:
        return npos * 430
    if nr == 6:
        return npos * 440
    if nr == 17:
        return npos * 350
    if nr == 18:
        return {0: 0, 1: 520, 2: 950}.get(npos, 0)
    if nr == 19:
        t = [0, 370, 810, 1250, 1630, 2010, 2390]
        return t[npos] if npos < len(t) else 0
    if nr == 20:
        t = [0, 360, 720, 1080, 1440, 1800, 2350, 3060]
        return t[npos] if npos < len(t) else 0
    if nr == 90:
        t = [80, 168, 184, 200]
        return t[npos] if npos < len(t) else 80
    return 0


# ---------------------------------------------------------------- WIL 缓存与平均色

class Libs:
    def __init__(self):
        self.wils: dict[str, WIL] = {}
        self.avg: dict[tuple[str, int], tuple | None] = {}

    def wil(self, name: str) -> WIL | None:
        if name not in self.wils:
            try:
                self.wils[name] = WIL(DATA, name)
            except FileNotFoundError:
                self.wils[name] = None            # type: ignore[assignment]
        return self.wils[name]

    def frame_avg(self, name: str, idx: int):
        key = (name, idx)
        if key not in self.avg:
            w = self.wil(name)
            self.avg[key] = w.frame_avg(idx) if w else None
        return self.avg[key]

    def frame_rgba(self, name: str, idx: int):
        w = self.wil(name)
        return w.frame_rgba(idx) if w else None


LIBS = Libs()


def blend(a, b, k: float):
    return (int(a[0] * (1 - k) + b[0] * k), int(a[1] * (1 - k) + b[1] * k),
            int(a[2] * (1 - k) + b[2] * k), 255)


# ---------------------------------------------------------------- 地图解析

def read_tiles(path: Path):
    data = path.read_bytes()
    if len(data) < 52:
        return None
    w, h = struct.unpack_from('<HH', data, 0)
    if not (w and h):
        return None
    body = memoryview(data)[52:]
    cells = w * h
    if len(body) == cells * 12:
        stride = 12
    elif len(body) == cells * 14:
        stride = 14
    elif len(body) >= cells * 12 and len(body) % cells == 0 and len(body) // cells >= 12:
        stride = len(body) // cells
    else:
        stride = 12
    usable = (len(body) // stride) * stride
    arr = array.array('H')
    arr.frombytes(body[:usable])
    k = stride // 2                       # 每个 tile 的 u16 数
    if stride < 12 or k > len(arr):
        return None
    return w, h, k, arr


def sample_even(arr, k, w: int, h: int, i16: int):
    """取偶数 x 行的偶数 y（客户端 bg 只在偶数坐标绘制）。返回 array('H')。"""
    out = array.array('H')
    for x in range(0, w, 2):
        base = x * h * k + i16
        out.extend(arr[base:base + h * k:k][::2])
    return out


SMALL_TILES = 4000          # w*h ≤ 此值的图走真实图层合成


def render_map_small(w: int, h: int, k: int, arr, spawns) -> bytes:
    """小图真实合成：按客户端 DrawTileMap/对象层口径缩放绘制 WIL 帧。

    - 背景 Tiles.wil（96×64，偶数格，左上角 = (x*48, y*32)）
    - 中层 SmTiles.wil（48×32，每格，(x*48, y*32)，α 混合）
    - 对象 GetObjs（底边对齐格底部：(x*48, (y+1)*32 - 帧高)），按 y 顺序绘制
    输出长边约 1400px（大图缩比例）。
    """
    bg = arr[0::k][:w * h]
    mid = arr[1::k][:w * h]
    fr = arr[2::k][:w * h]
    area = arr[5::k][:w * h]
    W0, H0 = w * 48, h * 32
    S = min(1.0, 1400.0 / max(W0, H0))
    W, H = max(1, round(W0 * S)), max(1, round(H0 * S))
    canvas = bytearray(bytes((40, 40, 40, 255)) * (W * H))
    spr_cache: dict[tuple[str, int], tuple | None] = {}

    def sprite(lib: str, idx: int):
        """缩放后的帧：逐行拆成「不透明连续段」+「半透明散点」，加速拷贝。"""
        key = (lib, idx)
        if key in spr_cache:
            return spr_cache[key]
        r = LIBS.frame_rgba(lib, idx)
        if r is None:
            spr_cache[key] = None
            return None
        sw, sh, src = r
        fw = max(1, round(sw * S))
        fh = max(1, round(sh * S))
        xs = [min(sw - 1, int(px / S)) for px in range(fw)]
        ys = [min(sh - 1, int(py / S)) for py in range(fh)]
        rows = []
        for sy in ys:
            prow = src[sy * sw * 4:(sy + 1) * sw * 4]
            row = bytearray()
            for x in xs:
                row += prow[x * 4:x * 4 + 4]
            data = bytes(row)
            runs, soft, start = [], [], None
            for dx in range(fw):
                a = data[dx * 4 + 3]
                if a >= 250:
                    if start is None:
                        start = dx
                else:
                    if start is not None:
                        runs.append((start, dx))
                        start = None
                    if a >= 16:
                        soft.append((dx, data[dx * 4:dx * 4 + 4]))
            if start is not None:
                runs.append((start, fw))
            rows.append((data, runs, soft))
        res = (sh, fw, fh, rows)
        spr_cache[key] = res
        return res

    def blit(lib: str, idx: int, x0: int, y0: int, bottom_anchor: bool = False) -> None:
        spr = sprite(lib, idx)
        if spr is None:
            return
        sh, fw, fh, rows = spr
        x0 = round(x0 * S)
        y0 = round((y0 + 32 - sh) * S) if bottom_anchor else round(y0 * S)
        dy0 = max(0, -y0)
        dy1 = min(fh, H - y0)
        for dy in range(dy0, dy1):
            data, runs, soft = rows[dy]
            dbase = (y0 + dy) * W * 4
            for s, e in runs:
                xa = x0 + s
                xb = x0 + e
                if xa < 0:
                    s -= xa
                    xa = 0
                if xb > W:
                    e -= xb - W
                    xb = W
                if xa < xb:
                    d = dbase + xa * 4
                    canvas[d:d + (xb - xa) * 4] = data[s * 4:e * 4]
            for dx, pix in soft:
                px = x0 + dx
                if not (0 <= px < W):
                    continue
                a = pix[3]
                if a < 16:
                    continue
                d = dbase + px * 4
                inv = 255 - a
                canvas[d] = (pix[0] * a + canvas[d] * inv) // 255
                canvas[d + 1] = (pix[1] * a + canvas[d + 1] * inv) // 255
                canvas[d + 2] = (pix[2] * a + canvas[d + 2] * inv) // 255

    for y in range(0, h, 2):
        for x in range(0, w, 2):
            v = bg[x * h + y]
            if v & 0x7FFF:
                blit('Tiles', (v & 0x7FFF) - 1, x * 48, y * 32)
    for y in range(h):
        for x in range(w):
            i = x * h + y
            m = mid[i]
            if m & 0x7FFF:
                blit('SmTiles', (m & 0x7FFF) - 1, x * 48, y * 32)
    for y in range(h):
        for x in range(w):
            i = x * h + y
            v = fr[i]
            if v & 0x7FFF:
                unit = area[i] & 0xFF
                blit('Objects' if not unit else f'Objects{unit + 1}',
                     (v & 0x7FFF) - 1, x * 48, y * 32, bottom_anchor=True)

    if spawns:
        size = max(12, min(24, min(W, H) // 40))
        for x, y, appr, race in spawns:
            icon = monster_icon(appr, race, size)
            if not icon:
                continue
            iw, ih, pix = icon
            cx, cy = round(x * 48 * S), round(y * 32 * S)
            ox, oy = cx - iw // 2, cy - ih // 2
            for iy in range(ih):
                py = oy + iy
                if not (0 <= py < H):
                    continue
                for ix in range(iw):
                    px = ox + ix
                    if not (0 <= px < W):
                        continue
                    s = (iy * iw + ix) * 4
                    a = pix[s + 3]
                    if a < 32:
                        continue
                    d = (py * W + px) * 4
                    if a == 255:
                        canvas[d:d + 4] = pix[s:s + 4]
                    else:
                        inv = 255 - a
                        canvas[d] = (pix[s] * a + canvas[d] * inv) // 255
                        canvas[d + 1] = (pix[s + 1] * a + canvas[d + 1] * inv) // 255
                        canvas[d + 2] = (pix[s + 2] * a + canvas[d + 2] * inv) // 255
    return _png(W, H, bytes(canvas))


def render_map(map_id: str, spawns: list[tuple[int, int, int, int]]) -> bytes | None:
    """spawns = [(x, y, appr, raceImg)]。返回 PNG 字节。

    小图（室内/房间，w*h≤4000）走真实图层合成（能看清地板/家具/墙）；
    大图走平均色栅格（整张区域图，色块近似）。
    """
    path = next((p for p in (MAPDIR / f'{map_id}.map', MAPDIR / f'{map_id}.MAP')
                 if p.is_file()), None)
    if path is None:
        return None
    parsed = read_tiles(path)
    if parsed is None:
        return None
    if parsed[0] * parsed[1] <= SMALL_TILES:
        return render_map_small(*parsed, spawns)
    w, h, k, arr = parsed
    # 大图（区域/洞穴）：1 像素/格。背景只在偶数格绘制（PlayScn.pas:562），
    # 中/前景每格都画（PlayScn.pas:586、1143）。先按「原始值 → 平均色」建
    # 查表，主循环只做字典查找（49 万格也只需几秒）。
    # 索引顺序与 .map 一致：列优先 i = x*height + y。
    w2, h2 = (w + 1) // 2, (h + 1) // 2
    bg = sample_even(arr, k, w, h, 0)
    mid = arr[1::k][:w * h]
    fr = arr[2::k][:w * h]
    area = arr[5::k][:w * h]
    dark = (40, 40, 40, 255)

    bgc: dict[int, tuple | None] = {}
    for v in set(bg):
        bgc[v] = None if not v or v == 0x8000 else             LIBS.frame_avg('Tiles', (v & 0x7FFF) - 1)
    midc: dict[int, tuple | None] = {}
    for v in set(mid):
        midc[v] = None if not v else LIBS.frame_avg('SmTiles', (v & 0x7FFF) - 1)
    frc: dict[tuple[int, int], tuple | None] = {}

    color_of = collections.OrderedDict()      # rgba -> idx
    color_of[dark] = 0                        # 0 号 = 空地面底色

    def cidx(c) -> int:
        if c not in color_of:
            if len(color_of) >= 256:
                return 255
            color_of[c] = len(color_of)
        return color_of[c]

    idx = bytearray(w * h)
    for y in range(h):
        by = y >> 1
        for x in range(w):
            i = x * h + y
            c = bgc[bg[(x >> 1) * h2 + by]]
            if c is None:
                c = dark
            m = mid[i]
            if m:
                mc = midc[m]
                if mc and mc[3] > 100:
                    c = mc
            f = fr[i]
            if f & 0x7FFF:
                key = (f, area[i] & 0xFF)
                if key in frc:
                    ob = frc[key]
                else:
                    unit = key[1]
                    ob = LIBS.frame_avg('Objects' if not unit else f'Objects{unit + 1}',
                                        (f & 0x7FFF) - 1)
                    frc[key] = ob
                if ob and ob[3] > 40:
                    c = blend(c, ob, 0.7)
            idx[y * w + x] = cidx(c)          # PNG 行优先（y 外层）

    # 调色板补齐到 256 色
    pal = bytearray(1024)
    for c, i2 in color_of.items():
        pal[i2 * 4:i2 * 4 + 4] = bytes(c)

    # 索引 → RGBA 展开（str.translate 是 C 层、可按字符映射成多字节）
    table = {i: chr(pal[i * 4]) + chr(pal[i * 4 + 1]) + chr(pal[i * 4 + 2])
             + chr(pal[i * 4 + 3]) for i in range(256)}
    rgba = idx.decode('latin-1').translate(table).encode('latin-1')
    W, H = w, h

    # 刷新点图标（α 合成）
    if spawns and rgba:
        size = max(10, min(18, min(W, H) // 50))
        buf = bytearray(rgba)
        for x, y, appr, race in spawns:
            icon = monster_icon(appr, race, size)
            if not icon:
                continue
            iw, ih, pix = icon
            ox, oy = x - iw // 2, y - ih // 2
            for iy in range(ih):
                py = oy + iy
                if not (0 <= py < H):
                    continue
                for ix in range(iw):
                    px = ox + ix
                    if not (0 <= px < W):
                        continue
                    s = (iy * iw + ix) * 4
                    a = pix[s + 3]
                    if a < 32:
                        continue
                    d = (py * W + px) * 4
                    if a == 255:
                        buf[d:d + 4] = pix[s:s + 4]
                    else:
                        for ch in range(4):
                            buf[d + ch] = (pix[s + ch] * a + buf[d + ch] * (255 - a)) // 255
        rgba = bytes(buf)

    return _png(W, H, rgba)


_ICON_CACHE: dict[tuple[int, int, int], tuple | None] = {}


def monster_icon(appr: int, race_img: int, size: int, halo: bool = True):
    """怪物站姿图标（dir=4，首帧）。halo=False 不打描边（站点表格用）。"""
    key = (appr, race_img, size, halo)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    lib = mon_lib(appr)
    offset = mon_offset(appr)
    ma = RACE_ACTION.get(race_img, 'MA10')
    st, fr, sk = STANDS.get(ma, (0, 4, 6))
    idx = offset + st + 4 * (fr + sk)          # dir=4
    r = LIBS.frame_rgba(lib, idx)
    if r is None:
        r = LIBS.frame_rgba(lib, offset)       # 兜底：该怪首帧
    if r is None:
        _ICON_CACHE[key] = None
        return None
    fw, fh, pix = r
    scale = max(1, int(round(size / max(fw, fh))))
    tw, th = max(1, fw * scale), max(1, fh * scale)
    if tw > size * 2 or th > size * 2:         # 大怪按比例缩到 size
        scale = size / max(fw, fh)
        tw, th = max(1, int(fw * scale)), max(1, int(fh * scale))
    small = bytearray(tw * th * 4)
    for ty in range(th):
        sy = min(fh - 1, int(ty / th * fh))
        for tx in range(tw):
            sx = min(fw - 1, int(tx / tw * fw))
            s = (sy * fw + sx) * 4
            d = (ty * tw + tx) * 4
            small[d:d + 4] = pix[s:s + 4]
    pad = 1 if halo else 0
    ow, oh = tw + pad * 2, th + pad * 2
    out = bytearray(ow * oh * 4)
    if halo:   # 1px 暗色描边（深色地形上也看得清）
        for ty in range(th):
            for tx in range(tw):
                if small[(ty * tw + tx) * 4 + 3] < 32:
                    continue
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        d = ((ty + 1 + dy) * ow + (tx + 1 + dx)) * 4
                        if out[d + 3] == 0:
                            out[d:d + 4] = b'\x00\x00\x00\xc8'
    for ty in range(th):
        s0 = ty * tw * 4
        d0 = ((ty + pad) * ow + pad) * 4
        seg = small[s0:s0 + tw * 4]
        cur = out[d0:d0 + tw * 4]
        for k in range(0, len(seg), 4):
            if seg[k + 3] >= 32:
                cur[k:k + 4] = seg[k:k + 4]
        out[d0:d0 + tw * 4] = cur
    res = (ow, oh, bytes(out))
    _ICON_CACHE[key] = res
    return res


# ---------------------------------------------------------------- CLI

def load_spawns(tree: Path) -> dict[str, list[tuple[int, int, int, int]]]:
    import csv

    def dec(p: Path) -> str:
        raw = p.read_bytes()
        try:
            return raw.decode('utf-8')
        except UnicodeDecodeError:
            return raw.decode('gbk', 'replace')

    appr: dict[str, tuple[int, int]] = {}
    with (tree / 'mud2' / 'DB' / 'Monster.csv').open(encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            appr[r['Name'].strip()] = (int(float(r['Appr'] or 0)), int(float(r['RaceImg'] or 0)))
    out: dict[str, list[tuple[int, int, int, int]]] = collections.defaultdict(list)
    for ln in dec(tree / 'Mir200' / 'Envir' / 'MonGen.txt').splitlines():
        p = ln.split()
        if len(p) >= 6 and not ln.lstrip().startswith(';'):
            ap, rc = appr.get(p[3], (0, 0))
            try:
                x, y = int(float(p[1])), int(float(p[2]))
            except ValueError:
                continue
            out[p[0]].append((x, y, ap, rc))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--tree', default=None, help='gamedata 树（默认生效指针）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--only', default=None, help='只渲染指定图号')
    ap.add_argument('--skip-if-exists', action='store_true')
    a = ap.parse_args()

    tree = Path(a.tree).resolve() if a.tree else None
    if tree is None:
        root = 'gamedata'
        for ln in (ROOT / 'config' / 'server.properties').read_text(encoding='utf-8').splitlines():
            if ln.strip().startswith('gamedata.root'):
                root = ln.split('=', 1)[1].strip()
        tree = (ROOT / root).resolve()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    spawns = load_spawns(tree)
    ids = sorted({p.stem for p in MAPDIR.glob('*.map')}
                 | {p.stem for p in MAPDIR.glob('*.MAP')})
    if a.only:
        ids = [a.only]
    done = skip = 0
    for mid in ids:
        try:
            target = out / f'{mid}.png'.lower()
            if a.skip_if_exists and target.exists():
                skip += 1
                continue
            png = render_map(mid, spawns.get(mid, []))
            if png:
                target.write_bytes(png)
                done += 1
        except Exception as e:                     # noqa: BLE001
            print(f'[render] {mid}: {e}')
    print(f'渲染 {done} 张，跳过 {skip}，/ {len(ids)}')


if __name__ == '__main__':
    main()