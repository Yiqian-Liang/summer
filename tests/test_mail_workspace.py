"""Main-app mail workspace checks with temporary storage and all network blocked."""
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import auth, database
from app.rag import RAGWorkbench
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


class MailWorkspaceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(temporary.name) / "workspace.db")),
            patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1",
                                   "MEDIACRAWLER_DATA_PATH": "", "ENABLE_AI_DRAFTING": "0",
                                   "GEMINI_API_KEY": "", "MAIL_ENCRYPTION_KEY": "",
                                   "GOOGLE_CLIENT_ID": "", "MICROSOFT_CLIENT_ID": ""}),
            patch.dict(auth.sessions, {}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        workbench = patch.object(main, "rag_workbench", RAGWorkbench())
        workbench.start()
        self.addCleanup(workbench.stop)
        blocked = patch("requests.sessions.Session.request", side_effect=AssertionError("Mail workspace tests must stay offline"))
        self.network = blocked.start()
        self.addCleanup(blocked.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.opportunity = {"id": "mail-project", "title": "Summer robot learning internship",
                            "source_url": "https://lab.example.edu/projects/robotics", "source_type": "university",
                            "professor_name": "Dr. Chen", "description": "Robot learning with visual observations. Contact lab@example.edu.",
                            "has_direct_apply": 1, "direct_apply_link": "https://lab.example.edu/apply"}
        db = database.get_db()
        db["opportunities"].insert(self.opportunity)
        db.conn.close()
        self.token = auth.create_session(1, {"id": 1, "username": "Alice", "email": "alice@example.edu"})
        self.client.cookies.set("session_token", self.token)
        self.headers = {"X-CSRF-Token": auth.get_session(self.token)["csrf_token"]}
        self.payload = {"opportunity_id": self.opportunity["id"], "student_name": "Alice", "school": "Example University", "interest": "robotics"}

    def generate(self, path="/api/mail/generate", **changes):
        return self.client.post(path, json={**self.payload, **changes}, headers=self.headers)

    def test_anonymous_and_csrf_are_enforced_by_the_actual_main_routes(self):
        for path in ("/api/mail/generate", "/api/generate-rag-draft"):
            self.assertEqual(self.client.post(path, json=self.payload).status_code, 403)
        self.client.cookies.clear()
        for path in ("/api/mail/capabilities", "/api/mail/context/mail-project", "/api/mail/drafts"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        for path in ("/api/mail/generate", "/api/generate-rag-draft"):
            self.assertEqual(self.generate(path).status_code, 401)
        response = self.client.get("/mail?opportunity=mail-project", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/login?next=%2Fmail%3Fopportunity%3Dmail-project")
        self.network.assert_not_called()

    def test_default_generation_uses_real_project_context_even_when_ai_is_configured(self):
        with patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1", "GEMINI_API_KEY": "never-use-this-key"}), patch.object(main, "rag_workbench", RAGWorkbench()):
            for path in ("/api/mail/generate", "/api/generate-rag-draft"):
                response = self.generate(path, resume_text="Built a ROS navigation demo.", focus_area="visual navigation",
                                         professor_profile={"name": "Fake Mentor", "recent_paper": "Invented Paper"})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
                self.assertEqual(result["draft"]["mode"], "template")
                for fact in ("Alice", "Dr. Chen", "Example University", "ROS navigation", "visual navigation", self.opportunity["title"], "Robot learning with visual observations"):
                    self.assertIn(fact, result["draft"]["body"])
                self.assertNotIn("Invented Paper", result["draft"]["body"])
                self.assertNotIn("Fake Mentor", result["draft"]["body"])
                self.assertEqual(result["draft"]["citations"], [self.opportunity["source_url"]])
                self.assertEqual(result["recipient"], "lab@example.edu")
        self.network.assert_not_called()

    def test_context_and_missing_or_invalid_inputs(self):
        context = self.client.get("/api/mail/context/mail-project").json()["opportunity"]
        self.assertEqual(context["title"], self.opportunity["title"])
        self.assertEqual(context["professor_email"], "lab@example.edu")
        for path in ("/api/mail/context/missing", "/mail?opportunity=missing"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.generate(opportunity_id="missing").status_code, 404)
        for values in ({"opportunity_id": ""}, {"opportunity_id": "x" * 101}, {"student_name": "x" * 101},
                       {"school": "x" * 201}, {"interest": "x" * 501}, {"resume_text": "x" * 6001},
                       {"focus_area": "x" * 501}, {"tone": "unknown"}, {"language": "unknown"}, {"use_ai": []}):
            with self.subTest(values={key: len(value) if isinstance(value, str) else value for key, value in values.items()}):
                self.assertEqual(self.generate(**values).status_code, 422)
        self.assertEqual(self.client.get("/mail?draft=0").status_code, 422)
        self.network.assert_not_called()

    def test_bootstrap_escapes_project_text_and_never_exposes_session_token(self):
        unsafe = '</script><script>alert("unsafe-project")</script>'
        db = database.get_db()
        db["opportunities"].update("mail-project", {"title": unsafe})
        db.conn.close()
        response = self.client.get("/mail?opportunity=mail-project")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertNotIn(unsafe, response.text)
        self.assertNotIn(self.token, response.text)
        bootstrap = re.search(r'<script id="mailBootstrap" type="application/json">(.*?)</script>', response.text, re.S)
        self.assertIsNotNone(bootstrap)
        self.assertIn("\\u003c", bootstrap.group(1))
        decoded = json.loads(bootstrap.group(1))
        self.assertEqual(decoded["opportunity"]["title"], unsafe)
        self.assertEqual(decoded["csrf"], self.headers["X-CSRF-Token"])
        self.assertFalse(decoded["capabilities"]["ai_available"])
        self.assertIn('id="applyReminder"', response.text)
        self.network.assert_not_called()

    def test_unconfigured_ai_and_mailboxes_still_allow_generation_and_private_save_load(self):
        capabilities = self.client.get("/api/mail/capabilities")
        self.assertFalse(capabilities.json()["ai_available"])
        self.assertEqual(capabilities.headers["cache-control"], "no-store")
        accounts = self.client.get("/api/mail/accounts").json()
        self.assertFalse(accounts["configured"])
        self.assertEqual(accounts["accounts"], [])
        generated = self.generate(use_ai=True, language="zh").json()["draft"]
        self.assertEqual((generated["mode"], generated["requested_mode"]), ("template", "ai"))
        self.assertIn("warning", generated)
        payload = {"subject": generated["subject"], "body": generated["body"], "recipient": "lab@example.edu",
                   "opportunity_id": self.opportunity["id"], "language": "zh", "account_id": None}
        saved = self.client.post("/api/mail/drafts", json=payload, headers=self.headers)
        self.assertEqual(saved.status_code, 201, saved.text)
        draft = saved.json()["draft"]
        loaded = self.client.get(f"/api/mail/drafts/{draft['id']}")
        self.assertEqual(loaded.json()["draft"]["body"], generated["body"])
        self.assertIsNone(loaded.json()["draft"]["account_id"])
        self.assertEqual(self.client.get(f"/mail?draft={draft['id']}").context["draft_id"], draft["id"])
        other = auth.create_session(2, {"id": 2, "username": "Other"})
        self.client.cookies.set("session_token", other)
        self.assertEqual(self.client.get(f"/api/mail/drafts/{draft['id']}").status_code, 404)
        self.assertEqual(self.client.get("/api/mail/drafts").json()["drafts"], [])
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
