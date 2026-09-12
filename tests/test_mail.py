"""Offline mail checks: all SMTP calls are mocked, no mail is ever sent."""
import os
import json
import smtplib
import socket
import ssl
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from requests import RequestException

from app import auth, database, mail_routes, mail_service
from app.mail_routes import router


class MailTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.key = Fernet.generate_key().decode()
        for patcher in (
            patch.object(database, "DB_NAME", str(Path(temp.name) / "mail.db")),
            patch.dict(os.environ, {"MAIL_ENCRYPTION_KEY": self.key}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        mail_service.init_mail_tables()
        mail_service.init_mail_tables()

        def require_user(request):
            user = request.headers.get("X-Test-User")
            if not user:
                raise HTTPException(401, "请登录")
            return {"id": int(user)}

        def require_csrf(request):
            if request.headers.get("X-CSRF-Token") != "test-csrf":
                raise HTTPException(403, "请求验证失败")

        for name, function in (("require_user", require_user), ("require_csrf", require_csrf)):
            patcher = patch.object(auth, name, side_effect=function, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.original_ssl = mail_service.PublicSMTPSSL
        smtp = patch.object(mail_service, "PublicSMTPSSL")
        self.smtp = smtp.start()
        self.addCleanup(smtp.stop)
        self.smtp.return_value.send_message.return_value = {}
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)
        self.headers = {"X-Test-User": "1", "X-CSRF-Token": "test-csrf"}
        self.other = {**self.headers, "X-Test-User": "2"}

    def account(self, email="student@gmail.com", headers=None, **fields):
        return self.client.post("/api/mail/accounts", headers=headers or self.headers, json={
            "provider": "gmail", "email": email, "password": "private-app-password",
            "label": "申请邮箱", **fields,
        })

    def draft(self, account_id=None, headers=None, **fields):
        return self.client.post("/api/mail/drafts", headers=headers or self.headers, json={
            "recipient": "professor@university.edu", "subject": "暑期科研申请",
            "body": "Dear Professor,\nI am interested in your project.", "account_id": account_id,
            **fields,
        })

    def send(self, draft, **fields):
        return self.client.post(f"/api/mail/drafts/{draft['id']}/send", headers=self.headers, json={
            "confirm": True, "updated_at": draft["updated_at"], **fields,
        })

    def test_credentials_encrypted_multiple_accounts_and_disabled_configuration(self):
        with patch.dict(os.environ, {"MAIL_ENCRYPTION_KEY": ""}):
            info = self.client.get("/api/mail/accounts", headers=self.headers).json()
            self.assertFalse(info["configured"])
            self.assertIn("MAIL_ENCRYPTION_KEY", info["reason"])
            self.assertEqual(self.account().status_code, 503)
            self.assertEqual(self.draft().status_code, 201)
            self.smtp.assert_not_called()
        with patch.dict(os.environ, {"MAIL_ENCRYPTION_KEY": "invalid-secret-key"}):
            response = self.client.get("/api/mail/accounts", headers=self.headers)
            self.assertFalse(response.json()["configured"])
            self.assertNotIn("invalid-secret-key", response.text)
        account = self.account().json()["account"]
        self.assertEqual(self.account().status_code, 409)
        self.assertEqual(self.account("student@qq.com", provider="qq").status_code, 201)
        self.assertNotIn("password", str(account))
        db = database.get_db()
        stored = db["mail_accounts"].get(account["id"])
        db.conn.close()
        self.assertNotIn("private-app-password", str(stored))
        secret = json.loads(Fernet(self.key.encode()).decrypt(stored["encrypted_secret"].encode()))
        self.assertEqual(secret, {"password": "private-app-password"})
        self.smtp.return_value.send_message.assert_not_called()
        self.assertEqual(self.smtp.call_args.args, ("smtp.qq.com", 465))
        self.assertTrue(self.smtp.call_args.kwargs["context"].check_hostname)
        self.assertEqual(self.smtp.call_args.kwargs["context"].verify_mode, 2)

    def test_login_and_csrf_are_required_for_every_mutation(self):
        self.assertEqual(self.client.get("/api/mail/drafts").status_code, 401)
        self.assertEqual(self.client.get("/api/mail/accounts").status_code, 401)
        self.assertEqual(self.client.post("/api/mail/drafts", json={}).status_code, 401)
        draft = self.draft().json()["draft"]
        account = self.account().json()["account"]
        for method, path, payload in (
            ("post", "/api/mail/drafts", {}),
            ("put", f"/api/mail/drafts/{draft['id']}", {}),
            ("delete", f"/api/mail/drafts/{draft['id']}", None),
            ("post", f"/api/mail/drafts/{draft['id']}/send", {"confirm": True, "updated_at": draft["updated_at"]}),
            ("post", "/api/mail/accounts", {"provider": "gmail", "email": "x@y.edu", "password": "test-password"}),
            ("delete", f"/api/mail/accounts/{account['id']}", None),
        ):
            response = self.client.request(method, path, headers={"X-Test-User": "1"}, json=payload)
            self.assertEqual(response.status_code, 403, path)
        self.smtp.return_value.send_message.assert_not_called()

    def test_owner_isolation_including_account_attachment_and_sending(self):
        account = self.account().json()["account"]
        draft = self.draft(account["id"]).json()["draft"]
        self.assertEqual(self.client.get("/api/mail/drafts", headers=self.other).json()["drafts"], [])
        self.assertEqual(self.client.get("/api/mail/accounts", headers=self.other).json()["accounts"], [])
        for method, path, payload in (
            ("get", f"/api/mail/drafts/{draft['id']}", None),
            ("put", f"/api/mail/drafts/{draft['id']}", {}),
            ("delete", f"/api/mail/drafts/{draft['id']}", None),
            ("post", f"/api/mail/drafts/{draft['id']}/send", {"confirm": True, "updated_at": draft["updated_at"]}),
            ("delete", f"/api/mail/accounts/{account['id']}", None),
        ):
            self.assertEqual(self.client.request(method, path, headers=self.other, json=payload).status_code, 404)
        self.assertEqual(self.draft(account["id"], headers=self.other).status_code, 404)
        self.smtp.return_value.send_message.assert_not_called()

    def test_header_injection_multi_recipient_arbitrary_host_and_storage_limits(self):
        for recipient in ("a@b.edu\r\nBcc: x@y.edu", "a@b.edu,x@y.edu", "a@b.edu;x@y.edu", "Name <a@b.edu>", "a..b@c.edu"):
            self.assertEqual(self.draft(recipient=recipient).status_code, 422, recipient)
        self.assertEqual(self.draft(subject="Hi\nBcc: x@y.edu").status_code, 422)
        self.assertEqual(self.draft(account_id=True).status_code, 422)
        self.assertEqual(self.account(provider="localhost").status_code, 422)
        self.assertEqual(self.account(host="127.0.0.1").status_code, 422)
        self.smtp.assert_not_called()
        for i in range(5):
            self.assertEqual(self.account(f"student{i}@gmail.com").status_code, 201)
        self.assertEqual(self.account("sixth@gmail.com").status_code, 409)
        self.assertEqual(self.smtp.call_count, 5)
        with patch.object(mail_service, "MAX_DRAFTS", 1):
            self.assertEqual(self.draft().status_code, 201)
            self.assertEqual(self.draft().status_code, 409)

    def test_saved_version_confirmation_and_success_are_idempotent(self):
        account = self.account().json()["account"]
        draft = self.draft(account["id"]).json()["draft"]
        self.assertEqual(self.send(draft, confirm=False).status_code, 422)
        self.assertEqual(self.send(draft, confirm="true").status_code, 422)
        saved = self.client.put(f"/api/mail/drafts/{draft['id']}", headers=self.headers, json={
            "recipient": draft["recipient"], "subject": "Updated subject", "body": "Saved body",
            "account_id": account["id"], "updated_at": draft["updated_at"],
        }).json()["draft"]
        self.assertEqual(self.send(draft).status_code, 409)
        self.assertEqual(self.client.put(f"/api/mail/drafts/{draft['id']}", headers=self.headers,
                                         json={"updated_at": draft["updated_at"]}).status_code, 409)
        sent = self.send(saved).json()["draft"]
        self.assertEqual(sent["status"], "sent")
        self.assertTrue(sent["sent_at"])
        message = self.smtp.return_value.send_message.call_args.args[0]
        self.assertEqual(message["Subject"], "Updated subject")
        self.assertIn("Saved body", message.get_content())
        self.assertEqual(self.smtp.return_value.send_message.call_args.kwargs["to_addrs"], [draft["recipient"]])
        self.assertEqual(self.send(sent).status_code, 409)
        self.assertEqual(self.client.put(f"/api/mail/drafts/{draft['id']}", headers=self.headers, json={}).status_code, 409)
        self.smtp.return_value.send_message.assert_called_once()

    def test_success_updates_linked_application_but_failure_does_not(self):
        account = self.account().json()["account"]
        success = self.draft(account["id"], opportunity_id="opp-1").json()["draft"]
        application = {"id": "opp-1", "progress_stage": "contacted"}
        with patch.object(mail_routes, "get_user_saved_entry", return_value={"id": 9}), \
                patch.object(mail_routes, "update_saved_progress", return_value=True) as update, \
                patch.object(mail_routes, "get_saved_application", return_value=application):
            response = self.send(success)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["application"], application)
        self.assertTrue(update.call_args.kwargs["record_contact"])
        self.assertEqual(update.call_args.kwargs["application_method"], "email")
        self.assertIn("+00:00", update.call_args.kwargs["next_action_at"])

        failed = self.draft(account["id"], opportunity_id="opp-2").json()["draft"]
        self.smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
        with patch.object(mail_routes, "get_user_saved_entry") as lookup, \
                patch.object(mail_routes, "update_saved_progress") as update:
            response = self.send(failed)
        self.assertEqual(response.json()["draft"]["status"], "failed")
        self.assertIsNone(response.json()["application"])
        lookup.assert_not_called()
        update.assert_not_called()

    def test_delivery_failures_distinguish_safe_retry_from_unknown_result(self):
        account = self.account().json()["account"]
        draft = self.draft(account["id"]).json()["draft"]
        self.smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"secret raw server message")
        failed = self.send(draft).json()["draft"]
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("secret raw", failed["last_error"])
        self.smtp.return_value.send_message.assert_not_called()
        self.smtp.return_value.login.side_effect = None
        self.smtp.return_value.send_message.side_effect = smtplib.SMTPRecipientsRefused({draft["recipient"]: (550, "no")})
        rejected = self.send(failed).json()["draft"]
        self.assertEqual(rejected["status"], "failed")
        self.smtp.return_value.send_message.side_effect = TimeoutError("lost connection after DATA")
        unknown = self.send(rejected).json()["draft"]
        self.assertEqual(unknown["status"], "unknown")
        self.assertEqual(self.send(unknown).status_code, 409)
        self.assertEqual(self.client.delete(f"/api/mail/drafts/{draft['id']}", headers=self.headers).status_code, 409)
        self.assertEqual(self.smtp.return_value.send_message.call_count, 2)

    def test_startup_marks_interrupted_send_unknown_for_manual_verification(self):
        draft = self.draft().json()["draft"]
        db = database.get_db()
        with db.conn:
            db.execute("UPDATE outgoing_drafts SET status = 'sending' WHERE id = ?", [draft["id"]])
        db.conn.close()

        mail_service.init_mail_tables()

        recovered = self.client.get(
            f"/api/mail/drafts/{draft['id']}", headers=self.headers
        ).json()["draft"]
        self.assertEqual(recovered["status"], "unknown")
        self.assertIn("真实邮箱", recovered["last_error"])
        self.assertIn("不会自动重发", recovered["last_error"])
        self.assertEqual(self.send(recovered).status_code, 409)

    def test_concurrent_send_and_disconnect_cannot_change_inflight_message(self):
        account = self.account().json()["account"]
        draft = self.draft(account["id"]).json()["draft"]
        entered, release = Event(), Event()

        def deliver(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return {}

        self.smtp.return_value.send_message.side_effect = deliver
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(mail_service.send_draft, 1, draft["id"], True, draft["updated_at"])
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(self.send(draft).status_code, 409)
                self.assertEqual(self.client.delete(f"/api/mail/accounts/{account['id']}", headers=self.headers).status_code, 409)
                self.assertEqual(self.client.put(f"/api/mail/drafts/{draft['id']}", headers=self.headers, json={}).status_code, 409)
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=5)["status"], "sent")
        self.assertEqual(self.client.delete(f"/api/mail/accounts/{account['id']}", headers=self.headers).status_code, 200)
        updated = self.client.get(f"/api/mail/drafts/{draft['id']}", headers=self.headers).json()["draft"]
        self.assertIsNone(updated["account_id"])
        self.assertEqual(updated["sender_email"], account["email"])
        self.assertEqual(self.client.get("/api/mail/accounts", headers=self.headers).json()["accounts"], [])
        self.smtp.return_value.send_message.assert_called_once()

    def test_custom_smtp_uses_required_tls_and_never_logs_in_on_tls_failure(self):
        with patch.object(mail_service, "PublicSMTP") as plain:
            response = self.account("me@university.edu", provider="custom", smtp_host="smtp.university.edu",
                                    smtp_port=587, smtp_security="starttls", smtp_username="campus-id")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["account"]["smtp_host"], "smtp.university.edu")
            self.assertEqual(plain.call_args.args, ("smtp.university.edu", 587))
            context = plain.return_value.starttls.call_args.kwargs["context"]
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            plain.return_value.login.assert_called_once_with("campus-id", "private-app-password")
            plain.return_value.login.reset_mock()
            plain.return_value.starttls.side_effect = smtplib.SMTPNotSupportedError("no tls")
            rejected = self.account("second@university.edu", provider="custom", smtp_host="smtp.university.edu",
                                    smtp_port=587, smtp_security="starttls")
            self.assertEqual(rejected.status_code, 502)
            plain.return_value.login.assert_not_called()
            plain.return_value.send_message.assert_not_called()
        for fields in ({"smtp_host": "http://127.0.0.1/"}, {"smtp_host": "smtp.edu:25"},
                       {"smtp_host": "smtp.edu", "smtp_port": 25},
                       {"smtp_host": "smtp.edu", "smtp_port": 587, "smtp_security": "ssl"}):
            self.assertEqual(self.account("invalid@university.edu", provider="custom", **fields).status_code, 422)

    def test_smtp_dns_rebinding_private_addresses_and_tls_hostname(self):
        def address(ip):
            return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 465))

        for ip in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "224.0.0.1", "100.64.0.1"):
            with patch.object(mail_service.socket, "getaddrinfo", return_value=[address("8.8.8.8"), address(ip)]), \
                    patch.object(mail_service.socket, "socket") as connection:
                with self.assertRaises(HTTPException):
                    mail_service._public_socket("smtp.university.edu", 465, 20)
                connection.assert_not_called()
        with patch.object(mail_service.socket, "getaddrinfo", side_effect=[[address("8.8.8.8")], [address("127.0.0.1")]]) as dns, \
                patch.object(mail_service.socket, "socket") as connection:
            result = mail_service._public_socket("smtp.university.edu", 465, 20)
            self.assertIs(result, connection.return_value)
            dns.assert_called_once()
            connection.return_value.connect.assert_called_once_with(("8.8.8.8", 465))
        client = object.__new__(self.original_ssl)
        client.context, client._host = MagicMock(), "smtp.university.edu"
        with patch.object(mail_service, "_public_socket") as connect:
            client._get_socket("smtp.university.edu", 465, 20)
            client.context.wrap_socket.assert_called_once_with(connect.return_value, server_hostname="smtp.university.edu")
            client.context.wrap_socket.side_effect = ssl.SSLCertVerificationError("invalid cert")
            with self.assertRaises(ssl.SSLCertVerificationError):
                client._get_socket("smtp.university.edu", 465, 20)
            connect.return_value.close.assert_called_once()

    def test_oauth_dispatch_keeps_same_ownership_confirmation_and_delivery_states(self):
        account = mail_service.store_account(1, "google", "student@gmail.com", "Google", {"refresh_token": "private-token"}, auth_type="oauth")
        send_oauth = MagicMock(return_value=None)
        with patch.dict(sys.modules, {"app.mail_oauth": SimpleNamespace(send_oauth_message=send_oauth)}):
            draft = self.draft(account["id"]).json()["draft"]
            self.assertEqual(self.send(draft).json()["draft"]["status"], "sent")
            self.assertEqual(send_oauth.call_args.args[0]["auth_type"], "oauth")
            self.assertEqual(send_oauth.call_args.args[1]["To"], draft["recipient"])
            send_oauth.side_effect = HTTPException(502, "授权过期，请重新连接")
            draft = self.draft(account["id"]).json()["draft"]
            failed = self.send(draft).json()["draft"]
            self.assertEqual(failed["status"], "failed")
            send_oauth.side_effect = RequestException("timeout after submission")
            unknown = self.send(failed).json()["draft"]
            self.assertEqual(unknown["status"], "unknown")
            self.assertEqual(self.send(unknown).status_code, 409)
            self.smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
