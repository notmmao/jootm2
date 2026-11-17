# -*- coding: utf-8 -*-
"""WIL/WIX 打包与预览工具（纯标准库，对应 .claude/skills/mir2-resource-converter）。

把 PNG 帧序列打包成前端 wil.mjs 可直接加载的 WIL/WIX 图库（8 位索引色），
并可生成自包含 HTML 预览（按帧号契约播放动画、可视化 px/py 锚点）用于打包验证。

格式依据（全部实测核验过，勿凭记忆改动）：
- frontend/gfx/wil.mjs        解码器（WIX u32@44=帧数、偏移 u32@48 起、<1080 视为空图；
                              图头 u16 w/u16 h/i16 px/i16 py；8 位索引色按 palette.mjs
                              取色、16 位按 RGB565；行序自底向上；行 4 字节对齐与
                              否由「字节数」自动判定）
- frontend/gfx/palette.mjs    256 色调色板单一数据源（index 0 = 透明，其余 alpha=255）
- frontend/actor/actorData.mjs 帧号契约单一数据源（HA/MA 动作表、HUMANFRAME、
                              getMonOffset/getNpcOffset）

子命令：
  info    <lib>                          图库概览
  dump    <lib> --indices a-b,c --out D  提取帧为 PNG
  pack    <frames_dir> --out <base> ...  PNG 帧目录 → WIL/WIX（+ manifest + 自校验）
  preview <lib> ...                      生成自包含 HTML 预览（读 manifest 或 --layout）

命令行示例见 SKILL.md。
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
from wil_extract import WIL as WILReader, _png, load_palette  # noqa: E402

WIL_TITLE = b'#ILIB v1.0-WEMADE Entertainment inc.'
WIX_TITLE = b'#INDX v1.0-WEMADE Entertainment inc.'
HEADER_SIZE = 1080          # 44 title + 2 count + 2 colorcount + 4 + 4 + 256*4 调色板
TILE_W, TILE_H = 48, 32     # 等距瓦片尺寸；锚点 = 实体所在格左上角

HUMANFRAME = 600            # 人物每个外观占 600 帧（actorData.mjs）
MERCHANTFRAME = 60          # NPC 每个外观占 60 帧
MON_APPEARANCE = 360        # 多数怪物外观槽位宽（MA11/MA14 等布局的最大帧数）


# ---------------------------------------------------------------------------
# 帧号契约（运行时从 actorData.mjs 解析，保证单一数据源）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Seg:
    start: int
    frame: int
    skip: int
    ftime: int


def _parse_tables() -> tuple[dict[str, Seg], dict[str, dict[str, Seg]]]:
    """从 frontend/actor/actorData.mjs 解析 HA（人物动作表）与 MA（怪物动作表）。"""
    text = (ROOT / 'frontend' / 'actor' / 'actorData.mjs').read_text(encoding='utf-8')
    entry = re.compile(r'(\w+):\s*\{\s*start:\s*(\d+),\s*frame:\s*(\d+),'
                       r'\s*skip:\s*(\d+),\s*ftime:\s*(\d+)\s*\}')

    def grab(block: str) -> dict[str, Seg]:
        return {k: Seg(int(s), int(f), int(k2), int(t))  # noqa: A002 - k2 是 skip
                for k, s, f, k2, t in entry.findall(block)}

    ha_m = re.search(r'export const HA = \{(.*?)\n\};', text, re.S)
    ma_m = re.search(r'const MA = \{(.*?)\n\};', text, re.S)
    if not ha_m or not ma_m:
        raise RuntimeError('无法从 actorData.mjs 解析 HA/MA 动作表')
    ha = grab(ha_m.group(1))
    ma = {name: grab(body) for name, body in
          re.findall(r'(MA\d+):\s*\{(.*?)\n    \}', ma_m.group(1), re.S)}
    return ha, ma


HA_TABLES, MA_TABLES = _parse_tables()

# getMonOffset 的 Python 复刻（actorData.mjs:313，原版 GetMonImg/GetOffset 逻辑，勿改）；
# 改动必须先改 actorData.mjs 再同步这里。
def mon_lib_and_offset(appr: int) -> tuple[str, int]:
    """外观 appr → (图库 key, 外观帧偏移)。"""
    nrace, npos = divmod(appr, 10)
    lib = 'mon1' if nrace == 0 else ('effect' if nrace == 90 else f'mon{nrace + 1}')
    if nrace in (0,):
        return lib, npos * 280
    if nrace == 1:
        return lib, npos * 230
    if nrace == 13 and npos in (1, 2, 3):
        return lib, {1: 360, 2: 440, 3: 550}[npos]
    if nrace == 4 and npos == 1:
        return lib, 600
    if nrace == 18:
        return lib, {0: 0, 1: 520, 2: 950}.get(npos, 0)
    if nrace == 19:
        return lib, [0, 370, 810, 1250, 1630, 2010, 2390][npos]
    if nrace == 20:
        return lib, [0, 360, 720, 1080, 1440, 1800, 2350, 3060][npos]
    if nrace == 90:
        return lib, [80, 168, 184, 200][npos]
    return lib, npos * 360


def npc_offset(appr: int) -> int:
    """NPC 外观 appr → 帧偏移（actorData.mjs getNpcOffset，占位图外观未复刻）。"""
    if 0 <= appr <= 22:
        return MERCHANTFRAME * appr
    if appr == 23:
        return 1380
    return 1470 + MERCHANTFRAME * (appr - 24)


# 布局 → (动作表, 每外观槽位宽, 动作名集合)
LAYOUTS = {
    'hum':  (HA_TABLES, HUMANFRAME, ('stand', 'walk', 'run', 'hit', 'spell',
                                     'struck', 'sitdown', 'die')),
    'npc':  (MA_TABLES.get('MA50'), MERCHANTFRAME, ('stand', 'attack', 'struck')),
    'mon':  (None, MON_APPEARANCE, ('stand', 'walk', 'attack', 'struck', 'die', 'death')),
    'plain': (None, 0, ()),
}


# ---------------------------------------------------------------------------
# PNG 读写（纯标准库）
# ---------------------------------------------------------------------------

def read_png_rgba(path: Path) -> tuple[int, int, bytes]:
    """读 PNG → (w, h, rgba)。支持 8-bit 灰度/RGB/RGBA/索引色，不支持隔行。"""
    data = path.read_bytes()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError(f'{path} 不是 PNG')
    ihdr = None
    plte: bytes | None = None
    trns: bytes | None = None
    idat = bytearray()
    pos = 8
    while pos < len(data):
        ln = int.from_bytes(data[pos:pos + 4], 'big')
        tag = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + ln]
        if tag == b'IHDR':
            ihdr = payload
        elif tag == b'PLTE':
            plte = payload
        elif tag == b'tRNS':
            trns = payload
        elif tag == b'IDAT':
            idat += payload
        elif tag == b'IEND':
            break
        pos += 12 + ln
    w, h, depth, ctype, _comp, _filt, interlace = struct.unpack('>IIBBBBB', ihdr)
    if interlace:
        raise ValueError(f'{path} 是 Adam7 隔行 PNG，先转为非隔行')
    if depth != 8:
        raise ValueError(f'{path} 位深 {depth}，仅支持 8-bit')
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    stride = w * nch
    raw = zlib.decompress(bytes(idat))
    out = bytearray(stride * h)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ftype == 1:
            for i in range(nch, stride):
                line[i] = (line[i] + line[i - nch]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - nch] if i >= nch else 0
                c = prev[i - nch] if i >= nch else 0
                b = prev[i]
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        out[y * stride:(y + 1) * stride] = line
        prev = line

    rgba = bytearray(w * h * 4)
    if ctype == 6:
        return w, h, bytes(out)
    if ctype == 2:
        for i in range(w * h):
            rgba[i * 4:i * 4 + 3] = out[i * 3:i * 3 + 3]
            rgba[i * 4 + 3] = 255
    elif ctype == 0:
        for i in range(w * h):
            rgba[i * 4:i * 4 + 4] = bytes((out[i],) * 3 + (255,))
    elif ctype == 4:
        for i in range(w * h):
            rgba[i * 4:i * 4 + 3] = bytes((out[i * 2],) * 3)
            rgba[i * 4 + 3] = out[i * 2 + 1]
    elif ctype == 3:
        for i in range(w * h):
            ci = out[i]
            rgba[i * 4:i * 4 + 3] = plte[ci * 3:ci * 3 + 3]
            rgba[i * 4 + 3] = 255
        if trns:
            for ci, al in enumerate(trns[:len(plte) // 3]):
                for i in range(w * h):
                    if out[i] == ci:
                        rgba[i * 4 + 3] = al
    return w, h, bytes(rgba)


class Quantizer:
    """RGBA → 256 色调色板索引。index 0 = 透明；其余在 alpha=255 的候选色里最近邻。"""

    def __init__(self, pal: bytes):
        self.cand = [i for i in range(1, 256) if pal[i * 4 + 3] == 255]
        self.rgb = [(pal[i * 4], pal[i * 4 + 1], pal[i * 4 + 2]) for i in range(256)]
        self.cache: dict[tuple[int, int, int], int] = {}

    def index_of(self, r: int, g: int, b: int, a: int) -> int:
        if a < 128:
            return 0
        key = (r >> 3, g >> 3, b >> 3)
        hit = self.cache.get(key)
        if hit is None:
            best, best_d = 1, 1 << 30
            for i in self.cand:
                pr, pg, pb = self.rgb[i]
                d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2
                if d < best_d:
                    best_d, best = d, i
            self.cache[key] = hit = best
        return hit


def rgba_to_indices(w: int, h: int, rgba: bytes, q: Quantizer) -> bytes:
    out = bytearray(w * h)
    for i in range(w * h):
        out[i] = q.index_of(rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3])
    return bytes(out)


def indices_to_rgba(w: int, h: int, indices: bytes, pal: bytes) -> bytes:
    rgba = bytearray(w * h * 4)
    for i, ci in enumerate(indices):
        rgba[i * 4:i * 4 + 4] = pal[ci * 4:ci * 4 + 4]
    return bytes(rgba)


# ---------------------------------------------------------------------------
# WIL/WIX 写入
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    width: int
    height: int
    px: int
    py: int
    indices: bytes      # 自顶向下行序（写入时转自底向上）


def frame_rgba_bytes(f: Frame, pal: bytes) -> bytes:
    return indices_to_rgba(f.width, f.height, f.indices, pal)


def _frame_record(f: Frame) -> bytes:
    """单张图记录：u16 w/u16 h/i16 px/i16 py + 自底向上行序索引（不补行对齐）。"""
    rec = bytearray(struct.pack('<HHhh', f.width, f.height, f.px, f.py))
    for row in range(f.height - 1, -1, -1):
        rec += f.indices[row * f.width:(row + 1) * f.width]
    return bytes(rec)


def write_wilwix(out_base: Path, frames: list[Frame | None], pal: bytes) -> None:
    """frames[index] = Frame 或 None（空图）。同时写 <base>.wil 与 <base>.WIX。"""
    hdr = bytearray(HEADER_SIZE)
    hdr[0:len(WIL_TITLE)] = WIL_TITLE
    struct.pack_into('<HHII', hdr, 44, len(frames), 0, 256, 1024)
    for i in range(256):
        r, g, b = pal[i * 4], pal[i * 4 + 1], pal[i * 4 + 2]
        hdr[56 + i * 4:60 + i * 4] = bytes((b, g, r, 0))   # COLORREF 0x00RRGGBB 小端

    body = bytearray()
    offsets: list[int] = []
    for f in frames:
        if f is None:
            offsets.append(0)
            continue
        off = HEADER_SIZE + len(body)
        offsets.append(off)
        body += _frame_record(f)

    wil = bytes(hdr) + bytes(body)
    out_base.with_suffix('.wil').write_bytes(wil)
    wix = bytearray(WIX_TITLE.ljust(44, b'\x00'))
    wix += struct.pack('<I', len(frames))
    for off in offsets:
        wix += struct.pack('<I', off)
    out_base.with_suffix('.WIX').write_bytes(bytes(wix))


def load_existing_frames(wil_path: Path) -> list[Frame | None] | None:
    """读现有 WIL 的原始图记录（字节级保真，8/16 位都能原样搬运）；不是 WIL 返回 None。"""
    if not wil_path.exists():
        return None
    raw = wil_path.read_bytes()
    wix = None
    for cand in (wil_path.with_suffix('.WIX'), wil_path.with_suffix('.wix')):
        if cand.exists():
            wix = cand
            break
    if wix is None:
        return None
    b = wix.read_bytes()
    count = struct.unpack_from('<I', b, 44)[0]
    offsets = [struct.unpack_from('<I', b, 48 + i * 4)[0] for i in range(count)]
    valid = sorted({o for o in offsets if o >= HEADER_SIZE})
    bounds = {o: valid[i + 1] if i + 1 < len(valid) else len(raw)
              for i, o in enumerate(valid)}
    frames: list[Frame | None] = []
    for off in offsets:
        if off < HEADER_SIZE:
            frames.append(None)
            continue
        rec = raw[off:bounds[off]]
        w, h, px, py = struct.unpack_from('<HHhh', rec, 0)
        frames.append(Frame(w, h, px, py, rec[8:]))   # 原始字节（含可能的行对齐）
    return frames


# ---------------------------------------------------------------------------
# 布局：PNG 帧目录 → 目标索引
# ---------------------------------------------------------------------------

def parse_png_name(name: str) -> tuple[str, int, int] | None:
    """<action>_<dir>_<frame>.png → (action, dir, frame)；不带下划线的按文件名序。"""
    stem = Path(name).stem
    m = re.fullmatch(r'([a-z]+)_(\d)_(\d+)', stem)
    if m:
        return m.group(1), int(m.group(2)), int(m.group(3))
    return None


def layout_indices(layout: str, segs, slot: int, actions: tuple[str, ...],
                   race: str | None) -> dict[tuple[str, int, int], int]:
    """按帧号契约算 (action, dir, frame) → 外观内相对帧号 cf。"""
    if layout == 'plain':
        return {}
    if layout == 'mon':
        if race is None or race not in MA_TABLES:
            raise SystemExit(f'--layout mon 需要 --race（MA 表名，如 MA11）。'
                             f'可选：{", ".join(sorted(MA_TABLES))}')
        segs = MA_TABLES[race]
    out: dict[tuple[str, int, int], int] = {}
    for act in actions:
        seg = segs[act]
        for d in range(8):
            for k in range(seg.frame):
                cf = seg.start + d * (seg.frame + seg.skip) + k
                if cf >= slot:
                    raise SystemExit(f'{act} dir{d} frame{k} 相对帧 {cf} 超出'
                                     f'外观槽位 {slot}，检查动作表/槽位宽')
                out[(act, d, k)] = cf
    return out


def default_anchor(layout: str, w: int, h: int) -> tuple[int, int]:
    """缺省锚点：角色/怪/NPC 以格底中心为基准（px 居中、py 脚踩格底）；plain 贴原点。"""
    if layout == 'plain':
        return 0, 0
    return (TILE_W - w) // 2, TILE_H - h


def cmd_pack(args: argparse.Namespace) -> None:
    frames_dir = Path(args.frames_dir)
    pngs = sorted(p for p in frames_dir.iterdir() if p.suffix.lower() == '.png')
    if not pngs:
        raise SystemExit(f'{frames_dir} 里没有 PNG')

    out_base = Path(args.out)
    if out_base.suffix.lower() in ('.wil', '.wix'):
        out_base = out_base.with_suffix('')
    pal = load_palette()
    q = Quantizer(pal)

    existing: list[Frame | None] | None = None
    if args.merge:
        existing = load_existing_frames(out_base.with_suffix('.wil'))
        if existing is None:
            raise SystemExit(f'--merge 目标 {out_base} 不是现有 WIL/WIX 图库')
        frames: list[Frame | None] = list(existing)
    else:
        if out_base.with_suffix('.wil').exists() and not args.force:
            raise SystemExit(f'{out_base}.wil 已存在；追加外观用 --merge，'
                             f'重建整库用 --force')
        frames = []

    segs, slot, actions = LAYOUTS[args.layout]
    base = args.base
    if args.layout == 'hum' and args.appearance is not None:
        base = HUMANFRAME * args.appearance
    if args.layout == 'mon' and args.appr is not None:
        lib, off = mon_lib_and_offset(args.appr)
        base = off
        print(f'[mon] appr={args.appr} → 图库 {lib}（新库请自行核对 getMonImgLib），'
              f'外观偏移 {base}')
    if args.layout == 'npc' and args.appr is not None:
        base = npc_offset(args.appr)

    layout_map = layout_indices(args.layout, segs, slot, actions, args.race)

    named: dict[tuple[str, int, int], Path] = {}
    plain_list: list[Path] = []
    for p in pngs:
        parsed = parse_png_name(p.name)
        if parsed:
            named[parsed] = p
        else:
            plain_list.append(p)

    manifest_frames: dict[str, dict] = {}
    used: set[int] = set()
    new_idx: list[int] = []
    for key, p in sorted(named.items()):
        act, d, k = key
        if act not in actions:
            raise SystemExit(f'{p.name}: 动作 {act!r} 不在 {args.layout} 布局中'
                             f'（{", ".join(actions)}）')
        if args.layout == 'plain':
            raise SystemExit(f'{p.name}: plain 布局不支持 <action>_<dir>_<frame> 命名')
        w, h, rgba = read_png_rgba(p)
        idx = base + layout_map[key]
        if idx in used:
            raise SystemExit(f'{p.name}: 目标索引 {idx} 已被占用')
        used.add(idx)
        new_idx.append(idx)
        while len(frames) <= idx:
            frames.append(None)
        anchor = default_anchor(args.layout, w, h)
        px = args.px if args.px is not None else anchor[0]
        py = args.py if args.py is not None else anchor[1]
        frames[idx] = Frame(w, h, px, py, rgba_to_indices(w, h, rgba, q))
        manifest_frames.setdefault(act, {}).setdefault(str(d), {})[str(k)] = {
            'index': idx, 'png': p.name, 'px': px, 'py': py}

    if args.layout == 'plain':
        for k, p in enumerate(plain_list):
            w, h, rgba = read_png_rgba(p)
            idx = base + k
            if idx in used:
                raise SystemExit(f'{p.name}: 目标索引 {idx} 已被占用')
            used.add(idx)
            new_idx.append(idx)
            while len(frames) <= idx:
                frames.append(None)
            px = args.px if args.px is not None else 0
            py = args.py if args.py is not None else 0
            frames[idx] = Frame(w, h, px, py, rgba_to_indices(w, h, rgba, q))
            manifest_frames.setdefault('frame', {}).setdefault(str(k), {})['index'] = idx

    n_empty = sum(1 for f in frames if f is None)
    print(f'[pack] {args.layout}: 共 {len(frames)} 帧（含空图 {n_empty}），'
          f'本次写入 {len(used)} 帧 → {out_base}.wil')

    # 防呆：槽位尾部空洞会顶到下一外观偏移（hum 600/appearance 是硬约束）
    if args.layout != 'plain' and used:
        max_idx = max(used)
        if base + slot <= max_idx:
            raise SystemExit(f'最大索引 {max_idx} 越过外观槽位 [{base}, {base + slot})')

    write_wilwix(out_base, frames, pal)

    manifest = {'lib': out_base.name, 'layout': args.layout, 'race': args.race,
                'base': base, 'slot': slot, 'appearance_base': HUMANFRAME,
                'merchant_frame': MERCHANTFRAME, 'frames': manifest_frames}
    manifest_path = out_base.with_suffix('.manifest.json')
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding='utf-8')
    print(f'[pack] manifest → {manifest_path}')

    # 自校验：用真实解码路径（wil_extract.WIL）读回，比对量化后的 RGBA。
    # 只比对本次写入的帧——merge 搬运的旧帧是字节级保真（含行对齐/16 位），
    # 不能用「量化→调色板→RGBA」链路比对。
    if not args.no_verify:
        verify_lib(out_base.with_suffix('.wil'), frames, new_idx, pal)


def verify_lib(wil_path: Path, expected: list[Frame | None], indices: list[int],
               pal: bytes) -> None:
    reader = WILReader(wil_path.parent, wil_path.with_suffix('').name)
    bad = 0
    for idx in indices:
        f = expected[idx]
        if f is None:
            continue
        got = reader.frame_rgba(idx)
        if got is None:
            print(f'[verify] 索引 {idx} 解码失败')
            bad += 1
            continue
        w, h, rgba = got
        want = frame_rgba_bytes(f, pal)
        if (w, h) != (f.width, f.height) or rgba != want:
            print(f'[verify] 索引 {idx} 像素不一致 ({w}x{h} vs {f.width}x{f.height})')
            bad += 1
    if bad:
        raise SystemExit(f'[verify] {bad} 帧不一致，图库不可用！')
    print(f'[verify] 读回比对通过（{len(indices)} 帧）')


# ---------------------------------------------------------------------------
# info / dump
# ---------------------------------------------------------------------------

def resolve_lib(name: str) -> Path:
    p = Path(name)
    if p.suffix.lower() in ('.wil', '.wix'):
        p = p.with_suffix('')
    if p.with_suffix('.wil').exists():
        return p
    cand = ROOT / 'resources' / 'Data' / p.name
    if cand.with_suffix('.wil').exists():
        return cand
    raise SystemExit(f'找不到图库 {name}')


def cmd_info(args: argparse.Namespace) -> None:
    base = resolve_lib(args.lib)
    wil = base.with_suffix('.wil')
    wix = next((c for c in (base.with_suffix('.WIX'), base.with_suffix('.wix'))
                if c.exists()), None)
    raw = wil.read_bytes()
    count = struct.unpack_from('<I', wix.read_bytes(), 44)[0]
    offsets = [struct.unpack_from('<I', wix.read_bytes(), 48 + i * 4)[0]
               for i in range(count)]
    nonempty = [o for o in offsets if o >= HEADER_SIZE]
    print(f'图库   : {wil.name}  大小 {len(raw)} 字节')
    print(f'帧数   : {count}（空图 {count - len(nonempty)}）')
    print(f'首图偏移: {min(nonempty) if nonempty else "-"}（标准头 = {HEADER_SIZE}）')
    reader = WILReader(wil.parent, wil.with_suffix('').name)
    depths = {8: 0, 16: 0}
    want = (_parse_indices(args.indices, count) if args.indices is not None
            else None)
    limit = len(want) if want is not None else 12
    print(f'帧详情（index: w×h px,py 字节 位深）:')
    shown = 0
    for idx in range(count):
        if want is not None and idx not in want:
            continue
        if shown >= limit:
            break
        off = offsets[idx]
        if off < HEADER_SIZE:
            continue
        w, h, px, py = struct.unpack_from('<HHhh', raw, off)
        noff = min([o for o in nonempty if o > off], default=len(raw))
        bits = 16 if noff - off >= 8 + w * h * 2 else 8
        depths[bits] += 1
        print(f'  {idx:6d}: {w:4d}×{h:<4d} px={px:5d} py={py:5d} '
              f'{noff - off:8d}B {bits}bit')
        shown += 1
    if want is None:   # 全量位深统计只在不指定范围时做
        for off in nonempty:
            w, h, _px, _py = struct.unpack_from('<HHhh', raw, off)
            noff = min([o for o in nonempty if o > off], default=len(raw))
            depths[16 if noff - off >= 8 + w * h * 2 else 8] += 1
    print(f'位深分布: 8bit={depths[8]} 16bit={depths[16]}')


def _parse_indices(spec: str, count: int) -> set[int]:
    out = set()
    for part in spec.split(','):
        if '-' in part:
            a, b = part.split('-')
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    bad = {i for i in out if not 0 <= i < count}
    if bad:
        raise SystemExit(f'索引越界: {sorted(bad)}（帧数 {count}）')
    return out


def cmd_dump(args: argparse.Namespace) -> None:
    base = resolve_lib(args.lib)
    reader = WILReader(base.parent, base.name)
    idxs = sorted(_parse_indices(args.indices, reader.count)) if args.indices \
        else list(range(reader.count))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for idx in idxs:
        r = reader.frame_png(idx)
        if r is None:
            continue
        png, w, h = r
        (out / f'{idx:06d}.png').write_bytes(png)
        n += 1
    print(f'[dump] 提取 {n} 帧 → {out}')


# ---------------------------------------------------------------------------
# preview：自包含 HTML（动画播放 + px/py 锚点可视化）
# ---------------------------------------------------------------------------

def _clip_plan(layout: str, race: str | None, manifest: dict | None) -> dict:
    """返回 {action: (rows, ftime)}；rows = 8 个方向各 [(帧号, 延迟ms), ...]。

    有 manifest（pack 产物）时优先用其记录的实际帧号，否则按动作表推算。
    """
    if manifest and manifest.get('frames') and 'frame' not in manifest['frames']:
        segs, _slot, _acts = LAYOUTS[layout]
        if layout == 'mon':
            segs = MA_TABLES[race or 'MA11']
        plan = {}
        for act, dirs in sorted(manifest['frames'].items()):
            ftime = segs[act].ftime if act in segs else 200
            rows = []
            for d in range(8):
                items = sorted((int(k), v['index'])
                               for k, v in (dirs.get(str(d)) or {}).items())
                rows.append([(idx, k * ftime) for k, idx in items])
            if any(rows):
                plan[act] = (rows, ftime)
        return plan
    segs, _slot, actions = LAYOUTS[layout]
    if layout == 'mon':
        segs = MA_TABLES[race or 'MA11']
    if segs is None:
        raise SystemExit('plain 图库预览请用 --indices 指定帧（网格模式）')
    plan = {}
    for act in actions:
        seg = segs[act]
        rows = [[(seg.start + d * (seg.frame + seg.skip) + k, k * seg.ftime)
                 for k in range(seg.frame)] for d in range(8)]
        plan[act] = (rows, seg.ftime)
    return plan


def cmd_preview(args: argparse.Namespace) -> None:
    base = resolve_lib(args.lib)
    reader = WILReader(base.parent, base.name)
    manifest = None
    mp = base.with_suffix('.manifest.json')
    layout = args.layout
    race = args.race
    if args.layout is None:
        if mp.exists():
            manifest = json.loads(mp.read_text(encoding='utf-8'))
            layout = manifest['layout']
            race = manifest.get('race')
        else:
            layout = 'plain'
    if layout == 'plain':
        n = args.indices if args.indices is not None else min(reader.count, 200)
        idxs = sorted(_parse_indices(n, reader.count)) if args.indices \
            else list(range(n))
        _preview_plain(reader, base, idxs, args.out)
        return
    plan = _clip_plan(layout, race, manifest)
    frames = sorted({i for rows, _t in plan.values() for row in rows for i, _d in row})
    _preview_animated(reader, base, layout, race, plan, frames, args.out)


def _collect_pngs(reader: WILReader, indices: list[int]) -> list[dict]:
    out = []
    for idx in indices:
        r = reader.frame_rgba(idx)
        if r is None:
            continue
        w, h, rgba = r
        uri = 'data:image/png;base64,' + base64.b64encode(
            _png(w, h, rgba)).decode('ascii')
        off = reader.offsets[idx]
        px, py = struct.unpack_from('<hh', reader.wil, off + 4)
        out.append({'i': idx, 'px': px, 'py': py, 'u': uri})
    return out


def _preview_plain(reader: WILReader, base: Path, indices: list[int],
                   out_path: str) -> None:
    frames = _collect_pngs(reader, indices)
    html = PLAIN_TMPL.replace('/*FRAMES*/', json.dumps(frames))
    out = Path(out_path) if out_path else base.with_suffix('.preview.html')
    _write_preview(html, out, base.name)


def _preview_animated(reader: WILReader, base: Path, layout: str, race: str | None,
                      plan: dict, frames_idx: list[int], out_path: str) -> None:
    frames = {str(f['i']): f for f in _collect_pngs(reader, frames_idx)}
    clips = {act: [[[(str(i), t) for i, t in row] for row in rows], ftime]
             for act, (rows, ftime) in plan.items()}
    html = ANIM_TMPL.replace('/*CLIPS*/', json.dumps(clips)) \
                    .replace('/*FRAMES*/', json.dumps(frames)) \
                    .replace('/*META*/', json.dumps({'lib': base.name, 'layout': layout,
                                                     'race': race}))
    out = Path(out_path) if out_path else base.with_suffix('.preview.html')
    _write_preview(html, out, base.name)


def _write_preview(html: str, path: Path, title: str) -> None:
    path.write_text(html, encoding='utf-8')
    print(f'[preview] {title} → {path}（浏览器打开；每格锚点处画了 48×32 格子虚线框）')


ANIM_TMPL = """<!doctype html><html><head><meta charset="utf-8"><title>WIL 预览</title>
<style>
body{background:#222;color:#ccc;font:13px/1.5 monospace;margin:16px}
h3{margin:18px 0 6px;color:#fff}
.row{display:flex;gap:10px;flex-wrap:wrap}
.cell{background:repeating-conic-gradient(#333 0 25%,#444 0 50%) 0 0/16px 16px;
  position:relative;width:170px;height:170px;overflow:hidden}
canvas{position:absolute;left:0;top:0}
.lbl{color:#888;font-size:11px}
</style></head><body>
<div id="meta"></div><div id="app"></div>
<script>
const CLIPS = /*CLIPS*/;
const FRAMES = /*FRAMES*/;
const META = /*META*/;
document.getElementById('meta').textContent =
  `图库 ${META.lib}  布局 ${META.layout}${META.race ? ' / ' + META.race : ''}  ` +
  `（虚线框 = 实体所在 48×32 格，左上角即锚点 anchor；帧按 px/py 偏移贴图，与游戏一致）`;
const app = document.getElementById('app');
for (const [act, [dirs, ftime]] of Object.entries(CLIPS)) {
  const h = document.createElement('h3'); h.textContent = act + `（${ftime}ms/帧）`; app.appendChild(h);
  const row = document.createElement('div'); row.className = 'row'; app.appendChild(row);
  dirs.forEach((pairs, dir) => {
    const wrap = document.createElement('div');
    const cv = document.createElement('canvas'); cv.width = 170; cv.height = 170;
    cv.className = 'cell';
    const lbl = document.createElement('div'); lbl.className = 'lbl'; lbl.textContent = 'dir ' + dir;
    wrap.appendChild(cv); wrap.appendChild(lbl); row.appendChild(wrap);
    const ctx = cv.getContext('2d');
    const ax = 60, ay = 60;   // 锚点 = 格左上角
    const imgs = pairs.map(([i]) => FRAMES[i] ? (() => {
      const im = new Image(); im.src = FRAMES[i].u; return im;
    })() : null);
    let k = 0;
    function draw() {
      ctx.clearRect(0, 0, 170, 170);
      ctx.strokeStyle = '#6cf'; ctx.setLineDash([3, 3]);
      ctx.strokeRect(ax + .5, ay + .5, 48, 32);
      ctx.fillStyle = '#6cf'; ctx.fillRect(ax - 1, ay - 1, 3, 3);
      ctx.setLineDash([]);
      const f = FRAMES[pairs[k % pairs.length][0]];
      const im = imgs[k % pairs.length];
      if (f && im && im.complete) ctx.drawImage(im, ax + f.px, ay + f.py);
      k++;
    }
    draw(); setInterval(draw, ftime);
  });
}
</script></body></html>
"""

PLAIN_TMPL = """<!doctype html><html><head><meta charset="utf-8"><title>WIL 网格</title>
<style>body{background:#222;color:#ccc;font:12px monospace}
.g{display:flex;flex-wrap:wrap;gap:4px}
.c{background:repeating-conic-gradient(#333 0 25%,#444 0 50%) 0 0/16px 16px;
  border:1px solid #555;padding:2px;text-align:center}
</style></head><body><div class="g" id="g"></div>
<script>
const FRAMES = /*FRAMES*/;
const g = document.getElementById('g');
for (const f of FRAMES) {
  const d = document.createElement('div'); d.className = 'c';
  const im = new Image(); im.src = f.u;
  d.appendChild(im);
  const t = document.createElement('div');
  t.textContent = f.i + ' ' + f.px + ',' + f.py;
  d.appendChild(t); g.appendChild(d);
}
</script></body></html>
"""


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description='WIL/WIX 打包与预览')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('info', help='图库概览')
    p.add_argument('lib')
    p.add_argument('--indices', help='如 0-59,100')
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser('dump', help='提取帧为 PNG')
    p.add_argument('lib')
    p.add_argument('--indices', help='如 0-59（缺省全部）')
    p.add_argument('--out', required=True)
    p.set_defaults(fn=cmd_dump)

    p = sub.add_parser('pack', help='PNG 帧目录 → WIL/WIX')
    p.add_argument('frames_dir')
    p.add_argument('--out', required=True, help='输出基名（如 data/x/testmon）')
    p.add_argument('--layout', default='plain', choices=list(LAYOUTS),
                   help='帧号布局（缺省 plain 顺序排列）')
    p.add_argument('--base', type=int, default=0, help='起始帧号（plain/覆盖布局用）')
    p.add_argument('--appearance', type=int, help='hum：外观号（基址 = 600×外观）')
    p.add_argument('--appr', type=int, help='mon/npc：外观号（自动算图库与偏移）')
    p.add_argument('--race', help='mon：MA 表名（如 MA11）')
    p.add_argument('--px', type=int, help='统一 px 锚点（缺省按布局自动）')
    p.add_argument('--py', type=int, help='统一 py 锚点（缺省按布局自动）')
    p.add_argument('--merge', action='store_true', help='合并进现有图库（保留原图字节）')
    p.add_argument('--force', action='store_true', help='覆盖已存在的整库')
    p.add_argument('--no-verify', action='store_true', help='跳过读回自校验')
    p.set_defaults(fn=cmd_pack)

    p = sub.add_parser('preview', help='生成自包含 HTML 预览')
    p.add_argument('lib')
    p.add_argument('--layout', choices=list(LAYOUTS))
    p.add_argument('--race', help='mon：MA 表名')
    p.add_argument('--indices', help='plain 网格模式的帧范围（如 0-59）')
    p.add_argument('--out', help='HTML 输出路径（缺省 <lib>.preview.html）')
    p.set_defaults(fn=cmd_preview)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == '__main__':
    main()
