import argparse
import http.cookiejar
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import video2notes as notes
from video_notes import bili_auth as auth
import server


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        notes._CACHE_DIR = None
        notes._API_TOTAL.update(input_tokens=0, output_tokens=0)
        notes._WARNINGS.clear()
        notes.set_progress_path("")

    def tearDown(self):
        self.tmp.cleanup()

    def test_clean_notes_preserves_code_and_removes_metadata(self):
        original = '## **背景：V4 Pro 与 Harness 同日发布 — [00:00]**\nPPT 对比两种模式（转录自 f_0011_10-07.jpg ）\n> 来源：frames/f_0011_10-07.jpg\n```python\ns = "[00:00] f_0011_10-07.jpg"\n```'
        cleaned = notes.clean_notes(original)
        self.assertIn('## **背景：V4 Pro 与 Harness 同日发布**', cleaned)
        self.assertIn('PPT 对比两种模式\n', cleaned)
        self.assertNotIn('> 来源', cleaned)
        self.assertIn('s = "[00:00] f_0011_10-07.jpg"', cleaned)

    def test_outline_accepts_array_and_object_numeric_times(self):
        chapter = {"title": "知识点", "start": 0, "end": 20}
        for payload in ([chapter], {"chapters": [chapter]}):
            with patch.object(notes, 'call_api', return_value=(json.dumps(payload), {})):
                result = notes.gen_outline({"duration_sec": 20}, [(0, 20, "内容")], [], '', '', '', False)
            self.assertEqual(result[0]['end'], 20)

    def test_outline_reads_end_of_long_transcript(self):
        entries = [(i * 10, (i + 1) * 10, str(i) + '内容' * 200) for i in range(100)]
        observed = []
        def outline(manifest, chunk, *rest):
            observed.extend(chunk)
            return [{"title": "章", "start": chunk[0][0], "end": chunk[-1][1]}]
        with patch.object(notes, '_gen_outline_chunk', side_effect=outline):
            result = notes.gen_outline({}, entries, [], '', '', '', False)
        self.assertEqual(observed, entries)
        self.assertEqual(result[-1]['end'], 1000)

    def test_invalid_outline_falls_back_and_covers_tail(self):
        entries = [(i * 20, (i + 1) * 20, '文本') for i in range(20)]
        with patch.object(notes, 'call_api', return_value=('[{"start":-5,"end":999}]', {})):
            result = notes.gen_outline({}, entries, [], '', '', '', False)
        self.assertEqual(result[0]['start'], 0)
        self.assertEqual(result[-1]['end'], 400)

    def test_checkpoint_reuses_success_and_counts_all_steps(self):
        notes._CACHE_DIR = self.root / '.checkpoints'
        with patch.object(notes, '_request_api', return_value=('笔记', {"input_tokens": 5, "output_tokens": 7})) as request:
            for _ in range(2):
                notes.call_api([], 'system', 'secret', 'model', 'url', 10)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(notes._API_TOTAL['input_tokens'], 10)
        self.assertNotIn('secret', next(notes._CACHE_DIR.iterdir()).read_text(encoding='utf-8'))

    def test_truncated_output_is_not_cached(self):
        notes._CACHE_DIR = self.root / '.checkpoints'
        with patch.object(notes, '_request_api', return_value=('半篇', {'_stop': 'max_tokens'})):
            with self.assertRaises(RuntimeError):
                notes.call_api([], '', '', '', '', 10)
        self.assertFalse(notes._CACHE_DIR.exists())

    def test_export_failure_is_partial_with_markdown_kept(self):
        args = argparse.Namespace(quality='balanced', format='pdf,docx')
        with patch.object(notes, 'make_extras', return_value=''), patch.object(notes, 'ensure_pdf', return_value=''), patch.object(notes, 'ensure_docx', return_value=''):
            result = notes.finalize(args, self.root, '标题', '正文', {}, {}, 'model', [], '', 'single', '', '')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['warnings']), 2)
        self.assertTrue((self.root / 'notes.md').exists())

    def test_cli_processes_every_separate_input(self):
        cli = ['video2notes.py', '-i', 'a.mp4', '-i', 'b.mp4', '-o', str(self.root), '--format', 'md']
        result = {'title': '标题', 'md': '正文', 'usage': {}}
        with patch.object(sys, 'argv', cli), patch.object(notes, 'resolve_api_key', return_value='test'), patch.object(notes, 'run_frames') as frames, patch.object(notes, 'make_notes', return_value=result), patch.object(notes, 'finalize'):
            notes.main()
        self.assertEqual([c.args[0].input for c in frames.call_args_list], ['a.mp4', 'b.mp4'])

    def test_chrome_import_handler_exists(self):
        self.assertTrue(callable(server.Handler.import_cookies))

    def test_interrupted_job_restores_original_inputs_after_restart(self):
        with patch.object(server, 'OUTPUTS', self.root), patch.dict(server.JOBS, {}, clear=True):
            job = server.Job([{'input': 'BV1234567890', 'isLocal': False}], 'single', '标题', 'md', 'precise', 'small', False)
            job.status = 'running'
            server.save_job_state(job)
            server.load_outputs_index()
            restored = server.JOBS[job.id]
            self.assertEqual(restored.status, 'cancelled')
            self.assertEqual(restored.input, 'BV1234567890')
            self.assertEqual(restored.quality, 'precise')
            self.assertEqual(restored.asr_model, 'small')

    def test_complete_generation_is_clean_and_usage_includes_outline_and_extras(self):
        (self.root / 'manifest.json').write_text('{"duration_sec":20}', encoding='utf-8')
        (self.root / 'transcript.txt').write_text('[00:00 - 00:20] 核心内容', encoding='utf-8')
        replies = [('[{"title":"概念","start":0,"end":20}]', {'input_tokens': 2, 'output_tokens': 3}),
                   ('## 概念 — [00:00]\n结论（转录自 f_0011_10-07.jpg ）', {'input_tokens': 5, 'output_tokens': 7}),
                   ('## 自测\n问题和答案', {'input_tokens': 11, 'output_tokens': 13})]
        cli = ['video2notes.py', '--from-out', '-i', str(self.root), '-o', str(self.root), '--format', 'md']
        with patch.object(sys, 'argv', cli), patch.object(notes, 'resolve_api_key', return_value='test'), patch.object(notes, '_request_api', side_effect=replies):
            task = notes.main()
        self.assertEqual(task['status'], 'done')
        self.assertEqual(task['usage'], {'input_tokens': 18, 'output_tokens': 23})
        text = (self.root / 'notes.md').read_text(encoding='utf-8')
        self.assertNotIn('[00:00]', text)
        self.assertNotIn('f_0011', text)
        self.assertTrue((self.root / 'outline.json').exists())
        self.assertTrue((self.root / 'chapters' / '001.md').exists())


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'bilibili.txt'
        auth._CACHE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def cookie(self, expiry, name='SESSDATA', prefix=''):
        self.path.write_text('# Netscape HTTP Cookie File\n' + prefix + '.bilibili.com\tTRUE\t/\tTRUE\t' + str(expiry) + '\t' + name + '\ttest-value\n', encoding='utf-8')

    def test_missing_and_expired_credentials_do_not_mean_logged_in(self):
        self.assertEqual(auth.login_status(self.path)['status'], 'missing')
        self.cookie(1)
        with patch.object(auth, 'verify_jar') as verify:
            self.assertEqual(auth.login_status(self.path)['status'], 'expired')
            verify.assert_not_called()

    def test_other_cookies_are_not_login(self):
        self.cookie(0, 'buvid3')
        self.assertFalse(auth.login_status(self.path)['loggedIn'])

    def test_httponly_cookie_survives_restart(self):
        self.cookie(0, prefix='#HttpOnly_')
        with patch.object(auth, 'verify_jar', return_value={'status': 'valid', 'loggedIn': True}) as verify:
            self.assertTrue(auth.login_status(self.path)['loggedIn'])
            auth.login_status(self.path)
            self.assertEqual(verify.call_count, 1)
            auth._CACHE.clear()  # 模拟服务重启
            self.assertTrue(auth.login_status(self.path)['loggedIn'])
            self.assertEqual(verify.call_count, 2)

    def test_network_failure_preserves_saved_credentials(self):
        self.cookie(0)
        before = self.path.read_bytes()
        opener = Mock()
        opener.open.side_effect = TimeoutError()
        with patch.object(auth.urllib.request, 'build_opener', return_value=opener):
            result = auth.login_status(self.path)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['loggedIn'])
        self.assertEqual(self.path.read_bytes(), before)

    def test_remote_rejection_is_expired(self):
        opener = Mock()
        response = Mock()
        response.read.return_value = b'{"code":-101}'
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(auth.urllib.request, 'build_opener', return_value=opener):
            self.assertEqual(auth.verify_jar(http.cookiejar.CookieJar())['status'], 'expired')

    def test_qr_does_not_accept_unrelated_cookie(self):
        session = Mock()
        session.get.return_value.json.return_value = {'data': {'code': 0}}
        session.cookies = []
        server.QR_SESSIONS['test'] = {}
        with patch.object(server, 'write_netscape_from_jar') as write:
            server.qr_worker('test', 'test-key', session)
        self.assertEqual(server.QR_SESSIONS.pop('test')['status'], 'error')
        write.assert_not_called()

    def test_qr_persists_valid_session(self):
        import requests
        session = requests.Session()
        session.cookies.set('SESSDATA', 'test-only', domain='.bilibili.com', path='/')
        session.get = Mock()
        session.get.return_value.json.return_value = {'data': {'code': 0}}
        server.QR_SESSIONS['test'] = {}
        with patch.object(server, 'COOKIES', self.path), patch.object(server, 'verify_jar', return_value={'status': 'valid'}):
            server.qr_worker('test', 'test-key', session)
        self.assertEqual(server.QR_SESSIONS.pop('test')['status'], 'ok')
        self.assertTrue(any(c.name == 'SESSDATA' for c in auth.load_jar(self.path)))

    def test_qr_expired(self):
        session = Mock()
        session.get.return_value.json.return_value = {'data': {'code': 86038}}
        server.QR_SESSIONS['test'] = {}
        server.qr_worker('test', 'test-key', session)
        self.assertEqual(server.QR_SESSIONS.pop('test')['status'], 'expired')


if __name__ == '__main__':
    unittest.main()
