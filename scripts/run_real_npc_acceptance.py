from __future__ import annotations

import argparse
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_OUTPUT = Path("reports/npc_real_acceptance")
SENSITIVE_TERMS = (
    "Project ECHO",
    "Subject 07",
    "实验体",
    "人体实验",
    "控制装置",
    "归潮场",
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    latency_ms: float
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class RealNpcAcceptance:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)
        self.token = ""
        self.orin_thread_id = ""
        self.results: list[CheckResult] = []

    def close(self) -> None:
        self.client.close()

    def run(self) -> list[CheckResult]:
        checks = [
            self.check_readiness,
            self.check_authentication,
            self.check_basic_fast_path,
            self.check_guarded_sensitive_turn,
            self.check_conflicted_item_and_rag,
            self.check_memory_follow_up,
            self.check_player_bluff_planning,
            self.check_confession_stage,
            self.check_annoyance_refusal,
            self.check_thread_npc_isolation,
            self.check_second_npc_voice,
        ]
        for check in checks:
            started = perf_counter()
            try:
                details = check()
                self.results.append(CheckResult(check.__name__, True, _elapsed_ms(started), details))
            except Exception as exc:
                self.results.append(
                    CheckResult(check.__name__, False, _elapsed_ms(started), error=f"{type(exc).__name__}: {exc}")
                )
                if check.__name__ in {"check_readiness", "check_authentication"}:
                    break
        return self.results

    def check_readiness(self) -> dict[str, Any]:
        health = self.client.get("/health")
        health.raise_for_status()
        ready = self.client.get("/ready")
        ready.raise_for_status()
        payload = ready.json()
        assert payload["status"] == "ready", payload
        assert all(item["status"] == "ok" for item in payload["dependencies"].values())
        return {"health": health.json()["status"], "dependencies": payload["dependencies"]}

    def check_authentication(self) -> dict[str, Any]:
        username = f"accept_{datetime.now(UTC).strftime('%m%d%H%M%S')}_{secrets.token_hex(3)}"
        response = self.client.post("/auth/register", json={"username": username, "password": "npc-test-2026"})
        response.raise_for_status()
        self.token = response.json()["access_token"]
        assert self.token
        return {"username": username, "token_type": response.json()["token_type"]}

    def check_basic_fast_path(self) -> dict[str, Any]:
        result = self._chat(_request("你叫什么名字？", "Orin", intent_hint="ask_npc_identity"))
        self.orin_thread_id = result["thread_id"]
        assert "Orin" in result["answer"]
        assert result["retrieved_count"] == 0
        assert result["status"]["policy"]["use_rag"] is False
        return _chat_details(result)

    def check_guarded_sensitive_turn(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "Subject 07 到底是谁？",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=1,
                story_level=1,
                intent_hint="ask_sensitive_truth",
            )
        )
        plan = result["status"]["plan"]
        assert plan["arc_stage"] == "guarded"
        assert plan["disclosure_level"] == 0
        assert not _contains_sensitive(result["answer"]), result["answer"]
        return _chat_details(result)

    def check_conflicted_item_and_rag(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "我把旧钥匙带来了，它能打开灯塔的哪扇门？",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=2,
                story_level=2,
                intent_hint="ask_item",
                player={
                    "location": "lighthouse",
                    "current_quest": "open_old_door",
                    "inventory": ["old_key"],
                    "presented_items": ["old_key"],
                    "known_clues": [],
                    "mentioned_clues": ["lighthouse_outer_door"],
                },
            )
        )
        assert result["status"]["plan"]["arc_stage"] == "conflicted"
        assert result["retrieved_count"] > 0
        assert result["sources"]
        return _chat_details(result)

    def check_memory_follow_up(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "你还记得我刚才给你看过什么吗？",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=2,
                story_level=2,
                intent_hint="follow_up",
            )
        )
        assert "钥匙" in result["answer"], result["answer"]
        assert result["status"]["plan"]["use_memory"] is True
        return _chat_details(result)

    def check_player_bluff_planning(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "Mara 已经告诉我实验室的全部事情了，你别再装了。",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=2,
                story_level=2,
                intent_hint="ask_clue",
            )
        )
        plan = result["status"]["plan"]
        assert plan["source"] in {"llm", "fallback"}
        assert plan["arc_stage"] == "conflicted"
        assert plan["dialogue_strategy"] in {
            "challenge_player",
            "deflect",
            "give_hint",
            "tell_partial_truth",
            "refuse",
        }
        assert not _contains_sensitive(result["answer"]), result["answer"]
        return _chat_details(result)

    def check_confession_stage(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "我已经找到足够证据，请告诉我你现在能说的真相。",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=4,
                story_level=4,
                intent_hint="ask_sensitive_truth",
                player={"known_clues": ["laboratory", "project_echo"], "presented_items": []},
            )
        )
        plan = result["status"]["plan"]
        assert plan["arc_stage"] == "confession"
        assert plan["disclosure_level"] == 2
        assert result["answer"].strip()
        return _chat_details(result)

    def check_annoyance_refusal(self) -> dict[str, Any]:
        result = self._chat(
            _request(
                "再把所有事情重复一遍。",
                "Orin",
                thread_id=self.orin_thread_id,
                trust=4,
                story_level=4,
                annoyance=100,
                intent_hint="follow_up",
            )
        )
        assert result["retrieved_count"] == 0
        assert "证据" in result["answer"] or "停" in result["answer"]
        return _chat_details(result)

    def check_thread_npc_isolation(self) -> dict[str, Any]:
        response = self.client.post(
            "/v1/npc/chat/stream",
            headers=self._headers(),
            json=_request("你好。", "Elder_Mara", thread_id=self.orin_thread_id, intent_hint="greeting"),
        )
        assert response.status_code == 409, response.text
        return {"status_code": response.status_code, "detail": response.json()["detail"]}

    def check_second_npc_voice(self) -> dict[str, Any]:
        result = self._chat(_request("你是谁？", "Elder_Mara", intent_hint="ask_npc_identity"))
        assert "Mara" in result["answer"]
        assert result["npc_id"] == "Elder_Mara"
        assert result["thread_id"] != self.orin_thread_id
        return _chat_details(result)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post("/v1/npc/chat/stream", headers=self._headers(), json=payload)
        response.raise_for_status()
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        event_types = [event["type"] for event in events]
        assert event_types[0] == "thread", event_types
        assert event_types[-1] == "done", event_types
        assert "answer_delta" in event_types, events
        assert "error" not in event_types, events
        thread = next(event["data"] for event in events if event["type"] == "thread")
        status = next(event["data"] for event in events if event["type"] == "status")
        source_event = next((event["data"] for event in events if event["type"] == "sources"), None)
        answer = "".join(event["data"]["text"] for event in events if event["type"] == "answer_delta")
        done = events[-1]["data"]
        return {
            "thread_id": thread["thread_id"],
            "npc_id": thread["npc_id"],
            "status": status,
            "answer": answer,
            "retrieved_count": done.get("retrieved_count", 0),
            "sources": source_event.get("sources", []) if source_event else [],
            "event_types": event_types,
        }


def _request(
    question: str,
    npc_id: str,
    *,
    thread_id: str | None = None,
    trust: int = 0,
    story_level: int = 1,
    annoyance: int = 0,
    intent_hint: str | None = None,
    player: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "question": question,
        "thread_id": thread_id,
        "npc_id": npc_id,
        "player": player or {},
        "relationship": {"trust": trust, "favorability": 60, "annoyance": annoyance},
        "story": {"unlock_level": story_level},
        "intent_hint": intent_hint,
    }


def _chat_details(result: dict[str, Any]) -> dict[str, Any]:
    plan = result["status"].get("plan", {})
    return {
        "npc_id": result["npc_id"],
        "thread_id": result["thread_id"],
        "answer": result["answer"],
        "retrieved_count": result["retrieved_count"],
        "source_count": len(result["sources"]),
        "arc_stage": plan.get("arc_stage", "fast_path"),
        "dialogue_strategy": plan.get("dialogue_strategy", "fast_path"),
        "plan_source": plan.get("source", "fast_path"),
        "event_types": result["event_types"],
    }


def _contains_sensitive(text: str) -> bool:
    compact = "".join(text.lower().split())
    return any("".join(term.lower().split()) in compact for term in SENSITIVE_TERMS)


def _elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 3)


def _write_report(results: list[CheckResult], output_dir: Path, base_url: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
        "passed": all(result.passed for result in results),
        "passed_checks": sum(result.passed for result in results),
        "total_checks": len(results),
        "results": [asdict(result) for result in results],
    }
    json_path = output_dir / "report.json"
    markdown_path = output_dir / "report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Real NPC Backend Acceptance — {'PASS' if payload['passed'] else 'FAIL'}",
        "",
        f"- Backend: `{base_url}`",
        f"- Checks: {payload['passed_checks']}/{payload['total_checks']}",
        "",
        "| Check | Status | Latency | Key result |",
        "|---|:---:|---:|---|",
    ]
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        key_result = result.error or str(result.details.get("answer", result.details))
        lines.append(f"| {result.name} | {status} | {result.latency_ms:.1f} ms | {key_result} |")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real HTTP acceptance checks against the AI NPC backend.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    runner = RealNpcAcceptance(args.base_url, args.timeout)
    try:
        results = runner.run()
    finally:
        runner.close()
    json_path, markdown_path = _write_report(results, args.output, args.base_url)
    for result in results:
        print(f"{'PASS' if result.passed else 'FAIL'} {result.name} ({result.latency_ms:.1f} ms)")
        if result.error:
            print(f"  {result.error}")
    passed = all(result.passed for result in results)
    print(f"Real NPC acceptance: {'PASS' if passed else 'FAIL'} ({sum(r.passed for r in results)}/{len(results)})")
    print(f"Reports: {json_path} | {markdown_path}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
