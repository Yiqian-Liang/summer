"""Offline checks for private application tracking and calendar export."""
import os
import sqlite3
import tempfile
import unittest
import datetime
from pathlib import Path
from unittest.mock import ANY, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth, database, mail_service

with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


def opportunity(key, **changes):
    return {
        "id": key,
        "title": f"Research opportunity {key}",
        "description": "Python research with reproducible experiments.",
        "requirements": "Python",
        "source_url": f"https://example.edu/{key}",
        "source_type": "university",
        "university": "Example University",
        "quality_score": 70,
        "hiring_signals": "[]",
        **changes,
    }


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(self.temp.name) / "applications.db")),
            patch.dict(auth.sessions, {}, clear=True),
            patch.dict(os.environ, {
                "DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1",
                "MEDIACRAWLER_DATA_PATH": "", "ENABLE_AI_DRAFTING": "0",
            }),
            patch("requests.sessions.Session.request", side_effect=AssertionError("No network in application tests")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
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

    def save(self, key):
        response = self.client.post(f"/api/save/{key}", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)

    def test_old_saved_table_migrates_with_compatible_defaults(self):
        path = str(Path(self.temp.name) / "legacy.db")
        connection = sqlite3.connect(path)
        connection.execute("""CREATE TABLE user_saved (
            id INTEGER PRIMARY KEY, user_id INTEGER, opportunity_id TEXT,
            notes TEXT, created_at TEXT
        )""")
        connection.execute("INSERT INTO user_saved (user_id, opportunity_id, notes, created_at) VALUES (1, 'old', '', '2026-01-01')")
        connection.commit()
        connection.close()
        with patch.object(database, "DB_NAME", path):
            database.init_user_tables()
            db = database.get_db()
            try:
                migrated = next(db.query("SELECT * FROM user_saved WHERE opportunity_id = 'old'"))
                self.assertEqual(migrated["progress_stage"], "preparing")
                self.assertEqual(migrated["reminder_days"], 7)
                self.assertEqual(migrated["application_method"], "email")
                for field in ("next_action_at", "contacted_at", "applied_at", "replied_at", "outcome", "outcome_at", "custom_source_url"):
                    self.assertIsNone(migrated[field])
            finally:
                db.conn.close()

    def test_private_import_is_canonical_deduplicated_and_user_isolated(self):
        payload = {
            "source_url": "HTTPS://Example.edu/project/?utm_source=feed#details",
            "title": "Outside lab opening",
            "summary": "Copied from a private feed; verify details.",
        }
        created = self.client.post("/api/saved/import", headers=self.headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        application = created.json()["application"]
        self.assertTrue(application["is_external"])
        self.assertEqual(application["custom_source_url"], "https://example.edu/project")
        self.assertEqual(self.client.get(f"/mail?opportunity={application['id']}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/mail/context/{application['id']}").status_code, 200)
        drafted = self.client.post("/api/mail/generate", headers=self.headers, json={
            "opportunity_id": application["id"], "student_name": "Student",
        })
        self.assertEqual(drafted.status_code, 200, drafted.text)

        duplicate = self.client.post("/api/saved/import", headers=self.headers, json={
            **payload, "source_url": "https://example.edu/project?utm_campaign=again", "title": "Changed title",
        })
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["application"]["id"], application["id"])
        self.assertEqual(duplicate.json()["application"]["title"], "Outside lab opening")

        db = database.get_db()
        try:
            self.assertEqual(db["opportunities"].count, 0)
            self.assertEqual(db["user_saved"].count_where("user_id = ?", [1]), 1)
        finally:
            db.conn.close()
        self.login(2)
        self.assertEqual(self.client.get("/api/saved").json()["saved"], [])
        self.assertEqual(self.client.get(f"/api/mail/context/{application['id']}").status_code, 404)
        self.assertEqual(self.client.post("/api/mail/generate", headers=self.headers, json={
            "opportunity_id": application["id"], "student_name": "Other",
        }).status_code, 404)
        second = self.client.post("/api/saved/import", headers=self.headers, json=payload)
        self.assertEqual(second.status_code, 201)

        for url in ("file:///tmp/private", "https://user:secret@example.edu/x", "https://example.edu/a b"):
            rejected = self.client.post("/api/saved/import", headers=self.headers, json={**payload, "source_url": url})
            self.assertEqual(rejected.status_code, 422, url)

    def test_progress_records_milestones_and_supports_explicit_clears(self):
        self.seed([opportunity("tracked")])
        self.save("tracked")
        contacted = self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "progress_stage": "contacted", "application_method": "email",
            "next_action_at": "2026-09-20T09:30:00", "progress_notes": "Follow up in seven days",
        })
        self.assertEqual(contacted.status_code, 200, contacted.text)
        item = contacted.json()["application"]
        self.assertTrue(item["contacted_at"])
        self.assertEqual(item["next_action_at"], "2026-09-20T09:30:00")

        self.assertEqual(self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "progress_stage": None,
        }).status_code, 422)
        self.assertEqual(self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "next_action_at": "not-a-date",
        }).status_code, 422)

        applied = self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "progress_stage": "applied", "application_method": "form",
        }).json()["application"]
        self.assertTrue(applied["applied_at"])
        self.assertEqual(applied["contacted_at"], item["contacted_at"])

        finished = self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "outcome": "accepted",
        }).json()["application"]
        self.assertEqual((finished["progress_stage"], finished["outcome"]), ("completed", "accepted"))
        self.assertTrue(finished["outcome_at"])
        self.assertIsNone(finished["next_action_at"])

        cleared = self.client.post("/api/saved/tracked/progress", headers=self.headers, json={
            "outcome": None, "next_action_at": None,
        }).json()["application"]
        self.assertIsNone(cleared["outcome"])
        self.assertIsNone(cleared["outcome_at"])

        self.seed([opportunity("later-stage")])
        self.save("later-stage")
        database.update_saved_progress(1, "later-stage", stage="applied")
        database.update_saved_progress(
            1, "later-stage", record_contact=True,
            next_action_at="2026-09-30T09:00:00+00:00",
        )
        later = database.get_saved_application(1, "later-stage")
        self.assertEqual(later["progress_stage"], "applied")
        self.assertTrue(later["contacted_at"])
        self.assertEqual(later["next_action_at"], "2026-09-30T09:00:00+00:00")
        database.update_saved_progress(1, "later-stage", next_action_at="2026-09-25T09:00:00+00:00")
        database.update_saved_progress(
            1, "later-stage", record_contact=True,
            next_action_at="2026-10-07T09:00:00+00:00",
        )
        self.assertEqual(
            database.get_saved_application(1, "later-stage")["next_action_at"],
            "2026-09-25T09:00:00+00:00",
        )
        database.update_saved_progress(1, "later-stage", outcome="accepted")
        database.update_saved_progress(
            1, "later-stage", record_contact=True,
            next_action_at="2026-10-07T09:00:00+00:00",
        )
        completed = database.get_saved_application(1, "later-stage")
        self.assertEqual(completed["progress_stage"], "completed")
        self.assertIsNone(completed["next_action_at"])

    def test_workspace_groups_custom_items_and_compare_enforces_ownership(self):
        self.seed([opportunity("a", description="Machine learning research with Python."), opportunity("b", direct_apply_link="https://example.edu/apply", has_direct_apply=1), opportunity("other")])
        self.save("a")
        self.save("b")
        imported = self.client.post("/api/saved/import", headers=self.headers, json={
            "source_url": "https://social.example/post/1", "title": "Private lead", "summary": "Needs verification",
        }).json()["application"]
        page = self.client.get("/applications")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(len(page.context["opportunities"]), 3)
        self.assertEqual(next(item for item in page.context["opportunities"] if item["id"] == "b")["application_method"], "form")
        self.assertIn(imported["id"], {item["id"] for item in page.context["today_tasks"]})
        self.assertEqual(page.context["application_groups"][0]["key"], "today")
        self.assertEqual(self.client.get("/saved").status_code, 200)

        comparison = self.client.get("/applications/compare?ids=a%2Cb")
        self.assertEqual(comparison.status_code, 200, comparison.text)
        self.assertEqual([item["id"] for item in comparison.context["applications"]], ["a", "b"])
        self.assertEqual(comparison.context["applications"][0]["research_focus"], "机器学习")
        self.assertEqual(self.client.get("/applications/compare?ids=a").status_code, 422)
        self.assertEqual(self.client.get("/applications/compare?ids=a%2Ca").status_code, 422)

        self.login(2)
        self.save("other")
        self.login(1)
        self.assertEqual(self.client.get("/applications/compare?ids=a%2Cother").status_code, 404)

    def test_calendar_exports_owned_exact_deadlines_and_escapes_text(self):
        self.seed([
            opportunity("dated", title="AI, systems; lab", deadline="2026-10-31", description="Line one\nLine two"),
            opportunity("rolling", deadline="rolling"),
            opportunity("private-date", deadline="2027-01-02"),
        ])
        self.save("dated")
        self.save("rolling")
        page = self.client.get("/applications")
        self.assertIn("/api/applications/calendar.ics?opportunity_id=dated", page.text)
        self.assertNotIn("/api/applications/calendar.ics?opportunity_id=rolling", page.text)
        calendar = self.client.get("/api/applications/calendar.ics")
        self.assertEqual(calendar.status_code, 200)
        self.assertIn("text/calendar", calendar.headers["content-type"])
        self.assertIn(b"\r\n", calendar.content)
        text = calendar.content.decode()
        self.assertIn("DTSTART;VALUE=DATE:20261031", text)
        self.assertIn("DTEND;VALUE=DATE:20261101", text)
        self.assertIn("AI\\, systems\\; lab", text)
        self.assertIn("Line one\\nLine two", text)
        self.assertNotIn("rolling", text)

        single = self.client.get("/api/applications/calendar.ics?opportunity_id=dated")
        self.assertEqual(single.status_code, 200)
        self.assertEqual(single.text.count("BEGIN:VEVENT"), 1)
        self.login(2)
        self.save("private-date")
        self.assertEqual(self.client.get("/api/applications/calendar.ics?opportunity_id=dated").status_code, 404)

    def test_calendar_preserves_source_day_folds_utf8_and_keeps_url_syntax(self):
        source_day = datetime.datetime.utcnow().date() + datetime.timedelta(days=20)
        source_url = "https://example.edu/apply?areas=ai,systems;ml&lang=zh"
        self.seed([opportunity(
            "zoned", title="跨时区中文科研机会" * 12, source_url=source_url,
            deadline=f"{source_day.isoformat()}T00:30:00+08:00",
        )])
        self.save("zoned")

        application = self.client.get("/applications").context["opportunities"][0]
        self.assertEqual(application["deadline_days_left"], (source_day - datetime.datetime.utcnow().date()).days)
        calendar = self.client.get("/api/applications/calendar.ics")
        text = calendar.content.decode("utf-8")
        unfolded = text.replace("\r\n ", "")
        self.assertIn(f"DTSTART;VALUE=DATE:{source_day.strftime('%Y%m%d')}", unfolded)
        self.assertIn(f"URL:{source_url}", unfolded)
        self.assertIn("\r\n ", text)
        for line in calendar.content.split(b"\r\n"):
            self.assertLessEqual(len(line), 75)

    def test_collection_is_admin_only_and_match_analysis_is_local_and_limited(self):
        self.seed([opportunity("match", description="Python machine learning research")])
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/analyze-match", json={
            "opportunity_id": "match", "query": "machine learning",
        }).status_code, 401)

        self.login(1)
        self.assertEqual(self.client.post("/api/analyze-match", json={
            "opportunity_id": "match", "query": "machine learning",
        }).status_code, 403)
        with patch.dict(os.environ, {"ENABLE_MATCH_AI": "0", "GEMINI_API_KEY": "must-not-be-used"}), \
                patch.object(main.research_agent, "analyze_match", return_value={"evidence_terms": ["machine learning"]}) as analyze, \
                patch.object(main, "enforce_rate_limit") as limited:
            response = self.client.post("/api/analyze-match", headers=self.headers, json={
                "opportunity_id": "match", "query": "machine learning",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(analyze.call_args.kwargs["use_llm"])
        limited.assert_called_once_with(ANY, "match_analysis", 30, 3600, 1)
        self.assertEqual(self.client.post("/api/analyze-match", headers=self.headers, json={
            "opportunity_id": "match", "unexpected": True,
        }).status_code, 422)

        self.assertEqual(self.client.post("/api/collect").status_code, 503)
        with patch.dict(os.environ, {"ADMIN_API_TOKEN": "admin-secret"}):
            self.assertEqual(self.client.post("/api/collect", headers={"X-Admin-Token": "wrong"}).status_code, 403)
            with patch("app.scheduler.DataCollectionScheduler.run_collection_job", return_value={"collected": 0}):
                allowed = self.client.post("/api/collect", headers={"X-Admin-Token": "admin-secret"})
        self.assertEqual(allowed.status_code, 200, allowed.text)
        self.assertEqual(allowed.json(), {"collected": 0})

        with patch.object(main, "enforce_rate_limit", side_effect=HTTPException(429, "limited")):
            blocked = self.client.post("/register", data={
                "email": "new@example.edu", "username": "new-user",
                "password": "safe-password-123", "password_confirm": "safe-password-123",
            })
        self.assertEqual(blocked.status_code, 429)

    def test_full_application_action_path(self):
        self.seed([opportunity("workflow", deadline="2026-12-20")])
        self.assertEqual(self.client.get("/search?q=workflow").status_code, 200)
        self.save("workflow")
        imported = self.client.post("/api/saved/import", headers=self.headers, json={
            "source_url": "https://external.example/lead", "title": "External lead",
        }).json()["application"]
        self.assertEqual(self.client.get(
            f"/applications/compare?ids=workflow%2C{imported['id']}"
        ).status_code, 200)

        resume = self.client.put("/api/resume", headers=self.headers, json={
            "text": "Education\nBSc Computer Science 2022 - 2026\nResearch Experience\n- Built Python experiments and improved accuracy by 10%.\nSkills\nPython",
            "keywords": ["Python"], "filename": "cv.txt", "enabled": True,
        })
        self.assertEqual(resume.status_code, 200, resume.text)
        self.assertEqual(self.client.get("/resume/review?opportunity_id=workflow").status_code, 200)
        generated = self.client.post("/api/mail/generate", headers=self.headers, json={
            "opportunity_id": "workflow", "student_name": "Student",
        }).json()["draft"]
        draft = self.client.post("/api/mail/drafts", headers=self.headers, json={
            "recipient": "mentor@example.edu", "subject": generated["subject"],
            "body": generated["body"], "opportunity_id": "workflow", "language": "en",
        }).json()["draft"]
        attached = self.client.post(
            f"/api/mail/drafts/{draft['id']}/attachments?filename=cv.txt",
            headers={**self.headers, "X-Draft-Version": draft["updated_at"]}, content=b"resume",
        )
        self.assertEqual(attached.status_code, 201, attached.text)
        draft = attached.json()["draft"]

        def successful_send(user_id, draft_id, confirm, updated_at):
            self.assertTrue(confirm)
            self.assertEqual(updated_at, draft["updated_at"])
            db = database.get_db()
            try:
                with db.conn:
                    db.execute("UPDATE outgoing_drafts SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                               ["2026-09-12T12:00:00+00:00", "2026-09-12T12:00:00+00:00", draft_id, user_id])
            finally:
                db.conn.close()
            return mail_service.get_draft(user_id, draft_id)

        with patch.object(mail_service, "send_draft", side_effect=successful_send):
            sent = self.client.post(f"/api/mail/drafts/{draft['id']}/send", headers=self.headers, json={
                "confirm": True, "updated_at": draft["updated_at"],
            })
        self.assertEqual(sent.status_code, 200, sent.text)
        application = sent.json()["application"]
        self.assertEqual(application["progress_stage"], "contacted")
        self.assertTrue(application["contacted_at"])
        self.assertEqual(application["mail_history"][0]["attachments"][0]["filename"], "cv.txt")

        due = self.client.post("/api/saved/workflow/progress", headers=self.headers, json={
            "progress_stage": "contacted", "next_action_at": "2026-01-01T09:00:00",
            "progress_notes": "Send follow-up",
        })
        self.assertEqual(due.status_code, 200, due.text)
        self.assertIn("workflow", {item["id"] for item in self.client.get("/").context["today_tasks"]})
        for stage, timestamp in (("applied", "applied_at"), ("replied", "replied_at")):
            application = self.client.post("/api/saved/workflow/progress", headers=self.headers, json={
                "progress_stage": stage,
            }).json()["application"]
            self.assertTrue(application[timestamp])
        completed = self.client.post("/api/saved/workflow/progress", headers=self.headers, json={
            "outcome": "accepted",
        }).json()["application"]
        self.assertEqual((completed["progress_stage"], completed["outcome"]), ("completed", "accepted"))


if __name__ == "__main__":
    unittest.main()
