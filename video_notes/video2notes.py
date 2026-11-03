#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video2notes.py —— 视频 → 高质量 Markdown / PDF / Word 笔记（DeepSeek API，独立于 DSH）

两段式（balanced/precise 质量）：
  1) 素材准备（video2frames.py）：下载/字幕或 ASR/关键帧/代码帧 OCR
  2) 大纲 → 分章细节（每章只带相关帧）→ 代码块 QA 复核 → 组装 → 打印

用法：
  python video2notes.py -i "BV1VkgK6NEZS" -o work/demo
  python video2notes.py -i "D:\\录屏\\第3讲.mp4" -o work/lesson --asr-model medium --quality precise
  python video2notes.py --from-out -i work/demo -o work/demo --format pdf,docx

参数：
  --format pdf|docx|md[,..]   输出格式（默认 pdf，可多选）
  --quality fast|balanced|precise  质量档（默认 balanced）
  --title NAME               笔记标题（默认视频标题/文件名）
  --max-tokens N             单次输出上限（默认按质量档）
  --no-thinking              关闭思考
  --progress-json FILE       进度 JSON（Web 端使用）
  --cookies FILE             B站 cookies（默认 cookies/bilibili.txt，存在即用）
"""
from __future__ import annotations

import argparse
import base64
import json
import hashlib
import copy
import shutil
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from .naming import safe_name, source_title, is_source_reference, document_title
from .runtime import find_executable

from .paths import PROJECT_ROOT

HERE = PROJECT_ROOT
DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_COOKIES = HERE / "cookies" / "bilibili.txt"

QUALITY = {
    "fast":     {"max_images": 12, "thinking": False, "chapters": False, "qa": 0, "max_tokens": 24000},
    "balanced": {"max_images": 26, "thinking": True,  "chapters": True,  "qa": 1, "max_tokens": 32000},
    "precise":  {"max_images": 40, "thinking": True,  "chapters": True,  "qa": 2, "max_tokens": 48000},
}

MERGE_SYSTEM = """根据多份视频笔记，输出一个“综合整合”小节：共同主线、重点对比、建议学习顺序、材料尚未解答的问题。
以客观精炼的知识笔记写作，不虚构个人学习动作。不输出时间码、来源帧、转录说明或处理过程。只根据材料判断。"""

OUTLINE_SYSTEM = """你是视频内容结构分析师。根据给定的带时间码转写与关键帧列表，输出视频的章节大纲。
要求：
1. 章节按内容自然分段，3~8 章，每章 15 秒到 5 分钟，不要机械按时间切。
2. 每章标题写「学习者视角在看什么」，如「为什么需要旋转位置编码」而不是「RoPE 介绍」。
3. 每章给 2~4 个 points（这段的关键信息），以及 goal（用第一人称写「我这段想弄懂什么」）。
4. 只输出 JSON 数组，元素：{"title": "...", "start": 0.0, "end": 123.0, "points": ["..."], "goal": "..."}。不要输出其它文字。"""

# 时间、帧名仅供内部定位；交付笔记只保留学习内容。
NOTE_SYSTEM = """你是一位严谨的课程笔记编辑。根据给定转写、画面和 OCR，输出精炼的中文 Markdown 学习笔记。
按知识点组织：核心结论、必要解释、操作步骤、实际出现的代码与易错点。标题只写知识点。
禁止在正文、标题和引用中输出时间码、截图路径、帧文件名、转录来源、生成时间和处理过程。
不要写“画面显示”“PPT 对比”“转录自”等旁白，直接陈述实质内容。
不要虚构第一人称思考、学习动作、背景或视频来源；不要每章重复总结、回看清单或固定模板。
代码只转录画面中可辨认的实际内容，保留语言和缩进；看不清就简短标注“代码不清晰，需核对”，不能补造。
只用提供的材料，不把推断写成事实，不添加未经材料支持的解释。只输出正文，以 ## 知识点标题开头。"""
QA_SYSTEM = """核对笔记代码与提供的画面及 OCR。仅修正可确认的错误，保留代码缩进和精炼风格。
不得输出时间码、帧名、路径、来源说明或校对过程；看不清处标注需核对。只输出完整修订章节。"""
EXTRA_SYSTEM = """根据笔记，补充简短的术语表和自测问题（附一句答案要点）。只保留有学习价值且不重复正文的内容。
不得输出时间码、截图文件名、来源说明、虚构学习动作。不强制凑数量，只输出 Markdown 小节。"""
_API_TOTAL = {"input_tokens": 0, "output_tokens": 0}
_CACHE_DIR = None
_WARNINGS = []


def clean_notes(text):
    """清理展示元信息，完整保留围栏代码块。"""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            lines.append(line)
            continue
        if fence:
            lines.append(line)
            continue
        if re.match(r"^\s*>?\s*(?:来源|转录自|来源帧|生成时间|回看要点)[：:]", line):
            continue
        line = re.sub(r"[（(][^（）()\n]*(?:转录自|来源帧|f_\d+_[\d-]+\.(?:jpg|png))[^（）()\n]*[）)]", "", line)
        line = re.sub(r"\[\d{1,3}:\d{2}(?::\d{2})?\]", "", line)
        line = re.sub(r"`?(?:frames/)?f_\d+_[\d-]+\.(?:jpg|png)`?", "", line)
        line = re.sub(r"\s+[—–-]\s*(?=\*?\*?\s*$)", "", line)
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# ---------------------------------------------------------------- 进度

_PROG = {"path": "", "t": 0.0, "stage": ""}


def set_progress_path(path):
    _PROG["path"] = path or ""


def prog(stage, percent, msg="", force=False):
    path = _PROG["path"]
    if not path:
        return
    now = time.time()
    if (not force) and stage == _PROG["stage"] and now - _PROG["t"] < 0.5:
        return
    _PROG["t"], _PROG["stage"] = now, stage
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"stage": stage, "percent": round(max(0, min(100, percent)), 1),
                       "msg": msg, "ts": now}, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ---------------------------------------------------------------- 基础

def log(msg):
    print(msg, flush=True)


def read_text(p):
    with open(p, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def parse_entries(transcript):
    """[mm:ss - mm:ss] text -> [(start,end,text)] seconds."""
    out = []
    for line in transcript.splitlines():
        m = re.match(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\s*-\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\]\s*(.*)$", line)
        if not m:
            continue
        def sec(a, b, c):
            return int(a) * (3600 if c else 60) + int(b) * (60 if c else 1) + (int(c) if c else 0)
        start = sec(m.group(1), m.group(2), m.group(3))
        end = sec(m.group(4), m.group(5), m.group(6))
        out.append((start, end, m.group(7).strip()))
    return out


def fmt(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def load_outputs(outdir):
    manifest = {}
    for name in ("manifest.json",):
        p = outdir / name
        if p.exists():
            try:
                manifest = json.loads(read_text(p))
            except Exception:
                pass
    if not manifest.get("title") or is_source_reference(manifest["title"]):
        manifest["title"] = source_title(outdir)
    transcript = read_text(outdir / "transcript.txt") if (outdir / "transcript.txt").exists() else ""
    frames = []
    idx = outdir / "frames_index.txt"
    if idx.exists():
        for line in read_text(idx).splitlines():
            line = line.strip()
            if not line or "\t" not in line:
                continue
            ts, rel = line.split("\t", 1)
            fp = outdir / rel.replace("/", os.sep)
            if fp.exists():
                frames.append({"ts": ts.strip(), "path": rel.strip(), "file": fp})
    code_frames = {}
    cf = outdir / "code_frames.json"
    if cf.exists():
        try:
            code_frames = json.loads(read_text(cf))
        except Exception:
            pass
    return manifest, transcript, frames, code_frames


def find_title(args, manifest):
    if args.title and not is_source_reference(args.title):
        return args.title
    if manifest.get("title") and not is_source_reference(manifest["title"]):
        return manifest["title"]
    src = manifest.get("input") or args.input or ""
    if src and not is_source_reference(src):
        return Path(src).stem
    return "视频笔记"


def resolve_api_key(cli_key, cfg):
    if cli_key:
        return cli_key
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    if cfg.get("apiKey"):
        return str(cfg["apiKey"]).strip()
    if os.name == "nt":
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "[Environment]::GetEnvironmentVariable('DEEPSEEK_API_KEY','User')"],
                               capture_output=True, text=True, timeout=20)
            return (p.stdout or "").strip()
        except Exception:
            return ""
    return ""


def load_config():
    p = HERE / "config.json"
    if p.exists():
        try:
            return json.loads(read_text(p))
        except Exception:
            return {}
    return {}


def api_url(cli_url, cfg):
    url = cli_url or cfg.get("baseUrl") or os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL
    url = url.rstrip("/")
    if url.endswith("/messages"):
        return url
    return (url if url.endswith("/v1") else url + "/v1") + "/messages"


# ---------------------------------------------------------------- API

def call_api(messages, system, key, model, url, max_tokens, thinking=True, timeout=900, retries=3):
    signature = json.dumps([messages, system, model, url, max_tokens, thinking], ensure_ascii=False, sort_keys=True)
    cache = _CACHE_DIR / (hashlib.sha256(signature.encode()).hexdigest() + ".json") if _CACHE_DIR else None
    if cache and cache.exists():
        try:
            saved = json.loads(read_text(cache))
            _bump(_API_TOTAL, saved["usage"])
            log("[resume] 复用已完成的生成步骤")
            return saved["text"], saved["usage"]
        except (ValueError, KeyError, TypeError):
            pass
    text, usage = _request_api(messages, system, key, model, url, max_tokens, thinking, timeout, retries)
    _bump(_API_TOTAL, usage)
    if not text or usage.get("_stop") == "max_tokens":
        raise RuntimeError("模型输出为空或达到输出上限，请提高 token 上限后重试")
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp")
        tmp.write_text(json.dumps({"text": text, "usage": usage}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cache)
    return text, usage


def _request_api(messages, system, key, model, url, max_tokens, thinking=True, timeout=900, retries=3):
    body = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages}
    if not thinking:
        body["thinking"] = {"type": "disabled"}
    data = json.dumps(body).encode("utf-8")
    headers = {"content-type": "application/json", "anthropic-version": "2023-06-01",
               "authorization": "Bearer " + key, "x-api-key": key}
    last = ""
    for attempt in range(1, retries + 1):
        import urllib.error
        import urllib.request
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = json.loads(r.read().decode("utf-8"))
            texts = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
            usage = payload.get("usage") or {}
            usage["_stop"] = payload.get("stop_reason")
            return "\n".join(t for t in texts if t).strip(), usage
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            last = "HTTP %s %s" % (e.code, detail)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError("API 失败：" + last)
        except Exception as e:  # noqa: BLE001
            last = str(e)[:300]
            if attempt < retries:
                time.sleep(4 * attempt)
                continue
            raise RuntimeError("API 失败：" + last)
    raise RuntimeError("API 失败：" + last)


def image_block(fp):
    raw = fp.read_bytes()
    ext = fp.suffix.lower()
    media = "image/jpeg" if ext in (".jpg", ".jpeg") else ("image/png" if ext == ".png" else "image/jpeg")
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.b64encode(raw).decode()}}


# ---------------------------------------------------------------- 章节

def split_chapters(entries, duration):
    """按时间间隔切章：间隔 > 45s 或累计 > 200s 换章；少于 3 章则均分。"""
    if not entries:
        return [{"title": "内容", "start": 0.0, "end": float(duration or 0)}]
    chapters, cur = [], []
    cur_start = entries[0][0]
    for s, e, txt in entries:
        if cur and (s - cur[-1][1] > 45 or s - cur_start > 200):
            chapters.append({"title": "", "start": float(cur_start), "end": float(cur[-1][1]),
                             "entries": cur})
            cur, cur_start = [], s
        cur.append((s, e, txt))
    if cur:
        chapters.append({"title": "", "start": float(cur_start), "end": float(cur[-1][1]),
                         "entries": cur})
    if len(chapters) < 3 and len(entries) >= 6:
        n = min(8, max(3, len(entries) // 6))
        span = (entries[-1][1] - entries[0][0]) / n
        chapters = []
        for i in range(n):
            a = entries[0][0] + i * span
            b = entries[0][0] + (i + 1) * span
            sub = [(s, e, t) for s, e, t in entries if a <= s < b]
            if sub:
                chapters.append({"title": "", "start": a, "end": sub[-1][1], "entries": sub})
    return chapters


def pick_frames(frames, code_frames, start, end, limit):
    cands = [f for f in frames if start - 3 <= frame_sec(f["ts"]) <= end + 3]
    if not cands:
        return []
    def is_code(f):
        return f["path"].split("/")[-1] in code_frames
    cands.sort(key=lambda f: (not is_code(f), frame_sec(f["ts"])))
    return cands[:limit]


def frame_sec(ts):
    if isinstance(ts, (int, float)):
        return float(ts)
    if ":" not in str(ts):
        return float(ts)
    parts = [float(x) for x in str(ts).split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def transcript_slice(entries, start, end, max_chars=6000):
    buf = []
    for s, e, txt in entries:
        if e < start - 2 or s > end + 2:
            continue
        buf.append("[%s] %s" % (fmt(s), txt))
    text = "\n".join(buf)
    return text


# ---------------------------------------------------------------- 生成

def gen_outline(manifest, entries, frames, key, model, url, thinking):
    # 全文分块，避免长视频尾部被静默截断。
    chunks, chunk, size = [], [], 0
    for entry in entries:
        if chunk and size + len(entry[2]) > 7500:
            chunks.append(chunk)
            chunk, size = [], 0
        chunk.append(entry)
        size += len(entry[2]) + 20
    if chunk:
        chunks.append(chunk)
    result = []
    for chunk in chunks:
        chs = _gen_outline_chunk(manifest, chunk, frames, key, model, url, thinking)
        result.extend(chs or split_chapters(chunk, chunk[-1][1]))
    # 将章节边界连续化，所有转写内容均有归属。
    result.sort(key=lambda ch: ch["start"])
    result = [ch for i, ch in enumerate(result) if i == 0 or ch["start"] > result[i - 1]["start"]]
    if result:
        result[0]["start"] = entries[0][0]
        for i in range(len(result) - 1):
            result[i]["end"] = result[i + 1]["start"]
        result[-1]["end"] = max(float(manifest.get("duration_sec") or 0), entries[-1][1])
    return result


def _gen_outline_chunk(manifest, entries, frames, key, model, url, thinking):
    tr = "\n".join("[%s] %s" % (fmt(s), t) for s, e, t in entries)
    frame_list = "\n".join("%s @ [%s]" % (f["path"], f["ts"]) for f in frames)[:4000]
    prompt = ("视频：%s\n时长：%s\n\n【带时间码转写】\n%s\n\n【关键帧列表】\n%s\n\n请输出章节大纲 JSON。"
              % (manifest.get("input", ""), fmt(manifest.get("duration_sec") or 0), tr, frame_list))
    try:
        out, _ = call_api([{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                          OUTLINE_SYSTEM, key, model, url, 6000, thinking)
        out = re.sub(r"^```(?:json)?\s*|\s*```$", "", out.strip())
        data = json.loads(out)
        chs = data if isinstance(data, list) else data.get("chapters", [])
        result = []
        for ch in chs:
            start, end = float(frame_sec(ch["start"])), float(frame_sec(ch["end"]))
            if not (entries[0][0] <= start < end <= entries[-1][1] + 1):
                raise ValueError("章节时间超出本段材料范围")
            result.append({"title": str(ch.get("title") or "")[:60],
                           "start": start,
                           "end": end,
                           "frames": [str(x) for x in (ch.get("frames") or [])]})
        if result:
            return result
    except Exception as e:  # noqa: BLE001
        log("[outline] 失败，改用时间切章：%s" % str(e)[:200])
    return None


def chapter_prompt(ch, entries, frames, code_frames, quality, idx, total, manifest):
    imgs = []
    parts = []
    start, end = ch["start"], ch["end"]
    ts_frames = []
    for rel in ch.get("frames", []):
        for f in frames:
            if f["path"].endswith(rel.split("/")[-1]):
                ts_frames.append(f)
                break
    if not ts_frames:
        ts_frames = pick_frames(frames, code_frames, start, end, 4)
    info = ("视频：%s\n本章：第 %d/%d 章，时间 %s ~ %s\n"
            % (manifest.get("input", ""), idx, total, fmt(start), fmt(end)))
    tr = transcript_slice(entries, start, end)
    text = info + ("\n【本章转写】\n" + (tr or "（无转写，仅根据画面）") + "\n")
    for f in ts_frames:
        name = f["path"].split("/")[-1]
        ocr = code_frames.get(name, {}).get("ocr_text")
        text += "\n画面帧：%s @ [%s]\n" % (f["path"], f["ts"])
        if ocr:
            text += "OCR 参考（可能缺空格/错字，仅供辅助）：\n" + ocr[:2500] + "\n"
        imgs.append((f, ocr))
    text += "\n请输出本章的 Markdown 小节（以 '## ' 开头；不要重复整个视频标题）。"
    parts.append({"type": "text", "text": text})
    for f, _ in imgs:
        parts.append({"type": "text", "text": "\n帧 %s @ [%s]" % (f["path"], f["ts"])})
        parts.append(image_block(f["file"]))
    return [{"role": "user", "content": parts}]


def qa_chapter(ch, md_text, frames, code_frames, key, model, url):
    imgs = [f for f in frames]
    if not imgs:
        return md_text
    parts = [{"type": "text", "text": "该章笔记：\n\n" + md_text + "\n\n请核对其中代码块。"}]
    for f in imgs:
        name = f["path"].split("/")[-1]
        parts.append({"type": "text", "text": "\n帧 %s @ [%s]" % (f["path"], f["ts"])})
        parts.append(image_block(f["file"]))
        ocr = code_frames.get(name, {}).get("ocr_text")
        if ocr:
            parts.append({"type": "text", "text": "OCR 参考：\n" + ocr[:2500]})
    try:
        out, _ = call_api([{"role": "user", "content": parts}], QA_SYSTEM, key, model, url,
                          16000, True)
        return out.strip() if out.strip() else md_text
    except Exception as e:  # noqa: BLE001
        log("[qa] 跳过：%s" % str(e)[:150])
        _WARNINGS.append("代码复核未完成，可重试任务继续复核")
        return md_text


def make_extras(notes_md, key, model, url):
    try:
        out, _ = call_api([{"role": "user", "content": [{"type": "text", "text":
                           "视频笔记内容：\n\n" + notes_md + "\n\n请按系统要求补充必要的术语与自测。"}]}],
                          EXTRA_SYSTEM, key, model, url, 6000, False)
        out = out.strip()
    except Exception as e:  # noqa: BLE001
        log("[extras] 跳过：%s" % str(e)[:150])
        _WARNINGS.append("术语表和自测问题未完成")
        return ""
    return out


# ---------------------------------------------------------------- 输出

def ensure_pdf(outdir, title, log_prefix=""):
    ps = HERE / "md2pdf.ps1"
    md = outdir / "notes.md"
    if not ps.exists() or not md.exists():
        return ""
    pdf = outdir / (safe_name(title) + ".pdf")
    pending = outdir / (safe_name(title) + ".render.pdf")
    pending.unlink(missing_ok=True)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
           "-Md", str(md), "-Title", title, "-Pdf", str(pending.resolve())]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env={**os.environ, 'VIDEONOTES_PYTHON': sys.executable})
    if p.returncode == 0 and pending.exists() and pending.stat().st_size > 2048:
        try:
            os.replace(pending, pdf)
        except PermissionError as e:
            raise RuntimeError("原 PDF 正被占用，请关闭后重新导出；新版已保留在 %s" % pending) from e
        return str(pdf)
    pending.unlink(missing_ok=True)
    log("[pdf] 失败：%s" % ((p.stdout or "") + (p.stderr or ""))[-800:])
    return ""


def ensure_docx(outdir, title):
    md = outdir / "notes.md"
    if not md.exists():
        return ""
    docx = outdir / (safe_name(title) + ".docx")
    pandoc = find_executable('pandoc', 'VIDEONOTES_PANDOC')
    if not pandoc:
        log("[docx] 未找到 pandoc，跳过 Word 导出")
        return ""
    ref = HERE / "templates" / "reference.docx"
    cmd = [pandoc, str(md), "-f", "markdown+pipe_tables+fenced_code_blocks+tex_math_dollars",
           "-o", str(docx), "--metadata", "title=" + title]
    if ref.exists():
        cmd += ["--reference-doc", str(ref)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if docx.exists() and docx.stat().st_size > 2048:
        return str(docx)
    log("[docx] 失败：%s" % ((p.stderr or "")[-800:]))
    return ""


# ---------------------------------------------------------------- main


def _pump(frames_prog, base, span, label, stop):
    while not stop.is_set():
        try:
            data = json.loads(Path(frames_prog).read_text(encoding="utf-8"))
            pct = float(data.get("percent") or 0)
            stage = str(data.get("stage") or "")
            msg = str(data.get("msg") or "")
            lab = ("%s：%s" % (stage, msg)) if msg else stage
            prog("prepare", base + span * pct / 100.0, ((label + " ") if label else "") + lab)
        except Exception:
            pass
        stop.wait(0.8)


def run_frames(args, outdir, base=1.0, span=66.0, label=""):
    """跑 video2frames，把子进度折算到 [base, base+span]。"""
    outdir = Path(outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    if args.reuse and (outdir / "manifest.json").exists():
        manifest, transcript, frames, _ = load_outputs(outdir)
        if manifest.get("input") == args.input and transcript.strip() and frames:
            log("[resume] 复用已准备的视频素材")
            return outdir
    prog("prepare", base, (label + " 准备素材").strip(), True)
    cmd = [sys.executable, str(HERE / "video2frames.py"), "-i", args.input, "-o", str(outdir)]
    for flag, val in (("--max-height", args.max_height), ("--scene", args.scene),
                      ("--interval", args.interval), ("--max-frames", args.max_frames),
                      ("--width", args.width), ("--asr-model", args.asr_model),
                      ("--lang", args.lang)):
        if val is not None:
            cmd += [flag, str(val)]
    for flag, on in (("--no-asr", args.no_asr), ("--no-subtitle", args.no_subtitle),
                     ("--no-code-ocr", args.no_code_ocr), ("--reuse", args.reuse)):
        if on:
            cmd.append(flag)
    if args.model_dir:
        os.environ["VIDEONOTES_MODEL_DIR"] = args.model_dir
    cookies = args.cookies or (str(DEFAULT_COOKIES) if DEFAULT_COOKIES.exists() else "")
    if cookies:
        cmd += ["--cookies", cookies]
    frames_prog = outdir / "progress_frames.json"
    cmd += ["--progress-json", str(frames_prog)]
    env = dict(os.environ)
    env["VIDEONOTES_PROGRESS"] = str(frames_prog)
    stop = threading.Event()
    th = threading.Thread(target=_pump, args=(frames_prog, base, span, label, stop), daemon=True)
    p = subprocess.Popen(cmd, cwd=str(HERE), env=env)
    th.start()
    try:
        rc = p.wait()
    finally:
        stop.set()
        th.join(timeout=2)
    if rc != 0:
        raise RuntimeError("video2frames.py 失败（exit %d）" % rc)
    return outdir


def _bump(store, usage):
    for k in store:
        store[k] += int((usage or {}).get(k) or 0)


def copy_prepared(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        return
    if not (source / "manifest.json").exists():
        raise ValueError("素材目录缺少 manifest.json")
    target.mkdir(parents=True, exist_ok=True)
    for name in ("manifest.json", "transcript.txt", "transcript.srt", "frames_index.txt", "code_frames.json"):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    if (source / "frames").is_dir():
        shutil.copytree(source / "frames", target / "frames", dirs_exist_ok=True)


def make_notes(args, cfg, q, outdir, key, model, url, base, span):
    """对已准备素材的单个目录生成笔记 Markdown，返回 dict。"""
    manifest, transcript, frames, code_frames = load_outputs(outdir)
    if getattr(args, "merge", False):
        # 整合模式下每个视频用自己的标题，避免全部用整合任务的标题
        title = find_title(argparse.Namespace(title=None, input=args.input), manifest)
    else:
        title = find_title(args, manifest)
    entries = parse_entries(transcript)
    duration = manifest.get("duration_sec") or (entries[-1][1] if entries else 0)
    log("[note] 《%s》转写 %d 字符；关键帧 %d；代码帧 %d"
        % (title, len(transcript), len(frames), len(code_frames)))
    usage_total = {"input_tokens": 0, "output_tokens": 0}
    chapters_md = []
    outline = []
    if entries and (q["chapters"] or len(transcript) > 12000):
        prog("note-outline", base + span * 0.05, "生成章节大纲", True)
        outline = gen_outline(manifest, entries, frames, key, model, url, q["thinking"]) \
            or split_chapters(entries, duration)
        bounded = []
        for ch in outline:
            sub = [x for x in entries if ch["start"] <= x[0] < ch["end"]]
            group, size = [], 0
            for entry in sub:
                if group and size + len(entry[2]) > 6000:
                    bounded.append({**ch, "start": group[0][0], "end": entry[0], "frames": []})
                    group, size = [], 0
                group.append(entry)
                size += len(entry[2]) + 20
            if group:
                bounded.append({**ch, "start": group[0][0], "frames": []})
        outline = bounded or outline
        (outdir / "outline.json").write_text(json.dumps(outline, ensure_ascii=False, indent=2), encoding="utf-8")
        for ch in outline:
            ch["entries"] = [(s, e, t) for s, e, t in entries if s <= ch["end"] and e >= ch["start"]]
            if not ch.get("title"):
                first = ch["entries"][0][2] if ch["entries"] else ""
                ch["title"] = re.sub(r"[。！？!?].*$", "", first)[:20] or "内容"
        total = len(outline) or 1
        prog("note-chapters", base + span * 0.08, "共 %d 章，逐章生成" % total, True)
        for i, ch in enumerate(outline, 1):
            ch["title"] = ch.get("title") or "内容"
            if not ch.get("frames"):
                ch["frames"] = [f["path"] for f in pick_frames(
                    frames, code_frames, ch["start"], ch["end"],
                    max(1, q["max_images"] // total))]
            ch["limit"] = max(2, q["max_images"] // total)
            prog("note-chapters", base + span * (0.08 + 0.6 * (i - 1) / total),
                 "生成第 %d/%d 章：%s" % (i, total, ch["title"]))
            log("[chapter] %d/%d %s [%s-%s]" % (i, total, ch["title"], fmt(ch["start"]), fmt(ch["end"])))
            try:
                msgs = chapter_prompt(ch, entries, frames, code_frames, q, i, total, manifest)
                md, usage = call_api(msgs, NOTE_SYSTEM, key, model, url, q["max_tokens"], q["thinking"])
                _bump(usage_total, usage)
            except Exception as e:  # noqa: BLE001
                log("[chapter] 失败：%s" % str(e)[:200])
                raise RuntimeError("第 %d 章生成失败；重试时会复用已完成步骤：%s" % (i, e)) from e
            md = clean_notes(md)
            checkpoint = outdir / "chapters"
            checkpoint.mkdir(exist_ok=True)
            (checkpoint / ("%03d.md" % i)).write_text(md, encoding="utf-8")
            chapters_md.append(md.strip())
        if q["qa"]:
            rounds = max(1, int(q["qa"]))
            for r in range(rounds):
                for idx, ch in enumerate(outline):
                    if idx >= len(chapters_md) or "```" not in chapters_md[idx]:
                        continue
                    prog("note-qa", base + span * (0.7 + 0.25 * (r + idx / max(1, len(outline))) / rounds),
                         "代码复核 %d/%d" % (idx + 1, len(outline)))
                    ch_frames = pick_frames(frames, code_frames, ch["start"], ch["end"],
                                            max(2, q["max_images"] // max(1, len(outline)) + 2))
                    log("[qa] 复核第 %d 章（第 %d 轮）" % (idx + 1, r + 1))
                    chapters_md[idx] = qa_chapter(ch, chapters_md[idx], ch_frames, code_frames, key, model, url)
    else:
        prog("note-chapters", base + span * 0.2, "生成笔记", True)
        sel = frames
        if len(sel) > q["max_images"]:
            step = len(sel) / float(q["max_images"])
            sel = [sel[min(int(i * step), len(sel) - 1)] for i in range(q["max_images"])]
        parts = [{"type": "text", "text":
                  "视频：%s\n时长：%s\n\n【转写】\n%s\n\n请输出完整视频笔记。"
                  % (manifest.get("input", ""), fmt(duration), transcript or "（无转写，仅根据画面）")}]
        for f in sel:
            parts.append({"type": "text", "text": "\n帧 %s @ [%s]" % (f["path"], f["ts"])})
            parts.append(image_block(f["file"]))
        md, usage = call_api([{"role": "user", "content": parts}], NOTE_SYSTEM, key, model, url,
                             q["max_tokens"], q["thinking"])
        _bump(usage_total, usage)
        chapters_md = [md.strip()]
    return {"title": title, "manifest": manifest, "duration": duration,
            "frames": len(frames), "code_frames": len(code_frames),
            "md": "\n\n".join(chapters_md), "usage": usage_total}


def finalize(args, outdir, title, notes, usage_total, q, model, videos, started, mode, key, url):
    outdir = Path(outdir)
    if not title or is_source_reference(title):
        title = source_title(outdir) or '视频笔记'
    prog("note-extras", 92, "生成术语表与复盘清单", True)
    try:
        extras = make_extras(notes, key, model, url)
        if extras:
            notes += "\n\n" + extras
    except Exception as e:  # noqa: BLE001
        log("[extras] 跳过：%s" % str(e)[:200])
    notes = document_title(clean_notes(notes), title)
    usage_total = dict(_API_TOTAL)
    md_path = outdir / "notes.md"
    md_path.write_text(notes + "\n", encoding="utf-8")
    (outdir / "api_usage.json").write_text(
        json.dumps({"model": model, "usage": usage_total, "quality": args.quality,
                    "mode": mode, "videos": len(videos),
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    log("[write] %s（%d 字符）" % (md_path, len(notes)))
    formats = [x.strip().lower() for x in args.format.split(",") if x.strip()]
    outputs = [str(md_path)]
    if "pdf" in formats:
        prog("render", 95, "打印 PDF", True)
        try:
            pdf = ensure_pdf(outdir, title)
        except Exception as e:
            log("[pdf] " + str(e)[:200])
            pdf = ""
        if pdf:
            outputs.append(pdf)
        else:
            _WARNINGS.append("PDF 导出失败，Markdown 已保存")
    if "docx" in formats:
        prog("render", 97, "生成 Word", True)
        try:
            docx = ensure_docx(outdir, title)
        except Exception as e:
            log("[docx] " + str(e)[:200])
            docx = ""
        if docx:
            outputs.append(docx)
        else:
            _WARNINGS.append("Word 导出失败，Markdown 已保存")
    task = {"mode": mode, "title": title, "quality": args.quality, "format": args.format,
            "started": started, "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
            "usage": usage_total, "videos": videos, "outputs": outputs, "outdir": str(outdir),
            "status": "partial" if _WARNINGS else "done", "warnings": list(dict.fromkeys(_WARNINGS))}
    (outdir / "task.json").write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    prog(task["status"], 100, "部分完成：" + "；".join(task["warnings"]) if _WARNINGS else "完成", True)
    print("\n== 完成 ==")
    for o in outputs:
        print("输出：", o)
    return task


def main():
    global _CACHE_DIR
    _API_TOTAL.update(input_tokens=0, output_tokens=0)
    _WARNINGS.clear()
    ap = argparse.ArgumentParser(description="视频 → 带代码块的 Markdown/PDF/Word 笔记（DeepSeek API）")
    ap.add_argument("-i", "--input", action="append", metavar="INPUT", help="视频链接/BV号/本地路径，可重复")
    ap.add_argument("--inputs-json", help="从 JSON 数组文件读取多个视频，避免批量输入超出命令长度限制")
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--from-out", action="store_true", help="输入已是 video2frames 输出目录")
    ap.add_argument("--format", default="pdf", help="pdf|docx|md，可逗号组合")
    ap.add_argument("--quality", default="balanced", choices=list(QUALITY))
    ap.add_argument("--title")
    ap.add_argument("--merge", action="store_true", help="多个 -i 合并生成一份整合笔记")
    ap.add_argument("--api-key")
    ap.add_argument("--model")
    ap.add_argument("--base-url")
    ap.add_argument("--no-thinking", action="store_true")
    ap.add_argument("--max-tokens", type=int, default=0)
    ap.add_argument("--max-images", type=int, default=0)
    ap.add_argument("--progress-json", default=os.environ.get("VIDEONOTES_PROGRESS", ""))
    ap.add_argument("--prepare-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-height", type=int)
    ap.add_argument("--scene", type=float)
    ap.add_argument("--interval", type=float)
    ap.add_argument("--max-frames", type=int)
    ap.add_argument("--width", type=int)
    ap.add_argument("--asr-model")
    ap.add_argument("--lang")
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--no-subtitle", action="store_true")
    ap.add_argument("--no-code-ocr", action="store_true")
    ap.add_argument("--cookies")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--model-dir")
    ap.add_argument("--no-cache", action="store_true", help="不复用已完成的模型调用")
    args = ap.parse_args()
    inputs = args.input or []
    if args.inputs_json:
        extra = json.loads(Path(args.inputs_json).read_text(encoding='utf-8'))
        if not isinstance(extra, list) or not all(isinstance(item, str) and item.strip() for item in extra):
            ap.error('--inputs-json 必须是非空视频路径/链接组成的数组')
        inputs.extend(extra)
    outdir = Path(args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    _CACHE_DIR = None if args.no_cache else outdir / ".checkpoints"
    if args.from_out and not inputs:
        inputs = [str(outdir)]
    set_progress_path(args.progress_json)
    cfg = load_config()
    q = dict(QUALITY[args.quality])
    if args.no_thinking:
        q["thinking"] = False
    if args.max_tokens:
        q["max_tokens"] = args.max_tokens
    if args.max_images:
        q["max_images"] = args.max_images

    if not args.from_out and not inputs:
        ap.error("需要 -i，或 --from-out")
    if args.merge and len(inputs) < 2:
        ap.error("--merge 需要至少两个 -i")
    started = time.strftime("%Y-%m-%d %H:%M:%S")

    key = resolve_api_key(args.api_key, cfg)
    if not key and not (args.prepare_only or args.dry_run):
        prog("error", 100, "缺少 API Key", True)
        sys.exit("[ERR] 没有 API Key：--api-key / 环境变量 DEEPSEEK_API_KEY / config.json apiKey")
    model = args.model or cfg.get("model") or DEFAULT_MODEL
    url = api_url(args.base_url, cfg)

    if len(inputs) > 1 and not args.merge:
        original = copy.deepcopy(args)
        tasks = []
        for i, inp in enumerate(inputs, 1):
            child = copy.deepcopy(original)
            child.input = inp
            sub = outdir / ("%02d-%s" % (i, re.sub(r'[^\w\u4e00-\u9fff-]', '-', Path(inp).stem)[:50]))
            child.title = None
            _CACHE_DIR = None if args.no_cache else sub / ".checkpoints"
            _API_TOTAL.update(input_tokens=0, output_tokens=0)
            _WARNINGS.clear()
            sub.mkdir(parents=True, exist_ok=True)
            if not args.from_out:
                run_frames(child, sub)
            elif Path(inp).resolve() != sub.resolve():
                copy_prepared(inp, sub)
            if args.prepare_only or args.dry_run:
                continue
            r = make_notes(child, cfg, q, sub, key, model, url, 70, 22)
            tasks.append(finalize(child, sub, r["title"], "# " + r["title"] + "\n\n" + r["md"],
                                  r["usage"], q, model, [{"input": inp, "title": r["title"]}],
                                  started, "single", key, url))
        return tasks

    # ---------- 多视频：整合成一份笔记 ----------
    if args.merge and len(inputs) >= 2:
        n = len(inputs)
        for i, inp in enumerate(inputs, 1):
            args.input = inp
            target = outdir / ("src-%02d" % i)
            if args.from_out:
                copy_prepared(inp, target)
            else:
                run_frames(args, target, 1 + 58.0 * (i - 1) / n, 58.0 / n, "视频 %d/%d：" % (i, n))
        if args.prepare_only or args.dry_run:
            prog("done", 100, "素材准备完成", True)
            return
        usage_total = {"input_tokens": 0, "output_tokens": 0}
        parts = []
        videos = []
        for i, inp in enumerate(inputs, 1):
            args.input = inp
            prog("note-chapters", 60 + 24.0 * (i - 1) / n,
                 "视频 %d/%d：生成分章笔记" % (i, n), True)
            r = make_notes(args, cfg, q, outdir / ("src-%02d" % i), key, model, url,
                           60 + 24.0 * (i - 1) / n, 24.0 / n)
            _bump(usage_total, r["usage"])
            videos.append({"index": i, "input": inp, "title": r["title"],
                           "duration": r["duration"], "frames": r["frames"],
                           "code_frames": r["code_frames"]})
            parts.append("## 视频 %d：《%s》\n\n%s" % (i, r["title"],
                                                      re.sub(r"(?m)^(#{2,5}) ", r"#\1 ", r["md"])))
        merged = ""
        prog("note-merge", 86, "多视频整合分析", True)
        try:
            text = "\n\n".join("### 视频%d《%s》\n%s" % (v["index"], v["title"], r["md"])
                               for v, r in zip(videos, [{"md": parts[k].split("\n\n", 1)[-1]}
                                                        for k in range(len(parts))]))
            md, usage = call_api([{"role": "user", "content": [{"type": "text", "text": text}]}],
                                 MERGE_SYSTEM, key, model, url, q["max_tokens"], q["thinking"])
            _bump(usage_total, usage)
            merged = md.strip()
            # 模型常自带「综合整合」标题，去掉开头的标题行，避免与下面统一标题重复
            lines = merged.splitlines()
            while lines and lines[0].lstrip().startswith("#"):
                lines.pop(0)
            merged = "\n".join(lines).strip()
        except Exception as e:  # noqa: BLE001
            log("[merge] 整合失败：%s" % str(e)[:200])
            _WARNINGS.append("跨视频整合未完成，分视频笔记已保留")
        title = args.title or (videos[0]['title'] + ' 等 %d 个视频' % len(videos))
        header = "# %s\n\n" % title
        notes = header + "\n\n".join(parts)
        if merged:
            notes += "\n\n## 🧩 综合整合：这些视频怎么串起来\n\n" + merged
        return finalize(args, outdir, title, notes, usage_total, q, model, videos, started, "merged", key, url)

    # ---------- 单视频 ----------
    args.input = inputs[0]
    if args.from_out and Path(args.input).resolve() != outdir:
        copy_prepared(args.input, outdir)
    if not args.from_out:
        run_frames(args, outdir, 1, 66)
        merge_progress(outdir, args.progress_json)
    prog("note-outline", 70, "素材就绪，开始分析", True)
    if args.prepare_only:
        log("[done] 仅准备素材")
        prog("done", 100, "素材准备完成", True)
        return
    if args.dry_run:
        manifest, transcript, frames, code_frames = load_outputs(outdir)
        entries = parse_entries(transcript)
        log("[dry-run] entries=%d frames=%d title=%s model=%s quality=%s"
            % (len(entries), len(frames), find_title(args, manifest), model, args.quality))
        prog("done", 100, "dry-run", True)
        return
    r = make_notes(args, cfg, q, outdir, key, model, url, 70, 22)
    title = r["title"]
    notes = "# %s\n\n%s" % (title, r["md"])
    return finalize(args, outdir, title, notes, r["usage"], q, model,
                    [{"index": 1, "input": args.input, "title": title, "duration": r["duration"],
                      "frames": r["frames"], "code_frames": r["code_frames"]}],
                    started, "single", key, url)


def merge_progress(outdir, target):
    """把 video2frames 的粗粒度进度折算进总进度（0-70%）。"""
    src = Path(outdir) / "progress_frames.json"
    if not target or not src.exists():
        return
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception:
        return
    pct = data.get("percent", 0) or 0
    data["percent"] = round(pct * 0.7, 1)
    data["stage"] = "prepare-" + str(data.get("stage", ""))
    try:
        tmp = target + ".tmp"
        Path(tmp).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        pass


if __name__ == "__main__":
    main()
