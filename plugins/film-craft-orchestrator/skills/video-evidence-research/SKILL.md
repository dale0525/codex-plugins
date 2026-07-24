---
name: video-evidence-research
description: 从 YouTube 或其他影视教学、访谈、拉片、剧本页—成片对读和 AI 视频教程中收集字幕、转录、关键画面、官方资料并蒸馏可执行知识时使用。要求时间码、画面边界、来源等级和可复核程序；不把视频标题、频道声誉、厂商演示或未查看的候选视频当成已蒸馏知识。
---

# Video Evidence Research

## 共享知识

共享根是 `../film-craft-orchestrator/`。先读 `references/video-evidence.md`。按任务选择知识库与证据包；剧本页—成片对读读取 `references/distilled-script-screen-procedures.json`、`references/script-screen-video-knowledge-base.json`、`references/script-screen-asr-evidence.json` 和 `references/script-screen-frame-evidence.json`。

## 证据等级

区分并明确标记：

- 官方模型文档、参数和弃用公告；
- 视频创作者的明确陈述；
- 字幕/ASR 转录；
- 实际画面、剧本页、时间线和音轨证据；
- 跨来源归纳出的程序；
- 尚未验证的候选视频或假设。

频道声誉只能帮助选样，不能代替看片。厂商演示只能证明展示过某结果，不能证明稳定成功率。

## 获取与分析

优先获取官方字幕；缺失时下载音频并转录，可使用 yt-dlp、Moonshine 或其他本地 ASR。需要视觉结论时下载视频或按时间抽帧；字幕不能证明构图、blocking、剪辑点、表演或光线。

共享脚本提供：

```bash
python scripts/collect_youtube_evidence.py metadata <url-or-id>
python scripts/collect_youtube_evidence.py captions <url-or-id>
python scripts/collect_youtube_evidence.py download <url-or-id>
python scripts/collect_youtube_evidence.py frames <video-path>
```

这些子命令可能需要 yt-dlp、ffmpeg/ffprobe 或 youtube-transcript-api；先检查本地工具，不在系统范围擅自安装。

## 蒸馏格式

每条知识至少保存：source/video ID、标题、频道、发布日期/访问日期、时间码范围、逐字 claim、ASR 置信与歧义、需要的画面证据、适用条件、失败边界、操作步骤、验收标准和反例。

把“原则”蒸馏成可执行程序：

```text
触发条件 → 观察/输入 → 决策步骤 → 产物字段
→ 可判定验收 → 常见失败 → 回退动作 → 来源时间码
```

如果字幕与画面冲突，以实际画面界定视频做了什么，同时保留说法与结果的差异。只有完成转录/字幕核对、必要画面检查和来源记录后，才称为“已蒸馏”。

## 语料验证

变更知识库或证据包后，在共享根运行：

```bash
python scripts/validate_corpus.py .
python tests/run_all.py
```

报告必须回答“蒸馏了多少、来自哪些视频、哪些只有字幕、哪些检查了画面、哪些仍是候选”。
