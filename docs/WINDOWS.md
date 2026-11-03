# Windows 安装与使用

首版支持 **Windows 10/11 x64，Python 3.11、3.12 或 3.13 的 64 位版本**。不需要 Anaconda、Node.js 或 Git；普通用户下载 ZIP 即可。Python 3.14、32 位 Python、Windows ARM64 和其他系统暂不在首版支持范围内。

## 1. 下载项目

在 [仓库首页](https://github.com/Leo-Dongyu/video-notes) 点击 **Code → Download ZIP**，解压到普通可写目录，例如 `C:\Users\你的用户名\Documents\video-notes`。不要在 ZIP 内直接运行，也不要放进 `Program Files` 等需要管理员权限的目录。

升级时保留旧目录中的 `config.json`、`cookies/`、`outputs/` 与 `models/`。不要把别人电脑上的 `.venv` 复制过来，Python 环境需要在本机重新安装。

## 2. 安装 Python

从 [Python 官方 Windows 下载页](https://www.python.org/downloads/windows/) 安装受支持的 **64 位 Python 3.13.x**（或 3.11/3.12）。使用传统安装器时勾选 “Add python.exe to PATH”。如果官方页面默认推荐了更高版本，请明确选择本项目支持的版本。

关闭并重新打开终端，然后检查：

```powershell
python --version
# 安装了 Python Launcher 的电脑也可以使用：
py -3.13 --version
```

## 3. 安装 FFmpeg

从 [FFmpeg 官方下载入口](https://ffmpeg.org/download.html#build-windows) 选择 Windows 构建。解压后，找到同时包含 `ffmpeg.exe`、`ffprobe.exe` 的 `bin` 目录，将其加入用户 `PATH`。

重新打开终端，确认以下命令都能运行：

```powershell
ffmpeg -version
ffprobe -version
```

也可以把这两个工具及其所需配套文件放在项目的 `bin/` 目录，或设置 `VIDEONOTES_FFMPEG`、`VIDEONOTES_FFPROBE` 为对应 EXE 的绝对路径。本仓库不附带 FFmpeg 二进制。

## 4. 安装项目依赖

双击根目录 **`install.bat`**。脚本会在本项目创建 `.venv`，从 PyPI 安装固定版本的 Python 依赖，然后检查环境。无需激活环境，不修改全局 Python 包，也不要求管理员权限。

需要联网。首次安装可能下载较大的语音识别运行库，请等待安装窗口提示结果；关闭窗口会中断安装。安装失败后可重新运行，已有环境会复用。

可选的画面 OCR：

```powershell
.\install.bat -WithOcr
```

缺少 OCR 不影响视觉模型读取关键帧。默认安装包含本地语音识别，但**不包含模型权重**。

## 5. PDF 与 Word

- **PDF**：需要本机 Edge 或 Chrome，支持系统级和当前用户安装位置。一般 Windows 电脑已有 Edge，无需另装 PDF 软件。
- **Word**：安装 [Pandoc](https://pandoc.org/installing.html)，重新打开终端后确认 `pandoc --version` 可用。不需要安装 Microsoft Word。
- 缺少对应工具时仍可生成 Markdown，任务会明确提示某种格式导出未完成。
- 自定义 Word 模板见 [模板说明](../templates/README.md)，公开版本使用 Pandoc 默认样式。

## 6. 环境检查与启动

双击 **`doctor.bat`** 查看检查结果。`MISSING` 是需要补齐的基本要求，`OPTIONAL` 表示某个可选能力尚未安装。诊断不会访问模型 API，也不会显示 API Key 或 Cookie。

双击 **`start-web.vbs`**，服务在后台启动，准备好后自动打开默认浏览器。`start-web.bat` 是兼容入口，可能瞬间闪过窗口。

默认地址为 <http://127.0.0.1:8765/>。关闭浏览器不会结束服务；停止时双击 **`stop-web.bat`**。关闭入口只会结束本项目对应的进程树。

启动日志位于 `work/web-service.log`、`work/web-service-error.log`。

## 7. 填写自己的 API Key

在网页“设置”中填写自己的 DeepSeek API Key。默认模型 `deepseek-flash`，接口为 `https://api.deepseek.com/anthropic`，使用兼容 Anthropic Messages 的请求格式。

默认 Flash 支持本项目所需的图像输入。切换模型或第三方接口时，应确认其同时支持当前接口格式和图像输入；纯文本模型无法可靠转录画面代码。参见 [DeepSeek 图像输入说明](https://api-docs.deepseek.com/guides/vision/)。

也可复制 `config.example.json` 为 `config.json` 后自行填写，或设置 `DEEPSEEK_API_KEY` 用户环境变量。Key 优先级为命令行 → 进程环境 → 配置文件 → Windows 用户环境变量。

每位使用者使用自己的 Key、承担自己的 API 费用。生成笔记会把转写、选中的关键帧及必要的笔记内容发送给配置的服务。配置、Cookie 和输出目录不能公开分享，见 [安全说明](../SECURITY.md)。

## 8. B站与本地视频

- B站视频填写 BV 号或链接；有需要时用手机客户端扫码登录。
- 合集选择“B站合集 / 多P”，粘贴合集页或合集内视频链接，点击“读取视频列表”。
- 本地目录可通过“浏览…”选择，按需勾选“包含子目录”。
- 选择分别生成或整合生成，设置格式、质量档，点击开始。
- 分别生成时，某个视频失败会继续处理后续视频，最终提示“部分完成”；可重试复用已完成步骤。

B站登录凭据仅保存在本机，重启可复用，但不能保证永久有效。账号权限、视频是否提供字幕、访问限制与平台接口变化会影响结果。

## 9. 语音模型与网络

没有可用字幕时使用 faster-whisper 本地转写，默认 CPU/int8，普通电脑可运行，但长视频可能较慢。首次下载默认 `small` 模型约需数百 MB 空间，后续复用项目 `models/`。

默认使用模型提供方的下载源，不强制指定镜像。若网络无法访问模型源，可在了解第三方镜像来源后自行设置 `HF_ENDPOINT`；也可通过 `VIDEONOTES_MODEL_DIR` 指定已有缓存。未缓存的模型无法在完全离线状态下首次使用。

## 可选配置

| 环境变量 | 作用 |
|---|---|
| `VIDEONOTES_PYTHON` | 指定 Python 绝对路径；默认优先本项目 `.venv`，再查找已安装版本 |
| `VIDEONOTES_PORT` | 修改端口，默认 8765；启停时应一致 |
| `VIDEONOTES_FFMPEG` / `VIDEONOTES_FFPROBE` | 指定 FFmpeg 工具路径 |
| `VIDEONOTES_YTDLP` | 指定 yt-dlp EXE |
| `VIDEONOTES_BROWSER` | 指定 PDF 导出浏览器 EXE |
| `VIDEONOTES_PANDOC` | 指定 Pandoc EXE |
| `VIDEONOTES_MODEL_DIR` | 共享语音模型缓存 |
| `HF_ENDPOINT` | 自选模型下载源 |

## 常见问题

- **找不到 Python**：确认安装了受支持的 64 位版本，重新打开终端；可设置 `VIDEONOTES_PYTHON`。
- **pip 下载失败**：检查网络、代理及证书，重新运行安装；不要关闭证书校验来绕过错误。
- **服务启动后没有页面**：检查日志、端口是否被其他软件占用，以及项目目录是否可写。可修改 `VIDEONOTES_PORT` 后重试。
- **模型下载失败**：检查模型源网络和缓存位置，首次下载期间不要关闭服务。
- **ASR runtime / DLL load failed**：安装 [微软 Visual C++ x64 运行库](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)，重启后重新运行环境检查。语音识别依赖的 CTranslate2 在 Windows 上需要该运行库；多数电脑已有，但全新系统可能没有。
- **PDF 无法覆盖**：关闭正在查看该 PDF 的程序后重试，新导出结果可能以 `.render.pdf` 暂存。
- **Chrome Cookie 导入失败**：优先使用网页扫码登录；辅助导入方式不是必需步骤。
- **字体不同**：PDF 使用本机中文字体，跨机器可能有轻微换行差异；首版仅支持 Windows。

卸载时停止服务后删除项目目录即可。删除前自行备份所需的笔记和模型；项目不会替你卸载系统 Python、FFmpeg、浏览器或 Pandoc。
