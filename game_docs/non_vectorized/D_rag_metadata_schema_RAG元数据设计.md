---
source: 11_rag_metadata_schema_RAG元数据设计.md
rag_ingest: false
document_role: developer_reference
---

# 11 RAG 元数据设计与维护规范

> 本文件是开发维护说明，不进入普通 NPC RAG。它用于保证后续扩展世界文本时，不破坏 NPC 权限、剧情解锁和防剧透边界。

## 1. 目标

本项目的 RAG 文档体系要同时满足四个目标：

1. 让 NPC 能检索到足够丰富的世界、地点、道具、关系和对话风格文本。
2. 防止 NPC 越权回答自己不该知道的内容。
3. 防止玩家在低剧情等级检索到终局真相。
4. 让任务状态、道具、地点和 NPC 个性共同影响回答，而不是让模型像百科一样解释世界。

当前最小检索过滤条件是：

```text
npc_id == 当前 NPC and unlock_level <= player.unlocked_story_level
```

后续可以继续结合 `current_quest`、`player_location`、`inventory`、`known_clues` 和 `visited_locations` 做 rerank 或提示词加权。

## 2. 当前入库文件

以下文件属于可入库游戏世界知识，必须在 `scripts/ingest_game_docs_to_milvus.py` 的 `INGEST_FILE_PREFIXES` 中存在，并在 `game_docs/manifest_v03.json` 的 `ingest_recommended` 中登记：

```text
01_world_lore_世界观.md
02_crystal_vein_水晶矿脉.md
03_indigenous_people_原住民与岛屿社会.md
04_shadow_organization_反政府科技组织.md
05_player_background_主人公背景.md
06_locations_地点设定.md
08_items_道具与线索.md
10_hidden_truth_隐藏真相.md
13_npc_memories_记忆与传闻.md
14_npc_relationships_人物关系网.md
15_timeline_岛屿时间线.md
16_daily_life_静潮村日常.md
17_npc_personal_arcs_个人经历链.md
18_rumor_pool_村中传闻.md
19_dialogue_examples_NPC对话样例.md
20_village_events_村庄事件.md
21_environmental_storytelling_环境叙事.md
22_ending_reactions_结局反应.md
23_rumor_truth_layers_传闻真相分层.md
24_player_choice_consequences_玩家行为后果.md
```

这些文件会按二级标题 `##` 切块。每个 `##` 章节会成为一个基础 chunk，再按可访问 NPC 展开成多条 Milvus 记录。

## 3. 当前不入库文件

以下文件不应进入普通 NPC RAG：

```text
00_README_使用说明.md
07_npc_profiles_角色档案.md
09_quest_guide_任务线.md
11_rag_metadata_schema_RAG元数据设计.md
12_npc_prompt_rules_NPC回答规则.md
CHANGELOG_阶段问题与修改建议.md
```

原因：

- `07_npc_profiles` 是 system prompt 角色配置，不是普通可检索知识。
- `09_quest_guide` 是任务系统和开发逻辑，不能让 NPC 检索到“任务设计”语言。
- `11_rag_metadata_schema` 是开发维护说明。
- `12_npc_prompt_rules` 是 system prompt 规则。
- `CHANGELOG` 是开发记录。

## 4. Markdown frontmatter 规范

可入库世界文档推荐使用：

```yaml
---
source: 06_locations_地点设定.md
default_unlock_level: 0
rag_ingest: true
recommended_topics:
  - locations
  - exploration
---
```

非入库开发或 prompt 文档推荐使用：

```yaml
---
source: 12_npc_prompt_rules_NPC回答规则.md
rag_ingest: false
document_role: prompt_rules
---
```

注意：当前入库脚本主要通过文件名前缀控制是否入库，`rag_ingest` 是文档语义标记，不是唯一控制开关。因此新增入库文档时必须同步更新脚本前缀和 manifest。

## 5. 解锁N 标题规范

当前脚本支持在章节标题中写 `解锁N`，例如：

```md
## 雾林石碑拓片（解锁3）
## 主共振核心室：最终区域（解锁5）
## Karo：地点询问样例（解锁1）
```

凡是以下前缀文件，标题中的 `解锁N` 会直接作为该 chunk 的 `unlock_level`：

```text
01_ 02_ 03_ 04_ 05_ 06_ 08_ 13_ 14_ 15_ 16_ 17_ 18_ 19_
```

建议：

- 公开生活、地点、社会常识：`解锁0` 或 `解锁1`
- 中期调查、道具联系、石碑、灯塔传闻：`解锁2` 或 `解锁3`
- 地下设施前段、Project ECHO 确认、灯塔下层：`解锁4`
- Subject 07、父母、保护仓、主共振核心、结局：`解锁5`

不要把终局真相写进低解锁章节，即使标题没有写 `解锁5`，内容级兜底也可能会把它抬高，但不要依赖兜底。

## 6. spoiler_level 映射

脚本根据 `unlock_level` 自动生成 `spoiler_level`：

```text
0-1 -> low
2   -> medium
3-4 -> high
5   -> final
```

这只是辅助字段。真正的过滤仍以 `npc_id` 和 `unlock_level` 为准。

## 7. NPC 权限矩阵维护

`game_docs/npc_knowledge_matrix_v03.json` 是文件级 NPC 权限入口。

每个 NPC 都有：

```json
{
  "role": "老渔夫",
  "allowed_sources": [
    "01_world_lore_世界观.md",
    "06_locations_地点设定.md"
  ],
  "max_default_unlock_level": 2,
  "must_not_reveal": []
}
```

维护原则：

- 新增可入库文件后，如果 NPC 需要检索它，必须加入该 NPC 的 `allowed_sources`。
- 只靠 `allowed_sources` 不够；高风险章节还要在 `restrict_npcs_for_chunk()` 中做章节级限制。
- 普通地表 NPC 不应访问 `10_hidden_truth`。
- Orin 和 Venn 虽然知道高风险片段，但也不应直接访问 `10_hidden_truth`；应通过 `04/05/06/08/13/14/15/17/19` 中拆分后的片段表达罪疚、猜测和记忆污染。
- Lab Terminal 不应访问普通村口闲聊类文件，除非是对话样例或终局相关。
- `max_default_unlock_level` 是设计参考，目前主要过滤由查询请求的 `unlocked_story_level` 控制。

## 8. 文件级与章节级权限

当前权限分两层：

第一层：文件级权限。

```python
allowed_npcs_by_source = get_allowed_npcs_for_source(path.name, npc_matrix)
```

第二层：章节级权限。

```python
allowed_npcs = restrict_npcs_for_chunk(...)
```

章节级权限用于防止以下情况：

- Karo 检索到保护仓或父母真相。
- Nia 检索到 Project ECHO 组织结构。
- Lira 提前解释主共振核心。
- Mara 用科学术语解释装置。
- Lab Terminal 讲普通村民闲话。

## 9. 标题路由规范

部分文档依赖章节标题来识别所属 NPC。

### 13 / 17 / 19

以下文件按标题前缀路由：

```text
13_npc_memories_记忆与传闻.md
17_npc_personal_arcs_个人经历链.md
19_dialogue_examples_NPC对话样例.md
```

标题必须使用：

```md
## Karo：...
## Lira：...
## Orin：...
## Nia：...
## Venn：...
## Elder Mara：...
## Lab Terminal：...
```

`19` 还允许：

```md
## 通用：...
```

通用样例会给所有 NPC 使用。

### 20 / 22 / 24

以下文件主要用于地表 NPC 的生活反应、结局反应和玩家行为后果：

```text
20_village_events_村庄事件.md
22_ending_reactions_结局反应.md
24_player_choice_consequences_玩家行为后果.md
```

原则：

- 只给地表 NPC 使用。
- Lab Terminal 不应检索这些文件来进行情感安慰或道德判断。
- 标题必须写 `（解锁N）`，终局反应统一使用 `解锁5`。

### 21

`21_environmental_storytelling_环境叙事.md` 按地点深度路由：

- 地表和雾林细节给地表 NPC。
- 灯塔下层、地下入口、人体观测区和主核心相关细节给 Orin、Venn、Elder Mara、Lira 或 Lab Terminal 中合适的角色。
- 地下细节不应让 Karo、Nia 或普通闲聊检索到完整解释。

### 23

`23_rumor_truth_layers_传闻真相分层.md` 必须把同一传闻拆成独立的低阶、中阶、高阶章节，不允许在 `解锁1` chunk 中同时写终局真相。

原则：

- 低阶传闻给地表 NPC。
- 中阶解释给 Lira、Orin、Venn、Elder Mara 等能推测的人。
- 高阶真相只给 Orin、Venn 和 Lab Terminal 中合适的高解锁上下文。
- `Subject 07` 的终局确认只给 Orin、Venn 和 Lab Terminal，不能给 Karo、Nia 或普通村民。

### 14

`14_npc_relationships_人物关系网.md` 会根据标题中出现的角色名路由。例如：

```md
## Karo 与 Orin：码头和灯塔之间的不信任（解锁1）
```

该 chunk 会给 Karo 和 Orin。

### 15

`15_timeline_岛屿时间线.md` 按章节主题做权限限制。公开历史给地表 NPC，Project ECHO、地下核心等给深层角色。

## 10. 新增可入库文档流程

新增一个可入库文档时，必须检查以下步骤：

1. 文件名使用新编号前缀，例如 `20_xxx.md`。
2. 添加 frontmatter，设置 `rag_ingest: true` 和 `default_unlock_level`。
3. 每个 `##` 标题尽量写 `（解锁N）`。
4. 在 `scripts/ingest_game_docs_to_milvus.py` 的 `INGEST_FILE_PREFIXES` 添加新前缀。
5. 在 `game_docs/manifest_v03.json` 的 `ingest_recommended` 添加文件名。
6. 在 `game_docs/npc_knowledge_matrix_v03.json` 中给需要访问的 NPC 添加 `allowed_sources`。
7. 如果文档有特殊路由规则，在 `restrict_npcs_for_chunk()` 中添加章节级限制。
8. 如果标题中的 `解锁N` 需要生效，确认 `section_unlock_override()` 的显式解锁前缀包含新前缀。
9. 运行 `python -m py_compile scripts\ingest_game_docs_to_milvus.py`。
10. 重建 Milvus：`python scripts\ingest_game_docs_to_milvus.py`。
11. 运行检索质量测试：`python scripts\test_retrieve.py`。

## 11. 新增非入库文档流程

新增 prompt、任务、开发说明类文档时：

1. 设置 `rag_ingest: false`。
2. 不要把文件前缀加入 `INGEST_FILE_PREFIXES`。
3. 添加到 `manifest_v03.json` 的 `do_not_ingest`。
4. 如果被后端 prompt 直接读取，确认路径在代码中正确。

适合非入库的内容：

- 任务状态机设计
- System prompt 规则
- 角色档案 prompt 配置
- 开发者说明
- 测试说明

## 12. 常见防剧透错误

错误：在 `解锁1` 章节中写“Subject 07 就是主人公”。

正确：早期只写“铭牌上有 Subject 07 字样，但没人知道含义”。

错误：让 Karo 解释主共振核心。

正确：Karo 只能说灯塔蓝光和海雾经验。

错误：让 Nia 检索 `10_hidden_truth`。

正确：Nia 只检索孩子视角传闻、日常、低剧透地点和对话样例。

错误：让 Orin 或 Venn 直接检索 `10_hidden_truth` 后解释完整终局真相。

正确：Orin 只讲灯塔下层、旧门禁、二次开门事故和守门罪疚；Venn 只讲旧资料、边界理论、Subject 残影和记忆污染。完整真相由 Lab Terminal、父母录音、实验日志和终局场景拼合。

错误：把 `09_quest_guide` 入库。

正确：任务系统使用 `09`，NPC 普通回答不检索任务设计文档。

错误：新增 `20_` 文档后只写了 Markdown，没有改脚本、manifest、matrix。

正确：三处配置都必须同步。

## 13. 检索质量测试

`scripts/test_retrieve.py` 是固定 RAG QA 套件。

常用命令：

```powershell
python scripts\test_retrieve.py --list
python scripts\test_retrieve.py
python scripts\test_retrieve.py --case level4_orin_lighthouse --verbose
python scripts\test_retrieve.py --json
python scripts\test_retrieve.py --question "Subject 07 是谁？" --npc-id Lab_Terminal --unlock-level 5
```

测试覆盖：

- 每个主要 NPC。
- 解锁等级 0 到 5。
- 关键道具和地点问题。
- 错问 NPC 的拒答场景。
- 早期防剧透。
- 终局 Subject 07、保护仓、父母权限真相。
- 命中结果的 `npc_id` 是否正确。
- 命中结果的 `unlock_level` 是否不超过当前等级。
- 禁止来源文件是否没有出现。

示例期望：

- Nia 解锁1问 Project ECHO，不应检索 `10_hidden_truth`。
- Orin 解锁4问灯塔，应检索 `06/08/13/14/19` 中的相关片段。
- Lab Terminal 解锁5问 Subject 07，应检索 `10/05/08/19` 中的终局片段。

## 14. 当前 Milvus schema

当前 collection：`guichao_island_chunks`。

每条记录字段：

```json
{
  "vector": "FLOAT_VECTOR",
  "chunk_uid": "string",
  "source_file": "string",
  "section_title": "string",
  "content": "string",
  "npc_id": "string",
  "unlock_level": "int",
  "topics": "string",
  "spoiler_level": "string"
}
```

每个基础 chunk 会按 `allowed_npcs` 展开。也就是说，同一段内容可能在 Milvus 中有多条记录，每条记录对应一个可访问 NPC。

## 15. 推荐章节写法

推荐：

```md
## Orin：灯塔下层入口试探（解锁4）

Orin 可以承认灯塔下层存在旧设施入口，但不能直接解释 Subject 07 或关闭归潮场方法。
```

不推荐：

```md
## Orin 的秘密

Orin 知道所有真相，包括 Subject 07、父母和关闭方法。
```

原因：标题没有解锁等级，内容过度剧透，且不符合 Orin 的知识边界。

## 16. 文档职责边界

简要职责：

- `01`：公共世界观和低剧透社会体验。
- `02`：蓝脉、水晶、回声病和共振机制。
- `03`：潮民社会、禁忌、仪式和文化。
- `04`：赫利俄斯结社和 Project ECHO 组织结构。
- `05`：玩家身份、主观体验和主角弧线。
- `06`：地点探索主干。
- `07`：NPC 角色档案，prompt 使用，不入库。
- `08`：道具与线索主干。
- `09`：任务线和状态机设计，不入库。
- `10`：终局隐藏真相。
- `11`：RAG 维护说明，不入库。
- `12`：NPC 回答规则，prompt 使用，不入库。
- `13`：NPC 个人记忆和传闻。
- `14`：NPC 关系网。
- `15`：岛屿时间线。
- `16`：静潮村日常。
- `17`：NPC 个人经历链。
- `18`：村中传闻池。
- `19`：NPC 对话样例。

后续扩展时，应先判断新内容属于哪类职责，避免同一真相在多个文件里用不同说法重复。
