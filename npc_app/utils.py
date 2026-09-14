"""对话配置、文本规范化和模型输出解析的共享工具。"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from npc_app.contracts import MAX_RETRIEVAL_TOP_K, NpcId

_DIALOGUE_CONFIG_PATH = Path(__file__).with_name("dialogue_config.json")
_DIALOGUE_CONFIG_SECTIONS = {
    "story_safety",
    "characters",
    "intent_rules",
    "intent_policies",
    "planner",
    "relationship",
    "npc_guards",
    "retrieval",
    "context",
    "memory_extraction",
}


@lru_cache(maxsize=1)
def dialogue_config() -> dict[str, Any]:
    """加载并校验对话配置，每个进程只执行一次磁盘读取。

    校验聚焦所有下游模块共同依赖的结构约束：必需分区、敏感词唯一性、角色字段和
    四类 Context 预算。配置无效时立即抛出 RuntimeError，避免运行到某个玩家回合才失败。
    """
    try:
        data = json.loads(_DIALOGUE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to load dialogue config: {_DIALOGUE_CONFIG_PATH}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("dialogue config root must be a JSON object")
    missing = sorted(_DIALOGUE_CONFIG_SECTIONS - data.keys())
    if missing:
        raise RuntimeError(f"dialogue config is missing sections: {', '.join(missing)}")
    if any(not isinstance(data[section], dict) for section in _DIALOGUE_CONFIG_SECTIONS):
        raise RuntimeError("every dialogue config section must be a JSON object")

    # 敏感词是模型外剧情守卫的基础，空值或重复项都可能造成行为不一致。
    story = data["story_safety"]
    terms = story.get("sensitive_terms")
    if not isinstance(terms, list) or not terms or len(terms) != len(set(terms)):
        raise RuntimeError("story_safety.sensitive_terms must be a non-empty list without duplicates")
    if not all(isinstance(term, str) and term.strip() for term in terms):
        raise RuntimeError("story_safety.sensitive_terms must contain non-empty strings")

    # 这里只校验所有角色运行时必需的公共字段；具体内容仍由配置文件维护。
    characters = data["characters"]
    character_fields = {
        "display_name",
        "profile_marker",
        "line_rule",
        "retrieval_terms",
        "basic_responses",
        "unknown_response",
        "annoyed_responses",
    }
    invalid_characters = [
        npc_id
        for npc_id, config in characters.items()
        if not npc_id.startswith("_") and (not isinstance(config, dict) or character_fields - config.keys())
    ]
    if invalid_characters:
        raise RuntimeError(f"dialogue config has invalid character entries: {', '.join(invalid_characters)}")
    character_ids = {npc_id for npc_id in characters if not npc_id.startswith("_")}
    expected_character_ids = {npc_id.value for npc_id in NpcId}
    if character_ids != expected_character_ids:
        raise RuntimeError("dialogue config characters must exactly match NpcId")
    for npc_id in character_ids:
        responses = characters[npc_id]["annoyed_responses"]
        if not isinstance(responses, dict) or any(
            not isinstance(responses.get(mode), str) or not responses[mode].strip()
            for mode in ("caring", "firm")
        ):
            raise RuntimeError(f"characters.{npc_id}.annoyed_responses must define caring and firm")

    policies = data["intent_policies"]
    for intent, policy in policies.items():
        if intent.startswith("_"):
            continue
        if not isinstance(policy, dict):
            raise RuntimeError(f"intent_policies.{intent} must be a JSON object")
        context_enabled = policy.get("context_enabled")
        top_k = policy.get("top_k")
        if not isinstance(context_enabled, bool) or not isinstance(top_k, int):
            raise RuntimeError(f"intent_policies.{intent} must define boolean context_enabled and integer top_k")
        valid_top_k = 1 <= top_k <= MAX_RETRIEVAL_TOP_K if context_enabled else top_k == 0
        if not valid_top_k:
            raise RuntimeError(
                f"intent_policies.{intent}.top_k must be 1-{MAX_RETRIEVAL_TOP_K} when context is enabled, "
                "or 0 when disabled"
            )

    # 每个策略预算固定对应摘要、记忆、历史和世界知识四个非负字符上限。
    budgets = data["context"].get("strategy_budgets", {})
    if not budgets or any(
        not isinstance(values, list)
        or len(values) != 4
        or any(not isinstance(value, int) or value < 0 for value in values)
        for values in budgets.values()
    ):
        raise RuntimeError("context.strategy_budgets values must contain four non-negative integers")
    return data


def compact_text(text: str) -> str:
    """统一大小写并删除空白，用于不受排版影响的规则词匹配。"""
    return "".join(text.lower().split())


def contains_any(text: str, terms: tuple[str, ...]) -> bool:
    """判断文本是否包含任一规范化关键词；空关键词集合自然返回 False。"""
    normalized = compact_text(text)
    return any(compact_text(term) in normalized for term in terms)


def search_terms(text: str) -> set[str]:
    """提取英文数字词和中文二元片段，供轻量相关性与回退检索使用。

    中文通常没有空格分词，因此同时保留完整连续短语和相邻双字片段；这不是语义模型，
    但在 Milvus 无结果时能提供稳定、无需额外依赖的关键词重叠评分。
    """
    ascii_terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", text)}
    cjk_terms: set[str] = set()
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        cjk_terms.add(phrase)
        cjk_terms.update(phrase[index : index + 2] for index in range(len(phrase) - 1))
    return ascii_terms | cjk_terms


def normalized_text(text: str) -> str:
    """压缩连续空白并统一大小写，用于内容去重和稳定哈希。"""
    return re.sub(r"\s+", " ", text).strip().lower()


def message_text(message: Any) -> str:
    """从 LangChain 字符串或多段消息内容中提取连续文本。

    正式生成、Planner 和记忆提取共用此适配器，以兼容 provider 返回纯字符串、内容块
    列表或其他可字符串化内容的差异。
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or item.get("content") or "") if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content) if content else ""


def extract_json_object(text: str, *, strict: bool = True) -> dict[str, Any]:
    """从纯文本、Markdown 代码围栏或带前后缀的模型回复中提取 JSON 对象。

    strict=True 用于必须可信的 Planner/分类输出，解析失败直接抛错并交由上层回退；
    strict=False 用于记忆提取，失败时返回空字典，避免非关键派生数据中断主流程。
    """
    stripped = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    candidate = stripped[start : end + 1] if start >= 0 and end >= start else stripped
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        if strict:
            raise
        return {}
    if isinstance(value, dict):
        return value
    if strict:
        raise ValueError("model returned non-object JSON")
    return {}


def truncate_text(text: str, max_chars: int) -> str:
    """按字符上限裁剪文本，并用省略号明确标识内容并非完整原文。"""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"
