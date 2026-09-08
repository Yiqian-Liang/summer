import os
import json
import threading
import unittest
from unittest.mock import patch

from app.scrapers import AcademicScraper, GeminiSearcher, SerpApiScraper, SogouWechatScraper


SOGOU_SAMPLE_HTML = """
<html><body>
<ul class="news-list">
<li id="sogou_vr_11002601_box_0">
  <div class="txt-box">
    <h3><a target="_blank" href="/link?url=abc123&amp;type=2&amp;query=%E6%9A%91%E7%A0%94&amp;token=tok">关于<em><!--red_beg-->暑研<!--red_end--></em>那些事 | 暑期科研项目全攻略</a></h3>
    <p class="txt-info">最近很多同学在询问暑期科研的事情:什么时候申请<em><!--red_beg-->暑研<!--red_end--></em>项目...</p>
    <div class="s-p">
      <span class="all-time-y2">再来人硕博留学</span><span class="s2"><script>document.write(timeConvert('1529987349'))</script></span>
    </div>
  </div>
</li>
<li id="sogou_vr_11002601_box_1">
  <div class="txt-box">
    <h3><a target="_blank" href="/link?url=def456&amp;type=2">2026 博士招生信息汇总</a></h3>
    <p class="txt-info">各实验室博士招生名额与申请时间线</p>
    <div class="s-p" t="1783872000">
      <a class="account">保研论坛</a><span class="s2"></span>
    </div>
  </div>
</li>
</ul>
</body></html>
"""

SOGOU_ANTISPIDER_HTML = "<html><body>用户您好，您的访问过于频繁，请输入验证码</body></html>"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeResponse(self.payload)


class FakeSerp:
    api_key = "serp-key"

    def __init__(self):
        self.calls = []

    def search_scholar(self, query, raise_errors=False):
        self.calls.append("scholar")
        return [{"title": "Paper", "url": "https://example.org/paper", "source_type": "scholar"}]

    def search_social_media(self, query, platform, raise_errors=False):
        self.calls.append(platform)
        return [{
            "title": f"{platform} PhD position",
            "description": "Open doctoral position",
            "url": f"https://example.org/{platform}",
            "source_type": platform,
        }]


class FakeGemini:
    api_key = "gemini-key"

    def __init__(self):
        self.calls = 0

    def search_opportunities(self, query, raise_errors=False):
        self.calls += 1
        return [{
            "title": "Gemini PhD position",
            "description": "Open doctoral position",
            "url": "https://example.org/gemini",
            "source_type": "university",
        }]


class ScraperTestCase(unittest.TestCase):
    def test_gemini_parser_uses_grounding_url(self):
        payload = {
            "candidates": [{
                "content": {"parts": [{"text": '[{"title":"Vision PhD","description":"Open position","source":"University Website"}]'}]},
                "groundingMetadata": {
                    "groundingChunks": [{"web": {"uri": "https://example.edu/phd", "title": "Vision PhD"}}]
                },
            }]
        }
        results = GeminiSearcher.parse_response(payload, "vision phd")
        self.assertEqual(results[0]["url"], "https://example.edu/phd")
        self.assertEqual(results[0]["source_type"], "university")

    def test_serpapi_uses_http_api_and_normalizes_results(self):
        session = FakeSession({
            "organic_results": [{
                "title": "Research Intern",
                "link": "https://www.google.com/url?q=https%3A%2F%2Fexample.edu%2Fintern",
                "snippet": "Applications are open",
            }]
        })
        scraper = SerpApiScraper(api_key="key", session=session)
        results = scraper.search_social_media("NLP", "university")
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(results[0]["url"], "https://example.edu/intern")
        self.assertEqual(results[0]["provider"], "serpapi")

    def test_serpapi_empty_search_is_success_but_other_errors_still_fail(self):
        cases = [
            ({"error": "Google hasn't returned any results for this query."}, "fresh"),
            ({"search_metadata": {"status": "Success"}, "search_information": {"organic_results_state": "Fully empty"}}, "fresh"),
            ({"error": "rate limited", "search_metadata": {"status": "Success"}}, "error"),
            ({"error": "Invalid API key"}, "error"),
        ]
        for payload, expected_status in cases:
            with self.subTest(payload=payload), patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0"}):
                serp = SerpApiScraper(api_key="test", session=FakeSession(payload))
                scraper = AcademicScraper(serp_scraper=serp, gemini_searcher=FakeGemini())
                result = scraper.search_all_sources("暑研", source_filter="xiaohongshu", return_status=True)
            self.assertEqual(result["status"], expected_status)
            self.assertEqual(result["results"], [])
            self.assertEqual(result["failed_sources"], [] if expected_status == "fresh" else ["xiaohongshu"])

    def test_gemini_requires_a_matching_citation_in_both_languages(self):
        items = [
            {"title": "Unrelated Chemistry Position", "url": "https://invented.edu/job"},
            {"title": "Vision PhD", "url": "https://invented.edu/vision"},
            {"title": "机器学习暑研招募"},
        ]
        payload = {"candidates": [{
            "content": {"parts": [{"text": json.dumps(items)}]},
            "groundingMetadata": {"groundingChunks": [
                {"web": {"uri": "https://real.edu/vision", "title": "Vision PhD"}},
                {"web": {"uri": "https://real.edu/ml", "title": "机器学习暑研招募"}},
            ]},
        }]}
        results = GeminiSearcher.parse_response(payload, "暑研")
        self.assertEqual([item["url"] for item in results], ["https://real.edu/vision", "https://real.edu/ml"])
        del payload["candidates"][0]["groundingMetadata"]
        self.assertEqual(GeminiSearcher.parse_response(payload, "暑研"), [])

    def test_coordinator_bounds_requests_and_caches(self):
        serp = FakeSerp()
        gemini = FakeGemini()
        scraper = AcademicScraper(serp_scraper=serp, gemini_searcher=gemini)
        with patch.dict(os.environ, {
            "DISABLE_LIVE_SEARCH": "0",
            "LIVE_SEARCH_MAX_REQUESTS": "3",
            "LIVE_SEARCH_CACHE_SECONDS": "600",
            "LIVE_SEARCH_TOTAL_TIMEOUT": "5",
        }):
            first = scraper.search_all_sources("NLP PhD")
            second = scraper.search_all_sources("NLP PhD")

        self.assertEqual(gemini.calls + len(serp.calls), 3)
        self.assertEqual(len(first), 3)
        self.assertEqual(first, second)

    def test_sogou_wechat_parser_extracts_articles(self):
        results = SogouWechatScraper.parse_results(SOGOU_SAMPLE_HTML, "暑研")
        self.assertEqual(len(results), 2)
        first, second = results
        self.assertEqual(first["source_type"], "wechat")
        self.assertEqual(first["provider"], "sogou")
        self.assertIn("暑研", first["title"])
        self.assertNotIn("red_beg", first["title"])
        self.assertTrue(first["url"].startswith("https://weixin.sogou.com/link?"))
        self.assertEqual(first["author"], "再来人硕博留学")
        self.assertTrue(first["published_at"].startswith("2018-"))
        self.assertEqual(second["author"], "保研论坛")
        self.assertTrue(second["published_at"].startswith("2026-"))

    def test_sogou_wechat_antispider_returns_empty(self):
        class AntispiderResponse:
            url = "http://weixin.sogou.com/antispider/?from=%2Flink"
            text = SOGOU_ANTISPIDER_HTML

            def raise_for_status(self):
                return None

        class AntispiderSession:
            def get(self, *args, **kwargs):
                return AntispiderResponse()

        scraper = SogouWechatScraper(session=AntispiderSession())
        with patch.dict(os.environ, {"ENABLE_SOGOU_WECHAT": "1"}):
            self.assertEqual(scraper.search_wechat("暑研"), [])

    def test_sogou_wechat_runs_outside_serp_budget_for_chinese_queries(self):
        class FakeSogou:
            def __init__(self):
                self.calls = []

            def enabled(self):
                return True

            def search_wechat(self, query, raise_errors=False):
                self.calls.append(query)
                return [{
                    "title": "暑研攻略",
                    "url": "https://weixin.sogou.com/link?url=abc",
                    "source_type": "wechat",
                }]

        serp = FakeSerp()
        gemini = FakeGemini()
        sogou = FakeSogou()
        scraper = AcademicScraper(serp_scraper=serp, gemini_searcher=gemini, sogou_scraper=sogou)
        with patch.dict(os.environ, {
            "DISABLE_LIVE_SEARCH": "0",
            "LIVE_SEARCH_MAX_REQUESTS": "3",
            "LIVE_SEARCH_CACHE_SECONDS": "600",
            "LIVE_SEARCH_TOTAL_TIMEOUT": "5",
        }):
            results = scraper.search_all_sources("暑研 NLP")

        # Sogou covers WeChat for free: it runs, does not consume one of the 3
        # bounded requests, and the SerpApi wechat query is skipped.
        self.assertEqual(sogou.calls, ["暑研 NLP"])
        self.assertEqual(gemini.calls + len(serp.calls), 3)
        self.assertNotIn("wechat", serp.calls)
        self.assertTrue(any(item["source_type"] == "wechat" for item in results))

    def test_chinese_plan_covers_official_and_social_sources_within_budget(self):
        plan = AcademicScraper._platform_plan("暑研 机器学习")
        self.assertEqual(plan[:5], ["xiaohongshu", "wechat", "nsf", "university", "zhihu"])
        self.assertIn("tieba", plan)

    def test_source_availability_matches_actual_provider_capabilities(self):
        serp, gemini = FakeSerp(), FakeGemini()
        serp.api_key = gemini.api_key = None
        scraper = AcademicScraper(serp_scraper=serp, gemini_searcher=gemini)
        with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0", "ENABLE_SOGOU_WECHAT": "1"}):
            self.assertFalse(scraper.has_live_search_credentials("NLP PhD"))
            self.assertFalse(scraper.has_live_search_credentials("暑研", "xiaohongshu"))
            self.assertEqual(scraper.live_search_providers("NLP", "微信"), ["Sogou微信"])
            self.assertEqual(scraper.live_search_providers("NLP", "nsf"), ["NSF"])
            self.assertEqual(scraper.search_all_sources("NLP", return_status=True)["status"], "unavailable")

    def test_status_reports_provider_failure_and_retries_instead_of_caching(self):
        serp = SerpApiScraper(api_key="test", session=FakeSession({"error": "rate limited"}))
        gemini = FakeGemini()
        scraper = AcademicScraper(serp_scraper=serp, gemini_searcher=gemini)
        with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0", "LIVE_SEARCH_MAX_REQUESTS": "2"}):
            first = scraper.search_all_sources("NLP PhD", return_status=True)
            second = scraper.search_all_sources("NLP PhD", return_status=True)
            failed = scraper.search_all_sources("NLP", source_filter="scholar", return_status=True)
        self.assertEqual(first["status"], "partial")
        self.assertEqual(first["failed_sources"], ["euraxess"])
        self.assertEqual(second["status"], "partial")
        self.assertEqual(gemini.calls, 2)
        self.assertEqual(failed["status"], "error")
        self.assertEqual(scraper._cache, {})

    def test_cached_results_are_isolated_and_refresh_bypasses_cache(self):
        scraper = AcademicScraper(serp_scraper=FakeSerp(), gemini_searcher=FakeGemini())
        with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0", "LIVE_SEARCH_CACHE_SECONDS": "600"}):
            first = scraper.search_all_sources(" NLP   PhD ", return_status=True)
            first["results"][0]["tags"].append("polluted")
            cached = scraper.search_all_sources("nlp phd", return_status=True)
            refreshed = scraper.search_all_sources("NLP PhD", force_refresh=True, return_status=True)
        self.assertEqual(cached["status"], "cached")
        self.assertTrue(cached["cached"])
        self.assertNotIn("polluted", cached["results"][0]["tags"])
        self.assertEqual(refreshed["status"], "fresh")

    def test_malformed_provider_rows_do_not_discard_valid_results(self):
        gemini = FakeGemini()
        gemini.search_opportunities = lambda query, raise_errors=False: [
            None, "bad row", {"title": ["invalid"], "url": "https://example.edu/1"},
            {"title": "invalid URL", "url": "https://[bad"},
            {"title": "unsafe URL", "url": "javascript:alert(1)"},
            {"title": "Valid research internship", "description": ["malformed"], "url": "https://example.edu/job"},
        ]
        scraper = AcademicScraper(serp_scraper=FakeSerp(), gemini_searcher=gemini)
        with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0"}):
            results = scraper.search_all_sources("NLP", source_filter="gemini")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://example.edu/job")
        self.assertEqual(results[0]["description"], "")

    def test_total_timeout_returns_partial_results_without_waiting_for_slow_source(self):
        import concurrent.futures

        release = threading.Event()
        gemini = FakeGemini()
        def slow_search(query, raise_errors=False):
            release.wait(2)
            return []
        gemini.search_opportunities = slow_search
        scraper = AcademicScraper(serp_scraper=FakeSerp(), gemini_searcher=gemini)
        real_wait = concurrent.futures.wait
        try:
            with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "0", "LIVE_SEARCH_MAX_REQUESTS": "2"}), patch(
                "app.scrapers.concurrent.futures.wait", side_effect=lambda futures, timeout: real_wait(futures, timeout=0.05)
            ):
                result = scraper.search_all_sources("NLP PhD", return_status=True)
        finally:
            release.set()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failed_sources"], ["gemini"])
        self.assertEqual(len(result["results"]), 1)


if __name__ == "__main__":
    unittest.main()
