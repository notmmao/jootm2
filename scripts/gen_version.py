"""生成 ``pyserver/version.json``：镜像构建前运行（部署流程见 HANDOFF-PYSERVER.md）。

内容 = git 短 hash + HEAD 主题 + 构建时间 + 最近 N 条提交摘要，
``/api/version`` 原样返回，部署后只需核对该端点的 version 是否为最新提交。

用法：
    python scripts/gen_version.py            # 默认带最近 10 条提交
    python scripts/gen_version.py --count 20
"""
import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "pyserver" / "version.json"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--count", type=int, default=10, help="changes 里带最近几条提交")
    args = ap.parse_args()

    lines = git("log", f"-{max(args.count, 1)}", "--pretty=%h %s").splitlines()
    if not lines:
        print("[version] git log 为空，未生成", file=__import__("sys").stderr)
        return 1
    version, _, subject = lines[0].partition(" ")
    data = {
        "version": version,
        "subject": subject,
        "builtAt": datetime.now().isoformat(timespec="seconds"),
        "changes": lines,
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(f"[version] {data['version']} {subject} -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
