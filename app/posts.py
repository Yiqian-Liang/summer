"""Member recruitment posts, stored alongside collected opportunities."""
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.auth import csrf_token_for, get_current_user, require_csrf, require_user, validate_email
from app.database import get_db, record_user_behavior
from app.i18n import DEFAULT_LANGUAGE, I18n, LANGUAGES
from app.rate_limits import enforce_rate_limit

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def init_post_tables():
    db = get_db()
    try:
        columns = db["opportunities"].columns_dict
        for name, kind in {"owner_user_id": int, "post_status": str, "contact_email": str, "post_kind": str}.items():
            if name not in columns:
                db["opportunities"].add_column(name, kind)
        db["opportunities"].create_index(["owner_user_id", "post_status"], if_not_exists=True)
        db.execute("UPDATE opportunities SET post_kind = 'recruitment' WHERE provider = 'user_post' AND post_kind IS NULL")
        db.conn.commit()
        if "post_comments" not in db.table_names():
            db["post_comments"].create({
                "id": int, "post_id": str, "owner_user_id": int, "body": str, "created_at": str,
            }, pk="id", foreign_keys=[("post_id", "opportunities", "id"), ("owner_user_id", "users", "id")])
            db["post_comments"].create_index(["post_id", "created_at"])
    finally:
        db.conn.close()


class PostInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=180)
    post_kind: Literal["recruitment", "experience", "discussion"] = "recruitment"
    university: str = Field(default="", max_length=160)
    professor_name: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=20000)
    requirements: str = Field(default="", max_length=8000)
    deadline: str = Field(default="", max_length=10)
    contact_email: str = Field(default="", max_length=254)
    direct_apply_link: str = Field(default="", max_length=2048)

    @field_validator("title", "university", "professor_name", "contact_email", "direct_apply_link")
    @classmethod
    def single_line(cls, value):
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("此字段不能包含换行或控制字符")
        return value

    @field_validator("contact_email")
    @classmethod
    def email(cls, value):
        if value and not validate_email(value):
            raise ValueError("请填写有效的联系邮箱")
        return value

    @field_validator("direct_apply_link")
    @classmethod
    def application_link(cls, value):
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password and parsed.port != 0
                     and not any(char.isspace() for char in value))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("申请链接必须是有效的 http 或 https 地址")
        return value

    @field_validator("deadline")
    @classmethod
    def application_deadline(cls, value):
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except ValueError:
                raise ValueError("截止日期格式应为 YYYY-MM-DD")
        return value


class PostStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["draft", "published", "closed"]


class CommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    body: str = Field(min_length=1, max_length=2000)


def _owned_post(db, post_id, user_id):
    rows = list(db["opportunities"].rows_where(
        "id = ? AND owner_user_id = ? AND provider = 'user_post' AND source_type = 'community'",
        [post_id, user_id], limit=1,
    ))
    if not rows:
        raise HTTPException(status_code=404, detail="帖子不存在或无权编辑")
    return rows[0]


def _check_publishable(post):
    if len(post.get("description", "").strip()) < 20:
        raise HTTPException(status_code=422, detail="发布前请填写至少 20 字的正文")
    if post.get("post_kind", "recruitment") != "recruitment":
        return
    if not post.get("university"):
        raise HTTPException(status_code=422, detail="发布招募前请填写机构名称")
    if not post.get("contact_email") and not post.get("direct_apply_link"):
        raise HTTPException(status_code=422, detail="发布前请至少提供联系邮箱或申请链接")


def _prefill_value(raw: Optional[str], max_len: int) -> str:
    if not raw:
        return ""
    cleaned = str(raw).replace("\r", " ").replace("\n", " ")
    return cleaned[:max_len].strip()


def _merge_prefill_post(request, post):
    query = request.query_params
    draft = {
        "post_kind": query.get("post_kind", post.get("post_kind", "recruitment")).strip(),
        "title": query.get("title", post.get("title", "")),
        "university": query.get("university", post.get("university", "")),
        "professor_name": query.get("professor_name", post.get("professor_name", "")),
        "description": query.get("description", post.get("description", "")),
        "requirements": query.get("requirements", post.get("requirements", "")),
        "contact_email": query.get("contact_email", post.get("contact_email", "")),
        "direct_apply_link": query.get("direct_apply_link", post.get("direct_apply_link", "")),
        "deadline": query.get("deadline", post.get("deadline", "")),
        "source_url_hint": query.get("source_url", ""),
    }
    if draft["post_kind"] not in {"recruitment", "experience", "discussion"}:
        draft["post_kind"] = post.get("post_kind", "recruitment")
    if draft["source_url_hint"]:
        draft["source_url_hint"] = _prefill_value(draft["source_url_hint"], 300)
    for field, limit in (
        ("title", 180),
        ("university", 160),
        ("professor_name", 120),
        ("description", 20000),
        ("requirements", 8000),
        ("contact_email", 254),
        ("direct_apply_link", 2048),
        ("deadline", 10),
    ):
        draft[field] = _prefill_value(draft[field], limit)
    if draft["source_url_hint"] and (len(draft["description"]) + len(f" [source: {draft['source_url_hint']}]") <= 20000):
        draft["description"] = (draft["description"] + f" [来源链接] {draft['source_url_hint']}").strip()
    if draft["contact_email"] and not validate_email(draft["contact_email"]):
        draft["contact_email"] = ""
    return draft


def _context(request, **extra):
    token = request.cookies.get("session_token")
    language = request.query_params.get("lang") or request.cookies.get("lang", DEFAULT_LANGUAGE)
    return {
        "request": request, "user": get_current_user(token),
        "csrf_token": csrf_token_for(request),
        "i18n": I18n(language if language in LANGUAGES else DEFAULT_LANGUAGE),
        "languages": LANGUAGES, **extra,
    }


def _posts_page(request, mine, page, kind, q):
    user = get_current_user(request.cookies.get("session_token"))
    if mine and not user:
        return RedirectResponse("/login?next=/posts/mine", status_code=302)
    db = get_db()
    try:
        where = "o.provider = 'user_post' AND o.source_type = 'community'"
        params = []
        if mine:
            where += " AND o.owner_user_id = ?"
            params.append(user["id"])
        else:
            where += " AND o.post_status = 'published'"
        if kind:
            where += " AND o.post_kind = ?"
            params.append(kind)
        q = q.strip()
        if q:
            term = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            where += " AND (o.title LIKE ? ESCAPE '\\' OR o.description LIKE ? ESCAPE '\\' OR o.university LIKE ? ESCAPE '\\')"
            params.extend([term] * 3)
        total = db.execute(f"SELECT COUNT(*) FROM opportunities o WHERE {where}", params).fetchone()[0]
        rows = list(db.query(
            f"""SELECT o.*, u.username AS author_name FROM opportunities o
                LEFT JOIN users u ON o.owner_user_id = u.id WHERE {where}
                ORDER BY {'o.updated_at' if mine else 'o.published_at'} DESC, o.id
                LIMIT 24 OFFSET ?""", params + [(page - 1) * 24],
        ))
    finally:
        db.conn.close()
    return templates.TemplateResponse("posts.html", _context(
        request, posts=rows, mine=mine, page=page, total=total, total_pages=(total + 23) // 24, kind=kind, query=q,
    ))


@router.get("/posts")
def posts_page(request: Request, page: int = Query(1, ge=1, le=100000),
               kind: Literal["", "recruitment", "experience", "discussion"] = "", q: str = Query("", max_length=300)):
    return _posts_page(request, False, page, kind, q)


@router.get("/posts/mine")
def my_posts_page(request: Request, page: int = Query(1, ge=1, le=100000),
                  kind: Literal["", "recruitment", "experience", "discussion"] = "", q: str = Query("", max_length=300)):
    return _posts_page(request, True, page, kind, q)


@router.get("/posts/new")
def new_post_page(request: Request):
    user = get_current_user(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse("/login?next=/posts/new", status_code=302)
    return templates.TemplateResponse("post_form.html", _context(request, post=_merge_prefill_post(request, {})))


@router.get("/posts/{post_id}/edit")
def edit_post_page(request: Request, post_id: str):
    user = get_current_user(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse(f"/login?next=/posts/{post_id}/edit", status_code=302)
    db = get_db()
    try:
        post = _owned_post(db, post_id, user["id"])
    finally:
        db.conn.close()
    return templates.TemplateResponse("post_form.html", _context(request, post=post))


@router.post("/api/posts", status_code=201)
def create_post(request: Request, payload: PostInput):
    user = require_user(request)
    require_csrf(request)
    enforce_rate_limit(request, "post_create", 30, 3600, user["id"])
    now = datetime.now(timezone.utc).isoformat()
    post_id = "post-" + uuid4().hex
    post = {
        **payload.model_dump(), "id": post_id, "owner_user_id": user["id"],
        "post_status": "draft", "source_type": "community", "provider": "user_post",
        "source_url": (os.getenv("SITE_BASE_URL") or "http://localhost:8000").rstrip("/") + f"/posts/{post_id}",
        "created_at": now, "updated_at": now, "published_at": None,
        "quality_score": 40, "hiring_signals": "[]",
        "has_direct_apply": int(bool(payload.direct_apply_link)),
    }
    db = get_db()
    try:
        db["opportunities"].insert(post)
    finally:
        db.conn.close()
    return {"post": post}


@router.put("/api/posts/{post_id}")
def update_post(request: Request, post_id: str, payload: PostInput):
    user = require_user(request)
    require_csrf(request)
    db = get_db()
    try:
        post = _owned_post(db, post_id, user["id"])
        values = {**payload.model_dump(), "updated_at": datetime.now(timezone.utc).isoformat(),
                  "has_direct_apply": int(bool(payload.direct_apply_link))}
        if post["post_status"] == "published":
            _check_publishable(values)
        db["opportunities"].update(post_id, values)
        return {"post": {**post, **values}}
    finally:
        db.conn.close()


@router.post("/api/posts/{post_id}/status")
def change_post_status(request: Request, post_id: str, payload: PostStatus):
    user = require_user(request)
    require_csrf(request)
    if payload.status == "published":
        enforce_rate_limit(request, "post_publish", 30, 86400, user["id"])
    db = get_db()
    try:
        post = _owned_post(db, post_id, user["id"])
        if payload.status == "published":
            _check_publishable(post)
        now = datetime.now(timezone.utc).isoformat()
        values = {"post_status": payload.status, "updated_at": now}
        if payload.status == "published" and not post.get("published_at"):
            values["published_at"] = now
        db["opportunities"].update(post_id, values)
        return {"post": {**post, **values}}
    finally:
        db.conn.close()


def _public_post(db, post_id):
    rows = list(db.query("""
        SELECT o.*, u.username AS author_name FROM opportunities o
        LEFT JOIN users u ON o.owner_user_id = u.id
        WHERE o.id = ? AND o.source_type = 'community' AND o.provider = 'user_post'
        AND o.post_status = 'published'
    """, [post_id]))
    if not rows:
        raise HTTPException(status_code=404, detail="帖子不存在或已下架")
    return rows[0]


@router.get("/posts/{post_id}")
def post_detail_page(request: Request, post_id: str, page: int = Query(1, ge=1, le=100000)):
    db = get_db()
    try:
        post = _public_post(db, post_id)
        comment_count = db["post_comments"].count_where("post_id = ?", [post_id])
        comments = list(db.query("""
            SELECT c.*, u.username AS author_name FROM post_comments c
            LEFT JOIN users u ON c.owner_user_id = u.id WHERE c.post_id = ?
            ORDER BY c.created_at, c.id LIMIT 50 OFFSET ?
        """, [post_id, (page - 1) * 50]))
    finally:
        db.conn.close()
    from app.resumes import get_resume, match_resume
    user = get_current_user(request.cookies.get("session_token"))
    resume = get_resume(user["id"], active_only=True) if user else None
    if user and post.get("post_status") == "published" and post.get("post_kind") == "recruitment":
        record_user_behavior(user["id"], "click", post_id, "opportunity")
    if resume and post.get("post_kind") == "recruitment":
        post["resume_match"] = match_resume(post, resume)
    return templates.TemplateResponse("post_detail.html", _context(
        request, post=post, comments=comments, comment_count=comment_count,
        page=page, total_pages=(comment_count + 49) // 50,
    ))


@router.post("/api/posts/{post_id}/comments", status_code=201)
def add_comment(request: Request, post_id: str, payload: CommentInput):
    user = require_user(request)
    require_csrf(request)
    enforce_rate_limit(request, "comment_create", 60, 3600, user["id"])
    db = get_db()
    try:
        _public_post(db, post_id)
        comment = {"post_id": post_id, "owner_user_id": user["id"], "body": payload.body,
                   "created_at": datetime.now(timezone.utc).isoformat()}
        result = db["post_comments"].insert(comment)
        count = db["post_comments"].count_where("post_id = ?", [post_id])
        return {"comment": {**comment, "id": result.last_pk}, "page": (count + 49) // 50}
    finally:
        db.conn.close()


@router.delete("/api/posts/{post_id}/comments/{comment_id}")
def delete_comment(request: Request, post_id: str, comment_id: int):
    user = require_user(request)
    require_csrf(request)
    db = get_db()
    try:
        result = db.execute("DELETE FROM post_comments WHERE id = ? AND post_id = ? AND owner_user_id = ?",
                            [comment_id, post_id, user["id"]])
        db.conn.commit()
        if not result.rowcount:
            raise HTTPException(status_code=404, detail="评论不存在或无权删除")
        return {"success": True}
    finally:
        db.conn.close()
