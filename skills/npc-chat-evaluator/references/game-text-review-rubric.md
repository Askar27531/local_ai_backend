# Game Text Review Rubric

这份 reference 只用于在测试报告和召回结果中评价游戏文本质量，并提出改进意见。不要在 `npc-chat-evaluator` skill 中直接修改 `game_docs/`；真正的文本修改应交给后续专门的 game text skill 或人工确认。

## 评价目标

评价游戏文本是否支持稳定的 NPC QA、剧情分层和 RAG 召回：

- canon 是否清晰：关键事实是否有明确、可引用的表述。
- unlock layering 是否明确：同一事实在不同 `unlock_level` 下是否有分层说法。
- NPC knowledge boundary 是否清楚：哪个 NPC 知道什么、不知道什么、愿不愿说。
- retrieval friendliness 是否足够：重要事实是否有可检索关键词、标题和上下文。
- spoiler safety 是否稳定：早期文本是否避免直接暴露后期真相。
- voice consistency 是否可靠：文本是否能支撑 NPC 口吻，而不是把别人的口吻混进当前 NPC。

## 常见文本问题

- 事实只以隐喻表达，导致 embedding 难以召回。
- 同一事实散落在多个文档，缺少明确的主来源。
- source 标题和正文关键词不匹配，导致 source coverage case 召回不到目标文档。
- early-level 文本包含过强剧透词，容易污染早期 NPC 回答。
- dialogue examples 太强，压过世界观、地点、物品等事实文档。
- NPC 人设文本没有写清“知道但不说”和“确实不知道”的区别。
- 物品、地点、症状、传闻之间缺少显式连接句。

## 输出建议格式

当发现文本问题时，只提出建议：

```text
Text issue: <简述问题>
Affected source: `<source_file>`
Evidence: <召回/报告中的证据>
Risk: retrieval | spoiler | persona | canon | test
Suggested text direction: <建议增加、澄清或拆分的内容方向，不直接改原文>
Handoff: game-text-reviewer
```

## 判断测试还是文本问题

- 如果测试期望与当前 canon 不一致，优先标记为 `test`。
- 如果 canon 缺失或太含糊，标记为 `data`，并提出文本补强建议。
- 如果 canon 清楚但召回不到，标记为 `retrieval` 或 `ingestion`。
- 如果召回正确但回答泄露/跑偏，标记为 `prompt` 或 `generation`。
- 如果文本修改可能影响剧情设定，交给专门文本 skill，不在本 skill 中直接执行。
