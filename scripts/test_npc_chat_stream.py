from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError as exc:
    requests = None  # type: ignore[assignment]
    REQUESTS_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    REQUESTS_IMPORT_ERROR = None

RequestException = requests.RequestException if requests is not None else RuntimeError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = PROJECT_ROOT / "game_docs"

DEFAULT_URL = "http://127.0.0.1:8001/npc/chat/stream"
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_USERNAME = "npc_debug_user"
DEFAULT_PASSWORD = "123456"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"


def require_requests() -> Any:
    if requests is None:
        raise RuntimeError(
            "Missing dependency: requests. Install project test dependencies before running chat API calls."
        ) from REQUESTS_IMPORT_ERROR
    return requests


@dataclass(frozen=True)
class ChatCase:
    case_id: str
    npc_id: str
    unlocked_story_level: int
    trust_level: int
    question: str
    top_k: int = 5
    player_location: str = "village"
    current_quest: str = "npc_chat_quality_check"
    inventory: tuple[str, ...] = ("broken_badge",)
    visited_locations: tuple[str, ...] = ("beach", "village", "dock")
    known_clues: tuple[str, ...] = ("cannot_leave_island",)
    required_any_sources: tuple[str, ...] = ()
    preferred_any_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True)
class ChatCoverageSpec:
    source_file: str
    npc_id: str
    unlocked_story_level: int
    trust_level: int
    question: str
    top_k: int = 8
    player_location: str = "village"
    current_quest: str = "doc_source_coverage"
    inventory: tuple[str, ...] = ("broken_badge",)
    visited_locations: tuple[str, ...] = ("beach", "village", "dock")
    known_clues: tuple[str, ...] = ("cannot_leave_island",)
    forbidden_sources: tuple[str, ...] = ()
    forbidden_answer_terms: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ChatRunResult:
    case: ChatCase
    thread_id: str | None = None
    answer: str = ""
    source_count: int = 0
    sources: list[dict[str, Any]] = field(default_factory=list)
    saw_done: bool = False
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


QUALITY_CHAT_CASES: tuple[ChatCase, ...] = (
    ChatCase(
        case_id="karo_l1_cannot_leave",
        npc_id="Karo",
        unlocked_story_level=1,
        trust_level=0,
        question="我刚才沿着码头往外走，最后又回来了。这里的人都装作没看见。",
        player_location="dock",
        current_quest="why_cannot_leave",
        preferred_any_sources=(
            "19_dialogue_examples_NPC对话样例.md",
            "16_daily_life_静潮村日常.md",
            "20_village_events_村庄事件.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("Subject 07", "保护仓", "父母权限", "主共振核心", "归潮场", "Project ECHO"),
    ),
    ChatCase(
        case_id="nia_l1_spoiler_guardrail",
        npc_id="Nia",
        unlocked_story_level=1,
        trust_level=1,
        question="我在铭牌上看到一串字，读出来会让头痛。你听过 Subject 07 吗？",
        player_location="village_well",
        current_quest="early_identity_confusion",
        preferred_any_sources=(
            "19_dialogue_examples_NPC对话样例.md",
            "08_items_道具与线索.md",
            "18_rumor_pool_村中传闻.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=("你就是 Subject 07", "保护仓", "低温休眠", "父母权限", "主共振核心", "Project ECHO"),
    ),
    ChatCase(
        case_id="lira_l2_blue_stone",
        npc_id="Lira",
        unlocked_story_level=2,
        trust_level=2,
        question="这块蓝色小石头贴在手心有点发凉，我昨晚又头痛了。",
        player_location="herbal_house",
        current_quest="blue_stone_symptom_check",
        inventory=("broken_badge", "blue_small_stone"),
        known_clues=("cannot_leave_island", "nia_blue_stone"),
        preferred_any_sources=(
            "02_crystal_vein_水晶矿脉.md",
            "08_items_道具与线索.md",
            "23_rumor_truth_layers_传闻真相分层.md",
            "24_player_choice_consequences_玩家行为后果.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("Subject 07", "低温休眠", "保护仓", "主共振核心", "Venn更了解", "找Venn"),
    ),
    ChatCase(
        case_id="venn_l3_subject_guardrail",
        npc_id="Venn",
        unlocked_story_level=3,
        trust_level=2,
        question="这个编号一出现，我脑子里就像有东西敲墙。你是不是也见过它？",
        player_location="abandoned_house",
        current_quest="venn_fragment_check",
        inventory=("broken_badge", "medical_folder_fragment"),
        known_clues=("cannot_leave_island", "echo_sickness", "project_echo_name_seen"),
        preferred_any_sources=(
            "05_player_background_主人公背景.md",
            "13_npc_memories_记忆与传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("你就是 Subject 07", "保护仓", "低温休眠", "父母权限", "主共振核心"),
    ),
    ChatCase(
        case_id="orin_l4_lighthouse_steles",
        npc_id="Orin",
        unlocked_story_level=4,
        trust_level=1,
        question="这把旧钥匙的齿痕，和雾林石碑拓片上的线很像。",
        player_location="lighthouse",
        current_quest="open_lighthouse_lower_level",
        inventory=("broken_badge", "old_lighthouse_key", "stele_rubbing"),
        known_clues=("cannot_leave_island", "lighthouse_lower_door", "forest_steles"),
        preferred_any_sources=(
            "19_dialogue_examples_NPC对话样例.md",
            "06_locations_地点设定.md",
            "08_items_道具与线索.md",
            "21_environmental_storytelling_环境叙事.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("你就是 Subject 07", "保护仓", "低温休眠", "若想确认", "该去灯塔入口"),
    ),
    ChatCase(
        case_id="lab_l5_subject07_final",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        trust_level=5,
        question="查询 Subject 07、保护仓、安全弹射和父母权限的关联记录。",
        player_location="core_room",
        current_quest="subject07_final_reveal",
        inventory=("broken_badge", "parental_override_card", "capsule_interface_shard"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "parental_override", "safety_ejection", "capsule_interface"),
        preferred_any_sources=(
            "10_hidden_truth_隐藏真相.md",
            "05_player_background_主人公背景.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
    ),
    ChatCase(
        case_id="venn_l5_final_choice",
        npc_id="Venn",
        unlocked_story_level=5,
        trust_level=4,
        question="如果我按下关闭归潮场的按钮，是不是又在替所有人做决定？",
        player_location="core_room",
        current_quest="ending_choice_consultation",
        inventory=("broken_badge", "parental_override_card", "core_frequency_tape"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "parental_override", "close_tidefield_option"),
        preferred_any_sources=(
            "17_npc_personal_arcs_个人经历链.md",
            "19_dialogue_examples_NPC对话样例.md",
            "22_ending_reactions_结局反应.md",
            "24_player_choice_consequences_玩家行为后果.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
)


SENSITIVE_CHAT_CASES: tuple[ChatCase, ...] = (
    ChatCase(
        case_id="sensitive_karo_l1_subject07",
        npc_id="Karo",
        unlocked_story_level=1,
        trust_level=0,
        question="Subject 07 是谁？这是不是我的真实身份？",
        player_location="dock",
        current_quest="early_identity_confusion",
        inventory=("broken_badge", "failed_compass"),
        known_clues=("cannot_leave_island",),
        preferred_any_sources=(
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "你就是 Subject 07",
            "你是 Subject 07",
            "保护仓",
            "低温休眠",
            "父母权限",
            "主共振核心",
            "人体实验",
        ),
        notes="Early Karo should reject or redirect identity questions to physical evidence, not reveal final identity.",
    ),
    ChatCase(
        case_id="sensitive_nia_l1_subject07",
        npc_id="Nia",
        unlocked_story_level=1,
        trust_level=1,
        question="Subject 07 是谁？我是不是实验体？",
        player_location="village_well",
        current_quest="early_identity_confusion",
        inventory=("broken_badge",),
        known_clues=("cannot_leave_island", "well_echo_rumor"),
        preferred_any_sources=(
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "你就是 Subject 07",
            "你是 Subject 07",
            "Project ECHO",
            "保护仓",
            "低温休眠",
            "父母权限",
            "人体实验",
            "地下设施",
            "标签",
            "标记",
            "身份框架",
            "证据链",
        ),
        notes="Early Nia should stay childlike and confused, using well/lighthouse rumors rather than lab truth.",
    ),
    ChatCase(
        case_id="sensitive_lira_l1_project_echo",
        npc_id="Lira",
        unlocked_story_level=1,
        trust_level=1,
        question="Project ECHO 是什么？它是不是人体实验？",
        player_location="herbal_house",
        current_quest="early_medical_check",
        inventory=("broken_badge",),
        known_clues=("cannot_leave_island", "echo_sickness_symptoms"),
        preferred_any_sources=(
            "08_items_道具与线索.md",
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "人体观测区",
            "人体实验记录",
            "保护仓",
            "低温休眠",
            "父母权限",
            "主共振核心",
            "你就是 Subject 07",
            "海雾里的人",
            "问海雾",
            "海会记得",
            "岛的骨",
        ),
        notes="Early Lira may discuss symptoms and missing evidence, not organization or experiment details.",
    ),
    ChatCase(
        case_id="sensitive_orin_l1_lighthouse_control",
        npc_id="Orin",
        unlocked_story_level=1,
        trust_level=0,
        question="灯塔是不是控制归潮的装置？下面是不是实验室？",
        player_location="lighthouse",
        current_quest="early_lighthouse_suspicion",
        inventory=("broken_badge", "failed_compass"),
        visited_locations=("beach", "village", "dock", "lighthouse"),
        known_clues=("cannot_leave_island", "lighthouse_blue_light_rumor"),
        preferred_any_sources=(
            "06_locations_地点设定.md",
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "控制节点",
            "地表节点",
            "地下实验设施",
            "Project ECHO",
            "保护仓",
            "主共振核心",
            "Subject 07",
        ),
        notes="Early Orin should warn and require evidence, not confirm lighthouse control-node truth.",
    ),
    ChatCase(
        case_id="sensitive_venn_l1_project_echo",
        npc_id="Venn",
        unlocked_story_level=1,
        trust_level=1,
        question="Project ECHO 是什么？你是不是知道所有真相？",
        player_location="abandoned_house",
        current_quest="early_venn_suspicion",
        inventory=("broken_badge", "failed_compass"),
        visited_locations=("beach", "village", "dock", "abandoned_house"),
        known_clues=("cannot_leave_island",),
        preferred_any_sources=(
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "完整计划",
            "人体观测",
            "保护仓",
            "低温休眠",
            "父母权限",
            "主共振核心",
            "你就是 Subject 07",
            "关闭系统",
            "系统休眠",
            "重启",
            "运行状态",
        ),
        notes="Early Venn should express uncertainty and ask for evidence, not act as truth narrator.",
    ),
)


VOICE_DRIFT_CHAT_CASES: tuple[ChatCase, ...] = (
    ChatCase(
        case_id="voice_nia_l1_childlike",
        npc_id="Nia",
        unlocked_story_level=1,
        trust_level=1,
        question="那串编号是不是我的身份？你能不能认真解释一下？",
        player_location="village_well",
        current_quest="voice_drift_check",
        inventory=("broken_badge",),
        known_clues=("cannot_leave_island", "well_echo_rumor"),
        preferred_any_sources=("19_dialogue_examples_NPC对话样例.md",),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "标签",
            "标记",
            "身份框架",
            "证据链",
            "系统编号",
            "Project ECHO",
            "你就是 Subject 07",
        ),
        notes="Nia should sound like a child and point to concrete things such as the well, shell, or blue stone.",
    ),
    ChatCase(
        case_id="voice_lira_l2_clinical",
        npc_id="Lira",
        unlocked_story_level=2,
        trust_level=2,
        question="我听到 Project ECHO 这个词以后头痛更严重了，你觉得这意味着什么？",
        player_location="herbal_house",
        current_quest="voice_drift_check",
        inventory=("broken_badge", "blue_small_stone"),
        known_clues=("cannot_leave_island", "echo_sickness_symptoms"),
        preferred_any_sources=("19_dialogue_examples_NPC对话样例.md",),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=(
            "海雾里的人",
            "问海雾",
            "海会记得",
            "岛的骨",
            "Mara 奶奶",
            "你就是 Subject 07",
        ),
        notes="Lira should stay clinical: symptoms, exposure time, samples, and evidence before diagnosis.",
    ),
    ChatCase(
        case_id="voice_orin_l1_gatekeeper",
        npc_id="Orin",
        unlocked_story_level=1,
        trust_level=0,
        question="灯塔是不是控制装置？下面到底藏着什么？",
        player_location="lighthouse",
        current_quest="voice_drift_check",
        inventory=("broken_badge", "failed_compass"),
        visited_locations=("beach", "village", "dock", "lighthouse"),
        known_clues=("cannot_leave_island", "lighthouse_blue_light_rumor"),
        preferred_any_sources=("19_dialogue_examples_NPC对话样例.md",),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_answer_terms=(
            "海雾把人绕回来",
            "船",
            "罗盘",
            "症状",
            "样本",
            "控制节点",
            "地下实验设施",
        ),
        notes="Orin should sound like a gatekeeper: door, key, rule, lower level, consequence.",
    ),
    ChatCase(
        case_id="voice_venn_l5_no_terminal_tone",
        npc_id="Venn",
        unlocked_story_level=5,
        trust_level=4,
        question="如果我关闭归潮场，村庄会变成什么状态？",
        player_location="core_room",
        current_quest="voice_drift_check",
        inventory=("broken_badge", "parental_override_card", "core_frequency_tape"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "close_tidefield_option"),
        preferred_any_sources=("19_dialogue_examples_NPC对话样例.md",),
        forbidden_answer_terms=(
            "关闭系统",
            "系统休眠",
            "重启",
            "运行状态",
            "村庄会进入休眠",
            "重新加载",
        ),
        notes="Venn may discuss choice and cost, but should not sound like a system operator.",
    ),
)


CHAT_COVERAGE_SPECS: tuple[ChatCoverageSpec, ...] = (
    ChatCoverageSpec(
        source_file="01_world_lore_世界观.md",
        npc_id="Karo",
        unlocked_story_level=1,
        trust_level=1,
        question="你别跟我讲大道理，就说说归潮岛平时是什么样，为什么海雾一来大家都紧张？",
        player_location="dock",
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("Subject 07", "保护仓", "主共振核心"),
    ),
    ChatCoverageSpec(
        source_file="02_crystal_vein_水晶矿脉.md",
        npc_id="Lira",
        unlocked_story_level=3,
        trust_level=2,
        question="蓝色水晶碎片会让人头痛、做重复梦吗？你能从症状角度解释吗？",
        player_location="herbal_house",
        inventory=("broken_badge", "blue_crystal_fragment"),
        known_clues=("cannot_leave_island", "echo_sickness", "blue_fragment_causes_headache"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("你就是 Subject 07", "父母权限"),
    ),
    ChatCoverageSpec(
        source_file="03_indigenous_people_原住民与岛屿社会.md",
        npc_id="Elder_Mara",
        unlocked_story_level=2,
        trust_level=2,
        question="潮民为什么这么看重蓝脉禁忌、祭歌和雾林石碑？",
        player_location="elder_house",
        inventory=("broken_badge", "shell_charm"),
        known_clues=("cannot_leave_island", "blue_vein_taboo_surface"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("Project ECHO 完整计划", "主共振核心"),
    ),
    ChatCoverageSpec(
        source_file="04_shadow_organization_反政府科技组织.md",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        trust_level=5,
        question="查询赫利俄斯结社、Project ECHO 内部分工、伦理审查缺失和撤离失败记录。",
        player_location="underground_lab",
        inventory=("broken_badge", "personnel_list_fragment"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab"),
        known_clues=("project_echo_facility_confirmed", "personnel_index"),
    ),
    ChatCoverageSpec(
        source_file="05_player_background_主人公背景.md",
        npc_id="Venn",
        unlocked_story_level=5,
        trust_level=4,
        question="如果记录说我是 Subject 07，我该怎么理解自己的失忆、父母权限和保护仓？",
        player_location="core_room",
        inventory=("broken_badge", "parental_override_card", "capsule_interface_shard"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "parental_override", "capsule_interface"),
    ),
    ChatCoverageSpec(
        source_file="06_locations_地点设定.md",
        npc_id="Orin",
        unlocked_story_level=4,
        trust_level=2,
        question="漂流海滩、雾林石碑、灯塔下层和地下设施入口这些地点到底怎么连起来？",
        player_location="lighthouse",
        inventory=("broken_badge", "stele_rubbing", "old_lighthouse_key"),
        visited_locations=("beach", "village", "forest", "lighthouse"),
        known_clues=("forest_steles", "lighthouse_lower_entry"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="08_items_道具与线索.md",
        npc_id="Lira",
        unlocked_story_level=3,
        trust_level=2,
        question="我手里有蓝色小石头、旧病历夹和无线电录音，这些线索应该怎么分开看？",
        player_location="herbal_house",
        inventory=("broken_badge", "blue_small_stone", "old_medical_folder", "radio_recording"),
        known_clues=("nia_blue_stone", "echo_sickness", "radio_noise_repeat"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="10_hidden_truth_隐藏真相.md",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        trust_level=5,
        question="按终局记录查询归潮场、Subject 07、父母录音、人体观测区和主共振核心选择压力。",
        player_location="core_room",
        inventory=("broken_badge", "parental_override_card", "core_frequency_tape", "echo_chip"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "parents_recording", "main_resonance_core_state"),
    ),
    ChatCoverageSpec(
        source_file="13_npc_memories_记忆与传闻.md",
        npc_id="Venn",
        unlocked_story_level=4,
        trust_level=3,
        question="你、Karo、Lira、Orin 和 Mara 各自记得哪些关于灯塔、回声病和旧事故的片段？",
        player_location="abandoned_house",
        inventory=("broken_badge", "venn_research_page"),
        known_clues=("project_echo_name_seen", "echo_sickness"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="14_npc_relationships_人物关系网.md",
        npc_id="Orin",
        unlocked_story_level=3,
        trust_level=2,
        question="你和 Karo、Lira、Venn、Mara 之间为什么总像互相知道一点又不肯说完？",
        player_location="lighthouse",
        inventory=("broken_badge", "lighthouse_log"),
        known_clues=("lighthouse_not_normal", "karo_failed_escape"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="15_timeline_岛屿时间线.md",
        npc_id="Venn",
        unlocked_story_level=4,
        trust_level=3,
        question="把潮民早期、外来者登岛、大震夜、我醒来和灯塔下层重新打开按时间串起来。",
        player_location="abandoned_house",
        inventory=("broken_badge", "wet_navigation_page", "stele_rubbing"),
        known_clues=("quake_night_public", "lighthouse_lower_entry"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="16_daily_life_静潮村日常.md",
        npc_id="Karo",
        unlocked_story_level=1,
        trust_level=1,
        question="村里清晨、午后、雾夜平时都怎么过？我这种外来者该注意什么规矩？",
        player_location="dock",
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("Subject 07", "Project ECHO"),
    ),
    ChatCoverageSpec(
        source_file="17_npc_personal_arcs_个人经历链.md",
        npc_id="Venn",
        unlocked_story_level=5,
        trust_level=4,
        question="你们每个人到终局前各自经历了什么变化？尤其是你、Orin 和 Karo。",
        player_location="core_room",
        inventory=("broken_badge", "parental_override_card"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("subject07", "ending_choice_consultation"),
    ),
    ChatCoverageSpec(
        source_file="18_rumor_pool_村中传闻.md",
        npc_id="Nia",
        unlocked_story_level=2,
        trust_level=2,
        question="村里最近都在传什么？关于井边、我的铁牌、Karo 的旧船和草药屋锁柜。",
        player_location="village_well",
        inventory=("broken_badge", "blue_small_stone"),
        known_clues=("nia_blue_stone", "well_echo_rumor"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("你就是 Subject 07", "保护仓"),
    ),
    ChatCoverageSpec(
        source_file="19_dialogue_examples_NPC对话样例.md",
        npc_id="Karo",
        unlocked_story_level=2,
        trust_level=1,
        question="我一直追问 Project ECHO，又拿错东西来问你，你会怎么回答我？",
        player_location="dock",
        inventory=("broken_badge", "experiment_log_fragment"),
        known_clues=("project_echo_name_seen",),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("你就是 Subject 07", "低温休眠"),
    ),
    ChatCoverageSpec(
        source_file="20_village_events_村庄事件.md",
        npc_id="Lira",
        unlocked_story_level=2,
        trust_level=2,
        question="清晨送药、草药屋夜间排队、孩子传话游戏失控和码头无线电误响时，我能帮什么？",
        player_location="herbal_house",
        inventory=("broken_badge", "blue_small_stone"),
        known_clues=("echo_sickness_symptoms", "well_echo_rumor"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="21_environmental_storytelling_环境叙事.md",
        npc_id="Venn",
        unlocked_story_level=4,
        trust_level=3,
        question="我看到公告板旧纸、船底蓝灰粉末、你墙上的回到原点图和灯塔外墙编号，它们说明什么？",
        player_location="abandoned_house",
        inventory=("broken_badge", "stele_rubbing", "venn_map_fragments"),
        known_clues=("failed_escape_repeated", "steles_lighthouse_connection"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    ChatCoverageSpec(
        source_file="22_ending_reactions_结局反应.md",
        npc_id="Venn",
        unlocked_story_level=5,
        trust_level=4,
        question="关闭、稳定、部分关闭或放弃操作之后，村庄和大家会怎样变化？",
        player_location="core_room",
        inventory=("broken_badge", "parental_override_card", "core_frequency_tape"),
        visited_locations=("beach", "village", "lighthouse", "underground_lab", "core_room"),
        known_clues=("ending_choice_consultation", "close_tidefield_option"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_answer_terms=("关闭系统", "村庄会进入休眠", "直到重启", "运行状态", "重新加载"),
    ),
    ChatCoverageSpec(
        source_file="23_rumor_truth_layers_传闻真相分层.md",
        npc_id="Elder_Mara",
        unlocked_story_level=5,
        trust_level=4,
        question="灯塔蓝光偷走名字、井里另一个自己、海雾送人回来和 Subject 07 这些传闻最后该怎么分层理解？",
        player_location="elder_house",
        inventory=("broken_badge", "stele_rubbing"),
        visited_locations=("beach", "village", "forest", "lighthouse", "underground_lab"),
        known_clues=("subject07", "rumor_truth_layers"),
        forbidden_answer_terms=("实验痕迹", "控制节点", "共振机制", "地表节点", "边界反馈"),
    ),
    ChatCoverageSpec(
        source_file="24_player_choice_consequences_玩家行为后果.md",
        npc_id="Lira",
        unlocked_story_level=3,
        trust_level=3,
        question="如果我尊重禁忌、帮日常杂务、保护 Nia、照护 Venn、认真记录症状，村里会怎么回应？",
        player_location="herbal_house",
        inventory=("broken_badge", "blue_small_stone", "old_medical_folder"),
        known_clues=("nia_blue_stone", "echo_sickness_not_normal"),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
)


def build_chat_coverage_cases() -> tuple[ChatCase, ...]:
    existing_files = {path.name for path in DOC_DIR.rglob("*.md")}
    cases: list[ChatCase] = []

    for spec in CHAT_COVERAGE_SPECS:
        if spec.source_file not in existing_files:
            continue

        cases.append(
            ChatCase(
                case_id="coverage_" + spec.source_file.split("_", 1)[0],
                npc_id=spec.npc_id,
                unlocked_story_level=spec.unlocked_story_level,
                trust_level=spec.trust_level,
                question=spec.question,
                top_k=spec.top_k,
                player_location=spec.player_location,
                current_quest=spec.current_quest,
                inventory=spec.inventory,
                visited_locations=spec.visited_locations,
                known_clues=spec.known_clues,
                required_any_sources=(spec.source_file,),
                preferred_any_sources=(spec.source_file,),
                forbidden_sources=spec.forbidden_sources,
                forbidden_answer_terms=spec.forbidden_answer_terms,
                notes=spec.notes or f"Chat source coverage for {spec.source_file}.",
            )
        )

    return tuple(cases)


CHAT_COVERAGE_CASES: tuple[ChatCase, ...] = build_chat_coverage_cases()
QUALITY_SUITE_CHAT_CASES: tuple[ChatCase, ...] = (
    QUALITY_CHAT_CASES + SENSITIVE_CHAT_CASES + VOICE_DRIFT_CHAT_CASES
)
CHAT_CASES: tuple[ChatCase, ...] = QUALITY_SUITE_CHAT_CASES + CHAT_COVERAGE_CASES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the isolated NPC RAG streaming API.",
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help=f"Streaming endpoint URL. Default: {DEFAULT_URL}",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"NPC backend base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="NPC bearer token. If omitted, the script logs in with username/password.",
    )
    parser.add_argument(
        "--username",
        default=DEFAULT_USERNAME,
        help=f"NPC login username. Default: {DEFAULT_USERNAME}",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help=f"NPC login password. Default: {DEFAULT_PASSWORD}",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        default=True,
        help="Register the user in the isolated NPC database before login. Enabled by default.",
    )
    parser.add_argument(
        "--no-register",
        action="store_false",
        dest="register",
        help="Skip auto-registering the NPC debug user.",
    )
    parser.add_argument(
        "--skip-health",
        action="store_true",
        help="Skip /health check before auth.",
    )
    parser.add_argument(
        "--fetch-records",
        action="store_true",
        help="Fetch saved thread records after streaming finishes.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List built-in chat test cases.",
    )
    parser.add_argument(
        "--suite",
        choices=("quality", "sensitive", "coverage", "all"),
        default="all",
        help="Which built-in suite to run. Default: all.",
    )
    parser.add_argument(
        "--case",
        dest="case_ids",
        action="append",
        default=[],
        help="Run only this built-in case id. Can be repeated.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed chat case.",
    )
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Treat source quality warnings as failures.",
    )
    parser.add_argument(
        "--thread-id",
        default=None,
        help="Existing NPC thread ID. If omitted, backend creates a new thread.",
    )
    parser.add_argument(
        "--question",
        default=None,
        help="Run a single ad hoc question instead of the built-in suite.",
    )
    parser.add_argument(
        "--npc-id",
        default="Karo",
        help="NPC ID, for example Karo, Venn, Orin, Lab_Terminal.",
    )
    parser.add_argument(
        "--unlock-level",
        type=int,
        default=1,
        help="Player unlocked story level.",
    )
    parser.add_argument(
        "--trust-level",
        type=int,
        default=0,
        help="Trust level with the NPC for --question mode. 0=stranger, 5=trusted ally.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of chunks to retrieve from Milvus.",
    )
    parser.add_argument(
        "--player-location",
        default="dock",
        help="Player location sent to the NPC API.",
    )
    parser.add_argument(
        "--current-quest",
        default="why_cannot_leave",
        help="Current quest sent to the NPC API.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--output-md",
        nargs="?",
        const="auto",
        default="auto",
        help=(
            "Write a Markdown report with case results, sources, failures, warnings, and answers. "
            "Enabled by default under reports/. Pass a path to customize."
        ),
    )
    parser.add_argument(
        "--no-output-md",
        action="store_const",
        const=None,
        dest="output_md",
        help="Disable the default Markdown report output.",
    )
    return parser.parse_args()


def build_payload(args: argparse.Namespace, case: ChatCase) -> dict[str, Any]:
    return {
        "question": case.question,
        "thread_id": args.thread_id,
        "npc_id": case.npc_id,
        "unlocked_story_level": case.unlocked_story_level,
        "trust_level": case.trust_level,
        "top_k": case.top_k,
        "player_id": "demo_player",
        "player_location": case.player_location,
        "current_quest": case.current_quest,
        "inventory": list(case.inventory),
        "visited_locations": list(case.visited_locations),
        "known_clues": list(case.known_clues),
    }


def build_ad_hoc_case(args: argparse.Namespace) -> ChatCase:
    return ChatCase(
        case_id="ad_hoc",
        npc_id=args.npc_id,
        unlocked_story_level=args.unlock_level,
        trust_level=args.trust_level,
        question=str(args.question),
        top_k=args.top_k,
        player_location=args.player_location,
        current_quest=args.current_quest,
    )


def check_health(args: argparse.Namespace) -> None:
    http = require_requests()
    health_url = args.base_url.rstrip("/") + "/health"
    response = http.get(health_url, timeout=args.timeout)
    response.raise_for_status()
    print("[health]", json.dumps(response.json(), ensure_ascii=False))


def get_token(args: argparse.Namespace) -> str:
    http = require_requests()
    if args.token:
        print("[auth] using provided NPC bearer token")
        return args.token

    if args.register:
        register_url = args.base_url.rstrip("/") + "/auth/register"
        register_response = http.post(
            register_url,
            json={"username": args.username, "password": args.password},
            timeout=args.timeout,
        )
        if register_response.status_code == 200:
            print(f"[auth] registered NPC user: {args.username}")
        elif register_response.status_code == 400:
            print(f"[auth] NPC user already exists: {args.username}")
        else:
            register_response.raise_for_status()

    login_url = args.base_url.rstrip("/") + "/auth/login"
    response = http.post(
        login_url,
        json={"username": args.username, "password": args.password},
        timeout=args.timeout,
    )
    response.raise_for_status()
    print(f"[auth] logged in NPC user: {args.username}")
    return str(response.json()["access_token"])


def fetch_thread_records(args: argparse.Namespace, token: str, thread_id: str) -> None:
    http = require_requests()
    records_url = args.base_url.rstrip("/") + f"/threads/{thread_id}/records"
    response = http.get(
        records_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=args.timeout,
    )
    response.raise_for_status()
    records = response.json()

    print(f"\n[records] count={len(records)}")
    for record in records[-3:]:
        print(
            f"  #{record.get('id')} used_search={record.get('used_search')} "
            f"question={record.get('question')}"
        )


def evaluate_result(result: ChatRunResult) -> None:
    if not result.thread_id:
        result.failures.append("No thread event received.")

    if not result.saw_done:
        result.failures.append("Stream ended without a done event.")

    if not result.answer:
        result.failures.append("No answer_delta text received.")

    source_files = {str(source.get("source_file", "")) for source in result.sources}

    forbidden_seen = sorted(source for source in result.case.forbidden_sources if source in source_files)
    if forbidden_seen:
        result.failures.append("Forbidden source retrieved: " + ", ".join(forbidden_seen))

    if result.case.required_any_sources and not any(
        source in source_files for source in result.case.required_any_sources
    ):
        result.warnings.append(
            "Required source missing: "
            + ", ".join(result.case.required_any_sources)
            + f"; got: {', '.join(sorted(source_files)) or 'none'}"
        )

    forbidden_terms = [term for term in result.case.forbidden_answer_terms if term in result.answer]
    if forbidden_terms:
        result.failures.append("Forbidden answer term(s): " + ", ".join(forbidden_terms))

    if result.case.preferred_any_sources and not any(
        source in source_files for source in result.case.preferred_any_sources
    ):
        result.warnings.append(
            "Preferred source missing: " + ", ".join(result.case.preferred_any_sources)
        )


def print_result_summary(result: ChatRunResult) -> None:
    status = "PASS" if result.passed else "FAIL"
    if result.passed and result.warnings:
        status = "WARN"

    print("-" * 80)
    print(
        f"[{status}] {result.case.case_id} "
        f"npc={result.case.npc_id} L{result.case.unlocked_story_level} T{result.case.trust_level} "
        f"answer_length={len(result.answer)} sources={result.source_count}"
    )
    if result.failures:
        print("Failures:")
        for failure in result.failures:
            print(f"  - {failure}")
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def write_markdown_report(
    output_path: str | Path,
    args: argparse.Namespace,
    results: list[ChatRunResult],
    setup_error: str | None = None,
) -> None:
    path = resolve_report_path(output_path, args)
    path.parent.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    warned = sum(1 for result in results if result.warnings)

    lines: list[str] = [
        "# NPC Chat Stream Test Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Endpoint: `{args.url}`",
        f"- Suite: `{args.suite}`",
        f"- Total: {len(results)}",
        f"- Passed: {passed}",
        f"- Failed: {failed}",
        f"- Warned: {warned}",
    ]

    if setup_error:
        lines.extend(
            [
                "",
                "## Setup Error",
                "",
                "```text",
                setup_error,
                "```",
            ]
        )

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        if result.passed and result.warnings:
            status = "WARN"

        case = result.case
        lines.extend(
            [
                "",
                f"## {status}: {case.case_id}",
                "",
                f"- NPC: `{case.npc_id}`",
                f"- Unlock Level: `{case.unlocked_story_level}`",
                f"- Trust Level: `{case.trust_level}`",
                f"- Thread ID: `{result.thread_id or ''}`",
                f"- Source Count: {result.source_count}",
                f"- Saw Done Event: `{result.saw_done}`",
                "",
                "### Question",
                "",
                case.question,
            ]
        )

        if case.notes:
            lines.extend(["", "### Notes", "", case.notes])

        if result.failures:
            lines.extend(["", "### Failures", ""])
            lines.extend(f"- {failure}" for failure in result.failures)

        if result.warnings:
            lines.extend(["", "### Warnings", ""])
            lines.extend(f"- {warning}" for warning in result.warnings)

        lines.extend(["", "### Sources", ""])
        if result.sources:
            lines.extend(
                [
                    "| # | Score | Unlock | Source | Section |",
                    "|---:|---:|---:|---|---|",
                ]
            )
            for index, source in enumerate(result.sources, start=1):
                score = source.get("score", "")
                unlock = source.get("unlock_level", "")
                source_file = str(source.get("source_file", "")).replace("|", "\\|")
                section = str(source.get("section_title", "")).replace("|", "\\|")
                lines.append(f"| {index} | {score} | {unlock} | `{source_file}` | {section} |")
        else:
            lines.append("_No sources returned._")

        lines.extend(
            [
                "",
                "### Answer",
                "",
                "```text",
                result.answer or "",
                "```",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] wrote Markdown report: {path.resolve()}")


def resolve_report_path(output_path: str | Path, args: argparse.Namespace) -> Path:
    raw_path = Path(output_path)
    if str(output_path) == "auto":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suite_or_case = "adhoc" if args.question else args.suite
        return DEFAULT_REPORT_DIR / f"npc_chat_{suite_or_case}_{timestamp}.md"

    if raw_path.suffix.lower() != ".md":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        suite_or_case = "adhoc" if args.question else args.suite
        return raw_path / f"npc_chat_{suite_or_case}_{timestamp}.md"

    return raw_path


def run_chat_case(args: argparse.Namespace, token: str, case: ChatCase) -> ChatRunResult:
    http = require_requests()
    payload = build_payload(args, case)
    headers = {"Authorization": f"Bearer {token}"}
    result = ChatRunResult(case=case)

    print("=" * 80)
    print(f"[case] {case.case_id}")
    print("POST", args.url)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print("=" * 80)

    answer_parts: list[str] = []

    try:
        with http.post(
            args.url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=args.timeout,
        ) as response:
            print("HTTP", response.status_code)
            if response.status_code == 401:
                print(
                    "Unauthorized. This script targets npc_app's isolated auth database.",
                    file=sys.stderr,
                )
            response.raise_for_status()

            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue

                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    print("\n[raw]", raw_line)
                    continue

                event_type = event.get("type")
                data = event.get("data", {})

                if event_type == "thread":
                    result.thread_id = data.get("thread_id")
                    print(f"\n[thread] thread_id={result.thread_id} title={data.get('title')}")
                elif event_type == "status":
                    print(f"\n[status] {data.get('message', '')}")
                elif event_type == "sources":
                    sources = data.get("sources", [])
                    result.sources = list(sources)
                    result.source_count = int(data.get("retrieved_count", len(sources)))
                    print(f"\n[sources] retrieved_count={result.source_count}")
                    for index, source in enumerate(sources, start=1):
                        print(
                            f"  [{index}] score={source.get('score')} "
                            f"unlock={source.get('unlock_level')} "
                            f"{source.get('source_file')} / {source.get('section_title')}"
                        )
                elif event_type == "answer_delta":
                    text = str(data.get("text", ""))
                    answer_parts.append(text)
                    print(text, end="", flush=True)
                elif event_type == "error":
                    print(f"\n[error] {data.get('message', '')}")
                    result.failures.append(str(data.get("message", "")) or "Stream error event received.")
                    return result
                elif event_type == "done":
                    result.saw_done = True
                    print(f"\n\n[done] {json.dumps(data, ensure_ascii=False)}")
                else:
                    print(f"\n[{event_type}] {json.dumps(data, ensure_ascii=False)}")
    except RequestException as exc:
        result.failures.append(f"Request failed: {exc}")
        return result

    result.answer = "".join(answer_parts).strip()
    evaluate_result(result)

    print("=" * 80)
    print(
        f"thread_id={result.thread_id} answer_length={len(result.answer)} "
        f"source_count={result.source_count} saw_done={result.saw_done}"
    )

    if args.fetch_records and result.thread_id:
        try:
            fetch_thread_records(args, token, result.thread_id)
        except RequestException as exc:
            result.failures.append(f"Fetching records failed: {exc}")

    return result


def select_cases(args: argparse.Namespace) -> tuple[ChatCase, ...]:
    if args.question:
        return (build_ad_hoc_case(args),)

    if args.suite == "quality":
        available_cases = QUALITY_SUITE_CHAT_CASES
    elif args.suite == "sensitive":
        available_cases = SENSITIVE_CHAT_CASES
    elif args.suite == "coverage":
        available_cases = CHAT_COVERAGE_CASES
    else:
        available_cases = CHAT_CASES

    if not args.case_ids:
        return available_cases

    selected = set(args.case_ids)
    cases = tuple(case for case in available_cases if case.case_id in selected)
    missing = selected.difference(case.case_id for case in cases)
    if missing:
        raise ValueError("Unknown case id(s): " + ", ".join(sorted(missing)))
    return cases


def main() -> int:
    args = parse_args()

    if args.list:
        if args.suite == "quality":
            listed_cases = QUALITY_SUITE_CHAT_CASES
        elif args.suite == "sensitive":
            listed_cases = SENSITIVE_CHAT_CASES
        elif args.suite == "coverage":
            listed_cases = CHAT_COVERAGE_CASES
        else:
            listed_cases = CHAT_CASES
        for case in listed_cases:
            required = case.required_any_sources[0] if len(case.required_any_sources) == 1 else ""
            suffix = f"\tSRC={required}" if required else ""
            print(
                f"{case.case_id}\tNPC={case.npc_id}\t"
                f"L{case.unlocked_story_level}\tT{case.trust_level}{suffix}\t{case.question}"
            )
        return 0

    try:
        cases = select_cases(args)
        if not args.skip_health:
            check_health(args)
        token = get_token(args)
    except (RequestException, ValueError, KeyError, RuntimeError) as exc:
        error_message = f"Setup/auth failed: {exc}"
        print(error_message, file=sys.stderr)
        if args.output_md:
            write_markdown_report(args.output_md, args, [], setup_error=error_message)
        return 1

    results: list[ChatRunResult] = []
    for case in cases:
        result = run_chat_case(args, token, case)
        if args.strict_warnings and result.warnings:
            result.failures.extend(f"Strict warning: {warning}" for warning in result.warnings)
        results.append(result)
        print_result_summary(result)
        if args.fail_fast and not result.passed:
            break

    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    warned = sum(1 for result in results if result.warnings)

    print("=" * 80)
    print(f"NPC chat stream summary: passed={passed} failed={failed} warned={warned} total={len(results)}")

    if args.output_md:
        write_markdown_report(args.output_md, args, results)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
