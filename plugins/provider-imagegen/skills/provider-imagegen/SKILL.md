---
name: provider-imagegen
description: Use the active Codex provider's OpenAI-compatible Images API through the plugin CLI for raster generation, editing, and genuinely transparent PNG output.
---

# Provider Imagegen

本技能是 Codex 中唯一的位图生图/改图入口。它只走插件 CLI：读取 Codex Sync
在成功 pull 后写入版本化插件 cache 的 provider endpoint、headers、环境变量引用和
query 参数，再由 CLI 直接调用该 provider 的 OpenAI-compatible Images API。不要调用
内置 `image_gen`，也不要调用系统 `.system/imagegen` 技能或脚本。

## 触发与边界

- 用户要生成、编辑、抠图、背景替换或批量生成 PNG/JPEG/WebP 时使用本技能。
- 目标是 SVG、CSS、canvas、已有矢量图标或可确定性编辑的原生文件时，不使用本技能。
- 每次真实生成都必须由用户已给出的图片意图、尺寸、质量和输出路径驱动；缺少会改变结果的关键主体或编辑范围时先提问。
- credential 由 Codex Sync 写入版本化插件 cache；不把图片内容、provider 响应、Authorization header 或完整错误 body 写入日志、诊断、其他持久化副本或回复。图片原子提交允许使用同目录短生命周期临时文件，失败即清理。
- 不自动重试、换 provider、换模型或删除用户的透明参数。provider 拒绝请求时，报告 `stage`、`code`、HTTP 状态（若有）并停在该边界。

## CLI 入口

`<plugin-root>` 是本技能所在插件目录。macOS/Linux 使用：

```bash
"<plugin-root>/scripts/run.sh" generate \
  --prompt "A clean product cutout of a ceramic mug" \
  --model gpt-image-2 \
  --size 1024x1024 \
  --quality medium \
  --out output/imagegen/mug.png
```

Windows 使用：

```powershell
& "<plugin-root>\scripts\run.ps1" generate `
  --prompt "A clean product cutout of a ceramic mug" `
  --model gpt-image-2 `
  --size 1024x1024 `
  --quality medium `
  --out output/imagegen/mug.png
```

`run.sh`/`run.ps1` 负责选择可用的 Python 3.8+；可用绝对路径环境变量
`PROVIDER_IMAGEGEN_PYTHON` 覆盖解释器。不要假设 `python` 一定存在，也不要把
credential 放在命令行参数中。

支持的子命令：

- `generate`：新图；必须有 `--prompt` 或 `--prompt-file`。
- `edit`：编辑已有图片；重复 `--image` 指定输入，`--mask` 可选。
- `generate-batch`：读取 JSONL，每行一个 `prompt` 和可选参数，必须指定 `--out-dir`。CLI 会先完整校验所有行、参数、basename、重复目标和已有文件，全部通过后才解析 credential 或发出请求；重复目标即使使用 `--force` 也停止。运行期中途失败不会自动重试或清理已完成文件，错误 JSON 的 `diagnostic.completed_files` 会列出它们。

常用参数：`--model`（默认 `gpt-image-2`）、`--size`（默认 `auto`）、`--quality`
（`low|medium|high|auto`）、`--n`（1–10）、`--background`
（`transparent|opaque|auto`）、`--output-format`（`png|jpeg|webp`）、`--out`、
`--out-dir`、`--force`、`--dry-run`。`edit` 另有 `--input-fidelity` 和 `--mask`。

## 透明图的明确流程

透明背景不是提示词中的愿望，而是请求参数和验收条件：

1. 以 PNG 为输出格式，显式传 `--background transparent --output-format png`；不要只写“透明背景”。
2. 默认仍使用 `gpt-image-2`，并把该参数原样交给当前 provider。不要因为旧的本地预检规则而提前拒绝，也不要静默改成 `gpt-image-1.5`。
3. CLI 将响应中的 `b64_json` 解码，或从 provider 返回的 URL 下载图片；此时只保存在内存中，下载 URL 不携带 provider credential，也不跟随重定向。
4. 在写入最终文件前解析 PNG 的实际 alpha 通道，必须存在至少一个 alpha 小于 255 的像素；仅有 RGBA 模式但全图不透明也算失败。结构或 alpha 验收失败时不产生最终文件。
5. 只有请求成功、有效 PNG 和 alpha 验收都通过后，才以原子方式写入最终文件并报告透明图已生成。

透明图命令：

```bash
"<plugin-root>/scripts/run.sh" generate \
  --model gpt-image-2 \
  --prompt "A polished orange robotic fox sticker, isolated subject, no text" \
  --size 1024x1024 \
  --quality medium \
  --background transparent \
  --output-format png \
  --out output/imagegen/robot-fox-transparent.png
```

若 provider 返回“不支持 `background=transparent`”等明确错误，保留该失败证据并停止；
只有用户明确选择另一个模型/方案时，才重新执行。不要删除透明参数来“让请求成功”。

## Prompt 组织

在 `--prompt` 或 `--prompt-file` 中按以下顺序写必要信息，避免凭空添加用户未要求的内容：

```text
Use case: <taxonomy>
Asset type: <where it will be used>
Primary request: <subject and goal>
Input images: <role of each --image>          # edit only
Scene/backdrop: <scene or transparent output>
Subject: <main subject>
Style/medium: <photo, illustration, 3D, ...>
Composition/framing: <placement and padding>
Lighting/mood: <lighting and mood>
Color palette: <palette notes>
Text (verbatim): "<exact text or none>"
Constraints: <must keep / must avoid>
Avoid: <negative constraints>
```

编辑时明确写出“只改变 X，保持 Y 不变”，并说明每个 `--image` 的角色和顺序。

## Provider 与 credential

- Codex Sync 在配置应用和插件收敛后，把当前 provider 原子写入 `<CODEX_HOME>/plugins/cache/<marketplace>/provider-imagegen/<version>/.codex-provider/credential.json`，且不把它加入同步仓库。CLI 直接读取该文件，不检查 POSIX mode 或 Windows ACL，不启动 Codex app-server，也不解析或改写 `config.toml`。
- cache 保存 endpoint、显式 headers、环境变量引用、query 参数和非敏感 fingerprint；字面 bearer token 会被转换为 `Authorization` header，不以 `experimental_bearer_token` 字段保存。`env_key` 与 `env_http_headers` 仍在 CLI 进程中解析。仅有 Codex 登录态 session 或 command-backed auth 的 provider 不生成可用 cache；不要读取 auth 文件或执行任意登录命令。
- 不要求用户设置或粘贴 `OPENAI_API_KEY`，不执行 provider 的任意 `auth` 命令，不自行把 token 拼入 URL、请求参数、输出文件或诊断信息。
- 若 cache、provider、base URL 或 credential 缺失，或 cache 格式/文件类型无效，返回 `credential_cache_missing`、`credential_cache_invalid` 或对应的结构化失败并停止；先运行 Codex Sync pull 刷新 cache，不要猜测 OpenAI、回退到另一个 provider 或改用内置工具。
- 网络请求禁用继承的 `HTTP_PROXY`/`HTTPS_PROXY`，禁止 credential-bearing 请求跟随重定向。
- 带 credential 的远程 HTTP provider 会在网络请求前拒绝；loopback HTTP 只用于用户明确配置的本机 gateway。provider 返回的跨源图片 URL 只允许 HTTPS，并且 DNS 的所有 A/AAAA 结果都必须是公网地址；与已配置 provider 完全同源的 URL 可用于本地 gateway。下载请求不带 provider credential。

## 输出、临时文件与验收

- 默认最终文件在 `output/imagegen/`；批量输入 JSONL 由调用方提供，CLI 不复制或持久化它。
- 已存在的目标文件不会被覆盖，除非用户明确传 `--force`。优先使用带语义的稳定文件名。
- 批量任务会在任何网络请求前完整校验所有任务、输出文件名、冲突和已有文件；同一批次的重复输出名即使传 `--force` 也会失败。
- 编辑输入最多 16 张图片，另加一个可选 mask；图片与 mask 合计不超过 50 MiB。CLI 不转换或本地限定输入图片格式，按文件名推断 content type 后原样交给 provider，由 provider 决定是否接受；multipart 内容区允许 prompt 的换行，但文件名会被限制为安全的 ASCII basename。
- 成功 stdout 是不含秘密的 JSON，至少包含 `ok`、模型、输出文件路径和每个文件的字节数；失败以非零退出码返回 `stage`、`code`、`retryable` 和必要的 HTTP 状态。
- `--dry-run` 只打印规范化请求，不读取 credential cache、不发网络请求，也不创建图片。
- 交付前检查文件存在、格式/扩展名匹配、大小可读；透明请求额外检查真实 alpha。检查失败时不声称成功。
- 只报告最终路径和实际执行的模型/参数；不要回显完整 prompt、provider 响应或任何 credential。
