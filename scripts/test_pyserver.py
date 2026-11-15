#!/usr/bin/env python3
"""pyserver 离线自检（零第三方依赖，不需要启动任何服务端）。

覆盖：配置、密码哈希、CSV 模板、GBK 配置解析、.map 二进制（12/14 字节、
列优先、阻挡/高墙/门）、资源索引的 WZL 拒绝、HTTP Range、WS 帧编码、
Gson 模板字段互通。

    python scripts/test_pyserver.py               # 只跑离线用例
    python scripts/test_pyserver.py --live 8081   # 额外做端到端 HTTP/WS 探测

退出码：0 全通过 / 1 有用例失败。
"""
from __future__ import annotations

import argparse
import hashlib
import gzip
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyserver import config as config_mod  # noqa: E402
from pyserver.db import Database, hash_password, new_salt  # noqa: E402
from pyserver.gamedata import (  # noqa: E402
    _TILE_12, _TILE_14, generate_resources_index, map_tile_stride,
    parse_map_info, parse_mini_map, parse_mon_gen, parse_start_point, read_lines, read_map,
)
from pyserver.net import (  # noqa: E402
    GzipCache, Request, Server, _etag_matches, _not_modified_since,
    cache_control, content_type, parse_range, ws_frame,
)
from pyserver.templates import ItemTemplate, MagicTemplate, MonsterTemplate  # noqa: E402
from pyserver.npc_script import NpcScript, NpcScriptEngine  # noqa: E402
from pyserver.world import direction_delta, exp_for_level  # noqa: E402

PASS, FAIL = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASS if condition else FAIL).append(name)
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f"  {detail}" if detail else ""))


def test_config() -> None:
    print("\n[1] 配置")
    cfg = config_mod.load(root=ROOT)
    check("默认端口取自 server.properties", cfg.port == 8080, f"port={cfg.port}")
    check("gamedata 指向项目内目录", (cfg.envir_dir / "MapInfo.txt").is_file())
    check("web root 存在", (cfg.web_root / "index.html").is_file())
    os.environ["JOOTM2_PORT"] = "9999"
    check("环境变量可覆盖端口", config_mod.load(root=ROOT, config="no-such-file").port == 9999)
    os.environ.pop("JOOTM2_PORT", None)


def test_password() -> None:
    print("\n[2] 密码哈希（Java DbUtil 等价）")
    expect = hashlib.sha256(b"abc1234").hexdigest()
    check("SHA-256(salt+password) 拼接规则", hash_password("abc", "1234") == expect)
    check("salt 为 16 字节 hex", len(new_salt()) == 32)


def test_csv_templates() -> None:
    print("\n[3] CSV 模板映射 + Gson 字段互通")
    from pyserver.templates import item_from_csv, magic_from_csv, monster_from_csv, read_csv_rows
    mud2 = ROOT / "gamedata" / "mud2" / "DB"
    items = read_csv_rows(mud2 / "StdItems.csv")
    monsters = read_csv_rows(mud2 / "Monster.csv")
    magics = read_csv_rows(mud2 / "Magic.csv")
    check("StdItems.csv 行数", len(items) >= 500, f"{len(items)} 行")
    check("Monster.csv 行数", len(monsters) >= 250, f"{len(monsters)} 行")
    check("Magic.csv 行数", len(magics) >= 30, f"{len(magics)} 行")
    t = item_from_csv(items[0])
    check("物品模板字段映射", t.name == items[0][1] and t.stdMode == int(items[0][2] or 0))
    m = monster_from_csv(monsters[0])
    check("怪物模板字段映射", m.name == monsters[0][0])
    mg = magic_from_csv(magics[0])
    check("技能 attrs + resolve", mg.magId == int(magics[0][0]) and mg.spell >= 0)
    keys = set(ItemTemplate().to_storage())
    check("ItemTemplate 含 Java Gson 关键字段",
          {"name", "stdMode", "duraMax", "dcMin", "hpAdd"} <= keys)
    check("MonsterTemplate 可反序列化",
          MonsterTemplate.from_storage(MonsterTemplate(name="鸡").to_storage()).name == "鸡")
    check("MagicTemplate 可反序列化",
          MagicTemplate.from_storage(MagicTemplate(magId=1, name="火球术").to_storage()).name == "火球术")


def test_text_data() -> None:
    print("\n[4] GBK / UTF-8 配置解析（按内容自动识别）")
    env = ROOT / "gamedata" / "Mir200" / "Envir"
    sp = parse_start_point(env / "StartPoint.txt")
    check("StartPoint 解析", sp.map_id and sp.x > 0 and sp.y > 0, str(sp))
    info = parse_map_info(env / "MapInfo.txt")
    check("MapInfo 地图定义数", len(info.titles) >= 400, f"{len(info.titles)} 张")
    check("MapInfo 门数量", len(info.gates) >= 2000, f"{len(info.gates)} 个")
    check("MonGen 刷怪条目", len(parse_mon_gen(env / "MonGen.txt")) >= 2000)
    check("MiniMap 索引", len(parse_mini_map(env / "MiniMap.txt")) >= 150)
    # 同一份内容分别写成 GBK / UTF-8 / UTF-8+BOM，read_lines 必须都读对
    sample = ";注释\n[3\t比奇省]\n0 330 330 鹿 50 10 5\n"
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        gbk = d / "a.txt"
        gbk.write_bytes(sample.encode("gbk"))
        u8 = d / "b.txt"
        u8.write_bytes(sample.encode("utf-8"))
        bom = d / "c.txt"
        bom.write_bytes(b"\xef\xbb\xbf" + sample.encode("utf-8"))
        check("GBK 文本可读", read_lines(gbk)[1] == "[3\t比奇省]")
        check("UTF-8 文本可读（无 BOM）", read_lines(u8)[1] == "[3\t比奇省]")
        check("UTF-8 文本可读（带 BOM）", read_lines(bom)[1] == "[3\t比奇省]")
    # 一键转换脚本：幂等 + 真的把 GBK 变 UTF-8
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        f = d / "x.txt"
        f.write_bytes("[0\t比奇省]\n".encode("gbk"))
        script = ROOT / "scripts" / "convert_gbk_to_utf8.py"
        subprocess.run([sys.executable, str(script), str(d), "--apply",
                        "--backup-dir", str(d / ".gbk-backup"), "--quiet"],
                       check=True, capture_output=True)
        check("转换脚本输出 UTF-8", f.read_bytes().decode("utf-8") == "[0\t比奇省]\n")
        check("转换脚本已备份原文件", (d / ".gbk-backup" / "x.txt").is_file())
        r = subprocess.run([sys.executable, str(script), str(d), "--check", "--quiet"],
                           capture_output=True)
        check("转换脚本幂等（--check 通过）", r.returncode == 0,
              r.stdout.decode("utf-8", "ignore").strip())


def _write_map(path: Path, width: int, height: int, tile: struct.Struct,
               cells: dict[tuple[int, int], tuple[int, int, int, int]]) -> None:
    data = bytearray(struct.pack("<HH", width, height) + bytes(48))
    for x in range(width):
        for y in range(height):
            data += tile.pack(*cells.get((x, y), (0, 0, 0, 0)))
    path.write_bytes(bytes(data))


def test_map_binary() -> None:
    print("\n[5] .map 二进制解析")
    check("tile 结构长度", _TILE_12.size == 12 and _TILE_14.size == 14)
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        cells = {
            (0, 0): (0x8000, 0, 0, 0),        # 普通阻挡
            (1, 0): (0, 0, 0x8000, 0),        # 高墙
            (2, 0): (0, 0, 0, 0x81),          # 门（低 7 位 = 1）
        }
        _write_map(d / "a.map", 3, 2, _TILE_12, cells)
        g = read_map("a", d)
        check("12 字节头+尺寸", g.width == 3 and g.height == 2 and g.from_file)
        check("普通阻挡", not g.can_walk(0, 0) and not g.is_high_wall(0, 0))
        check("高墙标志", g.is_high_wall(1, 0))
        check("门标志", (2, 0) in g.doors and g.doors[(2, 0)] == 1)
        check("列优先不错位", g.can_walk(0, 1) and not g.can_walk(1, 0))
        _write_map(d / "b.map", 3, 2, _TILE_14, cells)
        g14 = read_map("b", d)
        check("14 字节变体自适应", g14.is_high_wall(1, 0) and g14.can_walk(0, 1))
        # 1.80 客户端有 66 张图在 tile 数据后带附加数据块，长度不整除 w*h。
        check("步进推断 12/14/扩展/padding",
              (map_tile_stride(6 * 12, 6), map_tile_stride(6 * 14, 6),
               map_tile_stride(6 * 19, 6), map_tile_stride(6 * 12 + 20, 6))
              == (12, 14, 19, 12))
        padded = (d / "a.map").read_bytes() + bytes(20)
        (d / "c.map").write_bytes(padded)
        gc = read_map("c", d)
        check("带附加数据的地图按 12B 读不错位",
              not gc.can_walk(0, 0) and gc.is_high_wall(1, 0)
              and gc.doors.get((2, 0)) == 1 and gc.can_walk(0, 1),
              f"body={len(padded) - 52}B")
        missing = read_map("zzz", d)
        check("缺失地图回退 256x256 全通行",
              missing.width == 256 and missing.can_walk(10, 10) and not missing.from_file)


def test_resources_index() -> None:
    print("\n[6] resources.json 索引（WZL 假兼容修复）")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for name in ("Data", "Map", "Wav"):
            (root / name).mkdir()
        (root / "Data" / "hum.wix").write_bytes(b"x" * 48)
        (root / "Data" / "hum.wil").write_bytes(b"x" * 1080)
        (root / "Data" / "modern.wzx").write_bytes(b"x")
        (root / "Data" / "modern.wzl").write_bytes(b"x")
        (root / "Map" / "0.map").write_bytes(b"m")
        (root / "Wav" / "001.wav").write_bytes(b"w")
        idx = generate_resources_index(root)
        check("只索引 WIL/WIX 成对图库", set(idx["images"]) == {"hum"}, str(list(idx["images"])))
        check("WZL/WZX 被排除", not any("modern" in k for k in idx["images"]))
        check("地图索引", idx["maps"].get("0", "").startswith("Map/"))
        check("音效索引", idx["wavs"].get("001", "").startswith("Wav/"))
        check("索引文件已落盘 UTF-8",
              json.loads((root / "resources.json").read_text(encoding="utf-8")) == idx)


def test_http_ws_helpers() -> None:
    print("\n[7] HTTP / WS 工具函数")
    check("Range 0-99", parse_range("bytes=0-99", 1000) == (0, 99))
    check("Range 900-", parse_range("bytes=900-", 1000) == (900, 999))
    check("Range -50 后缀", parse_range("bytes=-50", 1000) == (950, 999))
    check("Range 越界返回 None", parse_range("bytes=2000-", 1000) is None)
    check("html no-cache", cache_control("x.html") == "no-cache")
    check("mjs 短缓存 + 可校验", "max-age=300" in cache_control("x.mjs")
          and "must-revalidate" in cache_control("x.mjs"))
    check("wil immutable", "immutable" in cache_control("x.wil"))
    check("MIME mjs", "javascript" in content_type("a.mjs"))
    check("ETag 精确匹配", _etag_matches('"10-ab", "10-cd"', '"10-cd"'))
    check("ETag 弱校验匹配", _etag_matches('W/"10-ab"', '"10-ab"'))
    check("ETag 不匹配", not _etag_matches('"10-ff"', '"10-ab"'))
    import time as _time
    from email.utils import formatdate as _fd
    check("If-Modified-Since 未来时间命中",
          _not_modified_since(_fd(_time.time() + 3600, usegmt=True), 1))
    check("If-Modified-Since 过去时间不命中",
          not _not_modified_since(_fd(1, usegmt=True), _time.time()))

    # 静态文件：304 / gzip / HEAD / Range 不压缩 / 素材版本指纹
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        payload = b'{"padding":"' + b"A" * 4000 + b'"}'
        (root / "a.json").write_bytes(payload)
        stub = type("S", (), {
            "_gzip": GzipCache(), "_error": Server._error,
            "_asset_ver": ("", 0.0),
            "config": type("C", (), {"resources_root": root})(),
            "_serve_file": lambda s, *a, **kw: Server._serve_file(s, *a, **kw),
        })()
        hdr = {"accept-encoding": "gzip, deflate"}
        get = Server._serve_file(stub, Request("GET", "/a.json", {}, hdr, b""),
                                 "/a.json", root, "", "index.html")
        check("GET 返回 ETag / Last-Modified", "ETag" in get.headers
              and "Last-Modified" in get.headers)
        check("GET 文本走 gzip", get.headers.get("Content-Encoding") == "gzip"
              and get.body[:2] == b"\x1f\x8b")
        check("gzip 体积显著变小", len(get.body) < len(payload) // 4,
              f"{len(payload)} -> {len(get.body)}")
        etag = get.headers["ETag"]
        cond = Server._serve_file(stub, Request("GET", "/a.json", {},
                                                {"if-none-match": etag}, b""),
                                  "/a.json", root, "", "index.html")
        check("If-None-Match -> 304", cond.status == 304 and not cond.body)
        head = Server._serve_file(stub, Request("HEAD", "/a.json", {}, hdr, b""),
                                  "/a.json", root, "", "index.html")
        check("HEAD 与 GET 同 Content-Length", head.head_only
              and head.headers.get("Content-Length") == get.headers.get("Content-Length"))
        rng = Server._serve_file(stub, Request("GET", "/a.json", {},
                                               {"range": "bytes=0-99"}, b""),
                                 "/a.json", root, "", "index.html")
        check("Range 206 无 Content-Encoding", rng.status == 206
              and "Content-Encoding" not in rng.headers)

        # Range 分片应用层 gzip：协商头 + 解压后字节必须与原始分片一致
        wil = (b"ABCD" * 4096 + b"\x00" * 8192) * 20
        (root / "c.wil").write_bytes(wil)
        plain = Server._serve_file(stub, Request("GET", "/c.wil", {},
                                                 {"range": "bytes=100-2099"}, b""),
                                   "/c.wil", root, "", "index.html")
        zreq = Request("GET", "/c.wil", {},
                       {"range": "bytes=100-2099", "x-jootm2-gzip": "1"}, b"")
        zr = Server._serve_file(stub, zreq, "/c.wil", root, "", "index.html")
        check("Range 分片默认不压缩", plain.status == 206
              and plain.headers.get("X-Jootm2-Gzip") is None
              and plain.headers.get("Content-Length") == "2000")
        check("协商后 206 带 X-Jootm2-Gzip 且 Content-Range 不变",
              zr.status == 206 and zr.headers.get("X-Jootm2-Gzip") == "1"
              and zr.headers.get("Content-Range") == f"bytes 100-2099/{len(wil)}"
              and "Content-Encoding" not in zr.headers)
        check("gzip 分片解压后字节一致且体积变小",
              len(zr.body) < 2000 and gzip.decompress(zr.body) == wil[100:2100]
              and zr.headers.get("Content-Length") == str(len(zr.body)))

        # 数据库静态站点：/site 前缀 → data/website，"/site/" 走目录 index.html
        site = root / "site"
        site.mkdir()
        (site / "index.html").write_text("<h1>db</h1>", encoding="utf-8")
        stub.config = type("C", (), {
            "resources_root": root, "resources_prefix": "/resources",
            "website_root": site, "website_prefix": "/site", "web_root": root,
        })()
        redir = Server.handle_http(stub, Request("GET", "/site", {}, {}, b""))
        check("/site 补斜杠 301", redir.status == 301
              and redir.headers.get("Location") == "/site/")
        idx = Server.handle_http(stub, Request("GET", "/site/", {}, {}, b""))
        check("/site/ 落到站点 index.html", idx.status == 200
              and idx.headers.get("Content-Type", "").startswith("text/html"))
        esc = Server.handle_http(stub, Request("GET", "/site/x.html", {}, {}, b""))
        check("/site 不存在的页面 -> 404", esc.status == 404)
        trav = Server.handle_http(stub, Request("GET", "/site/../a.json", {}, {}, b""))
        check("/site 前缀阻止 .. 穿越", trav.status == 404)
        ver1 = Server.asset_version(stub)
        (root / "b.wil").write_bytes(b"x" * 32)
        stub._asset_ver = ("", 0.0)
        ver2 = Server.asset_version(stub)
        check("素材版本为 16 位 hex 且随资源变化", len(ver1) == 16
              and ver1 != ver2 and Server.asset_version(stub) == ver2)

        # /api/asset-version 附带默认出生点，供客户端在 Loading 阶段预载首屏纹理
        world = type("W", (), {
            "default_map": type("M", (), {"name": "0"})(),
            "start_x": 289, "start_y": 618, "map_mini_index": {"0": 101},
        })()
        stub.service = type("Svc", (), {"world": world})()
        stub.asset_version = lambda: "0123456789abcdef"
        api = Server.handle_http(stub, Request("GET", "/api/asset-version", {}, {}, b""))
        data = json.loads(api.body.decode("utf-8"))
        check("asset-version 附带默认出生点",
              data.get("start") == {"map": "0", "x": 289, "y": 618, "miniMapIndex": 101},
              str(data.get("start")))
        stub.service = None
        api2 = Server.handle_http(stub, Request("GET", "/api/asset-version", {}, {}, b""))
        check("无 world 时出生点降级为空对象",
              json.loads(api2.body.decode("utf-8")).get("start") == {})

    for size in (5, 200, 70000):
        f = ws_frame(b"x" * size)
        head = 2 if size < 126 else (4 if size < 65536 else 10)
        check(f"WS 帧编码 len={size}", f[0] == 0x81 and len(f) == size + head)


def test_misc() -> None:
    print("\n[8] 世界常量")
    check("经验表 101 级", exp_for_level(1) == 100 and exp_for_level(101) == 2140000000)
    check("方向表 dir2=右", direction_delta(2) == (1, 0))
    check("方向表 dir4=下", direction_delta(4) == (0, 1))


def test_live(port: int) -> None:
    print(f"\n[9] 端到端探测 127.0.0.1:{port}")
    import urllib.error
    import urllib.request
    from smoke_ws import WS

    def get(path: str, headers: dict | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    code, _, body = get("/api/status")
    check("/api/status 200 + JSON", code == 200 and "monsters" in body.decode("utf-8"))
    code, headers, body = get("/main.mjs", {"Range": "bytes=0-99"})
    check("Range 返回 206", code == 206, headers.get("Content-Range", ""))
    import re
    check("Content-Range 带总长", bool(re.search(r"/\d+\s*$", headers.get("Content-Range", ""))))
    check("206 恰好 100 字节", len(body) == 100)
    check("CORS 头", headers.get("Access-Control-Allow-Origin") == "*")
    code, _, _ = get("/")
    check("静态首页 200", code == 200)
    try:
        ws = WS("127.0.0.1", port, "/ws", timeout=0.8)
        ws.send({"type": "ping"})
        got = ws.drain(4)
        ws.close()
        check("WS ping→pong", any(m.get("type") == "pong" for m in got))
    except (OSError, RuntimeError, EOFError) as e:
        check("WS ping→pong", False, str(e))


def test_shop_goods() -> None:
    """M2 [goods]：第二列是库存；买价 = Round(Round(Price*1.1)/100*%倍率)。

    原版链路 ObjNpc.pas:798-823 CheckItemPrice → 999-1024 GetItemPrice
    → 1385-1412 GetUserPrice，详见 NpcScriptEngine.goods 的注释。
    """
    print("\n[商店价格] Market_Def / NpcScriptEngine.goods")
    import types

    script = NpcScript.parse([
        "; 商店脚本头", "%150", "+5", "+6",
        "[@main]", "需要什么？",
        "[goods]", "木剑 10 10", "青铜剑 5 3",
    ])
    check("脚本头 %150 解析为价格倍率", script.price_rate == 150,
          f"rate={script.price_rate}")
    check("+N 分类行解析为 item_types", script.item_types == [5, 6],
          f"types={script.item_types}")
    check("[goods] 行按空白切分", script.goods_rows() == [["木剑", "10", "10"],
                                                        ["青铜剑", "5", "3"]])
    # 原版 LocalDB.pas:3251-3256：%rate 只有 >=55 才接受，否则保持默认 100；
    # LocalDB.pas:3261-3268：三列缺一不可。
    low = NpcScript.parse(["%50", "[@main]", "[goods]", "木剑 10 10", "残缺行"])
    check("%rate <55 不生效", low.price_rate == 100, f"rate={low.price_rate}")
    check("[goods] 不足三列的行被忽略",
          low.goods_rows() == [["木剑", "10", "10"]], str(low.goods_rows()))

    t1, t2 = ItemTemplate(), ItemTemplate()
    t1.index, t1.name, t1.price = 1, "木剑", 50
    t2.index, t2.name, t2.price = 2, "青铜剑", 900
    world = types.SimpleNamespace(
        item_template_by_name=lambda n: {"木剑": t1, "青铜剑": t2}.get(n))
    npc = types.SimpleNamespace(script=script)
    goods = NpcScriptEngine(world).goods(npc)
    from pyserver.util import delphi_round
    expected = [delphi_round(delphi_round(50 * 1.1) / 100 * 150),
                delphi_round(delphi_round(900 * 1.1) / 100 * 150)]
    check("买价 = Round(Round(StdItems.Price*1.1)/100*%倍率)",
          [g["price"] for g in goods] == expected,
          str([(g["name"], g["price"], g["stock"]) for g in goods]))
    check("第二列落到 stock", [g["stock"] for g in goods] == [10, 5])

    # 真实脚本：每个商店都有 %100，且价格不再全部相同
    real = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "2Bwe-0103.txt"
    if real.is_file():
        rs = NpcScript.parse(read_lines(real))
        check("真实武器店脚本头为 %100", rs.price_rate == 100,
              f"rate={rs.price_rate}")
        check("真实武器店 [goods] 有货", len(rs.goods_rows()) >= 10,
              f"{len(rs.goods_rows())} 行")

    # 比奇「白家服装老板」：原版 1 级能买到布衣（StdItems Price=400）
    cloth = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "3Tdr-0.txt"
    if cloth.is_file():
        cs = NpcScript.parse(read_lines(cloth))
        names = [r[0] for r in cs.goods_rows() if r]
        check("比奇服装店卖 1 级布衣", "布衣(男)" in names and "布衣(女)" in names,
              str(names[:4]))
        check("新手村服装店不卖中型盔甲", not any("中型" in n for n in names),
              str(names))

    # 村店/城店品类对比（用户第 15/16 轮反馈：新手村种类必须比主城少）。
    # 2026-11-15 数据清理（docs/MAIN-CHANGELOG.md）：176 的 Market_Def 是通用
    # 模板（比奇村/比奇城都 #CALL 同一份武器店.txt），村店所以改成独立精简表：
    # 武器店只留入门五把+鹤嘴锄，服装店不卖中型，杂货店卖蜡烛/火把但不卖药。
    # 比奇书店(5B) 回退为 176 基线的 7 本基础书，高级书只在边境村书店(5Tbo)出售。
    v_wea = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "2Twe-0.txt"
    c_wea = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "2Bwe-0103.txt"
    v_shop = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "4Tdu-0.txt"
    v_book = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "5Tbo-0132.txt"
    c_book = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "5B-0.txt"
    if v_wea.is_file() and c_wea.is_file():
        vn = {r[0] for r in NpcScript.parse(read_lines(v_wea)).goods_rows()}
        cn = {r[0] for r in NpcScript.parse(read_lines(c_wea)).goods_rows()}
        check("边境村武器店只卖入门五把+鹤嘴锄",
              vn == {"木剑", "青铜剑", "铁剑", "短剑", "匕首", "鹤嘴锄"},
              f"村店={sorted(vn)}")
        check("边境村武器店是比奇卫家店真子集", vn < cn,
              f"城独有={sorted(cn - vn)}")
    if v_shop.is_file():
        sn = {r[0] for r in NpcScript.parse(read_lines(v_shop)).goods_rows()}
        check("边境村小店对齐 176：卷轴/护身符/修复油",
              {"随机传送卷", "地牢逃脱卷", "行会回城卷", "护身符", "修复油"} <= sn,
              str(sorted(sn)))
        check("边境村小店卖蜡烛/火把（夜间视野）", {"蜡烛", "火把"} <= sn, str(sorted(sn)))
        check("边境村小店只卖小量红蓝药", {"金创药(小量)", "魔法药(小量)"} <= sn
              and not any("中量" in n or "强效" in n or "超级" in n or "包" in n
                          and "药" in n for n in sn), str(sorted(sn)))
    # 银杏新人村（map 0119 + map 0 银杏片区）与边界村同为新手村，同一分档口径
    g_wea = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "2Gwe-0.txt"
    g_cloth = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "3Gdr-0.txt"
    g_shop = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "7Gst-0.txt"
    g_drug = ROOT / "gamedata" / "Mir200" / "Envir" / "Market_Def" / "4Gdu-0119.txt"
    if g_wea.is_file():
        gn = {r[0] for r in NpcScript.parse(read_lines(g_wea)).goods_rows()}
        check("银杏村武器店只卖入门五把+鹤嘴锄",
              gn == {"木剑", "青铜剑", "铁剑", "短剑", "匕首", "鹤嘴锄"},
              f"村店={sorted(gn)}")
    if g_cloth.is_file():
        gc = {r[0] for r in NpcScript.parse(read_lines(g_cloth)).goods_rows()}
        check("银杏村服装店不卖中型盔甲", not any("中型" in n for n in gc),
              str(sorted(gc)))
    if g_shop.is_file():
        gs = {r[0] for r in NpcScript.parse(read_lines(g_shop)).goods_rows()}
        check("银杏村杂货店卖蜡烛/火把、不卖药", {"蜡烛", "火把"} <= gs
              and not any("药" in n for n in gs), str(sorted(gs)))
    if g_drug.is_file():
        gd = {r[0] for r in NpcScript.parse(read_lines(g_drug)).goods_rows()}
        check("银杏村药店只卖小量红蓝药",
              gd == {"金创药(小量)", "魔法药(小量)"}, str(sorted(gd)))
    if v_book.is_file() and c_book.is_file():
        vb = NpcScript.parse(read_lines(v_book))
        cb = NpcScript.parse(read_lines(c_book))
        vn2 = {r[0] for r in vb.goods_rows()}
        cn2 = {r[0] for r in cb.goods_rows()}
        check("边境村书店打 %80 折（比奇 %100）", vb.price_rate == 80,
              f"rate={vb.price_rate}")
        check("比奇书店只剩 176 基线的 7 本基础书",
              cn2 == {"火球术", "治愈术", "基本剑术", "精神力战法",
                      "大火球", "施毒术", "攻杀剑术"}, f"城={sorted(cn2)}")
        check("边境村书店同样只卖 176 的 7 本基础书",
              vn2 == {"火球术", "治愈术", "基本剑术", "精神力战法",
                      "大火球", "施毒术", "攻杀剑术"}
              and not ({"雷电术", "刺杀剑术"} & vn2), f"村={sorted(vn2)}")

    # 卖给 NPC 的原版定价（handlers_npc._sell_price）：
    # ClientSellItem(ObjNpc.pas:2156) = Round(GetItemPrice/2)；GetItemPrice:
    # 补货清单内 → Round(Price*1.1)；+N 类型外 → -1 拒收。
    def make_npc(row_indexes, item_types):
        goods_rows = [{"index": i, "name": f"i{i}", "price": 0, "stock": 1}
                      for i in row_indexes]
        sc = types.SimpleNamespace(item_types=item_types)
        return types.SimpleNamespace(script=sc), goods_rows

    from pyserver.handlers_npc import NpcHandlers
    h = NpcHandlers.__new__(NpcHandlers)   # 只测纯函数，绕过 mixin 依赖
    h.world = types.SimpleNamespace(
        goods=lambda n: make_npc.goods_rows)   # noqa: 由 make_npc 注入
    tw = types.SimpleNamespace(index=1, name="木剑", price=50, stdMode=5, duraMax=4000)
    to = types.SimpleNamespace(index=2, name="护身符", price=500, stdMode=25, duraMax=0)
    npc, rows = make_npc([1], [5, 6])
    make_npc.goods_rows = rows
    # 原版链路：GetItemPrice=Round(Price*1.1)=55 → 装备折旧段（满耐久无损）
    # → GetSellItemPrice = Round(55/2) = 28。
    check("卖本店补货物品 = Round(Round(Price*1.1)/2)（木剑 28）",
          h._sell_price(npc, tw) == 28,
          f"price={h._sell_price(npc, tw)}")
    check("卖 +N 类型外的物品被拒收（护身符 stdMode 25）",
          h._sell_price(npc, to) == -1, f"price={h._sell_price(npc, to)}")
    npc2, rows2 = make_npc([], [])
    make_npc.goods_rows = rows2
    check("无 +N 的 NPC 什么类型都收，价 = Round(Price/2)",
          h._sell_price(npc2, to) == delphi_round(500 / 2.0),
          f"price={h._sell_price(npc2, to)}")

    # 耐久/品质折旧（GetUserItemPrice ObjNpc.pas:1838-1919 直译）：
    # 肉(StdMode 40)：品质 = 耐久；品质=DuraMax 卖最贵，偏低打折，超上限暴跌。
    # 鸡肉 Price=80 → 补货价 88；DuraMax=4000。
    tmeat = types.SimpleNamespace(index=57, name="鸡肉", price=80, stdMode=40, duraMax=4000)
    npc3, rows3 = make_npc([57], [])
    make_npc.goods_rows = rows3
    it_full = {"durability": 4000, "durabilityMax": 4000}
    it_mid = {"durability": 3000, "durabilityMax": 4000}
    it_high = {"durability": 6500, "durabilityMax": 4000}
    # 88 → 88（满品质）→ 卖 44；3000: 88-11=77 → 再折 77-9.6=67 → 卖 34
    check("满品质肉卖 44（补货价 88 一半）",
          h._sell_price(npc3, tmeat, it_full) == 44,
          f"price={h._sell_price(npc3, tmeat, it_full)}")
    check("品质 3000 的鸡肉卖 34（双段折旧）",
          h._sell_price(npc3, tmeat, it_mid) == 34,
          f"price={h._sell_price(npc3, tmeat, it_mid)}")
    check("品质超 DuraMax 的肉暴跌（原版 else 分支）",
          h._sell_price(npc3, tmeat, it_high) <= 2,
          f"price={h._sell_price(npc3, tmeat, it_high)}")
    # 矿(StdMode 43)：DuraMax 强制 ≥10000；黑铁矿石 Price=2000 → 2200；
    # Dura 5000/DuraMax 30000：2200-917=1283 → 1283-535=748 → 卖 374。
    tore = types.SimpleNamespace(index=198, name="黑铁矿石", price=2000, stdMode=43, duraMax=30000)
    npc4, rows4 = make_npc([198], [])
    make_npc.goods_rows = rows4
    it_ore = {"durability": 5000, "durabilityMax": 30000}
    check("纯度 5000 的黑铁矿石卖 374（双段折旧）",
          h._sell_price(npc4, tore, it_ore) == 374,
          f"price={h._sell_price(npc4, tore, it_ore)}")
    # 装备磨损：木剑 Dura 2000/4000：55→55→磨损 55-13.75=41 → 卖 Round(41/2)=20。
    # 注意先把补货清单复位回木剑（上面矿石用例覆盖了 make_npc.goods_rows）。
    make_npc.goods_rows = rows
    it_worn = {"durability": 2000, "durabilityMax": 4000}
    check("半磨损木剑卖 20（装备折旧段）",
          h._sell_price(npc, tw, it_worn) == 20,
          f"price={h._sell_price(npc, tw, it_worn)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="pyserver 离线自检")
    ap.add_argument("--live", type=int, metavar="PORT", help="额外探测运行中的服务端端口")
    a = ap.parse_args()
    print("=" * 64)
    print("jootm2 pyserver 自检")
    print("=" * 64)
    test_config()
    test_password()
    test_csv_templates()
    test_text_data()
    test_map_binary()
    test_resources_index()
    test_http_ws_helpers()
    test_misc()
    test_shop_goods()
    if a.live:
        test_live(a.live)
    print("\n" + "=" * 64)
    print(f"结果: {len(PASS)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        for name in FAIL:
            print("  FAILED:", name)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
