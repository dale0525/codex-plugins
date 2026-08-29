# Provider Imagegen

Provider Imagegen 用本地 CLI 调用当前 Codex provider 的 OpenAI-compatible
Images API，生成或编辑 PNG/JPEG/WebP。Codex Sync 在成功 pull 后把当前
provider 的 endpoint、headers、环境变量引用和 query 参数写入版本化插件 cache，
并写入插件版本目录之外的稳定 sibling cache；CLI 先读版本化 cache，缺失时读
sibling cache，不需要用户粘贴密钥，也不使用系统内置 `image_gen`。
两处 cache 都会原子替换且不进入同步仓库；CLI 不检查 POSIX mode 或 Windows ACL。
字面 bearer token 只以 `Authorization` header 形式存在于该 cache，CLI
不回显或记录它。仅有 Codex 登录态 session 或 command-backed auth 的 provider 不会
生成可用 cache，也不会读取 auth 文件或执行任意登录命令。

## 安装

```bash
codex plugin add provider-imagegen@dale0525-codex-plugins
```

安装后请新建一个 Codex task，使新 skill 生效。
同时运行一次 Codex Sync `pull`，让同步后的 provider credential 注入该版本的
本地插件 cache；provider 配置或插件版本变化后也要重新 pull。cache 缺失、格式错误或
文件类型无效时，CLI 会在网络请求前返回对应的 `credential_cache_*` 错误。

## 生成普通图片

macOS/Linux：

```bash
plugins/provider-imagegen/scripts/run.sh generate \
  --prompt "A cozy alpine cabin at dawn" \
  --model gpt-image-2 \
  --size 1024x1024 \
  --quality medium \
  --out output/imagegen/alpine-cabin.png
```

Windows：

```powershell
& "plugins/provider-imagegen/scripts/run.ps1" generate `
  --prompt "A cozy alpine cabin at dawn" `
  --model gpt-image-2 `
  --size 1024x1024 `
  --quality medium `
  --out output/imagegen/alpine-cabin.png
```

## 生成真正透明的 PNG

同时传递参数和输出格式，不能只依赖 prompt：

```bash
plugins/provider-imagegen/scripts/run.sh generate \
  --model gpt-image-2 \
  --prompt "A clean orange robotic fox sticker, isolated subject, no text" \
  --size 1024x1024 \
  --quality medium \
  --background transparent \
  --output-format png \
  --out output/imagegen/robot-fox-transparent.png
```

CLI 会在写入最终文件前读取 PNG alpha 通道；至少一个像素必须真正透明，否则命令失败且不产生最终文件。
provider 若拒绝该参数，命令会报告结构化错误并停止，不会静默换模型、删参数或改走
内置工具。

## 编辑与批量

```bash
plugins/provider-imagegen/scripts/run.sh edit \
  --image input.png \
  --prompt "Change only the background; keep the subject and edges unchanged" \
  --background transparent \
  --output-format png \
  --out output/imagegen/cutout.png
```

批量使用 `generate-batch --input prompts.jsonl --out-dir output/imagegen/batch`；每行
至少包含 `prompt`，可覆盖模型、尺寸、质量、背景和输出文件名。CLI 会先完整校验
所有行、输出冲突和已有文件，再解析 credential 或发出任何请求；单批最多 500 个
任务，编辑输入最多 16 张图片（另加一个可选 mask），图片与 mask 合计 50 MiB。
编辑输入不会被 CLI 转换或本地限定格式，是否接受由当前 provider 决定。
运行期某个任务失败时，不自动重试或清理已经完成的文件；错误 JSON 会在
`diagnostic.completed_files` 列出已完成路径。

使用 `--dry-run` 可只查看规范化请求而不读取 credential cache、不发网络请求。
跨源图片 URL 只允许 HTTPS 且必须解析到公网地址；与已配置 provider 完全同源的 URL
可用于本地网关。带 credential 的远程 HTTP provider 会在网络请求前被拒绝；loopback
HTTP 仅用于本机 provider。
