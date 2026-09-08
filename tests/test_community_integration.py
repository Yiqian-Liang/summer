"""Offline checks across community publishing, opportunity search, and email drafts."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, database
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


class CommunityIntegrationTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for guard in (
            patch.object(database, "DB_NAME", str(Path(temporary.name) / "community.db")),
            patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1",
                                    "MEDIACRAWLER_DATA_PATH": "", "ENABLE_AI_DRAFTING": "0"}),
            patch.object(main.scraper_engine, "search_all_sources", side_effect=AssertionError("Unexpected live search")),
            patch.object(main.rag_workbench, "_request", side_effect=AssertionError("Unexpected AI request")),
        ):
            guard.start()
            self.addCleanup(guard.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        user_id = database.create_user("owner@example.edu", "owner", "test-only")
        token = auth.create_session(user_id, {"id": user_id, "username": "owner"})
        self.addCleanup(auth.destroy_session, token)
        self.client.cookies.set("session_token", token)
        self.client.headers["X-CSRF-Token"] = auth.get_session(token)["csrf_token"]

    def create_post(self, kind="recruitment", status="published", **fields):
        response = self.client.post("/api/posts", json={
            "title": f"Robotics summer research {kind}", "post_kind": kind,
            "university": "Example University", "professor_name": "Alex Chen",
            "description": "Summer research on robotics with contact through this public announcement.",
            "contact_email": "lab@example.edu", **fields,
        })
        self.assertEqual(response.status_code, 201, response.text)
        post = response.json()["post"]
        if status != "draft":
            changed = self.client.post(f"/api/posts/{post['id']}/status", json={"status": status})
            self.assertEqual(changed.status_code, 200, changed.text)
        return post["id"]

    def test_only_published_recruitment_enters_all_opportunity_surfaces(self):
        recruitment = self.create_post()
        experience = self.create_post("experience")
        discussion = self.create_post("discussion")
        draft = self.create_post(status="draft")
        closed = self.create_post(status="closed")
        db = database.get_db()
        db["opportunities"].insert({
            "id": "collected", "title": "Robotics doctoral opening at another institution",
            "source_url": "https://another.edu/openings/robotics", "source_type": "university",
            "description": "PhD applications open for robotics.", "university": "Another University",
            "quality_score": 80, "hiring_signals": "[]", "updated_at": "2026-09-01",
        })
        db.conn.close()
        allowed = {recruitment, "collected"}
        for rows in (database.get_opportunities(), database.get_recent_opportunities(), database.search_opportunities_fts("robotics")):
            self.assertEqual({row["id"] for row in rows}, allowed)
        self.assertEqual(database.get_opportunity_count(), 2)
        api = self.client.get("/api/search?q=robotics&live=false").json()
        self.assertEqual({row["id"] for row in api["results"]}, allowed)
        self.assertEqual(api["total"], 2)
        filtered = self.client.get("/api/search?q=robotics&source=community&live=false")
        self.assertEqual(filtered.status_code, 200, filtered.text)
        self.assertEqual([row["id"] for row in filtered.json()["results"]], [recruitment])
        home = self.client.get("/")
        self.assertEqual({row["id"] for row in home.context["opportunities"]}, allowed)
        html = self.client.get("/search?q=robotics&source=community&live=false")
        self.assertEqual([row["id"] for row in html.context["results"]], [recruitment])
        feed = self.client.get("/api/opportunities?source_type=community").json()
        self.assertEqual((feed["total"], [row["id"] for row in feed["opportunities"]]), (1, [recruitment]))
        self.assertEqual(self.client.get("/api/stats").json()["by_source"]["community"], 1)
        self.assertEqual({row["id"] for row in self.client.get("/posts").context["posts"]}, {recruitment, experience, discussion})
        for hidden in (experience, discussion, draft, closed):
            self.assertIsNone(database.get_opportunity_by_id(hidden))
            self.assertEqual(self.client.get(f"/opportunity/{hidden}", follow_redirects=False).status_code, 404)

    def test_community_detail_redirect_and_real_contact_prefill_without_ai(self):
        post_id = self.create_post(description="Summer research on robotics. An older generic address is unrelated@example.edu.")
        redirect = self.client.get(f"/opportunity/{post_id}", follow_redirects=False)
        self.assertEqual(redirect.status_code, 302)
        self.assertEqual(redirect.headers["location"], f"/posts/{post_id}")
        public = self.client.get(redirect.headers["location"])
        self.assertIn(f"/mail?opportunity={post_id}", public.text)
        self.assertIn("发布者未核验", public.text)
        page = self.client.get(f"/mail?opportunity={post_id}")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(page.context["opportunity"]["professor_email"], "lab@example.edu")
        self.assertIn('value="lab@example.edu"', page.text)
        self.assertEqual(page.headers["cache-control"], "no-store")
        context = self.client.get(f"/api/mail/context/{post_id}")
        self.assertEqual(context.status_code, 200, context.text)
        self.assertEqual(context.json()["opportunity"]["professor_email"], "lab@example.edu")
        generated = self.client.post("/api/mail/generate", json={
            "opportunity_id": post_id, "student_name": "Taylor", "language": "en", "use_ai": False,
        })
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(generated.json()["recipient"], "lab@example.edu")
        self.assertIn("Taylor", generated.json()["draft"]["body"])
        self.assertIn("Robotics summer research recruitment", generated.json()["draft"]["body"])
        self.client.cookies.clear()
        self.assertEqual(self.client.get(f"/api/mail/context/{post_id}").status_code, 401)
        self.assertEqual(self.client.get(f"/mail?opportunity={post_id}", follow_redirects=False).status_code, 302)

    def test_private_and_non_recruitment_content_cannot_be_used_for_mail(self):
        for kind, status in (("recruitment", "draft"), ("recruitment", "closed"), ("experience", "published"), ("discussion", "published")):
            post_id = self.create_post(kind, status)
            self.assertEqual(self.client.get(f"/mail?opportunity={post_id}").status_code, 404)
            self.assertEqual(self.client.get(f"/api/mail/context/{post_id}").status_code, 404)
            self.assertEqual(self.client.post("/api/mail/generate", json={"opportunity_id": post_id}).status_code, 404)

    def test_explicit_recruitment_type_is_sufficient_without_hiring_keywords(self):
        post_id = self.create_post(title="Robotics at our laboratory", description="We work on robot perception and movement in a small interdisciplinary team.")
        response = self.client.get("/api/search?q=robotics&source=community&live=false")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.json()["results"]], [post_id])


if __name__ == "__main__":
    unittest.main()
