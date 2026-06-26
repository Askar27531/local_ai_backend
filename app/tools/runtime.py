# ============================================================================
# 工具运行状态管理模块
# 功能：追踪每一轮 Agent 调用中使用过的工具和搜索来源
# ============================================================================

from dataclasses import dataclass, field
from typing import List

# 导入搜索结果数据结构
from app.schemas.chat import SearchItem


# ============================================================================
# 工具运行状态数据类
# ============================================================================
@dataclass
class ToolRuntimeState:
    """
    每一轮 Agent 调用都会创建一个新的 ToolRuntimeState 实例。
    
    这个类的作用：
    1. 记录本轮调用中使用过的工具名称
    2. 记录搜索工具返回的来源信息
    3. 提供工具调用的追踪和统计功能
    
    为什么不用全局变量？
    1. 避免全局状态污染
    2. 多用户并发时更安全（每个请求独立的状态）
    3. 工具调用记录更清晰
    4. 便于测试和调试
    """

    # 本轮调用中使用过的工具名称列表
    # 例如：["controlled_web_search", "safe_calculator"]
    used_tools: List[str] = field(default_factory=list)

    # 搜索工具返回的来源列表
    # 每个元素是一个 SearchItem 对象，包含标题、URL、摘要
    sources: List[SearchItem] = field(default_factory=list)

    # 最多保留的来源数量
    # 防止搜索结果过多导致响应体过大
    max_total_sources: int = 10

    # ========================================================================
    # 标记工具已使用
    # ========================================================================
    def mark_used(self, tool_name: str) -> None:
        """
        记录一个工具被调用过。
        
        这个方法会在工具执行时被调用，用于追踪哪些工具被使用过。
        
        参数：
            tool_name: 工具名称，例如 "controlled_web_search"
        
        示例：
            state.mark_used("controlled_web_search")
            state.mark_used("safe_calculator")
        """
        # 只添加未添加过的工具名称（避免重复）
        if tool_name not in self.used_tools:
            self.used_tools.append(tool_name)

    # ========================================================================
    # 添加搜索来源
    # ========================================================================
    def add_sources(self, new_sources: List[SearchItem]) -> None:
        """
        添加新的搜索来源，并进行去重和数量限制。
        
        功能：
        1. 合并已有的来源和新来源
        2. 按 URL 去重（同一个 URL 只保留一条）
        3. 限制总数量不超过 max_total_sources
        
        参数：
            new_sources: 新的搜索来源列表
        
        示例：
            new_sources = [
                SearchItem(title="Python官网", url="https://python.org", snippet="..."),
                SearchItem(title="Python教程", url="https://tutorial.python.org", snippet="..."),
            ]
            state.add_sources(new_sources)
        """
        # 用于去重的集合（存储已见过的 URL）
        merged: List[SearchItem] = []
        seen = set()

        # 遍历已有的来源和新来源
        for item in self.sources + new_sources:
            # 生成去重的 key
            # 优先使用 URL，如果 URL 为空则使用 "标题:摘要" 组合
            key = item.url.strip() or f"{item.title}:{item.snippet}"

            # 如果 key 为空或已经见过，跳过
            if not key or key in seen:
                continue

            # 标记为已见
            seen.add(key)
            # 添加到合并列表
            merged.append(item)

            # 如果达到最大数量限制，停止添加
            if len(merged) >= self.max_total_sources:
                break

        # 更新来源列表
        self.sources = merged

    # ========================================================================
    # 检查是否使用了搜索工具（属性）
    # ========================================================================
    @property
    def used_search(self) -> bool:
        """
        检查本轮调用中是否使用了搜索工具。
        
        这是一个属性（property），可以像访问属性一样使用：
            if state.used_search:
                print("使用了搜索工具")
        
        返回：
            True 如果使用了搜索工具，False 否则
        """
        # 检查 "controlled_web_search" 是否在已使用的工具列表中
        return "controlled_web_search" in self.used_tools
