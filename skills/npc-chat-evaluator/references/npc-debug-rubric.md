# NPC Debug Rubric

当任务需要更深入的评估、root-cause analysis，或需要整理 NPC 剧情/设定问答报告时，加载这份 reference。

## 质量结论

统一使用这些 verdict：

- `pass`：召回和回答都有依据，符合当前剧情/信任状态，并且保持人设安全。
- `warning`：回答基本可用，但缺少 preferred source、语气偏弱，或证据不够扎实。
- `fail`：回答违背 canon、泄露剧透、使用 forbidden source、忽略正确上下文、人设漂移，或 stream/test 未完成。
- `inconclusive`：现有 report 或 logs 不足以判断，需要补跑最小缺失检查。

## 召回检查

先评估 retrieval，再评估 generation：

- NPC 是否正确：返回 chunk 应匹配请求的 `npc_id`；除非代码明确允许公共/共享来源。
- 剧情等级是否正确：`unlock_level` 必须小于或等于请求里的 `unlocked_story_level`。
- 来源类型是否正确：早期村庄问题不应召回 hidden-truth 或 final-reveal 文档。
- 内容是否相关：chunk 必须真正能帮助回答玩家问题，不能只是共享关键词。
- source coverage 是否足够：如果已知存在权威来源，top results 里应至少出现一个相关 chunk。
- 信息是否多样：如果所有 chunk 都重复同一个弱线索，回答仍可能证据不足。

常见 retrieval root causes：

- 源文档缺少该事实。
- chunk 太大、太小，或和 heading 被切开。
- metadata 中的 `npc_id`、`unlock_level`、`spoiler_level` 或 `source_file` 错误。
- `_build_retrieval_question` 的 query expansion 过度偏向口吻样例，或偏离事实设定。
- `_rerank_chunks_for_request` 的 boost/penalty 过度提升风格样例，或压低了必要 lore。
- `top_k` 或 candidate count 对歧义 query 来说太低。

## 回答检查

从这些维度评估 generated answer：

- Grounding：每个具体事实都应由 retrieved chunks 或已建立的 public canon 支持。
- Persona：回答应像当前 NPC，而不是另一个 NPC、旁白、系统或通用助手。
- Knowledge boundary：NPC 不应知道超出其角色、`trust_level` 或剧情解锁范围的事实。
- Spoiler safety：早期等级不能揭露 Subject 07、Project ECHO、hidden truth、parental override、lab mechanisms 或 final choices，除非 case 明确允许。
- Output format：非终端 NPC 应只输出 spoken dialogue，不要 action beats、speaker labels、quotes 或 analysis。
- Length：大多数 NPC 回答应符合 prompt 里的 1 到 4 句限制。
- Refusal/unknown behavior：retrieval 为空或证据不足时，应以 NPC 的自然口吻表达不知道，而不是编造 canon。

## NPC 口吻锚点

这些只作为快速检查；准确规则优先看 `npc_prompt_service.py` 和角色档案：

- Karo：码头老渔夫，短硬，船、码头、旧规矩、海风和谨慎。
- Lira：克制的草药师，症状、接触时间、观察、低风险建议。
- Orin：守塔人，门、锁、钥匙、规矩、后果。
- Nia：孩子气，句子短而具体，贝壳、井边、害怕和好奇。
- Venn：破碎的研究者，地图、纸页、接口、记忆不可靠。
- Elder_Mara：长者，仪式、禁忌、代价、群体记忆。
- Lab_Terminal：冷静记录式输出，字段、权限、损坏索引。

## 报告模板

总结失败 case 时使用这个紧凑结构：

```text
Verdict: fail | warning | pass | inconclusive
Case/query: <case id or user query>
NPC/state: <npc_id>, unlock=<N>, trust=<N>, top_k=<K>
Evidence:
- Retrieval: <source files, sections, notable scores, forbidden/preferred source status>
- Answer: <short paraphrase or brief excerpt>
Root cause: data | ingestion | retrieval | prompt | generation | API | test
Fix: <minimal concrete change>
Verify: <single command>
```

## 修复选择

按层选择修复：

- 当 canon 缺失、矛盾，或表达太含糊导致召回困难时，改 `game_docs/`。
- 当 metadata 或 chunking 导致正确文档不可检索时，改 ingestion。
- 当 filtering 或 returned fields 错误时，改 `milvus_retriever_service.py`。
- 当正确 chunk 存在但排在无关 chunk 后面时，改 `_build_retrieval_question` 或 rerank logic。
- 当正确上下文已召回，但模型泄露、过度解释、切换说话人或违反输出格式时，改 `npc_prompt_service.py`。
- 当 expected source、forbidden source 或 forbidden answer term 已不符合当前 canon 时，改测试。

## 验证策略

用能证明修复的最小检查：

- Retrieval-only fix：运行相关 `scripts/test_retrieve.py` case 或 ad hoc query。
- Prompt/generation fix：运行相关 `scripts/test_npc_chat_stream.py` case。
- Broad rerank 或 ingestion fix：先跑 targeted case，时间允许再跑受影响 suite。
- Report-only fix：生成或检查 Markdown report，确认 failures/warnings 清晰。
