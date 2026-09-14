"""把角色配置装配为对话流程可直接消费的只读结构。"""

from __future__ import annotations

from dataclasses import dataclass, field

from npc_app.dialogue.intent import Intent
from npc_app.utils import dialogue_config

_CHARACTER_CONFIG = dialogue_config()["characters"]
_DEFAULT_ARC_STAGE_CONFIG = _CHARACTER_CONFIG["_default_arc_stages"]


@dataclass(frozen=True)
class CharacterArcStage:
    """角色在某个剧情阶段的表达偏好。

    tone 描述声线变化，preferred_strategies 限制敏感回合的首选策略，disclosure_level
    是角色维度的披露上限；最终上限还会与全局剧情解锁等级取较小值。
    """

    tone: str
    preferred_strategies: tuple[str, ...]
    disclosure_level: int


def _default_arc_stages() -> dict[str, CharacterArcStage]:
    """把共享 JSON 阶段配置转换为强类型对象，供未单独配置阶段的角色复用。"""
    return {
        stage: CharacterArcStage(
            tone=str(config["tone"]),
            preferred_strategies=tuple(config["preferred_strategies"]),
            disclosure_level=int(config["disclosure_level"]),
        )
        for stage, config in _DEFAULT_ARC_STAGE_CONFIG.items()
    }


@dataclass(frozen=True)
class CharacterDialogue:
    """单个 NPC 的静态对话配置。

    profile_marker 用于从角色档案中定位正文，retrieval_terms 扩展世界知识查询，
    basic_responses 承载无需 RAG/LLM 的低风险固定回答，line_rule 控制最终台词长度与形式。
    """

    display_name: str
    profile_marker: str
    line_rule: str
    retrieval_terms: str
    basic_responses: dict[Intent, str] = field(default_factory=dict)
    unknown_response: str = "我不知道。"
    arc_stages: dict[str, CharacterArcStage] = field(default_factory=_default_arc_stages)


# 模块加载时完成一次配置装配；以下划线开头的条目是共享模板，不是可对话 NPC。
CHARACTERS = {
    npc_id: CharacterDialogue(
        display_name=str(config["display_name"]),
        profile_marker=str(config["profile_marker"]),
        line_rule=str(config["line_rule"]),
        retrieval_terms=str(config["retrieval_terms"]),
        basic_responses={Intent(intent): str(answer) for intent, answer in config["basic_responses"].items()},
        unknown_response=str(config["unknown_response"]),
    )
    for npc_id, config in _CHARACTER_CONFIG.items()
    if not npc_id.startswith("_")
}


def character_dialogue(npc_id: str) -> CharacterDialogue:
    """返回经过启动配置校验的角色配置。"""
    return CHARACTERS[npc_id]


def other_character_names(npc_id: str) -> list[str]:
    """列出当前 NPC 之外的显示名，供 Prompt 明确禁止切换说话者。"""
    return [config.display_name for key, config in CHARACTERS.items() if key != npc_id]


def basic_response(npc_id: str, intent: str) -> str | None:
    """查询低风险固定回答；未知 Intent 或未配置回答时返回 None 进入完整流程。"""
    try:
        intent_value = Intent(intent)
    except ValueError:
        return None
    return character_dialogue(npc_id).basic_responses.get(intent_value)
