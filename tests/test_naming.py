import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes import naming
import server
import video2notes as notes


class NamingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.outputs = patch.object(server, 'OUTPUTS', self.root)
        self.outputs.start()

    def tearDown(self):
        self.outputs.stop()
        self.temp.cleanup()

    def test_task_directory_uses_timestamp_and_title(self):
        job = server.Job([{'input': 'test.mp4'}], 'single', '视频标题', 'pdf', 'fast', '', False)
        self.assertEqual(job.dir.name, job.id[:15] + '-视频标题')

    def test_duplicate_task_does_not_overwrite(self):
        first = naming.task_directory(self.root, '20261103-110144', '视频标题')
        second = naming.task_directory(self.root, '20261103-110144', '视频标题')
        self.assertEqual(second.name, first.name + ' (2)')

    def test_safe_windows_filenames(self):
        self.assertEqual(naming.safe_name('A/B: C?'), 'A_B_ C_')
        self.assertEqual(naming.safe_name('CON'), '_CON')
        self.assertEqual(naming.safe_name('中文 标题。'), '中文 标题。')

    def test_pdf_receives_explicit_title_filename(self):
        (self.root / 'notes.md').write_text('# 正文', encoding='utf-8')
        def render(cmd, **kwargs):
            target = Path(cmd[cmd.index('-Pdf') + 1])
            self.assertEqual(target.name, '真实视频标题.render.pdf')
            target.write_bytes(b'x' * 3000)
            return Mock(stdout='', stderr='', returncode=0)
        with patch.object(notes.subprocess, 'run', side_effect=render):
            result = notes.ensure_pdf(self.root, '真实视频标题')
        self.assertEqual(Path(result).name, '真实视频标题.pdf')
        self.assertFalse((self.root / 'notes.pdf').exists())

    def test_failed_pdf_export_does_not_accept_stale_pdf(self):
        (self.root / 'notes.md').write_text('# 正文', encoding='utf-8')
        old = self.root / '视频.pdf'
        old.write_bytes(b'original' * 1000)
        with patch.object(notes.subprocess, 'run', return_value=Mock(returncode=1, stdout='', stderr='Export failed')):
            self.assertEqual(notes.ensure_pdf(self.root, '视频'), '')
        self.assertEqual(old.read_bytes(), b'original' * 1000)

    def test_metadata_supplies_title_without_manual_read_title(self):
        (self.root / 'source.info.json').write_text('{"title":"真实标题"}', encoding='utf-8')
        (self.root / 'manifest.json').write_text('{"input":"https://www.bilibili.com/video/BV123"}', encoding='utf-8')
        manifest, *_ = notes.load_outputs(self.root)
        args = argparse.Namespace(title=None, input=manifest['input'])
        self.assertEqual(notes.find_title(args, manifest), '真实标题')

    def test_url_argument_never_overrides_real_video_title(self):
        args = argparse.Namespace(title='https://www.bilibili.com/video/BV123', input='BV123')
        self.assertEqual(notes.find_title(args, {'title': '真实标题'}), '真实标题')

    def test_document_heading_is_synchronized_without_rewriting_body(self):
        text = '# https://www.bilibili.com/video/BV123\n\n## 知识点\n正文\n'
        self.assertEqual(naming.document_title(text, '真实标题'), '# 真实标题\n\n## 知识点\n正文')

    def test_finalize_replaces_url_heading(self):
        (self.root / 'source.info.json').write_text('{"title":"真实标题"}', encoding='utf-8')
        args = argparse.Namespace(quality='fast', format='md')
        with patch.object(notes, 'make_extras', return_value=''):
            result = notes.finalize(args, self.root, 'https://example.com/video', '# https://example.com/video\n\n正文', {}, {}, '', [], '', 'single', '', '')
        self.assertEqual(result['title'], '真实标题')
        self.assertTrue((self.root / 'notes.md').read_text(encoding='utf-8').startswith('# 真实标题\n'))

    def test_existing_task_migration_and_history_reload(self):
        old = self.root / '20261103-110144-775c24-https-www-bilibili-com-video-BV1VC7g'
        old.mkdir()
        (old / 'source.info.json').write_text('{"title":"真实视频标题"}', encoding='utf-8')
        (old / 'notes.pdf').write_bytes(b'original-pdf')
        (old / 'notes.md').write_text('# 正文', encoding='utf-8')
        (old / 'task.json').write_text(json.dumps({'title': 'https://www.bilibili.com/video/BV123', 'videos': [{'input': 'BV123'}],
                                                 'outputs': [str(old / 'notes.pdf')], 'status': 'done'}), encoding='utf-8')
        job = server.Job([{'input': 'BV123'}], 'single', '旧标题', 'pdf', 'fast', '', False,
                         restore_dir=old, job_id='20261103-110144-775c24')
        job.status = 'done'
        server.finalize_job_names(job)
        self.assertEqual(job.dir.name, '20261103-110144-真实视频标题')
        self.assertEqual((job.dir / '真实视频标题.pdf').read_bytes(), b'original-pdf')
        data = json.loads((job.dir / 'task.json').read_text(encoding='utf-8'))
        self.assertEqual(data['outputs'], [str(job.dir / '真实视频标题.pdf')])
        server.save_job_state(job)
        with patch.dict(server.JOBS, {}, clear=True):
            server.load_outputs_index()
            self.assertEqual(server.JOBS[job.id].dir, job.dir)
            self.assertEqual(server.JOBS[job.id].title, '真实视频标题')
        count = len(list(self.root.iterdir()))
        server.finalize_job_names(job)
        self.assertEqual(len(list(self.root.iterdir())), count)

    def test_numbered_task_name_is_stable(self):
        (self.root / '20261103-110144-标题').mkdir()
        d = self.root / '20261103-110144-标题 (2)'
        d.mkdir()
        job = server.Job([{'input': 'test.mp4'}], 'single', '标题', 'pdf', 'fast', '', False,
                         restore_dir=d, job_id='20261103-110144-775c24')
        server.finalize_job_names(job)
        self.assertEqual(job.dir, d)


if __name__ == '__main__':
    unittest.main()
