"""按允许清单构建源码包，防止把本机配置、凭据和模型打包。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from video_notes import __version__

ROOT_FILES = (
    '.gitignore', '.gitattributes', 'LICENSE', 'README.md', 'CHANGELOG.md', 'SECURITY.md', 'THIRD_PARTY.md',
    'requirements.txt', 'requirements-optional.txt', 'config.example.json',
    'install.bat', 'doctor.bat', 'start-web.bat', 'start-web.vbs', 'stop-web.bat',
    'server.py', 'video2notes.py', 'video2frames.py', 'md2html.py', 'md2pdf.ps1',
)
PATTERNS = ('video_notes/*.py', 'web/*.html', 'web/*.js', 'web/*.css',
            'tools/*.py', 'tools/*.ps1', 'tests/test_*.py',
            '.github/workflows/*.yml', 'docs/WINDOWS.md', 'templates/README.md')


def release_files():
    files = {ROOT / name for name in ROOT_FILES}
    for pattern in PATTERNS:
        files.update(ROOT.glob(pattern))
    for path in sorted(files):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(ROOT):
            raise ValueError('Missing or unsafe release file: ' + str(path.relative_to(ROOT)))
        content = path.read_text(encoding='utf-8-sig')
        if re.search(r'\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}', content):
            raise ValueError('Possible credential in: ' + str(path.relative_to(ROOT)))
        yield path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查允许清单和敏感字符串')
    parser.add_argument('--manifest', help='写出仅含文件名、大小和 SHA-256 的发布清单')
    args = parser.parse_args()
    files = list(release_files())
    if args.manifest:
        manifest = [{'path': p.relative_to(ROOT).as_posix(), 'size': p.stat().st_size,
                     'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
        manifest_path = Path(args.manifest)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    if not args.check:
        output = ROOT / 'dist' / ('video-notes-%s-windows-source.zip' % __version__)
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                data = path.read_bytes()
                if path.suffix in ('.bat', '.vbs', '.ps1'):
                    data = data.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
                archive.writestr('video-notes-%s/%s' % (__version__, path.relative_to(ROOT).as_posix()), data)
        print(output)
    print('Release audit passed: %d source files; no local data directories included.' % len(files))


if __name__ == '__main__':
    main()
