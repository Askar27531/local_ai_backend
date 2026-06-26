from __future__ import annotations

from pathlib import Path

import pytest

from skill_app.domain.errors import ToolPermissionDenied
from skill_app.services.policy_service import load_policy, permissions_for_agent, require_tool_permission


def test_policy_loads_agent_permissions(tmp_path):
    path = Path(tmp_path) / "policy.yaml"
    path.write_text("agents:\n  planner:\n    allow: [read_project_file]\n", encoding="utf-8")

    policy = load_policy(path)

    assert permissions_for_agent("planner", policy) == ["read_project_file"]
    assert require_tool_permission("planner", "read_project_file", policy) == ["read_project_file"]


def test_policy_denies_unlisted_tool():
    with pytest.raises(ToolPermissionDenied):
        require_tool_permission("planner", "write_workspace_file")
