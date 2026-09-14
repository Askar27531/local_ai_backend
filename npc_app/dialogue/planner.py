"""将 Intent Policy 展开为单轮角色策略，并约束可选 LLM Planner。

规则计划先给出上下文和披露硬上限；LLM Planner 只在未知或玩家挑战等复杂回合选择
表达策略，并且只能收紧这些上限，不能获得新的剧情权限。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from npc_app.contracts import MAX_RETRIEVAL_TOP_K, UnityNpcTurnRequest
from npc_app.dialogue.characters import character_dialogue
from npc_app.dialogue.intent import LOW_RISK_INTENTS, Intent, NpcIntentResult, NpcTurnPolicy
from npc_app.services.llm_service import planner_llm
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.utils import dialogue_config, truncate_text

_PLANNER_CONFIG = dialogue_config()["planner"]
_ARC_THRESHOLDS = _PLANNER_CONFIG["arc_thresholds"]


class ArcStage(StrEnum):
    """由剧情等级与信任共同决定的角色关系弧阶段。"""

    GUARDED = "guarded"
    CONFLICTED = "conflicted"
    CONFESSION = "confession"


class DialogueStrategy(StrEnum):
    """本轮台词的表达策略，不代表可访问的剧情权限。"""

    ANSWER_DIRECTLY = "answer_directly"
    GIVE_HINT = "give_hint"
    TELL_PARTIAL_TRUTH = "tell_partial_truth"
    DEFLECT = "deflect"
    CHALLENGE_PLAYER = "challenge_player"
    REFUSE = "refuse"


class PlannerLlmOutput(BaseModel):
    """LLM Planner 的 Schema 约束输出。

    该模型同时作为 with_structured_output 的 JSON Schema 下发给 Ollama，让生成阶段
    直接受语法约束；Pydantic 仍会二次校验枚举与取值范围。输出只用于在规则计划
    上限内收紧表达策略，任何越权字段都不会进入 NpcTurnPlan。
    """

    dialogue_strategy: DialogueStrategy = Field(description="本轮表达策略，必须是 DialogueStrategy 枚举值")
    use_rag: bool = Field(description="是否允许使用世界知识检索；只能关闭，不能开启")
    use_memory: bool = Field(description="是否允许使用长期记忆；只能关闭，不能开启")
    use_dialogue_history: bool = Field(description="是否允许使用近期对话；只能关闭，不能开启")
    retrieval_top_k: int = Field(ge=0, description="检索条数；启用 RAG 时会被限制在规则上限内")
    disclosure_level: int = Field(ge=0, description="披露等级；只能小于等于规则计划给出的上限")
    reason: str = Field(default="", description="选择该策略的简短理由")


@dataclass(frozen=True)
class NpcTurnPlan:
    """一次回合进入检索和 Prompt 编译前的最终执行计划。

    三个 use_* 开关决定允许进入 Context Compiler 的材料类型；retrieval_top_k 与
    disclosure_level 是上限；must_not_reveal 汇总上下文过滤词和答案守卫词；source
    标明计划来自规则、LLM 或失败回退。
    """

    arc_stage: str
    dialogue_strategy: str
    use_rag: bool
    use_memory: bool
    use_dialogue_history: bool
    retrieval_top_k: int
    disclosure_level: int
    must_not_reveal: tuple[str, ...]
    source: str = "rules"
    reason: str = ""

    def __post_init__(self) -> None:
        """拒绝 RAG 开关与检索数量不一致的计划。"""
        DialogueStrategy(self.dialogue_strategy)
        valid_top_k = 1 <= self.retrieval_top_k <= MAX_RETRIEVAL_TOP_K if self.use_rag else self.retrieval_top_k == 0
        if not valid_top_k:
            raise ValueError(
                f"retrieval_top_k must be 1-{MAX_RETRIEVAL_TOP_K} when RAG is enabled, or 0 when disabled"
            )

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 友好的状态事件数据。"""
        data = asdict(self)
        data["must_not_reveal"] = list(self.must_not_reveal)
        return data


_PLAYER_CHALLENGE_TERMS = tuple(_PLANNER_CONFIG["challenge_terms"])


def resolve_arc_stage(req: UnityNpcTurnRequest) -> ArcStage:
    """只根据 Unity 权威状态保守计算角色阶段，不采信玩家自然语言自报进度。"""
    confession = _ARC_THRESHOLDS["confession"]
    conflicted = _ARC_THRESHOLDS["conflicted"]
    if req.story.unlock_level >= confession["story_unlock"] and req.relationship.trust >= confession["trust"]:
        return ArcStage.CONFESSION
    if req.story.unlock_level >= conflicted["story_unlock"] and req.relationship.trust >= conflicted["trust"]:
        return ArcStage.CONFLICTED
    return ArcStage.GUARDED


def should_use_llm_planner(intent: NpcIntentResult, req: UnityNpcTurnRequest) -> bool:
    """仅让含糊或带挑战性的回合使用 LLM Planner，常规回合保持确定性。"""
    if intent.intent in LOW_RISK_INTENTS:
        return False
    challenged = any(term in req.question for term in _PLAYER_CHALLENGE_TERMS)
    return challenged or (intent.intent == Intent.UNKNOWN.value and intent.source == "rules")


def build_turn_plan(
    intent: NpcIntentResult,
    policy: NpcTurnPolicy,
    req: UnityNpcTurnRequest,
    dialogue_history: list[tuple[str, str]],
    memory_items: list[RetrievedMemoryItem],
) -> NpcTurnPlan:
    """先建立规则上限，再允许 LLM 在该上限内收紧本轮表达策略。"""
    fallback = plan_from_policy(intent, policy, req)
    if not should_use_llm_planner(intent, req):
        return fallback
    try:
        output = _invoke_planner(intent, policy, req, dialogue_history, memory_items)
        return _validated_llm_plan(output, fallback)
    except Exception as exc:
        # Planner 不可用或输出非法时退回规则计划，不让辅助模型故障中断主对话。
        return replace(
            fallback,
            source="fallback",
            reason=f"Planner failed; policy fallback used: {exc}",
        )


def plan_from_policy(intent: NpcIntentResult, policy: NpcTurnPolicy, req: UnityNpcTurnRequest) -> NpcTurnPlan:
    """由剧情阶段和 Intent Policy 生成不可越权的确定性基线计划。"""
    stage = resolve_arc_stage(req)
    character_stage = character_dialogue(req.npc_id).arc_stages[stage.value]
    strategy = _rule_strategy(intent.intent, stage, req)
    if strategy.value not in character_stage.preferred_strategies and intent.intent == Intent.SENSITIVE_TRUTH.value:
        strategy = DialogueStrategy(character_stage.preferred_strategies[0])
    disclosure = min(character_stage.disclosure_level, _story_disclosure_cap(req.story.unlock_level))
    return NpcTurnPlan(
        arc_stage=stage.value,
        dialogue_strategy=strategy.value,
        use_rag=policy.context_enabled,
        use_memory=policy.context_enabled,
        use_dialogue_history=policy.context_enabled,
        retrieval_top_k=policy.retrieval_top_k,
        disclosure_level=disclosure,
        must_not_reveal=tuple(dict.fromkeys((*policy.forbidden_context_terms, *policy.answer_guard_terms))),
        source="rules",
        reason=f"Deterministic plan for {policy.intent} at {stage.value} stage.",
    )


def _rule_strategy(intent: str, stage: ArcStage, req: UnityNpcTurnRequest) -> DialogueStrategy:
    """根据意图、角色阶段和挑战语气选择确定性表达策略。"""
    if intent == Intent.SENSITIVE_TRUTH.value:
        if stage is ArcStage.CONFESSION:
            return DialogueStrategy.TELL_PARTIAL_TRUTH
        return DialogueStrategy.REFUSE if stage is ArcStage.GUARDED else DialogueStrategy.GIVE_HINT
    if any(term in req.question for term in _PLAYER_CHALLENGE_TERMS):
        return DialogueStrategy.CHALLENGE_PLAYER
    if intent in {Intent.CLUE.value, Intent.CURRENT_TASK.value}:
        return DialogueStrategy.GIVE_HINT
    if intent == Intent.FOLLOW_UP.value and stage is ArcStage.GUARDED:
        return DialogueStrategy.DEFLECT
    return DialogueStrategy.ANSWER_DIRECTLY


def _invoke_planner(
    intent: NpcIntentResult,
    policy: NpcTurnPolicy,
    req: UnityNpcTurnRequest,
    dialogue_history: list[tuple[str, str]],
    memory_items: list[RetrievedMemoryItem],
) -> PlannerLlmOutput:
    """用受限上下文调用 Schema 约束的 JSON Planner，返回未获信任的候选决策。

    通过 with_structured_output(json_schema) 让 Ollama 按 PlannerLlmOutput 的 JSON
    Schema 约束生成；解析失败或字段非法由调用方回退到规则计划。
    """
    stage = resolve_arc_stage(req)
    allowed_strategies = ", ".join(strategy.value for strategy in DialogueStrategy)
    # 只发送最近两轮和前三条截断记忆，Planner 无需也不应看到完整世界知识或长期历史。
    payload = {
        "npc_id": req.npc_id,
        "question": req.question,
        "intent": intent.intent,
        "risk_level": policy.risk_level,
        "arc_stage": stage.value,
        "story_unlock": req.story.unlock_level,
        "trust": req.relationship.trust,
        "recent_dialogue": dialogue_history[-2:],
        "memory_hints": [truncate_text(item.content, 100) for item in memory_items[:3]],
    }
    messages = [
        SystemMessage(
            content=(
                "Plan one RPG NPC dialogue turn. Return JSON matching the provided schema. "
                f"dialogue_strategy must be one of: {allowed_strategies}. "
                "You may reduce context usage and disclosure, never expand story access."
            )
        ),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]
    structured = planner_llm.with_structured_output(PlannerLlmOutput, method="json_schema")
    return structured.invoke(messages)


def _validated_llm_plan(output: PlannerLlmOutput, fallback: NpcTurnPlan) -> NpcTurnPlan:
    """把 Schema 校验后的 Planner 输出合并进规则计划，并逐字段执行“只能收紧”校验。"""
    use_rag = fallback.use_rag and output.use_rag
    use_memory = fallback.use_memory and output.use_memory
    use_dialogue_history = fallback.use_dialogue_history and output.use_dialogue_history
    retrieval_top_k = min(max(output.retrieval_top_k, 1), fallback.retrieval_top_k) if use_rag else 0
    disclosure_level = min(max(output.disclosure_level, 0), fallback.disclosure_level)
    return NpcTurnPlan(
        arc_stage=fallback.arc_stage,
        dialogue_strategy=output.dialogue_strategy.value,
        use_rag=use_rag,
        use_memory=use_memory,
        use_dialogue_history=use_dialogue_history,
        retrieval_top_k=retrieval_top_k,
        disclosure_level=disclosure_level,
        must_not_reveal=fallback.must_not_reveal,
        source="llm",
        reason=truncate_text(output.reason or "LLM dialogue strategy selected.", 240),
    )


def _story_disclosure_cap(unlock_level: int) -> int:
    """把 Unity 剧情等级映射为全局披露上限，再与角色阶段上限取最小值。"""
    if unlock_level >= _ARC_THRESHOLDS["confession"]["story_unlock"]:
        return 2
    if unlock_level >= _ARC_THRESHOLDS["conflicted"]["story_unlock"]:
        return 1
    return 0
