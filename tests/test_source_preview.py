import io
import socket
import unittest
from unittest.mock import patch

import requests

from app.source_preview import MAX_REDIRECTS, fetch_source_preview, is_previewable_url


def response(url, location=None):
    result = requests.Response()
    result.url = url
    result.status_code = 302 if location else 200
    result.headers["Content-Type"] = "text/html; charset=utf-8"
    if location:
        result.headers["Location"] = location
    result.raw = io.BytesIO(b'<meta name="description" content="A public research opportunity.">')
    return result


class SourcePreviewTests(unittest.TestCase):
    def test_private_redirect_is_never_requested(self):
        for destination in ("http://127.0.0.1/private", "http://169.254.169.254/meta", "http://[::1]/", "http://internal.example/", "http://100.64.0.1/"):
            with self.subTest(destination=destination):
                first = response("https://8.8.8.8/start", destination)
                private_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0))]
                with patch("app.source_preview.socket.getaddrinfo", return_value=private_dns), patch("app.source_preview.requests.get", return_value=first) as get:
                    self.assertFalse(fetch_source_preview(first.url)["ok"])
                self.assertEqual(get.call_count, 1)
                self.assertFalse(get.call_args.kwargs["allow_redirects"])
                self.assertTrue(first.raw.closed)

    def test_relative_public_redirect_and_excerpt(self):
        first = response("https://8.8.8.8/start", "/details")
        second = response("https://8.8.8.8/details")
        with patch("app.source_preview.requests.get", side_effect=[first, second]) as get:
            result = fetch_source_preview(first.url)
        self.assertTrue(result["ok"])
        self.assertEqual(result["final_url"], second.url)
        self.assertEqual(result["excerpt"], "A public research opportunity.")
        self.assertEqual(get.call_args_list[1].args[0], second.url)
        self.assertTrue(first.raw.closed)

    def test_redirect_loop_is_bounded(self):
        responses = [response("https://8.8.8.8/loop", "/loop") for _ in range(MAX_REDIRECTS + 1)]
        with patch("app.source_preview.requests.get", side_effect=responses) as get:
            self.assertFalse(fetch_source_preview(responses[0].url)["ok"])
        self.assertEqual(get.call_count, MAX_REDIRECTS + 1)
        self.assertTrue(all(item.raw.closed for item in responses))

    def test_unresolved_or_malformed_urls_fail_closed(self):
        with patch("app.source_preview.socket.getaddrinfo", side_effect=socket.gaierror), patch("app.source_preview.requests.get") as get:
            self.assertFalse(fetch_source_preview("https://unresolved.example/")["ok"])
            get.assert_not_called()
        for url in ("http://[invalid]/", "http://8.8.8.8:invalid/", "https://user:password@8.8.8.8/"):
            self.assertFalse(is_previewable_url(url))


if __name__ == "__main__":
    unittest.main()
