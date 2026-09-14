# 《归潮之岛》RAG 知识库文档包使用说明

本目录是《归潮之岛》NPC RAG 系统的游戏知识源。这里的 Markdown 不只是普通设定文档，而是会被切分、分级、分配给不同 NPC，并最终进入 Milvus 向量数据库，供 `npc_app` 在运行时检索。

如果根目录的 `README_启动说明.md` 解释的是“后端怎么跑”，那么本文件解释的是“知识库怎么写、怎么入库、怎么避免 NPC 乱说”。

## 1. 项目定位

《归潮之岛》是一个 Unity 3D 探索 RPG Demo。玩家扮演一名在海滩醒来的失忆年轻男子。他无法离开归潮岛：无论从海上、森林、山崖还是其他方向出发，最终都会被海雾、异常潮流和方向错乱带回岛上。

岛上地表社会的技术水平接近 1950 年代。岛民使用木船、煤油灯、柴油发电机、手摇电话、短波无线电和纸质档案。岛下则隐藏着远高于地表社会的旧实验设施。

这个文档包的目标是支持“有知识边界的 NPC 自由问答”：

- 玩家可以自由向 NPC 提问。
- NPC 不主动剧透。
- NPC 只回答自己知道或愿意说的内容。
- NPC 的回答受剧情解锁等级和信任等级约束。
- 同一个问题问不同 NPC，会得到不同角度、不同保留程度的回答。
- 终局真相只能在正确阶段、正确 NPC 或实验终端中逐步揭开。

## 2. 核心剧情设定

归潮岛地下存在特殊蓝色水晶矿脉。原住民称其为“蓝脉石”或“潮心石”。水晶在自然状态下只会造成轻微磁异常、头痛、梦境异常和无线电噪声。只有被特定装置激发后，它才会形成大范围磁场扰动、神经感知干扰和“归潮场”。

大约 80 年前，虚构的反政府科技组织“赫利俄斯结社”发现矿脉，并以气象站、矿业勘探和无线电导航测试为名进入归潮岛。他们在地下建立实验设施，进行 `Project ECHO` 计划，试图研究水晶场对磁场、记忆和人体感知的影响，并尝试将其武器化。

主人公的父母是该组织的科研人员。关键实验中，巨型水晶被意外过度激活，引发爆炸和地震。实验室大部分人员死亡，归潮场失控并覆盖整座岛。主人公当时位于保护仓内，因紧急机制触发低温休眠。约 80 年后，一次小地震触发保护仓安全弹射，他被抛入海中，随后漂回岛上。

这些终局信息不能在早期直接暴露。文档、入库脚本和 NPC 提示词共同维护这条边界。

## 3. 文档分类

### 3.1 推荐入库的游戏世界知识

这些文件会作为 NPC 可检索知识进入 Milvus。它们主要描述世界、地点、道具、人物记忆、传闻、事件和结局反应。

```text
vectorized/01_world_lore_世界观.md
vectorized/02_crystal_vein_水晶矿脉.md
vectorized/03_indigenous_people_原住民与岛屿社会.md
vectorized/04_shadow_organization_反政府科技组织.md
vectorized/05_player_background_主人公背景.md
vectorized/06_locations_地点设定.md
vectorized/08_items_道具与线索.md
vectorized/10_hidden_truth_隐藏真相.md
vectorized/13_npc_memories_记忆与传闻.md
vectorized/14_npc_relationships_人物关系网.md
vectorized/15_timeline_岛屿时间线.md
vectorized/16_daily_life_静潮村日常.md
vectorized/17_npc_personal_arcs_个人经历链.md
vectorized/18_rumor_pool_村中传闻.md
vectorized/19_dialogue_examples_NPC对话样例.md
vectorized/20_village_events_村庄事件.md
vectorized/21_environmental_storytelling_环境叙事.md
vectorized/22_ending_reactions_结局反应.md
vectorized/23_rumor_truth_layers_传闻真相分层.md
vectorized/24_player_choice_consequences_玩家行为后果.md
```

### 3.2 不建议入库的配置与开发文档

这些文件用于说明、配置、提示词或开发管理，不应作为普通 NPC 知识直接入库。

```text
non_vectorized/A_README_使用说明.md
non_vectorized/B_npc_profiles_角色档案.md
non_vectorized/C_quest_guide_任务线.md
non_vectorized/D_rag_metadata_schema_RAG元数据设计.md
non_vectorized/E_npc_prompt_rules_NPC回答规则.md
non_vectorized/F_CHANGELOG_阶段问题与修改建议.md
```

其中：

- `non_vectorized/B_npc_profiles_角色档案.md` 会被 `npc_prompt_service.py` 读取，用来构建当前 NPC 身份。
- `non_vectorized/E_npc_prompt_rules_NPC回答规则.md` 会被 `npc_prompt_service.py` 读取，用来约束回答方式。
- `non_vectorized/C_quest_guide_任务线.md` 更适合任务系统或 Unity 逻辑，不适合让 NPC 当作事实知识自由检索。
- `non_vectorized/D_rag_metadata_schema_RAG元数据设计.md` 是开发说明。

### 3.3 JSON 配置文件

```text
manifest_v03.json
npc_knowledge_matrix_v03.json
```

`manifest_v03.json` 用于声明推荐入库文件和不入库文件。

`npc_knowledge_matrix_v03.json` 用于声明每个 NPC 允许访问哪些知识来源。入库脚本会结合这个矩阵，把一个普通 chunk 展开为多条带 `npc_id` 的 Milvus 记录。

## 4. RAG 入库机制

入库脚本位于：

```text
scripts/ingest_game_docs_to_milvus.py
```

执行命令：

```powershell
cd D:\local_ai_backend
.\.venv\Scripts\Activate.ps1
python scripts\ingest_game_docs_to_milvus.py
```

脚本处理流程：

1. 读取本目录的 Markdown 文件。
2. 根据 `manifest_v03.json` 的 `ingest_recommended` 筛选可入库文件。
3. 按二级标题 `##` 切分文档，每个二级章节是一个基础 chunk。
4. 读取 frontmatter 中的默认解锁等级和主题标签。
5. 根据章节标题中的 `解锁N` 或 `default_unlock_level` 得到 `unlock_level`。
6. 根据 `npc_knowledge_matrix_v03.json` 判断哪些 NPC 可以访问该来源文件。
7. 生成 embedding。
8. 删除并重建 Milvus collection：`guichao_island_chunks`。
9. 写入 `chunk + npc_id + unlock_level + source_file + section_title` 记录。

文档质量检查不放在入库脚本里，单独由下面的脚本负责：

```text
scripts/validate_game_docs_for_rag.py
```

## 5. 解锁等级

`unlock_level` 控制玩家当前剧情阶段能检索到什么内容。运行时检索条件是：

```text
npc_id == "<当前 NPC>" and unlock_level <= <玩家当前剧情等级>
```

推荐分级：

```text
unlock_level 0：公共常识，岛民日常、村落生活、普通归潮传闻。
unlock_level 1：玩家醒来后，通过普通村民和初始道具可知的信息。
unlock_level 2：调查码头、草药屋、大震夜传闻后可知的信息。
unlock_level 3：接近雾林、灯塔、长老和 Venn 后可知的信息。
unlock_level 4：进入灯塔下层或接触旧实验设施后可知的信息。
unlock_level 5：最终真相，主人公身份、父母、低温休眠、关键实验事故。
```

写文档时建议在二级标题中显式标注：

```markdown
## 解锁1：码头上的归潮传闻

## 解锁3：蓝脉石对梦境和方向感的影响

## 解锁5：Subject 07 与保护仓记录
```

如果需要检查哪些章节缺少 `解锁N`，运行：

```powershell
python scripts\validate_game_docs_for_rag.py
```

## 6. NPC 知识边界

当前主要 NPC：

```text
Karo
Lira
Orin
Nia
Venn
Elder_Mara
Lab_Terminal
```

### 6.1 Karo

码头、海、船、旧航海经验和离岛失败相关。他可以讲海雾、归航、旧船和码头传闻，但不能解释 `Project ECHO`、低温休眠、实验设施核心机制。

### 6.2 Lira

草药屋、回声病、头痛、梦境、身体症状和样本观察相关。她可以从医学和经验角度提醒玩家，但不能早期确认人体实验或主人公真实身份。

### 6.3 Orin

灯塔看守人，知道灯塔异常、旧钥匙、下层入口和部分危险线索。他倾向于隐瞒核心真相，低信任时尤其防备。

### 6.4 Nia

儿童视角。她知道井边传闻、蓝色小石头、孩子之间的秘密和村中传话，但不能准确解释组织、实验、设施和编号。

### 6.5 Venn

失忆研究者，记忆破碎，接近部分真相。他可以提出人为封闭场猜想，也可能联想到 `Project ECHO`，但在终局前不应直接确认玩家就是 `Subject 07`。

### 6.6 Elder_Mara

长老，掌握潮民传统、蓝脉禁忌、祭歌和大震夜的传统记忆。她可以讲禁忌与传承，但不会用现代技术术语完整解释地下实验。

### 6.7 Lab_Terminal

实验终端。它是最接近事实记录的“NPC”，可以输出实验日志、权限字段和损坏记录。终局真相主要通过它确认。

## 7. 信任等级

`relationship.trust` 不控制事实是否解锁，而控制 NPC 是否愿意说、说多直接。

```text
relationship.trust 0：陌生人，防备、含糊、短句，可能反问。
relationship.trust 1：勉强回应，只给低风险经验或提醒。
relationship.trust 2：初步信任，可以解释自己领域内的观察。
relationship.trust 3：愿意合作调查，可以串联玩家已掌握的线索。
relationship.trust 4：深度信任，可以讨论高风险信息和个人立场。
relationship.trust 5：亲密同盟或终局托付对象，可以表达完整态度。
```

注意：即使 `relationship.trust` 很高，也不能突破 `story.unlock_level`。信任等级解决“愿不愿意说”，剧情等级解决“能不能说”。

## 8. 推荐写作规范

### 8.1 每个可入库章节只讲一类知识

好的章节：

```markdown
## 解锁2：Lira 对蓝色小石头的症状观察

Lira 会把蓝色小石头视为可能诱发头痛、低温触感和重复梦的危险样本。
她不会称它为实验材料，也不会确认它与地下设施有关。
```

不推荐：

```markdown
## 蓝色小石头

蓝色小石头会让人头痛。它其实来自 Project ECHO 的主共振核心，
也是主人公父母实验事故的重要证据。
```

后一种写法把早期观察和终局真相混在一起，容易导致低等级检索时泄露。

### 8.2 早期传闻和后期真相分开写

推荐：

```markdown
## 解锁1：铁牌上的陌生编号

村民只知道铁牌上的编号不像岛上的东西，读久了会让人不舒服。

## 解锁5：Subject 07 与主人公身份

实验记录确认 Subject 07 指向主人公的保护仓编号。
```

### 8.3 为 NPC 留出“不知道”的空间

不要把所有解释都写成全知旁白。NPC RAG 更需要分层信息：

- 普通村民看到什么。
- 医者如何解释症状。
- 长老如何解释禁忌。
- 灯塔看守人隐瞒什么。
- 终端记录确认什么。

### 8.4 不要把任务攻略写进世界知识

类似“玩家下一步应该去哪里、拿什么、按什么顺序开门”的内容，更适合放在 `non_vectorized/C_quest_guide_任务线.md` 或 Unity 任务系统中。NPC 可以提示，但不应该像攻略机器人一样输出任务清单。

## 9. 文件维护建议

### 9.1 修改已有世界设定

修改 `01_` 到 `24_` 文件后，需要重新入库：

```powershell
python scripts\ingest_game_docs_to_milvus.py
```

然后运行检索测试：

```powershell
python scripts\test_retrieve.py --suite quality
```

### 9.2 新增可入库文档

新增文件时需要同步检查：

1. `manifest_v03.json` 是否加入 `ingest_recommended`。
2. `npc_knowledge_matrix_v03.json` 是否给出合理 NPC 权限。
3. frontmatter 是否包含合理的 `default_unlock_level`、`rag_ingest` 和 `recommended_topics`。
4. 章节标题是否带 `解锁N`。
5. `python scripts\validate_game_docs_for_rag.py` 是否还有需要处理的 warning。

### 9.3 新增 NPC

新增 NPC 时至少需要更新：

```text
non_vectorized/B_npc_profiles_角色档案.md
non_vectorized/E_npc_prompt_rules_NPC回答规则.md
npc_knowledge_matrix_v03.json
npc_app/services/npc_prompt_service.py
scripts/ingest_game_docs_to_milvus.py
scripts/validate_game_docs_for_rag.py
scripts/test_retrieve.py
```

还要重新入库，因为 Milvus 中的记录会按 `npc_id` 展开。

### 9.4 调整终局真相

如果修改了主人公身份、父母、保护仓、低温休眠、主共振核心、`Subject 07` 或 `Project ECHO` 的设定，要额外检查：

- `04_shadow_organization_反政府科技组织.md`
- `05_player_background_主人公背景.md`
- `08_items_道具与线索.md`
- `10_hidden_truth_隐藏真相.md`
- `vectorized/19_dialogue_examples_NPC对话样例.md`
- `23_rumor_truth_layers_传闻真相分层.md`

这些文件之间必须保持一致，否则 NPC 容易出现“半真半假”的回答。

## 10. 质量检查命令

### 10.1 入库

```powershell
python scripts\ingest_game_docs_to_milvus.py
```

### 10.2 文档配置检查

```powershell
python scripts\validate_game_docs_for_rag.py
```

### 10.3 检索测试

```powershell
python scripts\test_retrieve.py --suite quality
python scripts\test_retrieve.py --suite coverage
python scripts\test_retrieve.py --suite all
```

### 10.4 临时检索

```powershell
python scripts\test_retrieve.py --question "Project ECHO 是什么？" --npc-id Nia --unlock-level 1
```

这个测试应该不会把组织真相、主人公身份或终局记录检索给 Nia。

### 10.5 NPC 对话测试

NPC 端到端请求、NDJSON 解析和场景回归测试在 Unity 项目中维护。本仓库通过 `npc_app/tests/` 验证 Runtime 规则，并在发布前通过 `scripts/run_real_npc_acceptance.py` 执行真实 HTTP 验收。

## 11. 与 Unity 的关系

Unity 客户端不需要直接理解全部 Markdown。推荐把 Unity 与后端的边界设计为：

- Unity 负责玩家位置、任务状态、物品栏、已访问地点、已知线索、NPC 信任等级。
- 后端负责根据这些状态生成 NPC 回答。
- Unity 每次对话请求时，把当前状态传给 `/v1/npc/chat/stream`。
- 后端返回 `thread`、`sources`、`answer_delta`、`done` 等事件。
- Unity 显示 `answer_delta`，并保存返回的 `thread_id` 以延续对话。

最重要的两个字段是：

```text
story.unlock_level
relationship.trust
```

它们应该由 Unity 的任务系统、探索进度和玩家行为共同维护。

## 12. 设计原则总结

- 世界真相要分层，不要一次写满。
- 每个 NPC 都要有“不知道”和“不愿说”的范围。
- 早期文档写传闻、观察、症状和误解。
- 中期文档写线索之间的关系。
- 后期文档写设施、组织和实验机制。
- 终局文档写主人公身份、父母和最终选择。
- 可入库文档要服务“NPC 会如何说”，而不仅是服务“设定是什么”。
- 改文档后必须重新入库，入库后必须测试检索边界。
