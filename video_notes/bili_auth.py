"""持久登录状态校验；不向前端暴露 Cookie 内容。"""
import http.cookiejar
import json
import threading
import time
import urllib.request
from pathlib import Path

LOCK = threading.Lock()
_CACHE = {}
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def load_jar(path):
    jar = http.cookiejar.MozillaCookieJar(str(path))
    if Path(path).exists():
        jar.load(ignore_discard=True, ignore_expires=True)
        for cookie in jar:
            # Netscape 导出中的 0 表示会话 Cookie，并非 1970 年过期。
            if cookie.expires == 0:
                cookie.expires = None
                cookie.discard = True
    return jar


def verify_jar(jar):
    request = urllib.request.Request(NAV_URL, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        with opener.open(request, timeout=8) as response:
            data = json.load(response)
        if data.get("code") == 0 and (data.get("data") or {}).get("isLogin") is True:
            return {"status": "valid", "loggedIn": True,
                    "username": (data.get("data") or {}).get("uname", ""), "message": "登录有效"}
        if data.get("code") == -101 or (data.get("code") == 0 and (data.get("data") or {}).get("isLogin") is False):
            return {"status": "expired", "loggedIn": False, "message": "登录已失效，请重新扫码"}
        return {"status": "unknown", "loggedIn": None, "message": "暂时无法确认登录状态，请稍后重试"}
    except Exception:
        return {"status": "unknown", "loggedIn": None, "message": "登录校验暂不可用，已保留本地登录凭据"}


def login_status(path, force=False):
    path = Path(path)
    with LOCK:
        signature = (str(path.resolve()), path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else (str(path.resolve()),)
        cached = _CACHE.get(signature)
        if not force and cached and time.time() - cached[0] < 60:
            return dict(cached[1])
        result = {"exists": path.exists(), "count": 0, "loggedIn": False, "status": "missing", "message": "尚未登录"}
        try:
            jar = load_jar(path)
            cookies = [c for c in jar if (c.domain.lstrip('.') == 'bilibili.com' or c.domain.endswith('.bilibili.com'))
                       and not c.is_expired()]
            result["count"] = len(cookies)
            sess = next((c for c in cookies if c.name == "SESSDATA" and c.value), None)
            if sess:
                result.update(verify_jar(jar))
                result["expiresAt"] = sess.expires
                result["expiringSoon"] = bool(sess.expires and sess.expires - time.time() < 7 * 86400)
            elif path.exists():
                result.update(status="expired", message="登录凭据缺失或过期，请重新扫码")
        except (OSError, http.cookiejar.LoadError, ValueError):
            result.update(status="invalid", message="登录文件无法读取，请重新扫码")
        result["checkedAt"] = time.time()
        _CACHE.clear()
        _CACHE[signature] = (time.time(), dict(result))
        return result
