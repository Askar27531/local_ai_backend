from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_ollama import ChatOllama
from sqlalchemy import DateTime, Integer, JSON, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS_DIR = PROJECT_ROOT / "skills"
DEFAULT_MODEL_NAME = os.getenv("DEBUG_MODEL_NAME", "qwen3:14b")
DEFAULT_OLLAMA_BASE_URL = os.getenv("DEBUG_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
DEFAULT_REPORTS_DIR = PROJECT_ROOT / "reports"
DEFAULT_MEMORY_DATABASE_URL = os.getenv(
    "SKILL_MEMORY_DATABASE_URL",
    os.getenv("DATABASE_URL", "postgresql+psycopg://postgres:01478963.@localhost:5432/local_ai"),
)
ALLOWED_TEST_EDIT_FILES = {
    (PROJECT_ROOT / "scripts" / "test_retrieve.py").resolve(),
    (PROJECT_ROOT / "scripts" / "test_npc_chat_stream.py").resolve(),
}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    path: Path
    body: str


class MemoryBase(DeclarativeBase):
    pass


class AgentMemoryThread(MemoryBase):
    __tablename__ = "agent_memory_threads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    skill_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="active", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentMemoryEvent(MemoryBase):
    __tablename__ = "agent_memory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


class AgentMemorySummary(MemoryBase):
    __tablename__ = "agent_memory_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    known_issues: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    decisions: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    next_actions: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Load a local skill and ask a local debug LLM to analyze an NPC QA issue.",
    )
    parser.add_argument("question", nargs="*", help="Debug question or task.")
    parser.add_argument("--skills-dir", default=str(DEFAULT_SKILLS_DIR), help="Directory containing skill folders.")
    parser.add_argument("--skill", default=None, help="Skill name to force. If omitted, the script selects one.")
    parser.add_argument("--list-skills", action="store_true", help="List discovered skills and exit.")
    parser.add_argument("--reference", action="append", default=[], help="Reference file inside the selected skill. Can repeat.")
    parser.add_argument("--all-references", action="store_true", help="Load all Markdown files under the skill references directory.")
    parser.add_argument("--file", action="append", default=[], help="Extra local file to include as evidence. Can repeat.")
    parser.add_argument("--report", action="append", default=[], help="Markdown report to include as evidence. Can repeat.")
    parser.add_argument("--model", default=DEFAULT_MODEL_NAME, help="Ollama model for the debug agent.")
    parser.add_argument("--base-url", default=DEFAULT_OLLAMA_BASE_URL, help="Ollama base URL.")
    parser.add_argument("--num-ctx", type=int, default=int(os.getenv("DEBUG_MODEL_NUM_CTX", "4096")))
    parser.add_argument("--temperature", type=float, default=float(os.getenv("DEBUG_MODEL_TEMPERATURE", "0.2")))
    parser.add_argument("--tool-loop", action="store_true", help="Let the model call safe local debug tools before finalizing.")
    parser.add_argument("--max-tool-steps", type=int, default=4, help="Maximum tool calls in --tool-loop mode.")
    parser.add_argument("--tool-timeout", type=int, default=180, help="Timeout in seconds for each tool subprocess.")
    parser.add_argument("--tool-output-chars", type=int, default=12000, help="Maximum characters returned from one tool result.")
    parser.add_argument("--summarize-latest-report", action="store_true", help="Generate a Markdown diagnosis from the latest NPC chat report.")
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR), help="Directory containing generated Markdown reports.")
    parser.add_argument("--summary-output", default=None, help="Path for the generated summary Markdown.")
    parser.add_argument("--summary-digest-chars", type=int, default=24000, help="Maximum characters from the report digest sent to the model.")
    parser.add_argument("--run-chat-test-and-summarize", action="store_true", help="Run scripts/test_npc_chat_stream.py, write a Markdown report, then summarize it.")
    parser.add_argument("--chat-suite", choices=("quality", "sensitive", "coverage", "all"), default="all", help="Suite passed to scripts/test_npc_chat_stream.py.")
    parser.add_argument("--chat-case", action="append", default=[], help="Case id passed to scripts/test_npc_chat_stream.py. Can repeat.")
    parser.add_argument("--chat-strict-warnings", action="store_true", help="Pass --strict-warnings to scripts/test_npc_chat_stream.py.")
    parser.add_argument("--chat-fail-fast", action="store_true", help="Pass --fail-fast to scripts/test_npc_chat_stream.py.")
    parser.add_argument("--chat-skip-health", action="store_true", help="Pass --skip-health to scripts/test_npc_chat_stream.py.")
    parser.add_argument("--chat-base-url", default=None, help="Base URL passed to scripts/test_npc_chat_stream.py.")
    parser.add_argument("--chat-report-output", default=None, help="Markdown report path for scripts/test_npc_chat_stream.py.")
    parser.add_argument("--allow-test-edits", action="store_true", help="Allow tool loop edits to the two whitelisted test files.")
    parser.add_argument("--memory-db-url", default=DEFAULT_MEMORY_DATABASE_URL, help="Postgres URL for skill memory.")
    parser.add_argument("--memory-thread-id", default=None, help="Existing memory thread id. If omitted, one is selected or created.")
    parser.add_argument("--memory-title", default=None, help="Title used when creating a new memory thread.")
    parser.add_argument("--project-id", default=os.getenv("SKILL_PROJECT_ID", "local_ai_backend"), help="Project id for memory scoping.")
    parser.add_argument("--disable-memory", action="store_true", help="Disable Postgres memory for this run.")
    return parser.parse_args()


def create_memory_session_factory(db_url: str) -> sessionmaker[Session]:
    engine = create_engine(db_url, echo=False, pool_pre_ping=True)
    MemoryBase.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_or_create_memory_thread(
    session: Session,
    skill_name: str,
    project_id: str,
    thread_id: str | None,
    title: str | None,
) -> AgentMemoryThread:
    now = datetime.now()
    if thread_id:
        thread = session.get(AgentMemoryThread, thread_id)
        if thread is None:
            thread = AgentMemoryThread(
                id=thread_id,
                project_id=project_id,
                skill_name=skill_name,
                title=title or f"{skill_name} memory",
                created_at=now,
                updated_at=now,
            )
            session.add(thread)
            session.commit()
        return thread

    stmt = (
        select(AgentMemoryThread)
        .where(
            AgentMemoryThread.project_id == project_id,
            AgentMemoryThread.skill_name == skill_name,
            AgentMemoryThread.status == "active",
        )
        .order_by(AgentMemoryThread.updated_at.desc())
        .limit(1)
    )
    thread = session.execute(stmt).scalars().first()
    if thread is not None:
        return thread

    thread = AgentMemoryThread(
        id=str(uuid.uuid4()),
        project_id=project_id,
        skill_name=skill_name,
        title=title or f"{skill_name} memory",
        created_at=now,
        updated_at=now,
    )
    session.add(thread)
    session.commit()
    return thread


def record_memory_event(
    session: Session | None,
    thread: AgentMemoryThread | None,
    role: str,
    event_type: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if session is None or thread is None:
        return
    now = datetime.now()
    session.add(
        AgentMemoryEvent(
            thread_id=thread.id,
            role=role,
            event_type=event_type,
            content=content[:20000],
            event_metadata=metadata or {},
            created_at=now,
        )
    )
    thread.updated_at = now
    session.commit()


def get_memory_summary(session: Session | None, thread: AgentMemoryThread | None) -> AgentMemorySummary | None:
    if session is None or thread is None:
        return None
    stmt = select(AgentMemorySummary).where(AgentMemorySummary.thread_id == thread.id)
    return session.execute(stmt).scalars().first()


def format_memory_for_prompt(summary: AgentMemorySummary | None) -> str:
    if summary is None:
        return ""
    parts = ["# Project Memory"]
    if summary.summary.strip():
        parts.extend(["", "## Summary", summary.summary.strip()])
    if summary.known_issues:
        parts.extend(["", "## Known Issues"])
        parts.extend(f"- {json.dumps(item, ensure_ascii=False)}" for item in summary.known_issues[-12:])
    if summary.decisions:
        parts.extend(["", "## Decisions"])
        parts.extend(f"- {json.dumps(item, ensure_ascii=False)}" for item in summary.decisions[-12:])
    if summary.next_actions:
        parts.extend(["", "## Next Actions"])
        parts.extend(f"- {json.dumps(item, ensure_ascii=False)}" for item in summary.next_actions[-12:])
    return "\n".join(parts).strip()


def upsert_memory_summary(
    session: Session | None,
    thread: AgentMemoryThread | None,
    summary_text: str,
    known_issue: dict[str, Any] | None = None,
    decision: dict[str, Any] | None = None,
    next_action: dict[str, Any] | None = None,
) -> None:
    if session is None or thread is None:
        return
    summary = get_memory_summary(session, thread)
    now = datetime.now()
    if summary is None:
        summary = AgentMemorySummary(
            thread_id=thread.id,
            summary="",
            known_issues=[],
            decisions=[],
            next_actions=[],
            updated_at=now,
        )
        session.add(summary)

    existing = summary.summary.strip()
    lines = [line for line in [existing, summary_text.strip()] if line]
    summary.summary = "\n".join(lines)[-8000:]
    if known_issue:
        summary.known_issues = [*summary.known_issues, known_issue][-50:]
    if decision:
        summary.decisions = [*summary.decisions, decision][-50:]
    if next_action:
        summary.next_actions = [*summary.next_actions, next_action][-50:]
    summary.updated_at = now
    thread.updated_at = now
    session.commit()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def parse_skill(skill_md: Path) -> Skill:
    text = read_text(skill_md)
    if not text.startswith("---"):
        raise ValueError(f"Missing YAML frontmatter: {skill_md}")

    try:
        _, frontmatter_text, body = text.split("---", 2)
    except ValueError as exc:
        raise ValueError(f"Invalid YAML frontmatter: {skill_md}") from exc

    frontmatter = yaml.safe_load(frontmatter_text) or {}
    name = str(frontmatter.get("name", "")).strip()
    description = str(frontmatter.get("description", "")).strip()
    if not name or not description:
        raise ValueError(f"Skill must define name and description: {skill_md}")

    return Skill(
        name=name,
        description=description,
        path=skill_md.parent,
        body=body.strip(),
    )


def discover_skills(skills_dir: Path) -> list[Skill]:
    if not skills_dir.exists():
        return []

    skills: list[Skill] = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            skills.append(parse_skill(skill_md))
        except Exception as exc:
            print(f"[warn] skipped invalid skill {skill_md}: {exc}", file=sys.stderr)
    return skills


def select_skill(skills: list[Skill], question: str, forced_name: str | None) -> Skill:
    if forced_name:
        for skill in skills:
            if skill.name == forced_name:
                return skill
        raise ValueError(f"Skill not found: {forced_name}")

    if not skills:
        raise ValueError("No skills discovered.")

    if len(skills) == 1:
        return skills[0]

    query = question.lower()
    scored = []
    for skill in skills:
        haystack = f"{skill.name}\n{skill.description}".lower()
        score = sum(1 for term in query.split() if term and term in haystack)
        scored.append((score, skill))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def collect_references(skill: Skill, args: argparse.Namespace) -> list[tuple[Path, str]]:
    references_dir = skill.path / "references"
    if not references_dir.exists():
        return []

    paths: list[Path] = []
    if args.all_references:
        paths.extend(sorted(references_dir.glob("*.md")))
    for reference in args.reference:
        ref_path = references_dir / reference
        if ref_path.suffix == "":
            ref_path = ref_path.with_suffix(".md")
        paths.append(ref_path)

    collected: list[tuple[Path, str]] = []
    for path in dict.fromkeys(paths):
        if not path.exists():
            raise FileNotFoundError(f"Reference not found: {path}")
        collected.append((path, read_text(path)))
    return collected


def collect_evidence(paths: list[str]) -> list[tuple[Path, str]]:
    evidence: list[tuple[Path, str]] = []
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        if not path.exists():
            raise FileNotFoundError(f"Evidence file not found: {path}")
        evidence.append((path, read_text(path)))
    return evidence


def find_latest_npc_report(reports_dir: Path) -> Path:
    if not reports_dir.exists():
        raise FileNotFoundError(f"Reports directory not found: {reports_dir}")

    candidates = [
        path
        for path in reports_dir.glob("npc_chat*.md")
        if path.is_file()
        and "summary" not in path.stem.lower()
        and "diagnosis" not in path.stem.lower()
    ]
    if not candidates:
        raise FileNotFoundError(f"No npc_chat*.md reports found under {reports_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def split_report_sections(report_text: str) -> tuple[str, list[tuple[str, str]]]:
    lines = report_text.splitlines()
    first_case_index = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")),
        len(lines),
    )
    header = "\n".join(lines[:first_case_index]).strip()

    sections: list[tuple[str, str]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in lines[first_case_index:]:
        if line.startswith("## "):
            if current_title:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = line.strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_title:
        sections.append((current_title, "\n".join(current_lines).strip()))
    return header, sections


def project_relative_path(path: Path) -> Path:
    resolved = path.resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return path


def build_report_digest(report_path: Path, max_chars: int) -> str:
    report_text = read_text(report_path)
    header, sections = split_report_sections(report_text)

    fail_sections = [(title, body) for title, body in sections if title.startswith("## FAIL")]
    warn_sections = [(title, body) for title, body in sections if title.startswith("## WARN")]
    pass_count = sum(1 for title, _ in sections if title.startswith("## PASS"))

    parts = [
        f"# Report Digest",
        f"- Source report: `{project_relative_path(report_path)}`",
        "",
        "## Header",
        header or "_No header found._",
        "",
        "## Case Counts",
        f"- PASS sections: {pass_count}",
        f"- WARN sections: {len(warn_sections)}",
        f"- FAIL sections: {len(fail_sections)}",
    ]

    if fail_sections:
        parts.extend(["", "## FAIL Sections"])
        for _, body in fail_sections:
            parts.append(body)

    if warn_sections:
        parts.extend(["", "## WARN Sections"])
        for _, body in warn_sections:
            parts.append(body)

    if not fail_sections and not warn_sections:
        parts.extend(
            [
                "",
                "## Note",
                "The report has no FAIL or WARN sections. Use the header summary and pass count to identify residual risk only.",
            ]
        )

    return truncate_text("\n\n".join(parts), max_chars)


def default_summary_output_path(report_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return report_path.with_name(f"{report_path.stem}_summary_{timestamp}.md")


def default_chat_report_output_path(args: argparse.Namespace) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suite_or_case = "cases" if args.chat_case else args.chat_suite
    return Path(args.reports_dir) / f"npc_chat_{suite_or_case}_{timestamp}.md"


def build_summary_prompt(report_path: Path, report_digest: str) -> str:
    generated_at = datetime.now().isoformat(timespec="seconds")
    return f"""
请根据下面的最新 NPC chat 测试报告 digest，生成一份新的 Markdown 总结报告。

要求：

1. 使用中文解释问题、流程和判断标准；文件路径、命令、字段名、case id、verdict 和 root cause 枚举值保持原样。
2. 不要复述整份原报告，只总结主要问题、证据、影响范围和下一步改进方向。
3. 必须指出每类问题应该优先改进哪个方面：`data`、`ingestion`、`retrieval`、`prompt`、`generation`、`API` 或 `test`。
4. 如果只有 WARN 没有 FAIL，也要说明当前状态、残余风险、最值得优先优化的 warning。
5. 给出可以直接执行的下一步验证命令。
6. 输出必须是完整 Markdown，建议结构如下：
   - `# NPC Chat Debug Summary`
   - `## 报告来源`
   - `## 总体结论`
   - `## 主要问题`
   - `## 分层归因`
   - `## 优先级建议`
   - `## 下一步验证`

生成时间：{generated_at}
原始报告：`{project_relative_path(report_path)}`

{report_digest}
""".strip()


def extract_bullets(section_body: str, heading: str) -> list[str]:
    lines = section_body.splitlines()
    bullets: list[str] = []
    in_heading = False
    for line in lines:
        if line.startswith("### "):
            in_heading = line.strip() == f"### {heading}"
            continue
        if in_heading and line.startswith("- "):
            bullets.append(line[2:].strip())
        elif in_heading and line.startswith("### "):
            break
    return bullets


def extract_source_files(section_body: str) -> list[str]:
    sources: list[str] = []
    for line in section_body.splitlines():
        if "|" not in line or "`" not in line:
            continue
        parts = line.split("`")
        if len(parts) >= 2 and parts[1].endswith(".md"):
            sources.append(parts[1])
    return list(dict.fromkeys(sources))


def infer_root_causes(title: str, failures: list[str], warnings: list[str], sources: list[str]) -> list[str]:
    text = "\n".join([title, *failures, *warnings, *sources]).lower()
    causes: list[str] = []
    if "forbidden source" in text or "preferred source missing" in text or "required source missing" in text:
        causes.append("retrieval")
    if "forbidden answer" in text or "subject 07" in text or "project echo" in text:
        causes.append("prompt")
    if "no answer_delta" in text or "stream" in text or "done event" in text:
        causes.append("API")
    if "coverage_" in text:
        causes.append("retrieval")
    if not causes:
        causes.append("test")
    return list(dict.fromkeys(causes))


def build_fallback_summary(report_path: Path) -> str:
    report_text = read_text(report_path)
    header, sections = split_report_sections(report_text)
    fail_sections = [(title, body) for title, body in sections if title.startswith("## FAIL")]
    warn_sections = [(title, body) for title, body in sections if title.startswith("## WARN")]
    pass_count = sum(1 for title, _ in sections if title.startswith("## PASS"))

    header_lines = [
        line
        for line in header.splitlines()
        if line.startswith("- Total:")
        or line.startswith("- Passed:")
        or line.startswith("- Failed:")
        or line.startswith("- Warned:")
        or line.startswith("- Generated:")
        or line.startswith("- Suite:")
    ]

    issue_rows: list[tuple[str, list[str], list[str], list[str], list[str]]] = []
    for title, body in [*fail_sections, *warn_sections]:
        failures = extract_bullets(body, "Failures")
        warnings = extract_bullets(body, "Warnings")
        sources = extract_source_files(body)
        causes = infer_root_causes(title, failures, warnings, sources)
        issue_rows.append((title.replace("## ", ""), failures, warnings, sources, causes))

    lines = [
        "# NPC Chat Debug Summary",
        "",
        "## 报告来源",
        "",
        f"- Source report: `{project_relative_path(report_path)}`",
        f"- Summary generated: {datetime.now().isoformat(timespec='seconds')}",
        "- Generator: deterministic fallback in `scripts/run_skill_debug_agent.py`",
        "",
        "## 总体结论",
        "",
        *header_lines,
        f"- Parsed PASS sections: {pass_count}",
        f"- Parsed FAIL sections: {len(fail_sections)}",
        f"- Parsed WARN sections: {len(warn_sections)}",
        "",
    ]

    if fail_sections:
        lines.extend(
            [
                "当前最新报告仍有 `FAIL`，优先处理失败 case，再处理 source coverage 和 preferred source warning。",
                "",
            ]
        )
    elif warn_sections:
        lines.extend(
            [
                "当前最新报告没有解析到 `FAIL`，但仍有 `WARN`。建议优先处理影响证据覆盖和剧情安全边界的 warning。",
                "",
            ]
        )
    else:
        lines.extend(["当前最新报告没有解析到 `FAIL` 或 `WARN`。下一步以抽样复测和新增边界 case 为主。", ""])

    lines.extend(["## 主要问题", ""])
    if issue_rows:
        for title, failures, warnings, sources, causes in issue_rows:
            lines.append(f"### {title}")
            lines.append("")
            lines.append(f"- Root cause candidates: {', '.join(f'`{cause}`' for cause in causes)}")
            if failures:
                lines.append("- Failures:")
                lines.extend(f"  - {failure}" for failure in failures)
            if warnings:
                lines.append("- Warnings:")
                lines.extend(f"  - {warning}" for warning in warnings)
            if sources:
                lines.append("- Retrieved sources:")
                lines.extend(f"  - `{source}`" for source in sources[:8])
            lines.append("")
    else:
        lines.extend(["_No FAIL/WARN sections found._", ""])

    grouped: dict[str, list[str]] = {}
    for title, _, _, _, causes in issue_rows:
        for cause in causes:
            grouped.setdefault(cause, []).append(title)

    lines.extend(["## 分层归因", ""])
    if grouped:
        for cause, titles in grouped.items():
            lines.append(f"- `{cause}`: " + ", ".join(f"`{title}`" for title in titles))
    else:
        lines.append("- `test`: 当前没有失败或警告，主要风险来自测试覆盖不足。")
    lines.append("")

    lines.extend(
        [
            "## 优先级建议",
            "",
            "1. 先处理所有 `FAIL` case，特别是包含 `Subject 07`、`Project ECHO`、forbidden source 或 forbidden answer term 的早期剧情 case。",
            "2. 再处理 `Preferred source missing` / `Required source missing`，优先检查 `_build_retrieval_question`、`_rerank_chunks_for_request`、chunk metadata 和 source coverage。",
            "3. 如果 retrieval 正确但 answer 泄露或人设漂移，优先改 `npc_app/services/npc_prompt_service.py`。",
            "4. 如果 expected/forbidden source 与当前 canon 不一致，才改 `scripts/test_npc_chat_stream.py` 或 `scripts/test_retrieve.py` 的测试期望。",
            "",
            "## 下一步验证",
            "",
            "```powershell",
            "python scripts\\test_npc_chat_stream.py --case sensitive_lira_l1_project_echo --strict-warnings",
            "python scripts\\test_retrieve.py --suite sensitive --strict-warnings --verbose",
            "python scripts\\test_npc_chat_stream.py --suite coverage --strict-warnings",
            "```",
            "",
        ]
    )
    return "\n".join(lines).strip()


def generate_latest_report_summary(
    llm: ChatOllama,
    skill: Skill,
    references: list[tuple[Path, str]],
    args: argparse.Namespace,
    memory_text: str = "",
) -> Path:
    report_path = find_latest_npc_report(Path(args.reports_dir))
    output_path = Path(args.summary_output) if args.summary_output else default_summary_output_path(report_path)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_digest = build_report_digest(report_path, args.summary_digest_chars)
    system_prompt = build_system_prompt(skill, references, memory_text=memory_text)
    user_prompt = build_summary_prompt(report_path, report_digest)

    print(f"[latest-report] {report_path}", file=sys.stderr)
    print(f"[summary-output] {output_path}", file=sys.stderr)

    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    summary = str(response.content).strip()
    if not summary:
        summary = build_fallback_summary(report_path)
    output_path.write_text(summary + "\n", encoding="utf-8")
    return output_path


def generate_report_summary(
    llm: ChatOllama,
    skill: Skill,
    references: list[tuple[Path, str]],
    report_path: Path,
    args: argparse.Namespace,
    memory_text: str = "",
) -> Path:
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    report_path = report_path.resolve()
    if not report_path.exists():
        raise FileNotFoundError(f"Report not found: {report_path}")

    output_path = Path(args.summary_output) if args.summary_output else default_summary_output_path(report_path)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    report_digest = build_report_digest(report_path, args.summary_digest_chars)
    print(f"[report] {report_path}", file=sys.stderr)
    print(f"[summary-output] {output_path}", file=sys.stderr)

    response = llm.invoke(
        [
            SystemMessage(content=build_system_prompt(skill, references, memory_text=memory_text)),
            HumanMessage(content=build_summary_prompt(report_path, report_digest)),
        ]
    )
    summary = str(response.content).strip()
    if not summary:
        summary = build_fallback_summary(report_path)
    output_path.write_text(summary + "\n", encoding="utf-8")
    return output_path


def run_chat_test_and_summarize(
    llm: ChatOllama,
    skill: Skill,
    references: list[tuple[Path, str]],
    args: argparse.Namespace,
    memory_text: str = "",
) -> tuple[Path, Path, int]:
    report_path = Path(args.chat_report_output) if args.chat_report_output else default_chat_report_output_path(args)
    if not report_path.is_absolute():
        report_path = PROJECT_ROOT / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)

    script_args = [
        "--suite",
        args.chat_suite,
        "--output-md",
        str(report_path),
    ]
    for case_id in args.chat_case:
        script_args.extend(["--case", case_id])
    if args.chat_strict_warnings:
        script_args.append("--strict-warnings")
    if args.chat_fail_fast:
        script_args.append("--fail-fast")
    if args.chat_skip_health:
        script_args.append("--skip-health")
    if args.chat_base_url:
        script_args.extend(["--base-url", args.chat_base_url])

    print(f"[chat-test-output] {report_path}", file=sys.stderr)
    result = run_python_script(
        PROJECT_ROOT / "scripts" / "test_npc_chat_stream.py",
        script_args,
        timeout=args.tool_timeout,
        max_chars=args.tool_output_chars,
    )
    print(str(result.get("output", "")), file=sys.stderr)

    if not report_path.exists():
        raise FileNotFoundError(f"Chat test did not create report: {report_path}")

    summary_path = generate_report_summary(llm, skill, references, report_path, args, memory_text=memory_text)
    return report_path, summary_path, int(result.get("exit_code") or 0)


def build_system_prompt(skill: Skill, references: list[tuple[Path, str]], memory_text: str = "") -> str:
    parts = [
        "你是本地 NPC 剧情/设定问答调试 agent。",
        "你必须遵循下面加载的 skill。解释、流程和判断标准用中文；文件路径、命令、字段名和枚举值保持原样。",
        "",
        f"# Loaded Skill: {skill.name}",
        skill.body,
    ]

    if memory_text.strip():
        parts.extend(["", memory_text.strip()])

    if references:
        parts.append("")
        parts.append("# Loaded References")
        for path, text in references:
            parts.extend([f"\n## {path.name}", text.strip()])

    return "\n".join(parts).strip()


def build_tool_loop_prompt(
    skill: Skill,
    references: list[tuple[Path, str]],
    allow_test_edits: bool = False,
    memory_text: str = "",
) -> str:
    base_prompt = build_system_prompt(skill, references, memory_text=memory_text)
    edit_status = "enabled" if allow_test_edits else "disabled"
    tool_instructions = f"\n\n# Tool Loop\n\n当前 `edit_test_file` 权限：`{edit_status}`。\n" + """

# Tool Loop

当前 `edit_test_file` 权限：`{edit_status}`。

你可以在最终回答前调用受限工具。每一轮必须只输出一个 JSON object，不要输出 Markdown，不要包 code fence。

可用 action：

```json
{"action":"list_retrieve_cases","args":{"suite":"all"}}
{"action":"run_retrieve_case","args":{"case_id":"<case_id>","suite":"all","verbose":true,"strict_warnings":false}}
{"action":"run_retrieve_question","args":{"question":"<question>","npc_id":"Karo","unlock_level":1,"top_k":8}}
{"action":"list_chat_cases","args":{"suite":"all"}}
{"action":"run_chat_case","args":{"case_id":"<case_id>","suite":"all","strict_warnings":false}}
{"action":"run_chat_question","args":{"question":"<question>","npc_id":"Karo","unlock_level":1,"trust_level":0,"top_k":5}}
{"action":"read_file","args":{"path":"reports/example.md"}}
{"action":"edit_test_file","args":{"path":"scripts/test_npc_chat_stream.py","old_text":"<exact old text>","new_text":"<replacement text>","reason":"<why this edit is needed>"}}
{"action":"write_summary_report","args":{"path":"reports/npc_chat_debug_summary.md","content":"<complete Markdown summary>"}}
{"action":"finish","args":{"answer":"<中文最终诊断>"}}
```

规则：

- `suite` 只能使用 `all`、`quality`、`sensitive` 或 `coverage`。
- 只有用户明确允许测试代码修改时，才可以使用 `edit_test_file`。
- `edit_test_file` 只能修改 `scripts/test_retrieve.py` 或 `scripts/test_npc_chat_stream.py`，并且必须使用精确 `old_text` 替换。
- 修改测试代码后，必须运行最小相关测试生成 Markdown 报告，再读取该报告。
- 总结报告必须用 `write_summary_report` 写入 `reports/` 下的 `.md` 文件。
- 如果需要先找 case，先用 `list_retrieve_cases` 或 `list_chat_cases`。
- 如果问题是召回质量，优先用 `run_retrieve_case` 或 `run_retrieve_question`。
- 如果问题是 NPC 最终回答、stream、报告或人设，优先用 `run_chat_case` 或 `run_chat_question`。
- 如果用户提供 report 或文件路径，优先用 `read_file`。
- 工具失败也算证据，要在最终诊断里说明。
- 当证据足够时，输出 `finish`。
"""
    return (base_prompt + tool_instructions).strip()


def build_user_prompt(question: str, evidence: list[tuple[Path, str]]) -> str:
    parts = [
        "请根据已加载 skill 处理下面的问题。",
        "",
        f"用户问题：{question or '请根据提供的 evidence 做 NPC QA 调试诊断。'}",
    ]

    if evidence:
        parts.append("")
        parts.append("# Evidence")
        for path, text in evidence:
            rel_path = path
            try:
                rel_path = path.relative_to(PROJECT_ROOT)
            except ValueError:
                pass
            parts.extend([f"\n## {rel_path}", text.strip()])

    return "\n".join(parts).strip()


def build_tool_loop_user_prompt(question: str, evidence: list[tuple[Path, str]]) -> str:
    return (
        build_user_prompt(question, evidence)
        + "\n\n请先判断是否需要调用工具。如果需要，输出一个 action JSON；如果证据已足够，输出 `finish` action JSON。"
    )


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end < start:
            raise
        data = json.loads(stripped[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("Tool action must be a JSON object.")
    return data


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    return text[:max_chars] + f"\n\n[truncated {omitted} chars]"


def safe_project_path(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"Path is outside project root: {raw_path}") from exc
    return resolved


def safe_test_edit_path(raw_path: str) -> Path:
    resolved = safe_project_path(raw_path)
    if resolved not in ALLOWED_TEST_EDIT_FILES:
        allowed = ", ".join(str(path.relative_to(PROJECT_ROOT)) for path in sorted(ALLOWED_TEST_EDIT_FILES))
        raise ValueError(f"Test edits are only allowed for: {allowed}")
    return resolved


def safe_summary_report_path(raw_path: str) -> Path:
    resolved = safe_project_path(raw_path)
    reports_root = DEFAULT_REPORTS_DIR.resolve()
    try:
        resolved.relative_to(reports_root)
    except ValueError as exc:
        raise ValueError("Summary reports must be written under reports/") from exc
    if resolved.suffix.lower() != ".md":
        raise ValueError("Summary report path must end with .md")
    return resolved


def run_python_script(script_path: Path, args: list[str], timeout: int, max_chars: int) -> dict[str, Any]:
    command = [sys.executable, str(script_path), *args]
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        output = "\n".join(
            part
            for part in [
                f"$ {' '.join(command)}",
                f"exit_code={completed.returncode}",
                "STDOUT:",
                completed.stdout.strip(),
                "STDERR:",
                completed.stderr.strip(),
            ]
            if part != ""
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "output": truncate_text(output, max_chars),
        }
    except subprocess.TimeoutExpired as exc:
        partial_stdout = exc.stdout or ""
        partial_stderr = exc.stderr or ""
        return {
            "ok": False,
            "exit_code": None,
            "output": truncate_text(
                f"$ {' '.join(command)}\nTimed out after {timeout}s\nSTDOUT:\n{partial_stdout}\nSTDERR:\n{partial_stderr}",
                max_chars,
            ),
        }


def edit_test_file(path: Path, old_text: str, new_text: str, reason: str) -> dict[str, Any]:
    if not old_text:
        raise ValueError("old_text must not be empty")
    current = read_text(path)
    occurrences = current.count(old_text)
    if occurrences != 1:
        raise ValueError(f"old_text must match exactly once; matched {occurrences} time(s)")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak_{timestamp}")
    backup_path.write_text(current, encoding="utf-8")

    updated = current.replace(old_text, new_text, 1)
    path.write_text(updated, encoding="utf-8")

    return {
        "ok": True,
        "exit_code": 0,
        "output": "\n".join(
            [
                f"Edited: {project_relative_path(path)}",
                f"Backup: {project_relative_path(backup_path)}",
                f"Reason: {reason}",
                f"Old chars: {len(old_text)}",
                f"New chars: {len(new_text)}",
            ]
        ),
    }


def write_summary_report(path: Path, content: str) -> dict[str, Any]:
    if not content.strip():
        raise ValueError("summary content must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return {
        "ok": True,
        "exit_code": 0,
        "output": f"Wrote summary report: {project_relative_path(path)}",
    }


def optional_bool_arg(args: list[str], flag: str, value: Any) -> None:
    if bool(value):
        args.append(flag)


def execute_tool_action(action: dict[str, Any], timeout: int, max_chars: int, allow_test_edits: bool = False) -> dict[str, Any]:
    name = str(action.get("action", "")).strip()
    tool_args = action.get("args", {}) or {}
    if not isinstance(tool_args, dict):
        raise ValueError("action.args must be an object.")

    if name == "list_retrieve_cases":
        suite = normalize_suite(str(tool_args.get("suite", "all")))
        args = ["--list", "--suite", suite]
        return run_python_script(PROJECT_ROOT / "scripts" / "test_retrieve.py", args, timeout, max_chars)

    if name == "run_retrieve_case":
        case_id = str(tool_args["case_id"])
        suite = normalize_suite(str(tool_args.get("suite", "all")))
        args = ["--case", case_id, "--suite", suite]
        optional_bool_arg(args, "--verbose", tool_args.get("verbose", True))
        optional_bool_arg(args, "--strict-warnings", tool_args.get("strict_warnings", False))
        if tool_args.get("top_k") is not None:
            args.extend(["--top-k", str(int(tool_args["top_k"]))])
        return run_python_script(PROJECT_ROOT / "scripts" / "test_retrieve.py", args, timeout, max_chars)

    if name == "run_retrieve_question":
        args = [
            "--question",
            str(tool_args["question"]),
            "--npc-id",
            str(tool_args.get("npc_id", "Karo")),
            "--unlock-level",
            str(int(tool_args.get("unlock_level", 1))),
            "--top-k",
            str(int(tool_args.get("top_k", 8))),
        ]
        return run_python_script(PROJECT_ROOT / "scripts" / "test_retrieve.py", args, timeout, max_chars)

    if name == "list_chat_cases":
        suite = normalize_suite(str(tool_args.get("suite", "all")))
        args = ["--list", "--suite", suite]
        return run_python_script(PROJECT_ROOT / "scripts" / "test_npc_chat_stream.py", args, timeout, max_chars)

    if name == "run_chat_case":
        case_id = str(tool_args["case_id"])
        suite = normalize_suite(str(tool_args.get("suite", "all")))
        args = ["--case", case_id, "--suite", suite]
        optional_bool_arg(args, "--strict-warnings", tool_args.get("strict_warnings", False))
        if tool_args.get("base_url"):
            args.extend(["--base-url", str(tool_args["base_url"])])
        if tool_args.get("output_md"):
            args.extend(["--output-md", str(tool_args["output_md"])])
        return run_python_script(PROJECT_ROOT / "scripts" / "test_npc_chat_stream.py", args, timeout, max_chars)

    if name == "run_chat_question":
        args = [
            "--question",
            str(tool_args["question"]),
            "--npc-id",
            str(tool_args.get("npc_id", "Karo")),
            "--unlock-level",
            str(int(tool_args.get("unlock_level", 1))),
            "--trust-level",
            str(int(tool_args.get("trust_level", 0))),
            "--top-k",
            str(int(tool_args.get("top_k", 5))),
        ]
        if tool_args.get("base_url"):
            args.extend(["--base-url", str(tool_args["base_url"])])
        return run_python_script(PROJECT_ROOT / "scripts" / "test_npc_chat_stream.py", args, timeout, max_chars)

    if name == "read_file":
        path = safe_project_path(str(tool_args["path"]))
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        if path.is_dir():
            raise IsADirectoryError(f"Path is a directory: {path}")
        return {
            "ok": True,
            "exit_code": 0,
            "output": truncate_text(read_text(path), max_chars),
        }

    if name == "edit_test_file":
        if not allow_test_edits:
            raise PermissionError("edit_test_file requires --allow-test-edits")
        path = safe_test_edit_path(str(tool_args["path"]))
        return edit_test_file(
            path=path,
            old_text=str(tool_args["old_text"]),
            new_text=str(tool_args["new_text"]),
            reason=str(tool_args.get("reason", "")),
        )

    if name == "write_summary_report":
        path = safe_summary_report_path(str(tool_args["path"]))
        return write_summary_report(path=path, content=str(tool_args["content"]))

    if name == "finish":
        return {
            "ok": True,
            "exit_code": 0,
            "output": str(tool_args.get("answer", "")).strip(),
            "finished": True,
        }

    raise ValueError(f"Unknown action: {name}")


def normalize_suite(raw_suite: str) -> str:
    normalized = raw_suite.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "all",
        "all": "all",
        "quality": "quality",
        "retrieval_quality": "quality",
        "chat_quality": "quality",
        "sensitive": "sensitive",
        "spoiler": "sensitive",
        "spoiler_guardrail": "sensitive",
        "coverage": "coverage",
        "source_coverage": "coverage",
    }
    if normalized not in aliases:
        raise ValueError("suite must be one of: all, quality, sensitive, coverage")
    return aliases[normalized]


def run_tool_loop(
    llm: ChatOllama,
    skill: Skill,
    references: list[tuple[Path, str]],
    question: str,
    evidence: list[tuple[Path, str]],
    args: argparse.Namespace,
    memory_text: str = "",
    memory_session: Session | None = None,
    memory_thread: AgentMemoryThread | None = None,
) -> str:
    messages: list[Any] = [
        SystemMessage(
            content=build_tool_loop_prompt(
                skill,
                references,
                allow_test_edits=args.allow_test_edits,
                memory_text=memory_text,
            )
        ),
        HumanMessage(content=build_tool_loop_user_prompt(question, evidence)),
    ]

    for step in range(1, args.max_tool_steps + 1):
        response = llm.invoke(messages)
        raw = str(response.content).strip()
        print(f"[agent step {step}] {raw}", file=sys.stderr)
        record_memory_event(memory_session, memory_thread, "assistant", "tool_action_raw", raw, {"step": step})

        try:
            action = extract_json_object(raw)
            result = execute_tool_action(
                action,
                timeout=args.tool_timeout,
                max_chars=args.tool_output_chars,
                allow_test_edits=args.allow_test_edits,
            )
        except Exception as exc:
            result = {
                "ok": False,
                "exit_code": None,
                "output": f"{type(exc).__name__}: {exc}",
            }
            action = {"action": "invalid", "args": {}}

        record_memory_event(
            memory_session,
            memory_thread,
            "tool",
            str(action.get("action", "invalid")),
            str(result.get("output", "")),
            {"ok": result.get("ok"), "exit_code": result.get("exit_code"), "step": step},
        )

        if action.get("action") == "finish":
            final = str(result.get("output", "")).strip()
            upsert_memory_summary(
                memory_session,
                memory_thread,
                f"{datetime.now().date()}: tool-loop finished. {final[:1000]}",
                decision={"type": "tool_loop_finish", "answer": final[:1000]},
            )
            return final

        messages.append(AIMessage(content=raw))
        messages.append(
            HumanMessage(
                content=json.dumps(
                    {
                        "tool_result": {
                            "action": action.get("action"),
                            "ok": result.get("ok"),
                            "exit_code": result.get("exit_code"),
                            "output": result.get("output"),
                        },
                        "instruction": "根据 tool_result 继续。需要更多证据就输出下一个 action JSON；证据足够就输出 finish action JSON。",
                    },
                    ensure_ascii=False,
                )
            )
        )

    messages.append(
        HumanMessage(
            content="已达到 max_tool_steps。请基于已有证据输出一个最终中文诊断，不要再请求工具。"
        )
    )
    final_response = llm.invoke(messages)
    final_text = str(final_response.content).strip()
    record_memory_event(memory_session, memory_thread, "assistant", "final", final_text)
    try:
        final_action = extract_json_object(final_text)
    except Exception:
        return final_text
    if final_action.get("action") == "finish":
        final_args = final_action.get("args", {}) or {}
        if isinstance(final_args, dict):
            final = str(final_args.get("answer", "")).strip()
            upsert_memory_summary(
                memory_session,
                memory_thread,
                f"{datetime.now().date()}: tool-loop reached max steps. {final[:1000]}",
                next_action={"type": "review_final_after_max_steps", "answer": final[:1000]},
            )
            return final
    return final_text


def main() -> int:
    args = parse_args()
    skills = discover_skills(Path(args.skills_dir))

    if args.list_skills:
        for skill in skills:
            print(f"{skill.name}\t{skill.description}")
        return 0

    question = " ".join(args.question).strip()
    selected_skill = select_skill(skills, question, args.skill)
    if (args.summarize_latest_report or args.run_chat_test_and_summarize) and not args.reference and not args.all_references:
        args.reference.append("npc-debug-rubric")
    references = collect_references(selected_skill, args)
    evidence = collect_evidence([*args.file, *args.report])

    memory_session_factory: sessionmaker[Session] | None = None
    memory_session: Session | None = None
    memory_thread: AgentMemoryThread | None = None
    memory_text = ""
    if not args.disable_memory:
        try:
            memory_session_factory = create_memory_session_factory(args.memory_db_url)
            memory_session = memory_session_factory()
            memory_thread = get_or_create_memory_thread(
                memory_session,
                skill_name=selected_skill.name,
                project_id=args.project_id,
                thread_id=args.memory_thread_id,
                title=args.memory_title,
            )
            memory_text = format_memory_for_prompt(get_memory_summary(memory_session, memory_thread))
            print(f"[memory-thread] {memory_thread.id}", file=sys.stderr)
            record_memory_event(
                memory_session,
                memory_thread,
                "user",
                "request",
                question or "<no question>",
                {
                    "mode": {
                        "tool_loop": args.tool_loop,
                        "summarize_latest_report": args.summarize_latest_report,
                        "run_chat_test_and_summarize": args.run_chat_test_and_summarize,
                    },
                    "reports": args.report,
                    "files": args.file,
                },
            )
        except Exception as exc:
            print(f"[memory-error] {type(exc).__name__}: {exc}", file=sys.stderr)
            print("Postgres memory is required by default. Use --disable-memory to run without memory.", file=sys.stderr)
            return 2

    print(f"[skill] {selected_skill.name}", file=sys.stderr)
    if references:
        print("[references] " + ", ".join(path.name for path, _ in references), file=sys.stderr)
    print(f"[model] {args.model} @ {args.base_url}", file=sys.stderr)

    llm = ChatOllama(
        model=args.model,
        base_url=args.base_url,
        temperature=args.temperature,
        num_ctx=args.num_ctx,
        client_kwargs={"trust_env": False},
    )

    if args.run_chat_test_and_summarize:
        report_path, summary_path, exit_code = run_chat_test_and_summarize(
            llm,
            selected_skill,
            references,
            args,
            memory_text=memory_text,
        )
        record_memory_event(
            memory_session,
            memory_thread,
            "tool",
            "run_chat_test_and_summarize",
            f"report={report_path}\nsummary={summary_path}\nexit_code={exit_code}",
            {"report_path": str(report_path), "summary_path": str(summary_path), "exit_code": exit_code},
        )
        upsert_memory_summary(
            memory_session,
            memory_thread,
            f"{datetime.now().date()}: ran chat test and summary. report={project_relative_path(report_path)}, summary={project_relative_path(summary_path)}, exit_code={exit_code}",
            known_issue={"type": "chat_test_exit", "exit_code": exit_code, "report": str(project_relative_path(report_path))},
            next_action={"type": "review_summary", "summary": str(project_relative_path(summary_path))},
        )
        answer = "\n".join(
            [
                f"Chat test report: {report_path}",
                f"Summary report: {summary_path}",
                f"Chat test exit code: {exit_code}",
            ]
        )
    elif args.summarize_latest_report:
        output_path = generate_latest_report_summary(llm, selected_skill, references, args, memory_text=memory_text)
        record_memory_event(
            memory_session,
            memory_thread,
            "assistant",
            "summarize_latest_report",
            f"Wrote summary report: {output_path}",
            {"summary_path": str(output_path)},
        )
        upsert_memory_summary(
            memory_session,
            memory_thread,
            f"{datetime.now().date()}: summarized latest report into {project_relative_path(output_path)}",
            next_action={"type": "review_summary", "summary": str(project_relative_path(output_path))},
        )
        answer = f"Wrote summary report: {output_path}"
    elif args.tool_loop:
        answer = run_tool_loop(
            llm,
            selected_skill,
            references,
            question,
            evidence,
            args,
            memory_text=memory_text,
            memory_session=memory_session,
            memory_thread=memory_thread,
        )
    else:
        response = llm.invoke(
            [
                SystemMessage(content=build_system_prompt(selected_skill, references, memory_text=memory_text)),
                HumanMessage(content=build_user_prompt(question, evidence)),
            ]
        )
        answer = str(response.content).strip()
        record_memory_event(memory_session, memory_thread, "assistant", "answer", answer)
        upsert_memory_summary(
            memory_session,
            memory_thread,
            f"{datetime.now().date()}: answered request. {answer[:1000]}",
        )

    print(answer)
    if memory_session is not None:
        memory_session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
