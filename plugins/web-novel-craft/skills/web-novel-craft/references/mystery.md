# 悬疑线索与公平反转

## 线索账本

```yaml
clues:
  - id: clue-001
    type: physical|emotional|knowledge|testimony|record|absence
    observed_at: chapter-id
    observer: char-id
    source: entity-or-document-id
    observation: what is actually available
    proposition_contribution: what hypothesis it raises or lowers
    confidence: low|medium|high
    cross_checks: []
    alternate_explanations: []
    red_herring: false
    payoff: reveal-id
reveals:
  - id: reveal-001
    motive: A
    connection: B
    conclusion: C
    prior_clue_ids: []
    refutation_test: how competing explanations fail
```

## 程序

1. 从谜底写出动机 A、连接 B 和结论 C，再反向布置读者可见材料。
2. 给每条线索标类型与“对哪个命题贡献什么”，不按每章固定数量撒线索。
3. 物证记录保管链；情绪异常和缺席只提高概率；异常知识追查来源；证词必须与记录、行动或另一证词交叉。
4. 为主要推理跳跃至少准备动机证据与连接证据，并保留合理但可排除的替代解释。
5. 揭晓后逐条回看：同一言行在旧解释与新解释下都自然，且答案没有靠最后临时引入的事实或能力。
6. 做粗结案反驳测试：时间、机会、手段、动机和替代嫌疑人均能被现有证据处理。

## 放行标准

- 关键线索在揭晓前可见，但当时不必显眼。
- 缺席、情绪或身份偏见不能单独定罪。
- 读者在谜底后能说“没猜到，但材料一直在那里”。
- 反转升级因果和人物理解，不撤销已发生的事实。

证据锚点：`GNR0g60m0EI` 00:00:20–00:13:26；`GwxFM0oHhfA` 00:00:00–00:13:27。教学来源支持程序，不证明具体线索类型穷尽所有悬疑写法。
