import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:' + str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()

    def test_web_assets(self):
        for path in ('/', '/static/app.js', '/static/styles.css'):
            with urllib.request.urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)

    def test_rejects_foreign_origin_and_host(self):
        for headers in ({'Origin': 'https://untrusted.example'}, {'Host': 'untrusted.example'}):
            request = urllib.request.Request(self.base + '/api/fs', headers=headers)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)

    def test_instance_endpoint_identifies_this_project(self):
        with urllib.request.urlopen(self.base + '/api/instance') as response:
            instance = json.load(response)
        self.assertEqual(Path(instance['root']), server.HERE)
        from video_notes import __version__
        self.assertEqual(instance['version'], __version__)

    def test_status_uses_remote_verification_result(self):
        with patch.object(server, 'login_status', return_value={'status': 'unknown', 'loggedIn': None, 'count': 8}):
            with urllib.request.urlopen(self.base + '/api/cookies/status') as response:
                result = json.load(response)
        self.assertEqual(result['status'], 'unknown')
        self.assertIsNone(result['loggedIn'])

    def test_directory_expansion_and_both_batch_modes(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outputs, patch.object(server, 'OUTPUTS', Path(outputs)):
            for name in ('课1.mp4', '课2.mp4'):
                (Path(folder) / name).touch()
            request = urllib.request.Request(self.base + '/api/sources/expand', data=json.dumps({'type': 'directory', 'input': folder}).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                expanded = json.load(response)
            self.assertEqual(expanded['count'], 2)
            with patch.object(server, 'resolve_api_key', return_value='test'), patch.object(server, 'start_job'), patch.dict(server.JOBS, {}, clear=True):
                for mode in ('merged', 'separate'):
                    request = urllib.request.Request(self.base + '/api/jobs', data=json.dumps({'inputs': expanded['inputs'], 'mode': mode, 'format': 'md'}).encode(), headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request) as response:
                        result = json.load(response)['job']
                    self.assertEqual(result['mode'], mode)
                    self.assertEqual(len(result['inputs']), 2)

    def test_collection_expansion_endpoint(self):
        inputs = [{'input': 'https://www.bilibili.com/video/BV1234567890', 'isLocal': False, 'title': '第一课'}]
        with patch.object(server, 'collection_videos', return_value={'title': '合集', 'inputs': inputs}):
            request = urllib.request.Request(self.base + '/api/sources/expand', data=b'{"type":"collection","input":"BV1234567890"}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(json.load(response)['inputs'], inputs)

    def test_preview_is_sandboxed_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'OUTPUTS', Path(directory)):
            job = server.Job([{'input': 'test.mp4'}], 'single', 'Test', 'md', 'fast', '', False)
            (job.dir / 'notes.md').write_text('# Test\n\nHello', encoding='utf-8')
            with patch.dict(server.JOBS, {job.id: job}):
                with urllib.request.urlopen(self.base + '/api/jobs/' + job.id + '/preview/notes.md') as response:
                    self.assertIn('sandbox', response.headers['Content-Security-Policy'])
                    self.assertIn('Hello', response.read().decode())
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(self.base + '/api/jobs/' + job.id + '/preview/../notes.md')
                self.assertEqual(error.exception.code, 404)

    def test_retry_reuses_task_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(server, 'OUTPUTS', Path(directory)):
            job = server.Job([{'input': 'test.mp4'}], 'single', 'Test', 'md', 'fast', '', False)
            job.status = 'partial'
            folder = job.dir
            with patch.dict(server.JOBS, {job.id: job}), patch.object(server, 'start_job') as start:
                request = urllib.request.Request(self.base + '/api/jobs/' + job.id + '/retry', data=b'{}', headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request) as response:
                    self.assertEqual(json.load(response)['job']['status'], 'queued')
                start.assert_called_once_with(job)
                self.assertEqual(job.dir, folder)
                self.assertTrue(job.options['resume'])


if __name__ == '__main__':
    unittest.main()
