import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from video_notes import batch_sources as batch
import server


BV1, BV2, BV3 = 'BV1234567890', 'BV1234567891', 'BV1234567892'


class BatchTests(unittest.TestCase):
    def test_canonical_urls_drop_tracking_and_keep_distinct_parts(self):
        self.assertEqual(batch.canonical_video_input(BV1), batch.canonical_video_input('https://www.bilibili.com/video/' + BV1 + '/?spm_id_from=123'))
        self.assertNotEqual(batch.canonical_video_input('https://www.bilibili.com/video/' + BV1 + '?p=1'), batch.canonical_video_input('https://www.bilibili.com/video/' + BV1 + '?p=2'))
    def test_directory_natural_order_and_recursive_option(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('课10.mp4', '课2.MP4', '课1.mkv', '说明.txt', '音频.mp3'):
                (root / name).touch()
            (root / '子目录').mkdir()
            (root / '子目录' / '课3.mov').touch()
            direct = batch.directory_videos(folder)
            self.assertEqual([v['title'] for v in direct['inputs']], ['课1', '课2', '课10'])
            recursive = batch.directory_videos(folder, True)
            self.assertEqual(len(recursive['inputs']), 4)
            self.assertTrue(all(v['isLocal'] for v in recursive['inputs']))

    def test_empty_invalid_and_blank_directories_report_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            for path in ('', folder, str(Path(folder) / 'missing')):
                with self.assertRaises(ValueError):
                    batch.directory_videos(path)

    def test_collection_pagination_preserves_order(self):
        calls = []
        def get(endpoint, params):
            calls.append(params)
            page = params['page_num']
            return {'page': {'total': 3}, 'meta': {'name': '合集名称'}, 'archives':
                    [{'bvid': x, 'title': x} for x in ([BV2, BV1] if page == 1 else [BV3])]}
        result = batch._collection_videos('https://space.bilibili.com/123/lists/456?type=season', get)
        self.assertEqual(result['title'], '合集名称')
        self.assertEqual([i['input'].split('/')[-1] for i in result['inputs']], [BV2, BV1, BV3])
        self.assertEqual([p['page_num'] for p in calls], [1, 2])

    def test_series_and_legacy_collection_urls(self):
        def get(endpoint, params):
            if endpoint == '/x/series/series':
                return {'meta': {'name': '系列'}}
            return {'page': {'total': 1}, 'archives': [{'bvid': BV1, 'title': '课程'}]}
        for url in ('https://space.bilibili.com/123/lists/456?type=series',
                    'https://space.bilibili.com/123/channel/seriesdetail?sid=456',
                    'https://space.bilibili.com/123/channel/collectiondetail?sid=456'):
            self.assertEqual(len(batch._collection_videos(url, get)['inputs']), 1)

    def test_video_link_discovers_its_collection(self):
        def get(endpoint, params):
            if endpoint == '/x/web-interface/view':
                return {'ugc_season': {'id': 9, 'mid': 7}}
            return {'page': {'total': 2}, 'meta': {'name': '合集'}, 'archives': [{'bvid': BV1}, {'bvid': BV2}]}
        self.assertEqual(len(batch._collection_videos(BV1, get)['inputs']), 2)

    def test_multipart_video_keeps_every_part(self):
        def get(endpoint, params):
            return {'bvid': BV1, 'title': '多P课程', 'pages': [{'page': 1, 'part': '第一课'}, {'page': 2, 'part': '第二课'}]}
        result = batch._collection_videos(BV1, get)
        self.assertEqual([v['input'] for v in result['inputs']], [
            'https://www.bilibili.com/video/' + BV1 + '?p=1', 'https://www.bilibili.com/video/' + BV1 + '?p=2'])

    def test_incomplete_playlist_is_not_silently_truncated(self):
        def get(endpoint, params):
            return {'page': {'total': 2}, 'archives': [{'bvid': BV1}]}
        with self.assertRaisesRegex(ValueError, '完整'):
            batch._collection_videos('https://space.bilibili.com/1/lists/2', get)

    def test_unrelated_domain_and_single_video_rejected(self):
        for value in ('https://bilibili.com.evil.test/1/lists/2', 'https://example.com', BV1):
            with self.assertRaises(ValueError):
                batch._collection_videos(value, lambda *_: {'pages': [{'page': 1}]})

    def test_separate_batch_continues_after_one_failure(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'OUTPUTS', Path(folder)):
            job = server.Job([{'input': BV1, 'title': '第一课'}, {'input': BV2, 'title': '第二课'}],
                             'separate', '课程合集', 'md', 'fast', '', False)
            calls = []
            def run(job, cmd, outdir, *rest):
                calls.append(outdir)
                if len(calls) == 1:
                    raise RuntimeError('Unavailable video')
                outdir.mkdir(parents=True)
                (outdir / 'task.json').write_text(json.dumps({'title': '第二课', 'warnings': [], 'outputs': []}), encoding='utf-8')
            with patch.object(server, '_run_cmd', side_effect=run):
                server.job_worker(job)
            self.assertEqual(len(calls), 2)
            self.assertEqual(job.status, 'partial')

    def test_merged_batch_uses_input_file(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(server, 'OUTPUTS', Path(folder)):
            job = server.Job([{'input': BV1}, {'input': BV2}], 'merged', '课程合集', 'md', 'fast', '', False)
            commands = []
            def run(job, cmd, outdir, *rest):
                commands.append(cmd)
                path = Path(cmd[cmd.index('--inputs-json') + 1])
                self.assertEqual(json.loads(path.read_text(encoding='utf-8')), [BV1, BV2])
            with patch.object(server, '_run_cmd', side_effect=run):
                server.job_worker(job)
            self.assertIn('--merge', commands[0])
            self.assertEqual(job.status, 'done')


if __name__ == '__main__':
    unittest.main()
