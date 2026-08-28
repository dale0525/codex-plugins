---
name: screenwriting
description: 仅当目标明确是电影、剧集、叙事短片、叙事广告或可拍 screenplay 时，创作或重写概念、人物弧、结构、场景、对白和标准剧本；也用于诊断不可拍说明、松散场景、虚假转折和台词问题。小说正文、网文场景、一般创意写作、营销文案、聊天对白和非视听戏剧文本不触发，除非用户要求改编为银幕剧本。只负责故事与剧本层；有源材料先接收已审查的改编矩阵，不擅自改写来源事实或导演/摄影方案。
---

# Screenwriting

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `../film-craft-orchestrator/references/writer.md`；按问题加载 `../film-craft-orchestrator/references/distilled-scene-procedures.json`、`../film-craft-orchestrator/references/distilled-structure-sound-procedures.json`、`../film-craft-orchestrator/references/distilled-theme-performance-camera-procedures.json`、`../film-craft-orchestrator/references/distilled-sorkin-procedures.json` 和 `../film-craft-orchestrator/references/distilled-targeted-foundation-procedures.json`。

## 从故事到剧本

1. 冻结一句可验收的 dramatic premise：谁要什么、为什么现在、什么力量阻挡、失败代价是什么。
2. 写主题问题，不写主题答案；让不同人物用行动给出互相冲突的答案。
3. 为主要人物定义外在目标、内在缺口、策略、谎言/信念、压力测试、关键选择和选择代价。
4. 用 sequence/beat map 跟踪因果：每个 beat 必须改变信息、权力、关系、计划或风险。
5. 写 scene card，再写场景。每场只有一个地点和连续时间段，至少包含进入状态、目标、障碍、策略、转折、退出状态和下一场压力。
6. 写对白时先标意图与障碍，再让人物通过策略说话。删除双方都已知道的解释和作者替观众总结的句子。
7. 最后做可拍性、因果、人物选择、节奏和 delivery requirement 对账。

## 可拍剧本规范

动作行只写观众能看到或听到的内容。关系、历史和心理若无法直接拍出，就转换成：

- 一个带状态的物件或环境细节；
- 一个具体动作、迟疑、回避、抢夺或空间距离变化；
- 一句由当前意图推动的对白；
- 电话、广播、屏幕、后期文字或画外音；
- 前后镜头的可理解对照。

例如“夏南星看见顾承泽——她结婚三年的丈夫”不合格；可写成她看见顾承泽，拇指本能地摩挲无名指戒痕，而他胸牌上的姓名进入视线。三年关系若是必要精确信息，还需要额外的可控载体。

## 场景与对白测试

- 没有转折的场景：合并、删掉，或改变人物策略与退出状态。
- 只传信息的场景：给信息增加目的、阻力、代价或误解。
- 反派只会阻挡：为其建立自洽目标、价值和可理解策略。
- 台词可互换角色：恢复人物特有的欲望、知识边界、节奏和回避方式。
- 说出的内容与动作重复：保留更有张力的通道，或让两者形成矛盾。
- 镜头设计提前替人物决定：退回剧本，只写选择发生前可成立的行为与信息。

## 交付

根据请求交付 logline、人物弧、beat map、scene cards、分集大纲或标准剧本。若进入 AI 视频流水线，输出 `story_and_scene_map.yaml`，保持 scene/beat/delivery requirement ID 稳定，并在共享根运行：

```bash
python ../film-craft-orchestrator/scripts/validate_story_stage.py <package-directory>
```
