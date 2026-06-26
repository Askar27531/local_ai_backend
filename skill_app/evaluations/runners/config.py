from __future__ import annotations

import yaml

from skill_app.agents.config_generator import NPCBehaviorConfigGenerator
from skill_app.tools.config_validation import validate_npc_behavior_yaml


def run_case(case: dict) -> dict:
    generator = NPCBehaviorConfigGenerator()
    first = generator.generate(
        task_id=case["id"],
        request=case["request"],
        repair_count=0,
        previous_content=None,
        validation_errors=[],
    )
    first_report = validate_npc_behavior_yaml(first.content)
    final = first
    repaired = False
    if not first_report.valid:
        repaired = True
        final = generator.generate(
            task_id=case["id"],
            request=case["request"],
            repair_count=1,
            previous_content=first.content,
            validation_errors=first_report.errors,
        )
    final_report = validate_npc_behavior_yaml(final.content)
    value = yaml.safe_load(final.content)
    actual = {
        "window_seconds": value["rules"]["repeated_question"]["window_seconds"],
        "base_increment": value["rules"]["repeated_question"]["base_increment"],
        "warning": value["thresholds"]["warning"],
        "refuse": value["thresholds"]["refuse"],
    }
    mismatches = {
        key: {"expected": expected, "actual": actual[key]}
        for key, expected in case["expected"].items()
        if actual[key] != expected
    }
    passed = final_report.valid and not mismatches
    return {
        "case_id": case["id"],
        "passed": passed,
        "metrics": {
            "schema_first_pass_rate": float(first_report.valid),
            "repair_success_rate": float(repaired and final_report.valid),
            "manual_modified_fields": float(len(mismatches)),
        },
        "details": {
            "first_errors": first_report.errors,
            "repaired": repaired,
            "mismatches": mismatches,
        },
    }
