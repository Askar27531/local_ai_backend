# npc_app Agent 开发岗位面试问题单与回答稿

这份题单按当前 `npc_app` 的真实实现来准备，重点覆盖 Agent 开发岗常问的 Agent 架构、LangGraph state、RAG、记忆、工具/外部系统、评估、安全和工程化落地。

## 项目一句话介绍

`npc_app` 是一个基于 FastAPI 的游戏 NPC 对话后端。它用 LangGraph 编排一次 NPC 回复流程，用 Milvus 做游戏知识和长期记忆检索，用 SQLAlchemy/SQLite 保存用户、线程、聊天记录和记忆摘要，用本地 Ollama 模型生成流式回答。

可以这样开场：

> 我做的是一个游戏 NPC 对话 Agent 后端。用户和某个 NPC 对话时，系统会先确定用户线程和 NPC 身份，加载最近对话、长期记忆摘要、可检索记忆条目，再用 Milvus 检索当前 NPC 可访问的世界知识。之后 LangGraph 把流程拆成检索上下文、构建 prompt、生成回答三个节点，最后以 NDJSON 流式返回，并把本轮对话保存到数据库，定期更新长期记忆。

## 1. 你的项目是不是 Agent？和普通 Chatbot 有什么区别？

**项目匹配点**

- 普通 Chatbot 通常是“用户问题 + 历史记录 -> LLM 回复”。
- `npc_app` 多了状态、RAG 检索、长期记忆、角色规则、剧情解锁控制和流式执行流程。
- 当前还不是完全自主规划型 Agent，更接近“受控 workflow Agent”。

**可以这样回答**

> 我会把它定义为一个受控型 NPC Agent，而不是完全自由的通用 Agent。它不是简单把用户输入直接发给模型，而是会根据用户、线程、NPC、信任度、剧情解锁等级等状态，检索长期记忆和世界知识，再构造符合 NPC 人设和剧情权限的 prompt。它的行为被 LangGraph workflow 约束，节点顺序是固定的，所以可控性比完全自主 Agent 更强，也更适合游戏 NPC 场景。

**可能追问**

- 为什么不用完全自主 Agent？
- workflow 和 Agent 的边界在哪里？

**补充回答**

> 游戏 NPC 更看重一致性、安全边界和剧情不可剧透，所以我更倾向用确定性 workflow 包住 LLM 能力。模型负责语言生成和少量记忆抽取，流程控制、检索、权限过滤和保存由代码负责。

## 2. 整体架构是怎样的？

**项目匹配点**

- Web 框架：FastAPI。
- 对话入口：`POST /npc/chat/stream`。
- 编排：LangGraph `StateGraph`。
- 检索：Milvus。
- 记忆持久化：SQLite/SQLAlchemy + Milvus。
- 模型：`ChatOllama`。

**可以这样回答**

> 架构上分为 API 层、服务层、存储层和模型/RAG 层。API 层用 FastAPI 暴露注册登录、线程列表、聊天记录和 NPC 流式聊天接口。服务层负责线程、记录、记忆、RAG 和 prompt 构建。存储层用 SQLAlchemy 保存用户、线程、聊天记录、线程摘要和长期记忆条目。RAG 层用 Milvus 检索游戏文档和长期记忆。一次聊天请求进入后，会经过线程确认、历史加载、记忆检索、LangGraph 执行、流式输出、记录保存和记忆更新。

## 3. LangGraph 在项目里负责什么？

**项目匹配点**

- `StateGraph(NpcGraphState)`。
- 三个节点：`retrieve_context`、`build_prompt`、`generate_answer`。
- 执行方式：`graph.stream(...)`。

**可以这样回答**

> LangGraph 在这里主要负责把一次 NPC 回复拆成可观察、可扩展的步骤。当前图有三个节点：第一个节点检索游戏知识和来源，第二个节点根据记忆、最近对话和 RAG 结果构建 prompt，第三个节点调用 LLM 流式生成回答。这样做的好处是每一步输入输出都在 state 里，后续如果要加审核、工具调用、人类审批或者多分支路由，会比写一大段顺序代码更容易维护。

## 4. 当前的 state 是用来干嘛的？

**项目匹配点**

`NpcGraphState` 里包含：

- `request`
- `messages`
- `dialogue_history`
- `summary`
- `memory_items`
- `chunks`
- `sources`
- `system_prompt`
- `user_prompt`
- `answer`
- `used_rag`
- `error`

**可以这样回答**

> 当前 state 是一次图执行过程中的中间数据载体。初始 state 里放用户请求、最近对话、长期记忆摘要、检索到的记忆条目和用户问题。检索节点会写入 chunks、sources 和 used_rag；prompt 节点会写入 system_prompt 和 user_prompt；生成节点会写入 answer 和 messages。它主要解决节点之间共享上下文的问题。

**重要补充**

> 目前这个 state 不是跨请求持久化的记忆。跨请求记忆主要靠数据库里的聊天记录、线程摘要、长期记忆条目，以及 Milvus 里的记忆向量。项目里有 checkpointer service 的准备，但 graph.compile 当前没有接入 checkpointer。

## 5. 为什么 state 里有 messages，还要有 dialogue_history？

**项目匹配点**

- `messages` 是 LangGraph 消息状态，使用 `add_messages` reducer。
- `dialogue_history` 是从数据库取出的最近 6 条历史问答。

**可以这样回答**

> `messages` 更偏 LangGraph 当前执行过程中的消息轨迹，适合节点追加当前轮 human/AI message；`dialogue_history` 是业务层从数据库恢复出来的最近对话，用来支持跨请求上下文。因为目前没有启用 LangGraph checkpoint，所以不能只依赖 messages 来恢复历史。dialogue_history 才是当前跨请求短期上下文的主要来源。

## 6. 短期记忆怎么做？

**项目匹配点**

- 每个 thread 保存聊天记录。
- 每次请求取最近 6 条记录。
- 转成 `(question, answer)` 传入 prompt。

**可以这样回答**

> 短期记忆是基于当前 thread 的最近聊天记录实现的。每次用户发起 NPC 对话，系统先查出该线程历史记录，然后只取最近 6 条问答作为 dialogue_history。这部分用于处理代词、省略、连续追问和短期上下文。

**为什么只取 6 条**

> 主要是为了控制 prompt 长度和噪声。游戏 NPC 对话通常最近几轮对当前回复最关键，过老的内容会转入长期记忆摘要或长期记忆条目。后面还可以根据 token budget 动态调整，而不是固定 6 条。

## 7. 长期记忆怎么做？

**项目匹配点**

- `NpcThreadMemory` 保存线程摘要。
- `NpcMemoryItem` 保存可检索长期记忆条目。
- 默认每累计 6 条新聊天记录更新一次。
- LLM 抽取 JSON：`summary` + `items`。
- 记忆条目同步到 Milvus。

**可以这样回答**

> 长期记忆分两层。第一层是 thread summary，保存这个线程的简短概览，帮助模型理解长期连续性。第二层是结构化 memory items，比如玩家事实、NPC 已透露内容、未解决问题、关系信号等。系统不会每轮都更新长期记忆，而是默认累计 6 条新聊天记录后，由 LLM 把新增对话整理成 JSON，再写入数据库，并同步到 Milvus 供后续语义检索。

**亮点表达**

> 这样做的原因是长期记忆不应该等同于完整聊天记录。完整记录适合审计和回放，长期记忆适合压缩、检索和注入 prompt。

## 8. 长期记忆为什么既存 SQLite 又存 Milvus？

**项目匹配点**

- SQLite 是权威存储。
- Milvus 是向量检索索引。
- Milvus 失败时 fallback 到 SQLite 关键词/重要度打分。

**可以这样回答**

> SQLite 负责可靠持久化，是长期记忆的 source of truth。Milvus 负责语义检索，适合根据当前问题找相关记忆。这样可以兼顾可靠性和召回能力。如果 Milvus 不可用，系统会 fallback 到数据库候选项，通过关键词 overlap、importance 和 last_seen_at 进行简单打分，保证功能不会完全中断。

## 9. RAG 在项目里怎么做？

**项目匹配点**

- 游戏文档在 `game_docs/vectorized`。
- 使用 Milvus 检索游戏知识 chunk。
- 请求里有 `npc_id`、`unlocked_story_level`、`top_k`。
- 检索后还有 rerank 和剧情权限过滤。

**可以这样回答**

> 项目里有两类 RAG：一类是世界知识 RAG，一类是长期记忆 RAG。世界知识 RAG 会根据用户问题、NPC 身份、剧情解锁等级和 top_k 去 Milvus 检索游戏文档 chunk。检索后还会根据 NPC、信任等级和内容做 rerank，避免低信任 NPC 直接给出过多核心真相。长期记忆 RAG 则按 user、thread、npc 过滤，只召回当前 NPC 当前线程相关的记忆。

## 10. 如何避免 NPC 剧透或说出不该知道的信息？

**项目匹配点**

- `unlocked_story_level` 控制 chunk 可见性。
- `npc_id` 控制 NPC 可访问知识。
- `trust_level` 影响回答开放程度。
- prompt 明确禁止提到 RAG、Milvus、prompt、知识库等。
- context planner 会过滤敏感内容。

**可以这样回答**

> 我主要从检索层、规划层和 prompt 层三层控制。检索层根据 NPC 身份和剧情解锁等级筛选可用知识；规划层会过滤还没解锁的敏感内容；prompt 层再明确要求 NPC 只能基于当前可见信息回答，并且不能提到 RAG、Milvus、prompt 或知识库。这样即使底层文档里有完整设定，也不会直接暴露给当前阶段的玩家。

## 11. Prompt 是怎么构建的？

**项目匹配点**

- `build_npc_system_prompt(...)`。
- 输入包括 NPC 请求、RAG chunks、dialogue history、memory summary、memory items、context strategy。
- 构建前经过 `plan_prompt_context` 和 `budget_prompt_context`。

**可以这样回答**

> Prompt 构建不是把所有上下文平铺进去，而是先做 context planning，再做 budget。planning 会把长期记忆分成 must_use、already_said、repeat_or_open_loop、tone_only 等类别，告诉模型这些上下文应该如何使用。budget 会限制摘要、记忆条目、最近对话和 RAG chunk 的字符数。最终 system prompt 包含 NPC 人设、行为规则、可用知识、近期对话和长期记忆。

## 12. 为什么要做 context planning？

**项目匹配点**

- 避免把所有记忆当成同等证据。
- 区分人设语气、已说过事实、必须使用的玩家事实、未解决问题。

**可以这样回答**

> 因为记忆不是越多越好，也不是所有记忆都应该同等影响回答。例如 relationship_signal 更适合影响语气，不应该当成剧情事实；npc_disclosed 表示 NPC 已经说过，可以帮助避免重复或保持一致；unresolved_question 用来处理玩家反复追问或未完成话题。所以我加了一层 context planner，把记忆按用途分类，再交给 prompt 生成。

## 13. 如何处理 token 长度和上下文污染？

**项目匹配点**

- `prompt_budget_service` 限制 summary、memory items、dialogue、world RAG 字符数。
- 最近对话按倒序预算。
- chunk 和 memory item 都会截断。

**可以这样回答**

> 我没有把全部上下文直接塞进模型，而是做了预算控制。summary、memory items、recent dialogue、world RAG chunks 都有独立字符上限。最近对话从最新开始保留，长期记忆按 score 排序，RAG chunk 控制内容长度。这样能降低 token 成本，也能减少无关上下文干扰模型。

## 14. 工具调用在这个项目里体现在哪里？

**项目匹配点**

- 当前没有开放式 function calling 工具循环。
- 外部系统调用包括 Milvus 检索、SQLite 数据库、Ollama LLM、认证服务。
- 这些由代码确定性调用，不交给模型自主选择。

**可以这样回答**

> 当前项目没有做开放式 function calling，也没有让模型自主决定调用哪些工具。它更像受控工具使用：代码负责调用数据库、Milvus 和本地模型。这样可控性更强，适合游戏 NPC 场景。后续如果要加工具调用，比如查询任务状态、背包、世界事件，我会先设计严格的 tool schema、权限边界和 human-in-the-loop，再让 Agent 在有限工具集合内选择。

## 15. 为什么不用模型自己决定是否检索？

**项目匹配点**

- 当前每轮固定走检索上下文节点。
- 高可控 NPC 场景比自主选择更稳。

**可以这样回答**

> 目前设计上每轮都会经过检索节点，因为游戏 NPC 对话对一致性要求高，宁愿用确定性流程保证上下文可用。是否使用检索结果则可以在 prompt 和回答生成阶段体现。如果后续考虑降成本，可以加一个路由节点，先判断问题是否需要 RAG，比如闲聊、情绪反馈可以跳过世界知识检索。

## 16. 流式输出怎么实现？

**项目匹配点**

- FastAPI `StreamingResponse`。
- media type 是 `application/x-ndjson`。
- LangGraph `graph.stream(..., stream_mode=["custom", "updates"])`。
- LLM token 通过 `answer_delta` 事件输出。

**可以这样回答**

> 接口返回的是 NDJSON 流。后端先发 thread 信息，再通过 LangGraph stream 输出检索状态、sources、answer_delta 和 done 事件。生成节点内部调用 LLM 的 stream，把每个 token 或文本片段包装成 answer_delta 返回。这样前端可以边生成边展示，也能显示检索状态和来源。

## 17. 如果 Milvus 检索失败怎么办？

**项目匹配点**

- 世界知识检索失败会写 error 事件，并返回空 chunks。
- 记忆检索失败会返回空列表，fallback 到 SQLite。

**可以这样回答**

> 世界知识 RAG 如果 Milvus 抛异常，节点会捕获异常，通过 stream 返回 error 事件，并把 chunks、sources 置空。长期记忆检索如果 Milvus 不可用，会 fallback 到数据库里的 memory items，按关键词重叠和重要度打分。这样系统会降级，而不是直接崩溃。

## 18. 如何评估这个 NPC Agent 的效果？

**项目匹配点**

当前可以从这些维度做评估：

- 人设一致性。
- 是否遵守剧情解锁。
- RAG 依据是否相关。
- 是否拒绝不知道的问题。
- 记忆连续性。
- 是否泄露系统实现。
- 延迟、成本和错误率。

**可以这样回答**

> 我会分离评估生成质量和检索质量。检索质量看 relevant chunk recall、source relevance、是否检索到当前 NPC 可见信息。生成质量看角色一致性、剧情不剧透、回答是否基于上下文、是否自然承接短期和长期记忆。工程指标看首 token 延迟、总延迟、Milvus 错误率、LLM 错误率和每轮 token 成本。测试数据可以从典型 NPC、不同信任等级、不同剧情解锁阶段构造 golden cases。

## 19. 如何防 prompt injection？

**项目匹配点**

- 当前主要靠 prompt 规则和剧情权限过滤。
- 还可以加强文档清洗、检索结果隔离、工具权限和输出审计。

**可以这样回答**

> 当前项目的风险主要来自 RAG 文档或用户输入诱导 NPC 忽略规则。已有措施包括检索层按剧情权限过滤，prompt 中明确角色边界和禁止暴露系统实现。进一步增强的话，我会把检索内容标记为不可信上下文，要求模型只把它当资料而不是指令；对工具调用做 allowlist 和最小权限；对高风险输出做规则检查，比如禁止透露 prompt、Milvus、系统消息和未解锁剧情。

## 20. 为什么没用 LangGraph checkpoint 做长期记忆？

**项目匹配点**

- 有 `checkpointer_service.py`，但当前图 `compile()` 没传 checkpointer。
- 当前长期记忆由业务数据库和 Milvus 管理。

**可以这样回答**

> checkpoint 更适合保存图执行状态，比如中断恢复、多轮图状态延续、人类审批后恢复等。我的长期记忆是业务语义层面的记忆，需要可查询、可审计、可按用户/thread/NPC 过滤，并且要同步向量检索，所以我把它放在数据库和 Milvus 里。当前项目里已经预留了 checkpointer service，但还没有接入 graph.compile。后续如果要支持任务中断恢复或复杂多步 Agent，我会接入 checkpointer。

## 21. 如果让你把这个项目升级成更标准的 Agent，你会怎么做？

**可以这样回答**

> 我会分三步。第一步加入路由节点，判断本轮是闲聊、记忆问题、世界知识问题还是高风险问题，减少不必要检索。第二步加入受控工具调用，例如查询任务状态、玩家背包、NPC 关系、世界事件，但所有工具都有 schema、权限和超时。第三步加入 checkpoint 和 observability，支持中断恢复、人工审批、失败回放和评估看板。

## 22. 如果面试官问“你项目最大的问题是什么？”

**可以这样回答**

> 我觉得当前最大的改进点有三个。第一，LangGraph checkpoint 还没真正接入，所以 state 只在单次请求内流转，复杂多步任务的恢复能力不足。第二，长期记忆抽取依赖 LLM 输出 JSON，虽然做了 JSON 解析容错，但还可以加入 schema validation 和失败重试。第三，评估体系还可以更系统化，例如为不同 NPC、信任等级和剧情阶段构造固定评测集，持续监控角色一致性、剧透率和检索命中率。

这个回答的好处是：主动承认不足，但每个不足都对应清晰改进方向。

## 23. 如果问“你最有技术含量的设计是什么？”

**可以这样回答**

> 我认为比较有价值的是把 NPC 对话上下文拆成四类：短期对话、线程摘要、长期记忆条目和世界知识 RAG。短期对话负责连续性，线程摘要负责压缩长期上下文，长期记忆条目负责可检索的玩家/NPC 关系事实，世界知识 RAG 负责游戏设定。然后再通过 context planner 和 prompt budget 控制它们如何进入 prompt，而不是直接把所有内容拼起来。这让 NPC 既能记住玩家，又不会无限增长上下文，也能控制剧情权限。

## 24. 如果问“你如何定位一次回答不准？”

**可以这样回答**

> 我会按链路拆分。先看请求参数是否正确，比如 npc_id、trust_level、unlocked_story_level。再看 Milvus 是否检索到正确 chunks，source 是否相关。然后看长期记忆是否召回了错误或过期内容。接着看 prompt budget 是否把关键上下文裁掉。最后看 LLM 生成是否违反 prompt。因为流程是 LangGraph 节点化的，所以每个节点的输入输出都可以记录和复现。

## 25. 如果问“这个项目如何上线？”

**可以这样回答**

> 上线时我会关注四块：可靠性、观测、安全和评估。可靠性包括数据库迁移、Milvus 可用性、LLM 超时重试、流式连接断开处理。观测包括记录每次请求的 thread_id、npc_id、检索来源、token 数、延迟和错误。安全包括鉴权、用户隔离、工具权限和 prompt injection 防护。评估包括固定 golden cases、线上 bad case 收集和回归测试。

## 项目对应知识点速查

| 面试知识点 | npc_app 对应实现 |
| --- | --- |
| FastAPI 服务 | `npc_app/main.py` |
| 流式对话接口 | `/npc/chat/stream` |
| LangGraph state | `NpcGraphState` |
| LangGraph 节点 | `retrieve_context`、`build_prompt`、`generate_answer` |
| 短期记忆 | 当前 thread 最近 6 条 `NpcChatRecord` |
| 长期摘要 | `NpcThreadMemory.summary` |
| 长期记忆条目 | `NpcMemoryItem` |
| 世界知识 RAG | `retrieve_game_chunks` + Milvus |
| 长期记忆 RAG | `retrieve_memory_items_from_milvus` |
| Prompt 规划 | `plan_prompt_context` |
| Prompt 预算 | `budget_prompt_context` |
| LLM | `ChatOllama` |
| 持久化 | SQLAlchemy + SQLite |
| checkpoint | 有 service 预留，但当前未接入 graph |

## 面试前建议背熟的 5 句话

1. 我这个项目不是完全自由 Agent，而是受控 workflow Agent，更适合剧情和人设边界强的游戏 NPC 场景。
2. LangGraph state 负责单次请求中节点之间的数据流转，跨请求记忆由数据库和 Milvus 负责。
3. 短期记忆用最近 6 条对话，长期记忆用线程摘要和可检索 memory items。
4. RAG 不只是向量检索，还要结合 NPC 身份、剧情解锁、信任等级和 prompt 预算。
5. 如果要生产化，我会补 checkpoint、评估集、观测日志、prompt injection 防护和更严格的 schema validation。

