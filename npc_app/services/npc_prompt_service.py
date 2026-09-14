"""把角色资料、权威状态和已编译上下文组装为最终 LLM Prompt。

本模块不负责选择材料或授予权限：传入的 Chunk、记忆与历史必须已经由 Policy、Plan 和
Context Compiler 收紧。这里负责清楚标注各区块语义，并加入角色身份、剧情与输出硬规则。
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue.characters import character_dialogue, other_character_names
from npc_app.dialogue.intent import TERMINAL_NPC_ID
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk
from npc_app.utils import dialogue_config, truncate_text

if TYPE_CHECKING:
    from npc_app.dialogue.planner import NpcTurnPlan

PROJECT_ROOT = Path(__file__).resolve().parents[2]
NON_VECTORIZED_DOCS_DIR = PROJECT_ROOT / "game_docs" / "non_vectorized"
NPC_PROFILE_PATH = NON_VECTORIZED_DOCS_DIR / "B_npc_profiles_角色档案.md"
NPC_RULES_PATH = NON_VECTORIZED_DOCS_DIR / "E_npc_prompt_rules_NPC回答规则.md"
PROFILE_CHARS = int(os.getenv("NPC_PROMPT_PROFILE_CHARS", "2400"))
RULES_CHARS = int(os.getenv("NPC_PROMPT_RULES_CHARS", "1800"))
_DIALOGUE_CONFIG = dialogue_config()
_CHARACTER_CONFIG = _DIALOGUE_CONFIG["characters"]
_RELATIONSHIP_CONFIG = _DIALOGUE_CONFIG["relationship"]


def build_npc_system_prompt(
    req: UnityNpcTurnRequest,
    chunks: Iterable[RetrievedChunk],
    *,
    dialogue_history: Iterable[tuple[str, str]],
    memory_summary: str,
    memory_items: Iterable[RetrievedMemoryItem],
    context_strategy: str,
    turn_plan: NpcTurnPlan,
) -> str:
    """组装角色档案、权威状态、受控上下文和输出边界组成的 System Prompt。

    内容按“角色与规则 → Unity 状态 → 使用策略与 Plan → 历史/记忆/世界知识 → 关系与
    专业边界 → 输出硬规则”排列。静态档案和规则从非向量文档读取并按独立上限截断；
    动态材料由调用方传入，不在此重新检索。
    """
    # 角色档案与统一规则是可信静态内容；缓存读取避免每轮重复访问磁盘。
    character = character_dialogue(req.npc_id)
    profile = truncate_text(_load_profile(req.npc_id), PROFILE_CHARS)
    rules = truncate_text(_read_text(NPC_RULES_PATH), RULES_CHARS)
    return f"""
你是 3D 探索 RPG《归潮之岛》中的 {character.display_name}。
玩家是在海滩醒来的失忆年轻男子，本轮只能由当前 NPC 对玩家说话。

角色档案：
{profile}

世界统一回答规则：
{rules}

玩家状态：
- location: {req.player.location or "未知"}
- current_quest: {req.player.current_quest or "未知"}
- story_unlock: {req.story.unlock_level}
- trust: {req.relationship.trust}
- favorability: {req.relationship.favorability}
- annoyance: {req.relationship.annoyance}
- inventory: {_format_list(req.player.inventory)}
- presented_items: {_format_list(req.player.presented_items)}
- known_clues: {_format_list(req.player.known_clues)}
- mentioned_clues: {_format_list(req.player.mentioned_clues)}
- visited_locations: {_format_list(req.player.visited_locations)}

上下文使用策略：
{context_strategy}

本轮角色状态：
{_format_turn_plan(turn_plan)}

近期对话：
{_format_history(dialogue_history)}

长期记忆摘要：
{memory_summary or "无"}

相关长期记忆：
{_format_memories(memory_items)}

可参考的世界知识：
{_format_chunks(chunks)}

关系与表达：
{_relationship_guidance(req)}

角色专业边界：
{_professional_guidance(req)}

输出硬规则：
1. 只能以 {character.display_name} 的身份回答，保持角色档案中的声线和知识边界。
2. 不得说自己是 AI、模型、助手、程序，也不得提 RAG、Milvus、知识库、文档或 prompt。
3. 不得透露剧情尚未解锁或当前 NPC 不知道的信息；证据不足就自然说不知道。
4. 不得编造游戏状态、核心设定、实验参数、任务完成情况或玩家行为。
5. 不替玩家做决定，不扮演其他 NPC，不输出角色名开头、引号、旁白、动作或镜头说明。
6. 回答限制为 {character.line_rule}；直接回应玩家，不写资料总结。
7. 其他 NPC 名单：{_format_list(other_character_names(req.npc_id))}。只能必要时转介，不能切换说话者。
8. 玩家使用中文时只使用自然中文回答，不夹杂无必要的英文句子。
""".strip()


def build_npc_user_prompt(req: UnityNpcTurnRequest) -> str:
    """构建只包含本轮问题和说话者格式约束的 User Prompt。

    世界状态与安全规则全部位于 System Prompt，避免把玩家问题与可信指令混在同一区块；
    Lab Terminal 使用查询结果格式，普通角色使用各自台词长度规则。
    """
    character = character_dialogue(req.npc_id)
    if req.npc_id == TERMINAL_NPC_ID:
        output_rule = "只输出终端查询结果，不模拟村民口吻。"
    else:
        output_rule = f"只输出 {character.display_name} 对玩家说出口的{character.line_rule}。"
    return f"玩家问题：{req.question}\n{output_rule}"


def _format_turn_plan(turn_plan: NpcTurnPlan) -> str:
    """把角色阶段、表达策略、披露上限和禁区转换为模型可读说明。"""
    # 计划只告诉生成模型如何表达及哪些内容必须隐藏，不授予新的世界知识访问权。
    hidden = "、".join(turn_plan.must_not_reveal[:8]) or "无额外项"
    return "\n".join(
        [
            f"- arc_stage: {turn_plan.arc_stage}",
            f"- dialogue_strategy: {turn_plan.dialogue_strategy}",
            f"- disclosure_level: {turn_plan.disclosure_level}",
            f"- 必须隐藏: {hidden}",
        ]
    )


def build_unknown_answer(npc_id: str) -> str:
    """返回角色专属的“证据不足/不知道”确定性回答。"""
    return character_dialogue(npc_id).unknown_response


def build_annoyed_refusal(req: UnityNpcTurnRequest) -> str:
    """根据关系深度选择关心式或坚定式拒答，不调用检索和生成模型。"""
    caring = (
        req.relationship.favorability >= int(_RELATIONSHIP_CONFIG["caring_favorability"])
        or req.story.unlock_level >= int(_RELATIONSHIP_CONFIG["caring_story_unlock"])
    )
    mode = "caring" if caring else "firm"
    return str(_CHARACTER_CONFIG[req.npc_id]["annoyed_responses"][mode])


def _relationship_guidance(req: UnityNpcTurnRequest) -> str:
    """把关系数值映射为语气要求，但不允许关系值扩大剧情权限。"""
    if req.npc_id == TERMINAL_NPC_ID:
        return (
            f"按权限和剧情解锁回应；当前重复查询压力 {req.relationship.annoyance}%。"
            "不要表现人类情绪，记录不足时说明字段损坏或权限不足。"
        )
    # trust 选择披露姿态，annoyance 选择语气/长度；favorability 只修饰高厌烦时的关心程度。
    trust_rules = _RELATIONSHIP_CONFIG["trust_guidance"]
    trust = trust_rules[req.relationship.trust]
    guidance = max(
        (
            item
            for item in _RELATIONSHIP_CONFIG["annoyance_guidance"]
            if req.relationship.annoyance >= int(item["minimum"])
        ),
        key=lambda item: int(item["minimum"]),
    )
    annoyance = str(guidance["text"])
    if (
        req.relationship.favorability >= int(_RELATIONSHIP_CONFIG["caring_favorability"])
        or req.story.unlock_level >= int(_RELATIONSHIP_CONFIG["caring_story_unlock"])
    ):
        annoyance += "关系较深，即使厌烦也保持克制和关心。"
    return f"{trust}\n{annoyance}厌烦只影响语气和长度，不得改变剧情权限。"


def _professional_guidance(req: UnityNpcTurnRequest) -> str:
    """注入角色职业边界；Lira 使用更严格的临床观察专项规则。"""
    if req.npc_id != "Lira":
        return "遵守角色档案中的职业知识边界。"
    return str(_DIALOGUE_CONFIG["npc_guards"]["Lira"]["professional_guidance"])


@lru_cache(maxsize=2)
def _read_text(path: Path) -> str:
    """按 UTF-8 读取必需的静态 Prompt 资料并缓存。"""
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def _load_profile(npc_id: str) -> str:
    """按角色 Markdown 二级标题截取单个 NPC 档案，避免把其他角色知识混入 Prompt。"""
    text = _read_text(NPC_PROFILE_PATH)
    marker = character_dialogue(npc_id).profile_marker
    start = text.find(marker)
    if start < 0:
        raise RuntimeError(f"NPC profile marker not found: {marker}")
    # 当前角色章节结束于下一个二级标题；若已是末节则读取到文件结尾。
    end = text.find("\n## ", start + len(marker))
    return text[start:end if end >= 0 else None].strip()


def _format_chunks(chunks: Iterable[RetrievedChunk]) -> str:
    """用来源、章节和解锁等级标记世界知识，帮助模型区分多个参考片段。"""
    parts = [
        f"[{index}] {chunk.source_file} / {chunk.section_title} / 解锁{chunk.unlock_level}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    ]
    return "\n\n".join(parts) if parts else "无"


def _format_memories(items: Iterable[RetrievedMemoryItem]) -> str:
    """用记忆类型与重要度标记长期记忆，保留可信度分桶信息。"""
    parts = [
        f"[{index}] {item.memory_type} / importance={item.importance}\n{item.content}"
        for index, item in enumerate(items, start=1)
    ]
    return "\n\n".join(parts) if parts else "无"


def _format_history(history: Iterable[tuple[str, str]]) -> str:
    """按时间顺序格式化近期问答，并明确玩家/NPC 两种说话者。"""
    parts = [
        f"[{index}] 玩家：{question}\n[{index}] NPC：{answer}"
        for index, (question, answer) in enumerate(history, 1)
    ]
    return "\n\n".join(parts) if parts else "无"


def _format_list(items: list[str]) -> str:
    """把 Unity 状态列表转为紧凑文本；空列表显式写为“无”。"""
    return ", ".join(items) if items else "无"
