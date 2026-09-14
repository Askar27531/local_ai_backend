# 《归潮之岛》AI NPC 后端

面向 Unity 探索 RPG 的单体 AI NPC Runtime，提供角色对话、剧情知识门控、Milvus RAG、长期记忆和流式输出。

## 产品边界

本仓库只包含：

- `npc_app/`：NPC Runtime、API、记忆、检索与核心测试；
- `game_docs/`：世界观、角色、任务和知识资产；
- `scripts/`：知识入库、内容校验和后端检索诊断；
- `.github/`：NPC 质量门。

通用助手、Skill Agent、代码写回和游戏生产 Agent 已移出本仓库。参见 [PROJECT_BOUNDARIES.md](PROJECT_BOUNDARIES.md)。

## 运行环境

- Python 3.12.8；
- Ollama，默认 `qwen3:14b`；
- Milvus，世界知识 collection 默认为 `guichao_island_chunks`，长期记忆 collection 默认为 `npc_long_term_memories_v2`；
- PostgreSQL 18 用于 Runtime 持久化；默认本地连接由 `.env` 中的 `NPC_DATABASE_URL` 提供。

PostgreSQL 只保存 Runtime 必需数据：账号凭据、玩家与 NPC 的线程归属、完整问答、线程摘要和结构化长期记忆。不保存账号停用状态、展示标题、软删除、模型审计、历史检索来源，以及可以由线程推导的重复用户/NPC 字段。

## 安装

```powershell
cd D:\local_ai_backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

项目根目录的 `.env` 是唯一的本地运行配置文件，已经包含认证、PostgreSQL、Ollama、模型、Milvus 和记忆配置。按本机环境修改其中的地址和凭据；`.env` 含有密钥且不进入 Git，也不再维护会过期的示例副本。

本地 PostgreSQL 示例：

```env
NPC_DATABASE_URL=postgresql+psycopg://ai_npc_app:<URL编码后的密码>@127.0.0.1:5432/AI_NPC_Database
```

后端仅接受 `postgresql` 连接；`NPC_DATABASE_URL` 缺失或指向其他数据库时会在启动阶段直接报错。首次启动时，SQLAlchemy 会在目标 PostgreSQL 数据库中创建缺失的数据表。

## 启动依赖

确认 Ollama 已运行并拉取模型：

```powershell
ollama pull qwen3:14b
ollama list
```

确认 Milvus 监听 `19530`，然后验证游戏文档：

```powershell
python scripts\validate_game_docs_for_rag.py
```

首次或文档变化后重新入库：

```powershell
python scripts\ingest_game_docs_to_milvus.py
```

入库脚本会重建同名 collection，只用于当前开发工作流。

首次部署时显式创建长期记忆 collection：

```powershell
python -m scripts.init_memory_milvus
```

该命令只允许创建不存在的 `npc_long_term_memories_v2`；集合已存在时会明确失败，不会覆盖长期记忆。对话检索和记忆写入路径不会创建 Schema、索引或 collection。应用启动时只校验字段、向量维度和索引并加载已有集合；缺失或不兼容时启动失败，并提示运行初始化命令。

## 启动 API

```powershell
uvicorn npc_app.main:app --host 0.0.0.0 --port 8001 --reload --env-file .env
```

入口：

```text
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/health
http://127.0.0.1:8001/ready
```

- `/health`：只确认进程存活；
- `/ready`：只读检查数据库、Ollama 模型、Milvus、世界知识 collection 和长期记忆 collection 的 Schema；
- 任一运行依赖不可用时 `/ready` 返回 HTTP 503 和排查提示。

## Unity v1 对话接口

Unity 使用唯一的 NPC 对话接口：

```http
POST /v1/npc/chat/stream
Authorization: Bearer <token>
Content-Type: application/json
```

请求示例：

```json
{
  "question": "这把钥匙能开什么？",
  "thread_id": null,
  "npc_id": "Orin",
  "player": {
    "location": "lighthouse",
    "current_quest": "open_old_door",
    "inventory": ["old_key"],
    "presented_items": ["old_key"],
    "visited_locations": ["village", "lighthouse"],
    "known_clues": [],
    "mentioned_clues": []
  },
  "relationship": {
    "trust": 3,
    "favorability": 70,
    "annoyance": 12
  },
  "story": {
    "unlock_level": 3
  },
  "intent_hint": "ask_item"
}
```

Unity 不再传入以下 Runtime 控制字段：

- `player_id`：从认证用户得到；
- `npc_interaction_count`：从服务端线程历史计算；
- `top_k`：由服务端 Intent Policy 决定。

请求边界规则：

- `npc_id` 必须是 `Karo`、`Lira`、`Orin`、`Nia`、`Venn`、`Elder_Mara` 或 `Lab_Terminal`；
- `thread_id` 必须是服务端返回的 UUID；
- 游戏状态字符串会去除首尾空白，单项最长 80 字符；列表有数量上限，不允许空项、重复项或控制字符；
- 非法客户端数据由 Pydantic 在 API 入口返回 422，业务层和 Milvus 层不会静默修正。

所有 Milvus 标量过滤使用表达式模板和 `filter_params` 参数绑定，业务值不会通过 f-string 拼入 Filter。长期记忆写入适配器只验证 Schema 长度，不再通过 `[:N]` 静默截断；LLM 提取内容的归一化与预算裁剪发生在记忆服务边界，并可被测试。

旧接口 `/npc/chat/stream` 已删除。NPC 端到端协议测试放在 Unity 项目中维护；本仓库保留 Runtime 单元测试、离线质量评测和检索诊断。

`thread_id` 与 NPC 严格绑定。Unity 显式传入 `thread_id` 时，其所属 NPC 必须与请求的 `npc_id` 相同；不匹配会返回 HTTP 409：

```json
{
  "detail": "thread_id 与 npc_id 不匹配，请使用该 NPC 对应的线程 ID"
}
```

后端不会自动换用其他线程或创建新线程。Unity 应为不同 NPC 分别保存服务端返回的 `thread_id`，修正映射后重新请求。只有不传 `thread_id` 时，后端才会复用当前用户与该 NPC 最近的线程，或在不存在时创建线程。

## NDJSON 事件

响应类型：

```http
Content-Type: application/x-ndjson; charset=utf-8
```

事件顺序：

```json
{"type":"thread","data":{"thread_id":"...","npc_id":"Orin"}}
{"type":"status","data":{"message":"..."}}
{"type":"sources","data":{"retrieved_count":3,"sources":[]}}
{"type":"answer_delta","data":{"text":"..."}}
{"type":"done","data":{"retrieved_count":3,"answer_length":42}}
```

低风险问候、NPC 身份和职责请求会跳过意图模型与 RAG。

### NPC Turn Trace

开发环境可在 `.env` 中启用结构化单行 JSON Trace：

```env
NPC_TRACE_ENABLED=true
```

Trace 记录意图来源、角色阶段、对话策略、检索与生成耗时、选中的知识/记忆 ID、Prompt 字符数、答案守卫结果和失败阶段。第一版固定不记录认证凭证、完整 Prompt、玩家问题或 NPC 回答；Context Manifest 也不会作为 NDJSON 事件暴露给 Unity。

## 认证

```text
POST /auth/register
POST /auth/login
```

注册或登录返回 Bearer Token，Unity 调用 NPC 对话接口时携带该 Token。线程创建、归属校验、最近对话和长期记忆均由 NPC Runtime 内部维护，不暴露旧调试 UI 的线程列表与聊天记录接口。

## Runtime 结构

```text
npc_app/
├─ api.py               # 健康、认证和唯一 Unity NDJSON 对话接口
├─ contracts.py         # Unity 与认证请求契约
├─ database.py          # PostgreSQL 配置和五个 Runtime 模型
├─ trace.py             # 不记录对话内容的 Turn Trace
├─ utils.py             # 文本、LLM 输出和 JSON 公共处理
├─ dialogue/
│  ├─ orchestrator.py   # 单轮直接编排，不依赖 LangGraph
│  ├─ intent.py         # 意图、安全规则、剧情门控与 LLM 兜底
│  ├─ planner.py        # 角色阶段和单轮策略
│  ├─ context.py        # Context 选择与字符预算
│  └─ characters.py     # 角色名称、固定回复和检索声线
├─ services/
│  ├─ chat_service.py
│  ├─ milvus_retriever_service.py
│  ├─ memory_service.py
│  ├─ memory_milvus_service.py
│  └─ npc_prompt_service.py
└─ tests/
```

旧的 `npc_rag_service.py`、`npc_intent_service.py` 和 `npc_context_planner_service.py` 已移除，内部代码统一引用 `dialogue/`。

## 质量门

提交前运行：

```powershell
python -m pip check
python -m ruff check npc_app
python -m pytest npc_app\tests --cov=npc_app --cov-report=term-missing --cov-fail-under=70
```

后端启动后，可运行面向完整 HTTP 链路的真实 NPC 验收：

```powershell
python scripts\run_real_npc_acceptance.py --output reports\npc_real_acceptance
```

该脚本会创建独立测试账号，使用多个连续的真实中文问题验证服务就绪、认证、低风险快速路径、剧情门控、Milvus 检索、长期记忆、玩家诈唬识别、角色阶段变化、烦躁拒答、线程与 NPC 隔离，以及不同 NPC 的声线。结果同时写入 `report.json` 和便于阅读的 `report.md`；脚本退出码可直接用于本地回归或 CI。

本地 14B 性能默认配置为关闭 reasoning 输出、NPC 回答最多 256 token、Planner 最多 128 token。普通线索、追问和剧情门控使用确定性 Planner；只有未知意图或玩家挑战/诈唬才调用模型 Planner，以避免每轮重复执行两次 14B 推理。

主要质量目标：

- 剧情知识越权率为 0；
- Recall@K 不低于 95%；
- 多轮记忆正确率不低于 90%；
- 所有 Runtime 修改必须通过离线质量门。

## 常见问题

### `/health` 正常但无法对话

访问 `/ready`：

- `database`：检查 `NPC_DATABASE_URL`；
- `ollama`：检查地址和 `ollama list`；
- `milvus`：检查服务、Token、collection 和入库结果。

### 修改文档后回答没有变化

Markdown 不会自动进入 Milvus，需要重新执行入库脚本。

### NPC 泄露高等级剧情

依次检查：

1. 文档章节的 `解锁N`；
2. `npc_knowledge_matrix_v03.json`；
3. `dialogue/intent.py` 与 `dialogue_config.json` 中的剧情门控；
4. `npc_app/tests/test_context_compiler.py` 的越权用例；
5. 重新入库后的真实检索评测。

## 路线图

阶段 1.5（Lean Unity Runtime）已完成，当前版本为 `v0.3.0`。版本历史参见 [CHANGELOG.md](CHANGELOG.md)。
