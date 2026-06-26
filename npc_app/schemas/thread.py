from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

from npc_app.schemas.chat import SearchItem


class ThreadResponse(BaseModel):
    id: str
    npc_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class ChatRecordItem(BaseModel):
    id: int
    question: str
    answer: str
    model: str
    used_search: bool
    sources: List[SearchItem] = Field(default_factory=list)
    created_at: datetime
