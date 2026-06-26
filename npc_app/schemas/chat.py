from pydantic import BaseModel


class SearchItem(BaseModel):
    title: str = ""
    url: str = ""
    snippet: str = ""
