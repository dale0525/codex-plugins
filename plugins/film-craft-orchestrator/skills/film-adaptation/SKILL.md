---
name: film-adaptation
description: 将小说、短篇、真实事件、旧剧本或其他有源材料改编为电影、剧集、短片或 AI 视频时使用。负责完整阅读来源、建立可追溯改编矩阵、保留因果与人物选择、设计视听转译和剧集边界；不把现有旧改编当答案，也不直接替代后续剧本、导演或生成阶段。
---

# Film Adaptation

## 目标与共享资源

把源材料的功能、因果、人物压力与主题冲突转换为可拍叙事，而非逐段搬运或凭印象概括。共享根是相邻的 `../film-craft-orchestrator/`；先读其 `references/adaptation.md`，复杂任务再读 `references/distilled-video-procedures.json`、`references/distilled-sorkin-procedures.json` 和 `references/distilled-targeted-foundation-procedures.json`。

## 工作流

1. 完整读取授权来源，并对实际文件建立稳定的章节、段落或时间定位；不要假定第一章等于第一集。
2. 提取 source units：事件、人物选择、诱因、结果、关系信息、视角、主题压力、时间状态和精确文字/数值。
3. 为每项选择 `must_preserve | translate | combine | omit | invent`，同时记录理由、风险和目标载体。
4. 给每个必须进入成片的独立信息分配 `delivery_requirement_id`；两个选择、两个结果或新钩子不能共用一个 ID。
5. 设计 episode/sequence/beat 边界。每个边界由戏剧问题、人物策略变化和可见结果决定，不由章节长度机械决定。
6. 输出 `adaptation_matrix.csv` 与改编说明；只有在独立审查通过后才交给编剧阶段。

## 改编矩阵最低字段

每行至少包含：稳定 source locator、可逐字命中的 `source_anchor`、source function、人物诱因/选择/结果、目标 screen claim、处理方式、delivery requirement、时间关系、责任人物、信息载体和批准变更理由。

以下改写默认不合格：

- 把“提交审核”改成“审核被拒”；
- 把“未接来电”改成“接听”；
- 只保留人物决定，删除使决定艰难的压力；
- 用新增视觉母题把来源已有动作标成 `invent`；
- 用画外解释替代本可由行动、阻挡或反应表达的信息；
- 从旧项目已有改编复制情节，而未回到完整小说核对。

## 可拍性

来源内心与背景不能原样写成不可见说明。把它们转成可验收载体：行为、空间关系、道具状态、表演节拍、对白、画外音、声音桥、后期文字或剪辑对照。精确日期、姓名、金额和枚举优先使用 `post_text`、`separate_audio` 或 `prop_composite`，不要依赖生成模型写字。

## 审查与验证

独立审查者只能看来源和当前 adaptation artifacts。逐行确认 source anchor、功能、选择极性、结果与改编 claim；标记 `preserved | approved_change | contradicted | unsupported`。

在共享根运行：

```bash
python scripts/validate_adaptation_stage.py <package-directory>
```

确定性校验通过不代表语义正确；两者都通过才可冻结。
