from datetime import datetime
from typing import List

from pydantic import BaseModel, Field

from app.schemas.chat import SearchItem


class ThreadResponse(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime


class RenameThreadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


class ChatRecordItem(BaseModel):
    id: int
    question: str
    answer: str
    model: str
    used_search: bool
    sources: List[SearchItem] = Field(default_factory=list)
    created_at: datetime
