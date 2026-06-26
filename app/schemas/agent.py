# ============================================================================
# Agent 输出数据模型
# 功能：定义 Agent 最终输出的数据结构
# ============================================================================

from typing import List

from pydantic import BaseModel, Field


# ============================================================================
# Agent 最终输出数据类
# ============================================================================
class AgentFinalOutput(BaseModel):
    """
    Agent 处理完成后的最终输出数据结构。
    
    这个类使用 Pydantic 定义，可以：
    1. 自动验证数据类型
    2. 生成 JSON Schema（用于告诉模型应该输出什么格式）
    3. 方便地转换为 JSON 或字典
    
    属性：
        answer: 最终给用户的中文答案
        used_tools: 本轮实际使用过的工具名称列表
    """

    # 最终答案字段
    answer: str = Field(
        ...,  # ... 表示这是必需字段
        description="最终给用户看的中文答案。如果使用了搜索结果，尽量标注来源编号，如 [1]、[2]。",
    )

    # 使用过的工具列表字段
    used_tools: List[str] = Field(
        default_factory=list,  # 默认值为空列表
        description="本轮实际使用过的工具名称，例如 controlled_web_search、safe_calculator、get_current_datetime。",
    )

    # ========================================================================
    # 类配置（可选）
    # ========================================================================
    # 如果需要额外的 Pydantic 配置，可以在这里添加
    # 例如：
    # class Config:
    #     json_schema_extra = {
    #         "example": {
    #             "answer": "Python 是一种高级编程语言...",
    #             "used_tools": ["controlled_web_search"]
    #         }
    #     }
