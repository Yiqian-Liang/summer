"""Offline checks for the real HTML/API search and pagination flow."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app.database as database
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main
from app.scheduler import DataCollectionScheduler, OpportunityProcessor


class SearchRoutesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_patch = patch.object(database, "DB_NAME", str(Path(self.temp.name) / "search.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.env = patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1", "MEDIACRAWLER_DATA_PATH": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def seed(self, count, title="AI PhD opening", quality=70, start=0):
        rows = [{
            "id": f"result-{i}", "title": f"{title} {i}",
            "source_url": f"https://example.edu/openings/{i}",
            "source_type": "university", "description": "Applications open for research.",
            "university": f"University {i}", "quality_score": quality,
            "updated_at": "2026-09-08", "hiring_signals": "[]",
        } for i in range(start, start + count)]
        db = database.get_db()
        db["opportunities"].insert_all(rows)
        db.conn.close()

    def test_complete_pagination_and_home_second_page(self):
        self.seed(215)
        api = self.client.get("/api/search", params={"q": "AI", "page": 11}).json()
        self.assertEqual((api["total"], api["count"], api["total_pages"]), (215, 15, 11))
        page = self.client.get("/search", params={"q": "AI", "page": 11})
        self.assertEqual(page.status_code, 200)
        self.assertEqual([r["id"] for r in page.context["results"]], [r["id"] for r in api["results"]])
        self.assertEqual(len(self.client.get("/?page=2").context["opportunities"]), 20)
        with patch.object(main, "get_current_user_from_request", return_value={"id": 1}), patch.object(main, "get_user_profile", return_value={"majors": "[]"}), patch.object(main, "get_recommendation_state", return_value={"use_behavior": True, "behaviors": [], "saved": [], "hidden": []}):
            home = self.client.get("/?page=11")
            self.assertEqual(home.context["total_count"], 215)
            self.assertEqual(len(home.context["opportunities"]), 15)

    def test_reranking_happens_before_api_limit_and_model_failure_falls_back(self):
        self.seed(3)
        with patch.object(main.matcher, "rank_candidates", side_effect=lambda q, rows: list(reversed(rows))):
            api = self.client.get("/api/search", params={"q": "AI", "limit": 1}).json()
            page = self.client.get("/search?q=AI")
            self.assertEqual(api["results"][0]["id"], page.context["results"][0]["id"])
            self.assertNotIn("text_for_matching", api["results"][0])
        with patch.object(main.matcher, "rank_candidates", side_effect=RuntimeError("offline model")):
            self.assertEqual(self.client.get("/api/search?q=AI").json()["count"], 3)

    def test_duplicate_sources_keep_stable_platform_counts(self):
        self.seed(1)
        db = database.get_db()
        row = dict(db["opportunities"].get("result-0"))
        db["opportunities"].insert({**row, "id": "linkedin-copy", "source_type": "linkedin", "source_url": "https://linkedin.com/jobs/view/123"})
        db.conn.close()
        all_results = self.client.get("/api/search?q=AI").json()
        filtered = self.client.get("/api/search?q=AI&source=linkedin").json()
        self.assertEqual((all_results["total"], filtered["total"]), (1, 1))
        self.assertEqual(all_results["source_counts"], filtered["source_counts"])
        self.assertEqual(set(all_results["results"][0]["matched_sources"]), {"university", "linkedin"})

    def test_live_ingestion_failure_state_and_first_page_only(self):
        self.seed(1)
        live = {"results": [{"title": "AI doctoral opening", "url": "https://lab.edu/phd", "description": "Funded PhD applications are open.", "requirements": None}], "status": "fresh", "cached": False, "providers": ["Test"], "failed_sources": []}
        with patch.object(main.scraper_engine, "has_live_search_credentials", return_value=True), patch.object(main.scraper_engine, "live_search_providers", return_value=["Test"]), patch.object(main.scraper_engine, "search_all_sources", return_value=live) as collect:
            api = self.client.get("/api/search?q=AI&refresh=true").json()
            self.assertEqual(api["count"], 2)
            self.assertEqual(api["live_search"]["synced_count"], 1)
            self.assertTrue(collect.call_args.kwargs["return_status"])
            self.client.get("/api/search?q=AI&page=2")
            self.assertEqual(collect.call_count, 1)
        with patch.object(main.scraper_engine, "has_live_search_credentials", return_value=True), patch.object(main.scraper_engine, "search_all_sources", side_effect=RuntimeError("offline")):
            page = self.client.get("/search?q=AI")
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.context["live_search"]["status"], "error")
            self.assertIn("在线搜索暂不可用", page.text)
            self.assertEqual(len(page.context["results"]), 2)

    def test_boundaries_and_filtered_api_total(self):
        self.seed(2)
        for params in ({"q": " "}, {"q": "a" * 301}, {"q": "AI", "limit": -1}, {"q": "AI", "page": 0}, {"q": "AI", "source": "bogus"}):
            self.assertEqual(self.client.get("/api/search", params=params).status_code, 422)
        self.assertEqual(self.client.get("/api/opportunities?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/opportunities?source_type=wechat").json()["total"], 0)
        self.assertEqual(self.client.get("/search?q=%20%20").context["query"], "")
        db = database.get_db()
        db.execute("UPDATE opportunities SET quality_score = ? WHERE id = ?", [float("inf"), "result-0"])
        db.conn.commit()
        db.conn.close()
        response = self.client.get("/api/search?q=AI&live=false")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(0 <= row["quality_score"] <= 100 for row in response.json()["results"]))

    def test_low_quality_relevant_record_is_not_lost_and_metadata_survives(self):
        self.seed(350, title="Email research PhD opening", quality=100)
        self.seed(1, quality=10, start=350)
        self.assertEqual([r["id"] for r in database.search_opportunities_fts("AI", limit=1)], ["result-350"])
        processor = OpportunityProcessor()
        item = processor.normalize_item({"title": "AI PhD", "url": "https://lab.edu/post", "deadline": "2030-01-01", "requirements": "Undergraduates welcome", "published_at": "2026-09-01"}, "university")
        database.upsert_opportunity(item)
        updated = processor.normalize_item({"title": "AI PhD", "url": "https://lab.edu/post", "requirements": None}, "university")
        database.upsert_opportunity(updated)
        row = database.get_opportunity_by_id(database.generate_opportunity_id("https://lab.edu/post"))
        self.assertEqual((row["deadline"], row["requirements"]), ("2030-01-01", "Undergraduates welcome"))
        evidence = {"title": "Email systems", "source_query": "funded hiring summer research", "url": "https://lab.edu/hiring"}
        self.assertEqual(processor.extract_hiring_signals(evidence), [])
        self.assertNotIn("Funded Position", processor.extract_hiring_signals({"title": "Unpaid research internship"}))
        import datetime
        year = datetime.datetime.now().year
        self.assertEqual(DataCollectionScheduler().queries[:2], [f"summer research {year}", f"summer research {year + 1}"])


if __name__ == "__main__":
    unittest.main()
