"""Private attachment lifecycle and real MIME/provider payloads, without sending."""
import base64
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import auth, database, mail_service, mail_oauth
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(temp.name) / "mail.db")),
            patch.dict(os.environ, {"MAIL_ENCRYPTION_KEY": Fernet.generate_key().decode(), "MEDIACRAWLER_DATA_PATH": ""}),
            patch.dict(auth.sessions, {}, clear=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        token = auth.create_session(1, {"id": 1, "username": "Student"})
        self.client.cookies.set("session_token", token)
        self.headers = {"X-CSRF-Token": auth.get_session(token)["csrf_token"]}
        self.draft = mail_service.save_draft(1, {"recipient": "lab@example.edu", "subject": "Application", "body": "Please find my application materials attached."})
        self.path = f'/api/mail/drafts/{self.draft["id"]}/attachments'

    def upload(self, filename="简历.pdf", content=b"%PDF-test\x00\xff", version=None):
        response = self.client.post(self.path, params={"filename": filename}, content=content,
            headers={**self.headers, "X-Draft-Version": version or self.draft["updated_at"]})
        if response.status_code == 201:
            self.draft = response.json()["draft"]
        return response

    def test_private_lifecycle_download_and_delete_cleanup(self):
        old_version = self.draft["updated_at"]
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        attachment = self.draft["attachments"][0]
        self.assertNotIn("content", attachment)
        self.assertNotEqual(self.draft["updated_at"], old_version)
        path = self.path + '/' + str(attachment["id"])
        download = self.client.get(path)
        self.assertEqual(download.content, b"%PDF-test\x00\xff")
        self.assertTrue(download.headers["content-disposition"].startswith("attachment; filename*=UTF-8''"))
        self.assertEqual(download.headers["x-content-type-options"], "nosniff")
        self.assertEqual(download.headers["cache-control"], "no-store")
        self.assertEqual(self.client.delete(path, headers={**self.headers, "X-Draft-Version": old_version}).status_code, 409)
        self.assertEqual(self.client.delete(path, headers={"X-Draft-Version": self.draft["updated_at"]}).status_code, 403)
        self.assertEqual(self.client.delete(path, headers={**self.headers, "X-Draft-Version": self.draft["updated_at"]}).status_code, 200)
        self.assertEqual(self.client.get(path).status_code, 404)
        self.draft = mail_service.get_draft(1, self.draft["id"])
        self.upload()
        mail_service.delete_draft(1, self.draft["id"])
        with mail_service._db() as db:
            self.assertEqual(db["mail_attachments"].count, 0)

    def test_owner_and_csrf_checks_cover_upload_download_remove(self):
        self.assertEqual(self.client.post(self.path, params={"filename": "cv.pdf"}, content=b"x").status_code, 403)
        self.upload()
        item_path = self.path + '/' + str(self.draft["attachments"][0]["id"])
        for user_id in (2, None):
            self.client.cookies.clear()
            if user_id:
                token = auth.create_session(user_id, {"id": user_id, "username": "Other"})
                self.client.cookies.set("session_token", token)
                self.headers = {"X-CSRF-Token": auth.get_session(token)["csrf_token"]}
            status = 404 if user_id else 401
            self.assertEqual(self.upload().status_code, status)
            self.assertEqual(self.client.get(item_path).status_code, status)
            self.assertEqual(self.client.delete(item_path, headers=self.headers).status_code, status)

    def test_names_limits_and_stale_uploads_are_rejected_without_partial_data(self):
        for name in ("../cv.pdf", "a\\cv.pdf", "bad\nname", ".."):
            self.assertEqual(self.upload(filename=name).status_code, 422)
        self.assertEqual(self.upload(content=b"").status_code, 413)
        self.assertEqual(self.upload(content=b"x" * (mail_service.MAX_ATTACHMENT_BYTES + 1)).status_code, 413)
        with patch.object(mail_service, "MAX_USER_ATTACHMENT_BYTES", 3):
            self.assertEqual(self.upload(content=b"four").status_code, 413)
        version = self.draft["updated_at"]
        self.assertEqual(self.upload(content=b"a").status_code, 201)
        self.assertEqual(self.upload(version=version).status_code, 409)
        for _ in range(4):
            self.assertEqual(self.upload(content=b"a").status_code, 201)
        self.assertEqual(self.upload(content=b"a").status_code, 413)
        self.assertEqual(len(mail_service.get_draft(1, self.draft["id"])["attachments"]), 5)
        self.draft = mail_service.save_draft(1, {"body": "Size limit check"})
        self.path = f'/api/mail/drafts/{self.draft["id"]}/attachments'
        self.assertEqual(self.upload(content=b"x" * mail_service.MAX_ATTACHMENT_BYTES).status_code, 201)
        self.assertEqual(self.upload(content=b"x").status_code, 413)

    def test_concurrent_uploads_only_accept_one_saved_version(self):
        def upload(_):
            try:
                mail_service.add_attachment(1, self.draft["id"], "cv.pdf", b"x", self.draft["updated_at"])
                return 201
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(upload, range(2))), [201, 409])

    def test_smtp_gmail_and_graph_receive_identical_attachment_bytes(self):
        account = mail_service.store_account(1, "qq", "student@qq.com", "Test", {"password": "fake"})
        self.draft = mail_service.save_draft(1, {**self.draft, "account_id": account["id"]}, self.draft["id"])
        self.upload()
        smtp = Mock()
        smtp.send_message.return_value = {}
        with patch.object(mail_service, "_smtp_client", return_value=smtp):
            sent = mail_service.send_draft(1, self.draft["id"], True, self.draft["updated_at"])
        self.assertEqual(sent["status"], "sent")
        mime = smtp.send_message.call_args.args[0]
        self.assertEqual(mime.get_body(preferencelist=('plain',)).get_content().strip(), self.draft["body"])
        attachment = list(mime.iter_attachments())[0]
        self.assertEqual(attachment.get_filename(), "简历.pdf")
        self.assertEqual(attachment.get_payload(decode=True), b"%PDF-test\x00\xff")
        for provider in ("google", "microsoft"):
            with patch.object(mail_oauth, "_access_token", return_value="fake"), patch.object(mail_oauth.requests, "post", return_value=Mock(status_code=200 if provider == "google" else 202)) as post:
                mail_oauth.send_oauth_message({"auth_type": "oauth", "provider": provider}, mime)
            payload = post.call_args.kwargs["json"]
            if provider == "google":
                decoded = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(payload["raw"]))
                self.assertEqual(list(decoded.iter_attachments())[0].get_payload(decode=True), b"%PDF-test\x00\xff")
            else:
                self.assertEqual(payload["message"]["body"]["content"].strip(), self.draft["body"])
                self.assertEqual(base64.b64decode(payload["message"]["attachments"][0]["contentBytes"]), b"%PDF-test\x00\xff")
        self.assertEqual(self.upload().status_code, 409)
        self.assertEqual(len(sent["attachments"]), 1)


if __name__ == "__main__":
    unittest.main()
