---
name: cinematography
description: 为电影、剧集、短片或 AI 视频设计与审查构图、视角、等效焦段、景深、机位高度、相机运动、灯光、色彩、质感和视觉连续性时使用。输出可执行的视觉圣经与镜头摄影规则；不把风格形容词、摄影师姓名或电影片名当作完整方案。
---

# Cinematography

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `references/cinematography.md`；按需要加载 `references/distilled-visual-cinematography-procedures.json`、`references/distilled-theme-performance-camera-procedures.json`、`references/distilled-foundation-procedures.json` 和 `references/distilled-targeted-foundation-procedures.json`。

## 从叙事意图到图像规则

1. 先写叙事功能和观众注意力，不先选“漂亮镜头”。
2. 建立 world look：画幅、空间压缩、视角距离、对比度、色温、饱和度、黑位、质感与允许的例外。
3. 为角色、地点、道具建立可核对视觉锚点和状态版本。
4. 对每镜定义 subject、视点、景别、等效焦段、机位高度、角度、景深/焦点、运动、光向/光质、时间和构图理由。
5. 写 entry/exit visual state，确保下镜能继承方向、光、位置、服装、伤势和道具状态。
6. 若规则发生变化，必须由故事节点触发，并说明观众应感到的差异。

## 摄影判断

- 焦段不是“更电影感”的按钮；同时说明摄影机距离、透视、背景压缩和空间体验。
- 浅景深只有在注意力选择、主观性或信息隐藏上有作用时才使用。
- 相机运动应由角色、信息或空间关系的变化触发；无触发的漂移会削弱可剪性和生成稳定性。
- 光线至少描述方向、大小/硬软、强弱关系、色彩、动机和跨镜保持项。
- 视觉母题必须有首次出现、变化条件和回收，不是重复摆放同一物件。
- 参考图分为角色、地点、道具、首帧/尾帧、构图探索和风格参考；角色参考不能同时冒充每个镜头的首帧。

## Visual bible

进入生产包时维护 `visual_bible.yaml` 与 `reference_asset_manifest.yaml`。角色年龄、服装、伤势、疤痕或时间状态变化必须建立显式 `state_versions`；地点记录布局、门窗、固定物、方向和尺度；道具记录形状、颜色、尺寸、持有人与状态机。

交付前与导演 intent、prompt IR 和 continuity state 对账。文字路径不是已附加图像，缺少必需参考时只允许诊断预览。
