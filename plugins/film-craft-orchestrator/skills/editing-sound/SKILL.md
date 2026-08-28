---
name: editing-sound
description: 设计或审查已生成 clips 的叙事剪辑、节奏、反应镜头、蒙太奇、对白编辑、ADR/独立 lip-sync、画外音、ambience、Foley、SFX、音乐、混音和声音母版技术交付时使用。负责时间线、声音层次与音画同步的技术验收；最终项目的语义与视觉放行由 continuity-qc 负责，不借剪辑改写未经批准的来源事实或人物选择。
---

# Editing and Sound

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `../film-craft-orchestrator/references/editing-sound.md`；表演剪辑、反应选择和 coverage 缺口加读 `../film-craft-orchestrator/references/distilled-directing-editing-procedures.json`、`../film-craft-orchestrator/references/distilled-structure-sound-procedures.json` 与 `../film-craft-orchestrator/references/distilled-targeted-foundation-procedures.json`。

## 剪辑设计

1. 先写每个剪点改变了什么：信息、视点、权力、情绪、时间、空间或节奏。
2. 以 clip 的 `edit_contribution_sec`、handles 和 entry/exit 为硬边界，保持 scene runtime 与贡献时长相等。
3. 动作匹配要求动作相位、方向、速度和视线可接；不只看构图相似。
4. 反应镜头只在它改变观众理解、人物关系或节奏时保留。
5. 蒙太奇需要明确组织原则、递进和结束条件；不是用音乐覆盖一组无因果素材。
6. delivery requirement 必须在最终 timeline 中有实际信息载体，不能只存在于 story map。

## 声音层次

按功能分开设计：production/native dialogue、ADR/voiceover、ambience、room tone、Foley、hard SFX、designed sound、music 和 silence。每条 cue 记录起止、来源、叙事作用、同步点、优先级、ducking 和 fallback。

- 对白先保可懂度与表演，再处理纯净度。
- 口型不稳时优先拆镜、离画、反打、遮挡、独立音频或后期 lip-sync，不无限重生成。
- 声音桥要写清先听后见或先离画后切换的时间关系。
- 音乐不能替代场景内部缺失的转折；它只能组织、对照或放大已有结构。
- 静默是一种主动设计，需说明它拿走了什么声音以及观众因此注意什么。

## 输出与验收

编译器生成派生文件的骨架后，只能在原位增量填写/修订 `edit_plan.yaml` 与 `sound_cue_sheet.csv` 的编辑与声音字段，不得另建第二套真源；每次变更记录 owner、status、source artifact version，并在上游重新编译时按版本失效重建。逐 clip 使用稳定 ID，给出 source in/out、timeline in/out、trim/handles、transition、信息载体和音画同步点。最终检查：节奏、空间可读性、动作/视线连接、对白清晰度、响度/峰值、音乐遮蔽、声音连续性和交付格式。

如果 coverage 不足，明确指出缺失的叙事功能与最小补镜，不用泛泛的“多拍几个角度”。
