# Director：把剧本变成观众的注意力路径

> AI 视频导演不管理实体剧组；核心是把人物策略、空间、注意力、表演和剪辑规则冻结成 visual bible 与 clip contract。多人/动作/声音先读取 `distilled-foundation-procedures.json`；三人以上调度、Z 轴、遮挡揭示和道具导视加读 `distilled-targeted-foundation-procedures.json`；AI 拆分和连续性读取 `distilled-ai-video-procedures.json`。

## 目录

- [导演问题](#导演问题)
- [场面调度](#场面调度)
- [表演与节拍](#表演与节拍)
- [Coverage 与 clip 设计](#coverage-与-clip-设计)
- [导演意图模板](#导演意图模板)
- [验收](#验收)

## 导演问题

每场回答：

1. 观众此刻知道、误解、等待什么？
2. 谁掌握空间、信息和时间主动权，何时转移？
3. 角色不能直说的内容如何由距离、视线、节奏、道具或声音显示？
4. 哪个动作或选择必须被看清？哪个信息应延迟？
5. 场景若只保留一个视觉和一个声音记忆点，是什么？
6. 哪些判断由表演承担，哪些由构图、运动、剪辑或声音承担？

每个含人物选择的场景都必须填写至少一个 `performance_beat` 和一个 `blocking_beat`；空数组、只列景别或只写“更克制”不构成 director pass。Performance beat 必须有 want、obstacle、tactic、playable action 和 reaction trigger；blocking beat 必须有 before、trigger、action、world/frame position、eye line、attention change 与 after。纯静物/环境段也要写清注意力与空间状态，不用空 director scene 混过冻结。

导演不是逐句插图。每个形式决定都要回答它让观众知道、误解、期待或重新解释什么。

## 场面调度

先画空间关系，再选镜头。使用动作动词：靠近、拦住、让路、占位、隐藏、暴露、交换、冻结、退出。对 AI 视频写 screen-space 和 world-space 双层坐标，不依赖实体片场米制施工。

```yaml
scene_id: S04
spatial_rule: "门框是权力边界"
axis: "table_to_door"
screen_direction:
  CHAR-MIRA: faces_right
  CHAR-KAI: faces_left
starting_positions:
  CHAR-MIRA: {world: table_left, frame: foreground_left}
  CHAR-KAI: {world: doorway, frame: background_right}
movement_beats:
  - beat_id: S04-B01
    trigger: "Kai 展示钥匙"
    action: "Kai 停在门外，迫使 Mira 先跨边界"
    attention_change: "Mira → key → doorway"
    exit_state: "Mira 在门内；钥匙仍由 Kai 持有"
```

每个 blocking beat 写：before、trigger、playable action、screen/world position、eye line、screen direction、attention handoff、after。一个 beat 只承担一次主要权力或信息变化。

### 权力/信息反转

1. 记录开场谁拥有视点、前景、出口、关键道具和信息。
2. 用人物策略触发走位或构图变化；相机不提前宣布反转。
3. 一次只改变一到两个主要视觉变量，便于归因。
4. 生成 before/midpoint/after 首帧或 storyboard，做无对白A/B。
5. 加回台词和声音后检查走位仍由策略触发。

`power-frame-reversal`、`blocking-power-transfer-map` 和 `visual-bible-world-anchor-build` 可直接组合；不得把“低角=强”“居中=重要”当固定字典。

## 表演与节拍

给角色可玩的行动，不给结果情绪。把“悲伤”改成“让对方以为自己不在乎”；把“紧张”改成“用整理桌面阻止自己看门”。

```yaml
beat_id: S04-B02
want_now: "让 Kai 交出钥匙"
obstacle: "不能暴露自己已发现票根"
tactic: "假装替他整理外套"
visible_behavior: "手伸向口袋后改为抚平衣角"
reaction_trigger: "Kai 后退半步"
cannot_say: "我知道你在撒谎"
```

AI 表演控制按风险选择：

- 静态表情/姿势：角色参考＋start frame。
- 微表情和节奏：真人/合成表演驱动，读取 `performance-capture-character-transfer`。
- 对白：先锁声音表演，再做单说话者口型；多人拆 coverage。
- 复杂接触：拆 before/contact/after，不让模型同时解决多人、手部、道具和相机。

输入表演、声音和人物肖像必须有授权/同意记录。工具参数由当前 adapter 决定。

## Coverage 与 clip 设计

Coverage 不是越多越安全。先选观看规则：

- **关系优先**：二人同框，只在权力/信息转移时切近。
- **主观优先**：镜头绑定某角色的知情范围。
- **空间优先**：稳定方向、视线和路径。
- **节奏优先**：切点由动作、声音、信息或反应触发。
- **生成风险优先**：多人、手部、道具、口型和复杂运动分开解决。

每个 clip 至少有一个职责：`orient / reveal / withhold / choice / reaction / transition / payoff`。先设计能承担表演和空间的 base clip，再只为 base 无法完成的知识、选择、反应或剪点增加 coverage。

```yaml
clip_id: C-S04-03
beat_ids: [S04-B02]
narrative_purpose: "观众先于 Mira 看见钥匙"
entry_state_ref: CONT-S04-02.exit
primary_action: "Kai 把手从口袋移到门框"
attention_change: "Kai face → key → Mira hand"
camera_behavior: "locked medium two-layer composition"
exit_state_ref: CONT-S04-03.exit
fallback: "key insert + Mira reaction"
```

### 多人和动作拆分

读取 `complex-action-coverage-split`：

1. 写 before→contact/change→after 状态链。
2. 同时出现三类以上高风险控制时默认拆分。
3. 正反打逐镜写左右位置、眼线和失焦前景。
4. 道具交接使用 before insert、遮挡/contact、after reaction。
5. 每个拆分仍须保持动作因果和呼吸；不能切成无意义碎片。

## 导演意图模板

```markdown
# Director Intent
## 一句话观众体验
## 主题问题与人物选择
## 叙事视角和信息纪律
## 场景 attention path
## 表演规则：目标、障碍、策略、反应
## Blocking、轴线、screen direction 和空间母题
## Base clip 与 coverage 规则
## 摄影、光线、声音、剪辑的统一语言
## 关键例外与理由
## 生成风险、fallback 和回退节点
## 验收和未决问题
```

## 验收

- 每场的入口、目标、策略、转折、选择和退出可由 must clips 独立成立。
- 每个 blocking movement 由人物策略触发，不是无理由“自然走动”。
- 轴线、screen direction、eye line、前中后景和道具状态可写入 continuity state。
- 多人/动作/口型已经按风险拆分，且 coverage 不重复同一信息。
- 无对白 storyboard 仍能指出目标反转 beat；声音版没有靠解释性台词修复空间问题。
- 失败能回到 writer、director、visual bible 或 clip plan，而不是靠无限 prompt 形容词。

练习：为同一场戏做静态同框、主观coverage和无对白blocking三版；只比较观众何时识别权力/信息变化，不比较哪版“更电影感”。
