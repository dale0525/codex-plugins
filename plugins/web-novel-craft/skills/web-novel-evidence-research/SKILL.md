---
name: web-novel-evidence-research
description: 仅当用户明确要求从视频或访谈中蒸馏可用于网络小说或连载小说的方法，并需要时间码、证据等级、适用边界或可验收程序时使用。普通转录、摘要、内容问答、一般文学/学术/广告写作研究不得调用；不把标题、声誉、片段或未看的候选视频当结论。
---

# Web Novel Evidence Research

## 共享知识

共享根是 `../web-novel-craft/`。开始前完整阅读 `../web-novel-craft/references/evidence-policy.md`。现有核心语料见 `../web-novel-craft/references/video-corpus-manifest.json`、`../web-novel-craft/references/video-asr-evidence.json`、`../web-novel-craft/references/video-knowledge-base.json` 和 `../web-novel-craft/references/distilled-procedures.json`。

## 工作流

1. 先冻结入选/排除标准和必做清单，避免根据取得难度偷偷换片。
2. 优先官方字幕；无完整字幕时下载授权音频并以 `../web-novel-craft/scripts/transcribe_media.py` 本地 ASR。
3. 记录来源、日期、时长、字幕类型、工具/模型、hash、首末时间与缺口。
4. 从第一段读到最后一段，建立时间序论证地图；关键词搜索不能代替通读。
5. 对主张记录时间码、类型、E1–E4、短摘录/释义、适用与失败边界。
6. 把建议转成 trigger→inputs→steps(action+check)→output→failure→repair→acceptance→example→counterexample。
7. 视觉未审查时只声称说话内容；平台/商业/法律说法必须另查当前权威来源。
8. 原始字幕留在用户指定或明确临时的非分发工作区；插件只保存最小短摘录、释义、时间码与程序，不删除用户输入。

## 完成条件

只有完整覆盖、全量阅读、证据台账、边界、反例和至少一个可验收程序都存在，单条视频才能标为 `deep_distilled`。在仓库根使用 `pixi run python plugins/web-novel-craft/skills/web-novel-craft/scripts/validate_corpus.py`；任一冻结视频缺失即失败。
