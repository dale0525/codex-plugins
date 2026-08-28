# AI Video Deliverables：生产包 schema 与验收

## 目录

- [生产包文件](#生产包文件)
- [统一头部](#统一头部)
- [Visual bible](#visual-bible)
- [Reference manifest](#reference-manifest)
- [Clip plan](#clip-plan)
- [Prompt pack](#prompt-pack)
- [Continuity state](#continuity-state)
- [Generation log](#generation-log)
- [QC、剪辑与声音](#qc剪辑与声音)
- [Production-ready 门](#production-ready-门)

## 生产包文件

```text
ai_video_brief.yaml
adaptation_matrix.csv               # 有源材料时
story_and_scene_map.yaml
director_intent.yaml
visual_bible.yaml
reference_asset_manifest.yaml
semantic_reviews.yaml
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

模板位于 `assets/templates/`。单一事实只允许一个 canonical 文件；其他文件用稳定 ID/版本引用，不复制成另一套真源。人工真源止于 brief、adaptation matrix、story map、director intent、visual bible、reference manifest 和 semantic reviews；其余文件由编译器生成骨架，再追加真实 generation/QC 结果。编译完成后，`editing-sound` 仅可在同一派生文件中增量填写编辑/声音字段，并记录 owner、status、source artifact version；上游重新编译时旧的编辑/声音增量按版本失效，不得另建平行文件或静默覆盖。

创建包时运行 `init_ai_video_package.py <output>`；小说、真实事件、旧剧本或其他有源材料改用 `init_ai_video_package.py <output> --with-adaptation`。默认只初始化上游输入，阶段冻结并独立审查后运行 `compile_ai_video_package.py` 生成下游。`--full-templates` 只用于旧式手填流程兼容。CSV 中含逗号、引号或换行的字段必须正确引用。

## 阶段真源、语义审查与编译

`director_intent.scenes[].clip_specs[]` 保存所有不可机械推导的 clip 决定：beat、purpose、时长/handles、可见与叙事实体、地点、entry/exit state、action steps、注意力、camera、lighting、时间顺序、continuity invariants、禁项、generation method、adapter、expected refs、carrier override、风险、fallback 和 sound 意图。下游只复制或派生稳定 ID、CSV quoting、prompt rendering/hash、reference transport、continuity linkage、planned log/QC、edit timeline、sound timecode、final pending 与 probe selection。

`semantic_reviews.yaml` 每个阶段恰有一个独立 review。review 必须声明 `independent_from_authors: true`、`expected_answer_visible: false`、`review_scope: source_and_frozen_artifacts_only`，保存全部阶段输入 SHA-256，并用 claims 覆盖所有 source units、must beats、delivery requirements 和 clips。adaptation 覆盖 brief+matrix；story 覆盖 brief+matrix（有源时）+story；director 覆盖 story+director+visual+reference manifest。claim 的 entailment 只能在 `preserved|approved_change` 时通过；`contradicted|unsupported` 必须回退上游。任何输入字节变化都会使 review hash 失效。

固定命令顺序：

```bash
python scripts/validate_adaptation_stage.py <package>   # 有源材料时
python scripts/validate_story_stage.py <package>
python scripts/validate_director_stage.py <package>
python scripts/compile_ai_video_package.py <package> --adapters references/model-adapters.json
python scripts/validate_ai_video_package.py <package> --adapters references/model-adapters.json
```

编译器默认拒绝覆盖派生文件。`--replace-derived` 是显式废弃旧派生骨架的操作，不能用来清除生成历史或 QC 证据。

## 统一头部

所有人读/机读 artifact 至少包含或伴随：

```yaml
id: blue-ticket.clip-plan
version: 1.0.0
owner_role: ai_video_supervisor
source_inputs: [blue-ticket.director-intent@1.0.0, blue-ticket.visual-bible@1.0.0]
assumptions: []
constraints: {}
open_questions: []
status: draft|frozen|generated|qc_pending|approved|rejected|superseded
change_log: []
```

## Visual bible

必填：

- `aspect_ratio`, `visual_medium`, `palette`, `camera_language`, `lighting_rules`。
- `characters[]`：稳定 ID、`state_versions[]`、可见锚点、禁止漂移项、reference IDs。重生、闪回、跨年、服装或伤势变化使用不同 state ID。
- `locations[]`：布局、方向、固定物、光源、时间/天气、reference IDs。
- `props[]`：外观、尺寸、状态机、初始位置/持有人、reference IDs。
- `motifs[]`：出现条件、变化和回收。
- `must_preserve`, `must_not` 和 `reference_assets`。

只有实际 attached 的图像/视频才能算 reference；写在 prompt 中的路径不算附加。

## Reference manifest

每项：

```yaml
asset_id: FRAME-C05-START
role: first_frame
entity_ids: [CHAR-MIRA, LOC-KITCHEN, PROP-LETTER]
path_or_uri: work/references/c05-start.png
sha256: 64-hex
dimensions: 1920x1080
rights_status: user-owned
source: generated_from_visual_bible
version: 1.0.0
status: approved
```

Reference 使用时记录 expected/attached/transport；缺少 expected 时结果只能标 diagnostic preview。

尚未制作的 planned/pending asset 必须先有稳定 `asset_id`、role、entity IDs 和 `reference_transport: none|not_attached`，但 `path_or_uri` 与 `sha256` 保持空。只有实际文件存在并完成 hash 后才能改成 attached/approved；attached/approved 而无 SHA-256 必须验证失败。Descriptor hash 要另写 `hash_scope: descriptor_only`，不得冒充 raster hash。

## Clip plan

CSV 每行至少：

```text
clip_id,scene_id,beat_ids,priority,narrative_purpose,target_duration_sec,
edit_contribution_sec,handle_in_sec,handle_out_sec,subject_ids,prop_ids,
location_id,entry_state_ref,primary_action,attention_change,camera_behavior,
exit_state_ref,generation_method,visual_bible_ref,reference_inputs_expected,
prompt_pack_ref,continuity_risks,fallback,status
```

规则：

- 每个 must beat 至少有一个 must clip 或 approved fallback。
- `target_duration_sec >= edit_contribution + handles`。
- 一个 clip 一个主要动作和主要摄影机行为；例外必须有 `complexity_exception`。
- IDs 必须存在于 visual bible、scene map、continuity state 或 reference manifest。
- 八秒及以下的 prompt 默认最多三个可独立验收 `action_steps`；跨等待时间、地点或新叙事钩子时拆 clip。

## Prompt pack

每个 clip：

- `prompt_ir`：主体、动作 start/motion/end、环境、相机、光线、时间顺序、连续性不变量、禁项。
- 小说改编另需 `visible_character_ids`、`visible_prop_ids`、`narrative_character_ids`、`narrative_prop_ids`、`action_steps`、`delivery_requirement_ids` 与逐项 `information_carriers`；visible IDs 与 clip plan 对齐，narrative IDs 可包含跨镜/画外参与实体。
- `adapter_id` 与 `adapter_version`。
- `rendered_prompt` 与 SHA-256。
- `reference_inputs_expected`, `reference_inputs_attached`, `reference_transport`。
- `output_contract`：时长、画幅、分辨率、fps/音频（官方明确时）。
- `parameters`：只允许 adapter 官方支持字段；未知不填写假值。
- `production_status`：缺必需能力/参考时不得 production_ready。

`delivery_requirement_ids` 从 adaptation matrix 与 story map 继承，并必须出现在 edit timeline。每个 `information_carrier` 写明观众收到的实际内容、载体和 fallback；精确 UI、日期、金额、姓名或枚举项不得只存在于上游说明。

每项 requirement 另列 `required_character_ids`、`visible_character_ids`、`required_prop_ids`、`location_ids`。prompt 的 `narrative_character_ids`/`narrative_prop_ids` 保留画外音、电话或字幕参与者；clip `subject_ids` 只列实际可见人物。承载 requirement 的 prompt、clip 与 continuity 必须使用同一地点和实体集合。

## Continuity state

每个 clip 有 entry/exit/expected_next 和 conflicts。至少记录人物身份/服装/位置/方向/知识、道具位置/持有人/状态、地点/时间/天气/光向、前后 clip。状态 append-only，修订使用 version/supersedes。

## Generation log

JSONL 每行一次尝试：

```json
{"run_id":"RUN-C05-002","clip_id":"C05","attempt":2,"baseline_run_id":"RUN-C05-001","primary_variable":"action_scope","provider":"example","model":"example","model_version":"2026-07","adapter_id":"adapter-example","prompt_hash":"sha256:...","reference_hashes":["sha256:..."],"seed":null,"output_uri":"work/outputs/c05-002.mp4","output_hash":"sha256:...","actual":{"duration_sec":5.0,"width":1280,"height":720,"fps":24},"status":"qc_pending","error":null}
```

不存 API key、cookie、签名 URL 或账号信息。非 exploratory 尝试必须有唯一 `primary_variable`；首轮可为 `baseline`。

## QC、剪辑与声音

`clip_qc_report.yaml`：技术、语义、连续性、可剪性、声音/口型、来源/同意六门；每门 `pass|fail|pending`、证据和 repair。

`edit_plan.yaml`：timeline 顺序、clip in/out、handles、cut/transition、cut purpose、must beat、声音桥和被绕过失败。

`sound_cue_sheet.csv`：cue ID、scene/clip、时间、类别、diegetic、source/rights、音频 hash、进入/退出、同步对象、状态。

`final_film_qc.yaml`：运行时对账、must beat、连续性、对话/口型、声音 stem、来源/provenance、未决风险和最终状态。

`generation_probe_plan.yaml`：从冻结 clip risks 自动选择多人/儿童、精确手部/UI/文字、年龄/时空转换、道具/食物连续性、对白/口型探针。编译时 `producibility_status` 固定为 `hypothesis`；只有真实输出 hash、三帧证据和相应 QC 通过后才可标 `verified_for_sampled_clips`。

## Production-ready 门

- 上游 story/director/visual bible 均 frozen 且版本一致。
- clip IDs 唯一；beat、角色、道具、地点、状态和 prompt 引用无悬空。
- required adapter 能力明确满足；弃用/地域/账户限制已记录。
- expected references 100% 实际附加并 hash 对应。
- prompt 无明显互斥相机、时长/动作或景别/信息要求。
- approved clip 有成功生成记录、输出 hash 和完整 QC。
- blocking continuity conflicts 为零。
- `Σ edit_contribution_sec` 与目标时长一致；handles 不重复计入。
- must beats 100% 在 edit plan 被使用。
- adaptation、story、prompt 与 edit 的 delivery requirement 集合完全一致，且每项有实际 carrier。
- 声音/口型、权利/同意和模型 provenance 完整；未知项保持 pending，不能标 final approved。
