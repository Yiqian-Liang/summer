"""Offline OAuth flows and mailbox sending; every provider request is mocked."""
import base64
import hashlib
import json
import logging
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, database, mail_oauth, mail_service


class MailOAuthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.key = Fernet.generate_key().decode()
        environment = {"MAIL_ENCRYPTION_KEY": self.key, "GOOGLE_CLIENT_ID": "google-client",
                       "GOOGLE_CLIENT_SECRET": "google-secret", "MICROSOFT_CLIENT_ID": "ms-client",
                       "MICROSOFT_CLIENT_SECRET": "ms-secret", "SITE_BASE_URL": "http://localhost:8000"}
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(temporary.name) / "oauth.db")),
            patch.dict(os.environ, environment, clear=True),
            patch.dict(auth.sessions, {}, clear=True),
            patch("app.mail_oauth.requests.sessions.Session.request", side_effect=AssertionError("Real network requests are forbidden")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        mail_service.init_mail_tables()
        app = FastAPI()
        app.include_router(mail_oauth.router)
        self.client = TestClient(app)
        self.token = auth.create_session(1, {"id": 1, "username": "student"})
        self.client.cookies.set("session_token", self.token)
        self.headers = {"X-CSRF-Token": auth.sessions[self.token]["csrf_token"]}

    def response(self, data=None, status=200):
        response = Mock(status_code=status)
        response.json.return_value = data
        return response

    def token_response(self, **changes):
        return self.response({"access_token": "private-access", "refresh_token": "private-refresh", "expires_in": 3600, "token_type": "Bearer", **changes})

    def start(self, provider="google"):
        response = self.client.post(f"/api/mail/oauth/{provider}/start", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return parse_qs(urlsplit(response.json()["authorization_url"]).query)

    def finish(self, provider="google", state=None, **params):
        state = state or self.start(provider)["state"][0]
        return self.client.get(f"/mail/oauth/{provider}/callback", params={"state": state, "code": "private-code", **params}, follow_redirects=False)

    def account(self, provider="google", expires=None):
        public = mail_service.store_account(1, provider, "student@example.edu", "Research", {
            "access_token": "private-access", "refresh_token": "private-refresh",
            "expires_at": expires if expires is not None else time.time() + 3600,
        }, auth_type="oauth")
        with mail_service._db() as db:
            return db["mail_accounts"].get(public["id"])

    def message(self):
        message = EmailMessage()
        message["From"] = "student@example.edu"
        message["To"] = "professor@example.edu"
        message["Subject"] = "暑研申请"
        message["Message-ID"] = "<one-submission@example.edu>"
        message.set_content("Hello,\nI am interested in your research.\n谢谢。")
        return message

    def test_configuration_capabilities_and_safe_site_origin(self):
        response = self.client.get("/api/mail/oauth/providers")
        self.assertEqual([row["id"] for row in response.json()["providers"]], ["google", "microsoft"])
        self.assertTrue(all(row["available"] for row in response.json()["providers"]))
        self.assertNotIn("secret", response.text)
        with patch.dict(os.environ, {"GOOGLE_CLIENT_SECRET": ""}):
            self.assertFalse(mail_oauth.oauth_capabilities()["providers"][0]["available"])
            self.assertEqual(self.client.post("/api/mail/oauth/google/start", headers=self.headers).status_code, 503)
        for base in ("https://example.edu/path", "http://example.edu", "https://user:secret@example.edu", "https://example.edu?next=evil", "//evil.edu", "https://example.edu:bad"):
            with self.subTest(base=base), patch.dict(os.environ, {"SITE_BASE_URL": base}):
                self.assertFalse(mail_oauth.oauth_capabilities()["providers"][0]["available"])
        with patch.dict(os.environ, {"SITE_BASE_URL": "https://summer.example.edu"}):
            self.assertEqual(self.start()["redirect_uri"], ["https://summer.example.edu/mail/oauth/google/callback"])
        with patch.dict(os.environ, {"SITE_BASE_URL": "", "RENDER_EXTERNAL_URL": "https://summer-alpha.onrender.com"}):
            self.assertEqual(self.start()["redirect_uri"], ["https://summer-alpha.onrender.com/mail/oauth/google/callback"])

    def test_start_requires_login_csrf_and_uses_pkce_minimal_scopes(self):
        self.assertEqual(self.client.post("/api/mail/oauth/google/start").status_code, 403)
        for provider in ("google", "microsoft"):
            query = self.start(provider)
            pending = auth.sessions[self.token]["mail_oauth_states"][query["state"][0]]
            challenge = base64.urlsafe_b64encode(hashlib.sha256(pending["verifier"].encode()).digest()).decode().rstrip("=")
            self.assertEqual(query["code_challenge"], [challenge])
            self.assertEqual(query["code_challenge_method"], ["S256"])
            self.assertGreaterEqual(len(pending["verifier"]), 43)
            self.assertNotIn("client_secret", query)
            self.assertNotIn("read", query["scope"][0].lower().replace("user.read", ""))
            self.assertNotIn("modify", query["scope"][0])
            self.assertIn("send", query["scope"][0].lower())
        self.client.cookies.clear()
        self.assertEqual(self.client.post("/api/mail/oauth/google/start", headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get("/api/mail/oauth/providers").status_code, 401)

    def test_invalid_expired_wrong_provider_and_wrong_session_states_never_exchange(self):
        with patch("app.mail_oauth.requests.post") as post:
            self.assertIn("invalid_state", self.finish(state="attacker").headers["location"])
            state = self.start()["state"][0]
            auth.sessions[self.token]["mail_oauth_states"][state]["expires_at"] = 0
            self.assertIn("invalid_state", self.finish(state=state).headers["location"])
            state = self.start()["state"][0]
            self.assertIn("invalid_state", self.finish("microsoft", state=state).headers["location"])
            state = self.start()["state"][0]
            other = auth.create_session(2, {"id": 2})
            self.client.cookies.set("session_token", other)
            self.assertIn("invalid_state", self.finish(state=state).headers["location"])
            self.client.cookies.clear()
            self.assertIn("session_expired", self.finish(state=state).headers["location"])
            post.assert_not_called()

    def test_google_callback_verifies_email_and_encrypts_tokens_without_leaking_them(self):
        query = self.start()
        state = query["state"][0]
        verifier = auth.sessions[self.token]["mail_oauth_states"][state]["verifier"]
        with patch("app.mail_oauth.requests.post", return_value=self.token_response()) as post, patch("app.mail_oauth.requests.get", return_value=self.response({"email": "student@example.edu", "email_verified": True})) as get:
            response = self.finish(state=state)
            self.assertEqual(response.headers["location"], "/mail?connected=1")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertEqual(post.call_args.kwargs["data"]["code_verifier"], verifier)
            self.assertFalse(post.call_args.kwargs["allow_redirects"])
            self.assertEqual(get.call_args.args[0], mail_oauth.PROVIDERS["google"]["profile"])
            self.assertIn("invalid_state", self.finish(state=state).headers["location"])
            post.assert_called_once()
        public = mail_service.list_accounts(1)["accounts"][0]
        self.assertEqual((public["provider"], public["auth_type"]), ("google", "oauth"))
        with mail_service._db() as db:
            stored = db["mail_accounts"].get(public["id"])
        self.assertNotIn("private-access", str(stored))
        self.assertNotIn("private-refresh", str(public))
        secret = json.loads(Fernet(self.key.encode()).decrypt(stored["encrypted_secret"].encode()))
        self.assertEqual(secret["refresh_token"], "private-refresh")

    def test_microsoft_callback_uses_graph_profile_and_accepts_upn(self):
        with patch("app.mail_oauth.requests.post", return_value=self.token_response(scope="Mail.Send User.Read")), patch("app.mail_oauth.requests.get", return_value=self.response({"mail": None, "userPrincipalName": "student@outlook.com"})) as get:
            self.assertEqual(self.finish("microsoft").headers["location"], "/mail?connected=1")
        self.assertEqual(mail_service.list_accounts(1)["accounts"][0]["email"], "student@outlook.com")
        self.assertEqual(get.call_args.args[0], mail_oauth.PROVIDERS["microsoft"]["profile"])

    def test_callback_cancellation_and_error_values_are_fixed_and_one_time(self):
        for error, expected in (("access_denied", "cancelled"), ("<script>private-secret</script>", "connection_failed")):
            state = self.start()["state"][0]
            with patch("app.mail_oauth.requests.post") as post:
                response = self.finish(state=state, error=error)
                self.assertEqual(response.headers["location"], f"/mail?oauth_error={expected}")
                self.assertIn("invalid_state", self.finish(state=state).headers["location"])
                post.assert_not_called()

    def test_unverified_identity_bad_tokens_and_missing_send_scope_do_not_connect(self):
        for token in (self.token_response(refresh_token=None), self.token_response(scope="openid email"), self.token_response(access_token="bad\nheader"), self.token_response(expires_in="NaN"), self.response({"error": "private-error"}, 400)):
            with self.subTest(token=token), patch("app.mail_oauth.requests.post", return_value=token), patch("app.mail_oauth.requests.get") as get:
                response = self.finish()
                self.assertIn("connection_failed", response.headers["location"])
                self.assertEqual(mail_service.list_accounts(1)["accounts"], [])
                get.assert_not_called()
        with patch("app.mail_oauth.requests.post", return_value=self.token_response()), patch("app.mail_oauth.requests.get", return_value=self.response({"email": "student@example.edu", "email_verified": False})):
            self.assertIn("connection_failed", self.finish().headers["location"])
        self.assertEqual(mail_service.list_accounts(1)["accounts"], [])

    def test_gmail_send_uses_rfc5322_base64_and_no_read_or_refresh_requests(self):
        account = self.account()
        with patch("app.mail_oauth.requests.post", return_value=self.response({}, 200)) as post:
            self.assertIsNone(mail_oauth.send_oauth_message(account, self.message()))
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0], mail_oauth.PROVIDERS["google"]["send"])
        raw = base64.urlsafe_b64decode(post.call_args.kwargs["json"]["raw"])
        decoded = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertEqual(decoded["To"], "professor@example.edu")
        self.assertEqual(decoded["Message-ID"], "<one-submission@example.edu>")
        self.assertEqual(decoded["Subject"], "暑研申请")
        self.assertIn("谢谢", decoded.get_content())
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    def test_graph_send_is_plain_text_single_recipient_and_saved_to_sent(self):
        account = self.account("microsoft")
        with patch("app.mail_oauth.requests.post", return_value=self.response(None, 202)) as post:
            mail_oauth.send_oauth_message(account, self.message())
        payload = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], mail_oauth.PROVIDERS["microsoft"]["send"])
        self.assertEqual(payload["message"]["body"]["contentType"], "Text")
        self.assertEqual(len(payload["message"]["toRecipients"]), 1)
        self.assertTrue(payload["saveToSentItems"])
        self.assertNotIn("ccRecipients", payload["message"])

    def test_refresh_rotates_encrypted_tokens_and_does_not_repeat_for_stale_account_objects(self):
        account = self.account(expires=0)
        with patch("app.mail_oauth.requests.post", side_effect=[self.token_response(access_token="new-access", refresh_token="new-refresh"), self.response({}, 200), self.response({}, 200)]) as post:
            mail_oauth.send_oauth_message(account, self.message())
            mail_oauth.send_oauth_message(account, self.message())
        self.assertEqual(post.call_count, 3)
        refresh_call = post.call_args_list[0]
        self.assertEqual(refresh_call.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new-access")
        with mail_service._db() as db:
            row = db["mail_accounts"].get(account["id"])
        secret = json.loads(mail_service._cipher().decrypt(row["encrypted_secret"].encode()))
        self.assertEqual(secret["refresh_token"], "new-refresh")

    def test_concurrent_refresh_happens_once_without_holding_database_write_lock(self):
        account = self.account(expires=0)
        entered, release = Event(), Event()
        def refresh(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(3))
            return self.token_response()
        with patch("app.mail_oauth.requests.post", side_effect=refresh) as post, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(mail_oauth._access_token, account)
            self.assertTrue(entered.wait(3))
            second = pool.submit(mail_oauth._access_token, account)
            mail_service.save_draft(1, {"subject": "While refreshing", "body": "Editable"})
            release.set()
            self.assertEqual(first.result(), "private-access")
            self.assertEqual(second.result(), "private-access")
            self.assertEqual(post.call_count, 1)

    def test_explicit_send_rejections_are_failed_but_uncertain_submissions_are_not_retryable(self):
        account = self.account()
        for status, expected in ((400, HTTPException), (401, HTTPException), (403, HTTPException), (429, HTTPException), (408, requests.RequestException), (500, requests.RequestException), (302, requests.RequestException)):
            with self.subTest(status=status), patch("app.mail_oauth.requests.post", return_value=self.response({}, status)) as post, self.assertRaises(expected):
                mail_oauth.send_oauth_message(account, self.message())
            post.assert_called_once()
        with patch("app.mail_oauth.requests.post", side_effect=requests.Timeout), self.assertRaises(requests.RequestException):
            mail_oauth.send_oauth_message(account, self.message())
        with mail_service._db() as db, db.conn:
            expired = {"access_token": "private-access", "refresh_token": "private-refresh", "expires_at": 0}
            db.execute("UPDATE mail_accounts SET encrypted_secret = ? WHERE id = ?", [mail_service._cipher().encrypt(json.dumps(expired).encode()).decode(), account["id"]])
        with patch("app.mail_oauth.requests.post", side_effect=requests.Timeout("private-secret")) as post, self.assertRaises(HTTPException) as error:
            mail_oauth.send_oauth_message(account, self.message())
        self.assertNotIn("private-secret", error.exception.detail)
        post.assert_called_once()
        self.assertEqual(post.call_args.args[0], mail_oauth.PROVIDERS["google"]["token"])

    def test_tokens_cannot_be_loaded_through_another_owner(self):
        account = self.account()
        with patch("app.mail_oauth.requests.post") as post, self.assertRaises(HTTPException) as error:
            mail_oauth.send_oauth_message({**account, "user_id": 2}, self.message())
        self.assertEqual(error.exception.status_code, 404)
        post.assert_not_called()

    def test_service_tracks_submission_and_blocks_retries_after_unknown_outcomes(self):
        account = self.account()
        for status, expected in ((200, "sent"), (400, "failed"), (503, "unknown")):
            draft = mail_service.save_draft(1, {"recipient": "professor@example.edu", "subject": "Research inquiry", "body": "Hello, I am interested in this project.", "account_id": account["id"]})
            with self.subTest(status=status), patch("app.mail_oauth.requests.post", return_value=self.response({}, status)) as post:
                result = mail_service.send_draft(1, draft["id"], True, draft["updated_at"])
                self.assertEqual(result["status"], expected)
                if expected != "failed":
                    with self.assertRaises(HTTPException) as error:
                        mail_service.send_draft(1, draft["id"], True, result["updated_at"])
                    self.assertEqual(error.exception.status_code, 409)
                post.assert_called_once()

    def test_callback_access_logs_strip_authorization_code_and_state(self):
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                                   ("localhost", "GET", "/mail/oauth/google/callback?code=private-code&state=private-state", "1.1", 303), None)
        self.assertTrue(mail_oauth._CallbackLogFilter().filter(record))
        self.assertNotIn("private-code", record.getMessage())
        self.assertNotIn("private-state", record.getMessage())
        self.assertIn("/mail/oauth/google/callback", record.getMessage())


if __name__ == "__main__":
    unittest.main()
