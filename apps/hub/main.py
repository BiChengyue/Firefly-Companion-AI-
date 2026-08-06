"""Hub API 独立进程入口（D-3 迁入同仓后保持独立进程 hub-api）。

启动：cd apps/hub && python main.py        # 或 python -m src.hub.api_server
依赖：jsonschema + psutil（系统 Python 已具备；见 README.md）
配置：PCH_TOKEN / PCH_PHONE_TOKEN / PCH_DATA_DIR / PCH_PORT 等环境变量同原 Hub。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.hub.api_server import main  # noqa: E402

if __name__ == "__main__":
    main()
