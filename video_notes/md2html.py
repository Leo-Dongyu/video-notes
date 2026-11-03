#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Markdown -> 单文件 HTML（内嵌样式 + Pygments 代码高亮），供 md2pdf.ps1 打印成 PDF。

用法: python md2html.py -i notes.md -o notes.html [-t "标题"]
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys

try:
    import markdown
except Exception:  # noqa: BLE001
    print("[ERR] 缺少 markdown 包：python -m pip install markdown", file=sys.stderr)
    sys.exit(2)

CSS = r"""
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI", sans-serif;
  font-size: 10.5pt; line-height: 1.65; color: #1f2328; margin: 0;
}
.doc-header { border-bottom: 2px solid #2563eb; padding-bottom: 6px; margin-bottom: 14px; }
.doc-header .title { font-size: 20pt; font-weight: 700; color: #111827; margin: 0 0 4px 0; }
.doc-header .meta { font-size: 8.5pt; color: #6b7280; }
h1 { font-size: 17pt; margin: 18px 0 8px; color: #0f172a; border-left: 5px solid #2563eb; padding-left: 8px; }
h2 { font-size: 14pt; margin: 16px 0 6px; color: #0f172a; border-bottom: 1px solid #e5e7eb; padding-bottom: 3px; }
h3 { font-size: 12pt; margin: 12px 0 4px; color: #1e293b; }
h4 { font-size: 11pt; margin: 10px 0 4px; color: #334155; }
p { margin: 5px 0; text-align: justify; }
ul, ol { margin: 5px 0 5px 20px; padding: 0; }
li { margin: 2px 0; }
code { font-family: Consolas, "Cascadia Code", "Courier New", monospace; font-size: 9pt;
  background: #f1f5f9; padding: 1px 4px; border-radius: 3px; color: #b91c1c; }
pre { font-family: Consolas, "Cascadia Code", "Courier New", monospace; font-size: 8.6pt;
  line-height: 1.45; background: #f8fafc; border: 1px solid #e2e8f0; border-left: 4px solid #2563eb;
  border-radius: 4px; padding: 8px 10px; margin: 6px 0 10px; white-space: pre-wrap;
  word-break: break-word; page-break-inside: avoid; }
pre code { background: none; padding: 0; color: #1f2328; font-size: 8.6pt; }
blockquote { margin: 6px 0; padding: 4px 12px; border-left: 4px solid #94a3b8; background: #f8fafc; color: #475569; }
table { border-collapse: collapse; width: 100%; margin: 6px 0 10px; font-size: 9pt; page-break-inside: avoid; }
th, td { border: 1px solid #d1d5db; padding: 4px 7px; vertical-align: top; }
th { background: #f1f5f9; }
img { max-width: 100%; page-break-inside: avoid; }
hr { border: none; border-top: 1px solid #e5e7eb; margin: 12px 0; }
a { color: #1d4ed8; text-decoration: none; }
.ts { color: #b45309; font-weight: 600; }
.tag { display: inline-block; background: #eef2ff; color: #3730a3; border-radius: 3px;
  padding: 0 5px; font-size: 8.5pt; margin-right: 4px; }
""" + r"""
.codehilite { background: #f8fafc; }
.codehilite pre { border-left-color: #2563eb; }
"""

TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
{css}
{pygments_css}
</style>
</head>
<body>
<div class="doc-header">
  <div class="title">{title}</div>
  <div class="meta">{meta}</div>
</div>
{body}
</body>
</html>
"""


def md_to_html(md_path: str, title: str | None, meta: str) -> str:
    with open(md_path, encoding="utf-8", errors="replace") as source:
        text = source.read()
    # 页面已有统一文档标题，正文不重复渲染同一个一级标题。
    first, separator, rest = text.partition("\n")
    if title and first.strip() == "# " + title:
        text = rest.lstrip("\r\n")
    html_body = markdown.markdown(
        text,
        extensions=["fenced_code", "codehilite", "tables", "sane_lists", "nl2br"],
        extension_configs={"codehilite": {"guess_lang": False, "css_class": "codehilite",
                                          "pygments_style": "friendly"}},
    )
    # 相对图片路径 -> 绝对 file:// URI
    base = os.path.dirname(os.path.abspath(md_path))

    def fix_img(m):
        src = m.group(1)
        if src.startswith(("http://", "https://", "data:", "file:")):
            return 'src="%s"' % src
        p = os.path.normpath(os.path.join(base, src))
        return 'src="file:///%s"' % p.replace("\\", "/")

    html_body = re.sub(r'src="([^"]+)"', fix_img, html_body)
    # 内嵌 <img> 高度限制
    html_body = html_body.replace("<img ", '<img style="max-height:230mm" ')
    # [12:34] 时间码高亮
    html_body = re.sub(r"\[(\d{1,2}:\d{2}(?::\d{2})?)\]", r'<span class="ts">[\1]</span>', html_body)
    # Pygments 样式
    try:
        from pygments.formatters import HtmlFormatter
        pygments_css = HtmlFormatter(style="friendly").get_style_defs(".codehilite")
    except Exception:  # noqa: BLE001
        pygments_css = ""
    if not title:
        title = os.path.splitext(os.path.basename(md_path))[0]
    return TEMPLATE.format(title=html.escape(title), meta=html.escape(meta),
                           css=CSS, pygments_css=pygments_css, body=html_body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("-t", "--title")
    ap.add_argument("--meta", default="")
    args = ap.parse_args()
    meta = args.meta
    out = md_to_html(args.input, args.title, meta)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(out)
    print("[md2html] " + args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
