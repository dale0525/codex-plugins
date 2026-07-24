# Adaptation：从源文本到影视功能

> 来源边界：本文件的改编矩阵、权利字段和交付接口是本 skill 的综合工作框架，不是某条视频原话。小说改编程序见 `distilled-video-procedures.json` 和 `distilled-targeted-foundation-procedures.json`，真实事件压缩程序见 `distilled-sorkin-procedures.json`；只有带 `claim_refs` 的主张才可归因给相应讲者。来源目录名称不代表已完成蒸馏。

## 目录

- [改编前置](#改编前置)
- [源作盘点](#源作盘点)
- [结构映射与取舍](#结构映射与取舍)
- [视角、人物与世界](#视角人物与世界)
- [改编矩阵](#改编矩阵)
- [核验与交付](#核验与交付)

## 改编前置

先记录：

```yaml
source_title: ""
source_creator: ""
source_version: "chapter / edition / URL / user file"
rights_status: user-owned|licensed|public-domain|unknown|not-applicable
adaptation_scope: "第一章 / 全书 / 单一事件 / 人物灵感"
target_format: short|feature|limited-series|series|web-episode
target_runtime: ""
non_negotiables: []
permission_to_invent: true|false|unknown
```

`unknown` 不等于 public domain。没有权利确认时可做研究、结构分析、课堂练习和转化性提案，但不要声称可以发行或交付完整改写稿；必要时建议咨询专业律师/版权机构。本 skill 不提供法律意见。

## 源作盘点

不要从“哪些情节好看”开始，先找功能：

- 核心承诺：源作让读者期待什么体验？
- 主题问题：哪些选择/代价反复出现？
- 叙事视角：谁能知道、谁被隐瞒、谁承担后果？
- 角色功能：欲望、阻力、关系和转变如何连接因果？
- 世界规则：哪些设定是冲突引擎，哪些只是纹理？
- 不可直接影视化的元素：内心独白、叙述者评论、时间跳跃、篇幅和版权敏感材料。

将源文本逐章/逐场标成 `must_preserve`, `translate`, `compress`, `merge`, `omit`, `invent`，每项给理由和风险。

一行只能有一个主要源功能与一个可核对的下游状态变化。若同一段同时包含“公平处理别人”和“主动照顾身体”等两个独立选择，拆成两个 source/screen units；不能用一个合并行让任一功能在 clip 中消失。`invent` 只标原作没有的连接、道具或视听装置；同一行主要动作来自原作时仍按 `translate/compress/merge` 标记，并把新增部分放入 `invent_ids`。

为每个必须被观众接收到的最小事实或状态变化建立 `delivery_requirement_id`。例如“重生”至少可拆为无疤身体证据、日期/时间和校园年龄证据；“健康选择”若源作明确列出白粥、热豆浆、茶叶蛋并实际吃下，就不能用一个泛化的“早餐”ID覆盖。ID 的粒度以“删掉这一项是否改变因果、人物选择或验收结论”为准。

每项 requirement 还冻结：叙事参与人物、实际可见人物、关键道具和合法地点。电话来电者可以只在 `required_character_ids` 中而不进入 `visible_character_ids`；“孩子跑来叫爸爸”则必须把孩子、父亲和现场关系人物列为可见。下游 prompt 的 narrative IDs、clip subjects/props 与 environment 必须满足这些约束。

一个 requirement 默认最多跨三个 beats；超过时重新判断是否把不同事实、选择、结果或钩子错误合并。章节结尾的新来电、未决选择或下一关系入口必须独立追踪，不能挂在前一个早餐/动作 requirement 下，也不能把“未接”替换成“接听”。

`source_unit` 的页/段/章节定位必须由实际源文件编号产生，不能凭记忆顺排。每行另填一条 4–60 字、可在授权源文本逐字搜索命中的唯一 `source_anchor`；冻结前重新打开 locator，确认 anchor、功能和所述事件确实同处该范围。locator 或 anchor 任一不实，追溯门失败。

不要停在标签。按以下顺序调用已验证程序：

1. `adapt-source-lift-and-return`：先做带定位的事件/关键对白提取，再离开原作起草，最后回源补漏。
2. `adapt-viewpoint-cut-filter`：源材料超过目标时长时，用 POV、因果、事实和作者立场四项筛选。
3. `adapt-compression-stress-test`：复制极限压缩稿，用实际断裂证据决定回补，禁止凭喜爱恢复。
4. 真实事件或特定社群再运行 `adapt-lived-world-research` 与 `adapt-creative-pass-accuracy-pass`。
5. 真实事件跨越长时间或多地点时，运行 `true-story-compressed-container`，逐项标明 `exact`、时间/地点压缩、合成人物和虚构连接；涉及责任主体或法律含义的变化必须回退或交专业审阅。

### 从段落到屏幕单元的选择算法

对每个 `source_unit` 依次执行，不跳步：

1. 写稳定定位：页码/段落/章节和 20 字以内摘要。
2. 写源作功能：`causal`, `character`, `information`, `theme`, `rhythm`, `world_rule` 可多选。
3. 写删掉后的具体断裂；若没有断裂，先标 `omit_candidate`。
4. 写最低成本的屏幕载体：行动、声音、空间、道具、对白或时间结构，只选主要载体。
5. 分配 `screen_unit`、预计秒数和进入/退出状态；没有状态变化的单元不得直接升为场景。
6. 总时长超标时，优先合并功能相近且不改变责任/视角的单元，再做极限压缩测试。
7. 对每个过程型事实写 `entry → action/process → exit`；禁止把“进入正常审核”压成“申请被拒”、把“来电悬停”压成“已经接听”等结果替换。
8. 对每个关键选择建立 `pressure/temptation → choice → consequence` 三联。即时压力若被删，必须用另一可见/可听载体恢复；只保留选择结果会让人物主动性失去代价，不能算功能覆盖。

时长估算只作计划基线：一般对白页约一分钟，但动作密度、沉默、调度和类型会显著改变结果。每场在 table read 或 animatic 后用实测秒数替换估算，不把页数经验当硬规则。

## 结构映射与取舍

影视时长、预算和观众注意力会改变结构。不要把每段文字机械变成一场戏；为每个改编单元回答：

1. 它在源作中的功能是什么（信息、关系、主题、节奏、世界规则）？
2. 影视中用什么可见行动、声音或空间完成同一功能？
3. 若合并角色/事件，新的因果链是否仍由人物选择驱动？
4. 若改变顺序，观众何时知道什么，悬念/惊奇/共情如何变化？
5. 这个改动是为了媒介、时长、受众、制作限制还是作者立场？

保真不是逐字复现，而是对“功能、情感合同、主题张力”的有意识取舍。若改编带来新的政治/文化含义，明确写入 `adaptation_rationale`，不要假装它来自原作。

## 视角、人物与世界

### 视角转译

小说第一人称可以转成主观摄影、画外音、选择性信息或反应镜头；也可以拆给多个角色。每次转译都记录代价：观众知道得更多/更少、叙述可靠性改变、谁获得主体性。

### 角色合并

合并前列出双方的功能和关键选择，确认新角色仍能承担全部因果。若只是为了减少演员而合并，检查声音、年龄、权力和关系含义是否被改变。

### 世界观

只保留会改变行动的设定。把说明性背景改成规则在压力下造成的具体后果；给不熟悉世界的观众一个可观察的入口，不用百科式对白填充。

## 改编矩阵

```yaml
source_unit: "第 1 章 / 关键段落"
source_function: "让读者意识到主人公的自我叙事不可靠"
screen_unit: "S01-S03"
delivery_requirement_ids: [DR-U03-01]
preservation: translate
new_visual_or_sound_device: "同一事件被三个角色以不同顺序叙述；画面保留互相矛盾的物件位置"
character_changes:
  - "把旁观叙述者的评论转成妹妹的质问"
structural_change: "先展示后果，再回到原因"
reason: "短片需要在前两分钟建立悬念"
risks:
  - "观众可能把不可靠性误读为剪辑错误"
verification: "用三名未读原作的观众测试信息理解"
rights_status: unknown
```

矩阵至少覆盖：源作单元、影视单元、功能、`invent_ids`、保留/转译/删改、原因、风险、待核验事实和权利状态。下游 writer 消费 `screen_unit` 与 `character_changes`；director 消费 `new_visual_or_sound_device`；cinematographer 消费可观察设备和空间限制。

### Adaptation → Writer 冻结接口

每个进入写作阶段的 `screen_unit` 必须交出：

```yaml
screen_unit: U03
source_refs: [chapter-01.paragraph-12]
preservation: translate
source_functions: [character, causal]
scene_id: S02
estimated_duration_sec: 75
entry_state: "Mira 相信父亲没有读信"
objective: "在早餐结束前拿回信"
obstacle: "信已在父亲盘子下面；她不能暴露离家计划"
turn: "父亲端走盘子，却把信原封不动留在桌上"
exit_state: "Mira 确认父亲读过信，并取消出租车"
visible_or_audible_devices: ["盘子压信", "删除订单提示音"]
invented_material: ["车票道具"]
open_facts: []
```

冻结前验收：

- 100% `must_preserve` 有 `screen_unit`；100% `invent` 明示。每个发明项使用稳定 `invent_id`，并分别报告改编矩阵、handoff、剧本和导演/摄影新增项的分母；不能用含义不明的“5/5 invent”覆盖多个层级。
- `adaptation_matrix.csv` 通过 `validate_adaptation_matrix.py`；任何含逗号/引号/换行的字段均被正确 CSV 引用，不允许解析后多列、少列或 must beat 无来源行。
- 每个独立因果、人物选择、主题兑现与结尾未决状态各有 source unit、must beat 和至少一个能真正承载它的 clip；仅在 scene map 或 visual bible 提到、不进入 prompt/edit 的内容视为未覆盖。
- adaptation matrix、story beat、prompt `information_carriers` 与 edit timeline 的 `delivery_requirement_id` 集合完全对账；精确文字、数字或多项枚举分别说明 post text、独立声音、合成道具或可见动作，不把 `no readable UI text` 与“观众必须读懂界面”同时留在包中。
- 每个 `source_anchor` 能在声明的源版本逐字命中，并与 locator、功能和 screen unit 一致；抽查发现一条伪 locator 即撤销整个 traceability pass。
- 每个场景的 `entry_state != exit_state`，并有可拍的目标、阻碍和转折。
- 预计总时长在目标 ±10%；超过时不进入对白稿。
- 下游镜头表返回后，用 `screen_duration_contribution_est` 重新对账每场 handoff 时长；若任一场或全片明细与冻结时长不一致，撤销时长 `pass` 并回退 writer/director，不得用原 handoff 总数覆盖新明细。
- 三名未读原作的读者中至少两人能正确复述核心因果、主角目标与结尾选择。
- 每个删并项有理由；涉及事实、责任主体或当事人时有独立风险编号。

## 核验与交付

- 对照授权剧本、制片方公开访谈或用户提供的版本，不把粉丝转录当定稿。
- 对“作者意图”保留来源和时间码；宣传采访可能有选择性回忆，写成一条证据而不是终审。
- 若改编真实人物/事件，事实、戏剧化假设和合成角色分栏；敏感指控需要可靠来源和专业审阅。
- 交付前给出“保留的情感合同”“有意改变的立场”“观众可能误解的点”“需要权利/事实确认的点”。

已深度蒸馏的改编来源包括 BAFTA 的 Rebecca Lenkiewicz 访谈、Outstanding Screenplays 的 Aaron Sorkin 访谈剪辑，以及 ScreenCraft 的 Gillian Flynn 访谈。具体时间码和程序见 `distilled-video-procedures.json`、`distilled-sorkin-procedures.json` 与 `distilled-targeted-foundation-procedures.json`。Flynn 的 Gone Girl、Sharp Objects 和 Dark Places 内容分别是项目经验或小说修订案例，不能推广成页数、片长或合作定律；所有来源都不能替代事实核查、权利确认或法律审阅。
