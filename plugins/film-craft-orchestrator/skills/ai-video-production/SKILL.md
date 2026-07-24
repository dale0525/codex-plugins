---
name: ai-video-production
description: 将已冻结的故事、导演意图和视觉圣经转换为 AI 视频 clip 计划、provider-neutral prompt IR、模型适配提示、参考资产传输、生成日志和可剪素材时使用。也用于模型路由、首帧/尾帧、口型策略与生成迭代；不把整段剧本直接粘给模型，也不擅自修改上游人物选择。
---

# AI Video Production

## 共享资源

共享根是 `../film-craft-orchestrator/`。依次读取：

- `references/ai-video-workflow.md`
- `references/ai-video-model-routing.md`
- `references/ai-video-deliverables.md`
- `references/distilled-ai-video-procedures.json`

需要连续性或失败修复时再读取对应分技能及 `references/ai-video-continuity.md`、`references/ai-video-failure-repair.md`。模型事实必须同时核对 `references/model-adapters.json` 与 `references/ai-video-official-evidence.json`。

## Scene-to-clip

按 beat 和可验收状态变化拆 clip，不按句号拆。每个 clip 默认只有一个叙事目的、一个主要动作、一次注意力变化和一个主要相机行为。多人、复杂手部、道具交接、口型、相机运动和环境变化同时出现三类以上时，默认拆分。

把动作写成短而有顺序的 `action_steps`。八秒及以下默认最多三个步骤。每个 clip 明确 entry/exit、handles、剪入/剪出、fallback、可见人物/道具、叙事人物/道具和 delivery requirements。

## Prompt IR

先写 provider-neutral IR，再渲染 adapter prompt：

```text
subject/state → start/action/end → environment/space
→ composition/viewpoint → one camera behavior
→ lighting/time order → continuity invariants
→ negative constraints → output contract
```

`rendered_prompt` 必须是可发送的完整提示，不能写 `pending`。Adapter 只能翻译能力与参数，不能改写 beat、人物选择、空间规则或导演意图。

## 模型与参考传输

- `required` 能力不满足即排除，`preferred` 只排序，unknown 按不支持。
- 时长、分辨率、参考数量、API 字段和弃用状态只认当前官方资料。
- 官方演示不证明成功率；先设计低成本 probe。
- 分别记录 `reference_inputs_expected` 与 `reference_inputs_attached`、role、hash 和 transport。
- 路径、Markdown 或提示中的“same character”都不算已附加参考。
- 缺必需参考时标 `diagnostic_preview`，不得提升为 production。

## 生成迭代

每次运行向 `generation_log.jsonl` 追加模型/version、adapter、prompt hash、reference hashes、参数、唯一主要变量、输出/hash、成本和状态。未实际生成时 `actual`、`output_uri` 与 `output_hash` 必须为空。

验收顺序：叙事目的 → 身份/状态 → 时间/空间 → 可剪性 → 表面质量。非探索轮次一次只改一个主要变量。连续两轮相同阻塞失败时拆 clip、换模式/adapter或回退上游。

## 编译与验证

在共享根运行：

```bash
python scripts/compile_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
python scripts/validate_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
```

编译器默认拒绝覆盖已有派生文件。只有明确废弃旧骨架并保留真实生成/QC 记录时才使用 `--replace-derived`。
