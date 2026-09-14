"""测试运行依赖的兜底配置。

`npc_app.database` 在导入阶段就要求 `NPC_DATABASE_URL`，而 CI 上没有本地 `.env`
（`.env` 被 .gitignore 排除）。这里先加载本地 `.env`，再为缺失项提供最小兜底值，
使测试既不依赖开发者本机配置，也不会在本地覆盖真实配置。

`load_dotenv` 默认不覆盖已存在的环境变量，因此本地 `.env` 的取值始终优先。
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# 仅在缺少配置时兜底：SQLAlchemy 的 create_engine 是惰性连接，测试不会真正连库。
os.environ.setdefault(
    "NPC_DATABASE_URL",
    "postgresql+psycopg://npc_test:npc_test@127.0.0.1:5432/npc_test",
)
