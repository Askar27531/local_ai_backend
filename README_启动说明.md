# local_ai_backend 启动与项目说明

这个项目是《归潮之岛》RAG NPC 后端的本地开发工程。它包含两个后端应用：

- `app/`：通用本地 AI 助手后端，支持用户登录、会话线程、流式聊天、工具调用和聊天记录保存。
- `npc_app/`：游戏 NPC 专用后端，围绕《归潮之岛》的世界观文档、NPC 知识边界、剧情解锁等级和信任等级进行 RAG 对话。

当前游戏方向的主要工作应优先使用 `npc_app/`。`app/` 更像早期通用聊天后端，可以继续保留作对照或通用助手入口。

## 1. 项目目标

《归潮之岛》是一个 Unity 3D 探索 RPG Demo。玩家在海滩醒来，失去记忆，并发现自己无论从海面、森林、山崖还是其他方向离开，最终都会被海雾、异常潮流和方向错乱带回岛上。

本后端的目标不是普通文档问答，而是实现“有知识边界的游戏 NPC 自由问答”：

- NPC 只能回答自己身份范围内知道的内容。
- NPC 只能回答玩家当前剧情阶段已经解锁的内容。
- NPC 的语气会受到信任等级影响。
- 玩家可以围绕道具、地点、任务、传闻和已知线索自由追问。
- 后端用 RAG 检索游戏知识，再用本地大模型生成 NPC 台词。
- 对话以 NDJSON 流式返回，方便 Unity 或前端逐字显示。

## 2. 目录结构

```text
local_ai_backend/
├─ app/
│  ├─ main.py                         # 通用 AI 助手 FastAPI 入口
│  ├─ db/                             # 通用助手数据库模型与连接
│  ├─ prompts/                        # 通用助手提示词
│  ├─ schemas/                        # 通用助手请求/响应模型
│  ├─ services/                       # 通用助手 LLM、Agent、记录、线程、认证等服务
│  └─ tools/                          # 通用助手工具注册与运行时
├─ npc_app/
│  ├─ main.py                         # NPC RAG FastAPI 入口
│  ├─ db/                             # NPC 后端独立 SQLite 数据库模型
│  ├─ schemas/                        # NPC 请求/响应模型
│  └─ services/
│     ├─ llm_service.py               # Ollama / 本地模型配置
│     ├─ milvus_retriever_service.py  # Milvus 向量检索
│     ├─ npc_prompt_service.py        # NPC 系统提示词构建
│     ├─ npc_rag_service.py           # LangGraph RAG 流程
│     ├─ auth_service.py              # NPC 用户认证
│     ├─ thread_service.py            # NPC 会话线程
│     └─ record_service.py            # NPC 聊天记录保存
├─ game_docs/
│  ├─ vectorized/                     # 会向量化并写入 Milvus 的 Markdown，数字编号
│  │  ├─ 01_world_lore_世界观.md
│  │  ├─ ...
│  │  └─ 24_player_choice_consequences_玩家行为后果.md
│  ├─ non_vectorized/                 # 不入库的说明、Prompt、任务和开发文档，字母编号
│  │  ├─ A_README_使用说明.md
│  │  ├─ B_npc_profiles_角色档案.md
│  │  ├─ ...
│  │  └─ F_CHANGELOG_阶段问题与修改建议.md
│  ├─ manifest_v03.json               # 文档入库清单
│  └─ npc_knowledge_matrix_v03.json   # NPC 可访问知识矩阵
├─ scripts/
│  ├─ ingest_game_docs_to_milvus.py   # 切块、生成 embedding 并写入 Milvus
│  ├─ validate_game_docs_for_rag.py   # 单独检查文档和配置质量
│  ├─ test_retrieve.py                # 检索质量测试
│  └─ test_npc_chat_stream.py         # NPC 流式接口测试
├─ requirements.txt
├─ requirements_additions.txt
└─ README_启动说明.md
```

## 3. 运行依赖

### 3.1 Python 环境

项目当前使用本地虚拟环境 `.venv`。进入项目根目录后激活：

```powershell
cd D:\local_ai_backend
.\.venv\Scripts\Activate.ps1
```

如需重新安装依赖：

```powershell
pip install -r requirements.txt
pip install -r requirements_additions.txt
```

### 3.2 Ollama / 本地模型

后端通过 `langchain-ollama` 调用本地模型。模型名和 Ollama 地址在 `app/services/llm_service.py` 与 `npc_app/services/llm_service.py` 中读取配置。

启动前需要确认：

- Ollama 服务已经运行。
- 所需模型已经拉取。
- `/health` 返回的 `model` 与 `ollama_base_url` 是你期望的配置。

### 3.3 Milvus

NPC RAG 使用 Milvus 保存游戏知识向量。默认配置在脚本和服务中保持一致：

```text
MILVUS_URI=http://localhost:19530
MILVUS_TOKEN=root:Milvus
MILVUS_COLLECTION_NAME=guichao_island_chunks
EMBED_MODEL_NAME=BAAI/bge-small-zh-v1.5
```

如果使用 Milvus Standalone，请先确保 Milvus 在本机 `19530` 端口可访问。

## 4. 构建游戏知识库

第一次运行 NPC 后端前，需要把 `game_docs/` 中可入库的游戏知识写入 Milvus：

```powershell
cd D:\local_ai_backend
.\.venv\Scripts\Activate.ps1
python scripts\ingest_game_docs_to_milvus.py
```

脚本会执行这些步骤：

1. 读取 `game_docs/manifest_v03.json` 和 `game_docs/npc_knowledge_matrix_v03.json`。
2. 按 `manifest_v03.json` 中的 `ingest_recommended` 选择入库 Markdown。
3. 按二级标题 `##` 切成知识 chunk。
4. 根据章节标题中的 `解锁N` 或 frontmatter 的 `default_unlock_level` 得到 `unlock_level`。
5. 根据 NPC 知识矩阵按来源文件展开成 `chunk + npc_id` 的记录。
6. 用 `BAAI/bge-small-zh-v1.5` 生成 embedding。
7. 删除并重建 Milvus collection：`guichao_island_chunks`。
8. 插入记录并输出入库结果。

注意：这个脚本会删除同名 Milvus collection 后重建，因此它适合本地开发和知识库全量刷新。

文档质量检查已经从入库脚本中拆出，单独运行：

```powershell
python scripts\validate_game_docs_for_rag.py
```

## 5. 启动 NPC RAG 后端

推荐把 NPC 后端运行在 `8001`，避免和通用助手后端 `8000` 混在一起：

```powershell
cd D:\local_ai_backend
.\.venv\Scripts\Activate.ps1
uvicorn npc_app.main:app --host 0.0.0.0 --port 8001 --reload
```

启动后访问：

```text
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/health
```

局域网内其他设备访问时，把 `127.0.0.1` 换成当前电脑的局域网 IP，例如：

```text
http://10.132.169.149:8001/docs
```

## 6. NPC API 使用流程

NPC 后端有独立的用户表和聊天记录数据库，默认 SQLite 文件为 `npc_app/npc_chat.sqlite3`。

### 6.1 注册

```http
POST /auth/register
Content-Type: application/json

{
  "username": "demo",
  "password": "123456"
}
```

返回：

```json
{
  "access_token": "...",
  "token_type": "bearer"
}
```

### 6.2 登录

```http
POST /auth/login
Content-Type: application/json

{
  "username": "demo",
  "password": "123456"
}
```

后续请求需要带：

```http
Authorization: Bearer <access_token>
```

### 6.3 流式 NPC 对话

```http
POST /npc/chat/stream
Authorization: Bearer <access_token>
Content-Type: application/json
```

示例请求：

```json
{
  "question": "我为什么沿着码头往外走，最后又回来了？",
  "thread_id": null,
  "npc_id": "Karo",
  "unlocked_story_level": 1,
  "trust_level": 0,
  "top_k": 5,
  "player_id": "demo_player",
  "player_location": "dock",
  "current_quest": "why_cannot_leave",
  "inventory": ["broken_badge"],
  "visited_locations": ["beach", "village", "dock"],
  "known_clues": ["cannot_leave_island"]
}
```

关键字段含义：

- `npc_id`：当前说话 NPC，例如 `Karo`、`Lira`、`Orin`、`Nia`、`Venn`、`Elder_Mara`、`Lab_Terminal`。
- `unlocked_story_level`：剧情解锁等级，范围 0 到 5，决定“能不能知道真相”。
- `trust_level`：NPC 信任等级，范围 0 到 5，决定“愿不愿说、说多直接”。
- `top_k`：本次从 Milvus 检索的 chunk 数量。
- `inventory`、`visited_locations`、`known_clues`：玩家当前状态，用于提示词和回答风格约束。
- `thread_id`：为空时创建新线程；传已有线程 ID 时延续上下文。

响应类型是：

```http
Content-Type: application/x-ndjson; charset=utf-8
```

流式事件大致包括：

```json
{"type":"thread","data":{"thread_id":"...","title":"新对话"}}
{"type":"status","data":{"message":"正在检索 Karo 可访问的记忆..."}}
{"type":"sources","data":{"retrieved_count":3,"sources":[...]}}
{"type":"answer_delta","data":{"text":"别急着往外跑。"}}
{"type":"done","data":{"retrieved_count":3,"answer_length":42}}
```

## 7. 测试与调试

### 7.1 检索质量测试

入库后可以先测 Milvus 检索是否合理：

```powershell
python scripts\test_retrieve.py --suite quality
python scripts\test_retrieve.py --suite coverage
python scripts\test_retrieve.py --suite all
```

列出所有内置测试：

```powershell
python scripts\test_retrieve.py --list
```

临时测试一个问题：

```powershell
python scripts\test_retrieve.py --question "Subject 07 是谁？" --npc-id Lab_Terminal --unlock-level 5
```

### 7.2 NPC 流式接口测试

先启动 `npc_app`，再运行：

```powershell
python scripts\test_npc_chat_stream.py --suite quality
python scripts\test_npc_chat_stream.py --suite coverage
python scripts\test_npc_chat_stream.py --suite all
```

临时测试一个 NPC：

```powershell
python scripts\test_npc_chat_stream.py --question "这块蓝色小石头会让我头痛吗？" --npc-id Lira --unlock-level 2 --trust-level 2
```

这个脚本会自动注册或登录默认调试用户：

```text
username: npc_debug_user
password: 123456
```

## 8. 启动通用助手后端

如果需要运行早期通用 AI 助手后端：

```powershell
cd D:\local_ai_backend
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问：

```text
http://127.0.0.1:8000/docs
http://127.0.0.1:8000/health
```

通用助手提供：

- `/auth/register`
- `/auth/login`
- `/auth/me`
- `/threads`
- `/chat`
- `/chat/stream`
- `/threads/{thread_id}/records`

## 9. NPC RAG 的核心机制

### 9.1 入库时的权限控制

`scripts/ingest_game_docs_to_milvus.py` 在入库阶段把知识拆成了“某个 NPC 可访问的某个 chunk”。它信任 Markdown 和 JSON 配置，不再承担大量剧情兜底检查。

一条 Milvus 记录包含：

- `vector`：文本向量。
- `chunk_uid`：稳定 chunk ID。
- `source_file`：来源 Markdown 文件。
- `section_title`：章节标题。
- `content`：章节正文。
- `npc_id`：允许访问该 chunk 的 NPC。
- `unlock_level`：最低剧情解锁等级。
- `topics`：主题标签。
- `spoiler_level`：剧透等级。

### 9.2 对话时的检索过滤

运行时检索表达式是：

```text
npc_id == "<当前 NPC>" and unlock_level <= <玩家当前剧情等级>
```

这意味着即使玩家问出了高剧透词，只要当前 NPC 或当前剧情等级不允许，就不会检索到对应真相 chunk。

### 9.3 提示词约束

`npc_app/services/npc_prompt_service.py` 会把这些内容组合进系统提示词：

- 当前 NPC 的角色档案。
- NPC 回答规则。
- 玩家状态。
- 近期对话历史。
- 同一 NPC 的长期记忆摘要。
- Milvus 检索到的上下文。
- 剧情解锁、信任等级和词汇边界规则。

回答硬性要求包括：

- 不说自己是 AI。
- 不提 RAG、Milvus、知识库、prompt 或检索。
- 不突破剧情解锁。
- 不突破 NPC 身份知识边界。
- 普通 NPC 只输出台词，不输出动作、旁白或角色名。

## 10. 常见问题

### 10.1 `/npc/chat/stream` 没有检索结果

检查：

- Milvus 是否启动。
- 是否已经运行 `python scripts\ingest_game_docs_to_milvus.py`。
- collection 名称是否是 `guichao_island_chunks`。
- 请求里的 `npc_id` 是否正确。
- `unlocked_story_level` 是否太低。

### 10.2 回答不够像 NPC

优先检查：

- `game_docs/non_vectorized/B_npc_profiles_角色档案.md`
- `game_docs/non_vectorized/E_npc_prompt_rules_NPC回答规则.md`
- `game_docs/vectorized/19_dialogue_examples_NPC对话样例.md`
- 请求里的 `trust_level`

### 10.3 早期 NPC 泄露高等级真相

优先检查：

- 对应章节标题是否标了正确的 `解锁N`。
- `game_docs/npc_knowledge_matrix_v03.json` 是否把敏感文件分配给了普通 NPC。
- `python scripts\validate_game_docs_for_rag.py` 是否提示低解锁章节包含敏感词。
- 重新入库后再跑 `scripts\test_retrieve.py --suite quality`。

### 10.4 修改文档后没有生效

Markdown 改完不会自动进入 Milvus。需要重新执行：

```powershell
python scripts\ingest_game_docs_to_milvus.py
```

然后重启或继续使用 NPC 后端即可。
