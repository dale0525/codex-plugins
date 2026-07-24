# AI Video Workflow：从导演意图到可生成、可剪辑的 clips

## 目录

- [核心原则](#核心原则)
- [冻结上游输入](#冻结上游输入)
- [建立视觉圣经](#建立视觉圣经)
- [从 beat 拆成 clip](#从-beat-拆成-clip)
- [生成模型无关规格](#生成模型无关规格)
- [选择生成方法](#选择生成方法)
- [提示词编译](#提示词编译)
- [生成与迭代](#生成与迭代)
- [质检和交付](#质检和交付)

## 核心原则

AI 视频不是把剧本整段粘进模型。先保留编剧和导演的因果、注意力与空间判断，再把它拆成模型可完成的时间单元。

```text
story truth → director intent → visual invariants → clip contract
            → provider-neutral prompt IR → model adapter → output/QC
```

- 一个 clip 默认只承担一个主要动作、一个注意力变化和一个主要摄影机行为。
- 把稳定创作判断写入 `visual_bible`、`scene_plan` 和 `clip_plan`；模型名称、参数和语法只写入 adapter 与 prompt pack。
- 文本重复角色描述不能代替实际参考资产。记录 `reference_inputs_expected` 与 `reference_inputs_attached`；二者不一致时只能生成诊断预览。
- 不以一次成功输出推断提示词定律。保留 baseline、失败类型、唯一主要变量和每次输出 hash。
- 模型无法可靠完成复杂动作时，优先拆 clip、换生成模式或设计剪辑补救，不持续堆叠形容词。

## 冻结上游输入

进入 AI 生产前，至少冻结：

```yaml
project_id: blue-ticket
runtime_target_sec: 90
aspect_ratio: "16:9"
audience: "adult drama audience"
rights_status: user-owned
story_map_version: 1.0.0
director_intent_version: 1.0.0
must_beats: [S01-B01, S01-B02, S01-B03, S01-B04]
generation_constraints:
  model_access: []
  max_iterations_per_clip: 4
  credit_budget: unknown
  dialogue_strategy: separate_audio_then_lipsync
open_questions: []
```

每个 must beat 必须有：进入状态、可见目标/信息任务、触发、变化和退出状态。若上游只写“悲伤”“史诗”“电影感”，先退回 director pass，将结果词翻译成动作、空间、注意力或声音。

## 建立视觉圣经

`visual_bible.yaml` 是跨镜头真源，不是风格形容词清单。至少记录：

- `character_ids`：稳定身份、轮廓、面部锚点、发型、服装版本、比例和禁止漂移项。
- `location_ids`：布局、门窗、关键方向、背景层次、固定道具、时间和天气。
- `prop_ids`：形状、颜色、尺寸、持有人、状态和叙事功能。
- `camera_language`：允许的景别、视角、机位高度、运动和轴线规则。
- `lighting_rules`：主光方向、光质、色彩关系、时间变化和例外。
- `palette` 与 `motifs`：出现条件、递进和高潮回收；不把颜色当普遍情绪字典。
- `reference_assets`：路径或资产 ID、SHA-256、角色、实际用途、授权/来源和版本。
- `must_not`：不出现的人物、文字、时代错误、服装变化、风格漂移或内容风险。

同一角色有多套服装或年龄状态时，为每个状态分配版本 ID，例如 `CHAR-MIRA.WARDROBE-02`；不要改写同一 ID 的含义。

小说改编中的每个角色都使用 `state_versions`。重生、闪回、跨年、伤势或身份可见状态改变时，story map 的 `temporal_state_changes` 必须把旧状态、转折 beat 和新状态连起来。画面中任何承担信息、动作、关系或连续性的可见人都注册 ID；“孩子跑来叫爸爸”“服务员推餐车”不能只写进自然语言 prompt 而从 visual bible、clip subjects 与 continuity 消失。

每个 delivery requirement 先冻结叙事人物、可见人物、关键道具与地点，再编译 prompt。画外音、电话或字幕人物进入 `narrative_character_ids`；实际出镜者同时进入 `visible_character_ids` 与 clip subjects。prompt environment 的 location ID 必须命中 requirement 与 scene；不得靠自然语言写另一个地点来绕过结构字段。

每个 scene 只有一个 location 与连续时间；跨地点即拆 scene 或 transition。小说改编的每个 must beat 都要有 performance 与 blocking 设计，不用一条代表性记录代替全片导演控制。

## 从 beat 拆成 clip

对每个 beat 依次执行：

1. 写观众任务：`orient`, `reveal`, `withhold`, `choice`, `reaction`, `transition`, `payoff`。
2. 写最小可见事件：谁从什么状态，通过什么动作，进入什么新状态。
3. 判断单个生成是否需要同时解决多人、复杂手部、道具交接、镜头运动、口型和环境变化。三类以上高风险同时存在时默认拆分。
4. 先设计 base clip；只为 base clip 无法完成的知识、选择、反应或剪点增加 coverage。
5. 为每个 clip 写进入/退出状态、预期剪入/剪出点、前后 handle 和 fallback。

再把 clip 的动作拆为可独立验收的 `action_steps`。每个 step 都要有动作和 end state；八秒及以下默认不超过三个短步骤。跨越等待时间、地点、道具交接或新的叙事钩子时优先切成新 clip，不能用一句摘要掩盖动作过载。

推荐字段见 `ai-video-deliverables.md`。示例：

```yaml
clip_id: C-S01-04
scene_id: S01
beat_ids: [S01-B03]
narrative_purpose: "父亲端走盘子却留下信，信息权转给女儿"
target_duration_sec: 5
edit_contribution_sec: 4
handles_sec: {in: 0.5, out: 0.5}
subject_ids: [CHAR-FATHER, CHAR-MIRA]
prop_ids: [PROP-PLATE, PROP-LETTER]
location_id: LOC-KITCHEN
entry_state: "PROP-LETTER partially under PROP-PLATE"
primary_action: "CHAR-FATHER lifts and carries PROP-PLATE; PROP-LETTER remains"
attention_change: "plate → exposed letter → Mira reaction"
camera_behavior: "locked medium two-layer composition; do not follow father"
exit_state: "PROP-LETTER exposed on table; Mira has visual access"
generation_method: first_frame_image_to_video
fallback: "split into plate/letter insert and Mira reaction"
```

## 生成模型无关规格

在选择供应商前创建 prompt IR：

```yaml
clip_id: C-S01-04
subject:
  ids: [CHAR-FATHER, CHAR-MIRA]
  visible_traits: ["father in soft background", "Mira remains foreground"]
action:
  start: "father's hand on plate"
  motion: "lift plate and walk toward sink"
  end: "letter remains fully visible"
environment:
  location_id: LOC-KITCHEN
  time: predawn
camera:
  framing: medium_two_layer
  position: Mira_side_30deg
  movement: locked
  focus_event: plate_to_letter
lighting:
  key: cool_window_left
  practical: warm_stove_background
temporal_constraints:
  duration_sec: 5
  event_order: [plate_moves, letter_revealed, Mira_notices]
continuity_invariants:
  - CHAR-MIRA.WARDROBE-01
  - PROP-LETTER.COLOR-WHITE
negative_constraints:
  - no_third_person
  - no_camera_pan
  - no_letter_text_change
visible_character_ids: [CHAR-FATHER, CHAR-MIRA]
action_steps:
  - {step_id: A01, action: "father lifts plate", end_state: "letter exposed"}
delivery_requirement_ids: [DR-S01-B03-01]
information_carriers:
  - {requirement_id: DR-S01-B03-01, carrier: visual_action, content: "盘子移开后信完整露出", fallback: "盘子与信的独立 insert"}
```

adapter 只能把这个 IR 映射为模型输入；不得改变 beat、人物、动作、轴线或叙事目的。

## 选择生成方法

按控制需求选择，而不是按营销排名：

| 条件 | 优先方法 | 失败回退 |
| --- | --- | --- |
| 世界和角色尚未冻结 | T2V 概念探索，状态标 `diagnostic_preview` | 先生成参考图和 visual bible |
| 角色/场景身份必须稳定 | I2V 或角色/对象 reference | 拆人物与环境，后期合成或改构图 |
| 需要精确开场构图 | first-frame I2V | 先重做首帧，不继续改动作 prompt |
| 需要命中明确结尾 | first+last frames 或短 clip 分段 | 生成中间过渡并以剪辑连接 |
| 延续已通过的镜头 | extend/continuation | 保留原结尾帧，另生成反应/insert |
| 只需局部变化 | edit/inpaint/video-to-video | 提前切出或遮挡错误区域 |
| 对白表演和口型关键 | 画面、表演驱动、对白、lip-sync 分层 | 反应镜头/画外对白/短句拆分 |

调用任何模型前读取 `ai-video-model-routing.md` 和 `model-adapters.json`；能力未知时不得猜测。

## 提示词编译

按以下顺序编译，避免把所有信息写成无结构散文：

```text
subject identity and state
→ primary action with start/end
→ environment and spatial relation
→ framing and camera position
→ one camera behavior
→ lighting and temporal order
→ continuity invariants
→ negative constraints
→ output contract
```

编译前检查：

- `locked camera` 与 dolly/pan/orbit 不得同时出现。
- 景别必须足以看清叙事关键物；全景不能同时要求不可读小字清晰。
- 动作、镜头运动和时长相容；一个 4 秒 clip 不承担三轮策略和跨房间复杂交接。
- 不将“保持完全一致”当作 reference 缺失的替代。
- 不把镜头目的写成模型不可见的内心解释。
- `visible_character_ids` 与 clip subjects、visual bible 完全一致；所有承担叙事功能的孩子、服务员、医生、主持人等已注册。
- `action_steps` 没有把多个等待、地点变化或四步以上动作压成一句。
- 每个 `delivery_requirement_id` 都有具体 `information_carrier`；若 rendered prompt 禁止可读文字，精确信息明确交给后期字幕、独立声音或合成道具。
- requirement 的人物、可见人物、道具和地点与 prompt narrative IDs、clip subjects/props、environment 和 continuity 一致；不存在“结构字段全写酒店，rendered prompt 再说医院”的双重事实。

## 生成与迭代

每次生成追加一条 `generation_log.jsonl`：

```yaml
run_id: RUN-C-S01-04-003
clip_id: C-S01-04
attempt: 3
baseline_run_id: RUN-C-S01-04-002
primary_variable: camera_movement
change: "replace slow pan with locked camera"
provider: example-provider
model: example-model
model_version: 2026-07
adapter_id: adapter-example-01
prompt_hash: sha256:...
reference_hashes: [sha256:...]
seed: null
output_hash: sha256:...
status: qc_pending
```

迭代顺序：

1. 先确认输出是否完成 `narrative_purpose`；画面漂亮但 beat 失败仍是 reject。
2. 使用 `ai-video-failure-repair.md` 分类主失败，不把所有瑕疵混成“质量不好”。
3. 每轮只改变一个主要变量；同时改 prompt、参考、模型和时长只能标 `exploratory`，不能归因。
4. 连续两轮同类失败时换策略：拆 clip、换 I2V/首尾帧、降低多人/手部复杂度或设计剪辑补救。
5. 达到 `iteration_limit` 后停止消耗，记录最佳失败输出与下一种方法。

## 质检和交付

逐 clip 依次通过：

1. **技术**：文件存在、hash、时长、分辨率、fps、可解码、音轨状态。
2. **语义**：人物/道具正确，主要动作和叙事目的完成，事件顺序正确。
3. **连续性**：身份、服装、空间方向、道具、时间、光线和前后状态。
4. **可剪性**：有明确剪入/剪出点和足够 handles；失败区域可否避开。
5. **声音/口型**：对白归属、同步、环境连续性和音乐/效果层级。
6. **来源/同意**：参考资产、声音/肖像、模型/版本和生成记录完整。

只有 must beat 均被 approved clip 或 approved fallback 覆盖，且 edit/sound plan 与最终时长对账，才可生成 `final_film_qc.yaml`。自动检查只能证明字段和确定性不变量；表演、意义和审美判断必须保留人工/模型审阅结论与证据帧。
