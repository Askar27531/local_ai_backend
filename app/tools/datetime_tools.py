# ============================================================================
# 日期时间工具模块
# 功能：提供获取当前日期和时间的功能
# ============================================================================

from datetime import datetime

from langchain_core.tools import tool

from app.tools.runtime import ToolRuntimeState
from langgraph.config import get_stream_writer

def emit_custom_event(event: dict) -> None:
    try:
        writer = get_stream_writer()
        writer(event)
    except Exception:
        pass

# ============================================================================
# 创建获取当前日期时间工具
# ============================================================================
def create_current_datetime_tool(state: ToolRuntimeState):
    """
    创建一个获取当前日期时间的工具。
    
    这个函数返回一个工具函数，该函数会：
    1. 标记时间工具已被使用
    2. 获取当前日期和时间
    3. 返回 ISO 8601 格式的时间字符串
    
    参数：
        state: ToolRuntimeState 对象，用于追踪工具使用
    
    返回：
        一个被 @tool 装饰的函数，可以被 LangChain Agent 调用
    """
    
    # 定义工具函数
    def get_current_datetime() -> str:
        """
        当需要查询日期时间时，获取当前最新日期和时间。
        """

        state.mark_used("get_current_datetime")

        emit_custom_event({
            "type": "tool_status",
            "tool": "get_current_datetime",
            "message": "正在获取当前服务器时间。",
        })

        now = datetime.now().astimezone()
        result = now.isoformat(timespec="seconds")

        emit_custom_event({
            "type": "tool_result",
            "tool": "get_current_datetime",
            "message": f"当前时间获取完成：{result}",
            "result": result,
        })

        return result
    
    # 使用 tool() 函数将函数转换为 LangChain 工具
    return tool(get_current_datetime)
