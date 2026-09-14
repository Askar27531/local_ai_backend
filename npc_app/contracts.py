"""Unity 与 NPC Runtime 之间的输入输出契约。

Pydantic 在请求进入业务层前完成类型、长度、枚举和范围校验。后续模块因此可以把
这些结构化字段视为 Unity 提供的权威游戏状态，而不是从玩家自然语言中猜测事实。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

MAX_RETRIEVAL_TOP_K = 6


class NpcId(StrEnum):
    """Runtime 正式支持的 NPC 标识；枚举值同时用于线程和知识过滤。"""

    KARO = "Karo"
    LIRA = "Lira"
    ORIN = "Orin"
    NIA = "Nia"
    VENN = "Venn"
    ELDER_MARA = "Elder_Mara"
    LAB_TERMINAL = "Lab_Terminal"


# 游戏状态标识统一去除首尾空白并限制长度，防止异常客户端输入无限放大检索查询和 Prompt。
GameStateValue = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


class UnityPlayerState(BaseModel):
    """Unity 提供的权威玩家状态。

    ``inventory`` 仅表示玩家持有物品；``presented_items`` 表示本轮确实展示给 NPC 的
    物品，只有后者能作为确认事实写入长期记忆。``known_clues`` 是已解锁线索，
    ``mentioned_clues`` 则是本轮主动提及、应提高检索相关性的线索。
    """

    location: GameStateValue | None = None
    current_quest: GameStateValue | None = None
    inventory: list[GameStateValue] = Field(default_factory=list, max_length=100)
    presented_items: list[GameStateValue] = Field(default_factory=list, max_length=20)
    visited_locations: list[GameStateValue] = Field(default_factory=list, max_length=100)
    known_clues: list[GameStateValue] = Field(default_factory=list, max_length=100)
    mentioned_clues: list[GameStateValue] = Field(default_factory=list, max_length=30)

    @field_validator("inventory", "presented_items", "visited_locations", "known_clues", "mentioned_clues")
    @classmethod
    def reject_duplicate_values(cls, values: list[str]) -> list[str]:
        """在 API 边界拒绝会污染检索条件或记忆证据的非法列表值。"""
        if any(any(ord(char) < 32 for char in value) for value in values):
            raise ValueError("游戏状态列表不能包含控制字符")
        if len(values) != len(set(values)):
            raise ValueError("游戏状态列表不能包含重复值")
        return values


class UnityRelationshipState(BaseModel):
    """当前 NPC 与玩家的关系状态。

    trust 参与角色阶段与披露强度；favorability 主要影响语气温度；annoyance 影响回答
    长度并可触发硬拒答。三者都不能单独突破 Unity 给出的剧情解锁等级。
    """

    trust: int = Field(default=0, ge=0, le=5)
    favorability: int = Field(default=50, ge=0, le=100)
    annoyance: int = Field(default=0, ge=0, le=100)


class UnityStoryState(BaseModel):
    """Unity 判定的全局剧情解锁等级，是防剧透过滤的权威上限。"""

    unlock_level: int = Field(default=1, ge=0, le=5)


class UnityNpcTurnRequest(BaseModel):
    """单轮 NPC 对话契约。

    ``thread_id`` 可省略，由服务端按“用户 + NPC”复用或创建；``intent_hint`` 只是
    Unity 的分类提示，敏感问题规则仍拥有更高优先级。用户身份、交互次数和检索 top_k
    不由客户端提交，避免客户端越权控制 Runtime。
    """

    question: str = Field(min_length=1, max_length=2000)
    thread_id: UUID | None = None
    npc_id: NpcId
    player: UnityPlayerState = Field(default_factory=UnityPlayerState)
    relationship: UnityRelationshipState = Field(default_factory=UnityRelationshipState)
    story: UnityStoryState = Field(default_factory=UnityStoryState)
    intent_hint: str | None = None


Username = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=2, max_length=50, pattern=r"^[\w.-]+$"),
]


class RegisterRequest(BaseModel):
    """账号注册输入；用户名格式和密码长度在 API 边界校验。"""

    username: Username
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    """账号登录输入，与注册使用相同的用户名和密码长度约束。"""

    username: Username
    password: str = Field(..., min_length=6, max_length=128)


class TokenResponse(BaseModel):
    """认证成功后返回的 Bearer Token 契约。"""

    access_token: str
    token_type: str = "bearer"
