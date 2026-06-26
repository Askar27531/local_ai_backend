import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional

from langchain.agents import create_agent
from langchain.agents.middleware import (
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    ToolCallLimitMiddleware,
)

from app.prompts.agent_prompt import build_agent_system_prompt
from app.schemas.chat import SearchItem
from app.services.checkpointer_service import get_checkpointer
from app.services.llm_service import llm
from app.tools.registry import build_agent_tools
from app.tools.runtime import ToolRuntimeState

logger = logging.getLogger(__name__)

SUMMARY_TRIGGER_TOKENS = int(os.getenv("SUMMARY_TRIGGER_TOKENS", "1600"))
SUMMARY_TRIGGER_MESSAGES = int(os.getenv("SUMMARY_TRIGGER_MESSAGES", "30"))
SUMMARY_KEEP_MESSAGES = int(os.getenv("SUMMARY_KEEP_MESSAGES", "12"))
SUMMARY_TRIM_TOKENS = int(os.getenv("SUMMARY_TRIM_TOKENS", "1200"))
MODEL_CALL_RUN_LIMIT = int(os.getenv("MODEL_CALL_RUN_LIMIT", "8"))
TOOL_CALL_RUN_LIMIT = int(os.getenv("TOOL_CALL_RUN_LIMIT", "4"))


@dataclass
class AgentChatResult:
    answer: str
    used_search: bool
    sources: List[SearchItem]
    raw_output: str = ""


@dataclass
class AgentStreamState:
    answer: str = ""
    used_search: bool = False
    sources: List[SearchItem] = field(default_factory=list)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item["text"]))
                elif "content" in item:
                    parts.append(str(item["content"]))
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)

    return str(content)


def _build_agent(tool_state: ToolRuntimeState):
    tools = build_agent_tools(tool_state)
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=build_agent_system_prompt(),
        checkpointer=get_checkpointer(),
        middleware=[
            SummarizationMiddleware(
                model=llm,
                trigger=[
                    ("tokens", SUMMARY_TRIGGER_TOKENS),
                    ("messages", SUMMARY_TRIGGER_MESSAGES),
                ],
                keep=("messages", SUMMARY_KEEP_MESSAGES),
                trim_tokens_to_summarize=SUMMARY_TRIM_TOKENS,
            ),
            ModelCallLimitMiddleware(
                run_limit=MODEL_CALL_RUN_LIMIT,
                exit_behavior="end",
            ),
            ToolCallLimitMiddleware(
                run_limit=TOOL_CALL_RUN_LIMIT,
                exit_behavior="continue",
            ),
        ],
    )


def _build_config(checkpoint_thread_id: str, user_id: int) -> dict:
    return {
        "configurable": {
            "thread_id": checkpoint_thread_id,
            "user_id": str(user_id),
        }
    }


def run_agent_chat(question: str, checkpoint_thread_id: str, user_id: int) -> AgentChatResult:
    tool_state = ToolRuntimeState()
    agent = _build_agent(tool_state)
    config = _build_config(checkpoint_thread_id, user_id)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
    )

    raw_output = _content_to_text(result["messages"][-1].content)
    return AgentChatResult(
        answer=raw_output.strip(),
        used_search=tool_state.used_search,
        sources=tool_state.sources,
        raw_output=raw_output,
    )


def run_agent_chat_stream(
    question: str,
    checkpoint_thread_id: str,
    user_id: int,
    stream_state: Optional[AgentStreamState] = None,
):
    if stream_state is None:
        stream_state = AgentStreamState()

    tool_state = ToolRuntimeState()
    agent = _build_agent(tool_state)
    config = _build_config(checkpoint_thread_id, user_id)

    yield make_stream_event("status", {"message": "正在分析问题..."})

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
        stream_mode=["updates", "messages", "custom"],
        version="v2",
    ):
        mode, data = normalize_stream_part(chunk)

        if mode == "custom":
            if isinstance(data, dict):
                custom_type = data.get("type", "custom")
                yield make_stream_event(custom_type, data)
            else:
                yield make_stream_event("status", {"message": str(data)})
            continue

        if mode == "updates":
            if not isinstance(data, dict):
                continue

            for node_name, node_data in data.items():
                if node_name == "model":
                    messages_in_node = node_data.get("messages", []) if isinstance(node_data, dict) else []
                    for msg in messages_in_node:
                        tool_calls = getattr(msg, "tool_calls", []) or []
                        for tool_call in tool_calls:
                            tool_name = tool_call.get("name", "unknown_tool")
                            tool_args = tool_call.get("args", {})
                            yield make_stream_event(
                                "tool_start",
                                {
                                    "tool": tool_name,
                                    "args": tool_args,
                                    "message": f"准备使用工具：{tool_name}",
                                },
                            )
                elif node_name == "tools":
                    yield make_stream_event("status", {"message": "工具执行完成，正在整理结果..."})
            continue

        if mode == "messages":
            if not isinstance(data, tuple) or len(data) != 2:
                continue

            token, metadata = data
            if isinstance(metadata, dict):
                node = metadata.get("langgraph_node")
                if node and node != "model":
                    continue

            text = message_to_text(token)
            if not text:
                continue

            stream_state.answer += text
            yield make_stream_event("answer_delta", {"text": text})
            continue

    stream_state.used_search = tool_state.used_search
    stream_state.sources = tool_state.sources

    if tool_state.sources:
        yield make_stream_event(
            "sources",
            {
                "used_search": tool_state.used_search,
                "sources": [
                    {
                        "title": source.title,
                        "url": source.url,
                        "snippet": source.snippet,
                    }
                    for source in tool_state.sources
                ],
            },
        )

    yield make_stream_event(
        "done",
        {
            "message": "处理完成。",
            "used_search": tool_state.used_search,
            "source_count": len(tool_state.sources),
        },
    )


def make_stream_event(event_type: str, data: dict) -> str:
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


def message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if not content:
        return ""
    return _content_to_text(content)


def normalize_stream_part(chunk: Any):
    if isinstance(chunk, dict) and "type" in chunk and "data" in chunk:
        return chunk["type"], chunk["data"]

    if isinstance(chunk, tuple) and len(chunk) == 2:
        return chunk[0], chunk[1]

    return None, chunk
