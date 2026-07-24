# Writer：从意图到可视、可生成的剧本

> 实战入口：诊断/重写单场戏时优先读取 `distilled-scene-procedures.json`，运行 `scene-four-function-gate`、`scene-conflict-escalation-grid` 和 `scene-turning-point-four-effects`。人物、对白和第二稿问题再读取 `distilled-sorkin-procedures.json`；剧集发动机、pilot 信息预算、三集循环和群像账本读取 `distilled-targeted-foundation-procedures.json`。不要用“更有冲突”“更有张力”代替逐回合策略与状态变化。

## 目录

- [核心原则](#核心原则)
- [故事与人物](#故事与人物)
- [场景与序列](#场景与序列)
- [对白与潜台词](#对白与潜台词)
- [结构模型的使用方式](#结构模型的使用方式)
- [练习与核验](#练习与核验)

## 核心原则

剧本不是小说的缩水版，也不是视频模型提示词。每一页都应让读者看见一个可执行的行为、关系变化或信息变化；AI 生产阶段再把这些功能拆成 clip contract。用以下因果链检查：

```text
欲望/问题 → 阻碍 → 策略 → 代价 → 选择 → 新状态
```

如果一个“转折”只改变观众知道的解释，而没有改变角色能做什么，通常还不够强。主题不是口号，而是角色在压力下反复做出的选择及其代价。

## 故事与人物

### 一页故事地图

```yaml
logline: "主角 + 目标 + 主要阻碍 + 独特代价"
dramatic_question: "观众想知道什么会如何发生？"
theme_question: "在何种选择之间拉扯？"
protagonist:
  want: "外在可拍目标"
  need: "内在误解或必须学会的能力"
  fear: "会阻止行动的具体恐惧"
  contradiction: "让人物不被标签化的矛盾"
antagonistic_force: "主动施压的人/制度/环境/自我"
stakes: ["物质", "关系", "身份"]
ending_choice: "主角最终主动选择什么"
```

人物弧不是“变好”，而是原有策略在更高压力下失效，人物选择继续坚持、改变或付出另一种代价。反派的功能是具体化阻力和价值冲突，不是只增加残酷程度；用“他认为自己在保护什么”检验其主体性。

人物介绍依赖履历或标签时，运行 `intention-obstacle-tactic-character`：给人物一个当下可成败的 `want_now`、真正能阻止它的障碍和至少三种不同策略；让策略的代价排序显示人物，而不是由旁白宣布性格。

### 角色压力测试

对每个主要角色问：

1. 他现在想立刻得到什么？别人如何阻止？
2. 他能做的最有效策略是什么，为什么还没用？
3. 哪个事实会让他的自我叙事崩塌？
4. 他在公开场合和私下场合分别如何表现？
5. 如果删掉这个角色，哪条因果链断掉？若没有断点，角色可能只是装饰。

## 场景与序列

场景卡先写功能，再写台词：

```yaml
scene_id: S04
location_time: "洗衣店 / 凌晨"
entry_state: "两人都以为对方已经同意分手"
objective: "Mira 拿回钥匙并保持体面"
obstacle: "钥匙在对方口袋；对方开始谈未来"
strategy: "把取钥匙伪装成整理衣服"
turn: "洗衣机停下，掉出一张不属于她的票根"
exit_state: "Mira 留下票根，放弃取钥匙"
visible_actions: ["折衣服", "避开目光", "把票根压进袖口"]
sound_or_visual_motif: "滚筒噪声在说真话时停止"
```

检查场景是否有“进入前不可能、离开后必须处理”的变化。开场尽快建立正在发生的行动；结尾留下新问题、代价或方向，而不是用总结台词解释意义。

### 冲突疲软场景的重写程序

先保留原稿作为 `baseline`，再建立以下诊断，不直接润色台词：

```yaml
scene_id: S04
baseline_problem: "双方重复立场，四页内策略和代价均不变"
character_A:
  want_now: "让 Kai 签担保"
  cannot_say: "公司已无现金"
  walk_away_point: "Kai 要求看账户"
character_B:
  want_now: "拒绝签字但保住友谊"
  cannot_say: "准备退出公司"
  walk_away_point: "Lin 用旧恩情施压"
clock: "银行 17:00 截止"
shared_resource: "唯一一份合同原件"
```

随后填写至少三轮升级网格：

| beat | A tactic | B visible response | B counter | immediate cost | state change |
| --- | --- | --- | --- | --- | --- |
| B01 | 请求帮助 | 把笔扣上 | 展示已签离岗单 | 时间减少 | Lin 失去道德优势 |
| B02 | 承诺调休 | 翻出旧承诺邮件 | 要求先看账户 | 秘密受威胁 | 话题从友情变证据 |
| B03 | 锁系统权限 | 把事故邮件抄送全组 | 公开要求 Lin 负责 | Lin 失去私控 | Bo 留下但权力反转 |

重写顺序：

1. 先只写 beat 的动作与反应，不写完整对白。
2. 每轮必须因上一轮失败而换一个可命名策略动词；换措辞不算换策略。
3. 第二或第三轮触及开场已建立的边界，并让关系、资源或身份成本升级。
4. 最后写对白；每句至少服务当前策略、隐藏信息或改变关系一项。
5. 输出 before/after diff，明确保留了原稿哪些事实与台词；不静默改设定。

通过条件：至少三轮不同策略；每轮有对方可观察反应；第三轮代价高于第一轮；退出状态至少两项不同；场景结尾不能靠一句道歉复位。若任一条件失败，退回 beat 网格，不继续修辞润色。

### 序列与结构

把连续场景按一个短期目标分成 sequence。每个 sequence 有承诺、升级、反转和暂时结果。长篇结构可以使用三幕、五幕、序列法、故事圈或节拍表生成草案，但最终以因果和情绪曲线验收：

需要构建长篇中段时，运行 `distilled-structure-sound-procedures.json` 的 `sequence-local-problem-chain` 与 `tension-release-contrast-map`；系列项目再运行 `series-carrying-capacity-test`、`pilot-character-information-budget`、`three-episode-block-cast-attendance` 和 `foreshadow-culture-causality-ledger`。不得把八序列、三集 block、集数或分钟数当固定模板。

- 观众何时获得信息，何时被迫重新解释？
- 主角的策略是否越来越昂贵？
- 关键转折是否来自角色选择，而非作者方便地降临？
- 中点后，问题是否从“能否得到”变成“得到后付什么代价”？

### AI 视频时长与 clip 前置检查

Writer 不决定具体模型或 prompt，但必须把故事写成可分配时长的可见事件：

1. 为每场写 `runtime_budget_sec`，用朗读、动作计时或 animatic 估计；无依据时保持区间。
2. 每个 beat 写 `visible_start / trigger / visible_end`，避免只写内心结论。
3. 标出多人、口型、复杂手部、道具交接、变形和精确文字等生成高风险点；不要在剧本阶段假装已解决。
4. 对关键信息指定首选载体：动作、构图、道具、声音、对白或剪辑；至少给一个不依赖精确口型的 fallback。
5. Writer 输出到此冻结；clip 原子化、参考资产和模型选择属于后续阶段。

```yaml
beat_id: S04-B03
runtime_budget_sec: 6
visible_start: "盘子压住信封，只露出一角"
trigger: "父亲端走盘子"
visible_end: "信封完整暴露，Mira 停止说话"
preferred_carrier: "prop reveal + reaction"
generation_risks: [two_characters, hand_prop_contact]
fallback: "letter insert + Mira reaction + off-screen plate sound"
```

## 对白与潜台词

对白的可见任务可以是获取、拒绝、试探、拖延、操控、确认或掩饰；同一行台词应尽量同时改变关系或信息。写完一轮后删去礼貌寒暄和重复信息，用动作、停顿、抢话和答非所问承载潜台词。

```text
意图：Lin 想让同事离开
障碍：她不能直接命令对方
表层对白：“你不是还有一份报告要交吗？”
潜台词：我怕你继续问
可见行为：她一边说一边把抽屉推回去
```

避免让角色轮流替作者讲主题；若必须解释背景，让信息带着代价（说错会失去信任、时间或机会）。台词节奏要服务人物差异：词汇、句长、比喻、沉默和打断比口头禅更有辨识度。

对白纸面成立但演员说不出时，运行 `dialogue-rhythm-speakability-pass`：作者出声计时、标句功能与拍点、演员盲读三次，再对原版/修订版做音频 A/B。关键因果信息至少让 4/5 听众复述正确，不能把“更快”当作“更好”。

完整初稿的结尾改变了影片真正的问题时，运行 `second-draft-discovered-movie`：先写结尾选择与代价，再审计前三场的承诺、缺失 setup 和错误承诺；从 scene map 重建第二稿，不把修台词冒充结构重写。

## 结构模型的使用方式

将模型当作“诊断镜头”，不当作法律：

- **故事圈**：从舒适状态进入未知、适应、得到、付出代价并回到新状态；适合检查角色欲望的循环。
- **三幕/五幕**：检查承诺、升级和后果是否分布合理；不规定每个百分比或页码。
- **序列法**：把长篇拆成若干有局部目标和反转的短旅程，适合剧集和改编压缩。
- **节拍表**：把抽象情绪变成待验证的节点；若某一拍无法写成行为或选择，就退回重定义。

使用任何模型后都做一次反模型检查：找出作品有意延迟、重复、跳跃或留白的地方，并说明它如何替代模型提供张力。

主题/母题不能停在“颜色代表情绪”。需要具体设计时，运行 `distilled-theme-performance-camera-procedures.json` 的 `motif-meaning-trajectory`；两条母题在高潮汇聚时运行 `motif-theme-convergence`。

## 练习与核验

优先看视频/访谈后做“看得见的作业”，而不是只抄概念：

1. 选一个 2–4 分钟片段，列出 setup → turn → payoff，并给每项写时间码。
2. 把同一场戏改成无对白版本，只保留行动、空间和声音线索，再检查是否能拆成原子 clips。
3. 写同一角色的三次失败策略，每次代价更高；最后一次必须改变关系。
4. 用两种结构模型重排同一组场景，比较信息释放和人物主动性，而不是比较“是否符合模板”。
5. 对照公开剧本和成片：标出删改、合并、镜头补充，并把原因写成假设。

来源线索（具体状态见各 `*-knowledge-base.json`）：只有 `deep_distilled` 可直接提供视频驱动程序；`claim_evidence_only` 只能提供带时间码的观点，`chapter_hypothesis_only` 与 `candidate` 只能进入待研究队列。当前 StudioBinder 场景程序见 `distilled-scene-procedures.json`，Aaron Sorkin 访谈剪辑的四个程序见 `distilled-sorkin-procedures.json`，WGF/Tony Gilroy 长篇程序见 `distilled-targeted-foundation-procedures.json`；频道名称不能替代字幕、时间码和程序字段核验。
