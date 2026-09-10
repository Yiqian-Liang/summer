"""Local recommendation and feedback checks using disposable, offline data."""
import copy
import datetime as dt
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import auth, database
from app.recommender import Recommender, _posting_topics
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


NOW = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)


def opportunity(key, **changes):
    return {"id": key, "title": f"Python research internship {key}", "description": "Research involving Python data analysis.",
        "source_url": f"https://example.edu/{key}", "source_type": "university", "university": f"Lab {key}",
        "quality_score": 60, "hiring_signals": "[]", **changes}


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(self.temp.name) / "recommendations.db")),
            patch.dict(auth.sessions, {}, clear=True),
            patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1", "MEDIACRAWLER_DATA_PATH": "", "ENABLE_AI_DRAFTING": "0"}),
            patch("requests.sessions.Session.request", side_effect=AssertionError("No network in recommendations")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.rank = Recommender().rank_opportunities
        self.login(1)

    def login(self, user_id):
        self.client.cookies.clear()
        token = auth.create_session(user_id, {"id": user_id, "username": f"User {user_id}"})
        self.client.cookies.set("session_token", token)
        self.headers = {"X-CSRF-Token": auth.get_session(token)["csrf_token"]}

    def seed(self, rows):
        db = database.get_db()
        try:
            db["opportunities"].insert_all(rows)
        finally:
            db.conn.close()

    def feedback(self, **payload):
        result = self.client.post("/api/recommendations/feedback", json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_resume_rare_terms_and_demographics_do_not_change_ranking(self):
        rows = [opportunity("common", quality_score=100), opportunity("rare", quality_score=30,
            description="Research involving Python and gravitational lensing.")]
        original = copy.deepcopy(rows)
        resume = {"text": "Built Python tools for gravitational lensing experiments.", "keywords": ["Python", "gravitational lensing"]}
        ranked = self.rank(rows, resume=resume, now=NOW)
        self.assertEqual(ranked[0]["id"], "rare")
        self.assertGreater(ranked[0]["recommendation_components"]["resume"], ranked[1]["recommendation_components"]["resume"])
        self.assertEqual(rows, original)
        self.assertEqual(ranked, self.rank(rows, resume=resume, profile={"school_tier": "985", "grade": "senior", "education_level": "phd"}, now=NOW))

    def test_saved_strength_click_decay_duplicates_and_removed_saves(self):
        rows = [opportunity("seed"), opportunity("related"), opportunity("different", title="Biology research internship", description="Biology research.")]
        click = {"action_type": "click", "target_id": "seed", "created_at": NOW.isoformat()}
        component = lambda ranked: next(row["recommendation_components"]["saved_and_browsed"] for row in ranked if row["id"] == "related")
        once = self.rank(rows, behaviors=[click], now=NOW)
        self.assertEqual(once, self.rank(rows, behaviors=[click] * 100, now=NOW))
        self.assertGreater(component(self.rank(rows, saved=[{"opportunity_id": "seed"}], now=NOW)), component(once))
        old = {**click, "created_at": (NOW - dt.timedelta(days=30)).isoformat()}
        self.assertLess(component(self.rank(rows, behaviors=[old], now=NOW)), component(once))
        old["created_at"] = (NOW - dt.timedelta(days=91)).isoformat()
        self.assertEqual(component(self.rank(rows, behaviors=[old], now=NOW)), 0)
        self.assertEqual(component(self.rank(rows, behaviors=[{**click, "action_type": "save"}], saved=[], now=NOW)), 0)

    def test_availability_hidden_duplicates_diversity_and_full_tail(self):
        rows = [opportunity(str(i), university="Same Lab") for i in range(8)]
        rows.append(opportunity("alternative", university="Different Lab", source_type="linkedin"))
        invalid = [opportunity("expired", deadline="2000-01-01"), opportunity("draft", provider="user_post", post_status="draft", post_kind="recruitment"),
            opportunity("discussion", provider="user_post", post_status="published", post_kind="discussion"),
            opportunity("profile", source_type="linkedin", source_url="https://linkedin.com/in/professor")]
        duplicate = {**rows[0], "id": "copy", "source_type": "wechat"}
        ranked = self.rank(rows + invalid + [duplicate], now=NOW)
        self.assertEqual(len(ranked), 9)
        self.assertIn("Different Lab", {row["university"] for row in ranked[:4]})
        hidden = self.rank(rows + invalid + [duplicate], hidden=["0"], now=NOW)
        self.assertEqual(len(hidden), 8)
        self.assertFalse({"0", "copy"} & {row["id"] for row in hidden})
        many = [opportunity(str(i)) for i in range(221)]
        self.assertEqual(len(self.rank(many, now=NOW)), 221)

    def test_private_feedback_learning_off_clear_and_restore(self):
        self.seed([opportunity("a"), opportunity("b")])
        self.client.get("/opportunity/a")
        self.client.get("/opportunity/a")
        self.assertEqual(len(database.get_recommendation_state(1)["behaviors"]), 1)
        self.feedback(action="hide", opportunity_id="a")
        self.assertEqual([row["id"] for row in self.client.get("/").context["opportunities"]], ["b"])
        self.assertIn("a", {row["id"] for row in self.client.get("/api/search?q=Python&live=false").json()["results"]})
        self.login(2)
        self.assertEqual(len(self.client.get("/").context["opportunities"]), 2)
        self.assertEqual(database.get_recommendation_state(2)["behaviors"], [])
        self.login(1)
        self.feedback(action="set_learning", enabled=False)
        self.client.get("/opportunity/b")
        self.assertEqual(database.get_recommendation_state(1)["behaviors"], [])
        self.feedback(action="set_learning", enabled=True)
        self.assertEqual([item["target_id"] for item in database.get_recommendation_state(1)["behaviors"]], ["a"])
        self.feedback(action="clear_history")
        self.assertEqual(database.get_recommendation_state(1)["behaviors"], [])
        self.feedback(action="restore")
        self.assertEqual(len(self.client.get("/").context["opportunities"]), 2)

    def test_bookmark_sources_feedback_validation_and_csrf(self):
        self.seed([opportunity("a"), opportunity("hidden", provider="user_post", post_kind="recruitment", post_status="draft")])
        self.assertEqual(self.client.post("/api/save/a").status_code, 403)
        for _ in range(2):
            self.assertTrue(self.client.post("/api/save/a", headers=self.headers).json()["success"])
        self.assertEqual(len(database.get_recommendation_state(1)["saved"]), 1)
        self.assertEqual(self.client.post("/api/save/hidden", headers=self.headers).status_code, 404)
        self.assertEqual(self.client.post("/api/recommendations/feedback", json={"action": "hide", "opportunity_id": "a"}).status_code, 403)
        for payload in ({"action": "unknown"}, {"action": "hide"}, {"action": "set_learning", "enabled": "false"}, {"action": "clear_history", "user_id": 2}):
            self.assertEqual(self.client.post("/api/recommendations/feedback", json=payload, headers=self.headers).status_code, 422)
        self.assertEqual(self.client.post("/api/track", headers=self.headers, data={"action_type": "apply", "target_type": "opportunity", "target_id": "a"}).status_code, 422)
        self.client.delete("/api/save/a", headers=self.headers)
        self.assertEqual(database.get_recommendation_state(1)["saved"], [])
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/recommendations/feedback", json={"action": "restore"}).status_code, 401)

    def test_saved_progress_api_validates_and_updates_fields(self):
        self.seed([opportunity("progress-target")])
        self.assertEqual(self.client.post("/api/save/progress-target", headers=self.headers).status_code, 200)

        base = self.client.get("/api/saved", headers=self.headers).json()["saved"]
        self.assertEqual(len(base), 1)
        self.assertEqual(base[0]["progress_stage"], "preparing")
        self.assertEqual(base[0]["reminder_days"], 7)
        self.assertEqual(base[0]["progress_notes"], "")

        failed = self.client.post("/api/saved/progress-target/progress", headers=self.headers, json={})
        self.assertEqual(failed.status_code, 422)
        updated = self.client.post("/api/saved/progress-target/progress", headers=self.headers, json={
            "progress_stage": "contacted",
            "reminder_days": 10,
            "progress_notes": "已发出第一封邮件",
        }).json()
        self.assertTrue(updated["success"])

        saved = self.client.get("/api/saved", headers=self.headers).json()["saved"][0]
        self.assertEqual(saved["progress_stage"], "contacted")
        self.assertEqual(saved["reminder_days"], 10)
        self.assertEqual(saved["progress_notes"], "已发出第一封邮件")

        self.assertEqual(self.client.post("/api/saved/progress-target/progress", headers=self.headers, json={"reminder_days": 31}).status_code, 422)
        self.assertEqual(self.client.post("/api/saved/unknown/progress", headers=self.headers, json={"progress_stage": "applied"}).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/saved/progress-target/progress", json={"progress_stage": "applied"}).status_code, 401)

    def test_home_paginates_after_ranking_and_recent_mode_keeps_no_private_evidence(self):
        rows = [opportunity(str(i), published_at=f"2026-09-{1 + i % 8:02d}") for i in range(45)]
        self.seed(rows)
        one, two = self.client.get("/?feed=latest"), self.client.get("/?feed=latest&page=2")
        self.assertEqual((one.context["total_count"], len(two.context["opportunities"])), (45, 20))
        self.assertFalse({row["id"] for row in one.context["opportunities"]} & {row["id"] for row in two.context["opportunities"]})
        self.assertIn('href="?feed=latest&page=3"', two.text)
        self.assertNotIn("recommendation_components", str(two.context["opportunities"]))
        self.assertEqual(two.headers["cache-control"], "no-store")
        for path in ("/", "/opportunity/0", "/search?q=Python", "/saved"):
            self.assertIn(self.headers["X-CSRF-Token"], self.client.get(path).text)

    def test_history_bounds_and_cache_uses_only_public_content(self):
        for i in range(205):
            database.record_user_behavior(1, "search", f"topic {i}", "search")
        self.assertEqual(len(database.get_user_behaviors(1, limit=1000)), 200)
        self.assertEqual(len(database.get_recommendation_state(1)["behaviors"]), 100)
        _posting_topics.cache_clear()
        rows = [opportunity("public")]
        first = {"text": "PRIVATE_STUDENT_A uses Python for research projects.", "keywords": ["Python"]}
        second = {"text": "PRIVATE_STUDENT_B uses Python in other experiments.", "keywords": ["Python"]}
        self.rank(rows, resume=first, now=NOW)
        self.rank(rows, resume=second, now=NOW)
        self.assertEqual((_posting_topics.cache_info().misses, _posting_topics.cache_info().hits), (1, 1))
        changed = self.rank([opportunity("public", title="Biology research internship", description="Biology experiments.")], resume=second, now=NOW)
        self.assertEqual(changed[0]["recommendation_components"]["resume"], 0)
        self.assertEqual(_posting_topics.cache_info().misses, 2)


if __name__ == "__main__":
    unittest.main()
