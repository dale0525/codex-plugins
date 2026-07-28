# 人物两难、代价传播与体验余响

本页用于同时处理人物、结构和场景的重大选择。目标不是把故事写得更惨，而是让选择具有角色专属性、代价具有可回溯因果，余波真实进入人物生活。单一人物判断仍以 `characters.md` 为准，事件因果以 `structure.md` 为准，具体场景以 `scene-prose-craft.md` 为准。

## 总原则

强项负责结构完整，弱项负责体验真实。每个重大选择、回收或不可逆状态变化都要有至少一种人物可感知的体验证据；普通推进节点不机械配置身体症状、创伤或象征。

```text
结构变化：人物选择 -> 因果结果 -> 不可逆状态 -> 下一压力
体验证明：日常刺激 -> 改变后的注意/解释 -> 可见行动 -> 关系或任务偏移
```

删除结构变化后，体验细节不能只剩气氛装饰；删除体验证明后，读者应明显感觉代价只存在于大纲。两者相互证明，不相互替代。

## 两难与选择

真正的两难要求两项义务、价值、身份或关系都具有正当要求，当前时间、资源、信息、权限或承诺使其无法同时满足，并由人物亲自承担选择。拒绝可以存在，但必须有自身后果；第三方案可以存在，但不能免费保全一切。

记录人物的工作认知：他相信什么能够保护人、维持秩序、证明价值或避免旧事重演。压力可以证明该认知错误、残缺、过时、局部有效或缺少伦理维度。不要强迫所有两难共享一个错误信念；现实本身也可能包含不可调和的正当责任。

选择可能来自权衡、习惯、训练反射、恐惧、羞耻、爱、拒绝或被时间截断的判断。选择方式决定场景表现，但行动仍要暴露人物的真实优先级和责任。事后可以误解、自辩、追问或沉默，不必用完整心理诊断替人物解释一切。

## 优势阴影卡

美德不是天然的缺陷。核心优势只有在越出适用范围、强度过量或缺乏制衡价值时，才形成破坏性阴影。

```yaml
strength_shadow:
  core_strength: ""
  past_success_evidence: ""
  overextension_condition: ""
  ignored_countervalue: ""
  blind_spot: ""
  immediate_gain: ""
  collateral_harm: ""
  recognition_or_denial: ""
  future_test: ""
```

危机中人物倾向于使用过去确实有效的核心优势。该优势应取得真实成果，同时因为越界、过量或缺乏制衡制造伤害。不要要求它自动导致最糟结果，也不要用“任何极致美德都是偏执”否定人物品质。

## 结构—体验配对卡

```yaml
major_story_node:
  structural_change:
    triggering_choice: ""
    causal_result: ""
    irreversible_state_change: ""
    next_pressure: ""

  experiential_proof:
    affected_person: ""
    changed_perception_or_behavior: ""
    ordinary_trigger: ""
    visible_trace: ""
    relationship_interpretation: ""
    echo_window: ""
```

验收：体验证据必须源于同一代价，出现在合理时间窗，并至少轻微改变注意、行动、信息或关系。重大节点最好同时改变外部状态和内部体验，但不要求普通节点逐一配对。

## 延迟代价生命周期

只对适合延迟生长的重要代价使用完整生命周期：

```text
压制 containment
-> 潜伏 latency
-> 溢出 spillover
-> 放大 amplification
```

1. **压制**：人物通过局部合理的谎言、资源消耗、让步、应急措施或过度补偿暂时控制问题，得到假胜利，但根因仍在。不要强迫人物为了推动剧情犯明显愚蠢的新错误。
2. **潜伏**：代价储存在信任、伤势、债务、证据、情报暴露、制度先例、错失承诺或行为习惯等具体载体中。至少出现一个可观察但能够被合理误读的信号。
3. **溢出**：代价经由明确因果桥梁从原领域侵入另一段关系、资源、身份或系统。不要用“事情开始影响一切”代替桥梁。
4. **放大**：后来本可处理的危机遇到积累缺口，人物失去原本可用的方案，问题性质发生变化。不要只增加规模或伤亡数字。

每个后续行动在当时都应具有局部合理性。最后阶段不能临时增加决定性规则、人物或证据。即时结清或不适合潜伏的代价不套四阶段。

### 延迟代价约束卡

```yaml
cost_lifecycle:
  source_choice: ""
  original_cost: ""

  containment:
    action: ""
    local_reason: ""
    temporary_gain: ""
    root_cause_left_intact: ""

  latency:
    carrier: ""
    accumulating_change: ""
    observable_but_ambiguous_signal: ""
    why_protagonist_misreads_it: ""

  spillover:
    source_domain: ""
    affected_domain: ""
    causal_bridge: ""
    lost_option_or_changed_relationship: ""

  amplification:
    later_crisis: ""
    old_cost_as_multiplier: ""
    unavailable_solution: ""
    irreversible_result: ""
    next_pressure: ""
```

### 延迟代价 Prompt

```text
请为以下重大选择设计一条潜伏代价链，暂时不要写正文。

输入：重大选择、人物当时目标、惯用策略、直接所得、直接代价、既有规则、相关人物的欲望与知识、后续必须保留的方向。

依次输出：
1. 压制：局部合理的控制行动、短期收益、未被消除的根因；
2. 潜伏：具体载体、累积变化、可观察但可误读的信号、人物误判理由；
3. 溢出：源领域、受影响领域、具体因果桥梁、失去的选项或改变的关系；
4. 放大：后来危机、旧代价的乘数作用、失效的原有方案、不可逆结果和下一压力。

硬约束：
- 每阶段继承上一阶段的具体载体；
- 后续行动在当时具有局部合理性；
- 至少提前出现一个可观察但可误读的信号；
- 不在最后阶段新增关键规则、人物或证据；
- 放大必须改变问题性质或可用选项，不只扩大伤亡；
- 若代价不适合潜伏，明确说明，不强行套用四阶段。
```

## 日常中的无声余响

重大代价可通过六类通道进入日常：身体/感知、注意/解释、物件/空间、语言/沉默、关系距离、日常仪式。选择最相关的两三种即可，不要求全部使用，也不强迫每项产生身体症状。

```text
相同或相似的普通刺激
-> 人物产生不同的注意或解释
-> 做出可见的微小行动
-> 当下任务、信息或关系轻微偏移
```

余波场景仍有当前目标，不能只展示创伤。身体反应必须对应具体经历；不要默认发抖、噩梦、失眠、饮酒或照镜子。重复出现的余响必须改变触发、意义、应对或他人理解。不要为证明代价新增原事件无法支持的创伤。

### 微观摩擦卡

```yaml
aftermath_residue:
  source_cost: ""
  affected_character: ""
  previously_normal_experience: ""
  permanently_changed_meaning: ""

  traces:
    - channel: body|attention|object|language|relationship|routine
      ordinary_trigger: ""
      old_response: ""
      new_response: ""
      visible_behavior: ""
      what_the_character_avoids_explaining: ""
      how_another_person_might_misread_it: ""

  recurrence:
    first_appearance: ""
    later_variation: ""
    eventual_transformation_or_non_recovery: ""
```

### 日常余响 Prompt

```text
请设计一个安静的余波场景，证明上一事件的代价已经进入人物的日常体验。

输入：上一事件、人物选择、外部损失、不愿面对的认识、当前日常场景、人物本场现实目标、在场人物及关系。

从身体/感知、注意/解释、物件/空间、语言/沉默、关系距离、日常仪式中选择最相关的两至三项。每项说明普通触发、过去反应、现在的微小阻力、可见行为、人物的掩饰、他人的可能误读，以及它如何轻微改变本场行动、信息或关系。

硬约束：
- 场景必须有当下目标，不能只是展示创伤；
- 不直接宣布人物留下心理阴影；
- 不默认使用发抖、噩梦、失眠、饮酒或照镜子；
- 身体反应必须由具体经历支持；
- 不强制固定数量，以足够让读者感知变化为准；
- 至少一处余响通过行动或关系被他人察觉；
- 重复余响必须发生变化；
- 不新增原事件无法支持的创伤。
```

## 综合审计

对重大两难与代价依次检查：

1. 两项要求是否都具有正当性，且当前约束确实阻止同时满足？
2. 换一个人物后选择是否会变化；策略是否来自其经历、认知和既有优势？
3. 优势是否取得真实成果，并只在越界、过量或缺乏制衡时形成阴影？
4. 意外后果是否来自既有规则、知识差和自主行动者，事后能够回溯？
5. 延迟代价是否有具体载体、可误读信号、跨域桥梁和失去的解决选项？
6. 重大结构变化是否拥有至少一种非机械的体验证明？
7. 余响是否改变当下注意、行动、信息或关系，而非只制造气氛？
8. 后续是否再也不能完全回到选择前的状态？
