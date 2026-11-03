"""无需依赖用户机器固定盘符的工具定位。"""
import os
from pathlib import Path
import shutil
import sys

from .paths import PROJECT_ROOT


def find_executable(name, variable=None):
    configured = os.environ.get(variable, '') if variable else ''
    if configured:
        return configured if Path(configured).is_file() else None
    for directory in (Path(sys.executable).parent, PROJECT_ROOT / 'bin', Path(sys.executable).parent / 'Scripts'):
        for filename in (name + '.exe', name):
            candidate = directory / filename
            if candidate.is_file():
                return str(candidate)
    return shutil.which(name)


def find_browser():
    configured = os.environ.get('VIDEONOTES_BROWSER')
    if configured:
        return configured if Path(configured).is_file() else None
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        base = os.environ.get(variable)
        if not base:
            continue
        for app in ('Microsoft/Edge/Application/msedge.exe', 'Google/Chrome/Application/chrome.exe'):
            candidate = Path(base) / app
            if candidate.is_file():
                return str(candidate)
    return shutil.which('msedge') or shutil.which('chrome')
