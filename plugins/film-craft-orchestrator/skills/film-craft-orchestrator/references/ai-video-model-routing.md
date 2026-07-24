# AI Video Model Routing：稳定规格与易变 adapter 分离

## 目录

- [路由原则](#路由原则)
- [能力需求](#能力需求)
- [适配器选择](#适配器选择)
- [编译前探测](#编译前探测)
- [弃用和回退](#弃用和回退)
- [来源纪律](#来源纪律)

## 路由原则

先写 provider-neutral `clip_plan` 与 prompt IR，再读取 `model-adapters.json` 和 `ai-video-official-evidence.json`。不得为了迁就某模型而让 adapter 偷改人物选择、beat、空间规则或导演意图。

模型路由回答“需要什么控制”，不回答“哪个模型最好”。质量随版本、输入、地区和服务变化；一次演示不能证明普遍优越。

## 能力需求

```yaml
clip_id: C05
required:
  mode: image_first_frame
  duration_sec: 5
  aspect_ratio: "16:9"
  character_assets: 2
  native_audio: false
preferred:
  first_and_last_frame: true
  extend: true
  reproducible_seed: false
forbidden:
  - "center crop that removes the key prop"
reference_inputs_expected:
  - asset_id: FRAME-C05-START
    role: first_frame
```

`required` 不满足即排除；`preferred` 只用于排序；`unknown` 按不支持处理，不能凭经验补值。

## 适配器选择

1. 读取 adapter `status`、`last_verified_at` 和 `deprecation`。
2. 核对生成模式、参考输入类型/数量、时长、画幅/分辨率、编辑/延长、音频和任务状态。
   - 每个 adapter 的 `official_evidence_refs` 必须能双向回到当前官方页面；只有视频元数据的来源不得提供参数。
3. 核对访问条件：地区、账户、订阅、API eligibility 和废弃日期。
4. 若多个 adapter 满足 required，依据控制匹配、已有 reference、可追溯性、成本/等待和回退能力选择；质量只写实验结果，不写永久排名。
5. 冻结 `adapter_id` 与版本，再编译 prompt pack；任何版本变化生成新 pack/hash。

## 编译前探测

调用前形成：

```yaml
adapter_id: luma-ray-2-api-2026-07
verified_at: 2026-07-24
capability_check:
  image_first_frame: pass
  duration_sec: pass_pending_exact_schema
  aspect_ratio: pass
  extend: not_required
reference_check:
  expected: [FRAME-C05-START]
  attached: []
result: diagnostic_preview_only
```

只有 expected references 全部实际附加、能力字段明确、输出合同可满足时，才能标 `production_ready`。

## 弃用和回退

- adapter 宣布弃用时仍可用于当前授权任务，但 prompt pack 必须显示 shutdown 日期和替代路径。
- 供应商页面改变、模型版本未知或能力消失时，将旧 adapter 标 `superseded`，保留历史生成 provenance。
- 回退优先保持 prompt IR 和 reference assets，只替换 adapter；若能力不等价，回到 clip plan 明示折衷。
- 不将供应商响应错误、429、地域限制或账户权限伪装成 prompt 失败。

## 来源纪律

- 参数、限制和 API 字段必须来自官方文档/帮助/API schema，附访问日期。
- 官方演示视频只证明“官方展示过某类结果”，不证明参数、稳定性或成功率。
- 创作者 breakdown 用来研究实际工作流和失败，不覆盖官方能力边界。
- 厂商质量形容词、排行榜和精选成片不能进入 adapter capability。
- 当前 adapter 是 2026-07-24 的快照；使用前重新读取官方来源，过期时保持 `verification_pending`。
