#!/usr/bin/env python3
"""把 1.80 客户端 ``resources/Wav`` 全量转成 MP3，并生成前端音效编号索引。

方案 B（全量预转）的离线构建脚本。服务端零改动：mp3 放在 ``/resources/Audio/``
下，由现有静态文件路由直接提供（Python/Java 两端都已支持 audio/mpeg + Range）。

输入
  resources/Wav/sound.lst   GBK，原版 Delphi ``SoundUtil.pas::LoadSoundList`` 的
                            「编号: wav\\文件名」表；注释行 ``;`` 开头，允许空值。
  resources/Wav/*.wav       723 个标准 PCM。

输出
  resources/Audio/*.mp3     每个唯一 wav 一份 mp3：被 sound.lst 引用的用「最小编号」
                            命名（如 103.mp3），未被引用的用文件名 slug。
  frontend/audio/audio_map.json   提交进仓库的索引（不含二进制）：
                            {base, sounds{}, bgm{}, files{}, missing[], stats{}}

用法
  python scripts/convert_audio.py                  # 全量转码；已转且未变更的跳过
  python scripts/convert_audio.py --force          # 忽略时间戳，全部重转
  python scripts/convert_audio.py --check          # 只校验产出完整性（不调 ffmpeg）
  python scripts/convert_audio.py --check --probe 20   # 额外用 ffprobe 抽 20 个解码
  python scripts/convert_audio.py --explain 103    # 查某个编号对应的 URL/文件
  python scripts/convert_audio.py --self-test      # 临时目录自检，不碰真实资源
  python scripts/convert_audio.py --jobs 8 --dry-run

参数：短音效 ``-ac 1 -ar 22050 -b:a 64k``；>=1MB 的 BGM/长音频
``-ar 44100 -b:a 96k`` 保留原声道。改了参数就 bump ENCODER_VERSION，
脚本会自动让旧产物全部失效重转。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WAV_DIR = ROOT / "resources" / "Wav"
SOUND_LIST = WAV_DIR / "sound.lst"
AUDIO_DIR = ROOT / "resources" / "Audio"
MAP_FILE = ROOT / "frontend" / "audio" / "audio_map.json"

BASE_URL = "/resources/"
LIST_ENCODING = "gbk"
LARGE_WAV_BYTES = 1 << 20
ENCODER_VERSION = 1

SFX_ARGS = ["-ac", "1", "-ar", "22050", "-b:a", "64k", "-codec:a", "libmp3lame"]
BGM_ARGS = ["-ar", "44100", "-b:a", "96k", "-codec:a", "libmp3lame"]

# Delphi SoundUtil.pas 的 bmg_* 常量 + 客户端里其余几首整曲。
# 这些不在 sound.lst 里（原版由 PlayBGM 按文件名直接加载），所以单独编索引。
BGM_FILES = {
    "intro": "log-in-long2.wav",
    "select": "sellect-loop2.wav",
    "field": "Field2.wav",
    "gameover": "game over2.wav",
    "theme": "main_theme.wav",
    "heroLogin": "HeroLogin.wav",
    "heroLogout": "HeroLogout.wav",
}

LINE_RE = re.compile(r"^(\d+)\s*:\s*(.*)$")


# ---- 解析与规划 ----------------------------------------------------------

def parse_sound_list(lst: Path, wav_dir: Path) -> tuple[dict[int, str], list[int], list[int], int]:
    """返回 (id -> 实际 wav 文件名, 缺失 id, 空值 id, 注释行数)。

    路径里的 ``wav\\`` 前缀统一剥掉；大小写不敏感地匹配磁盘上的真实文件名，
    避免 Windows 能跑、Linux 挂载卷 404。
    """
    if not lst.is_file():
        raise FileNotFoundError(f"缺少编号表：{lst}")
    raw = lst.read_bytes().decode(LIST_ENCODING, errors="replace")
    by_lower = {p.name.lower(): p.name for p in wav_dir.iterdir()
                if p.is_file() and p.suffix.lower() == ".wav"}
    entries: dict[int, str] = {}
    missing: list[int] = []
    blanks: list[int] = []
    comments = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith((";", "//")):
            comments += 1
            continue
        m = LINE_RE.match(line)
        if not m:
            comments += 1
            continue
        idx = int(m.group(1))
        value = m.group(2).strip().strip('"')
        if not value:
            blanks.append(idx)
            continue
        name = os.path.basename(value.replace("\\", "/"))
        real = by_lower.get(name.lower())
        if real is None:
            missing.append(idx)
            continue
        entries[idx] = real
    return entries, missing, blanks, comments


def _slug(stem: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_").lower()
    return s or "audio"


def build_plan(wav_dir: Path, entries: dict[int, str]) -> dict:
    """每个唯一 wav 只转一次；编号表里的 id 都指向同一份 mp3。"""
    wavs = sorted((p for p in wav_dir.iterdir()
                   if p.is_file() and p.suffix.lower() == ".wav"),
                  key=lambda p: p.name.lower())
    min_id: dict[str, int] = {}
    for idx, name in entries.items():
        key = name.lower()
        if key not in min_id or idx < min_id[key]:
            min_id[key] = idx

    # 被引用的文件先排（用编号命名），未引用的排后面（用 slug 命名）
    ordered = sorted(wavs, key=lambda p: (min_id.get(p.name.lower(), 1 << 30),
                                          p.name.lower()))
    used: set[str] = set()
    wav_to_mp3: dict[str, str] = {}
    for p in ordered:
        key = p.name.lower()
        if key in min_id:
            stem = str(min_id[key])
        else:
            stem = _slug(p.stem)
        name = stem + ".mp3"
        n = 2
        while name.lower() in used:
            name = f"{stem}-{n}.mp3"
            n += 1
        used.add(name.lower())
        wav_to_mp3[p.name] = name

    sounds = {
        str(idx): {"mp3": wav_to_mp3[name], "wav": "Wav/" + name}
        for idx, name in sorted(entries.items())
    }
    files = {name: mp3 for name, mp3 in sorted(wav_to_mp3.items())}
    by_lower = {p.name.lower(): p.name for p in wavs}
    bgm: dict[str, dict[str, str]] = {}
    bgm_warnings: list[str] = []
    for key, wanted in BGM_FILES.items():
        real = by_lower.get(wanted.lower())
        if not real:
            bgm_warnings.append(f"{key}: 找不到 {wanted}")
            continue
        bgm[key] = {"mp3": wav_to_mp3[real], "wav": "Wav/" + real}
    return {
        "wavs": wavs,
        "wav_to_mp3": wav_to_mp3,
        "sounds": sounds,
        "files": files,
        "bgm": bgm,
        "bgm_warnings": bgm_warnings,
    }


def build_map(plan: dict, missing: list[int], blanks: list[int],
              lst_lines: int, mp3_bytes: int) -> dict:
    wav_bytes = sum(p.stat().st_size for p in plan["wavs"])
    return {
        "version": ENCODER_VERSION,
        "source": "resources/Wav/sound.lst (GBK)",
        "base": BASE_URL,
        "audioDir": "Audio/",     # mp3 相对 resources 根目录的存放目录
        "encoder": {
            "sfx": "ffmpeg " + " ".join(SFX_ARGS),
            "bgm": "ffmpeg " + " ".join(BGM_ARGS),
            "largeWavBytes": LARGE_WAV_BYTES,
        },
        "sounds": plan["sounds"],
        "bgm": plan["bgm"],
        "files": plan["files"],
        "missing": sorted(missing),
        "stats": {
            "wavFiles": len(plan["wavs"]),
            "mp3Files": len(plan["files"]),
            "soundIds": len(plan["sounds"]),
            "bgmTracks": len(plan["bgm"]),
            "missingIds": len(missing),
            "blankIds": len(blanks),
            "soundListLines": lst_lines,
            "wavBytes": wav_bytes,
            "mp3Bytes": mp3_bytes,
            "ratio": round(wav_bytes / mp3_bytes, 1) if mp3_bytes else 0,
            "bgmWarnings": plan["bgm_warnings"],
        },
    }


# ---- 转码 ----------------------------------------------------------------

def convert_one(src: Path, dst: Path, ffmpeg: str, force: bool,
                version_ok: bool) -> tuple[str, str]:
    if (not force and version_ok and dst.is_file() and dst.stat().st_size > 0
            and dst.stat().st_mtime >= src.stat().st_mtime):
        return "skip", ""
    args = BGM_ARGS if src.stat().st_size >= LARGE_WAV_BYTES else SFX_ARGS
    # 临时文件保留 .mp3 后缀（ffmpeg 靠后缀推断容器），并显式 -f mp3 双保险
    tmp = dst.with_name(dst.stem + ".part.mp3")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(src), *args, "-f", "mp3", str(tmp)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        tmp.unlink(missing_ok=True)
        return "fail", str(e)
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return "fail", (proc.stderr or "").strip()[:300]
    os.replace(tmp, dst)
    return "ok", ""


def run_convert(plan: dict, wav_dir: Path, audio_dir: Path, ffmpeg: str, jobs: int,
                force: bool, version_ok: bool, dry_run: bool,
                limit: int = 0) -> tuple[dict[str, int], int]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    # wav_to_mp3 的 key 是文件名，需要拼回目录
    tasks = [(wav_dir / name, audio_dir / mp3)
             for name, mp3 in plan["wav_to_mp3"].items()]
    if limit:
        tasks = tasks[:limit]
    counts = {"ok": 0, "skip": 0, "fail": 0}
    failures: list[str] = []
    if dry_run:
        print(f"[dry-run] 待处理 {len(tasks)} 个 wav；前 5 个：")
        for src, dst in tasks[:5]:
            size = src.stat().st_size
            print(f"  {src.name} ({size/1024:.0f}KB, "
                  f"{'BGM' if size >= LARGE_WAV_BYTES else 'SFX'}) -> {dst.name}")
        return {"ok": 0, "skip": len(tasks), "fail": 0}, 0
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = {pool.submit(convert_one, src, dst, ffmpeg, force, version_ok):
                   (src, dst) for src, dst in tasks}
        for i, fut in enumerate(as_completed(futures), 1):
            status, err = fut.result()
            counts[status] += 1
            if status == "fail":
                src, dst = futures[fut]
                failures.append(f"{src.name} -> {dst.name}: {err}")
            if i % 100 == 0 or i == len(tasks):
                print(f"  [{i}/{len(tasks)}] ok={counts['ok']} "
                      f"skip={counts['skip']} fail={counts['fail']}", flush=True)
    for f in failures[:20]:
        print("  [fail]", f, file=sys.stderr)
    elapsed = time.time() - started
    print(f"  转码耗时 {elapsed:.1f}s")
    return counts, elapsed


# ---- 校验 / 查询 ---------------------------------------------------------

def read_map(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"缺少索引 {path}，请先跑一次全量转码")
    return json.loads(path.read_text(encoding="utf-8"))


def check_output(audio_dir: Path, data: dict, ffmpeg: str, probe: int) -> int:
    files = data.get("files", {})
    missing_files = []
    total = 0
    for name, mp3 in files.items():
        p = audio_dir / mp3
        if not p.is_file() or p.stat().st_size == 0:
            missing_files.append(mp3)
        else:
            total += p.stat().st_size
    dangling = []
    for key in ("sounds", "bgm"):
        for k, v in data.get(key, {}).items():
            if not (audio_dir / v["mp3"]).is_file():
                dangling.append(f"{key}.{k} -> {v['mp3']}")
    print(f"索引版本   : {data.get('version')}")
    print(f"wav / mp3  : {data['stats']['wavFiles']} / {len(files)}")
    print(f"编号 / BGM : {len(data['sounds'])} / {len(data['bgm'])}")
    print(f"mp3 总大小 : {total/1024/1024:.2f} MB（wav {data['stats']['wavBytes']/1024/1024:.1f} MB，"
          f"压缩 {data['stats'].get('ratio', 0)}x）")
    print(f"缺失文件   : {len(missing_files)}；悬空引用: {len(dangling)}")
    for x in (missing_files + dangling)[:10]:
        print("  !", x)
    if probe:
        ffprobe = shutil.which("ffprobe") or ffmpeg.replace("ffmpeg", "ffprobe")
        ok = 0
        for name, mp3 in list(files.items())[:probe]:
            r = subprocess.run([ffprobe, "-v", "error", "-show_entries",
                                "format=duration", "-of", "default=nw=1:nk=1",
                                str(audio_dir / mp3)], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                ok += 1
            else:
                print("  ! 解码失败:", mp3, r.stderr.strip()[:120])
        print(f"ffprobe 抽样: {ok}/{min(probe, len(files))} 可解码")
        if ok != min(probe, len(files)):
            return 1
    return 0 if not missing_files and not dangling else 1


def explain(data: dict, audio_dir: Path, idx: int) -> int:
    key = str(idx)
    e = data["sounds"].get(key)
    if not e:
        print(f"编号 {idx} 没有可用音效"
              f"（{'sound.lst 指向缺失文件' if idx in data['missing'] else '表里没有这条'}）。")
        return 1
    p = audio_dir / e["mp3"]
    url = BASE_URL + data.get("audioDir", "Audio/") + e["mp3"]
    print(f"编号 {idx}")
    print(f"  mp3 : {url}  "
          f"{'存在 ' + str(p.stat().st_size) + ' B' if p.is_file() else '缺失（前端会回退 WAV）'}")
    print(f"  wav : {BASE_URL}{e['wav']}")
    print(f"  试听: curl -I http://127.0.0.1:8080{url}")
    return 0


# ---- 自检（不依赖真实资源）------------------------------------------------

def self_test(ffmpeg: str | None) -> int:
    checks = 0
    passed = 0

    def ck(label: str, cond: bool) -> None:
        nonlocal checks, passed
        checks += 1
        if cond:
            passed += 1
            print(f"  PASS {label}")
        else:
            print(f"  FAIL {label}")

    with tempfile.TemporaryDirectory(prefix="jootm2-audio-") as td:
        root = Path(td)
        wav = root / "Wav"
        wav.mkdir()
        for name in ("1.wav", "Game over2.wav", "M12-1.wav"):
            with wave.open(str(wav / name), "wb") as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(22050)
                f.writeframes(b"\x00\x00" * 1102)   # 0.05s 静音
        lst = wav / "sound.lst"
        lst.write_bytes("\n".join([
            "; 注释行",
            ";;160:  wav\\1.wav",
            "1:  wav\\1.wav",
            "2:  wav\\1.wav",          # 同一文件被两个编号引用
            "3:  wav\\NoSuch.wav",     # 缺失
            "4:",                        # 空值
            "5:  wav\\game OVER2.wav",  # 大小写不一致
            "132: wav\\M12-1.wav",      # 非数字文件名
        ]).encode(LIST_ENCODING))
        entries, missing, blanks, comments = parse_sound_list(lst, wav)
        ck("GBK 解码 + 注释/空值跳过", comments == 2 and blanks == [4])
        ck("反斜杠路径 + 大小写不敏感", entries.get(5) == "Game over2.wav")
        ck("缺失文件被记录", missing == [3])
        ck("非数字文件名可解析", entries.get(132) == "M12-1.wav")
        plan = build_plan(wav, entries)
        ck("多编号共享一份 mp3", plan["sounds"]["1"]["mp3"] == plan["sounds"]["2"]["mp3"])
        ck("每个唯一 wav 只产出一份 mp3", len(plan["files"]) == 3)
        ck("编号优先命名", plan["sounds"]["1"]["mp3"] == "1.mp3")
        ck("BGM 指向已编号引用的那份 mp3",
           plan["bgm"]["gameover"]["mp3"] == plan["sounds"]["5"]["mp3"])
        ck("mp3 文件名唯一", len(set(plan["files"].values())) == 3)
        data = build_map(plan, missing, blanks, 8, 12345)
        ck("索引结构可被前端序列化", json.loads(json.dumps(data))["sounds"]["132"]["wav"]
           == "Wav/M12-1.wav")
        if ffmpeg:
            audio = root / "Audio"
            counts, _ = run_convert(plan, wav, audio, ffmpeg, 2, False, True, False)
            ck("ffmpeg 转码成功", counts["ok"] == 3 and counts["fail"] == 0)
            counts2, _ = run_convert(plan, wav, audio, ffmpeg, 2, False, True, False)
            ck("按 mtime 幂等（第二次全跳过）", counts2["skip"] == 3)
        else:
            print("  SKIP ffmpeg 转码（本机未找到 ffmpeg）")
    print(f"self-test: {passed}/{checks} PASS")
    return 0 if passed == checks else 1


# ---- 入口 ----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="全量 WAV→MP3 + 生成前端音效索引（方案 B）")
    ap.add_argument("--root", type=Path, default=ROOT, help="项目根目录")
    ap.add_argument("--wav-dir", type=Path, default=None)
    ap.add_argument("--audio-dir", type=Path, default=None)
    ap.add_argument("--map", type=Path, default=None)
    ap.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件")
    ap.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 4))
    ap.add_argument("--force", action="store_true", help="忽略时间戳，全部重转")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个（调试用，不写索引）")
    ap.add_argument("--check", action="store_true", help="只校验产出")
    ap.add_argument("--probe", type=int, default=0, help="--check 时用 ffprobe 抽 N 个")
    ap.add_argument("--explain", type=int, metavar="ID", help="查某个音效编号")
    ap.add_argument("--self-test", action="store_true", help="临时目录自检")
    args = ap.parse_args()

    wav_dir = args.wav_dir or args.root / "resources" / "Wav"
    audio_dir = args.audio_dir or args.root / "resources" / "Audio"
    map_file = args.map or args.root / "frontend" / "audio" / "audio_map.json"

    if args.self_test:
        return self_test(shutil.which(args.ffmpeg))
    ffmpeg = shutil.which(args.ffmpeg)
    if args.check or args.explain is not None:
        try:
            data = read_map(map_file)
        except (OSError, ValueError) as e:
            print(f"[error] {e}", file=sys.stderr)
            return 2
        if args.explain is not None:
            return explain(data, audio_dir, args.explain)
        return check_output(audio_dir, data, ffmpeg or args.ffmpeg, args.probe)

    if not wav_dir.is_dir():
        print(f"[error] 找不到资源目录 {wav_dir}；请先把 1.80 客户端的 Wav/ 放进去。",
              file=sys.stderr)
        return 2
    if not ffmpeg:
        print("[error] 找不到 ffmpeg（可用 --ffmpeg 指定路径）。", file=sys.stderr)
        return 2

    try:
        entries, missing, blanks, comments = parse_sound_list(wav_dir / "sound.lst", wav_dir)
    except FileNotFoundError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    plan = build_plan(wav_dir, entries)
    print(f"编号表: {len(entries)} 条有效 / {len(missing)} 条指向缺失文件 / "
          f"{len(blanks)} 条空值 / {comments} 行注释")
    print(f"素材  : {len(plan['wavs'])} 个 wav -> {len(plan['files'])} 个 mp3；"
          f"BGM {len(plan['bgm'])} 首")
    for warn in plan["bgm_warnings"]:
        print("  [warn]", warn)

    old_version = 0
    if map_file.is_file():
        try:
            old_version = read_map(map_file).get("version", 0)
        except (OSError, ValueError):
            pass
    version_ok = old_version == ENCODER_VERSION
    if not version_ok and not args.dry_run:
        print(f"  索引版本 {old_version} != {ENCODER_VERSION}，本次强制重转全部 mp3。")

    counts, _ = run_convert(plan, wav_dir, audio_dir, ffmpeg, args.jobs, args.force,
                            version_ok, args.dry_run, args.limit)
    if args.dry_run or args.limit:
        print("  dry-run/limit 模式：不写索引文件。")
        return 0
    if counts["fail"]:
        print(f"[error] {counts['fail']} 个文件转码失败，索引未更新。", file=sys.stderr)
        return 1

    mp3_bytes = sum((audio_dir / mp3).stat().st_size
                    for mp3 in plan["files"].values()
                    if (audio_dir / mp3).is_file())
    data = build_map(plan, missing, blanks,
                     len((wav_dir / "sound.lst").read_bytes()
                         .decode(LIST_ENCODING, errors="replace").splitlines()),
                     mp3_bytes)
    map_file.parent.mkdir(parents=True, exist_ok=True)
    map_file.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    wav_mb = data["stats"]["wavBytes"] / 1024 / 1024
    print(f"完成: mp3 {mp3_bytes/1024/1024:.2f} MB / wav {wav_mb:.1f} MB "
          f"({data['stats']['ratio']}x)，索引 -> {map_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
