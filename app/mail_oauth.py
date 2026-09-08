"""Optional Google and Microsoft mailbox authorization; never reads inboxes."""
import base64
import hashlib
import json
import logging
import math
import os
import secrets
import threading
import time
from email import policy
from urllib.parse import urlencode, urlsplit

import requests
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import auth, mail_service


router = APIRouter(tags=["mail"])
PROVIDERS = {
    "google": {
        "label": "Google / Gmail", "env": "GOOGLE",
        "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
        "token": "https://oauth2.googleapis.com/token",
        "profile": "https://openidconnect.googleapis.com/v1/userinfo",
        "send": "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        "scope": "openid email https://www.googleapis.com/auth/gmail.send",
        "send_scope": "https://www.googleapis.com/auth/gmail.send",
    },
    "microsoft": {
        "label": "Microsoft / Outlook", "env": "MICROSOFT",
        "authorize": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "profile": "https://graph.microsoft.com/v1.0/me?$select=id,mail,userPrincipalName",
        "send": "https://graph.microsoft.com/v1.0/me/sendMail",
        "scope": "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read offline_access",
        "send_scope": "Mail.Send",
    },
}
STATE_TTL = 600
TIMEOUT = (5, 20)
NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
# ponytail: locks share the existing single-process session limit; use shared locks with multi-worker sessions.
_refresh_locks = {}


class _CallbackLogFilter(logging.Filter):
    def filter(self, record):
        # Uvicorn normally logs callback authorization codes and state in the URL.
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if isinstance(args[2], str) and args[2].startswith("/mail/oauth/"):
                args[2] = args[2].split("?", 1)[0]
                record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(_CallbackLogFilter())


def _configuration(provider):
    if provider not in PROVIDERS:
        raise HTTPException(404, "不支持此邮箱授权服务。")
    settings = PROVIDERS[provider]
    prefix = settings["env"]
    client_id = os.getenv(f"{prefix}_CLIENT_ID", "").strip()
    client_secret = os.getenv(f"{prefix}_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(503, f"管理员尚未配置 {prefix}_CLIENT_ID 和 {prefix}_CLIENT_SECRET。")
    base = os.getenv("SITE_BASE_URL", "http://localhost:8000").strip().rstrip("/")
    try:
        parsed = urlsplit(base)
        valid_scheme = parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"})
        if not valid_scheme or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except ValueError:
        raise HTTPException(503, "SITE_BASE_URL 必须是 HTTPS 站点地址；本机开发可使用 http://localhost:8000。") from None
    mail_service._cipher()
    return {**settings, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": f"{base}/mail/oauth/{provider}/callback"}


def oauth_capabilities():
    providers = []
    for provider, settings in PROVIDERS.items():
        try:
            _configuration(provider)
            available, reason = True, ""
        except HTTPException as error:
            available, reason = False, error.detail
        providers.append({"id": provider, "label": settings["label"], "available": available, "reason": reason})
    return {"providers": providers}


def _redirect(error=None):
    return RedirectResponse("/mail?" + urlencode({"oauth_error": error} if error else {"connected": "1"}), status_code=303, headers=NO_STORE)


@router.get("/api/mail/oauth/providers")
def providers(request: Request):
    auth.require_user(request)
    return JSONResponse(oauth_capabilities(), headers=NO_STORE)


@router.post("/api/mail/oauth/{provider}/start")
def start(provider: str, request: Request):
    user = auth.require_csrf(request)
    config = _configuration(provider)
    with mail_service._db() as db:
        if db["mail_accounts"].count_where("user_id = ?", [user["id"]]) >= mail_service.MAX_ACCOUNTS:
            raise HTTPException(409, "最多连接 5 个邮箱，请先断开不再使用的邮箱。")
    session = auth.get_session(request.cookies.get("session_token"))
    states = session.setdefault("mail_oauth_states", {})
    for key, pending in list(states.items()):
        if pending["expires_at"] <= time.time():
            states.pop(key, None)
    if len(states) >= 5:
        raise HTTPException(429, "请先完成已打开的邮箱授权，或稍后重试。")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    states[state] = {"provider": provider, "user_id": user["id"], "verifier": verifier,
                     "expires_at": time.time() + STATE_TTL, "redirect_uri": config["redirect_uri"]}
    params = {"client_id": config["client_id"], "response_type": "code", "redirect_uri": config["redirect_uri"],
              "scope": config["scope"], "state": state, "code_challenge_method": "S256",
              "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")}
    if provider == "google":
        params.update(access_type="offline", prompt="consent select_account")
    else:
        params.update(response_mode="query", prompt="select_account")
    return JSONResponse({"authorization_url": config["authorize"] + "?" + urlencode(params)}, headers=NO_STORE)


def _token_response(config, form, previous_refresh=None):
    try:
        response = requests.post(config["token"], data={
            **form, "client_id": config["client_id"], "client_secret": config["client_secret"],
        }, timeout=TIMEOUT, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError()
        data = response.json()
        access = data.get("access_token")
        refresh = data.get("refresh_token") or previous_refresh
        if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh:
            raise ValueError()
        if any(ord(c) < 32 for c in access + refresh) or len(access) > 20000 or len(refresh) > 20000:
            raise ValueError()
        if data.get("token_type", "Bearer").lower() != "bearer":
            raise ValueError()
        expires = float(data.get("expires_in", 3600))
        if not math.isfinite(expires) or not 60 <= expires <= 31536000:
            raise ValueError()
        scope = data.get("scope", "")
        if not isinstance(scope, str):
            raise ValueError()
        granted = {item.lower().removeprefix("https://graph.microsoft.com/") for item in scope.split()}
        if scope and config["send_scope"].lower() not in granted:
            raise ValueError()
        return {"access_token": access, "refresh_token": refresh, "expires_at": time.time() + expires}
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        raise HTTPException(422, "邮箱授权已失效或未授予发送权限，请重新连接邮箱。") from None


def _profile_email(config, access_token, provider):
    try:
        response = requests.get(config["profile"], headers={"Authorization": "Bearer " + access_token},
                                timeout=TIMEOUT, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError()
        profile = response.json()
        if provider == "google":
            if profile.get("email_verified") is not True:
                raise ValueError()
            email = profile.get("email")
        else:
            email = profile.get("mail") or profile.get("userPrincipalName")
        return mail_service._address(email)
    except (requests.RequestException, ValueError, TypeError, AttributeError, HTTPException):
        raise HTTPException(422, "无法确认已授权邮箱的地址，请重新连接。") from None


@router.get("/mail/oauth/{provider}/callback")
def callback(provider: str, request: Request):
    try:
        user = auth.require_user(request)
    except HTTPException:
        return _redirect("session_expired")
    session = auth.get_session(request.cookies.get("session_token"))
    state = request.query_params.get("state", "")
    pending = session.get("mail_oauth_states", {}).pop(state, None)
    if not pending or pending["expires_at"] <= time.time() or pending["provider"] != provider or pending["user_id"] != user["id"]:
        return _redirect("invalid_state")
    if request.query_params.get("error"):
        return _redirect("cancelled" if request.query_params["error"] == "access_denied" else "connection_failed")
    code = request.query_params.get("code", "")
    if not code or len(code) > 4096 or any(ord(c) < 32 for c in code):
        return _redirect("connection_failed")
    try:
        config = _configuration(provider)
        if config["redirect_uri"] != pending["redirect_uri"]:
            return _redirect("invalid_state")
        tokens = _token_response(config, {"grant_type": "authorization_code", "code": code,
                                          "redirect_uri": config["redirect_uri"], "code_verifier": pending["verifier"]})
        email = _profile_email(config, tokens["access_token"], provider)
        mail_service.store_account(user["id"], provider, email, email, tokens, auth_type="oauth")
    except HTTPException as error:
        return _redirect("unavailable" if error.status_code == 503 else "account_conflict" if error.status_code == 409 else "connection_failed")
    return _redirect()


def _access_token(account):
    lock = _refresh_locks.setdefault((account["user_id"], account["id"]), threading.Lock())
    with lock:
        with mail_service._db() as db:
            current = mail_service._owned(db, "mail_accounts", account["user_id"], account["id"])
        if current["auth_type"] != "oauth" or current["provider"] not in PROVIDERS:
            raise HTTPException(422, "此邮箱的授权方式无效，请重新连接。")
        cipher = mail_service._cipher()
        try:
            tokens = json.loads(cipher.decrypt(current["encrypted_secret"].encode("ascii")).decode("utf-8"))
            access, refresh = tokens["access_token"], tokens["refresh_token"]
            expiry = float(tokens["expires_at"])
            if not isinstance(access, str) or not isinstance(refresh, str) or not access or not refresh or not math.isfinite(expiry):
                raise ValueError()
            if any(ord(c) < 32 for c in access + refresh):
                raise ValueError()
        except (InvalidToken, UnicodeError, ValueError, KeyError, TypeError):
            raise HTTPException(422, "邮箱授权信息无法读取，请断开后重新连接。") from None
        if expiry > time.time() + 60:
            return access
        config = _configuration(current["provider"])
        form = {"grant_type": "refresh_token", "refresh_token": refresh}
        if current["provider"] == "microsoft":
            form["scope"] = config["scope"]
        tokens = _token_response(config, form, previous_refresh=refresh)
        encrypted = cipher.encrypt(json.dumps(tokens).encode("utf-8")).decode("ascii")
        with mail_service._db() as db, db.conn:
            cursor = db.execute("UPDATE mail_accounts SET encrypted_secret = ? WHERE id = ? AND user_id = ? AND auth_type = 'oauth'", [encrypted, account["id"], account["user_id"]])
            if cursor.rowcount != 1:
                raise HTTPException(422, "邮箱已断开，请重新连接后发送。")
        return tokens["access_token"]


def send_oauth_message(account, message):
    """Called only after mail_service durably claims one confirmed draft."""
    if account.get("provider") not in PROVIDERS or account.get("auth_type") != "oauth":
        raise HTTPException(422, "此邮箱的授权方式无效。")
    access_token = _access_token(account)
    provider = account["provider"]
    if provider == "google":
        payload = {"raw": base64.urlsafe_b64encode(message.as_bytes(policy=policy.SMTP)).decode("ascii")}
    else:
        payload = {"message": {"subject": str(message["Subject"]),
                    "body": {"contentType": "Text", "content": message.get_body(preferencelist=('plain',)).get_content()},
                    "toRecipients": [{"emailAddress": {"address": str(message["To"])}}]},
                   "saveToSentItems": True}
        attachments = [{"@odata.type": "#microsoft.graph.fileAttachment",
                        "name": part.get_filename(), "contentType": part.get_content_type(),
                        "contentBytes": base64.b64encode(part.get_payload(decode=True)).decode("ascii")}
                       for part in message.iter_attachments()]
        if attachments:
            payload["message"]["attachments"] = attachments
    # Never retry a sending POST: a timeout or server failure may follow acceptance.
    response = requests.post(PROVIDERS[provider]["send"], json=payload,
                             headers={"Authorization": "Bearer " + access_token},
                             timeout=TIMEOUT, allow_redirects=False)
    if response.status_code == (200 if provider == "google" else 202):
        return
    if 400 <= response.status_code < 500 and response.status_code != 408:
        raise HTTPException(422, "邮箱服务商拒绝了发送请求。请检查授权、收件人和邮箱配额后再试。")
    raise requests.RequestException("Mailbox submission result is uncertain")
