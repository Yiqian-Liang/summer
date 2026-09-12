"""Authenticated, CSRF-protected mailbox and private draft endpoints."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import Response
from urllib.parse import quote
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt

from app import auth, mail_service
from app.database import (
    get_opportunity_by_id,
    get_saved_application,
    get_user_saved_entry,
    save_opportunity,
    update_saved_progress,
)


router = APIRouter(prefix="/api/mail", tags=["mail"])


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(max_length=30)
    email: str = Field(max_length=254)
    label: str = Field(default="", max_length=80)
    password: SecretStr
    smtp_host: str = Field(default="", max_length=253)
    smtp_port: StrictInt = 465
    smtp_security: str = "ssl"
    smtp_username: str = Field(default="", max_length=254)


class DraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(default="", max_length=254)
    subject: str = Field(default="", max_length=250)
    body: str = Field(default="", max_length=100000)
    account_id: Optional[StrictInt] = None
    opportunity_id: str = Field(default="", max_length=100)
    language: str = "en"
    updated_at: Optional[str] = Field(default=None, max_length=100)


class SendInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: StrictBool
    updated_at: str = Field(max_length=100)


def _writer(request):
    user = auth.require_user(request)
    auth.require_csrf(request)
    return user["id"]


@router.get("/accounts")
def accounts(request: Request):
    return mail_service.list_accounts(auth.require_user(request)["id"])


@router.post("/accounts", status_code=201)
def connect(request: Request, payload: AccountInput):
    user_id = _writer(request)
    fields = payload.model_dump(exclude={"password"})
    account = mail_service.connect_account(user_id, **fields, password=payload.password.get_secret_value())
    return {"account": account}


@router.delete("/accounts/{account_id}")
def disconnect(account_id: int, request: Request):
    mail_service.disconnect_account(_writer(request), account_id)
    return {"ok": True}


@router.get("/drafts")
def drafts(request: Request):
    return {"drafts": mail_service.list_drafts(auth.require_user(request)["id"])}


@router.post("/drafts", status_code=201)
def create(request: Request, payload: DraftInput):
    return {"draft": mail_service.save_draft(_writer(request), payload.model_dump())}


@router.get("/drafts/{draft_id}")
def detail(draft_id: int, request: Request):
    return {"draft": mail_service.get_draft(auth.require_user(request)["id"], draft_id)}


@router.put("/drafts/{draft_id}")
def update(draft_id: int, request: Request, payload: DraftInput):
    return {"draft": mail_service.save_draft(_writer(request), payload.model_dump(), draft_id)}


@router.delete("/drafts/{draft_id}")
def delete(draft_id: int, request: Request):
    mail_service.delete_draft(_writer(request), draft_id)
    return {"ok": True}


@router.post("/drafts/{draft_id}/send")
def send(draft_id: int, request: Request, payload: SendInput):
    user_id = _writer(request)
    draft = mail_service.send_draft(user_id, draft_id, payload.confirm, payload.updated_at)
    application = None
    opportunity_id = draft.get("opportunity_id")
    if draft.get("status") == "sent" and opportunity_id:
        saved = get_user_saved_entry(user_id, opportunity_id)
        if not saved and get_opportunity_by_id(opportunity_id):
            save_opportunity(user_id, opportunity_id)
            saved = get_user_saved_entry(user_id, opportunity_id)
        if saved:
            update_saved_progress(
                user_id, opportunity_id, record_contact=True, application_method="email",
                next_action_at=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            )
            application = get_saved_application(user_id, opportunity_id)
    return {"draft": draft, "application": application}


@router.post("/drafts/{draft_id}/attachments", status_code=201)
async def upload_attachment(draft_id: int, request: Request, filename: str = Query(..., min_length=1, max_length=180)):
    user_id = _writer(request)
    mail_service.get_draft(user_id, draft_id)
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > mail_service.MAX_ATTACHMENT_BYTES:
            raise HTTPException(413, "附件总计最多 2 MiB。")
        content.extend(chunk)
    return {"draft": mail_service.add_attachment(user_id, draft_id, filename, bytes(content), request.headers.get("X-Draft-Version"))}


@router.get("/drafts/{draft_id}/attachments/{attachment_id}")
def download_attachment(draft_id: int, attachment_id: int, request: Request):
    item = mail_service.get_attachment(auth.require_user(request)["id"], draft_id, attachment_id)
    return Response(item["content"], media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(item["filename"], safe=""),
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    })


@router.delete("/drafts/{draft_id}/attachments/{attachment_id}")
def delete_attachment(draft_id: int, attachment_id: int, request: Request):
    return {"draft": mail_service.remove_attachment(_writer(request), draft_id, attachment_id, request.headers.get("X-Draft-Version"))}
