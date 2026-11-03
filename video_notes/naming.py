"""用户可读且兼容 Windows 的产物命名。"""
import re
from pathlib import Path


def is_source_reference(value):
    value = str(value or '').strip()
    return bool(re.match(r'^(?:https?://|www\.|BV[0-9A-Za-z]{10}(?:$|[/?])|av\d+$)', value, re.I))


def document_title(text, title):
    """统一正文一级标题，其余正文保持原样。"""
    lines = text.lstrip('\ufeff\r\n').splitlines()
    if lines and re.match(r'^#\s+', lines[0]):
        lines[0] = '# ' + title
        return '\n'.join(lines)
    return '# ' + title + '\n\n' + text.lstrip('\ufeff\r\n')


def safe_name(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(title or '')).strip().rstrip('. ')
    name = name[:100].rstrip('. ') or '视频笔记'
    if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
        name = '_' + name
    return name


def task_directory(root, timestamp, title):
    """只在同秒同标题重复时加序号，绝不复用其他任务目录。"""
    base = Path(root) / (timestamp + '-' + safe_name(title))
    candidate, number = base, 2
    while True:
        try:
            candidate.mkdir(parents=True, exist_ok=False)
            return candidate
        except FileExistsError:
            candidate = base.with_name(base.name + ' (' + str(number) + ')')
            number += 1


def source_title(directory):
    import json
    for name in ('source.info.json', 'manifest.json'):
        try:
            title = json.loads((Path(directory) / name).read_text(encoding='utf-8')).get('title')
            if title and not is_source_reference(title):
                return str(title)
        except (OSError, ValueError):
            pass
    return ''
