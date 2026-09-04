# Web Novel Craft 路由

所有创作、续写、改写和评价先读 `narrative-effect-contract.md`，再按下表进入负责该创作决定的技能。合同确认读者承诺、人物发动机、压力/选项、选择/责任、后果/体验和文本实现；路由不会把它变成额外的大清单。

## 按创作决定路由

| 用户需要 | 主技能 | 按需读取 | 边界 |
| --- | --- | --- | --- |
| 选择题材、读者、前提或故事发动机 | `$web-novel-development` | `development.md` | 决定写什么以及为何可持续 |
| 设计主线、卷章、承诺回收、节奏或章末钩子 | `$web-novel-structure` | `structure.md` | 负责事件和期待结构 |
| 设计人物、关系、弧光、POV 或声音 | `$web-novel-characters` | `characters.md` | 负责选择、知识和人物体验 |
| 同时设计人物两难、优势阴影、延迟代价和日常余波 | `$web-novel-craft` | `choice-cost-experience.md`，并按需加载 `characters.md`、`structure.md`、`scene-prose-craft.md` | 跨人物、结构与场景；不把每个节点机械写成创伤或灾难 |
| 处理类型专项机制 | `$web-novel-genre-craft` | `genre-craft.md` 及对应类型页 | 类型经验是条件化工具，不是平台公式 |
| 设计升级、能力、资源、身份或谜题进展 | `$web-novel-progression` | `progression.md` | 负责规则、成本、反制和选项变化 |
| 写或修场景、动作、感官、情绪、对白和句群 | `$web-novel-prose-craft` | `scene-prose-craft.md` 及对应文体页 | 场景目的不清时先回结构/人物 |
| 阅读原稿、批评、提建议、修改或评价 | `$web-novel-revision` | `revision.md`、`writing-evaluation.md` | 先完整阅读，意见必须有文本证据 |
| 去除 AI 生成痕迹、使文本更自然 | `$humanizer-zh` | 上游技能正文；编排扫描传入 `orchestrated_fiction_edit` | 显式用户模式或受限编排模式只处理表达，不替代网文结构、人物、场景或言语行为判断 |
| 蒸馏视频写作知识 | `$web-novel-evidence-research` | `evidence-policy.md` | 标题与声誉不是证据，必须完整审阅 |

跨越两个以上创作决定时使用 `$web-novel-craft`。单一任务直接进入对应技能，不要求用户先完成其他阶段。

## 创作循环

```text
作者意图与目标读者
  -> 前提与故事发动机
  -> 人物、世界、规则与结构
  -> 章节和场景
  -> 阅读、诊断与意见
  -> 修改与比较评价
  -> 继续写作
```

这是一条创作循环，不是生产流程。写作可以发现新方向；只需检查它是否破坏已有正文、人物选择、世界规则或承诺回收。

## 最小输入

```yaml
language: zh-CN
task: 立项|构思|大纲|写作|续写|诊断|修改|评价
target_readers: 具体读者与期待体验
genre_and_tone: 题材、语气、内容边界
premise_or_text: 点子、已有大纲或正文
author_nonnegotiables: 不可擅改的选择
desired_output: 本次真正需要的交付
```

叙事效果合同由技能从作者意图与正文内部编译，不要求作者或调用方预填一套六段表格。

信息不足但可安全假设时，说明假设并继续。只有答案会实质改变题材、结局、核心关系、内容边界或修改方向时，才询问一个关键问题。

## 长篇续写

续写需要记忆时读 `story-bible.md`。已有正文优先于摘要，用户确认优先于模型推断。发现矛盾时指出冲突并给修复方案；不要求项目 ID、版本、提交、哈希、排期、存稿或发布信息。
