# 《归潮之岛》AI NPC 后端

面向 Unity 探索 RPG 的单体 AI NPC Runtime，提供角色对话、剧情知识门控、Milvus RAG 检索、长期记忆和 NDJSON 流式输出。

## 验收回放演示

**▶ [在浏览器中查看](https://askar27531.github.io/local_ai_backend/)**

展示 NPC Runtime 的真实验收结果：完整对话记录、逐轮角色阶段、对话策略、规划来源、检索条数、延迟与 NDJSON 事件序列，另有 7 个 NPC 的 22 条声线样本。支持直接定位到任意一轮，例如 [`#run=0&step=3`](https://askar27531.github.io/local_ai_backend/#run=0&step=3) 的剧情门控，或 [`#run=0&step=7`](https://askar27531.github.io/local_ai_backend/#run=0&step=7) 的角色阶段推进。

实现方式与数据来源见 [`demo/`](demo/)。

## 目录

- [验收回放演示](#验收回放演示)
- [项目组成](#项目组成)
- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [仓库结构](#仓库结构)
- [架构与数据流](#架构与数据流)
- [运行环境](#运行环境)
- [快速开始](#快速开始)
- [接口](#接口)
- [Runtime 结构](#runtime-结构)
- [质量门](#质量门)
- [常见问题](#常见问题)
- [文档索引](#文档索引)

## 项目组成

- `npc_app/`：NPC Runtime、API、记忆、检索与核心测试；
- `game_docs/`：世界观、角色、任务和知识资产；
- `scripts/`：知识入库、内容校验和后端检索诊断；
- `demo/`：验收回放演示站点；
- `.github/`：NPC 质量门与 Pages 部署。

本仓库聚焦 NPC Runtime 这一条产品主线，范围与依赖方向参见 [PROJECT_BOUNDARIES.md](PROJECT_BOUNDARIES.md)。

## 功能特性

- **单一 Unity 对话接口** —— `POST /v1/npc/chat/stream`，以 NDJSON 流式返回。
- **防剧透剧情门控** —— 知识在检索、Context 编译和答案守卫三层按 `unlock_level` 过滤。
- **Milvus RAG** —— 世界知识检索，外加一个带版本号的长期记忆 collection。
- **确定性回合规划** —— 角色阶段与对话策略由规则给出；只有未知意图、玩家挑战或诈唬才调用模型 Planner。
- **Bearer Token 认证** —— `POST /auth/register` 与 `POST /auth/login`。
- **隐私安全的链路追踪** —— 可选的结构化 JSON Turn Trace，永不记录凭证与对话内容。
- **CI 质量门** —— Ruff、Pytest，`npc_app` 覆盖率不低于 70%。

## 技术栈

| 层次 | 技术 |
| --- | --- |
| 运行时 | Python 3.12、FastAPI、Uvicorn |
| 数据库 | PostgreSQL，通过 SQLAlchemy + `psycopg` |
| 对话模型 | Ollama（默认 `qwen3:14b`），通过 LangChain `ChatOllama` |
| 向量库 | Milvus，通过 `pymilvus` |
| 向量化 / 重排 | `sentence-transformers`、`sentencepiece` |

Python 运行时依赖固定在 [`requirements.txt`](requirements.txt)，开发与测试工具在 [`requirements-dev.txt`](requirements-dev.txt)。

## 仓库结构

```text
npc_app/              # NPC Runtime：API、对话、检索、记忆与测试
  api.py              # 健康、认证和唯一 Unity NDJSON 对话接口
  contracts.py        # Unity 与认证请求契约
  database.py         # PostgreSQL 配置和 Runtime 模型
  trace.py            # 不记录对话内容的 Turn Trace
  utils.py            # 文本、LLM 输出和 JSON 公共处理
  dialogue/           # 编排、意图、规划、Context、角色
  services/           # 对话、检索、记忆、Prompt 构建
  tests/              # 单元测试与验收入口测试
game_docs/            # 世界观、角色、任务和知识资产
scripts/              # 知识入库、内容校验、检索诊断、真实验收
demo/                 # 验收回放演示站点（GitHub Pages）
.github/workflows/    # NPC 质量门（CI）与 Pages 部署
```

## 架构与数据流

```text
Unity 客户端 ──> npc_app API ──> dialogue / 检索 / 记忆 ──> PostgreSQL / Ollama / Milvus
demo（验收回放） ──> 运行报告
```

数据职责边界：

- **PostgreSQL** 是业务事实来源，保存账号凭据、玩家与 NPC 的线程归属、完整问答、线程摘要和结构化长期记忆。Schema 保持最小化：只保留 Runtime 必需字段，可由线程推导的重复用户 / NPC 字段不落库。
- **Milvus** 保存可重建的语义检索索引，与业务事实解耦 —— 向量服务异常不会导致已确认的业务数据丢失。

## 运行环境

- Python 3.12.8；
- Ollama，默认模型 `qwen3:14b`；
- Milvus，世界知识 collection 默认 `guichao_island_chunks`，长期记忆 collection 默认 `npc_long_term_memories_v2`；
- PostgreSQL 18 用于 Runtime 持久化，本地连接由 `.env` 中的 `NPC_DATABASE_URL` 提供。

## 快速开始

### 1. 安装

```powershell
cd D:\local_ai_backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

### 2. 配置 `.env`

项目根目录的 `.env` 是唯一的本地运行配置文件，包含认证、PostgreSQL、Ollama、模型、Milvus 和记忆配置。按本机环境修改其中的地址与凭据。

`.env` 含有密钥，不进入 Git。本地 PostgreSQL 连接示例：

```env
NPC_DATABASE_URL=postgresql+psycopg://ai_npc_app:<URL编码后的密码>@127.0.0.1:5432/AI_NPC_Database
```

后端**仅接受** `postgresql` 连接；`NPC_DATABASE_URL` 缺失或指向其他数据库时会在启动阶段直接报错。首次启动时，SQLAlchemy 会在目标 PostgreSQL 数据库中创建缺失的数据表。

### 3. 启动依赖

确认 Ollama 已运行并拉取模型：

```powershell
ollama pull qwen3:14b
ollama list
```

确认 Milvus 监听 `19530`，然后校验游戏文档：

```powershell
python scripts\validate_game_docs_for_rag.py
```

首次或文档变化后重新入库：

```powershell
python scripts\ingest_game_docs_to_milvus.py
```

> 注意：入库脚本会**重建同名 collection**。

### 4. 初始化长期记忆 collection

```powershell
python -m scripts.init_memory_milvus
```

该命令只允许创建不存在的 `npc_long_term_memories_v2`；集合已存在时会明确失败，**不会覆盖长期记忆**。对话检索与记忆写入路径不会创建 Schema、索引或 collection。应用启动时只校验字段、向量维度和索引并加载已有集合；缺失或不兼容时启动失败，并提示运行初始化命令。

### 5. 启动 API

```powershell
uvicorn npc_app.main:app --host 0.0.0.0 --port 8001 --reload --env-file .env
```

入口：

```text
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/health
http://127.0.0.1:8001/ready
```

## 接口

### 健康检查

- `/health`：只确认进程存活；
- `/ready`：只读检查数据库、Ollama 模型、Milvus、世界知识 collection 和长期记忆 collection 的 Schema；
- 任一运行依赖不可用时，`/ready` 返回 HTTP 503 与排查提示。

### 认证

```text
POST /auth/register
POST /auth/login
```

注册或登录返回 Bearer Token，Unity 调用 NPC 对话接口时携带该 Token。线程创建、归属校验、最近对话和长期记忆均由 NPC Runtime 内部维护。

### Unity v1 对话接口

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

以下 Runtime 控制字段由服务端决定，Unity 无需传入：

- `player_id`：从认证用户得到；
- `npc_interaction_count`：从服务端线程历史计算；
- `top_k`：由服务端 Intent Policy 决定。

请求边界规则：

- `npc_id` 必须是 `Karo`、`Lira`、`Orin`、`Nia`、`Venn`、`Elder_Mara` 或 `Lab_Terminal`；
- `thread_id` 必须是服务端返回的 UUID；
- 游戏状态字符串会去除首尾空白，单项最长 80 字符；列表有数量上限，不允许空项、重复项或控制字符；
- 非法客户端数据由 Pydantic 在 API 入口返回 422，业务层和 Milvus 层不会静默修正。

`inventory` 只表示玩家持有物品，`presented_items` 表示本轮确实展示给 NPC 的物品 —— 只有后者能作为确认事实写入长期记忆。

#### 线程与 NPC 的绑定

`thread_id` 与 NPC 严格绑定。Unity 显式传入 `thread_id` 时，其所属 NPC 必须与请求的 `npc_id` 相同，不匹配会返回 HTTP 409：

```json
{
  "detail": "thread_id 与 npc_id 不匹配，请使用该 NPC 对应的线程 ID"
}
```

这是一条刻意设计的严格契约：不匹配时直接拒绝，而不是静默换用其他线程或新建线程，从而避免上下文串到别的角色。Unity 应为不同 NPC 分别保存服务端返回的 `thread_id`，修正映射后重新请求。只有**不传** `thread_id` 时，后端才会复用当前用户与该 NPC 最近的线程，或在不存在时创建线程。

### NDJSON 事件

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

低风险问候、NPC 身份和职责请求会跳过意图模型与 RAG，因此不会出现 `sources` 事件。

### NPC Turn Trace

开发环境可在 `.env` 中启用结构化单行 JSON Trace：

```env
NPC_TRACE_ENABLED=true
```

Trace 记录意图来源、角色阶段、对话策略、检索与生成耗时、选中的知识 / 记忆 ID、Prompt 字符数、答案守卫结果和失败阶段。出于隐私安全考虑，Trace 不含认证凭证、完整 Prompt、玩家问题或 NPC 回答；Context Manifest 仅用于服务端诊断，不通过 NDJSON 暴露给 Unity。

## Runtime 结构

```text
npc_app/
├─ api.py               # 健康、认证和唯一 Unity NDJSON 对话接口
├─ contracts.py         # Unity 与认证请求契约
├─ database.py          # PostgreSQL 配置和五个 Runtime 模型
├─ trace.py             # 不记录对话内容的 Turn Trace
├─ utils.py             # 文本、LLM 输出和 JSON 公共处理
├─ dialogue/
│  ├─ orchestrator.py   # 单轮直接编排的对话主流程
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

内部实现统一收敛到 `dialogue/`，Unity 通过唯一的 `/v1/npc/chat/stream` 接口交互。本仓库维护 Runtime 单元测试、离线质量评测与检索诊断；Unity 侧的协议集成测试在 Unity 项目中维护。

## 质量门

### 离线质量门

提交前运行：

```powershell
python -m pip check
python -m ruff check npc_app
python -m pytest npc_app\tests --cov=npc_app --cov-report=term-missing --cov-fail-under=70
```

### 真实 HTTP 验收

后端启动后，运行面向完整 HTTP 链路的真实 NPC 验收：

```powershell
python scripts\run_real_npc_acceptance.py --output reports\npc_real_acceptance
```

该脚本会创建独立测试账号，用多个连续的真实中文问题验证：服务就绪、认证、低风险快速路径、剧情门控、Milvus 检索、长期记忆、玩家诈唬识别、角色阶段变化、烦躁拒答、线程与 NPC 隔离，以及不同 NPC 的声线。结果同时写入 `report.json` 和便于阅读的 `report.md`；脚本退出码可直接用于本地回归或 CI。

### 性能默认值

本地 14B 配置默认关闭 reasoning 输出，NPC 回答最多 256 token，Planner 最多 128 token。普通线索、追问和剧情门控使用确定性 Planner；只有未知意图或玩家挑战 / 诈唬才调用模型 Planner，以避免每轮重复执行两次 14B 推理。

### 质量目标

- 剧情知识越权率为 0；
- Recall@K 不低于 95%；
- 多轮记忆正确率不低于 90%；
- 所有 Runtime 修改必须通过离线质量门。

## 常见问题

### 对话依赖排查

访问 `/ready` 定位：

- `database`：检查 `NPC_DATABASE_URL`；
- `ollama`：检查地址和 `ollama list`；
- `milvus`：检查服务、Token、collection 和入库结果。

### 知识文档的更新与入库

Markdown 不会自动进入 Milvus，文档变更后需重新执行入库脚本。

### 在无 `.env` 的环境中运行测试

`npc_app/tests/conftest.py` 会先加载本地 `.env`，并在缺失时为 `NPC_DATABASE_URL` 提供测试兜底值，因此测试套件在 CI 等干净环境中可直接运行。重构或迁移测试目录时请保留该文件。

### 校验剧情门控是否生效

门控由多层共同保证，按顺序核对：

1. 文档章节的 `解锁N` 分级；
2. `npc_knowledge_matrix_v03.json` 的角色知识分配；
3. `dialogue/intent.py` 与 `dialogue_config.json` 中的门控规则；
4. `npc_app/tests/test_context_compiler.py` 的越权用例；
5. 重新入库后运行真实检索评测。

## 文档索引

- [`demo/`](demo/) —— 演示站点的数据来源与实现说明
- [CHANGELOG.md](CHANGELOG.md) —— 版本历史
- [PROJECT_BOUNDARIES.md](PROJECT_BOUNDARIES.md) —— 产品范围与依赖方向
- [`game_docs/non_vectorized/A_README_使用说明.md`](game_docs/non_vectorized/A_README_使用说明.md) —— 知识库的编写、分级与入库规范

## 版本

当前版本 `v0.3.0`，版本历史参见 [CHANGELOG.md](CHANGELOG.md)。
