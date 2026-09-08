"""Resume parsing, private confirmation and end-to-end matching without remote AI."""
import io
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from app import auth, database, resumes
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


class ResumeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(temp.name) / "resume.db")),
            patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1", "MEDIACRAWLER_DATA_PATH": "", "ENABLE_AI_DRAFTING": "0"}),
            patch.dict(auth.sessions, {}, clear=True),
            patch("requests.sessions.Session.request", side_effect=AssertionError("Resume data must not reach any external service")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.login(1)
        self.text = "CV_PRIVATE_STUDENT\nBuilt robot navigation with Python and ROS.\nDeveloped PyTorch perception experiments.\nNo MATLAB experience."
        self.payload = {"text": self.text, "keywords": ["Python", "Robotics", "PyTorch", "ROS"], "filename": "cv.txt", "enabled": True}

    def login(self, user_id):
        self.client.cookies.clear()
        token = auth.create_session(user_id, {"id": user_id, "username": "Student"})
        self.client.cookies.set("session_token", token)
        self.headers = {"X-CSRF-Token": auth.get_session(token)["csrf_token"]}

    def save(self, **updates):
        response = self.client.put("/api/resume", json={**self.payload, **updates}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["resume"]

    def seed(self, count=1):
        db = database.get_db()
        try:
            db["opportunities"].insert_all([{"id": f"robot-{i}", "title": f"Robotics research internship {i}",
                "description": "Research internship involving Python, PyTorch, and ROS robot navigation.",
                "requirements": "Python and robotics experience", "source_url": f"https://example.edu/lab/{i}",
                "university": f"Lab {i}", "source_type": "university", "quality_score": 60,
                "updated_at": "2026-09-08", "hiring_signals": "[]"} for i in range(count)])
        finally:
            db.conn.close()

    def test_parse_requires_confirmation_and_preserves_active_resume(self):
        first = self.save()
        response = self.client.post("/api/resume/parse?filename=replacement.txt", content=b"Built Python data analysis experiments for astronomy.", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("Python", response.json()["keywords"])
        self.assertIn("Astronomy", response.json()["keywords"])
        self.assertEqual(self.client.get("/api/resume").json()["resume"], first)
        self.assertNotIn("MATLAB", resumes.suggestions(self.text))
        self.assertNotIn("Computer Vision", resumes.suggestions("CV\nPython research experience and software experiments."))
        bad = self.client.put("/api/resume", json={**self.payload, "updated_at": first["updated_at"], "keywords": ["MATLAB"]}, headers=self.headers)
        self.assertEqual(bad.status_code, 422)

    def test_owner_csrf_versions_disable_and_delete(self):
        self.assertEqual(self.client.put("/api/resume", json=self.payload).status_code, 403)
        self.assertEqual(self.client.post("/api/resume/parse?filename=x.txt", content=self.text).status_code, 403)
        first = self.save()
        self.assertEqual(self.client.put("/api/resume", json=self.payload, headers=self.headers).status_code, 409)
        self.login(2)
        self.assertIsNone(self.client.get("/api/resume").json()["resume"])
        self.client.delete("/api/resume", headers=self.headers)
        self.assertEqual(resumes.get_resume(1)["text"], first["text"])
        self.login(1)
        disabled = self.save(updated_at=first["updated_at"], enabled=False)
        self.assertIsNone(resumes.get_resume(1, active_only=True))
        self.assertEqual(self.client.get("/api/search?sort=resume").status_code, 422)
        self.save(updated_at=disabled["updated_at"])
        self.assertEqual(self.client.delete("/api/resume", headers=self.headers).status_code, 200)
        self.assertIsNone(resumes.get_resume(1))
        self.client.cookies.clear()
        for path in ("/api/resume", "/api/search?sort=resume"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.get("/resume", follow_redirects=False).status_code, 302)

    def test_local_matches_filter_expired_hidden_unrelated_and_keep_full_pagination(self):
        self.seed(25)
        self.save()
        db = database.get_db()
        original = dict(db["opportunities"].get("robot-0"))
        db["opportunities"].insert_all([
            {**original, "id": "expired", "title": "Expired robot internship", "source_url": "https://example.edu/expired", "deadline": "2001-01-01"},
            {**original, "id": "draft", "provider": "user_post", "post_status": "draft", "post_kind": "recruitment", "source_type": "community"},
            {**original, "id": "discussion", "provider": "user_post", "post_status": "published", "post_kind": "discussion", "source_type": "community"},
            {**original, "id": "unrelated", "title": "History internship", "description": "Historical archive research", "requirements": "", "source_url": "https://example.edu/history"},
            {**original, "id": "copy", "source_type": "linkedin", "source_url": "https://linkedin.com/jobs/view/test"},
        ])
        db.conn.close()
        with patch.object(main.scraper_engine, "has_live_search_credentials", side_effect=AssertionError("Resume-only browsing must not start web search")), patch.object(main.matcher, "rank_candidates", side_effect=AssertionError("Resume-only browsing needs no model")) as model:
            api = self.client.get("/api/search?sort=resume&page=2").json()
            page = self.client.get("/search?sort=resume&page=2")
            model.assert_not_called()
        self.assertEqual((api["total"], api["count"]), (25, 5))
        self.assertEqual(page.status_code, 200, page.text[:200])
        self.assertEqual([row["id"] for row in api["results"]], [row["id"] for row in page.context["results"]])
        self.assertIn("sort=resume&amp;page=1", page.text.replace("&page", "&amp;page"))
        self.assertEqual(api["results"][0]["resume_match"]["count"], 4)
        self.assertIn("resume_evidence", json.dumps(api))
        filtered = self.client.get("/api/search?sort=resume&source=linkedin").json()
        self.assertEqual(filtered["total"], 1)
        self.assertEqual(filtered["results"][0]["source_type"], "linkedin")
        self.assertEqual(filtered["source_counts"], self.client.get("/api/search?sort=resume").json()["source_counts"])

    def test_query_constraints_and_public_results_never_receive_private_resume(self):
        self.seed(1)
        self.save()
        self.assertEqual(self.client.get("/api/search?q=chemistry&sort=resume&live=false").json()["count"], 0)
        result = self.client.get("/api/search?q=robotics&sort=resume&live=false")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["cache-control"], "no-store")
        self.assertIn("resume_match", result.json()["results"][0])
        page = self.client.get("/search?q=robotics&sort=resume&source=university")
        self.assertIn('href="/search?q=robotics&sort=resume"', page.text)
        self.assertIn("我的简历依据", self.client.get("/opportunity/robot-0").text)
        self.assertNotIn("resume_match", self.client.get("/api/search?q=robotics&live=false").text)
        self.client.cookies.clear()
        for path in ("/api/search?q=robotics&live=false", "/opportunity/robot-0", "/api/opportunities"):
            body = self.client.get(path).text
            self.assertNotIn("CV_PRIVATE_STUDENT", body)
            self.assertNotIn("resume_evidence", body)

    def test_txt_docx_pdf_and_bad_inputs(self):
        self.assertIn("Python", resumes.parse_local("cv.txt", b"Python and robotics research project experience."))
        document = io.BytesIO()
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Python and robotics research project experience.</w:t></w:r></w:p></w:body></w:document>')
        self.assertIn("Python", resumes.parse_local("cv.docx", document.getvalue()))
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 40 750 Td (Python and robotics research project experience.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        pdf = io.BytesIO(); writer.write(pdf)
        self.assertIn("Python", resumes.parse_local("cv.pdf", pdf.getvalue()))
        blank = PdfWriter(); blank.add_blank_page(width=612, height=792)
        scanned = io.BytesIO(); blank.write(scanned)
        for filename, data in (("cv.pdf", b"not pdf"), ("cv.docx", b"not a zip"), ("cv.pdf", scanned.getvalue()), ("cv.txt", b"x" * (resumes.MAX_BYTES + 1))):
            response = self.client.post("/api/resume/parse", params={"filename": filename}, content=data, headers=self.headers)
            self.assertIn(response.status_code, (413, 422), response.text[:100])
        with patch.object(resumes.subprocess, "run", side_effect=subprocess.TimeoutExpired("parser", 12)), self.assertRaises(HTTPException):
            resumes.parse_local("cv.pdf", b"test")
        self.assertTrue(resumes.parser_slots.acquire(blocking=False)); resumes.parser_slots.release()

    def test_matching_excerpts_and_custom_terms_are_grounded(self):
        chinese = {"title": "机器人视觉暑期研究实习", "description": "开放机器人感知与导航研究实习，欢迎申请。", "source_type": "university", "source_url": "https://example.edu/robot"}
        resume = {"text": "我使用 Python 完成机器人视觉与导航项目，研究机器人感知。", "keywords": ["Robotics", "Computer Vision"]}
        self.assertEqual(resumes.rank_by_resume([chinese], resume, browse=True)[0]["resume_match"]["count"], 2)
        self.assertIsNotNone(resumes.score_search_result("机器人", chinese))
        source = "This is a research internship. " + "context " * 80 + "PyTorch experience."
        match = resumes.match_resume({"description": source}, {"text": self.text, "keywords": ["PyTorch"]})
        self.assertIn("PyTorch", match["evidence"][0]["opportunity_evidence"])
        custom = self.save(text="Built a gravitational lensing analysis experiment.", keywords=["gravitational lensing"])
        self.assertEqual(resumes.match_resume({"description": "Research on gravitational lensing"}, custom)["count"], 1)
        for changes in ({"keywords": ["not in resume"]}, {"keywords": ["mail@example.edu"]}, {"text": "x"}, {"enabled": "true"}, {"keywords": ["x"] * 21}):
            self.assertEqual(self.client.put("/api/resume", json={**self.payload, **changes}, headers=self.headers).status_code, 422)


if __name__ == "__main__":
    unittest.main()
