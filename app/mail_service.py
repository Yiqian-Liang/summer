"""Private drafts and explicit, single-recipient SMTP delivery."""
import os
import ipaddress
import json
import mimetypes
import re
import smtplib
import socket
import sqlite3
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from requests import RequestException

from app.database import get_db


PROVIDERS = {
    "gmail": {"label": "Gmail / Google Workspace", "host": "smtp.gmail.com", "port": 465, "security": "ssl"},
    "qq": {"label": "QQ / Foxmail", "host": "smtp.qq.com", "port": 465, "security": "ssl"},
    "163": {"label": "网易 163", "host": "smtp.163.com", "port": 465, "security": "ssl"},
    "outlook": {"label": "Outlook / Microsoft 365", "host": "smtp.office365.com", "port": 587, "security": "starttls"},
    "custom": {"label": "学校邮箱 / 其他邮箱（自定义 SMTP）"},
}
MAX_ACCOUNTS = 5
MAX_DRAFTS = 500
MAX_ATTACHMENTS = 5
# ponytail: 2 MiB total fits the simple Graph send request after base64 encoding.
# Add resumable provider uploads only if larger application materials are needed.
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024
MAX_USER_ATTACHMENT_BYTES = 20 * 1024 * 1024
EDITABLE = ("draft", "failed")


@contextmanager
def _db():
    db = get_db()
    try:
        yield db
    finally:
        db.conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat()


def init_mail_tables():
    # Old email_drafts have no owner; never expose or assign their contents.
    with _db() as db, db.conn:
        db.execute("""CREATE TABLE IF NOT EXISTS mail_accounts (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            provider TEXT NOT NULL, email TEXT NOT NULL, label TEXT NOT NULL,
            auth_type TEXT NOT NULL DEFAULT 'smtp', encrypted_secret TEXT NOT NULL,
            smtp_host TEXT NOT NULL DEFAULT '', smtp_port INTEGER NOT NULL DEFAULT 465,
            smtp_security TEXT NOT NULL DEFAULT 'ssl', smtp_username TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(user_id, email)
        )""")
        columns = db["mail_accounts"].columns_dict
        if "encrypted_password" in columns:
            db.execute("ALTER TABLE mail_accounts RENAME COLUMN encrypted_password TO encrypted_secret")
        for name, declaration in {
            "auth_type": "TEXT NOT NULL DEFAULT 'smtp'", "smtp_host": "TEXT NOT NULL DEFAULT ''",
            "smtp_port": "INTEGER NOT NULL DEFAULT 465", "smtp_security": "TEXT NOT NULL DEFAULT 'ssl'",
            "smtp_username": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in columns:
                db.execute(f"ALTER TABLE mail_accounts ADD COLUMN {name} {declaration}")
        db.execute("""CREATE TABLE IF NOT EXISTS outgoing_drafts (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
            recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
            account_id INTEGER, opportunity_id TEXT NOT NULL DEFAULT '',
            language TEXT NOT NULL DEFAULT 'en', status TEXT NOT NULL DEFAULT 'draft',
            sender_email TEXT NOT NULL DEFAULT '', message_id TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, sent_at TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS outgoing_drafts_owner ON outgoing_drafts(user_id, updated_at)")
        db.execute("""UPDATE outgoing_drafts
            SET status = 'unknown',
                last_error = ?,
                updated_at = ?
            WHERE status = 'sending'""", [
                "上次发送过程中服务中断，发送结果未知。请前往真实邮箱的已发送文件夹核验；系统不会自动重发。",
                _now(),
            ])
        db.execute("""CREATE TABLE IF NOT EXISTS mail_attachments (
            id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL,
            filename TEXT NOT NULL, content_type TEXT NOT NULL,
            size INTEGER NOT NULL, content BLOB NOT NULL
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS mail_attachments_draft ON mail_attachments(draft_id)")


def _cipher():
    key = os.getenv("MAIL_ENCRYPTION_KEY", "")
    if not key:
        raise HTTPException(503, "邮箱连接尚未启用：管理员需要配置 MAIL_ENCRYPTION_KEY。草稿仍可正常使用。")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeError):
        raise HTTPException(503, "邮箱加密配置无效，请联系管理员。草稿仍可正常使用。") from None


def _address(value, required=True):
    if not isinstance(value, str) or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(422, "邮箱地址不能包含换行或控制字符。")
    value = value.strip()
    if not value and not required:
        return ""
    if len(value) > 254 or not re.fullmatch(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+", value):
        raise HTTPException(422, "请填写一个有效的邮箱地址；每次只能发送给一位收件人。")
    local, domain = value.rsplit("@", 1)
    if len(local) > 64 or local.startswith(".") or local.endswith(".") or ".." in local:
        raise HTTPException(422, "邮箱地址格式不正确。")
    return local + "@" + domain.lower()


def _owned(db, table, user_id, item_id):
    rows = list(db.query(f"SELECT * FROM {table} WHERE id = ? AND user_id = ?", [item_id, user_id]))
    if not rows:
        raise HTTPException(404, "记录不存在。")
    return rows[0]


def _public_account(account):
    return {key: account[key] for key in ("id", "provider", "auth_type", "email", "label", "created_at",
                                         "smtp_host", "smtp_port", "smtp_security", "smtp_username")}


def _smtp_settings(provider, email, host="", port=465, security="ssl", username=""):
    if provider not in PROVIDERS:
        raise HTTPException(422, "暂不支持此邮箱服务商。")
    if provider != "custom":
        host, port, security = (PROVIDERS[provider][key] for key in ("host", "port", "security"))
    if (port, security) not in ((465, "ssl"), (587, "starttls")):
        raise HTTPException(422, "SMTP 仅支持 465 端口 SSL 或 587 端口 STARTTLS 加密连接。")
    try:
        host = host.strip().rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise HTTPException(422, "SMTP 服务器域名无效。") from None
    if len(host) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host) or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in host.split(".")
    ):
        raise HTTPException(422, "请填写 SMTP 服务器域名，不要包含协议、路径或端口。")
    username = username or email
    if len(username) > 254 or any(ord(c) < 32 or ord(c) == 127 for c in username):
        raise HTTPException(422, "SMTP 用户名格式无效。")
    return {"smtp_host": host, "smtp_port": port, "smtp_security": security, "smtp_username": username}


def _public_socket(host, port, timeout):
    """Resolve once, reject every non-public result, and connect to numeric IPs."""
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError("No SMTP address")
    for family, kind, protocol, canonical, address in addresses:
        ip = ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved or getattr(ip, "ipv4_mapped", None):
            raise HTTPException(422, "SMTP 服务器必须位于公网，不允许连接本机或内网地址。")
    deadline = time.monotonic() + timeout
    last_error = OSError("SMTP connection unavailable")
    for family, kind, protocol, canonical, address in addresses:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        client_socket = socket.socket(family, kind, protocol)
        try:
            client_socket.settimeout(remaining)
            client_socket.connect(address)
            return client_socket
        except OSError as error:
            last_error = error
            client_socket.close()
    raise last_error


class PublicSMTP(smtplib.SMTP):
    def _get_socket(self, host, port, timeout):
        return _public_socket(host, port, timeout)


class PublicSMTPSSL(smtplib.SMTP_SSL):
    def _get_socket(self, host, port, timeout):
        client_socket = _public_socket(host, port, timeout)
        try:
            return self.context.wrap_socket(client_socket, server_hostname=self._host)
        except Exception:
            client_socket.close()
            raise


def _smtp_client(settings):
    context = ssl.create_default_context()
    host, port = settings["smtp_host"], settings["smtp_port"]
    if settings["smtp_security"] == "ssl":
        return PublicSMTPSSL(host, port, timeout=20, context=context)
    client = PublicSMTP(host, port, timeout=20)
    try:
        client.starttls(context=context)
        return client
    except Exception:
        client.close()
        raise


def list_accounts(user_id):
    try:
        _cipher()
        configured, reason = True, ""
    except HTTPException as error:
        configured, reason = False, error.detail
    with _db() as db:
        accounts = [_public_account(row) for row in db.query(
            "SELECT * FROM mail_accounts WHERE user_id = ? ORDER BY id", [user_id]
        )]
    return {"accounts": accounts, "configured": configured, "reason": reason,
            "max_accounts": MAX_ACCOUNTS,
            "providers": [{"id": key, "label": value["label"]} for key, value in PROVIDERS.items()]}


def _check_account_capacity(user_id, email):
    with _db() as db:
        if db["mail_accounts"].count_where("user_id = ?", [user_id]) >= MAX_ACCOUNTS:
            raise HTTPException(409, "最多连接 5 个邮箱，请先断开不再使用的邮箱。")
        if db["mail_accounts"].count_where("user_id = ? AND email = ?", [user_id, email]):
            raise HTTPException(409, "此邮箱已连接；更换授权请先断开。")


def connect_account(user_id, provider, email, label, password, smtp_host="", smtp_port=465, smtp_security="ssl", smtp_username=""):
    _cipher()
    email = _address(email)
    if not isinstance(password, str) or not password.strip() or len(password) > 512 or any(ord(c) < 32 for c in password):
        raise HTTPException(422, "请填写有效的邮箱授权码或应用专用密码。")
    if len(label) > 80 or any(ord(c) < 32 for c in label):
        raise HTTPException(422, "邮箱备注最多 80 字，不能包含换行。")
    settings = _smtp_settings(provider, email, smtp_host, smtp_port, smtp_security, smtp_username)
    _check_account_capacity(user_id, email)
    client = None
    try:
        client = _smtp_client(settings)
        client.login(settings["smtp_username"], password)
    except smtplib.SMTPAuthenticationError:
        raise HTTPException(422, "邮箱验证失败，请检查 SMTP 开关和授权码／应用专用密码。") from None
    except (smtplib.SMTPException, OSError):
        raise HTTPException(502, "暂时无法连接邮箱，请检查邮箱设置后重试。") from None
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
    return store_account(user_id, provider, email, label, {"password": password}, **settings)


def store_account(user_id, provider, email, label, secret, auth_type="smtp", smtp_host="", smtp_port=465,
                  smtp_security="ssl", smtp_username=""):
    """Persist already-verified credentials; called only by SMTP/OAuth flows."""
    email = _address(email)
    if auth_type not in ("smtp", "oauth") or (auth_type == "oauth" and provider not in ("google", "microsoft")):
        raise HTTPException(422, "邮箱授权类型无效。")
    if not isinstance(label, str) or len(label) > 80 or any(ord(c) < 32 for c in label):
        raise HTTPException(422, "邮箱备注格式无效。")
    encrypted = _cipher().encrypt(json.dumps(secret).encode("utf-8")).decode("ascii")
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        if db["mail_accounts"].count_where("user_id = ?", [user_id]) >= MAX_ACCOUNTS:
            raise HTTPException(409, "最多连接 5 个邮箱。")
        try:
            cursor = db.execute("""INSERT INTO mail_accounts
                (user_id, provider, email, label, auth_type, encrypted_secret, smtp_host, smtp_port, smtp_security, smtp_username, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [user_id, provider, email, label.strip() or email, auth_type, encrypted,
                 smtp_host, smtp_port, smtp_security, smtp_username, _now()])
        except sqlite3.IntegrityError:
            raise HTTPException(409, "此邮箱已连接。") from None
        return _public_account(_owned(db, "mail_accounts", user_id, cursor.lastrowid))


def disconnect_account(user_id, account_id):
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        _owned(db, "mail_accounts", user_id, account_id)
        if db["outgoing_drafts"].count_where("user_id = ? AND account_id = ? AND status = 'sending'", [user_id, account_id]):
            raise HTTPException(409, "此邮箱有邮件正在发送，请稍后再断开。")
        db.execute("UPDATE outgoing_drafts SET account_id = NULL, updated_at = ? WHERE user_id = ? AND account_id = ?", [_now(), user_id, account_id])
        db.execute("DELETE FROM mail_accounts WHERE id = ? AND user_id = ?", [account_id, user_id])


def list_drafts(user_id):
    with _db() as db:
        return list(db.query("SELECT * FROM outgoing_drafts WHERE user_id = ? ORDER BY updated_at DESC, id DESC", [user_id]))


def get_draft(user_id, draft_id):
    with _db() as db:
        return _draft(db, user_id, draft_id)


def _draft(db, user_id, draft_id):
    draft = _owned(db, "outgoing_drafts", user_id, draft_id)
    draft["attachments"] = list(db.query(
        "SELECT id, filename, content_type, size FROM mail_attachments WHERE draft_id = ? ORDER BY id", [draft_id]))
    return draft


def _attachment_editor(db, user_id, draft_id, version):
    draft = _owned(db, "outgoing_drafts", user_id, draft_id)
    if draft["status"] not in EDITABLE:
        raise HTTPException(409, "邮件已提交或结果待核实，不能修改附件。")
    if not version or version != draft["updated_at"]:
        raise HTTPException(409, "草稿已变更，请重新打开后修改附件。")


def add_attachment(user_id, draft_id, filename, content, version):
    if not filename or len(filename) > 180 or filename in {".", ".."} or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in filename):
        raise HTTPException(422, "附件文件名无效，请使用不含路径的文件名（最多 180 字）。")
    if not content or len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(413, "附件不能为空，每封邮件附件总计最多 2 MiB。")
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        _attachment_editor(db, user_id, draft_id, version)
        totals = next(db.query("SELECT count(*) AS n, coalesce(sum(size), 0) AS size FROM mail_attachments WHERE draft_id = ?", [draft_id]))
        if totals["n"] >= MAX_ATTACHMENTS or totals["size"] + len(content) > MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "每封邮件最多 5 个附件，总计最多 2 MiB。")
        used = next(db.query("SELECT coalesce(sum(a.size), 0) AS size FROM mail_attachments a JOIN outgoing_drafts d ON a.draft_id = d.id WHERE d.user_id = ?", [user_id]))["size"]
        if used + len(content) > MAX_USER_ATTACHMENT_BYTES:
            raise HTTPException(413, "附件存储已达 20 MiB，请删除不再需要的草稿或附件。")
        db.execute("INSERT INTO mail_attachments (draft_id, filename, content_type, size, content) VALUES (?, ?, ?, ?, ?)", [draft_id, filename, content_type, len(content), content])
        db.execute("UPDATE outgoing_drafts SET updated_at = ? WHERE id = ?", [_now(), draft_id])
        return _draft(db, user_id, draft_id)


def get_attachment(user_id, draft_id, attachment_id):
    with _db() as db:
        _owned(db, "outgoing_drafts", user_id, draft_id)
        rows = list(db.query("SELECT * FROM mail_attachments WHERE id = ? AND draft_id = ?", [attachment_id, draft_id]))
        if not rows:
            raise HTTPException(404, "附件不存在。")
        return rows[0]


def remove_attachment(user_id, draft_id, attachment_id, version):
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        _attachment_editor(db, user_id, draft_id, version)
        if db.execute("DELETE FROM mail_attachments WHERE id = ? AND draft_id = ?", [attachment_id, draft_id]).rowcount != 1:
            raise HTTPException(404, "附件不存在。")
        db.execute("UPDATE outgoing_drafts SET updated_at = ? WHERE id = ?", [_now(), draft_id])
        return _draft(db, user_id, draft_id)


def save_draft(user_id, payload, draft_id=None):
    recipient = _address(payload.get("recipient", ""), required=False)
    subject, body = payload.get("subject", ""), payload.get("body", "")
    if not isinstance(subject, str) or len(subject) > 250 or any(ord(c) < 32 or ord(c) == 127 for c in subject):
        raise HTTPException(422, "邮件主题最多 250 字，不能包含换行或控制字符。")
    if not isinstance(body, str) or len(body) > 100000 or "\x00" in body:
        raise HTTPException(422, "邮件正文最多 100000 字，不能包含空字符。")
    language = payload.get("language", "en")
    if language not in ("en", "zh"):
        raise HTTPException(422, "邮件语言请选择中文或英文。")
    opportunity_id = payload.get("opportunity_id", "")
    if not isinstance(opportunity_id, str) or len(opportunity_id) > 100:
        raise HTTPException(422, "项目编号无效。")
    account_id = payload.get("account_id")
    if account_id is not None and (type(account_id) is not int or account_id <= 0):
        raise HTTPException(422, "请选择已连接的邮箱。")
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        if account_id is not None:
            _owned(db, "mail_accounts", user_id, account_id)
        now = _now()
        values = [recipient, subject, body, account_id, opportunity_id, language, now]
        if draft_id is None:
            if db["outgoing_drafts"].count_where("user_id = ?", [user_id]) >= MAX_DRAFTS:
                raise HTTPException(409, "最多保存 500 份邮件，请删除不再需要的记录。")
            cursor = db.execute("""INSERT INTO outgoing_drafts
                (recipient, subject, body, account_id, opportunity_id, language, updated_at, user_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", values + [user_id, now])
            draft_id = cursor.lastrowid
        else:
            draft = _owned(db, "outgoing_drafts", user_id, draft_id)
            if draft["status"] not in EDITABLE:
                raise HTTPException(409, "这封邮件已发送或发送结果待核实，不能覆盖原始内容。")
            if payload.get("updated_at") and payload["updated_at"] != draft["updated_at"]:
                raise HTTPException(409, "草稿已在其他页面修改，请重新打开后编辑。")
            db.execute("""UPDATE outgoing_drafts SET recipient = ?, subject = ?, body = ?, account_id = ?,
                opportunity_id = ?, language = ?, updated_at = ?, status = 'draft', last_error = ''
                WHERE id = ? AND user_id = ?""", values + [draft_id, user_id])
        return _draft(db, user_id, draft_id)


def delete_draft(user_id, draft_id):
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        draft = _owned(db, "outgoing_drafts", user_id, draft_id)
        if draft["status"] in ("sending", "unknown"):
            raise HTTPException(409, "邮件正在发送或结果待核实，暂时保留原始记录。")
        db.execute("DELETE FROM mail_attachments WHERE draft_id = ?", [draft_id])
        db.execute("DELETE FROM outgoing_drafts WHERE id = ? AND user_id = ?", [draft_id, user_id])


def send_draft(user_id, draft_id, confirm, updated_at):
    if confirm is not True:
        raise HTTPException(422, "请核对收件人和正文，并明确确认发送。")
    cipher = _cipher()
    with _db() as db, db.conn:
        db.execute("BEGIN IMMEDIATE")
        draft = _owned(db, "outgoing_drafts", user_id, draft_id)
        if draft["status"] not in EDITABLE:
            raise HTTPException(409, "邮件已提交或发送结果待核实，请勿重复发送。")
        if not updated_at or updated_at != draft["updated_at"]:
            raise HTTPException(409, "草稿已变更，请重新保存、核对后发送。")
        recipient = _address(draft["recipient"])
        if not draft["subject"].strip() or not draft["body"].strip():
            raise HTTPException(422, "发送前请填写主题和正文。")
        if draft["account_id"] is None:
            raise HTTPException(422, "请选择已连接的发件邮箱。")
        account = _owned(db, "mail_accounts", user_id, draft["account_id"])
        password, settings = None, None
        if account["auth_type"] == "smtp":
            try:
                raw_secret = cipher.decrypt(account["encrypted_secret"].encode("ascii")).decode("utf-8")
                try:
                    secret = json.loads(raw_secret)
                except json.JSONDecodeError:
                    secret = {"password": raw_secret}  # Original SMTP-only encrypted value.
                password = secret["password"]
            except (InvalidToken, ValueError, UnicodeError, KeyError, TypeError):
                raise HTTPException(503, "无法解密邮箱授权码，请断开该邮箱后重新连接。") from None
            settings = _smtp_settings(account["provider"], account["email"], account["smtp_host"],
                                      account["smtp_port"], account["smtp_security"], account["smtp_username"])
        message = EmailMessage()
        message["From"] = _address(account["email"])
        message["To"] = recipient
        message["Subject"] = draft["subject"]
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid()
        message.set_content(draft["body"])
        for attachment in db.query("SELECT * FROM mail_attachments WHERE draft_id = ? ORDER BY id", [draft_id]):
            maintype, subtype = attachment["content_type"].split("/", 1)
            message.add_attachment(attachment["content"], maintype=maintype, subtype=subtype, filename=attachment["filename"])
        db.execute("""UPDATE outgoing_drafts SET status = 'sending', last_error = '',
            sender_email = ?, message_id = ?, updated_at = ? WHERE id = ? AND user_id = ?""",
            [account["email"], message["Message-ID"], _now(), draft_id, user_id])
    # A persisted sending state also blocks retries after process interruption.
    client, transmitting = None, False
    status, error = "failed", "邮箱暂时不可用，未提交邮件，请检查连接后重试。"
    try:
        if account["auth_type"] == "oauth":
            from app.mail_oauth import send_oauth_message
            send_oauth_message(account, message)
            status, error = "sent", ""
        else:
            client = _smtp_client(settings)
            client.login(settings["smtp_username"], password)
            transmitting = True
            refused = client.send_message(message, from_addr=account["email"], to_addrs=[recipient])
            if refused:
                error = "收件地址被邮箱服务商拒绝，邮件未发送。请检查地址。"
            else:
                status, error = "sent", ""
    except HTTPException as failure:
        error = str(failure.detail)
    except RequestException:
        status = "unknown"
        error = "提交邮件时服务响应不确定，发送结果未知。请到真实邮箱核实；系统不会自动重发。"
    except smtplib.SMTPAuthenticationError:
        error = "邮箱登录失败，邮件未发送。请重新连接邮箱。"
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused, smtplib.SMTPDataError):
        error = "邮箱服务商拒绝了这封邮件，邮件未发送。请核对邮箱、收件人和内容。"
    except (smtplib.SMTPException, OSError):
        if transmitting:
            status = "unknown"
            error = "提交邮件时连接中断，发送结果未知。请到真实邮箱核实；系统不会自动重发。"
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
    with _db() as db, db.conn:
        db.execute("""UPDATE outgoing_drafts SET status = ?, last_error = ?, updated_at = ?, sent_at = ?
            WHERE id = ? AND user_id = ? AND status = 'sending'""",
            [status, error, _now(), _now() if status == "sent" else None, draft_id, user_id])
        return _draft(db, user_id, draft_id)
