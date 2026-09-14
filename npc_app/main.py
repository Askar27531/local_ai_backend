"""FastAPI 应用装配入口。

本模块只负责日志、生命周期、中间件和路由注册。NPC 对话业务保留在 API 与
dialogue/service 层，避免 Web 框架入口承担领域决策。
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from npc_app import __version__
from npc_app.api import router
from npc_app.database import init_db
from npc_app.services.memory_milvus_service import load_memory_collection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """在开始接收流量前初始化 Runtime 必需的持久化依赖。

    ``yield`` 之前属于启动阶段：创建缺失的 PostgreSQL 表，并验证、加载已由运维
    显式创建的记忆 Collection。任何异常都会阻止 FastAPI 进入服务状态，而不是让
    首个玩家请求才暴露基础设施问题。
    """
    # PostgreSQL 负责业务事实，记忆 Milvus 负责语义召回；任一初始化失败都应阻止应用进入可用状态。
    init_db()
    load_memory_collection()
    yield


# 应用元数据会直接出现在 OpenAPI 文档中；版本号与 npc_app 包保持单一来源。
app = FastAPI(
    title="Guichao Island NPC Backend",
    description="Unity backend for RAG-powered NPC chat.",
    version=__version__,
    lifespan=lifespan,
)

# Unity 开发环境可能来自不同端口，因此当前 Runtime 放开跨域；认证仍由 Bearer Token 承担。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
