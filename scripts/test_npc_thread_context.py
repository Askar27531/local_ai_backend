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
DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"


@dataclass
class StreamResult:
    npc_id: str
    question: str
    thread_id: str | None = None
    title: str = ""
    annoyance_percent: int = 0
    favorability_percent: int = 50
    npc_interaction_count: int = 0
    answer: str = ""
    source_count: int = 0
    saw_done: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def require_requests() -> Any:
    if requests is None:
        raise RuntimeError("Missing dependency: requests. Install it before running this script.") from REQUESTS_IMPORT_ERROR
    return requests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate npc_app per-NPC thread isolation and repeated-question annoyance behavior."
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"NPC app base URL. Default: {DEFAULT_BASE_URL}")
    parser.add_argument("--username", default=None, help="Test username. Defaults to a timestamped fresh user.")
    parser.add_argument("--password", default="123456", help="Test password.")
    parser.add_argument("--token", default=None, help="Bearer token. If omitted, the script registers/logs in.")
    parser.add_argument("--timeout", type=float, default=120.0, help="HTTP timeout in seconds.")
    parser.add_argument("--skip-health", action="store_true", help="Skip /health check.")
    parser.add_argument("--annoyance-npc", default="Karo", help="NPC used for repeated-question test.")
    parser.add_argument("--annoyance-turns", type=int, default=6, help="Total turns sent to the same NPC.")
    parser.add_argument("--min-annoyance-percent", type=int, default=90, help="Expected minimum final annoyance percent.")
    parser.add_argument("--unlock-level", type=int, default=1, help="Story progress sent during annoyance simulation.")
    parser.add_argument("--favorability-percent", type=int, default=20, help="Favorability sent during annoyance simulation.")
    parser.add_argument(
        "--strict-annoyance",
        action="store_true",
        help="Treat missing annoyance keywords as a failure instead of a warning.",
    )
    parser.add_argument(
        "--output-md",
        nargs="?",
        const="auto",
        default="auto",
        help="Write a Markdown report under reports/ by default. Pass a path to customize.",
    )
    parser.add_argument("--no-output-md", action="store_const", const=None, dest="output_md")
    return parser.parse_args()


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    http = require_requests()
    response = http.request(method, url, **kwargs)
    response.raise_for_status()
    return response.json()


def check_health(args: argparse.Namespace) -> None:
    payload = request_json("GET", args.base_url.rstrip("/") + "/health", timeout=args.timeout)
    print("[health]", json.dumps(payload, ensure_ascii=False))


def get_token(args: argparse.Namespace) -> str:
    if args.token:
        print("[auth] using provided bearer token")
        return str(args.token)

    http = require_requests()
    username = args.username or "npc_context_test_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    args.username = username

    register_url = args.base_url.rstrip("/") + "/auth/register"
    register_response = http.post(
        register_url,
        json={"username": username, "password": args.password},
        timeout=args.timeout,
    )
    if register_response.status_code == 200:
        print(f"[auth] registered test user: {username}")
    elif register_response.status_code == 400:
        print(f"[auth] user already exists: {username}")
    else:
        register_response.raise_for_status()

    login_url = args.base_url.rstrip("/") + "/auth/login"
    login_response = http.post(
        login_url,
        json={"username": username, "password": args.password},
        timeout=args.timeout,
    )
    login_response.raise_for_status()
    print(f"[auth] logged in: {username}")
    return str(login_response.json()["access_token"])


def build_payload(
    npc_id: str,
    question: str,
    thread_id: str | None = None,
    unlocked_story_level: int = 2,
    favorability_percent: int = 50,
    annoyance_percent: int = 0,
    npc_interaction_count: int = 0,
) -> dict[str, Any]:
    return {
        "question": question,
        "thread_id": thread_id,
        "npc_id": npc_id,
        "unlocked_story_level": unlocked_story_level,
        "trust_level": 1,
        "favorability_percent": favorability_percent,
        "annoyance_percent": annoyance_percent,
        "npc_interaction_count": npc_interaction_count,
        "top_k": 5,
        "player_id": "npc_context_test_player",
        "player_location": "village",
        "current_quest": "npc_thread_context_regression",
        "inventory": ["broken_badge"],
        "visited_locations": ["beach", "village", "dock"],
        "known_clues": ["cannot_leave_island"],
    }


def stream_chat(
    args: argparse.Namespace,
    token: str,
    npc_id: str,
    question: str,
    thread_id: str | None = None,
    unlocked_story_level: int = 2,
    favorability_percent: int = 50,
    annoyance_percent: int = 0,
    npc_interaction_count: int = 0,
) -> StreamResult:
    http = require_requests()
    url = args.base_url.rstrip("/") + "/npc/chat/stream"
    headers = {"Authorization": f"Bearer {token}"}
    result = StreamResult(npc_id=npc_id, question=question)
    answer_parts: list[str] = []

    print(f"[chat] npc={npc_id} thread={thread_id or '<new>'} question={question}")
    with http.post(
        url,
        json=build_payload(
            npc_id,
            question,
            thread_id,
            unlocked_story_level=unlocked_story_level,
            favorability_percent=favorability_percent,
            annoyance_percent=annoyance_percent,
            npc_interaction_count=npc_interaction_count,
        ),
        headers=headers,
        stream=True,
        timeout=args.timeout,
    ) as response:
        response.raise_for_status()
        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue

            event = json.loads(raw_line)
            result.events.append(event)
            event_type = event.get("type")
            data = event.get("data", {})

            if event_type == "thread":
                result.thread_id = data.get("thread_id")
                result.title = str(data.get("title", ""))
                result.annoyance_percent = int(data.get("annoyance_percent", 0))
                result.favorability_percent = int(data.get("favorability_percent", 50))
                result.npc_interaction_count = int(data.get("npc_interaction_count", 0))
                print(
                    f"  [thread] {result.thread_id} title={result.title} "
                    f"annoyance={result.annoyance_percent}% "
                    f"favorability={result.favorability_percent}% "
                    f"count={result.npc_interaction_count}"
                )
            elif event_type == "sources":
                result.source_count = int(data.get("retrieved_count", 0))
            elif event_type == "answer_delta":
                answer_parts.append(str(data.get("text", "")))
            elif event_type == "done":
                result.saw_done = True
            elif event_type == "error":
                raise RuntimeError(str(data.get("message", "stream error")))

    result.answer = "".join(answer_parts).strip()
    print(f"  [done] answer_len={len(result.answer)} sources={result.source_count}")
    return result


def fetch_records(args: argparse.Namespace, token: str, thread_id: str) -> list[dict[str, Any]]:
    return list(
        request_json(
            "GET",
            args.base_url.rstrip("/") + f"/threads/{thread_id}/records",
            headers={"Authorization": f"Bearer {token}"},
            timeout=args.timeout,
        )
    )


def fetch_threads(args: argparse.Namespace, token: str) -> list[dict[str, Any]]:
    return list(
        request_json(
            "GET",
            args.base_url.rstrip("/") + "/threads",
            headers={"Authorization": f"Bearer {token}"},
            timeout=args.timeout,
        )
    )


def check_thread_isolation(
    args: argparse.Namespace,
    token: str,
) -> tuple[list[CheckResult], dict[str, StreamResult]]:
    karo_question = "我刚在码头绕了一圈，为什么又回到这里？"
    lira_question = "我手心碰到蓝色小石头后有点发热，你见过这种症状吗？"

    karo = stream_chat(args, token, "Karo", karo_question)
    if not karo.thread_id:
        return [CheckResult("thread event: Karo", False, "Karo stream did not return a thread_id.")], {"Karo": karo}

    lira = stream_chat(args, token, "Lira", lira_question, thread_id=karo.thread_id)
    if not lira.thread_id:
        return [CheckResult("thread event: Lira", False, "Lira stream did not return a thread_id.")], {"Karo": karo, "Lira": lira}

    checks = [
        CheckResult(
            "different npc gets different thread",
            karo.thread_id != lira.thread_id,
            f"Karo thread={karo.thread_id}; Lira thread={lira.thread_id}",
        )
    ]

    karo_records = fetch_records(args, token, karo.thread_id)
    lira_records = fetch_records(args, token, lira.thread_id)
    checks.extend(
        [
            CheckResult(
                "Karo records keep Karo question",
                any(record.get("question") == karo_question for record in karo_records),
                f"Karo record questions={[record.get('question') for record in karo_records]}",
            ),
            CheckResult(
                "Karo records do not include Lira question",
                all(record.get("question") != lira_question for record in karo_records),
                f"Karo record questions={[record.get('question') for record in karo_records]}",
            ),
            CheckResult(
                "Lira records keep Lira question",
                any(record.get("question") == lira_question for record in lira_records),
                f"Lira record questions={[record.get('question') for record in lira_records]}",
            ),
            CheckResult(
                "Lira records do not include Karo question",
                all(record.get("question") != karo_question for record in lira_records),
                f"Lira record questions={[record.get('question') for record in lira_records]}",
            ),
        ]
    )

    thread_by_id = {thread.get("id"): thread for thread in fetch_threads(args, token)}
    checks.extend(
        [
            CheckResult(
                "Karo thread response has npc_id",
                thread_by_id.get(karo.thread_id, {}).get("npc_id") == "Karo",
                json.dumps(thread_by_id.get(karo.thread_id, {}), ensure_ascii=False),
            ),
            CheckResult(
                "Lira thread response has npc_id",
                thread_by_id.get(lira.thread_id, {}).get("npc_id") == "Lira",
                json.dumps(thread_by_id.get(lira.thread_id, {}), ensure_ascii=False),
            ),
        ]
    )

    return checks, {"Karo": karo, "Lira": lira}


def annoyance_markers(npc_id: str) -> tuple[str, ...]:
    markers_by_npc = {
        "Karo": ("说过", "别", "够了", "新线索", "证据", "码头", "旧规矩", "重复"),
        "Lira": ("说过", "重复", "样本", "症状", "接触时间", "新", "记录"),
        "Orin": ("说过", "重复", "别", "门", "锁", "规矩", "证据"),
        "Nia": ("说过", "不知道", "别问", "害怕", "还能说什么"),
        "Venn": ("说过", "重复", "空白", "证据", "别", "想不起来"),
        "Elder_Mara": ("说过", "孩子", "禁忌", "代价", "不要", "新线索"),
        "Lab_Terminal": ("重复查询", "权限", "字段", "索引", "未变化"),
    }
    return markers_by_npc.get(npc_id, ("说过", "重复", "新线索", "证据", "不要"))


def simulated_annoyance_percent(
    npc_id: str,
    completed_turns: int,
    unlocked_story_level: int,
    favorability_percent: int,
) -> int:
    base_growth_by_npc = {
        "Orin": 24,
        "Nia": 22,
        "Karo": 20,
        "Venn": 17,
        "Lira": 14,
        "Elder_Mara": 12,
        "Lab_Terminal": 8,
    }
    base_growth = base_growth_by_npc.get(npc_id, 15)

    progress_multiplier_by_level = {
        0: 1.35,
        1: 1.25,
        2: 1.05,
        3: 0.85,
        4: 0.65,
        5: 0.50,
    }
    progress_multiplier = progress_multiplier_by_level.get(unlocked_story_level, 1.0)
    favorability_multiplier = 1.20 - (min(100, max(0, favorability_percent)) / 100 * 0.70)
    per_turn_growth = base_growth * progress_multiplier * favorability_multiplier
    return min(100, round(completed_turns * per_turn_growth))


def check_repeated_question_annoyance(
    args: argparse.Namespace,
    token: str,
) -> tuple[list[CheckResult], list[StreamResult]]:
    npc_id = args.annoyance_npc
    thread_id: str | None = None
    results: list[StreamResult] = []

    for turn in range(1, args.annoyance_turns + 1):
        question = f"第 {turn} 次问你：关于海雾和离岛这件事，你还能再解释一遍吗？"
        annoyance_percent = simulated_annoyance_percent(
            npc_id=npc_id,
            completed_turns=turn - 1,
            unlocked_story_level=args.unlock_level,
            favorability_percent=args.favorability_percent,
        )
        result = stream_chat(
            args,
            token,
            npc_id,
            question,
            thread_id=thread_id,
            unlocked_story_level=args.unlock_level,
            favorability_percent=args.favorability_percent,
            annoyance_percent=annoyance_percent,
            npc_interaction_count=turn - 1,
        )
        results.append(result)
        thread_id = result.thread_id or thread_id

    final = results[-1]
    markers = annoyance_markers(npc_id)
    matched = [marker for marker in markers if marker in final.answer]
    detail = (
        f"matched={matched or 'none'}; markers={markers}; "
        f"final_answer={final.answer}"
    )
    passed = bool(matched)
    if not passed and not args.strict_annoyance:
        detail = "WARNING only unless --strict-annoyance is set. " + detail
        passed = True

    return [
        CheckResult(
            "same npc repeated questions complete",
            all(result.saw_done and result.thread_id == thread_id for result in results),
            f"thread_id={thread_id}; turns={len(results)}",
        ),
        CheckResult(
            "annoyance percent rises fast enough",
            final.annoyance_percent >= args.min_annoyance_percent,
            (
                f"final_annoyance={final.annoyance_percent}%; "
                f"expected>={args.min_annoyance_percent}%; "
                f"turns={len(results)}"
            ),
        ),
        CheckResult(
            "final answer shows annoyance or boundary markers",
            passed,
            detail,
        ),
    ], results


def resolve_report_path(output_md: str | Path) -> Path:
    raw_path = Path(output_md)
    if str(output_md) == "auto":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return DEFAULT_REPORT_DIR / f"npc_thread_context_{timestamp}.md"
    if raw_path.suffix.lower() != ".md":
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return raw_path / f"npc_thread_context_{timestamp}.md"
    return raw_path


def write_report(
    output_md: str | Path,
    args: argparse.Namespace,
    checks: list[CheckResult],
    streams: list[StreamResult],
) -> None:
    path = resolve_report_path(output_md)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# NPC Thread Context Regression Report",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Base URL: `{args.base_url}`",
        f"- Username: `{args.username or ''}`",
        f"- Annoyance NPC: `{args.annoyance_npc}`",
        f"- Annoyance Turns: `{args.annoyance_turns}`",
        f"- Min Annoyance Percent: `{args.min_annoyance_percent}`",
        f"- Unlock Level: `{args.unlock_level}`",
        f"- Favorability Percent: `{args.favorability_percent}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        detail = check.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {status} | {check.name} | {detail} |")

    lines.extend(["", "## Streams", ""])
    for stream in streams:
        lines.extend(
            [
                f"### {stream.npc_id} / {stream.thread_id or ''}",
                "",
                f"- Question: {stream.question}",
                f"- Saw Done: `{stream.saw_done}`",
                f"- Source Count: `{stream.source_count}`",
                f"- Interaction Count: `{stream.npc_interaction_count}`",
                f"- Annoyance Percent: `{stream.annoyance_percent}`",
                f"- Favorability Percent: `{stream.favorability_percent}`",
                "",
                "```text",
                stream.answer,
                "```",
                "",
            ]
        )

    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[report] wrote {path.resolve()}")


def print_checks(checks: list[CheckResult]) -> None:
    print("=" * 80)
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"[{status}] {check.name}: {check.detail}")


def main() -> int:
    args = parse_args()
    checks: list[CheckResult] = []
    streams: list[StreamResult] = []

    try:
        if not args.skip_health:
            check_health(args)
        token = get_token(args)

        isolation_checks, isolation_streams = check_thread_isolation(args, token)
        checks.extend(isolation_checks)
        streams.extend(isolation_streams.values())

        annoyance_checks, annoyance_streams = check_repeated_question_annoyance(args, token)
        checks.extend(annoyance_checks)
        streams.extend(annoyance_streams)
    except (RequestException, RuntimeError, json.JSONDecodeError) as exc:
        checks.append(CheckResult("script execution", False, str(exc)))

    print_checks(checks)
    if args.output_md:
        write_report(args.output_md, args, checks, streams)

    return 0 if all(check.passed for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
