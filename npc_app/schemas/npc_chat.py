from typing import List, Optional

from pydantic import BaseModel, Field


class NpcChatRequest(BaseModel):
    question: str = Field(..., description="Player question for the NPC")
    thread_id: Optional[str] = Field(default=None, description="Conversation thread ID")
    npc_id: str = Field(..., description="Current NPC ID, for example Karo")
    unlocked_story_level: int = Field(
        default=1,
        ge=0,
        le=5,
        description="Current unlocked story level",
    )
    trust_level: int = Field(
        default=0,
        ge=0,
        le=5,
        description="Current trust level with this NPC. 0 means stranger, 5 means trusted ally.",
    )
    npc_interaction_count: int = Field(
        default=0,
        ge=0,
        description="Prior completed chat turns with this NPC, supplied by the game client when available.",
    )
    annoyance_percent: int = Field(
        default=0,
        ge=0,
        le=100,
        description="Current NPC annoyance level supplied by the game client.",
    )
    favorability_percent: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Current NPC favorability toward the player, supplied by the game client.",
    )
    top_k: int = Field(default=5, ge=1, le=10, description="Milvus retrieval count")

    player_id: str = Field(default="demo_player", description="Player ID")
    player_location: Optional[str] = Field(default=None, description="Current player location")
    current_quest: Optional[str] = Field(default=None, description="Current quest")
    inventory: List[str] = Field(default_factory=list, description="Player inventory")
    visited_locations: List[str] = Field(default_factory=list, description="Visited locations")
    known_clues: List[str] = Field(default_factory=list, description="Known clues")


class NpcSourceItem(BaseModel):
    source_file: str = ""
    section_title: str = ""
    npc_id: str = ""
    unlock_level: int = 0
    topics: str = ""
    spoiler_level: str = ""
    score: float = 0.0
    snippet: str = ""
