# AI Video Failure Repair：先分类，再单变量修复

## 目录

- [诊断顺序](#诊断顺序)
- [失败分类与修复](#失败分类与修复)
- [单变量循环](#单变量循环)
- [换策略条件](#换策略条件)
- [验收](#验收)

## 诊断顺序

对失败输出按以下顺序判断：

1. **beat failure**：是否完成叙事目的、动作和状态变化？
2. **identity/state failure**：人物、服装、道具和地点是否正确？
3. **temporal/spatial failure**：动作顺序、几何、方向和相机是否稳定？
4. **editability failure**：是否有可用区间、剪点和 handles？
5. **surface failure**：局部纹理、闪烁、文字、手指或背景瑕疵。

上层失败不能用下层美化掩盖。角色没有拿到钥匙时，肤质更真实没有意义。

## 失败分类与修复

| 类型 | 可观察信号 | 首选修复 | 不要先做 |
| --- | --- | --- | --- |
| identity drift | 脸、发型、体型或年龄改变 | 使用正确角色 reference/首帧；减少同时可见角色；固定服装版本 | 重复“same person”十次 |
| wardrobe/prop drift | 颜色、数量、持有人或损伤状态变化 | 独立道具/服装参考；拆交接；用 insert 锁状态 | 接受错误并让后续跟着错 |
| anatomy/geometry | 手、肢体、门窗、车辆或接触关系变形 | 缩短动作；减少接触；遮挡关键接触；拆 before/after clips | 同时增加更多动作细节 |
| temporal flicker | 纹理、背景、光线逐帧跳动 | 缩短 clip；减少动态背景；I2V/视频编辑；提前切出 | 用剪辑插值证明内容正确 |
| action ambiguity | 动作未开始/未完成/顺序错误 | 写 start→one action→end；降低速度/距离；拆 clip | 增加多个同义动作动词 |
| multi-character confusion | 身份交换、动作归属错、眼神混乱 | 每 clip 聚焦一个施动者；用 reaction/OTS 分离；固定静态参照 | 要求三人同时精确交互 |
| camera conflict | 锁定与平移/环绕冲突，主体漂出 | 只留一个 camera behavior；用演员运动替代相机运动 | 同时写 cinematic dynamic movement |
| screen-direction break | 人物运动或视线无意翻转 | 重做首帧/构图；水平翻转需检查文字和光向；加中性建立镜头 | 仅靠对白解释位置 |
| text failure | 标牌、屏幕、信件文字乱码 | 单独制作合规文字资产并合成；只让模型生成无可读纹理 | 在视频模型中要求长段精确文字 |
| lip-sync failure | 音素、说话人、时长或表情不匹配 | 缩短台词；先锁音频/表演；单人近景 lip-sync；用反应/画外音 | 让多人长段同时说话 |
| style/light drift | 色彩、材质、光向或时间跳变 | 回到 visual bible 和 scene master；用同 reference/首帧 | 用“cinematic”覆盖具体规则 |
| editability failure | 开头/结尾正在变形，无稳定 handles | 请求额外 handles；提前切出；生成 insert/reaction/cutaway | 强行使用完整生成时长 |

## 单变量循环

```yaml
baseline_run_id: RUN-C05-01
primary_failure: action_ambiguity
evidence: "角色在 2.1 秒松开钥匙，未递给对方"
hypothesis: "递交、转身和相机推近同时发生，超过动作承载"
primary_variable: action_scope
change: "删除转身；相机保持静止；只保留递钥匙"
kept_constant:
  - model/version
  - reference assets and hashes
  - duration
  - framing
  - lighting
acceptance: "钥匙从 A 手进入 B 手，0.5 秒内不消失或复制"
```

每轮：

1. 保留失败证据帧与时间码。
2. 写一个可被新输出证伪的原因假设。
3. 只改变一个主要变量；必要的语法联动写成同一变量的子项。
4. 生成后先检查 acceptance，再看次要美学。
5. 记录结果为 `improved`, `unchanged`, `regressed`, `inconclusive`。

## 换策略条件

- 连续两次单变量修改仍出现同类 blocking failure：换生成方法或拆 clip。
- 角色/地点参考未实际附加：停止生成，补 reference transport。
- 模型 adapter 明确不支持所需控制：选择另一 adapter 或重写 clip，不伪造参数。
- 达到 `iteration_limit` 或 credit ceiling：选择最可剪版本、设计补救或回退上游。
- 修复会改变 beat、人物选择或因果：回到 writer/director，不让 adapter 偷改故事。

## 验收

- 每个 rejected clip 有一个主失败类型、证据帧、影响和下一动作。
- 每个非 exploratory 迭代只有一个 `primary_variable`。
- 每个 accepted clip 的叙事目的和连续性先通过，再评价表面质量。
- 换策略有明确触发，不进行无界重抽。
- 修复结果回写 generation log、clip QC、continuity state 和 edit plan。
