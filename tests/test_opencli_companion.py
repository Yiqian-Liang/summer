import json
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from app.opencli_companion import (
    CompanionError,
    CompanionServer,
    normalize_origin,
    normalize_results,
    run_xiaohongshu_search,
    validate_search_payload,
)


class OpenCLICompanionTest(unittest.TestCase):
    def test_validation_and_result_normalization(self):
        self.assertEqual(normalize_origin("HTTPS://Example.COM:443/"), "https://example.com")
        with self.assertRaises(ValueError):
            normalize_origin("https://example.com/path")
        self.assertEqual(validate_search_payload({"query": "  暑研   计算机  ", "limit": 5}), ("暑研 计算机", 5))
        self.assertEqual(validate_search_payload({"query": "暑研", "limit": 100}), ("暑研", 100))
        with self.assertRaises(CompanionError):
            validate_search_payload({"query": "暑研", "limit": 101})
        rows = normalize_results(
            [
                {"title": "第一条", "url": "https://www.xiaohongshu.com/explore/abc", "author": "A"},
                {"title": "恶意链接", "url": "javascript:alert(1)"},
                {"title": "其他站", "url": "https://example.com/post"},
            ],
            20,
        )
        self.assertEqual(rows, [{
            "rank": 1,
            "title": "第一条",
            "author": "A",
            "likes": "",
            "published_at": "",
            "url": "https://www.xiaohongshu.com/explore/abc",
        }])

    @patch("app.opencli_companion.resolve_opencli", return_value="/usr/local/bin/opencli")
    @patch("app.opencli_companion.subprocess.run")
    def test_opencli_uses_fixed_argv_and_json(self, run, _resolve):
        run.return_value = subprocess.CompletedProcess(
            [],
            0,
            stdout=json.dumps([
                {"title": "暑研机会", "url": "https://www.xiaohongshu.com/explore/abc", "likes": "12"}
            ]),
            stderr="",
        )
        query = "暑研; touch /tmp/should-not-exist"
        rows = run_xiaohongshu_search(query, 5, "opencli", 30)
        command = run.call_args.args[0]
        self.assertIn(query, command)
        self.assertEqual(command.count(query), 1)
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["env"]["OPENCLI_BROWSER_CONNECT_TIMEOUT"], "8")
        self.assertEqual(rows[0]["title"], "暑研机会")

    def test_http_origin_allowlist(self):
        def fake_search(query, limit, executable, timeout):
            return [{
                "rank": 1,
                "title": query,
                "author": "",
                "likes": "",
                "published_at": "",
                "url": "https://www.xiaohongshu.com/explore/abc",
            }]

        try:
            server = CompanionServer(0, {"https://summer.example"}, "opencli", 10, fake_search)
        except PermissionError:
            self.skipTest("Local socket binding is blocked in this environment")

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/search/xiaohongshu"
        body = json.dumps({"query": "暑研", "limit": 5}).encode()
        try:
            allowed = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Origin": "https://summer.example"},
            )
            with urllib.request.urlopen(allowed, timeout=2) as response:
                payload = json.load(response)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://summer.example")
                self.assertEqual(payload["results"][0]["title"], "暑研")

            denied = urllib.request.Request(
                url,
                data=body,
                method="POST",
                headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
            )
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(denied, timeout=2)
            self.assertEqual(error.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
