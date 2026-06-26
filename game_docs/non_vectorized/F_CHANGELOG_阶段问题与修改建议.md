# 《归潮之岛》RAG 文档阶段问题总结与修改建议 v0.3

## 1. 本阶段出现的主要问题

### 1.1 旧 collection 没有重新入库

测试中曾出现 Karo 在 `unlock_level <= 1` 时检索到 `破损金属铭牌：后期真相`、`主共振核心室`、`RAG 使用建议` 等内容。这个问题的根源不是 Milvus 不能过滤，而是旧 collection 仍然保留了旧入库规则生成的数据，或者脚本修改后没有重新运行入库流程。

修改建议：

- 每次修改 Markdown 内容、NPC 权限或解锁等级后，必须重新执行入库脚本。
- 入库脚本应先删除旧 collection，再创建新 collection。
- 测试时必须同时检查 `npc_id`、`unlock_level`、`source_file`、`section_title`。

### 1.2 早期线索和后期真相混在同一 chunk

典型问题是“破损金属铭牌”。早期玩家可以看到 `Project ECHO / Subject 07`，但早期 NPC 不能理解这些文字的真实含义。如果同一个 chunk 同时写“早期看不懂”和“后期证明主人公是人体试验体”，就会导致 Karo 这类 NPC 被检索结果污染。

修改建议：

- 关键线索拆成“早期可见信息”和“后期真相”。
- 早期 chunk 只写角色能看到、听到、感受到的表层信息。
- 后期 chunk 才写身份确认、父母、保护仓、低温休眠、实验记录等信息。

### 1.3 游戏内知识和开发者说明混在一起

部分文件中出现“Unity 实现建议”“玩法功能”“RAG 使用建议”“Demo 初版建议”等内容。这些内容是给开发者看的，不应进入 NPC 检索结果。否则 NPC 可能说出“触发器”“known_clues”“Demo 初版”等破坏沉浸感的话。

修改建议：

- `game_docs` 只保留游戏世界内可能存在的知识。
- Prompt 规则、任务逻辑、开发建议、入库规则单独放在非入库文件中。
- 入库脚本应过滤标题中含有“RAG 使用建议”“Unity 实现建议”“不应知道”“开发者说明”的章节。

### 1.4 NPC 档案不应进入普通向量库

`07_npc_profiles_角色档案.md` 是构建 prompt 的配置文档，不是游戏世界内资料。如果入库，Karo 可能检索到 Lab Terminal 的知识边界，从而间接知道 Project ECHO、Subject 07、父母记录等信息。

修改建议：

- `07_npc_profiles_角色档案.md` 不进入 Milvus。
- 该文件由后端在生成 prompt 时读取。
- NPC 的 `knows`、`does_not_know`、`speech_style` 是系统约束，而不是检索知识。

### 1.5 任务文档不应与 NPC 世界知识混库

`09_quest_guide_任务线.md` 包含后期任务、结局、最终真相、解锁等级等开发逻辑。如果进入 NPC RAG，早期 NPC 可能检索到任务 7、任务 8，从而提前知道 Subject 07、父母、低温休眠、关闭装置等内容。

修改建议：

- `09_quest_guide_任务线.md` 不进入 NPC 普通知识库。
- 任务系统单独使用 `current_quest`、`known_clues`、`inventory` 控制。
- 如需任务提示，建议单独建立 `guichao_quest_hints` collection。

## 2. v0.3 修改重点

1. 将公共世界观改为“地表可知信息”，减少早期水晶和地下设施细节。
2. 将水晶矿脉按“原住民传说、弱影响、装置激发、高阶真相”分层。
3. 移除地点文档中的 Unity 实现描述。
4. 将道具文档中的“用途”“玩法功能”改成“可见信息”和“后期解释”。
5. 明确 `07_npc_profiles` 与 `09_quest_guide` 不入库。
6. 增强 NPC 权限边界，尤其限制 Karo、Nia、Lira 解释 Project ECHO 真相。
7. 增加 `npc_knowledge_matrix_v03.json`，便于后端权限过滤。

## 3. 推荐入库文件

```text
01_world_lore_世界观.md
02_crystal_vein_水晶矿脉.md
03_indigenous_people_原住民与岛屿社会.md
04_shadow_organization_反政府科技组织.md
05_player_background_主人公背景.md
06_locations_地点设定.md
08_items_道具与线索.md
10_hidden_truth_隐藏真相.md
```

## 4. 不建议入库文件

```text
00_README_使用说明.md
07_npc_profiles_角色档案.md
09_quest_guide_任务线.md
11_rag_metadata_schema_RAG元数据设计.md
12_npc_prompt_rules_NPC回答规则.md
CHANGELOG_阶段问题与修改建议.md
```

## 5. 下一阶段建议

下一阶段可以开始实现 FastAPI 的 `/npc/chat` 接口。接口输入不应只有问题，还应包括 `npc_id`、`player_location`、`current_quest`、`unlocked_story_level`、`inventory`、`known_clues` 等状态。

检索必须至少使用：

```text
npc_id == 当前 NPC and unlock_level <= player.unlocked_story_level
```

然后结合 NPC 档案和 Prompt 规则生成回答。
