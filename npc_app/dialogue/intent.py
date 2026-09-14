"""NPC 意图识别与确定性 Turn Policy。

本模块先判断玩家在问什么，再把该意图转换成检索开关、来源范围、top_k 和剧情守卫。
LLM 只处理规则无法识别的普通问题，敏感剧情判断始终由模型外代码优先执行。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.services.llm_service import planner_llm
from npc_app.utils import compact_text, contains_any, dialogue_config, extract_json_object, message_text

_DIALOGUE_CONFIG = dialogue_config()
_STORY_SAFETY = _DIALOGUE_CONFIG["story_safety"]
_INTENT_RULES = _DIALOGUE_CONFIG["intent_rules"]
_ALLOWED_SOURCES = _DIALOGUE_CONFIG["retrieval"]["allowed_source_prefixes_by_intent"]
SENSITIVE_STORY_TERMS = tuple(_STORY_SAFETY["sensitive_terms"])
_BROAD_QUESTION_TERMS = set(_STORY_SAFETY["broad_question_terms"])
SENSITIVE_QUESTION_TERMS = tuple(term for term in SENSITIVE_STORY_TERMS if term not in _BROAD_QUESTION_TERMS) + tuple(
    _STORY_SAFETY["sensitive_question_phrases"]
)
STORY_TRUTH_UNLOCK_LEVEL = int(_STORY_SAFETY["truth_unlock_level"])
TERMINAL_NPC_ID = str(_STORY_SAFETY["terminal_npc_id"])


def is_sensitive_question(text: str) -> bool:
    """判断玩家问题是否直接命中敏感剧情词或敏感问法。"""
    return contains_any(text, SENSITIVE_QUESTION_TERMS)


def story_text_allowed(npc_id: str, unlocked_story_level: int, text: str) -> bool:
    """判断一段候选文本能否进入当前 NPC、当前剧情等级的上下文。

    Lab Terminal 或达到真相解锁等级时允许敏感文本；其他角色在早期阶段只要命中任一
    敏感词就整段拒绝，避免局部裁剪后残留可拼接的剧透信息。
    """
    if npc_id == TERMINAL_NPC_ID or unlocked_story_level >= STORY_TRUTH_UNLOCK_LEVEL:
        return True
    return not contains_any(text, SENSITIVE_STORY_TERMS)


class Intent(StrEnum):
    """Runtime 支持的标准意图集合，值会出现在 Trace 和 NDJSON 状态元数据中。"""

    GREETING = "greeting"
    NPC_IDENTITY = "ask_npc_identity"
    NPC_ROLE = "ask_npc_role"
    LOCATION = "ask_location"
    CURRENT_TASK = "ask_current_task"
    ITEM = "ask_item"
    CLUE = "ask_clue"
    LORE = "ask_lore"
    SENSITIVE_TRUTH = "ask_sensitive_truth"
    FOLLOW_UP = "follow_up"
    UNKNOWN = "unknown"


class RiskLevel(StrEnum):
    """意图风险分级；风险越高越需要检索、规划与生成后守卫。"""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# 兼容名称仍被测试和离线评估工具引用，不能只因内部改用枚举就删除。
INTENT_ASK_NPC_IDENTITY = Intent.NPC_IDENTITY.value
INTENT_ASK_CLUE = Intent.CLUE.value
INTENT_ASK_SENSITIVE_TRUTH = Intent.SENSITIVE_TRUTH.value
LOW_RISK_INTENTS = set(_INTENT_RULES["low_risk_intents"])
_INTENT_RISKS = {intent: RiskLevel.LOW for intent in LOW_RISK_INTENTS} | {
    Intent.SENSITIVE_TRUTH.value: RiskLevel.HIGH
}


@dataclass(frozen=True)
class NpcIntentResult:
    """意图分类结果及其可审计来源。

    confidence 主要记录 LLM 分类置信度，规则结果使用稳定默认值；source 区分安全覆盖、
    Unity hint、本地规则、LLM 或失败回退，便于定位错误分类来自哪一层。
    """

    intent: str
    confidence: float = 0.0
    reason: str = ""
    source: str = "rules"

    @property
    def risk_level(self) -> str:
        """根据标准 Intent 派生风险，不采信模型自行声明的风险等级。"""
        return _INTENT_RISKS.get(self.intent, RiskLevel.MEDIUM).value

    def to_dict(self) -> dict[str, Any]:
        """转换为可写入状态事件和 Trace 的普通字典，并附加派生风险。"""
        return {**asdict(self), "risk_level": self.risk_level}


@dataclass(frozen=True)
class NpcTurnPolicy:
    """连接意图识别与后续 Planner/RAG/答案守卫的确定性权限策略。

    context_enabled 控制是否使用历史、记忆和 RAG；allowed_source_prefixes 限制知识来源；
    forbidden_context_terms 在生成前过滤材料；answer_guard_terms 在生成后复查成稿。
    """

    intent: str
    risk_level: str
    context_enabled: bool = True
    retrieval_top_k: int = 5
    allowed_source_prefixes: tuple[str, ...] = ()
    forbidden_context_terms: tuple[str, ...] = SENSITIVE_STORY_TERMS
    answer_guard_terms: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """将 tuple 字段转换为 JSON 友好的 list，供 NDJSON 状态事件展示。"""
        data = asdict(self)
        for key in ("allowed_source_prefixes", "forbidden_context_terms", "answer_guard_terms"):
            data[key] = list(data[key])
        return data


@dataclass(frozen=True)
class _PolicyTemplate:
    """从配置装配 Policy 时使用的不可变模板，不包含单轮剧情状态。"""

    risk: RiskLevel = RiskLevel.MEDIUM
    context_enabled: bool = True
    top_k: int = 5
    prefixes: tuple[str, ...] = ()


# 模块加载时把字符串 Intent 转为枚举，配置错误会在启动导入阶段尽早暴露。
_POLICY_TEMPLATES = {
    Intent(intent): _PolicyTemplate(
        risk=RiskLevel(config["risk"]),
        context_enabled=bool(config["context_enabled"]),
        top_k=int(config["top_k"]),
        prefixes=tuple(_ALLOWED_SOURCES.get(intent, ())),
    )
    for intent, config in _DIALOGUE_CONFIG["intent_policies"].items()
    if not intent.startswith("_")
}


def classify_npc_intent(req: UnityNpcTurnRequest) -> NpcIntentResult:
    """按“敏感规则、可信 hint、本地规则、LLM 兜底”的顺序识别意图。"""
    # 敏感词检查必须先于客户端 hint，防止错误或伪造 hint 绕过剧情安全策略。
    if is_sensitive_question(req.question):
        return _result(Intent.SENSITIVE_TRUTH, "Sensitive story term matched.", "rules_override")
    hinted = _parse_intent(req.intent_hint)
    if hinted:
        return _result(hinted, "Unity intent_hint accepted.", "hint")
    ruled = classify_npc_intent_with_rules(req)
    if ruled.intent != Intent.UNKNOWN.value:
        return ruled
    try:
        return _classify_with_llm(req)
    except Exception as exc:
        return NpcIntentResult(
            ruled.intent,
            ruled.confidence,
            f"LLM classifier failed; deterministic fallback used: {exc}",
            "rules_fallback",
        )


def classify_npc_intent_with_rules(req: UnityNpcTurnRequest) -> NpcIntentResult:
    """按固定优先级执行零模型成本的中文关键词和结构化状态规则。"""
    question = compact_text(req.question)
    asks_identity = not ("我" in question and _contains(question, *_INTENT_RULES["self_identity_terms"])) and _contains(
        question, *_INTENT_RULES["identity_terms"]
    )
    asks_role = ("你" in question or "您" in question) and _contains(question, *_INTENT_RULES["role_terms"])
    simple_greeting = _contains(question, *_INTENT_RULES["greeting_terms"]) and not _contains(
        question, *_INTENT_RULES["greeting_exclusions"]
    )
    # 元组顺序就是匹配优先级：先保护敏感剧情，再处理具体低风险意图，最后才是追问。
    rules = (
        (is_sensitive_question(question), Intent.SENSITIVE_TRUTH, "Sensitive story term matched."),
        (asks_identity, Intent.NPC_IDENTITY, "NPC identity pattern matched."),
        (asks_role, Intent.NPC_ROLE, "NPC role pattern matched."),
        (simple_greeting, Intent.GREETING, "Greeting pattern matched."),
        (_contains(question, *_INTENT_RULES["location_terms"]), Intent.LOCATION, "Location term matched."),
        (_contains(question, *_INTENT_RULES["current_task_terms"]), Intent.CURRENT_TASK, "Task term matched."),
        (
            bool(req.player.presented_items) or _contains(question, *_INTENT_RULES["item_terms"]),
            Intent.ITEM,
            "Item term or presented_items matched.",
        ),
        (
            bool(req.player.mentioned_clues) or _contains(question, *_INTENT_RULES["clue_terms"]),
            Intent.CLUE,
            "Clue term or mentioned_clues matched.",
        ),
        (_contains(question, *_INTENT_RULES["lore_terms"]), Intent.LORE, "Lore term matched."),
        (_contains(question, *_INTENT_RULES["follow_up_terms"]), Intent.FOLLOW_UP, "Follow-up term matched."),
    )
    for matched, intent, reason in rules:
        if matched:
            return _result(intent, reason)
    return _result(Intent.UNKNOWN, "No deterministic pattern matched.")


def build_turn_policy(intent: NpcIntentResult, req: UnityNpcTurnRequest) -> NpcTurnPolicy:
    """把意图转换为确定性的检索范围、上下文开关与答案守卫。"""
    intent_value = _parse_intent(intent.intent) or Intent.UNKNOWN
    template = _POLICY_TEMPLATES[intent_value]
    truth_locked = req.story.unlock_level < STORY_TRUTH_UNLOCK_LEVEL
    always_guarded = intent_value.value in LOW_RISK_INTENTS or intent_value is Intent.LOCATION
    # 即使某些中风险意图允许检索，未解锁真相仍会同时在上下文和最终答案两端受限。
    return NpcTurnPolicy(
        intent=intent_value.value,
        risk_level=template.risk.value,
        context_enabled=template.context_enabled,
        retrieval_top_k=template.top_k,
        allowed_source_prefixes=template.prefixes,
        forbidden_context_terms=()
        if intent_value is Intent.SENSITIVE_TRUTH and not truth_locked
        else SENSITIVE_STORY_TERMS,
        answer_guard_terms=SENSITIVE_STORY_TERMS if truth_locked or always_guarded else (),
    )


def _classify_with_llm(req: UnityNpcTurnRequest) -> NpcIntentResult:
    """把规则未知回合交给 JSON 模式 LLM，并将非法 Intent 收敛为 unknown。"""
    messages = [
        SystemMessage(
            content="Classify one RPG NPC utterance as strict JSON with intent, confidence, and reason. "
            f"Allowed intents: {', '.join(intent.value for intent in Intent)}."
        ),
        HumanMessage(
            content=json.dumps(
                {
                    "npc_id": req.npc_id,
                    "question": req.question,
                    "presented_items": req.player.presented_items,
                    "mentioned_clues": req.player.mentioned_clues,
                    "current_quest": req.player.current_quest,
                },
                ensure_ascii=False,
            )
        ),
    ]
    payload = extract_json_object(message_text(planner_llm.invoke(messages)))
    intent = _parse_intent(str(payload.get("intent", ""))) or Intent.UNKNOWN
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return NpcIntentResult(
        intent.value,
        confidence,
        str(payload.get("reason", "")),
        "llm",
    )


def _parse_intent(value: str | None) -> Intent | None:
    """规范化外部字符串并安全转换为 Intent；非法值返回 None。"""
    try:
        return Intent(value.strip().lower()) if value else None
    except ValueError:
        return None


def _result(intent: Intent, reason: str, source: str = "rules") -> NpcIntentResult:
    """构造格式统一的确定性分类结果。"""
    return NpcIntentResult(intent.value, 0.75, reason, source)


def _contains(text: str, *terms: str) -> bool:
    """执行已由上层 compact_text 处理后的轻量子串匹配。"""
    return any(term in text for term in terms)
