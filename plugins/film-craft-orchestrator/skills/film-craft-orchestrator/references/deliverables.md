# 交付 schema 与验收清单

## 目录

- [通用元数据](#通用元数据)
- [Writer 产物](#writer-产物)
- [Director 产物](#director-产物)
- [Cinematographer 产物](#cinematographer-产物)
- [改编与证据产物](#改编与证据产物)
- [验收清单](#验收清单)

## 通用元数据

每个文件开头包含统一头部（见 `routing.md`）。ID 必须稳定、版本递增、引用输入明确。Markdown 适合人读，JSON/YAML 适合脚本，CSV 适合片场表格；不要让同一事实在多个文件里出现不同版本。

## Writer 产物

### Beat/sequence map

```yaml
beat_id: B07
sequence_id: Q02
dramatic_function: "迫使主角公开承认失败"
setup: "同事以为她已经修好系统"
want: "保住信任"
obstacle: "日志显示她删过文件"
choice: "她先承认删文件，再提出补救"
turn: "补救需要牺牲朋友的排班"
payoff_or_question: "她是否愿意承受关系代价？"
```

### Scene card

至少字段：`scene_id`, `location_time`, `entry_state`, `objective`, `obstacle`, `tactics`, `turn`, `exit_state`, `visible_actions`, `sound_or_visual_motif`, `continuity_notes`。

冲突重写另交 `conflict_rewrite.yaml`：双方 `want_now/cannot_say/walk_away_point`、时间压力、共享资源和至少三轮 `tactic → visible response → counter → cost → state change`。只有台词修辞、没有逐轮策略变化的重写不得标完成。

### Screenplay draft

遵循目标行业格式，但若用户未指定格式，先用清晰 Markdown：场次标题、动作、角色名、对白、必要 parenthetical。不要在动作段写不可拍的内心解释；把心理改成行为或声音线索。

## Director 产物

至少字段：`audience_knowledge`, `attention_path`, `performance_beats`, `blocking`, `coverage_strategy`, `sound_intent`, `edit_triggers`, `non_negotiables`, `fallbacks`。导演阐述要写可感知规则、关键例外、制作约束，而不是只写形容词。

权力/情绪反转另交 `blocking_plan.yaml`：米制空间坐标、起始位置、每拍 before/trigger/after、视线、距离、节奏拍数、轴线、摄影交接、灯光/声音影响、安全同意和 fallback。无台词盲测至少 4/5 观察者在目标 beat±1 识别反转。

## Cinematographer 产物

Shot list 每行包含：

```text
shot_id,scene_id,beat_id,setup_id,shoot_order,priority,purpose,
framing,lens_or_fov,camera_format,fps_shutter,camera_position,movement,
axis,focus_target,focus_limits,exposure_limits,light,sound,audio_track_mic,
edit_trigger,screen_duration_est,screen_duration_contribution_est,shoot_time_est,
schedule_start,schedule_end,page_fraction,cast_props,location,gear_crew_status,
slate,continuity_risk,safety_note,fallback,status
```

灯位图/色彩笔记必须能回到 scene/beat；曝光、焦点、反射、窗光、声音和安全风险写成可检查条件。不得把昂贵器材当成必要条件。

## 改编与证据产物

改编矩阵字段：`source_unit`, `source_function`, `screen_unit`, `invent_ids`, `preservation`, `new_visual_or_sound_device`, `character_changes`, `structural_change`, `reason`, `risks`, `verification`, `rights_status`。

进入 writer 前必须再交 `adaptation_handoff.csv`，逐个 `screen_unit` 给 source refs、scene id、预计秒数、进入/退出状态、目标、障碍、转折、屏幕设备、虚构材料和未决事实。总预计时长应在目标 ±10%。

视频证据包字段见 `video-evidence.md`，最小交付包括：来源 URL/标题/频道、访问日期、字幕/ASR 状态、少量带时间码的 claims、证据等级、练习和未核验项。

## 验收清单

### 内容

- [ ] 主角目标、障碍、策略、选择和代价可见。
- [ ] 每场戏进入/退出状态不同，转折来自人物或明确外力。
- [ ] 冲突重写至少三轮不同策略；每轮有可见反应，代价逐轮升级。
- [ ] 导演/摄影选择能回答观众注意力或信息问题。
- [ ] 反转调度带坐标/触发/fallback，并通过无台词识别测试。
- [ ] 每个“必拍”镜头都有 purpose；optional 镜头可删。
- [ ] must beat 覆盖率 100%；入口—转折—退出能由 must 镜头独立剪出。
- [ ] setup 工时合计不超核定工时；器材/岗位未知时不得标 `shootable`。
- [ ] 逐镜最终剪辑贡献之和与 scene/beat 时长一致；逐镜拍摄工时+公共工时与 setup 时间表/声明总工时一致，算式可复核。
- [ ] 改编取舍和视角变化有理由，不隐瞒新立场。
- [ ] 改编总时长在目标 ±10%，must/invent 项均可追溯。
- [ ] invent 验收写明层级、稳定 ID 和分母；下游新增项不会被 handoff 的旧计数掩盖。

### 证据

- [ ] 事实、video claim、interpretation、experiment 分栏。
- [ ] 高风险陈述有来源和时间码；不确定性旗标保留。
- [ ] 只有满足严格程序字段的视频标 `deep_distilled`；章节和元数据不计蒸馏量。
- [ ] 视频只用短引文/摘要，不提供整段转录或整部剧本。
- [ ] 缩略图只标 `thumbnail_only`，不推断剪辑、音频或因果。

### 工程

- [ ] 页数/时长、人物、道具、空间和 shot_id 一致。
- [ ] 所有脚本能在依赖缺失时给出清楚错误，不静默生成假数据。
- [ ] 每个 CSV 都通过 `validate_csv.py` 的严格列数/引号检查；不能以单一宽松解析器能打开作为有效证据。
- [ ] 版本、状态、来源输入和 change_log 完整。
- [ ] 摄影/声音/片场安全边界写明，未把 skill 当法律或安全批准。
