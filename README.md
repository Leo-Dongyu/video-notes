# video-notes

[![Windows checks](https://github.com/Leo-Dongyu/video-notes/actions/workflows/windows.yml/badge.svg)](https://github.com/Leo-Dongyu/video-notes/actions/workflows/windows.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

将 **B站视频、合集、多 P 视频、本地视频或目录**整理为精炼的学习笔记，支持 Markdown、PDF 和 Word。核心能力包括字幕/语音转写、关键帧与代码画面分析、分章写作与复核、多视频整合，以及可恢复的本地任务管理。

**v0.1.0：Windows 10/11 x64 源码版，Python 3.11–3.13。** 不需要 Anaconda、Node.js 或 Git。需要自备模型 API Key；首次安装与模型下载需要网络。其他平台暂未承诺支持。

## 快速安装

1. 点击 **Code → Download ZIP**，解压到自己的可写目录。
2. 安装 [受支持的 64 位 Python](https://www.python.org/downloads/windows/) 和 [FFmpeg](https://ffmpeg.org/download.html#build-windows)，确认 `ffmpeg`、`ffprobe` 在 PATH 中。
3. 双击 **`install.bat`**，在项目独立的 `.venv` 中安装依赖。
4. 双击 **`doctor.bat`**，检查环境；PDF 需要本机 Edge/Chrome，Word 需要另装 [Pandoc](https://pandoc.org/installing.html)。
5. 双击 **`start-web.vbs`**，后台启动并自动打开浏览器。
6. 在网页“设置”中填写自己的 DeepSeek API Key，再选择视频开始生成。

**首次使用请阅读 [完整 Windows 安装说明与常见问题](docs/WINDOWS.md)。** 安装脚本不自动修改系统环境或安装外部软件。关闭浏览器不会停止后台服务；停止时双击 `stop-web.bat`。

默认地址：<http://127.0.0.1:8765/>。本项目仅供本机使用，**不能直接部署到公网**，详见 [安全与隐私](SECURITY.md)。

## 使用方式

- **单视频**：输入 BV 号、视频链接或本地视频路径。
- **合集 / 多 P**：选择“B站合集 / 多P”，输入合集页或合集内视频链接，自动读取全部视频。
- **本地目录**：选择目录，按需包含子目录，按文件名自然排序。
- **生成方式**：每个视频分别生成一份笔记，或整合为一份总笔记。
- **任务管理**：进度、停止、重试继续、网页预览、下载和历史记录。

PDF/Word 使用视频标题命名，任务目录为 `outputs/YYYYMMDD-HHMMSS-视频标题/`。正文围绕知识点展开，不显示时间码、帧文件名或转录旁白；内部素材仍保留，供复核和重试。

B站可在网页扫码登录。凭据只保存本机，关闭网页或重启后可复用；过期或被撤销时需要重新扫码，不保证永久登录。

## DeepSeek API 与本地处理

默认使用 `deepseek-flash` 和兼容 Anthropic Messages 的 `https://api.deepseek.com/anthropic`，支持文字与关键帧输入。每位使用者填写自己的 Key，并承担自己的调用费用。参见 [DeepSeek 图像接口说明](https://api-docs.deepseek.com/guides/vision/)。

- 下载、抽帧、语音转写、读取合集和格式导出不调用总结 API。
- 写作、大纲、代码复核、术语和整合会把必要的转写、画面与笔记内容发给配置的模型服务。
- `config.json`、`cookies/`、`outputs/`、`models/`、日志和模型响应缓存均不随源码发布。
- 默认 CPU/int8 语音识别；首次使用下载模型，之后复用 `models/`。不要求 NVIDIA 显卡。

## 命令行

安装完成后，从项目目录运行：

```powershell
# 单视频
.\.venv\Scripts\python.exe video2notes.py -i "BV1VC7g6vE9f" -o outputs/demo --format pdf

# 多视频整合
.\.venv\Scripts\python.exe video2notes.py -i "C:\课程\第1课.mp4" -i "C:\课程\第2课.mp4" --merge -o outputs/merged --format pdf,docx

# 重试复用已有素材与成功的模型响应
.\.venv\Scripts\python.exe video2notes.py -i "BV1VC7g6vE9f" -o outputs/demo --reuse
```

`--prepare-only` 只准备素材，`--dry-run` 不调用模型，`--no-cache` 禁用模型响应复用。完整参数见 `--help`。

## 项目结构

```text
video-notes/
├─ install.bat / doctor.bat              安装与环境诊断
├─ start-web.vbs / stop-web.bat          静默启动与停止
├─ server.py / video2notes.py / ...      保留的命令行入口
├─ video_notes/                         应用实现
├─ web/                                 本地网页
├─ tools/                               Windows 脚本与发布工具
├─ tests/                               自动化测试
├─ templates/                           可选个人 Word 模板说明
├─ docs/WINDOWS.md                      安装、配置、排错
└─ .github/workflows/windows.yml        Windows/Python 版本测试
```

`outputs/`、`models/`、`cookies/`、`work/`、`.venv/` 是本机数据，不要提交到仓库。根目录兼容入口仍被子进程和导出脚本使用，不应删除。

## 开发和验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe tools/build_release.py --check
```

维护网页时可额外使用 Node.js 运行 `node --check web/app.js`，普通使用者不需要 Node.js。测试不需要 API Key，使用模拟响应验证模型调用流程，不会自动产生 API 费用。

依赖直接版本固定在 `requirements.txt`；可选 OCR 在 `requirements-optional.txt`。源码压缩包由允许清单构建，排除本机数据。版本记录见 [CHANGELOG](CHANGELOG.md)。

## 许可与限制

原创代码采用 [MIT License](LICENSE)。外部工具与模型遵循各自许可，详见 [第三方依赖说明](THIRD_PARTY.md)。本仓库不附带视频、模型权重、个人模板或 API Key。

请仅处理有权访问和使用的视频。B站平台接口、网络和账号权限变化可能影响下载、字幕与登录；代码转录和自动总结仍需人工核对。提交 Issue 前请移除配置、Cookie 和私人内容。
