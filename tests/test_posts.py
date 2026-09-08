"""Offline lifecycle and ownership checks for community recruitment posts."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, database, posts


class PostsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        db_patch = patch.object(database, "DB_NAME", str(Path(temporary.name) / "posts.db"))
        db_patch.start()
        self.addCleanup(db_patch.stop)
        database.init_db()
        database.init_user_tables()
        posts.init_post_tables()
        app = FastAPI()
        app.include_router(posts.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.tokens = []
        self.addCleanup(lambda: [auth.destroy_session(token) for token in self.tokens])
        for name in ("owner", "other"):
            database.create_user(name + "@example.edu", name, "test-only")
        self.login(1, "owner")
        self.payload = {
            "title": "AI summer research opportunity", "university": "Example University",
            "description": "Research assistants are invited to join our computer vision project.",
            "requirements": "Undergraduates welcome", "contact_email": "lab@example.edu",
        }

    def login(self, user_id, name):
        token = auth.create_session(user_id, {"id": user_id, "username": name})
        self.tokens.append(token)
        self.client.cookies.set("session_token", token)
        self.client.headers["X-CSRF-Token"] = auth.get_session(token)["csrf_token"]

    def create(self, **values):
        response = self.client.post("/api/posts", json={**self.payload, **values})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["post"]

    def status(self, post_id, status):
        return self.client.post(f"/api/posts/{post_id}/status", json={"status": status})

    def test_create_publish_edit_close_and_republish(self):
        post = self.create()
        self.assertEqual(post["post_status"], "draft")
        self.assertIsNone(post["published_at"])
        self.assertIn(f"/posts/{post['id']}", post["source_url"])
        self.assertEqual(self.client.get(f"/posts/{post['id']}").status_code, 404)
        self.assertEqual(self.client.get("/posts").context["total"], 0)
        self.assertEqual(self.client.get("/posts/mine").context["total"], 1)
        published = self.status(post["id"], "published").json()["post"]
        self.assertTrue(published["published_at"])
        public = self.client.get("/posts")
        self.assertEqual(public.context["total"], 1)
        self.assertIn("发布者未核验", public.text)
        self.assertEqual(public.context["posts"][0]["author_name"], "owner")
        detail = self.client.get(f"/posts/{post['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(f"/mail?opportunity={post['id']}", detail.text)
        self.assertIn("lab@example.edu", detail.text)
        updated = self.client.put(f"/api/posts/{post['id']}", json={**self.payload, "title": "Updated title"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["post"]["post_status"], "published")
        self.assertEqual(updated.json()["post"]["source_type"], "community")
        self.assertEqual(self.status(post["id"], "closed").status_code, 200)
        self.assertEqual(self.client.get("/posts").context["total"], 0)
        self.assertEqual(self.client.get(f"/posts/{post['id']}").status_code, 404)
        republished = self.status(post["id"], "published").json()["post"]
        self.assertEqual(republished["published_at"], published["published_at"])

    def test_drafts_allow_incomplete_content_but_publishing_does_not(self):
        response = self.client.post("/api/posts", json={"title": "Unfinished"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.status(response.json()["post"]["id"], "published").status_code, 422)
        no_contact = self.create(contact_email="")
        self.assertEqual(self.status(no_contact["id"], "published").status_code, 422)
        post = self.create()
        self.status(post["id"], "published")
        invalid = self.client.put(f"/api/posts/{post['id']}", json={"title": "Missing public content"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get("/posts").context["posts"][0]["title"], self.payload["title"])

    def test_ownership_and_csrf_for_every_mutation(self):
        post_id = self.create()["id"]
        self.login(2, "other")
        self.assertEqual(self.client.get(f"/posts/{post_id}/edit").status_code, 404)
        self.assertEqual(self.client.put(f"/api/posts/{post_id}", json=self.payload).status_code, 404)
        self.assertEqual(self.status(post_id, "published").status_code, 404)
        self.assertEqual(self.client.get("/posts/mine").context["total"], 0)
        self.login(1, "owner")
        self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post("/api/posts", json=self.payload).status_code, 403)
        self.assertEqual(self.client.put(f"/api/posts/{post_id}", json=self.payload).status_code, 403)
        self.assertEqual(self.status(post_id, "published").status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/posts", json=self.payload).status_code, 401)
        for path in ("/posts/mine", "/posts/new", f"/posts/{post_id}/edit"):
            self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 302)

    def test_validation_and_server_owned_metadata(self):
        for values in (
            {"title": " "}, {"title": "x" * 181}, {"title": "one\ntwo"},
            {"contact_email": "invalid"}, {"contact_email": "lab@example.edu\nBcc:other@example.edu"},
            {"direct_apply_link": "javascript:alert(1)"}, {"direct_apply_link": "https://user:pass@example.edu"},
            {"direct_apply_link": "https://example.edu:99999"}, {"deadline": "2026-02-30"},
            {"deadline": "20260901"}, {"source_type": "university"}, {"owner_user_id": 2},
            {"post_status": "published"}, {"quality_score": 100},
            {"post_kind": "verified"},
        ):
            with self.subTest(values=values):
                self.assertEqual(self.client.post("/api/posts", json={**self.payload, **values}).status_code, 422)
        post = self.create(deadline="2028-02-29", direct_apply_link="https://example.edu/apply")
        self.assertEqual((post["provider"], post["quality_score"], post["has_direct_apply"]), ("user_post", 40, 1))
        self.assertEqual(self.status(post["id"], "verified").status_code, 422)

    def test_templates_escape_content_and_pages_are_bounded(self):
        marker = '<script>alert("untrusted")</script>'
        post = self.create(title=marker)
        edited = self.client.get(f"/posts/{post['id']}/edit")
        self.assertEqual(edited.status_code, 200)
        self.assertNotIn(marker, edited.text)
        self.assertIn("&lt;script&gt;", edited.text)
        self.assertIn(auth.get_session(self.tokens[-1])["csrf_token"], edited.text)
        self.status(post["id"], "published")
        self.assertNotIn(marker, self.client.get("/posts").text)
        self.assertEqual(self.client.get("/posts?page=0").status_code, 422)
        self.assertEqual(self.client.get("/posts/mine?page=100001").status_code, 422)
        self.assertEqual(self.client.get("/posts/new").status_code, 200)

    def test_experience_discussion_filters_and_optional_recruitment_fields(self):
        for kind in ("experience", "discussion"):
            post = self.create(post_kind=kind, university="", contact_email="", title=f"{kind} 100% experience")
            self.assertEqual(self.status(post["id"], "published").status_code, 200)
            detail = self.client.get(f"/posts/{post['id']}")
            self.assertEqual(detail.status_code, 200)
            self.assertNotIn("起草联系邮件", detail.text)
        draft = self.create(post_kind="experience")
        page = self.client.get("/posts?kind=experience&q=100%25")
        self.assertEqual(page.context["total"], 1)
        self.assertEqual(page.context["posts"][0]["post_kind"], "experience")
        self.assertEqual(self.client.get("/posts?kind=experience&q=missing").context["total"], 0)
        self.assertEqual(self.client.get("/posts/mine?kind=experience").context["total"], 2)
        self.assertEqual(self.client.get("/posts?kind=invalid").status_code, 422)
        self.assertEqual(self.client.get(f"/posts/{draft['id']}").status_code, 404)

    def test_comment_lifecycle_ownership_authentication_and_visibility(self):
        post = self.create(post_kind="discussion")
        path = f"/api/posts/{post['id']}/comments"
        self.assertEqual(self.client.post(path, json={"body": "Hello"}).status_code, 404)
        self.status(post["id"], "published")
        marker = '<img src=x onerror="alert(1)">'
        added = self.client.post(path, json={"body": marker})
        self.assertEqual(added.status_code, 201)
        comment_id = added.json()["comment"]["id"]
        detail = self.client.get(f"/posts/{post['id']}")
        self.assertEqual(detail.context["comment_count"], 1)
        self.assertNotIn(marker, detail.text)
        self.assertIn(f'data-delete-comment="{comment_id}"', detail.text)
        for body in (" ", "x" * 2001):
            self.assertEqual(self.client.post(path, json={"body": body}).status_code, 422)
        self.login(2, "other")
        self.assertEqual(self.client.delete(path + f"/{comment_id}").status_code, 404)
        self.assertNotIn(f'data-delete-comment="{comment_id}"', self.client.get(f"/posts/{post['id']}").text)
        self.login(1, "owner")
        self.client.headers.pop("X-CSRF-Token")
        self.assertEqual(self.client.post(path, json={"body": "Test"}).status_code, 403)
        self.assertEqual(self.client.delete(path + f"/{comment_id}").status_code, 403)
        self.login(1, "owner")
        self.assertEqual(self.client.delete(path + f"/{comment_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/posts/{post['id']}").context["comment_count"], 0)
        self.status(post["id"], "closed")
        self.assertEqual(self.client.post(path, json={"body": "Test"}).status_code, 404)
        self.client.cookies.clear()
        self.assertEqual(self.client.post(path, json={"body": "Test"}).status_code, 401)

    def test_complete_posts_and_comment_pagination(self):
        post = self.create()
        self.status(post["id"], "published")
        db = database.get_db()
        source = dict(db["opportunities"].get(post["id"]))
        db["opportunities"].insert_all([{**source, "id": f"extra-{i}", "source_url": f"https://example.edu/posts/{i}"} for i in range(25)])
        db["post_comments"].insert_all([{"post_id": post["id"], "owner_user_id": 1, "body": f"Comment {i}", "created_at": "2026-09-01"} for i in range(51)])
        db.conn.close()
        page = self.client.get("/posts?page=2&kind=recruitment&q=AI")
        self.assertEqual((page.context["total"], len(page.context["posts"])), (26, 2))
        self.assertIn("kind=recruitment&amp;q=AI", page.text)
        detail = self.client.get(f"/posts/{post['id']}?page=2")
        self.assertEqual((detail.context["comment_count"], len(detail.context["comments"])), (51, 1))

    def test_migration_is_repeatable_and_does_not_touch_collected_records(self):
        db = database.get_db()
        db["opportunities"].insert({"id": "collected", "title": "Existing opening", "source_type": "university", "source_url": "https://example.edu/job"})
        db.conn.close()
        posts.init_post_tables()
        posts.init_post_tables()
        self.assertEqual(self.client.get("/posts/mine").context["total"], 0)
        self.assertEqual(self.client.put("/api/posts/collected", json=self.payload).status_code, 404)
        db = database.get_db()
        self.assertEqual(db["opportunities"].get("collected")["title"], "Existing opening")
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
