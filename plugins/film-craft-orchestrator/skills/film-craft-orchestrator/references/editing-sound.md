# Editing & Sound：把生成 clips 变成连续的观众经验

> 实战优先读取 `distilled-ai-video-procedures.json` 中的 `foley-ambience-music-layer-build`、`replace-native-audio-continuity`、`separate-dialogue-voice-lipsync`、`two-speaker-lipsync-composite` 和 `edit-rescue-and-stop-gate`。表演优先、持表演后切反应、节奏 A/B 和 coverage 缺口修复见 `distilled-targeted-foundation-procedures.json`。创作者视频只证明其工作流；当前工具的音频/口型能力仍由官方 adapter 决定。

## 目录

- [剪辑先保因果](#剪辑先保因果)
- [连接检查](#连接检查)
- [AI 素材补救](#ai-素材补救)
- [对白与口型](#对白与口型)
- [声音结构](#声音结构)
- [交付验收](#交付验收)

## 剪辑先保因果

每个 cut 必须至少完成一项：重新分配信息、显示选择/反应、改变空间、压缩时间、制造对照、建立/释放期待或隐藏生成错误。不能只因为 clip 已生成就使用它。

先只用 must clips 剪出：场景入口、目标、策略、转折、选择和退出。然后再添加 should/optional 纹理。若 must 因果无法成立，回到 clip plan，不用音乐掩盖。

## 连接检查

逐 cut 检查：

- **动作**：前镜动作方向、速度和接触点能否在后镜继续。
- **视线**：看向画外左的人/物，在反打中应处于相容方向。
- **空间**：门、桌、人物和关键道具的相对位置可理解。
- **信息**：观众何时第一次看见、听见或确认关键内容。
- **表演**：反应开始于台词前、台词中还是台词后；不要默认说话者特写。
- **声音**：room tone、环境、动作和对白是否跨切点连续或有意断裂。
- **生成瑕疵**：cut 是否正好落在身份/几何开始漂移之前。

## AI 素材补救

| 问题 | 剪辑补救 | 限制 |
| --- | --- | --- |
| clip 后半变形 | 提前切出，使用后续 reaction/insert | 不能删掉必要动作结果 |
| 道具交接失败 | before insert → 手部/遮挡 cut → after reaction | 关键因果仍须可理解 |
| 人脸短暂漂移 | 切到倾听者、背影、环境或关键物 | 不能让说话人身份混淆 |
| 空间跳变 | 加中性宽景、方位 insert 或声音提前 | 宽景本身必须连续 |
| 动作不够有力 | 使用更早准备、更短接触、更长结果反应 | 不用重复帧伪造新动作 |
| clips 风格不同 | 先统一曝光/色彩/颗粒，再用声音和节奏连接 | 大幅身份差异不能靠调色修复 |
| 时长不足 | reaction、off-screen sound、环境 cutaway、持有尾帧（审慎） | 不制造僵硬停顿或口型滑动 |

补救是设计选择，不是隐瞒失败。`edit_plan` 必须记录被绕过的错误、使用区间和 fallback。

执行补救前先分级：身份、关键动作、道具状态或因果在最终区间中错误是 `blocking`，必须回炉或改写；只有错误位于可裁边缘且 must beat 仍成立时，才使用提前切出、insert、reaction、cutaway、遮挡、crop 或声音桥。

## 对白与口型

把五层分开：剧本台词、声音表演、人物画面表演、lip-sync、最终剪辑。

1. 先锁台词意图、节奏和音频版本；一段音频有稳定 ID/hash。
2. 长台词按策略或思想转折拆成可表演短句，不按固定字数机械拆分。
3. 一个 clip 默认只有一个可见说话者；多人对话用单人/过肩/反应和画外音组合。
4. lip-sync 后检查音素、说话人、呼吸、眨眼、下颌、头部运动和句尾停顿。
5. 口型失败但表演可用时，优先画外对白、反应镜头或更远景；关键近景台词必须重做。
6. 记录声音/肖像授权或合成身份；不把未经同意的真实声音克隆当默认。

双人口型工具让两人同时说话时，不把宽景直接晋级。对可分区构图可使用 mask/crop＋非说话侧 frame hold，并用近景/反应覆盖边界；角色交叉走位或区域重叠时改生成独立 coverage。

## 声音结构

每场至少规划：

```yaml
dialogue: "角色语言与画外音"
room_tone: "维持地点和切点连续"
ambience: "可被主观增减的环境层"
spot_effects: "动作、道具和信息落点"
foley_or_generated_effects: "脚步、衣物、触碰"
music: "进入/退出条件和不能覆盖的对白"
silence_or_subtraction: "何时移除一层以制造注意力"
```

使用 J-cut/L-cut 时写明它改变的是期待、空间还是情绪。声音桥不能替代缺失的关键可见因果，但可以跨越不完全匹配的环境、提前提示下一空间或延长上一反应。

每个生成 clip 的原音先做 `keep / replace / mute` 审计。合格且有授权的原音可以保留；环境、对白或自动音乐跨 cut 不连续时，删除问题层并从统一 room tone/ambience 开始重建，不教条式清空所有原音。

## 交付验收

- `Σ edit_contribution_sec` 与场景/成片目标对账，handles 不重复计入最终时长。
- must beat 100% 被最终使用范围覆盖。
- 每个 cut 有动作、视线、空间、信息、表演或声音连接理由。
- 使用的 clip in/out 不包含已标 blocking 的连续性或身份错误。
- 对白 ID、音频 hash、说话人、lip-sync 输出和最终时间线一致。
- ambience/room tone 不因 AI clips 切换无意跳变；有意断裂写目的。
- 音乐、效果和对白有独立 stem/来源记录；最终响度标准按目标平台填写，未知时保持 pending。
