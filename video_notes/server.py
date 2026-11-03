#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""server.py —— 视频笔记 Web 服务（本地，127.0.0.1:8765）

功能：
  - 前端：选择本地视频 / B站链接，实时进度，导出 PDF/Word，文件名默认视频名
  - B站扫码登录（写 cookies/bilibili.txt）
  - 从 Chrome 导入 cookie（尝试多种方式，失败时给出指引）
  - DeepSeek API Key 配置（config.json）
  - 任务历史与产物下载

启动：python server.py [--port 8765] [--open]
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from .bili_auth import login_status, verify_jar
from .naming import safe_name, source_title, task_directory, is_source_reference
from .batch_sources import collection_videos, directory_videos, canonical_video_input
from .runtime import find_executable
from . import __version__

from .paths import PROJECT_ROOT

HERE = PROJECT_ROOT
WEB = HERE / "web"
WORK = HERE / "work"
JOBS_DIR = WORK / "jobs"
COOKIES = HERE / "cookies" / "bilibili.txt"
CONFIG = HERE / "config.json"
DEFAULT_PORT = 8765
OUTPUTS = HERE / "outputs"      # 所有任务产物集中存放（项目目录下）


def open_folder(path):
    """在资源管理器中打开目录（仅 Windows 本机使用）。"""
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, str(HERE), 1)
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception:
        try:
            subprocess.Popen(["explorer", str(path)])
            return True
        except Exception:
            return False


def kill_process_tree(pid):
    """结束进程及其全部子进程（Windows 用 taskkill /T）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=20,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.kill(pid, 15)
    except Exception:
        pass


if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

VIDEO_EXT = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm", ".ts", ".m4v", ".wmv", ".mp3", ".wav", ".m4a", ".aac", ".flac"}

STATE_LOCK = threading.Lock()
JOBS = {}
QR_SESSIONS = {}


# ------------------------------------------------------------------ 工具

def load_config():
    try:
        if CONFIG.exists():
            return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_config(cfg):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_api_key(cli_key="", cfg=None):
    cfg = cfg or load_config()
    if cli_key:
        return cli_key
    if os.environ.get("DEEPSEEK_API_KEY", "").strip():
        return os.environ["DEEPSEEK_API_KEY"].strip()
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


def cookie_count():
    try:
        if not COOKIES.exists():
            return 0
        return sum(1 for l in COOKIES.read_text(encoding="utf-8", errors="replace").splitlines()
                   if l and not l.startswith("#"))
    except Exception:
        return 0


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------------ 任务

def slugify(text):
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", str(text or "")).strip("-")
    return (s[:36] or "note")


STEPS = [("素材准备", "prepare", 0), ("章节大纲", "outline", 68), ("分章写作", "chapters", 72),
         ("代码复核", "qa", 86), ("术语与复盘", "extras", 92), ("渲染输出", "render", 95),
         ("完成", "done", 100)]


def steps_for(percent):
    out = []
    active_set = False
    for i, (label, key, th) in enumerate(STEPS):
        if percent + 0.01 >= th:
            state = "done"
        elif not active_set:
            state = "active"
            active_set = True
        else:
            state = "pending"
        out.append({"label": label, "key": key, "state": state, "threshold": th})
    return out


class Job:
    def __init__(self, inputs, mode, title, fmt, quality, asr_model, cookies_ok, options=None, restore_dir=None, job_id=None):
        self.id = job_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self.inputs = [dict(x) for x in inputs]
        first = self.inputs[0]
        self.mode = mode or ("single" if len(self.inputs) == 1 else "separate")
        self.input = first.get("input", "")
        self.is_local = bool(first.get("isLocal"))
        self.title = title or first.get("title") or Path(str(self.input)).stem
        self.fmt = fmt or "pdf"
        self.quality = quality or "balanced"
        self.asr_model = asr_model or ""
        self.cookies_ok = cookies_ok
        self.options = options or {}
        self.dir = (Path(restore_dir) if restore_dir else task_directory(OUTPUTS, self.id[:15], self.title)).resolve()
        self.progress_file = self.dir / "progress.json"
        self.log_file = self.dir / "run.log"
        self.status = "queued"
        self.percent = 0.0
        self.stage = "queued"
        self.message = "排队中"
        self.files = {}
        self.started = time.time()
        self.finished = 0.0
        self.proc = None
        self.error = ""
        self.log_tail = []
        self.deleted = False
        self.worker = None

    def public(self):
        elapsed = (self.finished or time.time()) - self.started
        eta = 0
        if self.status == "running" and self.percent > 3:
            eta = max(1, int(elapsed / (self.percent / 100.0) - elapsed))
        return {"id": self.id, "input": self.input, "isLocal": self.is_local,
                "inputs": self.inputs, "mode": self.mode,
                "title": self.title, "format": self.fmt, "quality": self.quality,
                "status": self.status, "percent": round(self.percent, 2),
                "stage": self.stage, "message": self.message, "files": self.files,
                "error": self.error, "started": self.started, "finished": self.finished,
                "elapsed": round(elapsed, 1), "eta": eta, "dir": str(self.dir),
                "steps": steps_for(self.percent), "log": self.log_tail[-40:],
                "usage": collect_usage(self)}


def append_log(job, line):
    try:
        with open(job.log_file, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
        job.log_tail.append(line.rstrip()[:400])
        if len(job.log_tail) > 400:
            job.log_tail = job.log_tail[-200:]
    except Exception:
        pass


def collect_usage(job):
    """汇总任务目录内所有 api_usage.json 的 token 用量。"""
    total = {"input_tokens": 0, "output_tokens": 0,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    try:
        for p in Path(job.dir).rglob("api_usage.json"):
            try:
                d = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            u = d.get("usage") or {}
            for k in total:
                try:
                    total[k] += int(u.get(k) or 0)
                except Exception:
                    pass
    except Exception:
        return None
    return total if any(total.values()) else None


def collect_files(job):
    """收集任务目录下的交付文件（顶层与多视频子目录）。"""
    out = {}
    try:
        for p in sorted(Path(job.dir).rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(Path(job.dir)).as_posix()
            if rel.startswith("src-"):
                continue
            if p.name == "progress.json":
                continue
            low = p.suffix.lower()
            if low not in (".pdf", ".docx", ".md", ".json"):
                continue
            if low == ".json" and p.name not in ("task.json", "api_usage.json"):
                continue
            out[rel] = {"size": p.stat().st_size, "url": "/api/jobs/%s/file/%s" % (job.id, rel)}
    except Exception:
        pass
    return dict(sorted(out.items(), key=lambda kv: (kv[0].count("/"), kv[0])))


def job_dir_listing(job):
    return [{"name": k, **v} for k, v in collect_files(job).items()]


def _env_for_job():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    key = resolve_api_key()
    if key:
        env["DEEPSEEK_API_KEY"] = key
    return env


def _pump_progress(job, prog_file, base, span, label, proc):
    while proc.poll() is None:
        sync_job_title(job)
        try:
            data = json.loads(prog_file.read_text(encoding="utf-8"))
            pct = float(data.get("percent") or 0)
            job.percent = max(job.percent, min(99.9, base + span * pct / 100.0))
            job.stage = str(data.get("stage") or job.stage)
            msg = str(data.get("msg") or "")
            job.message = (label + msg) if label else msg
        except Exception:
            pass
        time.sleep(0.4)


def _run_cmd(job, cmd, outdir, base, span, label=""):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    append_log(job, "命令: " + " ".join(cmd) + "\n")
    logfh = open(job.log_file, "a", encoding="utf-8", errors="replace")
    with STATE_LOCK:
        if job.deleted or job.status == "cancelled":
            logfh.close()
            raise RuntimeError("任务已停止")
        proc = subprocess.Popen(cmd, cwd=str(HERE), env=_env_for_job(), stdout=logfh,
                                stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        job.proc = proc
    pump = threading.Thread(target=_pump_progress,
                            args=(job, outdir / "progress.json", base, span, label, proc),
                            daemon=True)
    pump.start()
    rc = proc.wait()
    pump.join(timeout=3)
    logfh.close()
    if rc != 0:
        append_log(job, "[ERR] exit %d\n" % rc)
        try:
            progress = json.loads((outdir / 'progress.json').read_text(encoding='utf-8'))
            if progress.get('stage') == 'error' and progress.get('msg'):
                raise RuntimeError(str(progress['msg'])[:1000])
        except (OSError, ValueError):
            pass
        raise RuntimeError("处理失败（exit %d），详见 run.log" % rc)


def _base_cmd(job, inp, outdir, title=None):
    cmd = [sys.executable, str(HERE / "video2notes.py"), "-i", str(inp), "-o", str(outdir),
           "--format", job.fmt, "--quality", job.quality,
           "--progress-json", str(Path(outdir) / "progress.json")]
    t = title if title is not None else job.title
    if t and not job.options.get("autoTitle"):
        cmd += ["--title", t]
    if job.asr_model:
        cmd += ["--asr-model", job.asr_model]
    if job.cookies_ok and COOKIES.exists():
        cmd += ["--cookies", str(COOKIES)]
    opts = job.options or {}
    if opts.get("resume"):
        cmd += ["--reuse"]
    for k, flag in (("maxHeight", "--max-height"), ("maxFrames", "--max-frames"), ("interval", "--interval")):
        v = opts.get(k)
        if v not in (None, ""):
            cmd += [flag, str(v)]
    return cmd


def start_job(job):
    save_job_state(job)
    job.worker = threading.Thread(target=job_worker, args=(job,), daemon=True)
    job.worker.start()


def save_job_state(job):
    if job.deleted:
        return
    data = {"id": job.id, "inputs": job.inputs, "mode": job.mode, "title": job.title, "format": job.fmt,
            "quality": job.quality, "asrModel": job.asr_model, "options": job.options,
            "status": job.status, "message": job.message, "error": job.error,
            "started": job.started, "finished": job.finished}
    try:
        target = job.dir / ".job.json"
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


def sync_job_title(job):
    """素材元数据就绪后立即同步标题，不等待付费生成成功。"""
    if job.mode != 'single':
        return
    if not (job.options.get('autoTitle') or is_source_reference(job.title) or job.title == '视频笔记'):
        return
    title = source_title(job.dir)
    if title and job.title != title:
        job.title = title
        job.inputs[0]['title'] = title
        save_job_state(job)


def finalize_job_names(job):
    """完成后按真实标题命名，并同步任务记录中的绝对路径。"""
    root = job.dir.resolve()
    if not root.is_relative_to(OUTPUTS.resolve()) or root == OUTPUTS.resolve():
        raise ValueError("任务目录不在输出目录内")
    records = []
    for path in root.rglob('task.json'):
        data = json.loads(path.read_text(encoding='utf-8'))
        # 重命名前解析仍存在的路径，统一 Windows 8.3 别名和完整路径。
        # 目录移动后再解析旧路径，Windows 将无法还原已不存在的短文件名。
        data['outputs'] = [str(Path(value).resolve()) if Path(value).is_absolute() else str(value)
                           for value in data.get('outputs', [])]
        title = str(data.get('title') or '')
        if not title or title.startswith(('http://', 'https://')) or re.fullmatch(r'BV\w+|av\d+', title):
            title = source_title(path.parent) or title or '视频笔记'
        data['title'] = title
        for video in data.get('videos') or []:
            if len(data['videos']) == 1:
                video['title'] = title
        renamed = {}
        for extension in ('pdf', 'docx'):
            old = path.parent / ('notes.' + extension)
            new = path.parent / (safe_name(title) + '.' + extension)
            if old.exists() and old != new and not new.exists():
                old.rename(new)
                renamed[str(old)] = str(new)
        data['outputs'] = [renamed.get(str(p), str(p)) for p in data.get('outputs', [])]
        if path.parent == root:
            job.title = title
            if job.mode == 'single':
                job.inputs[0]['title'] = title
        records.append((path.relative_to(root), data))
    destination = OUTPUTS.resolve() / (job.id[:15] + '-' + safe_name(job.title))
    if root.parent == destination.parent and re.fullmatch(re.escape(destination.name) + r' \(\d+\)', root.name):
        destination = root
    with STATE_LOCK:
        if root != destination:
            base, number = destination, 2
            while destination.exists():
                destination = base.with_name(base.name + ' (' + str(number) + ')')
                number += 1
            root.rename(destination)
    job.dir = destination
    job.progress_file = destination / 'progress.json'
    job.log_file = destination / 'run.log'
    for relative, data in records:
        data['outdir'] = str((destination / relative).parent)
        updated = []
        for value in data.get('outputs', []):
            p = Path(value)
            updated.append(str(destination / p.relative_to(root)) if p.is_relative_to(root) else value)
        data['outputs'] = updated
        (destination / relative).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def job_worker(job):
    with STATE_LOCK:
        if job.deleted or job.status == "cancelled":
            return
        job.status = "running"
    job.stage = "prepare"
    job.message = "开始"
    append_log(job, "== 开始 %s（mode=%s，%d 个视频）\n" % (job.id, job.mode, len(job.inputs)))
    try:
        n = len(job.inputs)
        failed = []
        if job.mode == "merged" and n >= 2:
            cmd = [sys.executable, str(HERE / "video2notes.py")]
            input_file = job.dir / '.inputs.json'
            input_file.write_text(json.dumps([str(it['input']) for it in job.inputs], ensure_ascii=False), encoding='utf-8')
            cmd += ['--inputs-json', str(input_file)]
            cmd += ["--merge", "-o", str(job.dir), "--format", job.fmt, "--quality", job.quality,
                    "--progress-json", str(job.progress_file)]
            if job.title and not job.options.get("autoTitle"):
                cmd += ["--title", job.title]
            if job.asr_model:
                cmd += ["--asr-model", job.asr_model]
            if job.cookies_ok and COOKIES.exists():
                cmd += ["--cookies", str(COOKIES)]
            opts = job.options or {}
            if opts.get("resume"):
                cmd += ["--reuse"]
            for k, flag in (("maxHeight", "--max-height"), ("maxFrames", "--max-frames"), ("interval", "--interval")):
                v = opts.get(k)
                if v not in (None, ""):
                    cmd += [flag, str(v)]
            _run_cmd(job, cmd, job.dir, 0, 100, "")
        elif job.mode == "separate" and n > 1:
            for i, it in enumerate(job.inputs, 1):
                t = it.get("title") or Path(str(it["input"])).stem
                sub = job.dir / ("%02d-%s" % (i, slugify(t)))
                cmd = _base_cmd(job, it["input"], sub, title=t)
                try:
                    _run_cmd(job, cmd, sub, 100.0 * (i - 1) / n, 100.0 / n,
                             "视频 %d/%d《%s》：" % (i, n, t))
                except Exception:
                    if job.status == 'cancelled' or job.deleted:
                        raise
                    failed.append('《%s》处理失败，可重试' % t)
                job.percent = max(job.percent, 100.0 * i / n - 0.5)
        else:
            _run_cmd(job, _base_cmd(job, job.input, job.dir), job.dir, 0, 100, "")
        if job.status == "cancelled" or job.deleted:
            return
        finalize_job_names(job)
        job.percent = 100.0
        job.stage = "done"
        if failed and len(failed) == n:
            raise RuntimeError('全部视频处理失败，详见任务日志')
        warnings = list(failed)
        for tp in job.dir.rglob("task.json"):
            data = json.loads(tp.read_text(encoding="utf-8"))
            warnings.extend(data.get("warnings") or [])
        job.status = "partial" if warnings else "done"
        job.message = "部分完成：" + "；".join(dict.fromkeys(warnings)) if warnings else "完成"
        append_log(job, "== 完成 %s\n" % job.id)
    except Exception as e:  # noqa: BLE001
        if not job.deleted and job.status != "cancelled":
            job.status = "error"
            job.error = str(e)[:300]
            job.message = job.error
            append_log(job, "[ERR] %s\n" % job.error)
    finally:
        job.finished = time.time()
        if not job.deleted:
            sync_job_title(job)
            if job.status == 'error':
                try:
                    finalize_job_names(job)
                except OSError as error:
                    append_log(job, '[title] 目录暂无法重命名：%s\n' % error)
            job.files = collect_files(job)
            save_job_state(job)


# ------------------------------------------------------------------ 扫码登录

def write_netscape_from_jar(session, out):
    lines = ["# Netscape HTTP Cookie File", "# generated by video-notes server", ""]
    n = 0
    now = time.time()
    for c in session.cookies:
        domain = c.domain or ""
        if "bilibili" not in domain:
            continue
        exp = int(c.expires) if c.expires else 0
        if exp and exp < now:
            continue
        lines.append("\t".join([domain, "TRUE" if domain.startswith(".") else "FALSE",
                                c.path or "/", "TRUE" if c.secure else "FALSE", str(exp),
                                c.name, c.value]))
        n += 1
    if n:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, out)
    return n


def bili_headers():
    return {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/"}


def qr_worker(sess_id, qr_key, session):
    import requests
    state = QR_SESSIONS[sess_id]
    url = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key=" + qr_key
    deadline = time.time() + 180
    last = ""
    while time.time() < deadline:
        try:
            r = session.get(url, headers=bili_headers(), timeout=15)
            data = r.json()
            code = (data.get("data") or {}).get("code")
            state["lastCode"] = code
            msg = (data.get("data") or {}).get("message") or data.get("message") or ""
            if code == 0:
                jump = (data.get("data") or {}).get("url")
                if jump:
                    try:
                        session.get(jump, headers=bili_headers(), timeout=15)
                    except Exception:
                        pass
                has_session = any(c.name == "SESSDATA" and c.value for c in session.cookies)
                verified = verify_jar(session.cookies) if has_session else {"status": "expired"}
                if verified["status"] == "expired" or not has_session:
                    state.update(status="error", message="未取得有效登录凭据，请重新扫码", cookies=0)
                    return
                n = write_netscape_from_jar(session, COOKIES)
                state.update(status="ok" if n else "error",
                             message="登录成功，凭据已保存" if verified["status"] == "valid" else
                                     "登录凭据已保存，网络恢复后将自动校验",
                             cookies=n)
                return
            if code == 86101:
                new = "等待扫码"
            elif code == 86090:
                new = "已扫码，请在手机上确认"
            elif code == 86038:
                state.update(status="expired", message="二维码已过期，请刷新")
                return
            else:
                new = msg or ("状态 %s" % code)
            if new != last:
                last = new
                state["message"] = new
        except Exception as e:  # noqa: BLE001
            state["message"] = "轮询失败：" + str(e)[:120]
        time.sleep(2)
    state.update(status="expired", message="扫码超时，请重试")


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "video-notes/1.0"

    # ---- 基础 ----
    def log_message(self, fmt, *args):
        if os.environ.get("VIDEONOTES_HTTP_LOG"):
            sys.stderr.write("[http] " + (fmt % args) + "\n")

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def send_json(self, obj, code=200):
        self._send(code, json_bytes(obj))

    def send_error_json(self, code, msg, detail=""):
        self.send_json({"ok": False, "error": msg, "detail": detail}, code)

    def read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {}

    def serve_file(self, path: Path, download_name=None):
        if not path.exists() or not path.is_file():
            return False
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        extra = {}
        if download_name:
            extra["Content-Disposition"] = "attachment; filename*=UTF-8''%s" % (
                __import__("urllib.parse").parse.quote(download_name))
        self._send(200, data, ctype, extra)
        return True

    # ---- 路由 ----
    def local_request_allowed(self):
        port = self.server.server_address[1]
        hosts = {'127.0.0.1:%s' % port, 'localhost:%s' % port}
        if self.headers.get('Host', '').lower() not in hosts:
            self.send_error_json(403, '仅允许本机访问')
            return False
        origin = self.headers.get('Origin')
        if origin and origin.lower() not in {'http://' + host for host in hosts}:
            self.send_error_json(403, '不允许其他网站调用本机接口')
            return False
        return True

    def do_GET(self):
        if not self.local_request_allowed():
            return
        try:
            self.route_get()
        except Exception:
            traceback.print_exc()
            self.send_error_json(500, "服务器错误")

    def do_POST(self):
        if not self.local_request_allowed():
            return
        try:
            self.route_post()
        except Exception:
            traceback.print_exc()
            self.send_error_json(500, "服务器错误")

    def do_DELETE(self):
        if not self.local_request_allowed():
            return
        try:
            self.route_delete()
        except Exception:
            traceback.print_exc()
            self.send_error_json(500, "服务器错误")

    def route_get(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)
        if path == '/api/instance':
            return self.send_json({'ok': True, 'pid': os.getpid(), 'root': str(HERE),
                                   'port': self.server.server_address[1], 'version': __version__})
        if path == "/" or path == "/index.html":
            return self.serve_file(WEB / "index.html")
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            fp = (WEB / rel).resolve()
            if fp.is_relative_to(WEB.resolve()) and fp.is_file():
                return self.serve_file(fp)
            return self.send_error_json(404, "not found")
        if path == "/api/config":
            cfg = load_config()
            key = resolve_api_key(cfg=cfg)
            return self.send_json({"ok": True, "model": cfg.get("model") or "deepseek-flash",
                                   "baseUrl": cfg.get("baseUrl") or "",
                                   "hasKey": bool(key),
                                   "keySource": "env" if os.environ.get("DEEPSEEK_API_KEY") else
                                                ("config" if cfg.get("apiKey") else ("windows-user-env" if key else "")),
                                   "cookies": {"exists": COOKIES.exists(), "count": cookie_count(),
                                               "path": str(COOKIES)},
                                   "port": self.server.server_address[1]})
        if path == "/api/cookies/status":
            return self.send_json({"ok": True, **login_status(COOKIES, force=qs.get("refresh") == ["1"])})
        if path == "/api/login/qr":
            key = qs.get("key", [""])[0]
            st = QR_SESSIONS.get(key)
            if not st:
                return self.send_error_json(404, "二维码会话不存在")
            return self.send_json({"ok": True, **{k: st.get(k) for k in
                                                  ("status", "message", "cookies")}})
        if path == "/api/videoinfo":
            inp = qs.get("input", [""])[0].strip()
            return self.videoinfo(inp)
        if path == "/api/fs":
            return self.fs_listing(qs.get("path", [""])[0])
        if path == "/api/jobs":
            with STATE_LOCK:
                items = sorted(JOBS.values(), key=lambda j: j.started, reverse=True)[:30]
            return self.send_json({"ok": True, "jobs": [j.public() for j in items]})
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)/preview/(.+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error_json(404, "任务不存在")
            fp = (job.dir / m.group(2)).resolve()
            if not fp.is_relative_to(job.dir.resolve()) or fp.suffix.lower() != ".md" or not fp.is_file():
                return self.send_error_json(404, "笔记不存在")
            from .md2html import md_to_html
            body = md_to_html(str(fp), job.title, "")
            return self._send(200, body.encode("utf-8"), "text/html; charset=utf-8", {
                "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'"})
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error_json(404, "任务不存在")
            pub = job.public()
            pub["files"] = job_dir_listing(job)
            pub["usage"] = self.read_usage(job)
            return self.send_json({"ok": True, "job": pub})
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)/file/(.+)$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error_json(404, "任务不存在")
            name = unquote(m.group(2))
            fp = (job.dir / name).resolve()
            if fp.is_relative_to(job.dir.resolve()) and fp.is_file():
                return self.serve_file(fp, download_name=Path(name).name)
            return self.send_error_json(404, "文件不存在")
        return self.send_error_json(404, "not found")

    def read_usage(self, job):
        p = job.dir / "api_usage.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
        # 分视频模式没有顶层 api_usage.json：汇总各子目录的用量
        agg = collect_usage(job)
        if agg:
            return {"usage": agg, "mode": job.mode, "quality": job.quality}
        return None

    def videoinfo(self, inp):
        inp = inp.strip()
        if not inp:
            return self.send_error_json(400, "input 为空")
        if re.match(r"^(BV[0-9A-Za-z]{10}|av\d+)$", inp) or "bilibili.com" in inp:
            yt = find_executable('yt-dlp', 'VIDEONOTES_YTDLP')
            if not yt:
                return self.send_error_json(400, '缺少 yt-dlp，请先运行 install.bat')
            url = inp if "http" in inp else "https://www.bilibili.com/video/" + inp
            try:
                p = subprocess.run([yt, "--no-warnings", "--skip-download", "--print",
                                    "%(title)s\t%(duration)s\t%(webpage_url)s", url],
                                   capture_output=True, text=True, timeout=60)
                line = (p.stdout or "").strip().splitlines()
                if line:
                    parts = line[-1].split("\t")
                    title = parts[0] if parts else inp
                    dur = parts[1] if len(parts) > 1 else ""
                    return self.send_json({"ok": True, "kind": "bilibili", "input": inp,
                                           "title": title, "duration": dur,
                                           "url": parts[2] if len(parts) > 2 else url})
            except Exception as e:  # noqa: BLE001
                return self.send_json({"ok": True, "kind": "bilibili", "input": inp,
                                       "title": inp, "duration": "", "warning": str(e)[:200]})
        fp = Path(inp)
        if fp.exists() and fp.is_file():
            return self.send_json({"ok": True, "kind": "local", "input": inp,
                                   "title": fp.stem, "size": fp.stat().st_size})
        return self.send_error_json(404, "本地文件不存在，或不是有效 B站 链接/编号")

    def fs_listing(self, path):
        path = path or str(Path.home())
        fp = Path(path)
        if not fp.exists():
            fp = Path.home()
        if fp.is_file():
            fp = fp.parent
        try:
            dirs, files = [], []
            for child in sorted(fp.iterdir(), key=lambda p: p.name.lower()):
                try:
                    if child.is_dir() and not child.name.startswith("$"):
                        dirs.append(child.name)
                    elif child.is_file() and child.suffix.lower() in VIDEO_EXT:
                        files.append({"name": child.name, "size": child.stat().st_size})
                except Exception:
                    continue
            parent = str(fp.parent) if fp.parent != fp else ""
            return self.send_json({"ok": True, "path": str(fp), "parent": parent,
                                   "dirs": dirs[:200], "files": files[:300],
                                   "drives": self.drives()})
        except Exception as e:  # noqa: BLE001
            return self.send_error_json(400, "目录读取失败", str(e)[:200])

    def drives(self):
        out = []
        if os.name == "nt":
            import string
            for d in string.ascii_uppercase:
                p = Path(d + ":\\")
                if p.exists():
                    out.append(str(p))
        return out

    def route_post(self):
        path = unquote(urlparse(self.path).path)
        body = self.read_json()
        if path == '/api/sources/expand':
            try:
                kind = body.get('type')
                if kind == 'directory':
                    result = directory_videos(str(body.get('input') or ''), body.get('recursive') is True)
                elif kind == 'collection':
                    result = collection_videos(body.get('input') or '', COOKIES)
                else:
                    return self.send_error_json(400, '不支持的批量来源')
                return self.send_json({'ok': True, **result, 'count': len(result['inputs'])})
            except ValueError as error:
                return self.send_error_json(400, str(error))
        if path == "/api/config":
            cfg = load_config()
            if "apiKey" in body:
                key = str(body["apiKey"] or "").strip()
                if key:
                    cfg["apiKey"] = key
                elif cfg.get("apiKey"):
                    cfg.pop("apiKey", None)
            if "model" in body and str(body["model"]).strip():
                cfg["model"] = str(body["model"]).strip()
            if "baseUrl" in body:
                cfg["baseUrl"] = str(body["baseUrl"] or "").strip() or None
                if cfg["baseUrl"] is None:
                    cfg.pop("baseUrl", None)
            save_config({k: v for k, v in cfg.items() if v is not None})
            key = resolve_api_key(cfg=cfg)
            return self.send_json({"ok": True, "hasKey": bool(key)})
        if path == "/api/cookies/import":
            return self.import_cookies(body)
        if path == "/api/login/qr":
            return self.qr_start()
        if path == "/api/jobs":
            return self.create_job(body)
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)/retry$", path)
        if m:
            with STATE_LOCK:
                job = JOBS.get(m.group(1))
                if not job:
                    return self.send_error_json(404, "任务不存在")
                if job.status in ("running", "queued"):
                    return self.send_error_json(409, "任务仍在运行")
                if job.worker and job.worker.is_alive():
                    return self.send_error_json(409, "任务正在停止，请稍后再试")
                job.status, job.stage, job.message = "queued", "queued", "正在重试，将复用已完成步骤"
                job.percent, job.finished, job.error = 0, 0, ""
                job.started = time.time()
                job.options["resume"] = True
                job.cookies_ok = COOKIES.exists()
            start_job(job)
            return self.send_json({"ok": True, "job": job.public()})
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)/open-folder$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error_json(404, "任务不存在")
            ok = open_folder(job.dir)
            return self.send_json({"ok": ok, "dir": str(job.dir)})
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)/cancel$", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self.send_error_json(404, "任务不存在")
            with STATE_LOCK:
                if job.status not in ("running", "queued"):
                    return self.send_error_json(409, "任务已经结束")
                job.status = "cancelled"
                job.message = "已停止，可重试继续处理"
                proc = job.proc
            if proc and proc.poll() is None:
                kill_process_tree(proc.pid)
            return self.send_json({"ok": True})
        return self.send_error_json(404, "not found")

    # ---- 删除任务 ----
    def route_delete(self):
        path = unquote(urlparse(self.path).path)
        m = re.match(r"^/api/jobs/([0-9a-zA-Z\-]+)$", path)
        if not m:
            return self.send_error_json(404, "not found")
        return self.delete_job(m.group(1))

    def delete_job(self, job_id):
        """删除任务及其全部产物（运行中的任务先结束进程树）。"""
        job = JOBS.get(job_id)
        target = None
        if job:
            job.deleted = True
            proc = job.proc
            if proc is not None and proc.poll() is None:
                kill_process_tree(proc.pid)
                try:
                    proc.wait(timeout=15)
                except Exception:
                    pass
            job.status = "deleted"
            job.message = "任务已删除"
            target = Path(job.dir)
            with STATE_LOCK:
                JOBS.pop(job_id, None)
        elif OUTPUTS.exists():
            # 兼容：服务重启后索引里没有、但目录还在的任务
            for d in sorted(OUTPUTS.glob(job_id + "*")):
                if d.is_dir():
                    target = d
                    break
        if target is None:
            return self.send_error_json(404, "任务不存在或已删除")
        if target.resolve() == OUTPUTS.resolve() or not target.resolve().is_relative_to(OUTPUTS.resolve()):
            return self.send_error_json(400, "任务目录无效")
        # 子进程可能仍在写盘，重试删除几次
        for _ in range(6):
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            if not target.exists():
                break
            time.sleep(0.5)
        ok = not target.exists()
        return self.send_json({"ok": ok, "deleted": ok, "dir": str(target),
                               "detail": "" if ok else "目录被占用，未能完全删除"})

    def import_cookies(self, body):
        method = (body.get("method") or "").strip()
        cmd = [sys.executable, str(HERE / "tools" / "chrome_cookies.py"),
               "--json", "-o", str(COOKIES)]
        if method == "cdp":
            cmd = [sys.executable, str(HERE / "tools" / "chrome_cookies.py"), "--json",
                   "-o", str(COOKIES), "--cdp", str(body.get("port") or 9222)]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            raw = (p.stdout or "").strip()
            data = {}
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith("{"):
                    try:
                        data = json.loads(line)
                    except Exception:
                        pass
            return self.send_json({"ok": bool(data.get("ok")), "result": data,
                                   "hint": "" if data.get("ok") else
                                   "Chrome 127+ 的 App-Bound Encryption 会阻止普通进程读取 cookie。"
                                   "请改用扫码登录，或运行 tools\\chrome-debug.ps1（会短暂关闭并重开 Chrome）后再点导入。"})
        except Exception as e:  # noqa: BLE001
            return self.send_error_json(500, "导入失败", str(e)[:300])

    def qr_start(self):
        for key, state in list(QR_SESSIONS.items()):
            if time.time() - state.get("created", 0) > 600:
                QR_SESSIONS.pop(key, None)
        try:
            import requests
        except Exception:
            return self.send_error_json(500, "缺少 requests 库")
        try:
            session = requests.Session()
            r = session.get("https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                            headers=bili_headers(), timeout=20)
            data = r.json()
            if data.get("code") != 0:
                return self.send_error_json(502, "B站返回错误", json.dumps(data, ensure_ascii=False)[:300])
            d = data.get("data") or {}
            qr_key = d.get("qrcode_key")
            url = d.get("url")
            if not qr_key:
                return self.send_error_json(502, "B站未返回 qrcode_key")
            img_b64 = ""
            try:
                import qrcode
                img = qrcode.make(url)
                buf = __import__("io").BytesIO()
                img.save(buf, format="PNG")
                img_b64 = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            except Exception:
                img_b64 = ""
            sess_id = uuid.uuid4().hex
            QR_SESSIONS[sess_id] = {"status": "pending", "message": "等待扫码", "cookies": 0,
                                    "url": url, "created": time.time(), "lastCode": None}
            threading.Thread(target=qr_worker, args=(sess_id, qr_key, session), daemon=True).start()
            return self.send_json({"ok": True, "session": sess_id, "url": url, "qrcode": img_b64})
        except Exception as e:  # noqa: BLE001
            return self.send_error_json(500, "生成二维码失败", str(e)[:300])

    def create_job(self, body):
        raw = body.get("inputs")
        if not raw:
            raw = [{"input": body.get("input") or "",
                    "isLocal": bool(body.get("isLocal")),
                    "title": body.get("title") or ""}]
        inputs, seen = [], set()
        for it in raw:
            if not isinstance(it, dict):
                continue
            inp = str(it.get("input") or "").strip()
            if not inp:
                continue
            is_local = bool(it.get("isLocal"))
            title = str(it.get("title") or "").strip()
            if is_local:
                fp = Path(inp)
                if not fp.exists() or not fp.is_file():
                    return self.send_error_json(400, "本地文件不存在：%s" % inp)
                title = title or fp.stem
            else:
                try:
                    inp = canonical_video_input(inp)
                except ValueError as error:
                    return self.send_error_json(400, str(error))
                title = title if title and not title.startswith(('http://', 'https://')) else "视频笔记"
            key = (os.path.normcase(str(Path(inp).resolve())) if is_local else inp, is_local)
            if key in seen:
                continue
            seen.add(key)
            inputs.append({"input": inp, "isLocal": is_local, "title": title})
        if not inputs:
            return self.send_error_json(400, "请至少填写一个视频链接或本地路径")

        mode = (body.get("mode") or ("single" if len(inputs) == 1 else "separate")).strip()
        if mode not in ("single", "separate", "merged"):
            mode = "separate"
        if len(inputs) == 1:
            mode = "single"
        if mode == "merged" and len(inputs) < 2:
            return self.send_error_json(400, "整合模式需要至少两个视频")

        fmt = body.get("format") or "pdf"
        if fmt not in ("pdf", "docx", "pdf,docx", "md"):
            fmt = "pdf"
        quality = body.get("quality") or "balanced"
        if quality not in ("fast", "balanced", "precise"):
            quality = "balanced"
        if not resolve_api_key():
            return self.send_error_json(400, "还没有配置 DeepSeek API Key（右上角「设置」）")

        options = {k: body.get(k) for k in ("maxHeight", "maxFrames", "interval")
                   if body.get(k) not in (None, "")}
        title = (body.get("title") or "").strip()
        options['autoTitle'] = not title or any(title == item['input'] for item in inputs) or title.startswith(('http://', 'https://'))
        if options['autoTitle']:
            title = ''
        if not title:
            title = inputs[0]["title"] if len(inputs) == 1 else \
                ("%s 等 %d 个视频" % (inputs[0]["title"], len(inputs)))
        auth = login_status(COOKIES) if any(not x["isLocal"] for x in inputs) else {}
        if auth.get("status") in ("expired", "invalid"):
            return self.send_error_json(400, auth["message"])
        job = Job(inputs, mode, title, fmt, quality, body.get("asrModel") or "",
                  auth.get("status") in ("valid", "unknown"), options)
        with STATE_LOCK:
            JOBS[job.id] = job
        append_log(job, "任务排队：%s\n" % json.dumps(job.public(), ensure_ascii=False)[:2000])
        start_job(job)
        return self.send_json({"ok": True, "job": job.public()})


def _ts(value):
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return time.time()


def load_outputs_index():
    """启动时把 outputs/ 下已有任务载入历史列表（服务重启后仍可见）。"""
    if not OUTPUTS.exists():
        return
    for d in sorted(OUTPUTS.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        d = d.resolve()
        m = re.match(r"^(\d{8}-\d{6})-(.+)$", d.name)
        if not m:
            continue
        task = {}
        saved = {}
        try:
            saved = json.loads((d / ".job.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        legacy = re.match(r'^(\d{8}-\d{6}-[0-9a-f]{6})-(.+)$', d.name)
        jid = saved.get('id') or (legacy.group(1) if legacy else m.group(1) + '-' + uuid.uuid5(uuid.NAMESPACE_URL, d.name).hex[:6])
        if jid in JOBS:
            continue
        tp = d / "task.json"
        if tp.exists():
            try:
                task = json.loads(tp.read_text(encoding="utf-8"))
            except Exception:
                pass
        videos = task.get("videos") or []
        if not videos:
            # 分视频模式：顶层没有 task.json，从子目录汇总
            for sub in sorted(p for p in d.iterdir() if p.is_dir()):
                stp = sub / "task.json"
                if not stp.exists():
                    continue
                try:
                    st = json.loads(stp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                videos.extend(st.get("videos") or [])
        inputs = [{"input": v.get("input", ""), "isLocal": True, "title": v.get("title", "")}
                  for v in videos]
        if not inputs:
            inputs = [{"input": str(d), "isLocal": True, "title": m.group(2)}]
        inputs = saved.get("inputs") or inputs
        job = Job(inputs, task.get("mode") or ("single" if len(inputs) == 1 else "separate"),
                  task.get("title") or m.group(2).replace("-", " "), task.get("format") or "pdf",
                  task.get("quality") or "balanced", "", cookie_count() > 0, {}, restore_dir=d, job_id=jid)
        job.options = saved.get("options") or {}
        job.title = saved.get("title") or job.title
        job.asr_model = saved.get("asrModel") or ""
        job.mode = saved.get("mode") or job.mode
        job.fmt = saved.get("format") or job.fmt
        job.quality = saved.get("quality") or job.quality
        job.progress_file = d / "progress.json"
        job.log_file = d / "run.log"
        has_notes = (d / "notes.md").exists() or any(d.glob("*/notes.md"))
        job.status = task.get("status") or ("done" if has_notes else "error")
        warnings = task.get("warnings") or []
        if not task:
            for subtask in d.glob("*/task.json"):
                try:
                    warnings.extend(json.loads(subtask.read_text(encoding="utf-8")).get("warnings") or [])
                except (OSError, ValueError):
                    pass
        if warnings:
            job.status = "partial"
        job.percent = 100 if has_notes else 0
        job.stage = job.status
        job.message = ("部分完成：" + "；".join(warnings)) if warnings else ("已完成（历史任务）" if has_notes else "未完成")
        job.started = _ts(task.get("started")) if task.get("started") else d.stat().st_mtime
        job.finished = _ts(task.get("finished")) if task.get("finished") else d.stat().st_mtime
        if saved:
            job.status = saved.get("status") or job.status
            job.message = saved.get("message") or job.message
            job.error = saved.get("error") or ""
            job.started = saved.get("started") or job.started
            job.finished = saved.get("finished") or job.finished
            if job.status in ("running", "queued"):
                job.status = "cancelled"
                job.message = "上次服务中断，可重试继续处理"
            job.stage = job.status
        job.files = collect_files(job)
        logp = d / "run.log"
        if logp.exists():
            try:
                lines = logp.read_text(encoding="utf-8", errors="replace").splitlines()
                job.log_tail = [x[:400] for x in lines[-40:]]
            except Exception:
                pass
        with STATE_LOCK:
            JOBS[job.id] = job


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--open", action="store_true")
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    if args.host not in ('127.0.0.1', 'localhost'):
        ap.error('本版本只支持本机运行，--host 请使用 127.0.0.1 或 localhost')
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    load_outputs_index()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = "http://%s:%d/" % (args.host, args.port)
    print("视频笔记 Web 服务已启动：", url, flush=True)
    print("按 Ctrl+C 停止", flush=True)
    if args.open:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n关闭中…")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
