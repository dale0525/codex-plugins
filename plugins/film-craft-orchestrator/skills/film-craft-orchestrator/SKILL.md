---
name: film-craft-orchestrator
description: 仅当用户明确要求端到端影视生产包，或同一请求跨越至少两个相邻影视阶段且后一阶段必须消费前一阶段 artifact 时使用。多个互不依赖的小建议、单阶段交付附带下游提示、普通视频摘要或产品研究不触发；单一阶段任务直接使用同插件对应分技能。
---

# Film Craft Orchestrator

## 角色

作为端到端影视总控，组织同插件的七个分技能，但不以“全都做一遍”代替明确路由。先判断任务跨越哪些阶段，再加载最少的共享资料。对于只要求一个剧本、一份摄影方案或一次连续性诊断的请求，转用对应分技能，不擅自改动其他阶段。

共享根就是本技能目录。`references/`、`scripts/`、`assets/` 和 `tests/` 是全插件唯一事实源；分技能不得复制这些资源。

## 分技能路由

| 请求 | 技能 | 主要边界 |
| --- | --- | --- |
| 小说、真实事件、旧剧本改编 | `$film-adaptation` | 来源功能、取舍、因果与改编矩阵 |
| 概念、人物、结构、场景、对白、完整剧本 | `$screenwriting` | 可拍行为、场景转折、人物选择与台词 |
| 导演阐述、表演、blocking、coverage | `$directing` | 注意力、空间、行为节拍、信息揭示 |
| 构图、机位、焦段、运动、灯光、色彩 | `$cinematography` | 视觉圣经与可执行摄影规则 |
| clip 拆分、提示词、模型、参考资产、生成日志 | `$ai-video-production` | provider-neutral IR 到模型适配 |
| 连续性、失败诊断、clip/终片质检 | `$continuity-qc` | 状态机、证据、修复与放行标准 |
| 剪辑、口型、对白、Foley、音乐、混音 | `$editing-sound` | 时间线、声音层次与声音母版技术验收 |

跨阶段请求不需要逐个显式调用技能名；按照下面的顺序执行，并在每次交接中保留稳定 ID、版本和审查状态。

## Canonical 流水线

```text
source / brief
  → adaptation matrix（有源材料时）
  → story and scene map
  → director intent
  → visual bible + reference manifest
  → scene-to-clip plan
  → provider-neutral prompt IR
  → model adapter prompt pack
  → generation log + single-variable iteration
  → continuity and clip QC
  → edit and sound plan
  → final film QC
```

先读 `references/routing.md`。执行完整生产任务时再读 `references/orchestration-guardrails.md`；它保留原独立技能的全部 canonical artifact、阶段冻结、审查、参考传输和验证规则。不要在普通问答中一次性加载整个证据库。

## 最小 intake

信息不足时列明假设并继续。只有会显著改变故事、权利或生成路径的选择才阻塞。

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

权利未知时可以做结构与诊断，但必须保留 `rights_status: unknown`，不得标为可发行成片。

## 阶段门

人工只创作并冻结这些上游真源：

```text
ai_video_brief.yaml
adaptation_matrix.csv
story_and_scene_map.yaml
director_intent.yaml
visual_bible.yaml
reference_asset_manifest.yaml
semantic_reviews.yaml
```

派生文件由编译器生成。不要人工平行维护 `clip_plan.csv`、`generation_prompt_pack.json`、`continuity_state.json` 等第二套真源。

```bash
python scripts/init_ai_video_package.py <empty-output-directory> --with-adaptation
python scripts/validate_adaptation_stage.py <package-directory>
python scripts/validate_story_stage.py <package-directory>
python scripts/validate_director_stage.py <package-directory>
python scripts/compile_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
python scripts/validate_ai_video_package.py <package-directory> \
  --adapters references/model-adapters.json
```

没有源材料时省略 adaptation matrix 和 adaptation gate，并使用不带 `--with-adaptation` 的初始化命令。运行命令前将路径解析为本技能目录中的绝对路径；不要假设用户项目的当前目录就是 skill root。

## 独立审查

创作者不能为同一阶段自行放行。审查者只接收授权来源和当前阶段 artifacts，不接收旧改编、期望答案或后续文件。任何上游输入变化都会使旧 hash 审查失效。

审查至少确认：

- 来源事实、人物选择、诱因、结果和时间关系是否被正确保留或明确批准修改；
- 每个必要信息是否有可拍、可听或后期可控的 `information_carrier`；
- 人物、地点、道具、状态版本与 clip 的可见/叙事 ID 是否一致；
- prompt 是否真正可发送给所选 adapter，必需参考是否实际附加；
- continuity、generation log、QC、edit timeline 是否逐 clip 对齐；
- `diagnostic_preview` 是否被诚实标记，未生成结果是否保持空 `actual`。

## 交付原则

- 剧本文字必须能被拍到或听到；“她看见结婚三年的丈夫”这类不可见关系说明要转换为行为、道具、对白、声音或剪辑信息链。
- 结构模型只生成假设，不替代因果、主题压力和人物选择判断。
- 一个 clip 默认只承担一个叙事目的、一个主要动作、一次注意力变化和一个主要相机行为。
- 模型能力只认当前官方证据；厂商演示不等于成功率，创作者偏好不等于排名。
- 必需参考未作为真实图像输入附加时，只能输出诊断预览。
- 先验收叙事与身份状态，再验时间空间、可剪性和表面质量。
- 连续两轮同类阻塞失败时，拆 clip、换模式/adapter，或回退上游，不做无界重抽。

## 验证

变更共享资源后运行：

```bash
python tests/run_all.py
```

只有确定性验证、独立语义审查和真实输出证据均满足时，才可把项目标记为最终放行。
