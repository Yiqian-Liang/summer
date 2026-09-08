import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import app.mediacrawler_runner as runner
import app.mediacrawler_import as importer
from app.mediacrawler_import import import_mediacrawler_path, media_record_to_item
from app.scheduler import OpportunityProcessor


class MediaCrawlerImportTestCase(unittest.TestCase):
    def test_runner_prefers_checkout_inside_project(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "summer"
            expected = project_root / "MediaCrawler"
            expected.mkdir(parents=True)
            with patch.dict(os.environ, {}, clear=True), patch.object(runner, "PROJECT_ROOT", project_root):
                self.assertEqual(runner.mediacrawler_root(), expected.resolve())

    def test_empty_export_path_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(FileNotFoundError, "No MediaCrawler content exports"):
                import_mediacrawler_path(temp_dir, dry_run=True)

    def test_maps_supported_platform_records(self):
        xhs = media_record_to_item({
            "note_id": "abc",
            "title": "暑研招募",
            "desc": "课题组招募科研实习生",
            "note_url": "https://www.xiaohongshu.com/explore/abc?xsec_token=token",
            "source_keyword": "暑研",
        }, "xiaohongshu")
        zhihu = media_record_to_item({
            "title": "博士招生",
            "content_text": "<p>实验室招收博士生</p>",
            "content_url": "https://www.zhihu.com/question/1/answer/2",
        }, "zhihu")
        bili = media_record_to_item({
            "video_id": "123",
            "title": "科研实习信息",
            "desc": "暑期科研项目申请",
        }, "bilibili")

        self.assertEqual(xhs["provider"], "mediacrawler")
        self.assertEqual(zhihu["description"], "实验室招收博士生")
        self.assertEqual(bili["url"], "https://www.bilibili.com/video/av123")

    def test_imports_jsonl_content_and_ignores_comment_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            content_dir = root / "xhs" / "jsonl"
            content_dir.mkdir(parents=True)
            content_file = content_dir / "search_contents_2026-07-13.jsonl"
            content_file.write_text(json.dumps({
                "note_id": "abc",
                "title": "NLP 暑研招募",
                "desc": "课题组招募 NLP 科研实习生",
                "note_url": "https://www.xiaohongshu.com/explore/abc?xsec_token=token",
                "source_keyword": "NLP 暑研",
                "time": 1783872000000,
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            (content_dir / "search_comments_2026-07-13.jsonl").write_text(
                json.dumps({"content": "comment"}) + "\n",
                encoding="utf-8",
            )

            imported = []
            stats = import_mediacrawler_path(
                root,
                processor=OpportunityProcessor(),
                upsert=lambda item: imported.append(item) or True,
            )

        self.assertEqual(stats["files"], 1)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(imported[0]["source_type"], "xiaohongshu")
        self.assertEqual(imported[0]["provider"], "mediacrawler")
        self.assertEqual(imported[0]["source_query"], "NLP 暑研")

    def test_maps_weibo_and_tieba_records(self):
        weibo = media_record_to_item({
            "note_id": "Pabc123",
            "content": "暑期科研项目开放申请，欢迎本科生联系",
            "note_url": "https://m.weibo.cn/detail/Pabc123",
            "create_time": 1783872000,
            "nickname": "某导师",
            "source_keyword": "暑研",
        }, "weibo")
        tieba = media_record_to_item({
            "note_id": "999888",
            "title": "2026 暑研信息汇总贴",
            "desc": "汇总各学校暑研招募信息",
            "note_url": "https://tieba.baidu.com/p/999888",
            "publish_time": "2026-07-01 10:00",
            "user_nickname": "吧友甲",
        }, "tieba")

        self.assertEqual(weibo["source_type"], "weibo")
        self.assertEqual(weibo["title"][:10], "暑期科研项目开放申请"[:10])
        self.assertEqual(weibo["author"], "某导师")
        self.assertEqual(weibo["provider"], "mediacrawler")
        self.assertEqual(tieba["source_type"], "tieba")
        self.assertEqual(tieba["url"], "https://tieba.baidu.com/p/999888")
        self.assertEqual(tieba["author"], "吧友甲")

    def test_infers_platform_from_weibo_directory_and_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            weibo_dir = root / "weibo" / "jsonl"
            weibo_dir.mkdir(parents=True)
            (weibo_dir / "search_contents_2026-07-19.jsonl").write_text(
                json.dumps({
                    "note_id": "Pabc123",
                    "content": "课题组招收暑期科研实习生",
                    "note_url": "https://m.weibo.cn/detail/Pabc123",
                    "create_time": 1783872000,
                    "nickname": "某导师",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            imported = []
            stats = import_mediacrawler_path(
                root,
                processor=OpportunityProcessor(),
                upsert=lambda item: imported.append(item) or True,
            )

        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(imported[0]["source_type"], "weibo")
        self.assertEqual(imported[0]["source_url"], "https://m.weibo.cn/detail/Pabc123")

    def test_bad_export_rows_are_counted_and_do_not_drop_later_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            content_dir = Path(temp_dir) / "xhs"
            content_dir.mkdir()
            (content_dir / "search_contents.jsonl").write_text("\n".join([
                "not json",
                "[]",
                json.dumps({"note_url": "https://[invalid", "title": "bad"}),
                json.dumps({"note_url": "https://xiaohongshu.com.evil.test/post", "title": "wrong domain"}),
                json.dumps({"note_url": "https://m.weibo.cn/detail/123", "content": "暑研招募"}),
            ]), encoding="utf-8")
            imported = []
            stats = import_mediacrawler_path(content_dir, upsert=lambda item: imported.append(item) or True)
        self.assertEqual(stats["read"], 5)
        self.assertEqual(stats["errors"], 3)
        self.assertEqual(stats["skipped"], 1)
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(imported[0]["source_type"], "weibo")

    def test_reconstructs_platform_urls_without_corrupting_video_ids(self):
        for video_id, expected in [("BV1abc", "BV1abc"), ("av123", "av123"), ("123", "av123")]:
            item = media_record_to_item({"video_id": video_id, "title": "科研实习"}, "bilibili")
            self.assertEqual(item["url"], "https://www.bilibili.com/video/" + expected)
        answer = media_record_to_item({"content_id": "2", "question_id": "1", "content_type": "answer", "title": "暑研"}, "zhihu")
        self.assertEqual(answer["url"], "https://www.zhihu.com/question/1/answer/2")
        tieba = media_record_to_item({"note_id": "3", "title": "暑研"}, "tieba")
        self.assertEqual(tieba["url"], "https://tieba.baidu.com/p/3")

    def test_dry_run_entrypoints_do_not_initialize_database(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for module, argv in [
                (importer, ["import", temp_dir, "--dry-run"]),
                (runner, ["runner", "--dry-run"]),
            ]:
                with patch.object(sys, "argv", argv), patch.object(module, "init_db") as init, patch.object(
                    module, "import_mediacrawler_path", return_value={"inserted": 0}
                ), patch("builtins.print"):
                    module.main()
                init.assert_not_called()

    def test_runner_timeout_stops_the_crawler_process_group(self):
        process = MagicMock()
        process.pid = 12345
        process.wait.side_effect = [subprocess.TimeoutExpired("crawler", 1), -9]
        with patch.object(runner, "_runner_command", return_value=["python", "main.py"]), patch.object(
            runner.subprocess, "Popen"
        ) as popen, patch.object(runner.os, "killpg") as killpg:
            popen.return_value.__enter__.return_value = process
            code = runner.run_platform_crawl(Path("/tmp"), "xhs", ["暑研"], timeout=1)
        self.assertEqual(code, 124)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        killpg.assert_called_once_with(12345, runner.signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
