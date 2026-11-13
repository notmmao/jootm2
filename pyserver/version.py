"""版本信息：读 ``pyserver/version.json``（由 ``scripts/gen_version.py`` 在镜像构建前生成）。

多 Agent 协作约定：部署构建镜像前先跑 ``python scripts/gen_version.py``，
``/api/version`` 返回的内容即部署验证依据（部署流程见 HANDOFF-PYSERVER.md）。
文件缺失（如未生成的开发环境）时回退 ``{"version": "dev"}``。
"""
import json
from pathlib import Path

_VERSION_FILE = Path(__file__).with_name("version.json")


def load_version() -> dict:
    """读取版本信息，每次调用都重新读盘（镜像内文件静态，本地开发便于热更）。"""
    try:
        data = json.loads(_VERSION_FILE.read_text("utf-8"))
        if isinstance(data, dict) and data.get("version"):
            return data
    except (OSError, ValueError):
        pass
    return {"version": "dev", "changes": []}
