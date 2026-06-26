from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from skill_app.config import PROJECT_ROOT
from skill_app.domain.errors import ConfigurationError, ToolPermissionDenied
from skill_app.schemas.policy import ToolPolicy

DEFAULT_POLICY_PATH = PROJECT_ROOT / "skill_app" / "policies" / "default.yaml"


@lru_cache(maxsize=8)
def load_policy(path: Path = DEFAULT_POLICY_PATH) -> ToolPolicy:
    if not path.exists():
        raise ConfigurationError("Tool policy file does not exist.", {"path": str(path)})
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    try:
        return ToolPolicy.model_validate(raw)
    except Exception as exc:
        raise ConfigurationError("Tool policy is invalid.", {"path": str(path)}) from exc


def permissions_for_agent(agent_name: str, policy: ToolPolicy | None = None) -> list[str]:
    selected = policy or load_policy()
    agent = selected.agents.get(agent_name)
    return list(agent.allow) if agent is not None else []


def require_tool_permission(agent_name: str, tool_name: str, policy: ToolPolicy | None = None) -> list[str]:
    permissions = permissions_for_agent(agent_name, policy)
    if tool_name not in permissions:
        raise ToolPermissionDenied(
            f"Agent {agent_name} is not allowed to use tool {tool_name}.",
            {"agent_name": agent_name, "tool_name": tool_name},
        )
    return permissions
