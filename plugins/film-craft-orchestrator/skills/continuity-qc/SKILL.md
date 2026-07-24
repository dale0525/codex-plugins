---
name: continuity-qc
description: 审查或修复 AI 视频和叙事镜头的角色身份、年龄服装、道具、地点、屏幕方向、光色、动作、时间状态、口型、可剪性与终片质量时使用。也用于生成失败分类、证据帧对比和放行判断；不通过重抽掩盖上游结构错误，也不把无输出的计划记录标成成功。
---

# Continuity and Quality Control

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `references/ai-video-continuity.md`；生成失败加读 `references/ai-video-failure-repair.md`，最终交付加读 `references/ai-video-deliverables.md`。

## 连续性状态机

对每个 clip 记录可核对的 entry、exit 和 `expected_next.must_preserve`：

- 角色身份、state version、年龄、服装、伤势、位置、视线与动作相位；
- 道具形状、颜色、持有人、完整/损坏/开合等状态；
- 地点、布局、屏幕方向、时间、天气、光向、色温与环境活动；
- 叙事上离画但仍参与的信息链；
- 显式 transition：跨地点、年代、死亡/重生、梦境或蒙太奇。

`planned`、`sequence-time`、“按前镜”或“状态变化”不是状态。必须写具体、可在证据帧中判定的值。角色、道具和地点 ID 必须存在于 visual bible；同一 ID 不能同时承载冲突状态。

## 失败诊断

先分类，再改动：

1. 叙事/语义失败：人物选择、信息或结果错误；回退到 story/director。
2. 身份/状态漂移：修正状态版本、参考角色和 prompt invariants。
3. 时空/物理失败：拆动作、减少同时变量、调整 entry/exit。
4. 参考传输失败：核对 expected/attached、role、hash 和实际输入。
5. 模型能力失败：验证 adapter 能力，换模式或模型。
6. 可剪性失败：补 handles、反应、连接镜头或重新设计 coverage。
7. 表面质量失败：最后处理伪影、细节和风格偏差。

一次修复只提出一个主要可证伪原因和一个主要变量。两轮同类阻塞失败后停止无界重抽。

## QC 证据

真实生成至少检查入口、中点和出口帧；涉及动作、口型或变形时检查关键中间帧。每项结论链接 output hash、时间码/帧号和判定标准。计划中的 clip 可以是 `pending`，但不能伪造 actual、hash 或成功结论。

Clip QC 先判叙事目的与状态，再判连续性、可剪性和表面质量。Final film QC 另查节奏、信息可读性、声音同步、镜头连接、全片状态和交付规格。

## 验证

在共享根按需要运行：

```bash
python scripts/validate_continuity_state.py <continuity-state.json>
python scripts/continuity_lint.py <continuity-state.json>
python scripts/validate_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
```

确定性验证只证明结构一致；最终放行仍需要对真实图像、声音和剪辑的语义审查。
