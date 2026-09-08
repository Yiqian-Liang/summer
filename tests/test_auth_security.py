"""Authentication checks for the private mailbox workspace."""
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app import auth, database
with patch.dict(os.environ, {"DISABLE_LIVE_SEARCH": "1", "DISABLE_SEMANTIC_SEARCH": "1"}):
    from app import main


class AuthSecurityTests(unittest.TestCase):
    def test_hash_upgrade_login_redirect_and_reset_revokes_mail_access(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, "DB_NAME", str(Path(directory) / "auth.db")), patch.dict(os.environ, {"MEDIACRAWLER_DATA_PATH": ""}), TestClient(main.app, base_url="https://testserver") as client:
            password = "test-password"
            legacy = "salt:" + hashlib.sha256(("salt" + password).encode()).hexdigest()
            self.assertTrue(auth.verify_password(password, legacy))
            user_id = database.create_user("student@example.edu", "student", legacy)
            db = database.get_db()
            db["users"].update(user_id, {"onboarding_complete": 1})
            db.conn.close()
            response = client.post("/login", data={"email": "student@example.edu", "password": password, "next": "https://example.org"}, follow_redirects=False)
            self.assertEqual(response.headers["location"], "/")
            self.assertIn("Secure", response.headers["set-cookie"])
            upgraded = database.get_user_by_id(user_id)["password_hash"]
            self.assertTrue(upgraded.startswith("pbkdf2_sha256:600000:"))
            self.assertTrue(auth.verify_password(password, upgraded))
            self.assertFalse(auth.verify_password("wrong", upgraded))
            self.assertEqual(client.get("/login?next=//example.org", follow_redirects=False).headers["location"], "/")
            self.assertEqual(client.get("/api/mail/drafts").status_code, 200)
            self.assertEqual(client.post("/api/mail/drafts", json={"body": "private"}).status_code, 403)
            token = database.create_password_reset_token(user_id)
            self.assertEqual(client.post("/reset-password", data={"token": token, "password": "new-password", "password_confirm": "new-password"}, follow_redirects=False).status_code, 302)
            self.assertEqual(client.get("/api/mail/drafts").status_code, 401)
            self.assertIsNone(database.verify_reset_token(token))
            self.assertTrue(auth.verify_password("new-password", database.get_user_by_id(user_id)["password_hash"]))

    def test_corrupt_hashes_and_external_redirects_fail_closed(self):
        for value in (None, "", "no-separator", "pbkdf2_sha256:999999999:salt:hash"):
            self.assertFalse(auth.verify_password("password", value))
        for value in ("//example.org", "https://example.org", "/\\example.org", "/\r\nLocation:evil"):
            self.assertEqual(main.local_redirect(value), "/")
        self.assertEqual(main.local_redirect("/mail?opportunity=123"), "/mail?opportunity=123")

    def test_unconfigured_recovery_does_not_claim_to_send_or_log_a_token(self):
        with patch.dict(os.environ, {"ENABLE_DEV_PASSWORD_RESET": "0"}), patch.object(main, "create_password_reset_token") as token, patch("builtins.print") as output:
            response = TestClient(main.app).post("/forgot-password", data={"email": "student@example.edu"})
        self.assertEqual(response.status_code, 503)
        self.assertIn("账号恢复邮件", response.text)
        token.assert_not_called()
        output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
