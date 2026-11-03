#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""video2frames.py —— 视频 -> 转写稿 + 关键帧 + 时间戳索引 + 代码帧 OCR

输入：B站链接 / BV号 / yt-dlp 支持的 URL / 本地视频文件
输出（<outdir>）：
    source.*            在线视频下载文件（--reuse 时复用）
    transcript.txt      转写稿（[mm:ss - mm:ss] 行）
    transcript.srt      ASR 生成的字幕
    frames/f_XXXX_HH-MM.jpg  关键帧（原生分辨率）
    frames_index.txt    时间<TAB>帧相对路径
    manifest.json       元信息
    code_frames.json    疑似代码帧 + OCR 文本（有 rapidocr 时）
    progress.json       进度（--progress-json 指定时）

用法示例：
    python video2frames.py -i "BV1VkgK6NEZS" -o work/demo
    python video2frames.py -i "D:\\录屏\\课.mp4" -o work/lesson --asr-model small
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from .naming import source_title
from .paths import PROJECT_ROOT
from .runtime import find_executable

VIDEO_EXTS = (".mp4", ".mkv", ".webm", ".flv", ".mov", ".m4v", ".ts", ".avi")

CODE_TOKENS = re.compile(
    r"(^\s*(def|class|import|from|return|if|for|while|try|except|with|async|await|"
    r"const|let|var|function|public|private|static|void|int|float|double|char|"
    r"#include|#define|template|namespace|using|package|fn|impl|pub|struct|enum|mod)\b)"
    r"|(\b[a-zA-Z_][\w]*\s*\()|([{};]\s*$)|(=>|->|::|==|!=|<=|>=|\+\+|--)|"
    r"(\b(print|printf|console\.log|System\.out|fmt\.Print|echo)\b)")


# ---------------------------------------------------------------- 进度

def make_progress(path):
    state = {"t": 0.0, "stage": ""}

    def prog(stage, percent, msg="", **extra):
        if not path:
            return
        now = time.time()
        if (not extra.get("force")) and stage == state["stage"] and now - state["t"] < 0.3:
            return
        state["t"], state["stage"] = now, stage
        data = {"stage": stage, "percent": round(max(0.0, min(100.0, percent)), 1),
                "msg": msg, "ts": now, **extra}
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            pass

    return prog


# ---------------------------------------------------------------- 基础

def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", **kw)


def find_tool(name, env=None):
    return find_executable(name, env)


def is_url(s):
    return re.match(r"^https?://", s) is not None


def fmt_ts(sec):
    sec = int(sec)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def srt_ts(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def find_video(outdir):
    cands = [f for f in glob.glob(os.path.join(outdir, "source.*")) if f.lower().endswith(VIDEO_EXTS)]
    return max(cands, key=os.path.getsize) if cands else None


def dhash(img_or_path, size=8):
    """64-bit difference hash；失败返回 None。"""
    try:
        from PIL import Image
        im = img_or_path if hasattr(img_or_path, "convert") else Image.open(img_or_path)
        g = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(g.getdata())
        bits = 0
        k = 0
        for y in range(size):
            row = px[y * (size + 1):(y + 1) * (size + 1)]
            for x in range(size):
                if row[x] > row[x + 1]:
                    bits |= (1 << k)
                k += 1
        return bits
    except Exception:
        return None


def hamming(a, b):
    if a is None or b is None:
        return 64
    return bin(a ^ b).count("1")


def pixel_change_ratio(a, b):
    """两帧缩略图的像素差 > 25 的比例（0~1）。"""
    try:
        from PIL import Image, ImageChops
        im1 = a.convert("L").resize((320, 180), Image.BILINEAR) if hasattr(a, "convert") \
            else Image.open(a).convert("L").resize((320, 180))
        im2 = b.convert("L").resize((320, 180), Image.BILINEAR) if hasattr(b, "convert") \
            else Image.open(b).convert("L").resize((320, 180))
        diff = ImageChops.difference(im1, im2)
        px = list(diff.getdata())
        return sum(1 for v in px if v > 25) / float(len(px))
    except Exception:
        return 1.0


# ---------------------------------------------------------------- 下载

def download_video(url, outdir, max_height, cookies, reuse, no_subtitle, prog):
    ytdlp = find_tool("yt-dlp", "VIDEONOTES_YTDLP")
    if not ytdlp:
        print("[ERR] 找不到 yt-dlp，请先安装或在 PATH 中暴露", file=sys.stderr)
        return None
    existing = find_video(outdir)
    if reuse and existing:
        print("[yt-dlp] 复用已有视频: %s" % existing)
        prog("download", 30, "复用已有视频")
        return existing
    fmt = (f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
           f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b")
    args = [ytdlp, "--no-playlist", "--no-warnings", "--no-mtime", "--newline",
            "-f", fmt, "--merge-output-format", "mp4", "--write-info-json",
            "-o", os.path.join(outdir, "source.%(ext)s")]
    if not no_subtitle:
        args += ["--write-subs", "--write-auto-subs",
                 "--sub-langs", "zh-Hans,zh-CN,zh,en", "--convert-subs", "srt"]
    if cookies and os.path.exists(cookies):
        args += ["--cookies", cookies]
    args.append(url)
    print("[yt-dlp] 下载中（清晰度上限 %dp）..." % max_height)
    prog("download", 5, "开始下载", force=True)
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail = []
    for line in p.stdout:
        line = line.rstrip()
        if not line:
            continue
        tail.append(line)
        tail = tail[-40:]
        m = re.search(r"\[download\]\s+([0-9.]+)%", line)
        if m:
            prog("download", 5 + float(m.group(1)) * 0.25, "下载 %s%%" % m.group(1))
        elif "[Merger]" in line or "Merging" in line:
            prog("download", 29, "合并音视频")
    rc = p.wait()
    if rc != 0:
        print("[yt-dlp] 退出码 %d：\n%s" % (rc, "\n".join(tail[-12:])), file=sys.stderr)
        prog("download", 30, "下载失败（尝试复用/本地文件）")
    return find_video(outdir)


def probe_duration(video):
    ffprobe = find_tool("ffprobe", "VIDEONOTES_FFPROBE")
    if not ffprobe:
        return 0.0, 0, 0
    p = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-show_entries", "stream=width,height", "-of", "json", video])
    try:
        data = json.loads(p.stdout or "{}")
        dur = float(data.get("format", {}).get("duration") or 0)
        w = h = 0
        for st in data.get("streams", []):
            if st.get("width"):
                w, h = int(st["width"]), int(st.get("height") or 0)
                break
        return dur, w, h
    except Exception:
        return 0.0, 0, 0


# ---------------------------------------------------------------- 字幕 / ASR

def srt_to_transcript(srt_path, out_txt):
    if not os.path.exists(srt_path):
        return 0
    with open(srt_path, "r", encoding="utf-8", errors="replace") as fh:
        blocks = re.split(r"\n\s*\n", fh.read())
    lines = []
    for b in blocks:
        rows = [r.strip() for r in b.splitlines() if r.strip()]
        if len(rows) < 2:
            continue
        m = re.search(r"(\d{2}):(\d{2}):(\d{2})[,.]\d{3}\s*-->\s*(\d{2}):(\d{2}):(\d{2})", b)
        if not m:
            m2 = re.search(r"(\d{2}):(\d{2})[,.]\d{3}\s*-->\s*(\d{2}):(\d{2})", b)
            if not m2:
                continue
            start = int(m2.group(1)) * 60 + int(m2.group(2))
            end = int(m2.group(3)) * 60 + int(m2.group(4))
        else:
            start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            end = int(m.group(4)) * 3600 + int(m.group(5)) * 60 + int(m.group(6))
        text = " ".join(rows[2:]).strip() if rows[0].isdigit() else " ".join(rows[1:]).strip()
        if text:
            lines.append("[%s - %s] %s" % (fmt_ts(start), fmt_ts(end), text))
    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(lines)


def find_subtitle(outdir):
    for pat in ("*.srt", "*.vtt"):
        for f in sorted(glob.glob(os.path.join(outdir, pat))):
            if os.path.basename(f).startswith("source"):
                return f
    return None


def transcribe(video, outdir, model_size, lang, keep_audio, prog):
    ffmpeg = find_tool("ffmpeg", "VIDEONOTES_FFMPEG")
    if not ffmpeg:
        print("[ERR] 找不到 ffmpeg", file=sys.stderr)
        return 0, ""
    audio = os.path.join(outdir, "audio.wav")
    if not os.path.exists(audio):
        print("[ffmpeg] 提取音频 ...")
        prog("asr", 32, "提取音频", force=True)
        p = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
                 "-vn", "-ac", "1", "-ar", "16000", "-f", "wav", audio])
        if p.returncode != 0:
            print("[ffmpeg] 提取音频失败: " + (p.stderr or "")[-2000:], file=sys.stderr)
            return 0, ""
    dur = 0.0
    try:
        dur, _, _ = probe_duration(audio)
    except Exception:
        pass
    print("[asr] faster-whisper 模型=%s（首次使用需下载，可通过 HF_ENDPOINT 配置下载源）..." % model_size)
    prog("asr", 33, "加载 ASR 模型 %s" % model_size, force=True)
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    model_dir = os.environ.get("VIDEONOTES_MODEL_DIR") or os.path.join(
        str(PROJECT_ROOT), "models")
    os.makedirs(model_dir, exist_ok=True)
    try:
        from faster_whisper import WhisperModel
        device = os.environ.get("VIDEONOTES_ASR_DEVICE", "cpu")
        compute = os.environ.get("VIDEONOTES_ASR_COMPUTE", "int8")
        model = WhisperModel(model_size, device=device, compute_type=compute,
                             download_root=model_dir)
        segments, info = model.transcribe(
            audio, language=lang, vad_filter=True, beam_size=5,
            initial_prompt="以下是普通话内容，涉及计算机、人工智能、大语言模型、代码与编程术语。",
            condition_on_previous_text=False)
        lines, srt = [], []
        n = 0
        for seg in segments:
            n += 1
            lines.append("[%s - %s] %s" % (fmt_ts(seg.start), fmt_ts(seg.end), seg.text.strip()))
            srt.append("%d\n%s --> %s\n%s\n" % (n, srt_ts(seg.start), srt_ts(seg.end), seg.text.strip()))
            if dur and seg.end > 0:
                prog("asr", 33 + min(1.0, seg.end / dur) * 27,
                     "转写 %s / %s" % (fmt_ts(seg.end), fmt_ts(dur)))
        with open(os.path.join(outdir, "transcript.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        with open(os.path.join(outdir, "transcript.srt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(srt))
        if not keep_audio:
            try:
                os.remove(audio)
            except Exception:
                pass
        print("[asr] 完成，%d 段" % n)
        return n, model_size
    except Exception as e:  # noqa: BLE001
        print("[asr] 失败：%s: %s" % (type(e).__name__, str(e)[:500]), file=sys.stderr)
        return 0, ""


# ---------------------------------------------------------------- 抽帧

def scan_frames(video, scan_dir, fps, width, prog):
    ffmpeg = find_tool("ffmpeg", "VIDEONOTES_FFMPEG")
    os.makedirs(scan_dir, exist_ok=True)
    pattern = os.path.join(scan_dir, "s_%05d.jpg")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
           "-vf", "fps=%s,scale=%d:-1" % (fps, width), "-q:v", "4", pattern]
    p = run(cmd)
    if p.returncode != 0:
        print("[frames] 扫描抽帧失败: " + (p.stderr or "")[-1500:], file=sys.stderr)
    return sorted(glob.glob(os.path.join(scan_dir, "s_*.jpg")))


def pick_timestamps(files, fps, interval, scene, max_frames, prog):
    """按画面变化 + 间隔选点，并用 dHash 去重。返回 [(time, scanfile)]。"""
    selected = []
    last_keep_t = -1e9
    last_hash = None
    prev_img = None
    total = max(1, len(files))
    for i, f in enumerate(files):
        t = i / float(fps)
        if i % 20 == 0:
            prog("frames", 62 + min(1.0, i / total) * 10,
                 "分析画面 %d/%d" % (i + 1, total))
        try:
            from PIL import Image
            img = Image.open(f)
            h = dhash(img)
            change = pixel_change_ratio(img, prev_img) if prev_img is not None else 1.0
            prev_img = img
        except Exception:
            continue
        is_scene = change >= scene
        is_interval = (t - last_keep_t) >= interval
        same = hamming(h, last_hash) <= 4
        if (is_scene or is_interval) and not same:
            selected.append((t, f))
            last_keep_t = t
            last_hash = h
    if files and not selected:
        selected = [(0.0, files[0])]
    if files:
        if selected[0][0] > 0.5:
            selected.insert(0, (0.0, files[0]))
        if files[-1] not in [f for _, f in selected]:
            selected.append(((len(files) - 1) / float(fps), files[-1]))
    if len(selected) > max_frames:
        step = len(selected) / float(max_frames)
        picked, seen, out = [], set(), []
        for i in range(max_frames):
            picked.append(selected[min(int(i * step), len(selected) - 1)])
        for t, f in picked:
            if f not in seen:
                seen.add(f)
                out.append((t, f))
        selected = out
    return selected


def extract_selected(video, frames_dir, pairs, width, prog):
    ffmpeg = find_tool("ffmpeg", "VIDEONOTES_FFMPEG")
    os.makedirs(frames_dir, exist_ok=True)
    out = []
    total = max(1, len(pairs))
    for i, (t, scanfile) in enumerate(pairs):
        name = "f_%04d_%s.jpg" % (i + 1, fmt_ts(t).replace(":", "-"))
        dst = os.path.join(frames_dir, name)
        scale = ("scale=%d:-1" % width) if width else "scale=iw:ih"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", "%.3f" % t,
               "-i", video, "-frames:v", "1", "-vf", scale, "-q:v", "2", dst]
        p = run(cmd)
        if p.returncode != 0 or not os.path.exists(dst):
            print("[frames] 抽帧失败 t=%s: %s" % (t, (p.stderr or "")[-300:]), file=sys.stderr)
            continue
        out.append((t, dst))
        if i % 5 == 0:
            prog("frames", 72 + min(1.0, i / total) * 10, "抽取关键帧 %d/%d" % (i + 1, total))
    return out


def detect_code_frames(pairs, outdir, prog, enabled=True):
    """用 rapidocr 识别疑似代码帧，写 code_frames.json；返回 {帧名: {...}}。"""
    if not enabled:
        return {}
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return {}
    try:
        engine = RapidOCR()
    except Exception:
        return {}
    result = {}
    total = max(1, len(pairs))
    for i, (t, path) in enumerate(pairs):
        prog("code-ocr", 85 + min(1.0, i / total) * 5, "识别代码帧 %d/%d" % (i + 1, total))
        try:
            ocr_out, _ = engine(path)
        except Exception:
            continue
        if not ocr_out:
            continue
        lines = [x[1] for x in ocr_out if x and len(x) >= 2]
        text = "\n".join(lines)
        hits = len(CODE_TOKENS.findall(text))
        sym = sum(1 for ch in text if ch in "{}();=<>[]#*+-/" or (ch.isascii() and not ch.isalpha()))
        ratio = sym / max(1, len(text))
        score = hits + (3 if ratio > 0.7 else 0) + (2 if len(lines) >= 4 else 0)
        if score >= 4:
            result[os.path.basename(path)] = {"time": fmt_ts(t), "score": score,
                                              "ocr_text": text[:4000]}
    if result:
        with open(os.path.join(outdir, "code_frames.json"), "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
        print("[code-ocr] 识别到 %d 个疑似代码帧" % len(result))
    return result


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="视频 -> 转写稿 + 关键帧 + 时间戳索引")
    ap.add_argument("-i", "--input", required=True)
    ap.add_argument("-o", "--outdir", required=True)
    ap.add_argument("--max-height", type=int, default=1080)
    ap.add_argument("--scene", type=float, default=0.02)
    ap.add_argument("--interval", type=float, default=12.0)
    ap.add_argument("--max-frames", type=int, default=400)
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--asr-model", default="small")
    ap.add_argument("--lang", default="zh")
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--no-subtitle", action="store_true")
    ap.add_argument("--no-code-ocr", action="store_true")
    ap.add_argument("--cookies")
    ap.add_argument("--reuse", action="store_true")
    ap.add_argument("--keep-audio", action="store_true")
    ap.add_argument("--progress-json", default=os.environ.get("VIDEONOTES_PROGRESS", ""))
    args = ap.parse_args()

    prog = make_progress(args.progress_json)
    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    src = args.input
    prog("prepare", 1, "准备中", force=True)

    if os.path.isfile(src):
        video = src
        print("[input] 本地视频: %s" % video)
        prog("prepare", 10, "本地视频")
    else:
        if not is_url(src) and re.fullmatch(r"BV[0-9A-Za-z]{10}", src):
            src = "https://www.bilibili.com/video/" + src
        prog("prepare", 3, "解析链接")
        video = download_video(src, outdir, args.max_height, args.cookies,
                               args.reuse, args.no_subtitle, prog)
        if not video:
            print("[ERR] 视频下载失败", file=sys.stderr)
            prog("error", 100, "视频下载失败", force=True)
            return 2

    prog("probe", 30, "读取视频信息")
    dur, w, h = probe_duration(video)
    print("[probe] 时长=%.1fs 分辨率=%dx%d" % (dur, w, h))

    subtitle_source = ""
    asr_model = ""
    n_lines = 0
    srt = None if args.no_subtitle else find_subtitle(outdir)
    if srt:
        n_lines = srt_to_transcript(srt, os.path.join(outdir, "transcript.txt"))
        subtitle_source = os.path.basename(srt)
        print("[subtitle] 使用站方字幕 %s，共 %d 行" % (subtitle_source, n_lines))
        prog("asr", 60, "使用站方字幕 %d 行" % n_lines)
    if n_lines == 0 and not args.no_asr:
        n_lines, asr_model = transcribe(video, outdir, args.asr_model, args.lang,
                                        args.keep_audio, prog)
    elif args.no_asr:
        print("[asr] 已跳过转写")
    if n_lines == 0:
        print("[WARN] 没有转写稿（无字幕且 ASR 未成功），只能靠画面做笔记", file=sys.stderr)

    frames_dir = os.path.join(outdir, "frames")
    scan_dir = os.path.join(outdir, "_scan")
    if not args.reuse:
        for d in (frames_dir, scan_dir):
            if os.path.isdir(d):
                shutil.rmtree(d)
    os.makedirs(frames_dir, exist_ok=True)
    print("[frames] 1fps 扫描画面变化 ...")
    prog("frames", 62, "扫描画面", force=True)
    scan_files = scan_frames(video, scan_dir, 1.0, 480, prog)
    print("[frames] 扫描帧 %d 张" % len(scan_files))
    times = pick_timestamps(scan_files, 1.0, args.interval, args.scene, args.max_frames, prog)
    print("[frames] 选中 %d 个时间点，按原生分辨率重抽 ..." % len(times))
    pairs = extract_selected(video, frames_dir, times, args.width, prog)
    shutil.rmtree(scan_dir, ignore_errors=True)

    code_frames = detect_code_frames(pairs, outdir, prog, not args.no_code_ocr)

    index_lines = []
    for t, f in pairs:
        rel = os.path.relpath(f, outdir).replace("\\", "/")
        index_lines.append("%s\t%s" % (fmt_ts(t), rel))
    with open(os.path.join(outdir, "frames_index.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index_lines) + "\n")
    print("[frames] 保留 %d 张 -> frames_index.txt" % len(pairs))

    manifest = {
        "input": args.input, "source": os.path.basename(video),
        "title": source_title(outdir) or (os.path.splitext(os.path.basename(args.input))[0] if not is_url(args.input) else ""),
        "duration_sec": round(dur, 2), "width": w, "height": h,
        "subtitle_source": subtitle_source, "asr_model": asr_model,
        "transcript_lines": n_lines, "frames": len(pairs),
        "transcript_status": "available" if n_lines else ("skipped" if args.no_asr else "unavailable"),
        "code_frames": len(code_frames),
        "params": {"max_height": args.max_height, "scene": args.scene,
                   "interval": args.interval, "width": args.width},
    }
    with open(os.path.join(outdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    prog("prepared", 90, "素材准备完成", force=True)
    print("[done] " + outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
