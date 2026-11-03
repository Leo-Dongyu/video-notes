import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes import runtime
from video_notes.paths import PROJECT_ROOT


class WindowsReleaseTests(unittest.TestCase):
    def test_tools_resolved_from_current_virtual_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'python.exe'
            tool = Path(folder) / 'yt-dlp.exe'
            tool.touch()
            with patch.object(runtime.sys, 'executable', str(executable)), patch.object(runtime.shutil, 'which', return_value=None):
                self.assertEqual(runtime.find_executable('yt-dlp'), str(tool))

    def test_explicit_tool_path_and_missing_override(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / 'ffmpeg.exe'
            tool.touch()
            with patch.dict(os.environ, {'VIDEONOTES_FFMPEG': str(tool)}):
                self.assertEqual(runtime.find_executable('ffmpeg', 'VIDEONOTES_FFMPEG'), str(tool))
            with patch.dict(os.environ, {'VIDEONOTES_FFMPEG': str(tool) + '.missing'}):
                self.assertIsNone(runtime.find_executable('ffmpeg', 'VIDEONOTES_FFMPEG'))

    def test_browser_discovery_supports_per_user_install(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=True):
            browser = Path(folder) / 'Google/Chrome/Application/chrome.exe'
            browser.parent.mkdir(parents=True)
            browser.touch()
            os.environ['LOCALAPPDATA'] = folder
            self.assertEqual(runtime.find_browser(), str(browser))

    def test_public_package_excludes_local_data_and_private_templates(self):
        path = PROJECT_ROOT / 'tools/build_release.py'
        spec = importlib.util.spec_from_file_location('build_release', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        files = [p.relative_to(PROJECT_ROOT).as_posix() for p in module.release_files()]
        for path in files:
            self.assertNotIn(path.split('/')[0], {'work', 'outputs', 'cookies', 'models', '.venv', 'tmp'})
        self.assertNotIn('templates/reference.docx', files)
        self.assertNotIn('config.json', files)
        self.assertIn('config.example.json', files)
        self.assertIn('install.bat', files)
        self.assertIn('.github/workflows/windows.yml', files)

    def test_configuration_example_has_no_key(self):
        example = json.loads((PROJECT_ROOT / 'config.example.json').read_text(encoding='utf-8'))
        self.assertEqual(example['apiKey'], '')
        self.assertEqual(example['model'], 'deepseek-flash')


if __name__ == '__main__':
    unittest.main()
