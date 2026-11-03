"""只检查本机环境，不访问模型 API、不输出凭据。"""
import argparse
import importlib.metadata
import json
import platform
import struct
import subprocess
import sys

from .runtime import find_browser, find_executable
from .paths import PROJECT_ROOT

PACKAGES = ('yt-dlp', 'Pillow', 'requests', 'qrcode', 'Markdown', 'Pygments', 'faster-whisper', 'av')


def diagnose():
    checks = []
    def add(name, ok, detail, required=True):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail, 'required': required})
    add('Windows x64', sys.platform == 'win32' and struct.calcsize('P') == 8,
        platform.platform())
    add('Python 3.11-3.13', (3, 11) <= sys.version_info[:2] <= (3, 13), platform.python_version())
    for name in PACKAGES:
        try:
            add(name, True, importlib.metadata.version(name))
        except importlib.metadata.PackageNotFoundError:
            add(name, False, 'Missing: run install.bat')
    if any(c['name'] == 'faster-whisper' and c['ok'] for c in checks):
        try:
            loaded = subprocess.run([sys.executable, str(PROJECT_ROOT / 'video_notes' / 'asr_health.py')],
                                    capture_output=True, timeout=30)
            add('ASR audio decoder', loaded.returncode == 0,
                'WAV decode passed' if loaded.returncode == 0 else 'Cannot decode audio. Run install.bat to restore compatible PyAV; check Visual C++ x64 runtime.')
        except (OSError, subprocess.TimeoutExpired):
            add('ASR audio decoder', False, 'Audio decode failed or timed out; see docs/WINDOWS.md')
    for name, variable in (('ffmpeg', 'VIDEONOTES_FFMPEG'), ('ffprobe', 'VIDEONOTES_FFPROBE')):
        path = find_executable(name, variable)
        add(name, path is not None, path or 'Install FFmpeg and add its bin directory to PATH')
    browser = find_browser()
    add('PDF browser', browser is not None, browser or 'Install Edge/Chrome or set VIDEONOTES_BROWSER', False)
    pandoc = find_executable('pandoc', 'VIDEONOTES_PANDOC')
    add('Word export', pandoc is not None, pandoc or 'Install pandoc for Word export', False)
    try:
        version = importlib.metadata.version('rapidocr-onnxruntime')
        add('Optional OCR', True, version, False)
    except importlib.metadata.PackageNotFoundError:
        add('Optional OCR', False, 'Optional: install.bat -WithOcr', False)
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    checks = diagnose()
    ready = all(c['ok'] for c in checks if c['required'])
    if args.json:
        print(json.dumps({'ready': ready, 'checks': checks}, ensure_ascii=False, indent=2))
    else:
        for check in checks:
            state = 'OK' if check['ok'] else 'MISSING' if check['required'] else 'OPTIONAL'
            print('[%s] %s: %s' % (state, check['name'], check['detail']))
        print('Ready.' if ready else 'Complete the missing requirements, then run doctor.bat again.')
    return 0 if ready else 1


if __name__ == '__main__':
    raise SystemExit(main())
