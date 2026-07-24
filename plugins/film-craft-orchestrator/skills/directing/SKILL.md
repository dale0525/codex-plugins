---
name: directing
description: 需要导演阐述、场面调度、演员行为、注意力控制、信息隐藏与揭示、多人空间、动作节拍或 coverage 设计时使用。把抽象情绪结果翻译为可执行的表演、blocking、镜头与声音触发；不负责重写上游故事，也不以镜头术语掩盖缺失的戏剧判断。
---

# Directing

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `references/director.md`；多人、动作与视觉叙事任务加读 `references/distilled-foundation-procedures.json`、`references/distilled-targeted-foundation-procedures.json` 和 `references/distilled-directing-editing-procedures.json`。

## 导演转换

不要停留在“紧张、浪漫、压迫、电影感”。对每场明确：

1. 观众进入时知道什么、误解什么，离开时改变了什么；
2. 注意力顺序：先看谁/什么，何时转移，什么被暂时隐藏；
3. 每个人的当前目标、策略、可见行为和策略变化；
4. blocking：起点、距离、朝向、障碍、越界、接触、道具和终点；
5. performance beats：刺激、接收、抑制/反应、决定、行动、余波；
6. coverage：base shot 承担什么，哪些反应、信息或剪点必须单独覆盖；
7. 声音与剪辑触发：何时先听后见、何时留反应、何时切走。

## 约束

- 相机不能提前宣布人物尚未做出的选择。
- 每个 story beat 都要有自己的 performance 与 blocking 记录，不能用一条代表性调度覆盖整场。
- Director scene 只能包含该 story scene 拥有的 beats；禁止把全片 beats 复制到每个场景。
- 多人场景先建立空间轴、视线和权力关系，再设计 coverage。
- 情绪通过任务、阻力、节奏、距离、目光和身体控制产生，不向演员只下“更悲伤”类结果指令。
- coverage 只为 base shot 无法可靠承担的信息、选择、反应或剪点服务。

## 输出合同

输出导演阐述时包含：dramatic question、audience state、attention map、blocking map、performance beats、coverage、sound/edit triggers、continuity entry/exit 和 fallback。进入生产包时写入 `director_intent.yaml`，同时定义 `clip_specs[]` 作为唯一 clip 设计真源。

独立审查通过后，在共享根运行：

```bash
python scripts/validate_director_stage.py <package-directory>
```
