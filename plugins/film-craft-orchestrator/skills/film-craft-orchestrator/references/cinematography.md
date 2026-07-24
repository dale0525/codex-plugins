# Visual Supervisor / Cinematographer：把导演意图变成可生成的视觉系统

> 本文件面向 AI 视频的视觉结果，不管理实体灯具、器材、通告或片场安全。先冻结视觉圣经和参考资产，再把每个 clip 编译为模型可理解的构图、相机、光线和连续性约束。景深/焦点运行 `focus-depth-information-plan`，跨镜光向、动态范围和色彩运行 `motivated-light-range-color-continuity`；两者见 `distilled-targeted-foundation-procedures.json`。

## 目录

- [从叙事目的反推视觉](#从叙事目的反推视觉)
- [视觉圣经](#视觉圣经)
- [构图、视角与景深](#构图视角与景深)
- [相机行为](#相机行为)
- [光线、色彩与材质](#光线色彩与材质)
- [场景与世界锚点](#场景与世界锚点)
- [从 Director Intent 到 Clip Visual Spec](#从-director-intent-到-clip-visual-spec)
- [验收](#验收)

## 从叙事目的反推视觉

任何视觉选择都写 `purpose`：观众看见/不知道什么，人物关系如何变化，为什么此刻移动或保持静止。模型名称、镜头品牌和“电影感”不能替代结果描述。

先回答：

1. 关键叙事物有多大、位于画面哪里、何时可见？
2. 观众与角色的空间距离和知情距离是什么？
3. 前景、中景、背景分别承载谁/什么？
4. 哪个变量在这一 beat 变化：距离、遮挡、焦点、机位、运动、光向或色彩？
5. 哪些视觉不变量必须跨 clips 保持？

## 视觉圣经

`visual_bible.yaml` 至少冻结：

```yaml
aspect_ratio: "16:9"
visual_medium: "grounded live-action realism"
camera_language:
  framing: [wide_two_layer, medium_ots, close_reaction, prop_insert]
  viewpoint: "mostly CHAR-MIRA knowledge"
  allowed_movement: [locked, slow_lateral_reveal]
  forbidden_movement: [orbit, unmotivated_crane]
  horizon: level
lighting_rules:
  key_direction: window_left
  key_quality: soft_overcast
  practicals: [warm_stove_background]
  contrast_rule: "faces keep one shadow side until confession"
palette:
  base: [cool_blue_gray, muted_amber]
  accent: [letter_white]
texture: "fine grain, low digital sharpening"
must_preserve: [screen_direction, key_direction, wardrobe_versions, prop_scale]
must_not: [readable_generated_letter_text, extra_people, neon_teal_orange]
```

角色、地点、道具和母题使用稳定 ID；不同服装、年龄、天气、伤势和时间状态建立版本。探索图、scene master、角色表、道具master、首帧、末帧和style reference分开标 `role`。

同一人物跨年龄、伤疤、死亡/重生或明显服装状态时，分别建立状态 ID（例如 `CHAR-X.PREDEATH` 与 `CHAR-X.REBORN-21`）并定义允许变化；不能把后一状态的“无疤”“更年轻”误写成跨时间 identity invariant。

## 构图、视角与景深

### 等效视角

用可见关系描述等效焦段，不假装视频模型精确模拟物理镜头：

- **广角近距离**：空间延展、前后比例差大、边缘更易变形；适合空间关系和不稳定亲近。
- **中性视角**：主体比例自然，便于表演与空间同时可读。
- **长焦远距离**：空间压缩、背景靠近、观察感增强；适合隔离或被监视。

Prompt 同时写等效视角和结果，例如“moderate telephoto perspective; compressed doorway and subject; no wide-angle edge distortion”。最终是否命中由画面QC判断，不以模型返回的元数据当光学事实。

### 景深与焦点

景深是注意力和连续性选择：

- 深景深：前中后景关系同时发生，适合多人blocking和视觉喜剧。
- 浅景深：隔离信息，但容易丢失关键道具、眼线和背景锚点。
- 焦点转移：只在注意力交接时使用，明确 start focus、trigger 和 end focus。

禁止同时要求“极浅景深”和“所有背景文字清晰”。关键线索必须在目标交付分辨率下可辨；精确文字优先独立资产/合成，不依赖视频模型生成。

### 前中后景

每层只分配必要职责：

```yaml
foreground: "Mira hand, partially hiding PROP-LETTER"
midground: "Mira face, visual focus"
background: "father at sink, soft but identifiable"
reveal_trigger: "plate leaves foreground"
```

多人场景用 `visual-bible-world-anchor-build` 和 `complex-action-coverage-split`；用遮挡和层次引导注意力，不让每个人同等锐利地抢画面。

## 相机行为

一个 clip 默认一个主要行为：

- `locked`：让人物走位、表演或声音承担变化。
- `pan/tilt`：追随已有注意力，不提前泄露。
- `push/pull`：改变心理/信息距离，必须有触发。
- `lateral reveal`：由遮挡到并置或暴露空间关系。
- `tracking`：保持主体相对尺度，强调路径和环境。
- `handheld drift`：只在视觉圣经中有明确规则时使用。

相机行为写 start/trigger/end：

```yaml
camera:
  framing: medium_two_layer
  position: mira_side_30deg
  movement: slow_lateral_reveal
  trigger: "father lifts plate"
  end_condition: "letter fully visible; Mira remains foreground"
```

`locked` 不能与 `orbit/pan/dolly` 同时出现。镜头运动、人物动作和时长不相容时先拆 clip，不增加形容词。

## 光线、色彩与材质

用光向、光质、对比、色温、来源和时间变化描述结果：

```yaml
lighting_intent: "角色从自保走向暴露，阴影侧在每个scene减少"
key_direction: window_left
key_quality: soft_cool_overcast
fill_level: low
practical: warm_stove_background
background_separation: subtle
continuity_risks: [window_direction, shadow_side, stove_color, time_of_day]
```

每个场景冻结 `key_direction / shadow side / time / weather / practicals`。生成后比较进入、中点、退出帧；光向漂移不能用“cinematic lighting”覆盖。

颜色不使用普遍情绪字典。定义出现条件、归属、变化和高潮回收。调色用于统一曝光、色温、对比、饱和度和grain，不修复身份、几何或故事状态。

材质/纹理记录肤质、织物、金属、玻璃、雨、雾和grain尺度。过度锐化、塑料皮肤、局部闪烁和纹理爬动进入clip QC。

## 场景与世界锚点

地点master不是一张氛围图。记录：

- 布局和出入口。
- 固定物、门窗和主要方向。
- 角色与道具相对尺度。
- 前中后景层次和可取景位置。
- 时间、天气、光源和背景活动。
- must-not 时代/文字/人物错误。

至少选择三个跨镜可见锚点。松散world reference可用于取景探索；精确production start/end frame必须通过全部连续性不变量，不能混用。

## 从 Director Intent 到 Clip Visual Spec

1. 冻结 aspect ratio、visual medium、scene master、camera/lighting rules 和角色/道具版本。
2. 为每个 beat 写观众任务和关键可见物。
3. 设计能承担空间与表演的 base clip，再增加必要coverage。
4. 每clip填写：framing、viewpoint、camera position、equivalent perspective、depth/focus、one movement、lighting、foreground/midground/background、reference roles、must-preserve 和 must-not。
5. 生成对应 start frame；检查构图、角色、道具、地点锚点、光向和可读性。
6. 通过后写 provider-neutral prompt IR；adapter不得改变视觉规则。
7. 输出后比较 entry/mid/exit evidence frames，并将实际状态写入clip QC和continuity state。

示例：

```yaml
clip_id: C-S04-03
narrative_purpose: "盘子移开后，观众与Mira同时看见信"
framing: "locked medium two-layer"
equivalent_perspective: "neutral-to-moderate telephoto; no wide distortion"
foreground: "Mira shoulder and hand"
midground: "plate and letter"
background: "father soft at sink"
focus:
  start: plate
  trigger: plate clears letter
  end: letter then Mira eyes
camera_behavior: locked
lighting: "cool window left; warm stove background"
reference_inputs_expected: [FRAME-S04-03-START, CHAR-MIRA.W01, LOC-KITCHEN, PROP-LETTER]
fallback: "letter insert + Mira reaction"
```

## 验收

- visual bible 中角色、地点、道具、相机、光线、色彩和母题规则没有冲突。
- 每个clip的叙事关键物在目标分辨率和景别下可见。
- 一个clip只有一个主要相机行为；相机不提前泄露beat。
- 相邻 clips 的角色尺度、screen direction、地点锚点、光向、时间和grain可合并。
- expected references 实际附加并hash对应；缺失时不标production-ready。
- 画面漂亮但未完成 `narrative_purpose` 一律reject。
- 调色/颗粒只修 surface continuity；身份、几何、关键道具和因果错误回到生成或上游。

项目内A/B优先于固定字典：同一storyboard只改变视角、景深、机位或光向中的一项，比较注意力和空间理解，不比较抽象“高级感”。
