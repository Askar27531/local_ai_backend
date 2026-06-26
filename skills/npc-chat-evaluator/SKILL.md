---
name: npc-chat-evaluator
description: Test, evaluate, summarize, and advise on NPC story/lore/persona/RAG question-answering quality in the Guichao Island local AI backend. Use when the user asks to run or improve NPC chat tests, retrieval tests, source coverage checks, spoiler guardrail checks, Markdown test reports, or test-only fixes in scripts/test_npc_chat_stream.py and scripts/test_retrieve.py. This skill may suggest game text, prompt, retrieval, or API improvements, but should not directly modify production game text, prompt, RAG, or API code.
---

# NPC Chat Evaluator

使用这个 skill 诊断《归潮之岛》本地后端里的 NPC 剧情/设定问答测试问题。这个 skill 只负责测试、评估、总结和提出建议；不要直接修改生产游戏文本、NPC prompt、RAG 业务逻辑或 API 业务代码。

允许修改的范围只有测试文件中的测试问题：

- `scripts/test_retrieve.py`
- `scripts/test_npc_chat_stream.py`

如果证据指向 `game_docs/`、`npc_prompt_service.py`、retrieval/rerank、ingestion 或 API 代码，只提出建议和交接说明，不在本 skill 中直接修改。

## 核心文件

优先检查这些文件：

- `scripts/test_retrieve.py`：召回回归测试、ad hoc retrieval checks、source coverage。
- `scripts/test_npc_chat_stream.py`：端到端 NPC stream 测试、case 断言、Markdown 报告生成。
- `reports/`：测试输出报告和总结报告。
- `npc_app/services/npc_rag_service.py`：只读参考，理解 retrieval -> prompt -> generation 流程。
- `npc_app/services/milvus_retriever_service.py`：只读参考，理解 Milvus filter、score、metadata。
- `npc_app/services/npc_prompt_service.py`：只读参考，理解 NPC persona、spoiler guardrail、unknown answer。
- `game_docs/`：只读参考，判断 canon、文本召回友好度、剧情分层。

需要详细测试评分标准时，读取 `references/npc-debug-rubric.md`。

需要评价游戏文本是否适合测试/RAG/剧情分层时，读取 `references/game-text-review-rubric.md`。该 reference 只用于提出文本意见，不用于直接修改 `game_docs/`。

## 工作流程

1. 先判断问题类型。
   - `Retrieval miss`：没有召回相关 chunk，或召回了错误 `npc_id`、`unlock_level`、`source_file`。
   - `Retrieval contamination`：召回了 forbidden source 或当前剧情阶段不该出现的后期资料。
   - `Context ignored`：召回 chunk 正确，但 answer 矛盾或没有使用关键事实。
   - `Persona drift`：answer 像另一个 NPC、旁白、系统或通用助手。
   - `Guardrail failure`：answer 泄露 RAG、Milvus、prompt、AI 身份、隐藏真相或未解锁身份。
   - `Streaming/API issue`：缺少 `thread`、`sources`、`answer_delta` 或 `done` event。
   - `Test/report issue`：case 设计、expected source、forbidden term、strict warning、report 输出或参数传递错误。
   - `Game text issue`：canon 缺失、文本太隐喻、unlock 分层不清、source 不利于 retrieval。

2. 先验证 retrieval，再评估 generation。
   - 用 `scripts/test_retrieve.py --list` 找相关 case。
   - 针对具体问题运行 `scripts/test_retrieve.py --question "<question>" --npc-id <NPC> --unlock-level <N> --top-k <K> --verbose`。
   - 检查 `source_file`、`section_title`、`npc_id`、`unlock_level`、`spoiler_level`、`score` 和内容预览。

3. 验证端到端 NPC stream。
   - 用 `scripts/test_npc_chat_stream.py --list` 找 case。
   - 先运行最小相关 case：`--case <case_id>`。
   - 读取刚生成的 Markdown report，检查 `FAIL`、`WARN`、sources、answer、exit code。

4. 分层归因。
   - `data`：canon 缺失、含糊、重复、放错文档、unlock 分层不清。
   - `ingestion`：chunk metadata、`npc_id`、`unlock_level`、`source_file` 或 chunking 有问题。
   - `retrieval`：query expansion、candidate count、`top_k`、embedding、Milvus filter 或 rerank 有问题。
   - `prompt`：persona、spoiler guardrail、unknown answer 或输出格式约束不足。
   - `generation`：本地模型忽略上下文、过度泛化或稳定性不足。
   - `API`：stream event、auth、thread、records 或 backend endpoint 问题。
   - `test`：case、expected/forbidden sources、forbidden terms、report logic 或 strictness 问题。

5. 只在证据指向测试文件时修改测试。
   - 测试期望过期或不准确：可以修改测试文件。
   - report 输出或参数传递错误：可以修改测试文件。
   - forbidden term / expected source 覆盖不足：可以修改测试文件。
   - 生产代码、prompt、game_docs、retrieval/rerank 问题：只写建议，不直接修改。

6. 输出诊断时按这个顺序：
   - 用户请求或 failing case。
   - Verdict：`pass`、`warning`、`fail` 或 `inconclusive`。
   - Evidence：sources、answer 摘要、warnings/failures、文件引用。
   - Root cause：`data`、`ingestion`、`retrieval`、`prompt`、`generation`、`API` 或 `test`。
   - Test-only fix 或 non-test handoff suggestion。
   - Verification command。

## 常用命令

```powershell
python scripts\test_retrieve.py --list
python scripts\test_retrieve.py --case <case_id> --verbose
python scripts\test_retrieve.py --question "<question>" --npc-id Karo --unlock-level 1 --top-k 8 --verbose
python scripts\test_npc_chat_stream.py --list
python scripts\test_npc_chat_stream.py --case <case_id>
python scripts\test_npc_chat_stream.py --case <case_id> --strict-warnings
python scripts\run_skill_debug_agent.py --summarize-latest-report --skill npc-chat-evaluator --model qwen3:14b
python scripts\run_skill_debug_agent.py --run-chat-test-and-summarize --skill npc-chat-evaluator --model qwen3:14b --num-ctx 8192
python scripts\run_skill_debug_agent.py --tool-loop --allow-test-edits --skill npc-chat-evaluator --model qwen3:14b --num-ctx 8192 "<testing issue>"
```

## 受控测试修改流程

当用户要求“检查某类问题并修正测试流程”时：

1. 使用 `scripts/run_skill_debug_agent.py --tool-loop --allow-test-edits`。
2. 先读取 `scripts/test_retrieve.py` 和/或 `scripts/test_npc_chat_stream.py`。
3. 确认问题确实属于测试文件：case 设计、expected source、forbidden term、report 输出、参数传递或验证逻辑。
4. 只能通过 `edit_test_file` 修改两个测试文件中的测试问题；修改时必须使用精确 `old_text` 替换。
5. 修改时自动生成 `.bak_<timestamp>` 备份。
6. 修改后运行最小相关测试，并生成 `--output-md reports/<name>.md`。
7. 读取刚生成的 md 报告。
8. 使用 `write_summary_report` 写出总结报告，说明改了什么、测试结果、剩余问题和下一步应改进的层。

## 最新报告总结

当用户要求“根据最新报告生成总结报告”时：

1. 找到 `reports/` 下最新的 `npc_chat*.md`，不要把已有 summary/diagnosis 报告当作原始测试报告。
2. 优先抽取 header、所有 `FAIL` case、所有 `WARN` case 和关键统计。
3. 新报告必须说明：总体状态、主要问题、分层 root cause、下一步改进方向、验证命令。
4. 如果只有 `WARN` 没有 `FAIL`，也要说明残余风险和优先优化项。
5. 输出保存为新的 Markdown 文件，避免覆盖原始测试报告。

当用户要求“一次性跑测试并生成总结报告”时：

1. 使用 `scripts/run_skill_debug_agent.py --run-chat-test-and-summarize`。
2. 该模式会先运行 `scripts/test_npc_chat_stream.py` 并用 `--output-md` 写出新的原始测试报告。
3. 然后读取刚生成的原始测试报告，而不是旧的 latest report。
4. 最后生成 summary Markdown，指出主要问题、root cause 和下一步改进方向。
5. 如果测试脚本返回非 0，也仍然读取已生成报告并生成总结；非 0 exit code 本身也应作为风险说明。

## Memory

本 skill 使用 Postgres 线程式 memory，而不是 Markdown 文件记忆。

- 默认使用 `SKILL_MEMORY_DATABASE_URL`；未设置时复用 `DATABASE_URL`。
- memory 表：`agent_memory_threads`、`agent_memory_events`、`agent_memory_summaries`。
- 每次运行都绑定一个 memory thread；可用 `--memory-thread-id` 继续指定线程。
- prompt 中只加载 `agent_memory_summaries` 的压缩摘要；完整运行历史留在 `agent_memory_events`。
- memory 应记录测试运行、报告路径、失败/警告 case、root cause、用户确认过的判断和下一步建议。
- 临时不需要记忆时才使用 `--disable-memory`。

## 输出标准

- 诊断必须绑定到具体 sources、命令、报告或文件。
- NPC answer 流畅不等于正确；必须被 allowed retrieved context 或 canon 支持。
- 不要只看 `score` 判断 retrieval 好坏，必须检查实际内容。
- 面向用户总结时，不要泄露当前 `unlocked_story_level` 不允许的隐藏剧情。
- 只允许直接修改测试文件中的测试问题。
- 生产文本、prompt、retrieval 和 API 修改应作为建议交给其它 skill 或人工确认。
