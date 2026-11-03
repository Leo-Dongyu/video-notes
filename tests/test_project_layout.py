"""确保整理源码目录不改变入口与资源位置。"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes.paths import PROJECT_ROOT
from video_notes import server, video2notes, video2frames, md2html


class ProjectLayoutTests(unittest.TestCase):
    def test_public_imports_still_refer_to_same_modules(self):
        import server as old_server
        import video2notes as old_notes
        import video2frames as old_frames
        import md2html as old_html
        self.assertIs(old_server, server)
        self.assertIs(old_notes, video2notes)
        self.assertIs(old_frames, video2frames)
        self.assertIs(old_html, md2html)

    def test_persistent_resources_stay_at_project_root(self):
        self.assertEqual(server.HERE, PROJECT_ROOT)
        self.assertEqual(video2notes.HERE, PROJECT_ROOT)
        self.assertEqual(video2frames.PROJECT_ROOT, PROJECT_ROOT)
        self.assertTrue((server.WEB / 'index.html').is_file())
        self.assertTrue((video2notes.HERE / 'md2pdf.ps1').is_file())
        self.assertTrue((video2notes.HERE / 'templates' / 'README.md').is_file())

    def test_cli_entrypoints_work_outside_project_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            for script in ('server.py', 'video2notes.py', 'video2frames.py', 'md2html.py'):
                with self.subTest(script=script):
                    result = subprocess.run([sys.executable, str(PROJECT_ROOT / script), '--help'],
                                            cwd=folder, capture_output=True, timeout=20,
                                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                    self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))


if __name__ == '__main__':
    unittest.main()
