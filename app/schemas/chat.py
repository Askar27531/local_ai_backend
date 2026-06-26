from typing import List, Optional

from pydantic import BaseModel, Field


class SearchItem(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""


class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    thread_id: Optional[str] = Field(default=None, description="当前对话线程 ID；为空时后端自动创建")


class ChatResponse(BaseModel):
    answer: str
    model: str
    thread_id: str
    used_search: bool = Field(default=False, description="本轮是否使用了搜索工具")
    sources: List[SearchItem] = Field(default_factory=list, description="搜索来源")
