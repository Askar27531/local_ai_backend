# ============================================================================
# 工具注册表模块
# 功能：集中管理所有可用的工具，为 Agent 提供工具列表
# ============================================================================

from typing import List

from langchain_core.tools import BaseTool

# 导入所有工具的创建函数
from app.tools.datetime_tools import create_current_datetime_tool
from app.tools.runtime import ToolRuntimeState
from app.tools.search_tools import create_controlled_web_search_tool


# ============================================================================
# 构建 Agent 工具列表
# ============================================================================
def build_agent_tools(state: ToolRuntimeState) -> List[BaseTool]:
    """
    构建 Agent 可用的工具列表。
    
    这个函数集中管理所有工具的创建，便于后续添加或修改工具。
    
    参数：
        state: ToolRuntimeState 对象，用于追踪工具使用
    
    返回：
        工具对象列表，每个工具都是一个 LangChain BaseTool 实例
    
    可用工具：
    1. controlled_web_search - 联网搜索工具
       用于获取最新信息、新闻、价格等需要实时数据的问题
    
    2. get_current_datetime - 获取当前时间工具
       用于获取当前日期和时间
    """
    
    # 返回工具列表
    # 每个工具都通过对应的创建函数生成
    return [
        # 工具 1：联网搜索
        create_controlled_web_search_tool(state),
        
        # 工具 2：获取当前时间
        create_current_datetime_tool(state),
    ]
