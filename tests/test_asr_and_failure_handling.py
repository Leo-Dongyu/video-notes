import argparse
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes import server, video2notes as notes, doctor
from video_notes.asr_health import check_audio_decoder


class AsrAndFailureTests(unittest.TestCase):
    def test_real_wav_decode_without_model_or_network(self):
        self.assertEqual(check_audio_decoder(), 3200)

    def test_doctor_rejects_importable_but_broken_decoder(self):
        with patch.object(doctor.platform, 'platform', return_value='Windows'), patch.object(doctor.subprocess, 'run', return_value=Mock(returncode=1)):
            result = next(c for c in doctor.diagnose() if c['name'] == 'ASR audio decoder')
        self.assertFalse(result['ok'])
        self.assertTrue(result['required'])

    def test_title_survives_failure_before_task_json_exists(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'OUTPUTS', Path(folder)):
            job = server.Job([{'input': 'https://www.bilibili.com/video/BV1234567890'}],
                             'single', '视频笔记', 'md', 'balanced', '', False, {'autoTitle': True})
            (job.dir / 'source.info.json').write_text('{"title":"已识别的视频标题"}', encoding='utf-8')
            with patch.object(server, '_run_cmd', side_effect=RuntimeError('HTTP 503')):
                server.job_worker(job)
            self.assertEqual(job.status, 'error')
            self.assertEqual(job.title, '已识别的视频标题')
            self.assertTrue(job.dir.name.endswith('-已识别的视频标题'))
            saved = json.loads((job.dir / '.job.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['title'], job.title)
            self.assertEqual(saved['status'], 'error')

    def test_custom_title_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'OUTPUTS', Path(folder)):
            job = server.Job([{'input': 'test.mp4'}], 'single', '我的标题', 'md', 'fast', '', False)
            (job.dir / 'source.info.json').write_text('{"title":"视频标题"}', encoding='utf-8')
            server.sync_job_title(job)
            self.assertEqual(job.title, '我的标题')

    def test_cli_records_failure_in_progress_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'progress.json'
            notes.set_progress_path(str(path))
            try:
                with patch.object(notes, 'main', side_effect=RuntimeError('HTTP 503 busy')), patch.object(notes.traceback, 'print_exc'):
                    self.assertEqual(notes.cli(), 1)
                progress = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(progress['stage'], 'error')
                self.assertIn('503', progress['msg'])
            finally:
                notes.set_progress_path('')

    def test_busy_service_has_actionable_error(self):
        failure = urllib.error.HTTPError('https://api.example.test/messages', 503, 'Busy', {}, io.BytesIO(b'{"error":"busy"}'))
        with patch('urllib.request.urlopen', side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, 'HTTP 503'):
                notes._request_api([], '', 'test', 'model', 'https://api.example.test/messages', 100, retries=1)

    def test_missing_transcript_is_reported_as_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            (out / 'manifest.json').write_text('{"duration_sec":20,"transcript_status":"unavailable"}', encoding='utf-8')
            notes._WARNINGS.clear()
            args = argparse.Namespace(merge=False, input='test.mp4', title=None)
            try:
                with patch.object(notes, 'call_api', return_value=('## 画面要点\n内容', {})):
                    notes.make_notes(args, {}, notes.QUALITY['fast'], out, 'test', 'model', 'https://api.example.test', 0, 100)
                self.assertTrue(any('仅根据画面' in warning for warning in notes._WARNINGS))
            finally:
                notes._WARNINGS.clear()


if __name__ == '__main__':
    unittest.main()
