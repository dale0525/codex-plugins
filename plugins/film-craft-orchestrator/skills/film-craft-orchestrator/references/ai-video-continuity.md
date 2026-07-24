# AI Video Continuity：跨 clip 状态与视觉身份

## 目录

- [连续性的五层](#连续性的五层)
- [状态真源](#状态真源)
- [生成前检查](#生成前检查)
- [生成后回写](#生成后回写)
- [冲突处理](#冲突处理)
- [验收](#验收)

## 连续性的五层

不要把连续性缩成“脸一样”。逐层检查：

1. **身份**：脸、体型、轮廓、发型、年龄状态和声音身份。
2. **状态**：服装、污损、伤势、表情基线、手中物和道具状态。
3. **空间**：人物/道具位置、门窗、轴线、screen direction、视线和尺度。
4. **时间/环境**：日夜、天气、光向、阴影、实景光、环境声和背景活动。
5. **叙事**：谁知道什么、目标/策略、关系权力、动作是否从上一镜结果开始。

叙事连续性高于表面相似：角色在上一镜已经发现线索，下一镜不能在无说明时再次表现为未知。

## 状态真源

使用 append-only `continuity_state.json`；新版本可以 supersede 旧状态，但不能静默覆盖：

```yaml
clip_id: C-S01-04
visual_bible_version: 1.1.0
prior_clip_id: C-S01-03
entry:
  characters:
    CHAR-MIRA:
      wardrobe: WARDROBE-MIRA-01
      position: table_left
      screen_facing: right
      knowledge: [letter_moved]
  props:
    PROP-LETTER:
      holder: none
      location: under_plate_partial
      condition: sealed
  environment:
    time: predawn
    key_direction: window_left
exit:
  characters:
    CHAR-MIRA:
      position: table_left
      screen_facing: right
      knowledge: [letter_moved, father_read_letter]
  props:
    PROP-LETTER:
      holder: none
      location: table_exposed
      condition: opened_by_steam
expected_next:
  must_preserve: [CHAR-MIRA.wardrobe, PROP-LETTER.location, environment.key_direction]
conflicts: []
status: approved
```

字段值引用 visual bible 中的稳定 ID；不要在每个 prompt 中重新发明近义描述。

## 生成前检查

对每个 clip：

1. `entry` 必须与上一 approved clip 的 `exit` 合并；无上一镜时引用 scene entry。
2. 列出在画面中可验证的连续性项和仅在叙事层存在的项。
3. 为高风险项指定控制手段：实际参考图、首帧、尾帧、角色资产、道具 insert、构图遮挡或单独生成。
4. 写 `reference_inputs_expected`；调用模型时另写 `reference_inputs_attached` 和 hash。
5. 检查 screen direction。人物从画面右侧离开后，下一镜默认从左侧进入；有意跳轴必须标记 `intentional_discontinuity` 和叙事理由。
6. 对多人、道具交接、伤势变化和昼夜变化建立显式 transition clip 或声音/字幕桥；不得让模型自动补因果。

## 生成后回写

生成输出不能自动成为真源。先：

1. 截取进入、中点、退出证据帧并记录时间码/hash。
2. 将实际身份、服装、位置、道具、光线和动作结果与预期逐项对照。
3. 叙事目的完成但有可剪掉的边缘漂移时，记录 trim/insert 修复，不把错误状态写入 canonical exit。
4. 错误状态在最终剪辑中清晰可见时，reject 或回退上游；不得为迁就错误输出篡改后续故事。
5. 只有 clip QC `approved` 后，才把实际 exit 合并到后续 entry。

## 冲突处理

```yaml
conflict_id: CONT-C-S01-04-02
severity: blocking
entity_id: PROP-LETTER
field: condition
expected: opened_by_steam
observed: sealed
evidence_frame: C-S01-04@00:04.1
impact: "下一镜女儿无法看到信中关键句"
repairs:
  - "局部编辑信封开口"
  - "拆出信件 insert，以独立首帧控制"
  - "修改下一 beat，改由声音提供线索"
selected: null
```

- `cosmetic`：不影响身份、因果和剪辑，可记录并接受。
- `repairable`：可用 trim、insert、遮挡、声音或局部编辑修复。
- `blocking`：破坏人物身份、关键动作、因果或下一镜状态；禁止进入 final manifest。
- `intentional_discontinuity`：有明确形式目的和观众理解测试，不当成错误。

## 验收

- 100% approved clips 引用 frozen visual bible 版本。
- 100% 相邻 clips 的 canonical exit/entry 可合并，或有显式 transition/conflict resolution。
- 所有 must-preserve 项有实际控制手段；不能只有文字“保持一致”。
- 所有 blocking conflicts 已修复、重生成或由上游改写解决。
- 人物、服装、道具、空间、时间、光线、知识状态都有证据帧或明确的不可见标记。
- 最终剪辑使用的 in/out 范围与 continuity evidence 的时间范围一致。
