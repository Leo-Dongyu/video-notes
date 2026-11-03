# 第三方工具与依赖

本仓库发布应用源码，不包含 Python、FFmpeg、浏览器、Pandoc、模型权重或个人 Word 模板。安装脚本从 PyPI 安装 Python 依赖，系统工具由使用者单独安装。

各依赖的版权与许可归其作者所有，适用各自发布的许可证；本仓库的 MIT 许可证只适用于本项目原创代码，不替代以下项目的许可证。

| 项目 | 用途 | 官方来源 |
|---|---|---|
| yt-dlp | 视频下载与字幕 | https://github.com/yt-dlp/yt-dlp |
| FFmpeg | 音视频探测、转码、抽帧 | https://ffmpeg.org/ |
| faster-whisper / CTranslate2 | 本地语音识别 | https://github.com/SYSTRAN/faster-whisper |
| Pillow | 图像处理 | https://python-pillow.org/ |
| Requests | B站请求与扫码登录 | https://requests.readthedocs.io/ |
| qrcode | 二维码图像 | https://github.com/lincolnloop/python-qrcode |
| Python-Markdown | Markdown 渲染 | https://python-markdown.github.io/ |
| Pygments | 代码高亮 | https://pygments.org/ |
| RapidOCR（可选） | 代码画面 OCR | https://github.com/RapidAI/RapidOCR |
| Pandoc（可选） | Word 导出 | https://pandoc.org/ |
| Edge / Chrome（PDF 导出） | 本机浏览器打印 | https://www.microsoft.com/edge / https://www.google.com/chrome/ |

语音识别模型首次使用时从模型提供方下载，应遵守相应模型仓库的许可。B站内容及生成笔记不随本项目分发。请仅处理自己有权访问和使用的视频内容。
