# ============================================================================
# Agent 输出解析模块
# 功能：解析 LLM 模型的输出，将其转换为结构化的 Python 对象
# ============================================================================

import json
import re

# 导入 Pydantic 输出解析器
from langchain_core.output_parsers import PydanticOutputParser


# 导入输出数据结构
from app.schemas.agent import AgentFinalOutput


# ============================================================================
# 创建 Pydantic 输出解析器实例
# ============================================================================
# 这个解析器会根据 AgentFinalOutput 的定义生成 JSON 格式要求
# 并用于解析模型的输出
output_parser = PydanticOutputParser(pydantic_object=AgentFinalOutput)


# ============================================================================
# 获取格式要求
# ============================================================================
def get_format_instructions() -> str:
    """
    获取 JSON 格式要求字符串。
    
    ⚠️ 重要：这个函数返回的是 **字符串**，不是 JSON 对象！
    
    返回类型：str（字符串）
    
    返回值是什么样的？
    这个函数返回一个长字符串，内容看起来像 JSON，但实际上是文本。
    
    工作原理详解（5 个步骤）：
    
    1️⃣ 定义 Pydantic 类
    class AgentFinalOutput(BaseModel):
        answer: str
        used_tools: List[str]
    
    2️⃣ 创建 PydanticOutputParser
    output_parser = PydanticOutputParser(pydantic_object=AgentFinalOutput)
    这个 parser 会分析 AgentFinalOutput 类的定义，
    知道它有两个字段：answer（字符串）和 used_tools（字符串列表）
    
    3️⃣ 调用 get_format_instructions()
    output_parser.get_format_instructions()
    这个方法会：
    - 读取 AgentFinalOutput 的字段定义
    - 生成一个 JSON Schema（格式规范）
    - 将 Schema 转换为字符串
    - 返回这个字符串
    
    4️⃣ 这个字符串被用在哪里？
    在 agent_service.py 中：
    messages = AGENT_PROMPT.format_messages(
        format_instructions=get_format_instructions(),  # 这里传入字符串
        ...
    )
    这个字符串被插入到系统提示词中，告诉 LLM 应该输出什么格式。
    
    5️⃣ LLM 读到这个字符串后
    LLM 看到这个格式说明，就知道应该输出什么样的 JSON。
    
    总结：
    - get_format_instructions() 返回的是字符串
    - 这个字符串的内容看起来像 JSON，但它本身是文本
    - 用途：被插入到系统提示词中，告诉 LLM 输出格式
    - 类比：就像一个"格式说明书"，用文字描述应该输出什么格式
    """
    return output_parser.get_format_instructions()


# ============================================================================
# 验证 Agent 输出
# ============================================================================
def _validate_agent_output(data: dict) -> AgentFinalOutput:
    """
    将字典转换为 AgentFinalOutput 对象。
    
    这个函数兼容 Pydantic v1 和 v2 的不同 API。
    
    参数：
        data: 包含 answer 和 used_tools 的字典
    
    返回：
        AgentFinalOutput 对象
    
    异常：
        ValidationError: 如果数据不符合 AgentFinalOutput 的定义
    """

    # 检查是否有 model_validate 方法（Pydantic v2）
    if hasattr(AgentFinalOutput, "model_validate"):
        return AgentFinalOutput.model_validate(data)

    # 否则使用 parse_obj 方法（Pydantic v1）
    return AgentFinalOutput.parse_obj(data)


# ============================================================================
# 解析 Agent 输出（核心函数）
# ============================================================================
def parse_agent_output(raw_text: str) -> AgentFinalOutput:
    """
    解析 LLM 模型的原始输出。
    
    这个函数实现了一个多层次的解析策略：
    1. 优先使用 Pydantic 解析器（最严格）
    2. 如果失败，用正则表达式提取 JSON（容错）
    3. 如果还是失败，返回默认对象（保证不崩溃）
    
    这样设计的好处：
    - 如果模型输出格式正确，能准确解析
    - 如果模型输出格式有偏差，仍能尽力提取信息
    - 即使完全失败，也能返回一个有效的对象，保证接口稳定
    
    参数：
        raw_text: LLM 模型的原始输出文本
    
    返回：
        AgentFinalOutput 对象
    """


    # ========================================================================
    # 第 1 步：尝试使用 Pydantic 解析器（最严格的方式）
    # ========================================================================
    try:
        # 使用 Pydantic 解析器解析 JSON
        return output_parser.parse(raw_text)  # 注意：这里传入原始文本，解析器会自己处理
    except Exception:
        # 如果失败，继续尝试其他方法
        pass

    # ========================================================================
    # 第 2 步：用正则表达式提取 JSON（容错方式）
    # ========================================================================
    # 使用正则表达式查找 {...} 格式的 JSON 对象
    # re.DOTALL 标志使 . 能匹配换行符
    json_match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)

    if json_match:
        try:
            # 提取匹配的 JSON 字符串
            json_str = json_match.group(0)
            # 解析 JSON 字符串为字典
            data = json.loads(json_str)
            # 验证并转换为 AgentFinalOutput 对象
            return _validate_agent_output(data)
        except Exception:
            # 如果解析失败，继续尝试最后的兜底方案
            pass

    # ========================================================================
    # 第 3 步：兜底方案（保证不崩溃）
    # ========================================================================
    # 如果所有解析都失败，返回一个默认对象
    # 将整个原始文本作为答案
    return AgentFinalOutput(
        answer=raw_text,  # 使用整个文本作为答案
        used_tools=[],  # 工具列表为空
    )
