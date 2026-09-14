"""不记录对话正文的单轮 NPC 决策 Trace。

Trace 面向性能诊断和安全审计，只保留意图、策略、选中材料标识、耗时和失败阶段；
不会记录认证凭证、玩家原始问题、完整 Prompt 或 NPC 回答。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from uuid import uuid4

TRACE_LOGGER_NAME = "npc_app.npc_turn_trace"
logger = logging.getLogger(TRACE_LOGGER_NAME)


@dataclass
class NpcTurnTrace:
    """一次 NPC 回合的结构化元数据。

    ``selected_chunk_ids`` 与 ``selected_memory_keys`` 只保存稳定标识，三个 ``*_ms``
    字段分别定位规划、检索与生成耗时，``guard_result`` 和 ``error_stage`` 用于判断
    回答是否被替换以及失败发生在哪个阶段。
    """

    trace_id: str
    thread_id: str
    npc_id: str
    intent: str = ""
    policy_source: str = ""
    plan_source: str = ""
    arc_stage: str = ""
    dialogue_strategy: str = ""
    retrieved_count: int = 0
    selected_chunk_ids: list[str] = field(default_factory=list)
    selected_memory_keys: list[str] = field(default_factory=list)
    prompt_chars: int = 0
    retrieval_ms: float = 0.0
    planning_ms: float = 0.0
    generation_ms: float = 0.0
    guard_result: str = "not_run"
    error_stage: str = ""

    @classmethod
    def create(cls, thread_id: str, npc_id: str) -> NpcTurnTrace:
        """用随机 trace_id 创建一条尚未经过任何决策阶段的 Trace。"""
        return cls(trace_id=str(uuid4()), thread_id=thread_id, npc_id=npc_id)

    def to_dict(self) -> dict:
        """转换为适合单行 JSON 日志输出的普通字典。"""
        return asdict(self)


def trace_enabled() -> bool:
    """解析环境变量开关；默认关闭以避免生产环境无意增加日志量。"""
    return os.getenv("NPC_TRACE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def emit_trace(trace: NpcTurnTrace) -> None:
    """启用时输出紧凑单行 JSON；关闭时不产生序列化开销。"""
    if not trace_enabled():
        return
    # 紧凑分隔符便于日志采集系统逐行解析，ensure_ascii=False 保留可读的中文元数据。
    logger.info(json.dumps(trace.to_dict(), ensure_ascii=False, separators=(",", ":")))
