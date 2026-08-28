---
name: film-craft-orchestrator
description: 面向 AI 生成电影、剧集、短片、广告叙事和小说影视改编的端到端影视创作与生成监督 skill。用户需要故事概念、人物弧、场景、对白、剧本、导演阐述、视觉圣经、场面调度、镜头/clip 规划、摄影与灯光语言、模型路由、参考资产、提示词包、跨镜连续性、生成失败修复、对白口型、剪辑声音或最终成片质检时使用。默认把改编顾问→编剧→导演→视觉监督→AI 视频监督→剪辑/声音串成可回溯流水线，不把单一理论、厂商宣传或未附加的参考图当成事实。
---

# Film Craft Orchestrator

## 目标

把原创概念、小说、真实事件或旧剧本转换为可生成、可迭代、可剪辑和可验收的 AI 视频生产包。用专业编剧、导演、摄影、剪辑和声音判断控制模型，而不是把整段剧本粘进视频生成器。

默认使用中文沟通；剧本、对白和提示词语言按项目指定。稳定的故事/导演判断与易变的模型能力分开保存。任何阶段都必须能回到输入、版本、来源、参考 hash、生成记录和连续性状态。

## 先路由，再读取

先读取 `references/routing.md`，再按交付物选择最小必要 references：

1. 小说、短篇、真实事件或旧剧本改编：`adaptation.md`；需要来源驱动程序时加读 `distilled-video-procedures.json`、`distilled-sorkin-procedures.json` 和 `distilled-targeted-foundation-procedures.json`。
2. 概念、人物、结构、场景、对白或剧本：`writer.md`；按问题加读 scene、structure、theme、Sorkin 和 foundation procedures；长篇/剧集加读 `distilled-targeted-foundation-procedures.json`。
3. 导演意图、blocking、表演、注意力、coverage：`director.md`；多人/动作/视觉叙事加读 `distilled-foundation-procedures.json` 和 `distilled-targeted-foundation-procedures.json`。
4. 构图、视角、等效焦段、景深、运动、光线、色彩、world look：`cinematography.md`；景深、焦点与跨镜光色连续性加读 `distilled-targeted-foundation-procedures.json`。
5. AI 生产包、参考资产、clip 拆分、prompt、模型、连续性、失败修复：依次读取 `ai-video-workflow.md`、`ai-video-model-routing.md`、`ai-video-continuity.md`、`ai-video-failure-repair.md`、`ai-video-deliverables.md` 和 `distilled-ai-video-procedures.json`。
6. 剪辑、口型、对白、Foley、ambience、SFX、音乐或最终混音：`editing-sound.md`；表演剪辑、反应选择和 coverage 缺口加读 `distilled-targeted-foundation-procedures.json`。
7. 需要外部影视事实时，使用通用研究流程并记录一手来源；本插件不再提供视频证据研究技能。

只问单阶段时，不暗改上游。端到端请求固定走：

```text
source/brief
  → adaptation map
  → story and scene map
  → director intent
  → visual bible
  → scene-to-clip plan
  → provider-neutral prompt IR
  → model adapter prompt pack
  → generation log + single-variable iteration
  → continuity and clip QC
  → edit and sound plan
  → final film QC
```

## 最小 intake

信息不足时列出假设并继续；只有会显著改变故事、权利或生成路径的选择才阻塞。

```yaml
project_id: short-slug
format: feature|series|short|scene|ad|novel-adaptation|analysis
audience: target viewers
runtime_sec: target final runtime
aspect_ratio: "16:9"
genre_tone: genre + tonal references
source_material: none|user-provided|public-link|unknown
rights_status: user-owned|licensed|public-domain|unknown|not-applicable
creative_goal: one sentence
must_beats: []
model_access: []
reference_assets: []
dialogue_strategy: separate_audio_then_lipsync|native_audio|voiceover|none|unknown
generation_constraints:
  iteration_limit_per_clip: 4
  credit_budget: unknown
delivery_constraints: platform, resolution, fps, language, deadline
assumptions: []
open_questions: []
```

源材料或声音/肖像权利未知时可以继续做结构和诊断，但必须保留 `rights_status: unknown`，不得把结果标成可发行 final。

## Canonical artifacts

完整 AI 视频项目分为人工真源和编译产物。人工只创作、审阅和冻结：

```text
ai_video_brief.yaml
adaptation_matrix.csv                  # 有源材料时
story_and_scene_map.yaml
director_intent.yaml
visual_bible.yaml
reference_asset_manifest.yaml
semantic_reviews.yaml
```

`director_intent.scenes[].clip_specs[]` 是唯一 clip 设计真源。冻结后由编译器生成，不得人工平行重写：

```text
clip_plan.csv
generation_prompt_pack.json
continuity_state.json
generation_log.jsonl
clip_qc_report.yaml
edit_plan.yaml
sound_cue_sheet.csv
final_film_qc.yaml
generation_probe_plan.yaml
```

创建包之前必须先运行：

```bash
python scripts/init_ai_video_package.py <empty-output-directory>
# 有源材料的项目必须改用：
python scripts/init_ai_video_package.py <empty-output-directory> --with-adaptation
# 旧式手填全模板只作兼容，不用于新项目：
python scripts/init_ai_video_package.py <empty-output-directory> --full-templates
```

新项目不得一次性填写完整生产包。先冻结上游阶段并通过独立语义审查，再运行 `compile_ai_video_package.py`。编译器只复制、展开、hash 和派生；不得替代改编、表演、blocking、构图或信息载体判断。真实 canonical 文件名使用下划线，以 `assets/templates/` 为唯一 schema 真源；validator 暂时兼容旧连字符文件名，但新包不得继续产生旧名。

`semantic_reviews.yaml` 的审查者不能是该阶段作者，不能看期望答案或旧改编，只能看授权来源与待冻结 artifacts。每条 claim 必须写 source claim、proposed screen claim、`preserved|approved_change|contradicted|unsupported`、源/成片位置、时间关系、责任人物、选择/结果极性和证据；每个 source unit、must beat、delivery requirement 和 clip 都必须被 claim 覆盖。adaptation review hash brief+matrix，story review hash brief+matrix（有源时）+story，director review hash story+director+visual+reference manifest；任一输入改动，旧审查自动失效。

有源材料时，`adaptation_matrix.csv` 是必检 canonical artifact，不是说明附件。用真正的 CSV writer 或逐字段正确引用所有含逗号、引号或换行的值；不得手写出列错位文件。每个源单元使用稳定定位和单独的功能记录。两个独立人物选择、因果结果或结尾状态不能塞进同一行/同一 must beat 来伪装覆盖；源作已有动作不得因新增视觉母题而把整行标成 `invent`。过程与结果必须区分，例如“送审/正常审核”不得改写成“拒绝申请”，“来电”不得无意改成“接听”。

每个必须落到成片的源事实、动作结果、关系信息或精确文字/数值都分配稳定 `delivery_requirement_id`。同一 ID 必须同时出现在 adaptation row、story beat、对应 prompt 的 `information_carriers` 和 edit timeline；不能只把 ID 抄下去，carrier 的 `content` 必须写清观众最终看到或听到什么。需要精确日期、姓名、金额或多项枚举时，不依赖生成模型写字：明确选择 `post_text`、`separate_audio`、`prop_composite` 或可拍实物，并给 fallback。独立必要项未进入 prompt/edit 即视为功能缺失。

每个 delivery requirement 同时声明 `required_character_ids`、实际 `visible_character_ids`、`required_prop_ids` 与 `location_ids`。prompt 另写 `narrative_character_ids`、`visible_prop_ids` 和 `narrative_prop_ids`；人物或道具可以只通过电话、字幕、画外音或跨镜信息链参与叙事，但不能从 narrative IDs 消失，`visible_prop_ids` 必须与 clip plan 的实际可见 props 一致。承载该 requirement 的 clip/prompt 必须满足这些实体与地点约束，禁止把红毯、医院、公寓和酒店统一伪写成一个地点来过检。

一个 atomic delivery requirement 默认不跨越超过三个 beats。新的关系钩子、未决来电、独立人物选择或独立结果必须拥有自己的 requirement 和 source row；不得用“早餐”ID覆盖“唐晚来电”，也不得把未接来电改成接听。

不得凭记忆编段落号。先对实际源文件编号，再为每行填写可在原文逐字搜索命中的唯一 `source_anchor`；回源逐行验证 locator 与 anchor 后才冻结。每个关键选择同时追踪它的即时诱因/压力、可见选择和结果；如果只保留“拒绝/拉黑”却删掉让该选择困难的舆论、诱惑或关系压力，视为因果未覆盖。每个 row/beat 的源功能必须实际进入 prompt 与 edit，不能只停在 story map 或 visual bible。

每个事实只有一个真源；其他文件只引用稳定 ID 和版本。每个 `clip_id` 都必须在 prompt、continuity、generation log、clip QC 和 edit timeline 中有独立记录；尚无结果时保留同一个 ID 的 `planned`/`pending` 记录，不能省略或写成 `C03-C09`。

`prompt_ir` 的 subject、action、environment、camera、lighting、temporal constraints 和 continuity invariants 都必须有实际内容；`rendered_prompt` 必须是可发送给所选 adapter 的完整提示，不能写 `pending`、`diagnostic` 或占位短词。即使尚未生成，generation log 也必须记录当前 rendered prompt 的 SHA-256 和相同 adapter ID。

每个 prompt 的 `output_contract.duration_sec` 必须是 adapter 官方 limits 允许的值；需要剪成更短贡献时，在 clip/edit plan 明写 trim 与 handles，不能把任意目标秒数伪装成模型参数。`image_first_frame`/首帧 I2V 必须列出 first-frame `reference_inputs_expected`；未制作时仍列 planned asset ID、attached 为空并保持 `diagnostic_preview`，不能以空 expected 通过。

`clip_plan.target_duration_sec` 必须等于对应 prompt 的 `output_contract.duration_sec`。成片贡献更短时用 `handle_in_sec + edit_contribution_sec + handle_out_sec == target_duration_sec` 明示可裁区间。adapter 已弃用或宣布 shutdown 时，每个 prompt 必须携带可见 `deprecation_warning` 和 `migration_fallback`；缺任一项即验证失败。

Continuity entry/exit 不能只放空的 characters/props/environment；每个非末镜都必须在 `expected_next.must_preserve` 写至少一个可核对路径。纯环境镜头也至少记录时间、地点、光向、天气或 screen direction 中的实际状态。

Continuity 中的角色、道具和地点必须存在于 visual bible；有人物或关键道具的非末镜，`must_preserve` 不能只写一个与叙事无关的通用环境字段。跨地点、跨年代或死亡→重生的切换要么有显式 transition 状态，要么在 story/director/edit/sound 中一致地定义桥接；不能让角色位置写“医院”而环境仍引用“红毯”。

每个 continuity entry/exit 至少包含 clip subjects/props；额外离画人物或 carry-through 道具也要保留具体状态。若写 `state_id`，必须命中对应角色的 visual-bible state version。`planned`、`sequence-time`、`neutral soft key` 不是可核对状态；时间、光向、服装/年龄/疤痕和道具状态要写具体值。

同一人物的年龄、疤痕、服装或死亡/重生状态不同时建立显式状态版本；不得把今生的“无疤”写成跨时间角色不变量。每个 clip 的 location 必须与 continuity entry/exit 的 location 一致；跨地点变化拆 clip，不用 `resolved` 标签掩盖同一 clip 的矛盾环境。

小说包的每个角色都要有 `state_versions`；存在重生、闪回、跨年或显著年龄/伤势变化时，在 story map 写 `temporal_state_changes`，逐项连接 `from_state_id → transition_beat_id → to_state_id`。每个 prompt 的 `visible_character_ids` 必须列出画面中所有可见人物并与 clip plan 一致；孩子、服务员、主持人、医生、路人中任何承担叙事功能或可连续辨认者都必须注册稳定 character ID，不能当无名布景漏掉。

状态版本必须写可核对的年龄/年龄段、时间上下文、伤势/疤痕和服装差异；`adult`、`前世或今生`、`按 clip 描述`、`前一状态`、`状态变化` 等占位词不构成状态或场景设计。temporal transition 的 from/to 版本至少有一项实际可见状态不同。

参考图不是每个 clip 的强制输入。确实选择纯 T2V 或不需要参考时，保留 `assets: []`、`reference_inputs_expected: []` 和 `reference_inputs_attached: []`；有连续性风险而参考图尚未制作时，则先写 expected asset ID 和 planned/descriptor manifest，attached 仍为空。不得为了通过 validator 创建空图片、伪造 raster hash 或把 descriptor 当实际附件。

交付前必须运行：

```bash
python scripts/validate_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
```

解析失败、缺字段、悬空 ID 或跨文件不一致都必须修复后再交付。`diagnostic_preview` 可以没有真实输出，但不能跳过 canonical records。

## 端到端工作流

### 0. 冻结 brief

把用户目标写成可验收的一句话，锁定时长、画幅、受众、类型、must beats、参考/权利、模型访问、迭代和交付限制。重大改动递增版本并写 `change_log`。

### 1. 改编与故事地图

有源材料时先做功能追踪：`must_preserve / translate / combine / omit / invent`。不得把“忠于”理解为逐段搬运；保留因果、视角、人物选择和主题压力，再决定载体。

Writer pass 依次产出 logline、主题问题、人物策略、sequence/beat map、场景卡、对白和剧本。每场至少有进入状态、目标、障碍、策略、转折、退出状态和可见行为。结构模型只作假设生成器。

每个 scene 只使用一个 `location_id` 和连续时间段；红毯→医院→公寓→酒店必须拆成不同 scenes 或显式 transition scene。scene 下所有 clips 的 location 必须与 scene location 一致。

每个 scene 的 `runtime_budget_sec` 必须等于该 scene 所有 clip `edit_contribution_sec` 之和；不能只让全片总时长碰巧相等而把14秒尾场塞进22秒素材。`hook` beat 只拥有一个独立 delivery requirement；早餐选择与下一关系来电必须拆 beats。

### 2. Director intent

把情绪结果词翻译成：观众知道/误解什么、注意力顺序、人物策略、blocking、空间方向、表演节拍、信息隐藏/揭示、coverage、声音和剪辑触发。相机不能提前宣布人物尚未做出的选择。

小说改编的每个 must beat 都要有独立 `performance_beats` 与 `blocking_beats` 记录；只给一条代表性表演或一条拉黑调度不能算导演控制完整。

Director scene 只能包含该 story scene 拥有的 beats，且 performance/blocking 集合分别与该 scene beats 完全一致；不得把全片 14 个 beats 复制到每个地点场景来伪装覆盖。

### 3. Visual bible

冻结：

- 角色身份、轮廓、脸部锚点、服装/年龄/伤势版本和 must-not-drift。
- 地点布局、门窗、固定物、方向、尺度和环境活动。
- 道具形状、颜色、尺寸、持有人和状态机。
- 画幅、构图、等效视角、景深、机位高度、运动、光向/光质/色彩和例外。
- 母题的出现、变化和回收条件。

同一 ID 不承载多个冲突状态。参考图、首尾帧和探索图分配不同 `role`；文本描述不能替代实际图像附件。

### 4. Scene-to-clip

按 beat 拆 clip，不按剧本句号拆。每个 clip 默认只有：

- 一个 `narrative_purpose`；
- 一个主要动作；
- 一个注意力变化；
- 一个主要相机行为；
- 明确 entry/exit state、handles、剪入/剪出和 fallback。

多人、复杂手部、道具交接、口型、相机运动和环境变化同时出现三类以上时默认拆分。先设计 base clip，只为 base 无法承担的信息、选择、反应或剪点增加 coverage。

把每个可独立验收的状态变化写成 `prompt_ir.action_steps`。八秒及以下默认最多三个短步骤；“退回早餐→重新下单→等待送达→进食→发现来电”不是一个动作，必须拆 clip 或明确做后期蒙太奇。不得用一句概括把四个动作伪装成一个 step。每个可见角色、精确信息 carrier 和 action step 在编译 rendered prompt 前做一次清单对账。

### 4.5 阶段冻结与确定性编译

禁止同一 agent 连续完成创作和语义放行。每次 review 只接收授权源和当前阶段 artifacts，不接收期望答案、run-05 修复包、旧成片或后续文件。固定顺序：

```bash
# 有源材料时
python scripts/validate_adaptation_stage.py <package-directory>
python scripts/validate_story_stage.py <package-directory>
python scripts/validate_director_stage.py <package-directory>
python scripts/compile_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
python scripts/validate_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
```

任何阶段失败只回到该阶段修订，重新冻结并重新独立审查；不能在下游补写一致文本来掩盖上游语义错误。编译器默认拒绝覆盖已有派生文件；只有明确要废弃旧派生骨架并已保留必要生成/QC 记录时才使用 `--replace-derived`。

### 5. Prompt IR 与 model adapter

先写 provider-neutral prompt IR：主体/状态 → start/action/end → 环境/空间 → 构图/机位 → 一个相机行为 → 光线/时间顺序 → 连续性不变量 → 禁项 → 输出合同。

再读取 `model-adapters.json` 和 `ai-video-official-evidence.json`：

- `required` 不满足即排除；`preferred` 只用于排序；unknown 按不支持。
- 参数、时长、分辨率、参考数量和 API 字段只认当前官方页面。
- 官方演示不证明成功率；创作者偏好不构成模型排名。
- adapter 不得改写 beat、人物选择、空间规则或导演意图。
- 弃用模型必须显示 shutdown、迁移和 fallback。

### 6. Reference transport gate

分别记录 `reference_inputs_expected` 与 `reference_inputs_attached`、role、hash 和 transport。必需参考未实际附加时只允许 `diagnostic_preview`；不得因 prompt 中出现路径或“same character”而标 production-ready。

### 7. 生成与单变量迭代

每次生成追加 `generation_log.jsonl`：run、baseline、唯一主要变量、模型/版本、adapter、prompt hash、reference hashes、参数、输出/hash、成本和状态。

`planned`、`pending`、`diagnostic_preview` 记录的 `actual` 必须是空对象，`output_uri`/`output_hash` 必须为空；请求的时长、分辨率和画幅只写 output contract，不能冒充实测输出。

验收顺序：叙事目的 → 身份/状态 → 时间/空间 → 可剪性 → 表面质量。先按 `ai-video-failure-repair.md` 分类，再提出可证伪原因。非 exploratory 轮次一次只改一个主要变量；连续两轮同类 blocking failure 时拆 clip、换模式/adapter或回退上游，达到 iteration/credit limit 时停止无界重抽。

编译生成的 `generation_probe_plan.yaml` 只把 producibility 标为 `hypothesis`。至少优先覆盖多人/儿童、精确手部/UI/文字、年龄或时空转换、道具/食物连续性、对白/口型中的四类；项目没有某类时写明 uncovered 原因。只有选中 clip 有真实输出 hash、入口/中点/出口证据帧和语义/连续性/可剪性 QC 后，才可升为 `verified_for_sampled_clips`，且不得外推为全片已验证。

### 8. 连续性与 clip QC

逐 clip 截取进入/中点/退出证据帧，比较 visual bible 和 continuity state：

1. 身份：脸、体型、年龄、声音。
2. 状态：服装、伤势、表情基线、手中物。
3. 空间：位置、轴线、screen direction、视线、尺度。
4. 环境：时间、天气、光向、背景活动、ambience。
5. 叙事：知识、目标、策略和因果从上一镜结果继续。

只有 approved clip 的实际退出状态才能写入后续 canonical entry。Blocking 冲突不能进入 final manifest。

### 9. 剪辑与声音

先用 must clips 剪出入口、目标、策略、转折、选择和退出；漂亮但不完成 beat 的素材不用。逐 cut 检查动作、视线、空间、信息、表演、声音和生成瑕疵。

对白、声音表演、画面表演、lip-sync 和最终剪辑分层。一个 clip 默认一个可见说话者；多人对白优先单人、OTS、反应和画外声。原生 AI 音频逐 clip 审计；不连续时静音并重建 dialogue、room tone、ambience、Foley、SFX、music/silence，不教条式清空合格原音。

### 10. Final film QC

只有以下条件同时成立才可 `final: approved`：

- 上游 artifacts frozen 且版本一致。
- must beats 100% 被最终使用区间覆盖。
- `Σ edit_contribution_sec == runtime_target_sec`，handles 不重复计入。
- expected references 全部实际附加并可追溯。
- approved clips 均有 generation log、输出 hash 和六门 QC。
- 相邻 entry/exit 可合并；blocking conflicts 为零。
- 对白/口型、声音 stems、权利/同意、模型/版本/provenance 完整。
- 未知项保持 pending；不能用人工口头判断覆盖验证器失败。

## 回退纪律

失败时回到拥有该决定的最近阶段：

- 故事/人物选择错 → writer/adaptation。
- 注意力、blocking、coverage错 → director。
- 角色/地点/look不稳定 → visual bible/reference assets。
- 动作负载过高 → clip plan。
- 能力或参数不支持 → model routing/adapter。
- 单个输出漂移 → generation/continuity。
- 可见错误只在边缘且不破坏beat → edit repair。

不得为迁就一次错误输出而静默篡改后续故事真相。

## 外部资料纪律

外部视频、网页或厂商资料只作为用户明确提供的研究输入，不自动启动下载、ASR 或抽帧流程。需要当前模型事实时，使用通用研究技能并记录一手来源；不要把候选视频、厂商演示或未查看资料当成已验证证据。画面型主张需要明确的帧/clip 来源；字幕、ASR、OCR 和缩略图不能单独证明构图、blocking、剪辑点、表演或光线。不得分发完整字幕、完整剧本或可替代原视频的连续镜头。

## 参考导航

- 角色和阶段接口：`routing.md`
- 改编/编剧/导演/视觉：`adaptation.md`, `writer.md`, `director.md`, `cinematography.md`
- AI 核心：`ai-video-workflow.md`, `ai-video-model-routing.md`, `ai-video-continuity.md`, `ai-video-failure-repair.md`, `ai-video-deliverables.md`
- 剪辑声音：`editing-sound.md`
- 模型能力：`model-adapters.json`, `ai-video-official-evidence.json`
- AI 实战程序与证据：`distilled-ai-video-procedures.json`, `ai-video-source-knowledge-base.json`, `ai-video-asr-evidence.json`, `ai-video-frame-evidence.json`
- 基础影视程序与证据：`distilled-foundation-procedures.json`, `foundation-video-knowledge-base.json`, `foundation-asr-evidence.json`
- 定向补强程序与证据：`distilled-targeted-foundation-procedures.json`, `targeted-foundation-video-knowledge-base.json`, `targeted-foundation-asr-evidence.json`, `targeted-foundation-frame-evidence.json`
- 剧本—成片对读：`distilled-script-screen-procedures.json`, `script-screen-video-knowledge-base.json`, `script-screen-asr-evidence.json`, `script-screen-frame-evidence.json`
- 旧语料程序：其余 `distilled-*-procedures.json` 与 `video-knowledge-base.json`
- 模板和字段：`ai-video-deliverables.md` 与 `assets/templates/`

## 确定性工具

- `init_ai_video_package.py`：默认只初始化人工上游；`--full-templates` 仅兼容旧包。
- `validate_adaptation_stage.py`：检查有源改编冻结、矩阵结构、独立审查 hash 与 ID 覆盖。
- `validate_story_stage.py`：检查 brief→story 版本、场景/beat/时长和独立语义审查。
- `validate_director_stage.py`：检查 story→director→visual/reference、逐 beat 表演/blocking 与唯一 `clip_specs`。
- `compile_ai_video_package.py`：从冻结上游确定性生成 clip、prompt、continuity、log、QC、edit、sound、final 和 probe 骨架。
- `validate_adaptation_matrix.py`：有源材料时检查 CSV 完整性、invent ID 和 must beat 来源覆盖。
- `validate_corpus.py`：来源、claim、procedure、frame 和统计双向引用。
- `validate_model_adapters.py`：adapter 与官方证据双向引用、版本和弃用字段。
- `validate_clip_plan.py`：clip ID、beat、时长、原子性和参考。
- `validate_prompt_pack.py`：悬空 ID、缺参考和明显互斥指令。
- `validate_continuity_state.py`：相邻状态、方向、服装、道具、时间和光线。
- `validate_ai_video_package.py`：跨文件生产包总检查。
- `tests/run_ai_video_fixtures.py`：确认 valid 包通过且关键错误被拒绝。
- `tests/run_staged_compiler_fixtures.py`：确认阶段门、审查失效、编译、小说改编和拒绝隐式覆盖。
- `tests/run_capability_packages.py`：验证六类 fresh-context 生产包。
- `tests/run_all.py`：运行完整确定性测试套件。

自动检查只证明确定性不变量；表演、意义和审美结论仍需证据帧、比较版本和明确审阅结论。

## 典型调用

- “把这篇短篇改成 90 秒 AI 剧情片”：完整流水线，交付生产包而非传统实拍 shot list。
- “两个人说话总是一起动嘴”：读 dialogue/lip-sync 程序，拆声音、可见说话者、反应和合成。
- “角色第三镜开始变脸”：运行 canonical-reference-reset、连续性冲突和单变量修复。
- “这段动作一个 prompt 总失败”：回到 action state chain，拆 clip、coverage、首尾帧和fallback。
- “分析一条 AI 电影教程”：先建 ASR/画面证据，再决定 deep、claim-only 或候选，不能把转录完成当蒸馏完成。
