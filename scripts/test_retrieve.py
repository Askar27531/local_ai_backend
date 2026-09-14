from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOC_DIR = PROJECT_ROOT / "game_docs"

COLLECTION_NAME = "guichao_island_chunks"

MILVUS_URI = "http://localhost:19530"
MILVUS_TOKEN = "root:Milvus"

EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"


@dataclass(frozen=True)
class RetrieveHit:
    score: float
    source_file: str
    section_title: str
    npc_id: str
    unlock_level: int
    topics: str
    spoiler_level: str
    content: str


@dataclass(frozen=True)
class RetrieveCase:
    case_id: str
    question: str
    npc_id: str
    unlocked_story_level: int
    top_k: int = 8
    description: str = ""
    expected_any_sources: tuple[str, ...] = ()
    expected_all_sources: tuple[str, ...] = ()
    forbidden_sources: tuple[str, ...] = ()
    expected_any_sections: tuple[str, ...] = ()
    forbidden_section_keywords: tuple[str, ...] = ()
    preferred_any_sources: tuple[str, ...] = ()
    preferred_any_sections: tuple[str, ...] = ()
    preferred_top_n: int = 8
    min_hits: int = 1
    notes: str = ""


@dataclass(frozen=True)
class SourceCoverageSpec:
    source_file: str
    question: str
    npc_id: str
    unlocked_story_level: int
    top_k: int = 12
    description: str = ""


@dataclass
class CaseResult:
    case: RetrieveCase
    hits: list[RetrieveHit]
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


QUALITY_CASES: tuple[RetrieveCase, ...] = (
    RetrieveCase(
        case_id="level0_karo_public_village",
        npc_id="Karo",
        unlocked_story_level=0,
        question="静潮村平时怎么生活？",
        description="Level 0 public world/life query should stay in low-spoiler village lore.",
        expected_any_sources=(
            "01_world_lore_世界观.md",
            "16_daily_life_静潮村日常.md",
            "19_dialogue_examples_NPC对话样例.md",
            "20_village_events_村庄事件.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
    ),
    RetrieveCase(
        case_id="level1_karo_cannot_leave",
        npc_id="Karo",
        unlocked_story_level=1,
        question="我为什么无法离开这座岛？海雾和码头有什么关系？",
        description="Karo level 1 should retrieve public归潮/Karo/码头 context, not final truth.",
        expected_any_sources=(
            "01_world_lore_世界观.md",
            "06_locations_地点设定.md",
            "13_npc_memories_记忆与传闻.md",
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "20_village_events_村庄事件.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
    ),
    RetrieveCase(
        case_id="level1_nia_project_echo_guardrail",
        npc_id="Nia",
        unlocked_story_level=1,
        question="Project ECHO 是什么？Subject 07 是我吗？",
        description="Nia must not retrieve organization/final truth when asked a spoiler question early.",
        expected_any_sources=(
            "08_items_道具与线索.md",
            "13_npc_memories_记忆与传闻.md",
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_section_keywords=("Subject 07 与玩家自我认知", "真实身份", "父母"),
    ),
    RetrieveCase(
        case_id="level2_lira_blue_stone",
        npc_id="Lira",
        unlocked_story_level=2,
        question="这块蓝色小石头会不会让我头痛？我应该拿给谁看？",
        description="Lira level 2 should retrieve symptom/sample/blue stone context.",
        expected_any_sources=(
            "02_crystal_vein_水晶矿脉.md",
            "08_items_道具与线索.md",
            "13_npc_memories_记忆与传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
            "24_player_choice_consequences_玩家行为后果.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "10_hidden_truth_隐藏真相.md",
        ),
    ),
    RetrieveCase(
        case_id="level2_karo_wrong_parent_card",
        npc_id="Karo",
        unlocked_story_level=2,
        question="我拿到一张父母权限覆盖卡，你能告诉我它怎么用吗？",
        description="Wrong item/wrong NPC should retrieve refusal-style Karo examples, not parent truth.",
        expected_any_sources=("19_dialogue_examples_NPC对话样例.md",),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_section_keywords=("父母权限与最终真相", "主人公父亲", "主人公母亲"),
    ),
    RetrieveCase(
        case_id="level3_mara_forest_steles",
        npc_id="Elder_Mara",
        unlocked_story_level=3,
        question="雾林石碑和祭歌木片是什么意思？蓝脉禁忌为什么重要？",
        description="Mara level 3 should retrieve indigenous/steles/ritual context.",
        expected_any_sources=(
            "02_crystal_vein_水晶矿脉.md",
            "03_indigenous_people_原住民与岛屿社会.md",
            "08_items_道具与线索.md",
            "13_npc_memories_记忆与传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "10_hidden_truth_隐藏真相.md",
        ),
    ),
    RetrieveCase(
        case_id="level3_venn_subject_guardrail",
        npc_id="Venn",
        unlocked_story_level=3,
        question="Subject 07 是我吗？Project ECHO 为什么让我头痛？",
        description="Venn level 3 may retrieve fragments/examples, but must not retrieve final truth.",
        expected_any_sources=(
            "13_npc_memories_记忆与传闻.md",
            "17_npc_personal_arcs_个人经历链.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
            "24_player_choice_consequences_玩家行为后果.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_section_keywords=("真实身份：Subject 07", "父母录音后的心理节点"),
    ),
    RetrieveCase(
        case_id="level4_orin_lighthouse",
        npc_id="Orin",
        unlocked_story_level=4,
        question="灯塔下层、旧钥匙和雾林石碑到底有什么关系？",
        description="Orin level 4 should retrieve lighthouse/Orin/steles/dialogue examples.",
        expected_any_sources=(
            "06_locations_地点设定.md",
            "08_items_道具与线索.md",
            "13_npc_memories_记忆与传闻.md",
            "14_npc_relationships_人物关系网.md",
            "19_dialogue_examples_NPC对话样例.md",
            "21_environmental_storytelling_环境叙事.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
    RetrieveCase(
        case_id="level4_lab_project_echo_partial",
        npc_id="Lab_Terminal",
        unlocked_story_level=4,
        question="Project ECHO 是什么？灯塔节点状态如何？",
        description="Lab level 4 can retrieve organization/facility fragments, but no Subject final reveal.",
        expected_any_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "06_locations_地点设定.md",
            "13_npc_memories_记忆与传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "21_environmental_storytelling_环境叙事.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
        forbidden_section_keywords=("Subject 07 记录匹配", "父母权限与最终真相"),
    ),
    RetrieveCase(
        case_id="level5_lab_subject07_final",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        question="Subject 07 是谁？保护仓、安全弹射和父母权限有什么关系？",
        description="Lab level 5 should retrieve final identity/truth/item/dialogue context.",
        expected_any_sources=(
            "10_hidden_truth_隐藏真相.md",
            "05_player_background_主人公背景.md",
            "08_items_道具与线索.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        expected_any_sections=(
            "主人公是 Subject 07",
            "真实身份：Subject 07",
            "Subject 07 保护仓接口片",
            "Lab Terminal：破损日志格式样例",
        ),
    ),
    RetrieveCase(
        case_id="level5_venn_final_choice",
        npc_id="Venn",
        unlocked_story_level=5,
        question="如果关闭归潮场，会有什么代价？我该怎么理解父母留下的选择？",
        description="Venn level 5 can retrieve ending/player psychology fragments, but not the full hidden-truth file.",
        expected_any_sources=(
            "05_player_background_主人公背景.md",
            "08_items_道具与线索.md",
            "13_npc_memories_记忆与传闻.md",
            "17_npc_personal_arcs_个人经历链.md",
            "19_dialogue_examples_NPC对话样例.md",
            "22_ending_reactions_结局反应.md",
            "24_player_choice_consequences_玩家行为后果.md",
        ),
        preferred_any_sources=(
            "22_ending_reactions_结局反应.md",
            "24_player_choice_consequences_玩家行为后果.md",
            "17_npc_personal_arcs_个人经历链.md",
            "19_dialogue_examples_NPC对话样例.md",
        ),
        preferred_top_n=8,
        forbidden_sources=("10_hidden_truth_隐藏真相.md",),
    ),
)


EARLY_SENSITIVE_CASES: tuple[RetrieveCase, ...] = (
    RetrieveCase(
        case_id="sensitive_level1_karo_subject07",
        npc_id="Karo",
        unlocked_story_level=1,
        question="Subject 07 是谁？这是不是我的真实身份？",
        description="Early Karo should avoid final identity/Subject 07 truth and retrieve refusal or low-risk clue context.",
        expected_any_sources=(
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
        forbidden_section_keywords=(
            "Subject 07：名字还是编号：终局确认",
            "真实身份",
            "保护仓",
            "父母权限",
        ),
    ),
    RetrieveCase(
        case_id="sensitive_level1_nia_subject07",
        npc_id="Nia",
        unlocked_story_level=1,
        question="Subject 07 是谁？我是不是实验体？",
        description="Early Nia should stay in child-view rumors/refusal examples, not retrieve lab or identity truth.",
        expected_any_sources=(
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
        forbidden_section_keywords=(
            "Subject 07：名字还是编号：终局确认",
            "真实身份",
            "人体观测",
            "父母",
        ),
    ),
    RetrieveCase(
        case_id="sensitive_level1_lira_project_echo",
        npc_id="Lira",
        unlocked_story_level=1,
        question="Project ECHO 是什么？它是不是人体实验？",
        description="Early Lira may discuss symptoms and uncertainty, but must not retrieve organization/human-experiment truth.",
        expected_any_sources=(
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
        forbidden_section_keywords=(
            "Project ECHO 总体结构",
            "人体观测区",
            "人体实验",
            "父母权限",
        ),
    ),
    RetrieveCase(
        case_id="sensitive_level1_orin_lighthouse_control",
        npc_id="Orin",
        unlocked_story_level=1,
        question="灯塔是不是控制归潮的装置？下面是不是实验室？",
        description="Early Orin should warn and redirect to evidence, not retrieve control-node/facility explanations.",
        expected_any_sources=(
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
        forbidden_section_keywords=(
            "灯塔节点",
            "地表控制节点",
            "地下实验设施：入口通道",
            "Project ECHO 总体结构",
            "主共振核心",
        ),
    ),
    RetrieveCase(
        case_id="sensitive_level1_venn_project_echo",
        npc_id="Venn",
        unlocked_story_level=1,
        question="Project ECHO 是什么？你是不是知道所有真相？",
        description="Early Venn should retrieve uncertainty/refusal style, not organization or hidden-truth chunks.",
        expected_any_sources=(
            "18_rumor_pool_村中传闻.md",
            "19_dialogue_examples_NPC对话样例.md",
            "23_rumor_truth_layers_传闻真相分层.md",
        ),
        forbidden_sources=(
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        ),
        forbidden_section_keywords=(
            "Project ECHO 总体结构",
            "Subject 07：名字还是编号：终局确认",
            "真实身份",
            "父母",
        ),
    ),
)


SOURCE_COVERAGE_SPECS: tuple[SourceCoverageSpec, ...] = (
    SourceCoverageSpec(
        source_file="01_world_lore_世界观.md",
        npc_id="Karo",
        unlocked_story_level=1,
        question="归潮岛的日常、海雾、旧设备和岛民为什么无法离开？",
        description="Covers public world lore.",
    ),
    SourceCoverageSpec(
        source_file="02_crystal_vein_水晶矿脉.md",
        npc_id="Lira",
        unlocked_story_level=3,
        question="蓝脉石、蓝色水晶碎片、头痛、梦境和回声病之间有什么关系？",
        description="Covers crystal vein and low/mid-level medical interpretation.",
    ),
    SourceCoverageSpec(
        source_file="03_indigenous_people_原住民与岛屿社会.md",
        npc_id="Elder_Mara",
        unlocked_story_level=2,
        question="潮民的蓝脉禁忌、祭歌、雾林石碑和孩子规矩是什么意思？",
        description="Covers indigenous society and taboo context.",
    ),
    SourceCoverageSpec(
        source_file="04_shadow_organization_反政府科技组织.md",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        question="赫利俄斯结社、Project ECHO、内部部门、伦理审查缺失和组织撤离失败是什么？",
        description="Covers organization truth and internal records.",
    ),
    SourceCoverageSpec(
        source_file="05_player_background_主人公背景.md",
        npc_id="Venn",
        unlocked_story_level=5,
        question="Subject 07、玩家失忆、父母权限、保护仓和身份确认有什么关系？",
        description="Covers player background at final reveal.",
    ),
    SourceCoverageSpec(
        source_file="06_locations_地点设定.md",
        npc_id="Orin",
        unlocked_story_level=4,
        question="漂流海滩、静潮村、雾林、废弃灯塔和地下实验设施这些地点怎么连接？",
        description="Covers location lore.",
    ),
    SourceCoverageSpec(
        source_file="08_items_道具与线索.md",
        npc_id="Lira",
        unlocked_story_level=3,
        question="蓝色小石头、旧病历夹、无线电录音、灯塔日志和雾林石碑拓片这些道具说明什么？",
        description="Covers items and clue text.",
    ),
    SourceCoverageSpec(
        source_file="10_hidden_truth_隐藏真相.md",
        npc_id="Lab_Terminal",
        unlocked_story_level=5,
        question="最终真相里归潮场、Subject 07、父母录音、人体观测区和主共振核心选择是什么？",
        description="Covers final hidden truth.",
    ),
    SourceCoverageSpec(
        source_file="13_npc_memories_记忆与传闻.md",
        npc_id="Venn",
        unlocked_story_level=4,
        question="Venn、Karo、Lira、Orin、Nia 和 Mara 各自记得哪些关于回声、灯塔和旧事故的片段？",
        description="Covers NPC memories.",
    ),
    SourceCoverageSpec(
        source_file="14_npc_relationships_人物关系网.md",
        npc_id="Orin",
        unlocked_story_level=3,
        question="Karo、Lira、Orin、Venn、Nia 和 Mara 之间的关系、冲突和互相试探是什么？",
        description="Covers relationship network.",
    ),
    SourceCoverageSpec(
        source_file="15_timeline_岛屿时间线.md",
        npc_id="Venn",
        unlocked_story_level=4,
        question="潮民早期、外来者登岛、Project ECHO、大震夜、玩家醒来和灯塔下层开启的时间线是什么？",
        description="Covers island timeline.",
    ),
    SourceCoverageSpec(
        source_file="16_daily_life_静潮村日常.md",
        npc_id="Karo",
        unlocked_story_level=1,
        question="静潮村清晨、午后、雾夜、孩子游戏、物资交换和村民对玩家的日常反应是什么？",
        description="Covers daily life.",
    ),
    SourceCoverageSpec(
        source_file="17_npc_personal_arcs_个人经历链.md",
        npc_id="Venn",
        unlocked_story_level=5,
        question="Karo、Lira、Orin、Nia、Venn、Mara 和终端各自的个人经历链如何变化？",
        description="Covers NPC personal arcs.",
    ),
    SourceCoverageSpec(
        source_file="18_rumor_pool_村中传闻.md",
        npc_id="Nia",
        unlocked_story_level=2,
        question="村里关于海雾、井边、Nia 藏物、草药屋锁柜、Karo 旧船和公告板有什么传闻？",
        description="Covers rumor pool.",
    ),
    SourceCoverageSpec(
        source_file="19_dialogue_examples_NPC对话样例.md",
        npc_id="Karo",
        unlocked_story_level=2,
        question="如果玩家拿错道具、反复追问敏感真相或问下一步，Karo 应该怎样自然拒答和提示？",
        description="Covers dialogue examples, especially chat behavior.",
    ),
    SourceCoverageSpec(
        source_file="20_village_events_村庄事件.md",
        npc_id="Lira",
        unlocked_story_level=2,
        question="清晨送药、公告板换纸、孩子传话失控、草药屋夜间排队和码头无线电误响会怎样触发？",
        description="Covers village events.",
    ),
    SourceCoverageSpec(
        source_file="21_environmental_storytelling_环境叙事.md",
        npc_id="Venn",
        unlocked_story_level=4,
        question="公告板旧纸层、Karo 旧船蓝灰粉末、Venn 废屋地图、灯塔编号和地下走廊标语说明什么？",
        description="Covers environmental storytelling.",
    ),
    SourceCoverageSpec(
        source_file="22_ending_reactions_结局反应.md",
        npc_id="Venn",
        unlocked_story_level=5,
        question="关闭、稳定、部分关闭或放弃操作后，村庄和每个 NPC 的结局反应会怎样？",
        description="Covers ending reactions.",
    ),
    SourceCoverageSpec(
        source_file="23_rumor_truth_layers_传闻真相分层.md",
        npc_id="Elder_Mara",
        unlocked_story_level=5,
        question="灯塔蓝光偷走名字、井里另一个自己、海雾送人回来和 Subject 07 的传闻真相分层是什么？",
        description="Covers rumor/truth layering.",
    ),
    SourceCoverageSpec(
        source_file="24_player_choice_consequences_玩家行为后果.md",
        npc_id="Lira",
        unlocked_story_level=3,
        question="玩家尊重禁忌、帮助日常杂务、归还危险物品、照护 Venn 和记录回声病会造成什么后果？",
        description="Covers player choice consequences.",
    ),
)


def build_source_coverage_cases() -> tuple[RetrieveCase, ...]:
    existing_files = {path.name for path in DOC_DIR.rglob("*.md")}
    cases: list[RetrieveCase] = []

    for spec in SOURCE_COVERAGE_SPECS:
        if spec.source_file not in existing_files:
            continue

        cases.append(
            RetrieveCase(
                case_id="coverage_" + spec.source_file.split("_", 1)[0],
                question=spec.question,
                npc_id=spec.npc_id,
                unlocked_story_level=spec.unlocked_story_level,
                top_k=spec.top_k,
                description=spec.description or f"Source coverage for {spec.source_file}.",
                expected_any_sources=(spec.source_file,),
                preferred_any_sources=(spec.source_file,),
                preferred_top_n=spec.top_k,
            )
        )

    return tuple(cases)


DOC_COVERAGE_CASES: tuple[RetrieveCase, ...] = build_source_coverage_cases()
QUALITY_SUITE_CASES: tuple[RetrieveCase, ...] = QUALITY_CASES + EARLY_SENSITIVE_CASES
TEST_CASES: tuple[RetrieveCase, ...] = QUALITY_SUITE_CASES + DOC_COVERAGE_CASES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run fixed RAG retrieval quality checks for Guichao Island NPC docs.",
    )
    parser.add_argument("--case", dest="case_ids", action="append", default=[], help="Run only this case id. Can be repeated.")
    parser.add_argument("--list", action="store_true", help="List available test cases.")
    parser.add_argument(
        "--suite",
        choices=("quality", "sensitive", "coverage", "all"),
        default="all",
        help="Which built-in suite to run. Default: all.",
    )
    parser.add_argument("--top-k", type=int, default=None, help="Override top_k for all cases.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed case.")
    parser.add_argument("--strict-warnings", action="store_true", help="Treat retrieval quality warnings as failures.")
    parser.add_argument("--json", action="store_true", help="Print a machine-readable JSON summary.")
    parser.add_argument("--verbose", action="store_true", help="Print content previews for each hit.")
    parser.add_argument("--question", default=None, help="Run a single ad hoc question instead of the suite.")
    parser.add_argument("--npc-id", default="Karo", help="NPC ID for --question.")
    parser.add_argument("--unlock-level", type=int, default=1, help="Unlocked story level for --question.")
    return parser.parse_args()


def build_model() -> Any:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(EMBED_MODEL_NAME)


def build_client() -> Any:
    from pymilvus import MilvusClient

    if MILVUS_TOKEN:
        return MilvusClient(uri=MILVUS_URI, token=MILVUS_TOKEN)
    return MilvusClient(uri=MILVUS_URI)


def retrieve(
    model: Any,
    client: Any,
    question: str,
    npc_id: str,
    unlocked_story_level: int,
    top_k: int = 5,
) -> list[RetrieveHit]:
    query_vector = model.encode(
        [question],
        normalize_embeddings=True,
    )[0].tolist()

    filter_expr = "npc_id == {npc_id} and unlock_level <= {unlock_level}"

    results = client.search(
        collection_name=COLLECTION_NAME,
        data=[query_vector],
        filter=filter_expr,
        filter_params={"npc_id": npc_id, "unlock_level": unlocked_story_level},
        limit=int(top_k),
        output_fields=[
            "source_file",
            "section_title",
            "content",
            "npc_id",
            "unlock_level",
            "topics",
            "spoiler_level",
        ],
    )

    hits = results[0] if results else []
    return [_hit_to_retrieve_hit(hit) for hit in hits]


def _hit_to_retrieve_hit(hit: Any) -> RetrieveHit:
    entity = hit.get("entity", {}) if isinstance(hit, dict) else hit["entity"]
    score = hit.get("distance", 0.0) if isinstance(hit, dict) else hit["distance"]

    return RetrieveHit(
        score=float(score),
        source_file=str(entity.get("source_file", "")),
        section_title=str(entity.get("section_title", "")),
        content=str(entity.get("content", "")),
        npc_id=str(entity.get("npc_id", "")),
        unlock_level=int(entity.get("unlock_level", 0)),
        topics=str(entity.get("topics", "")),
        spoiler_level=str(entity.get("spoiler_level", "")),
    )


def evaluate_case(case: RetrieveCase, hits: list[RetrieveHit]) -> CaseResult:
    result = CaseResult(case=case, hits=hits)
    sources = {hit.source_file for hit in hits}
    sections = {hit.section_title for hit in hits}
    top_hits = hits[: max(0, case.preferred_top_n)]
    top_sources = {hit.source_file for hit in top_hits}
    top_sections = {hit.section_title for hit in top_hits}

    if len(hits) < case.min_hits:
        result.failures.append(f"Expected at least {case.min_hits} hits, got {len(hits)}.")

    wrong_npcs = [hit for hit in hits if hit.npc_id != case.npc_id]
    if wrong_npcs:
        result.failures.append(
            "Hits with wrong npc_id: "
            + ", ".join(f"{hit.source_file}/{hit.section_title}:{hit.npc_id}" for hit in wrong_npcs)
        )

    over_level = [hit for hit in hits if hit.unlock_level > case.unlocked_story_level]
    if over_level:
        result.failures.append(
            "Hits above unlocked_story_level: "
            + ", ".join(
                f"{hit.source_file}/{hit.section_title}:L{hit.unlock_level}"
                for hit in over_level
            )
        )

    for source in case.expected_all_sources:
        if source not in sources:
            result.failures.append(f"Expected source missing: {source}")

    if case.expected_any_sources and not any(source in sources for source in case.expected_any_sources):
        result.failures.append(
            "Expected at least one source from: "
            + ", ".join(case.expected_any_sources)
            + f"; got: {', '.join(sorted(sources)) or 'none'}"
        )

    forbidden_seen = sorted(source for source in case.forbidden_sources if source in sources)
    if forbidden_seen:
        result.failures.append("Forbidden source retrieved: " + ", ".join(forbidden_seen))

    if case.expected_any_sections and not any(
        any(expected in section for section in sections)
        for expected in case.expected_any_sections
    ):
        result.failures.append(
            "Expected at least one section containing: "
            + ", ".join(case.expected_any_sections)
            + f"; got: {', '.join(sorted(sections)) or 'none'}"
        )

    for keyword in case.forbidden_section_keywords:
        matched = [section for section in sections if keyword in section]
        if matched:
            result.failures.append(
                f"Forbidden section keyword {keyword!r} retrieved in: "
                + ", ".join(matched)
            )

    if case.preferred_any_sources and not any(source in top_sources for source in case.preferred_any_sources):
        result.warnings.append(
            f"Preferred source missing from top {case.preferred_top_n}: "
            + ", ".join(case.preferred_any_sources)
        )

    if case.preferred_any_sections and not any(
        any(expected in section for section in top_sections)
        for expected in case.preferred_any_sections
    ):
        result.warnings.append(
            f"Preferred section missing from top {case.preferred_top_n}: "
            + ", ".join(case.preferred_any_sections)
        )

    return result


def print_case_result(result: CaseResult, verbose: bool = False) -> None:
    status = "PASS" if result.passed else "FAIL"
    case = result.case
    print("=" * 100)
    print(f"[{status}] {case.case_id}")
    print(f"NPC={case.npc_id} unlock={case.unlocked_story_level} top_k={case.top_k}")
    print(f"Q: {case.question}")
    if case.description:
        print(f"Why: {case.description}")

    for index, hit in enumerate(result.hits, start=1):
        print(
            f"  [{index}] score={hit.score:.4f} "
            f"npc={hit.npc_id} L{hit.unlock_level} "
            f"{hit.source_file} / {hit.section_title}"
        )
        if verbose:
            print("      " + hit.content[:220].replace("\n", " "))

    if result.failures:
        print("Failures:")
        for failure in result.failures:
            print(f"  - {failure}")

    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")


def result_to_dict(result: CaseResult) -> dict[str, Any]:
    return {
        "case_id": result.case.case_id,
        "passed": result.passed,
        "failures": result.failures,
        "warnings": result.warnings,
        "npc_id": result.case.npc_id,
        "unlocked_story_level": result.case.unlocked_story_level,
        "question": result.case.question,
        "hits": [
            {
                "score": hit.score,
                "source_file": hit.source_file,
                "section_title": hit.section_title,
                "npc_id": hit.npc_id,
                "unlock_level": hit.unlock_level,
                "spoiler_level": hit.spoiler_level,
            }
            for hit in result.hits
        ],
    }


def run_suite(args: argparse.Namespace) -> int:
    if args.suite == "quality":
        selected_cases = QUALITY_SUITE_CASES
    elif args.suite == "sensitive":
        selected_cases = EARLY_SENSITIVE_CASES
    elif args.suite == "coverage":
        selected_cases = DOC_COVERAGE_CASES
    else:
        selected_cases = TEST_CASES
    if args.case_ids:
        selected = set(args.case_ids)
        selected_cases = tuple(case for case in TEST_CASES if case.case_id in selected)
        missing = selected.difference(case.case_id for case in selected_cases)
        if missing:
            print("Unknown case id(s): " + ", ".join(sorted(missing)), file=sys.stderr)
            return 2

    try:
        model = build_model()
        client = build_client()
    except Exception as exc:
        print(f"Failed to initialize retrieval runtime: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    results: list[CaseResult] = []

    for case in selected_cases:
        top_k = args.top_k if args.top_k is not None else case.top_k
        effective_case = RetrieveCase(
            **{
                **case.__dict__,
                "top_k": top_k,
            }
        )
        hits = retrieve(
            model=model,
            client=client,
            question=effective_case.question,
            npc_id=effective_case.npc_id,
            unlocked_story_level=effective_case.unlocked_story_level,
            top_k=effective_case.top_k,
        )
        result = evaluate_case(effective_case, hits)
        if args.strict_warnings and result.warnings:
            result.failures.extend(f"Strict warning: {warning}" for warning in result.warnings)
        results.append(result)

        if not args.json:
            print_case_result(result, verbose=args.verbose)

        if args.fail_fast and not result.passed:
            break

    passed = sum(1 for result in results if result.passed)
    failed = len(results) - passed
    warned = sum(1 for result in results if result.warnings)

    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "failed": failed,
                    "warned": warned,
                    "total": len(results),
                    "results": [result_to_dict(result) for result in results],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print("=" * 100)
        print(f"Retrieval QA summary: passed={passed} failed={failed} warned={warned} total={len(results)}")

    return 0 if failed == 0 else 1


def run_ad_hoc(args: argparse.Namespace) -> int:
    try:
        model = build_model()
        client = build_client()
    except Exception as exc:
        print(f"Failed to initialize retrieval runtime: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    hits = retrieve(
        model=model,
        client=client,
        question=str(args.question),
        npc_id=args.npc_id,
        unlocked_story_level=args.unlock_level,
        top_k=args.top_k or 8,
    )
    case = RetrieveCase(
        case_id="ad_hoc",
        question=str(args.question),
        npc_id=args.npc_id,
        unlocked_story_level=args.unlock_level,
        top_k=args.top_k or 8,
    )
    result = evaluate_case(case, hits)
    print_case_result(result, verbose=True)
    return 0


def main() -> int:
    args = parse_args()

    if args.list:
        if args.suite == "quality":
            listed_cases = QUALITY_SUITE_CASES
        elif args.suite == "sensitive":
            listed_cases = EARLY_SENSITIVE_CASES
        elif args.suite == "coverage":
            listed_cases = DOC_COVERAGE_CASES
        else:
            listed_cases = TEST_CASES
        for case in listed_cases:
            expected_source = case.expected_any_sources[0] if len(case.expected_any_sources) == 1 else ""
            suffix = f"\tSRC={expected_source}" if expected_source else ""
            print(f"{case.case_id}\tNPC={case.npc_id}\tL{case.unlocked_story_level}{suffix}\t{case.description}")
        return 0

    if args.question:
        return run_ad_hoc(args)

    return run_suite(args)


if __name__ == "__main__":
    raise SystemExit(main())
