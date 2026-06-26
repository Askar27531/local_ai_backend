# ============================================================================
# 搜索工具模块
# 功能：实现联网搜索功能，使用 DuckDuckGo 搜索引擎
# ============================================================================

from typing import List
from fastapi import HTTPException
from ddgs import DDGS  # DuckDuckGo 搜索库
from app.schemas.chat import SearchItem
from langchain_core.tools import tool
from app.tools.runtime import ToolRuntimeState
from langgraph.config import get_stream_writer


# ============================================================================
# 常量定义
# ============================================================================
# 每次搜索最多返回的结果数量
MAX_SEARCH_RESULTS = 5

def emit_custom_event(event: dict) -> None:
    """
    向 LangGraph stream_mode='custom' 输出自定义事件。
    如果当前不在 LangGraph 流式上下文中，则静默跳过。
    """
    try:
        writer = get_stream_writer()
        writer(event)
    except Exception:
        pass


# ============================================================================
# 创建受控联网搜索工具
# ============================================================================
def create_controlled_web_search_tool(state: ToolRuntimeState):
    """
    创建一个受控的联网搜索工具。
    
    这个函数返回一个工具函数，该函数会：
    1. 标记搜索工具已被使用
    2. 执行网络搜索
    3. 保存搜索来源
    4. 格式化搜索结果返回给 LLM
    
    参数：
        state: ToolRuntimeState 对象，用于追踪工具使用和搜索来源
    
    返回：
        一个被 @tool 装饰的函数，可以被 LangChain Agent 调用
    """
    
    # 定义工具函数
    def controlled_web_search(query: str) -> str:
        """
        当用户问题需要最新信息、官网资料、政策、新闻、价格或外部资料时执行联网搜索。
        """

        state.mark_used("controlled_web_search")

        emit_custom_event({
            "type": "tool_status",
            "tool": "controlled_web_search",
            "message": f"正在联网搜索：{query}",
            "query": query,
        })

        new_sources = web_search(query=query, max_results=5)

        state.add_sources(new_sources)

        emit_custom_event({
            "type": "tool_result",
            "tool": "controlled_web_search",
            "message": f"搜索完成，得到 {len(new_sources)} 条结果。",
            "query": query,
            "count": len(new_sources),
            "sources": [
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                }
                for item in new_sources
            ],
        })

        return format_search_results(new_sources)
    
    # 使用 tool() 函数将函数转换为 LangChain 工具
    return tool(controlled_web_search)


# ============================================================================
# 执行网络搜索
# ============================================================================
def web_search(query: str, max_results: int = MAX_SEARCH_RESULTS) -> List[SearchItem]:
    """
    使用 DuckDuckGo 搜索引擎执行网络搜索。
    
    参数：
        query: 搜索关键词
        max_results: 最多返回的结果数量
    
    返回：
        SearchItem 对象列表，每个对象包含标题、URL、摘要
    
    异常：
        HTTPException: 如果搜索失败，抛出 HTTP 500 错误
    """
    try:
        # 使用 DuckDuckGo 搜索
        # DDGS() 是 DuckDuckGo 搜索的客户端
        with DDGS() as ddgs:
            # .text() 方法执行文本搜索
            results = ddgs.text(query, max_results=max_results)

        # 将搜索结果转换为 SearchItem 对象列表
        items = [
            SearchItem(
                title=item.get("title", ""),  # 搜索结果的标题
                url=item.get("href", ""),  # 搜索结果的链接
                snippet=item.get("body", ""),  # 搜索结果的摘要
            )
            for item in results
        ]
        
        # 去重并返回
        return deduplicate_results(items)[:max_results]
    except Exception as e:
        # 搜索失败时抛出 HTTP 错误
        raise HTTPException(status_code=500, detail=f"联网搜索失败：{str(e)}")


# ============================================================================
# 去重搜索结果
# ============================================================================
def deduplicate_results(results: List[SearchItem]) -> List[SearchItem]:
    """
    对搜索结果进行去重。
    
    同一个 URL 的搜索结果只保留一条，避免重复。
    
    参数：
        results: 搜索结果列表
    
    返回：
        去重后的搜索结果列表
    """
    # 用于记录已见过的 URL
    seen = set()
    # 去重后的结果列表
    unique_results: List[SearchItem] = []

    for item in results:
        # 获取 URL 并去除首尾空格
        url = item.url.strip()
        
        # 如果 URL 为空或已经见过，跳过
        if url and url not in seen:
            # 标记为已见
            seen.add(url)
            # 添加到结果列表
            unique_results.append(item)

    return unique_results


# ============================================================================
# 格式化搜索结果
# ============================================================================
def format_search_results(results: List[SearchItem]) -> str:
    """
    将搜索结果格式化为易读的字符串。
    
    格式化后的结果会被返回给 LLM，LLM 会根据这些信息生成最终答案。
    
    参数：
        results: SearchItem 对象列表
    
    返回：
        格式化后的搜索结果字符串
    
    示例输出：
        [1]
        标题：Python 官方文档
        链接：https://docs.python.org
        摘要：Python 是一种高级编程语言...
        
        [2]
        标题：Python 教程
        链接：https://www.python.org/tutorial
        摘要：学习 Python 的基础知识...
    """
    # 如果没有搜索结果，返回提示信息
    if not results:
        return "没有检索到有效结果。"

    # 格式化每个搜索结果
    # enumerate(results, start=1) 会从 1 开始编号
    return "\n".join(
        f"[{index}]\n标题：{item.title}\n链接：{item.url}\n摘要：{item.snippet}\n"
        for index, item in enumerate(results, start=1)
    )

