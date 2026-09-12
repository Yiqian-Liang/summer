"""
Main FastAPI application for the Summer Research Platform.
Provides endpoints for browsing, searching, and managing research opportunities.
"""

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, Query, Body, HTTPException, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
import uvicorn
from typing import Optional, Dict, List
from collections import Counter
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, StrictBool
from urllib.parse import urlencode, urlsplit
import os
import datetime
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from app.scrapers import AcademicScraper
from app.matching import matcher
from app.agents import ResearchAgent, extract_email, generate_email_search_url, generate_homepage_search_url
from app.rag import RAGWorkbench
from app.database import (
    DB_NAME, init_db, get_db, 
    get_opportunities, search_opportunities_fts, 
    get_opportunity_by_id, get_opportunity_count,
    upsert_opportunity,
    # User management
    init_user_tables, create_user, get_user_by_email, get_user_by_username,
    get_user_by_id, update_user_last_login, save_user_profile, get_user_profile,
    record_user_behavior, save_opportunity as db_save_opportunity,
    unsave_opportunity, get_user_saved, is_opportunity_saved, get_user_saved_entry,
    update_saved_progress, import_saved_opportunity, get_saved_application,
    # Password reset
    create_password_reset_token, verify_reset_token, use_reset_token, update_user_password
)
from app.auth import (
    hash_password, verify_password, create_session, get_session, 
    destroy_session, get_current_user, validate_email, validate_username,
    validate_password, get_onboarding_options
)
from app.auth import require_user, require_csrf, require_admin_token, csrf_token_for, revoke_user_sessions
from app.rate_limits import enforce_rate_limit, init_rate_limit_table
from app.recommender import recommender
from app.i18n import I18n, LANGUAGES, DEFAULT_LANGUAGE
from app.search_utils import (
    SOURCE_ALIASES,
    build_external_search_links,
    canonicalize_source_url,
    detect_source_type,
    normalize_source_filter,
)
from app.source_preview import fetch_source_preview
from app.mail_routes import router as mail_router
from app.mail_service import init_mail_tables
from app.mail_oauth import router as mail_oauth_router
from app.posts import router as posts_router, init_post_tables
from app.resumes import router as resume_router, init_resume_table, get_resume, rank_by_resume, match_resume
from app.database import get_recommendation_state, update_recommendation_feedback
from app.search_ranking import _parse_date

app = FastAPI(title="Summer Research Platform")
app.include_router(mail_router)
app.include_router(mail_oauth_router)
app.include_router(posts_router)
app.include_router(resume_router)


@app.middleware("http")
async def private_workspace_headers(request: Request, call_next):
    request.state.csrf_token = csrf_token_for(request)
    response = await call_next(request)
    if request.cookies.get("session_token") or request.url.path in {"/mail", "/resume", "/saved", "/applications", "/api/generate-rag-draft", "/posts/mine", "/posts/new"} or request.url.path.startswith(("/api/mail", "/api/resume", "/api/saved", "/api/applications", "/applications/")) or request.url.path.endswith("/edit"):
        response.headers["Cache-Control"] = "no-store"
    return response

# Cookie names
SESSION_COOKIE = "session_token"
LANG_COOKIE = "lang"

SOURCE_COUNT_LABELS = [
    ("university", "University/Lab"),
    ("community", "站内招募"),
    ("professor_site", "Professor forms"),
    ("nsf", "NSF REU"),
    ("pathways", "Pathways"),
    ("academicjobs", "AcademicJobsOnline"),
    ("euraxess", "Euraxess"),
    ("findaphd", "FindAPhD"),
    ("nature_careers", "Nature Careers"),
    ("jobs_ac_uk", "jobs.ac.uk"),
    ("mitacs", "Mitacs"),
    ("daad", "DAAD"),
    ("cnrs", "CNRS"),
    ("inria", "INRIA"),
    ("max_planck", "Max Planck"),
    ("linkedin", "LinkedIn"),
    ("twitter", "X/Twitter"),
    ("xiaohongshu", "小红书"),
    ("wechat", "微信"),
    ("zhihu", "知乎"),
    ("bilibili", "Bilibili"),
    ("weibo", "微博"),
    ("tieba", "贴吧"),
    ("discord", "Discord"),
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("tiktok", "TikTok"),
    ("scholar", "Scholar"),
    ("other", "Other"),
]


def validated_source(source: Optional[str]) -> Optional[str]:
    source = normalize_source_filter(source)
    if source and source not in set(SOURCE_ALIASES.values()) | {"other"}:
        raise HTTPException(status_code=422, detail="Unknown search source")
    return source


SAVED_PROGRESS_STAGES = ("preparing", "contacted", "applied", "replied", "interviewing", "completed")


def _safe_int(value, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _calendar_date(value) -> Optional[datetime.date]:
    parsed = _parse_date(value)
    if not parsed:
        return None
    text = str(value).strip()
    if len(text) >= 10 and (len(text) == 10 or text[10] in "T "):
        try:
            return datetime.date.fromisoformat(text[:10])
        except ValueError:
            pass
    return parsed.date()


def _deadline_days_left(deadline: Optional[str]) -> Optional[int]:
    deadline_date = _calendar_date(deadline)
    if not deadline_date:
        return None
    return (deadline_date - datetime.datetime.utcnow().date()).days


def _attach_saved_progress(opp: Dict, saved_item: Dict) -> None:
    opp["notes"] = saved_item.get("notes") or ""
    opp["progress_stage"] = saved_item.get("progress_stage") or "preparing"
    opp["progress_notes"] = saved_item.get("progress_notes") or ""
    opp["reminder_days"] = _safe_int(saved_item.get("reminder_days"), 7)
    opp["progress_updated_at"] = saved_item.get("progress_updated_at")
    opp["application_method"] = saved_item.get("application_method") or "email"
    for field in ("next_action_at", "contacted_at", "applied_at", "replied_at", "outcome", "outcome_at",
                  "custom_title", "custom_source_url", "custom_summary"):
        opp[field] = saved_item.get(field)

    deadline_days = _deadline_days_left(opp.get("deadline", ""))
    opp["deadline_days_left"] = deadline_days
    opp["reminder_due"] = bool(deadline_days is not None and deadline_days <= opp["reminder_days"])
    if deadline_days is not None and deadline_days < 0:
        opp["deadline_status"] = "overdue"
    elif opp["reminder_due"]:
        opp["deadline_status"] = "soon"
    else:
        opp["deadline_status"] = "ok"


def _load_applications(user_id: int, resume: Optional[Dict] = None) -> List[Dict]:
    applications = []
    for saved in get_user_saved(user_id):
        item = get_saved_application(user_id, saved["opportunity_id"])
        if not item:
            continue
        _attach_saved_progress(item, saved)
        try:
            item["hiring_signals_list"] = json.loads(item.get("hiring_signals") or "[]")
        except (TypeError, ValueError):
            item["hiring_signals_list"] = []
        item["professor_email"] = item.get("contact_email") or extract_email(item.get("description", ""))
        item["verification_fields"] = [
            field for field in ("deadline", "stipend", "requirements") if not item.get(field)
        ]
        item["verification_pending"] = bool(item.get("is_external") or item["verification_fields"])
        item["missing_resume"] = resume is None
        try:
            item["research_focus"] = research_agent.analyze_match("", item).get("research_focus", "")
        except Exception:
            item["research_focus"] = ""
        if resume:
            item["resume_match"] = match_resume(item, resume)
        applications.append(item)
    return applications


def _application_task_reasons(application: Dict) -> List[str]:
    if application.get("progress_stage") == "completed" or application.get("outcome"):
        return []
    reasons = []
    next_action = application.get("next_action_at")
    if next_action:
        try:
            parsed = datetime.datetime.fromisoformat(str(next_action).replace("Z", "+00:00"))
            if parsed.date() <= datetime.datetime.now().date():
                reasons.append("next_action")
        except ValueError:
            pass
    deadline_days = application.get("deadline_days_left")
    if deadline_days is not None and deadline_days <= application.get("reminder_days", 7):
        reasons.append("deadline")
    if application.get("is_external") and application.get("progress_stage") == "preparing":
        reasons.append("verify_source")
    if application.get("missing_resume") and application.get("progress_stage") == "preparing":
        reasons.append("missing_resume")
    return reasons


def _group_applications(applications: List[Dict], i18n: I18n) -> tuple[List[Dict], List[Dict]]:
    today_tasks = []
    remaining = []
    for item in applications:
        item["task_reasons"] = _application_task_reasons(item)
        (today_tasks if item["task_reasons"] else remaining).append(item)
    definitions = [
        ("today", "今天要处理" if i18n.is_zh() else "Due today", today_tasks),
        ("preparing", "准备中" if i18n.is_zh() else "Preparing", []),
        ("contacted", "已联系" if i18n.is_zh() else "Contacted", []),
        ("applied", "已申请" if i18n.is_zh() else "Applied", []),
        ("replied", "有回复" if i18n.is_zh() else "Replies", []),
        ("completed", "已结束" if i18n.is_zh() else "Completed", []),
    ]
    groups = {key: items for key, _, items in definitions}
    for item in remaining:
        stage = item.get("progress_stage") or "preparing"
        key = "replied" if stage == "interviewing" else stage
        groups.get(key, groups["preparing"]).append(item)
    return [{"key": key, "label": label, "applications": items} for key, label, items in definitions], today_tasks


def _sanitize_prefill_value(value, max_length: int) -> str:
    if not value:
        return ""
    normalized = str(value).replace("\r", " ").replace("\n", " ")
    return (normalized[:max_length]).strip()


def build_community_post_url(item: Dict) -> str:
    if item.get("provider") == "user_post":
        return ""
    description = _sanitize_prefill_value(item.get("description", ""), 1200)
    source_url = _sanitize_prefill_value(item.get("source_url", ""), 260)
    if source_url:
        description = f"{description} [source: {source_url}]".strip()
    payload = {
        "post_kind": "recruitment",
        "title": _sanitize_prefill_value(item.get("title", ""), 180),
        "university": _sanitize_prefill_value(item.get("university", ""), 160),
        "professor_name": _sanitize_prefill_value(item.get("professor_name", ""), 120),
        "description": description,
        "contact_email": _sanitize_prefill_value(item.get("contact_email") or extract_email(item.get("description", "")) or "", 254),
        "direct_apply_link": _sanitize_prefill_value(item.get("direct_apply_link", ""), 2048),
        "deadline": _sanitize_prefill_value(item.get("deadline", ""), 10),
    }
    payload = {key: value for key, value in payload.items() if value}
    if not payload.get("title"):
        return ""
    return "/posts/new?" + urlencode(payload)


def sync_live_opportunities(query: str, source: Optional[str], refresh: bool) -> Dict:
    """Collect and ingest once; the page and API share the same failure handling."""
    from app.scheduler import OpportunityProcessor

    report = scraper_engine.search_all_sources(
        query, True, source, refresh, return_status=True,
    )
    items = report.pop("results")
    processor = OpportunityProcessor()
    report["synced_count"] = 0
    rejected = 0
    for item in items:
        try:
            source_type = detect_source_type(
                item.get("source_type") or item.get("source", ""),
                item.get("url") or item.get("recent_paper") or item.get("link") or "",
            )
            if source == "professor_site" and source_type in {"university", "other", "professor_site"}:
                source_type = "professor_site"
            if source and source_type != source:
                continue
            normalized = processor.normalize_item(item, source_type)
            upsert_opportunity(normalized)
            report["synced_count"] += 1
        except Exception:
            rejected += 1
            logger.warning("Skipped an invalid live search result")
    if rejected:
        report["status"] = "partial" if report["synced_count"] else "error"
    return report


async def run_opportunity_search(
    query: str, source: Optional[str], page: int = 1, limit: int = 20,
    refresh: bool = False, live: bool = True,
    resume: Optional[Dict] = None,
    request: Optional[Request] = None, user_id: Optional[int] = None,
) -> Dict:
    """Refresh, verify, rank, and finally paginate for both search entry points."""
    enabled = bool(query) and live and scraper_engine.has_live_search_credentials(query, source)
    report = {
        "status": "local" if enabled else "unavailable", "cached": False,
        "providers": scraper_engine.live_search_providers(query, source) if enabled else [],
        "failed_sources": [], "synced_count": 0,
    }
    if enabled and page == 1 and request:
        enforce_rate_limit(request, "live_search", 30, 3600, user_id)
        if refresh:
            enforce_rate_limit(request, "live_search_refresh", 5, 3600, user_id)
    if enabled and page == 1:
        try:
            report = await run_in_threadpool(sync_live_opportunities, query, source, refresh)
        except Exception:
            logger.warning("Live search unavailable; keeping local results")
            report["status"] = "error"

    if not query and resume:
        available = await run_in_threadpool(get_opportunities, limit=get_opportunity_count(), offset=0)
        all_results = await run_in_threadpool(rank_by_resume, available, resume, browse=True)
        candidates = all_results if not source else await run_in_threadpool(rank_by_resume, [row for row in available if row.get("source_type") == source], resume, browse=True, source=source)
    else:
        candidates = await run_in_threadpool(search_opportunities_fts, query, None, source)
        all_results = candidates if not source else await run_in_threadpool(search_opportunities_fts, query, None)
    source_counter = Counter(
        source_key for row in all_results
        for source_key in (row.get("matched_sources") or [row.get("source_type") or "other"])
    )
    if source:
        source_counter[source] = len(candidates)
    # ponytail: semantic reranking is capped at 200; all remaining verified
    # matches remain pageable. Increase the cap only after measuring model cost.
    head = [dict(row, text_for_matching=" ".join(
        str(row.get(field) or "") for field in ("title", "description", "professor_name")
    )) for row in candidates[:200]]
    if query:
        try:
            head = await run_in_threadpool(matcher.rank_candidates, query, head)
        except Exception:
            logger.warning("Semantic ranking unavailable; keeping evidence-based order")
    ranked = head + candidates[200:]
    if resume:
        ranked = await run_in_threadpool(rank_by_resume, ranked, resume)
    offset = (page - 1) * limit
    results = ranked[offset:offset + limit]
    for result in results:
        result.pop("text_for_matching", None)
    return {
        "results": results,
        "total_count": len(ranked),
        "source_hit_total": len(all_results),
        "source_hit_counts": [
            {"source": key, "label": label, "count": source_counter.get(key, 0)}
            for key, label in SOURCE_COUNT_LABELS
        ],
        "live_search_enabled": enabled,
        "live_search_attempted": enabled and page == 1,
        "live_result_count": report["synced_count"],
        "live_search_providers": report["providers"],
        "live_search": report,
    }


def get_current_user_from_request(request: Request) -> Optional[Dict]:
    """Extract current user from request cookies."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return get_current_user(token)
    return None


def get_lang_from_request(request: Request) -> str:
    """Get language preference from request (cookie or query param)."""
    # First check query param
    lang = request.query_params.get("lang")
    if lang and lang in LANGUAGES:
        return lang
    # Then check cookie
    lang = request.cookies.get(LANG_COOKIE)
    if lang and lang in LANGUAGES:
        return lang
    # Default
    return DEFAULT_LANGUAGE


def get_i18n(request: Request) -> I18n:
    """Get i18n helper for templates."""
    return I18n(get_lang_from_request(request))


def local_redirect(value: str) -> str:
    """Login destinations must remain in this application."""
    if value and value.startswith("/") and not value.startswith("//") and not any(char in value for char in ("\\", "\r", "\n")):
        return value
    return "/"

# Create static directory if not exists
os.makedirs("app/static", exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Initialize components
scraper_engine = AcademicScraper()
research_agent = ResearchAgent()
rag_workbench = RAGWorkbench()


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    init_db()
    init_user_tables()
    init_mail_tables()
    init_post_tables()
    init_resume_table()
    init_rate_limit_table()
    media_data_path = os.getenv("MEDIACRAWLER_DATA_PATH", "").strip()
    if media_data_path:
        try:
            from app.mediacrawler_import import import_mediacrawler_path
            stats = await run_in_threadpool(import_mediacrawler_path, media_data_path)
            logger.info("MediaCrawler startup import: %s", stats)
        except Exception as error:
            logger.error("MediaCrawler startup import failed: %s", error)


# ============== Language Routes ==============

@app.get("/set-lang/{lang}")
async def set_language(request: Request, lang: str):
    """Set language preference and redirect back."""
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE
    
    # Get the referer to redirect back
    from urllib.parse import urlsplit
    referer_url = urlsplit(request.headers.get("referer", "/"))
    referer = local_redirect(referer_url.path + ("?" + referer_url.query if referer_url.query else ""))
    
    response = RedirectResponse(url=referer, status_code=302)
    response.set_cookie(
        key=LANG_COOKIE,
        value=lang,
        max_age=60 * 60 * 24 * 365,  # 1 year
        httponly=True,
        samesite="lax"
    )
    return response


# ============== Auth Routes ==============

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    """Login page."""
    user = get_current_user_from_request(request)
    if user:
        return RedirectResponse(url=local_redirect(next), status_code=302)
    i18n = get_i18n(request)
    return templates.TemplateResponse("auth/login.html", {
        "request": request,
        "next": next,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.post("/login")
async def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    """Handle login form submission."""
    i18n = get_i18n(request)
    # Find user
    user = get_user_by_email(email)
    if not user:
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": i18n.t("auth.error_email_password"),
            "next": next,
            "i18n": i18n,
            "languages": LANGUAGES,
        })
    
    # Verify password
    if not verify_password(password, user["password_hash"]):
        return templates.TemplateResponse("auth/login.html", {
            "request": request,
            "error": i18n.t("auth.error_email_password"),
            "next": next,
            "i18n": i18n,
            "languages": LANGUAGES,
        })
    
    # Create session
    if not user["password_hash"].startswith("pbkdf2_sha256:"):
        update_user_password(user["id"], hash_password(password))
    token = create_session(user["id"], {
        "id": user["id"],
        "email": user["email"],
        "username": user["username"],
        "onboarding_complete": user["onboarding_complete"],
    })
    
    # Update last login
    update_user_last_login(user["id"])
    
    # Set cookie and redirect
    redirect_url = "/onboarding" if not user["onboarding_complete"] else local_redirect(next)
    redirect_response = RedirectResponse(url=redirect_url, status_code=302)
    redirect_response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 7,  # 7 days
        samesite="lax"
    )
    return redirect_response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    """Registration page."""
    user = get_current_user_from_request(request)
    if user:
        return RedirectResponse(url="/", status_code=302)
    i18n = get_i18n(request)
    return templates.TemplateResponse("auth/register.html", {
        "request": request,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.post("/register")
async def register_submit(
    request: Request,
    email: str = Form(...),
    username: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Handle registration form submission."""
    enforce_rate_limit(request, "register", 20, 3600)
    errors = []
    
    # Validate email
    if not validate_email(email):
        errors.append("邮箱格式不正确")
    elif get_user_by_email(email):
        errors.append("该邮箱已被注册")
    
    # Validate username
    valid, msg = validate_username(username)
    if not valid:
        errors.append(msg)
    elif get_user_by_username(username):
        errors.append("该用户名已被使用")
    
    # Validate password
    valid, msg = validate_password(password)
    if not valid:
        errors.append(msg)
    
    # Check password match
    if password != password_confirm:
        errors.append("两次输入的密码不一致")
    
    i18n = get_i18n(request)
    if errors:
        return templates.TemplateResponse("auth/register.html", {
            "request": request,
            "errors": errors,
            "email": email,
            "username": username,
            "i18n": i18n,
            "languages": LANGUAGES,
        })
    
    # Create user
    password_hash = hash_password(password)
    user_id = create_user(email, username, password_hash)
    
    if not user_id:
        return templates.TemplateResponse("auth/register.html", {
            "request": request,
            "errors": [i18n.t("common.error")],
            "email": email,
            "username": username,
            "i18n": i18n,
            "languages": LANGUAGES,
        })
    
    # Create session and redirect to onboarding
    token = create_session(user_id, {
        "id": user_id,
        "email": email,
        "username": username,
        "onboarding_complete": 0,
    })
    
    redirect_response = RedirectResponse(url="/onboarding", status_code=302)
    redirect_response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=request.url.scheme == "https",
        max_age=60 * 60 * 24 * 7,
        samesite="lax"
    )
    return redirect_response


@app.get("/logout")
async def logout(request: Request):
    """Logout and clear session."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        destroy_session(token)
    
    response = RedirectResponse(url="/", status_code=302)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Forgot password page."""
    i18n = get_i18n(request)
    return templates.TemplateResponse("auth/forgot_password.html", {
        "request": request,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.post("/forgot-password")
async def forgot_password_submit(request: Request, email: str = Form(...)):
    """Handle forgot password form submission."""
    i18n = get_i18n(request)
    if os.getenv("ENABLE_DEV_PASSWORD_RESET", "0") != "1":
        return templates.TemplateResponse("auth/forgot_password.html", {
            "request": request, "i18n": i18n, "languages": LANGUAGES,
            "error": "网站尚未配置账号恢复邮件，请联系管理员协助恢复。" if i18n.is_zh() else "Account recovery email is not configured. Contact the site administrator.",
        }, status_code=503)
    user = get_user_by_email(email)

    # Always show success message to prevent email enumeration
    success_msg = "开发模式：如果账号存在，重置链接已输出到本机服务控制台；未发送邮件。" if i18n.is_zh() else "Development mode: if the account exists, a reset link is in the local server console. No email was sent."

    if user:
        # Generate reset token
        token = create_password_reset_token(user["id"])
        reset_link = f"http://localhost:8000/reset-password?token={token}"

        # In production, send email here
        # For now, print to console (development mode)
        print(f"\n{'='*50}")
        print(f"PASSWORD RESET LINK for {email}:")
        print(f"{reset_link}")
        print(f"{'='*50}\n")
        logger.info(f"Password reset requested for {email}. Token generated.")

    return templates.TemplateResponse("auth/forgot_password.html", {
        "request": request,
        "i18n": i18n,
        "languages": LANGUAGES,
        "success": success_msg,
    })


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = Query(...)):
    """Reset password page."""
    i18n = get_i18n(request)

    # Verify token
    user_id = verify_reset_token(token)
    if not user_id:
        return templates.TemplateResponse("auth/reset_password.html", {
            "request": request,
            "i18n": i18n,
            "languages": LANGUAGES,
            "error": i18n.t("auth.invalid_reset_link") if i18n.is_zh() else "Invalid or expired reset link.",
            "token": None,
        })

    return templates.TemplateResponse("auth/reset_password.html", {
        "request": request,
        "i18n": i18n,
        "languages": LANGUAGES,
        "token": token,
    })


@app.post("/reset-password")
async def reset_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Handle reset password form submission."""
    i18n = get_i18n(request)

    # Verify token
    user_id = verify_reset_token(token)
    if not user_id:
        return templates.TemplateResponse("auth/reset_password.html", {
            "request": request,
            "i18n": i18n,
            "languages": LANGUAGES,
            "error": i18n.t("auth.invalid_reset_link") if i18n.is_zh() else "Invalid or expired reset link.",
            "token": None,
        })

    # Validate password
    valid, msg = validate_password(password)
    if not valid:
        return templates.TemplateResponse("auth/reset_password.html", {
            "request": request,
            "i18n": i18n,
            "languages": LANGUAGES,
            "error": msg,
            "token": token,
        })

    if password != password_confirm:
        error_msg = "两次输入的密码不一致" if i18n.is_zh() else "Passwords do not match"
        return templates.TemplateResponse("auth/reset_password.html", {
            "request": request,
            "i18n": i18n,
            "languages": LANGUAGES,
            "error": error_msg,
            "token": token,
        })

    # Update password
    password_hash = hash_password(password)
    update_user_password(user_id, password_hash)
    use_reset_token(token)
    revoke_user_sessions(user_id)

    # Redirect to login with success message
    return RedirectResponse(url="/login?reset=success", status_code=302)


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    """Onboarding page for new users to select interests."""
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login?next=/onboarding", status_code=302)
    
    # Get existing profile if any
    profile = get_user_profile(user["id"])
    if profile:
        for field in ("target_types", "majors", "research_fields", "region_preferences"):
            try:
                value = json.loads(profile.get(field) or "[]")
                profile[field] = value if isinstance(value, list) else []
            except (TypeError, ValueError):
                profile[field] = []
    options = get_onboarding_options()
    i18n = get_i18n(request)
    
    return templates.TemplateResponse("auth/onboarding.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "options": options,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.post("/onboarding")
async def onboarding_submit(request: Request):
    """Handle onboarding form submission."""
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    
    form = await request.form()
    
    # Parse form data
    profile = {
        "education_level": form.get("education_level"),
        "school_tier": form.get("school_tier"),
        "grade": form.get("grade"),
        "target_types": json.dumps(form.getlist("target_types")),
        "majors": json.dumps(form.getlist("majors")),
        "research_fields": json.dumps(form.getlist("research_fields")),
        "region_preferences": json.dumps(form.getlist("region_preferences")),
    }
    
    # Save profile
    save_user_profile(user["id"], profile)
    
    # Update session
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = get_session(token)
        if session:
            session["user"]["onboarding_complete"] = 1
    
    return RedirectResponse(url="/", status_code=302)


# ============== Page Routes ==============

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, page: int = Query(1, ge=1), feed: Literal["personal", "latest"] = Query("personal")):
    """
    Home page with opportunity feed.
    Shows personalized opportunities based on user preferences.
    """
    user = get_current_user_from_request(request)
    resume = get_resume(user["id"], active_only=True) if user else None
    profile = None
    state = {"use_behavior": False, "behaviors": [], "saved": [], "hidden": []}
    recommended_searches = []
    today_tasks = []
    
    limit = 20
    offset = (page - 1) * limit
    
    # If user is logged in, get their profile for personalization
    if user:
        profile = get_user_profile(user["id"])
        state = get_recommendation_state(user["id"])
        
    total_count = get_opportunity_count()
    opportunities = get_opportunities(
        limit=total_count,
        offset=0,
        order_by="quality_score desc, updated_at desc, id",
    )
    ranked = await run_in_threadpool(recommender.rank_opportunities, opportunities, profile,
        state["behaviors"], resume, state["saved"], state["hidden"], feed == "personal")
    total_count = len(ranked)
    opportunities = ranked[offset:offset + limit]
    saved_ids = {item["opportunity_id"] for item in state["saved"]}
    if profile and feed == "personal":
        recommended_searches = recommender.get_recommended_searches(profile)
    
    # Parse hiring_signals JSON and generate tags for template
    for opp in opportunities:
        try:
            opp["hiring_signals_list"] = json.loads(opp.get("hiring_signals") or "[]")
        except:
            opp["hiring_signals_list"] = []
        
        # Generate tags and research focus
        try:
            analysis = research_agent.analyze_match("", opp)
            opp["tags"] = analysis.get("tags", [])
            opp["research_focus"] = analysis.get("research_focus", "")
        except:
            opp["tags"] = []
            opp["research_focus"] = ""

        # Extract email from description or generate search URL
        extracted_email = extract_email(opp.get("description", ""))
        opp["professor_email"] = extracted_email
        if not extracted_email and opp.get("professor_name"):
            opp["email_search_url"] = generate_email_search_url(
                opp.get("professor_name"),
                opp.get("university")
            )
            opp["homepage_search_url"] = generate_homepage_search_url(
                opp.get("professor_name"),
                opp.get("university")
            )

        # Check if saved by user
        opp["is_saved"] = opp["id"] in saved_ids
    
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    i18n = get_i18n(request)
    if user:
        _, today_tasks = _group_applications(_load_applications(user["id"], resume), i18n)
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "profile": profile,
        "has_resume": bool(resume),
        "feed": feed,
        "learning_enabled": state["use_behavior"],
        "hidden_count": len(state["hidden"]),
        "opportunities": opportunities,
        "recommended_searches": recommended_searches,
        "today_tasks": today_tasks[:5],
        "total_count": total_count,
        "current_page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request, 
    q: str = Query(None, max_length=300, description="Search query"),
    source: str = Query(None, description="Source filter"),
    page: int = Query(1, ge=1),
    refresh: bool = Query(False, description="Bypass the live-search cache"),
    sort: Literal["relevance", "resume"] = Query("relevance"),
):
    """
    Unified search page - aggregates results from all platforms,
    ranks them together by relevance (like a meta search engine).
    If no query provided, browses latest opportunities.
    """
    user = get_current_user_from_request(request)
    resume = get_resume(user["id"], active_only=True) if user else None
    if sort == "resume" and not resume:
        return RedirectResponse("/resume", status_code=302)
    
    limit = 20
    offset = (page - 1) * limit
    results = []
    total_count = 0
    source_hit_counts = []
    source_hit_total = 0

    q = (q or "").strip()
    filter_source = validated_source(source)
    source = filter_source
    search_shortcuts = build_external_search_links(q, filter_source)
    search_data = {}
    live_search_enabled = False if sort == "resume" and not q else scraper_engine.has_live_search_credentials(q, filter_source)
    live_search_attempted = False
    live_result_count = 0
    live_search_providers = scraper_engine.live_search_providers(q, filter_source) if live_search_enabled else []

    if q or sort == "resume":
        if user and q:
            record_user_behavior(user["id"], "search", q, "search")
        search_data = await run_opportunity_search(
            q, filter_source, page, limit, refresh,
            resume=resume if sort == "resume" else None,
            request=request, user_id=user["id"] if user else None,
        )
        results = search_data["results"]
        total_count = search_data["total_count"]
    else:
        # Browse Mode (No Query)
        # Fetch from DB based on filters
        results = get_opportunities(
            limit=limit,
            offset=offset,
            source_type=filter_source,
            order_by="updated_at desc"
        )

        if not results and filter_source and page == 1 and live_search_enabled:
            enforce_rate_limit(request, "live_search", 30, 3600, user["id"] if user else None)
            if refresh:
                enforce_rate_limit(request, "live_search_refresh", 5, 3600, user["id"] if user else None)
            default_q = "暑研" if filter_source in {"xiaohongshu", "wechat", "zhihu", "bilibili", "weibo", "tieba"} else "summer research"
            try:
                await run_in_threadpool(sync_live_opportunities, default_q, filter_source, refresh)
                results = get_opportunities(limit=limit, offset=offset, source_type=filter_source, order_by="updated_at desc")
            except Exception:
                logger.warning("Live source unavailable while browsing")

        total_count = get_opportunity_count(source_type=filter_source)
        
        for result in results:
            result["match_score"] = result.get("quality_score", 50)

    # Common Post-Processing for both Search and Browse
    for result in results:
        result["community_post_url"] = build_community_post_url(result)
        try:
            analysis = research_agent.analyze_match(q, result)
            result["tags"] = analysis.get("tags", [])
            result["research_focus"] = analysis.get("research_focus", "")
        except Exception:
            result["tags"] = []
            result["research_focus"] = ""
        # Extract email from description or generate search URL
        extracted_email = extract_email(result.get("description", ""))
        result["professor_email"] = extracted_email
        if not extracted_email and result.get("professor_name"):
            result["email_search_url"] = generate_email_search_url(
                result.get("professor_name"),
                result.get("university")
            )
            result["homepage_search_url"] = generate_homepage_search_url(
                result.get("professor_name"),
                result.get("university")
            )

        # Parse hiring_signals JSON and check saved status
        try:
            result["hiring_signals_list"] = json.loads(result.get("hiring_signals") or "[]")
        except:
            result["hiring_signals_list"] = []
        
        # Check if saved by user
        if user:
            result["is_saved"] = is_opportunity_saved(user["id"], result["id"])
        else:
            result["is_saved"] = False
    
    community_related = []
    if q and source != "community" and sort != "resume":
        try:
            community_related = await run_in_threadpool(search_opportunities_fts, q, 3, "community")
            for post in community_related:
                post["community_post_url"] = build_community_post_url(post)
        except Exception:
            logger.warning("Failed to load community-related opportunities")

    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    i18n = get_i18n(request)
    
    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "user": user,
        "query": q or "",
        "source": source,
        "sort": sort,
        "has_resume": bool(resume),
        "results": results,
        "total_count": total_count,
        "current_page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "search_shortcuts": search_shortcuts,
        "source_hit_counts": source_hit_counts,
        "source_hit_total": source_hit_total,
        "live_search_enabled": live_search_enabled,
        "live_search_attempted": live_search_attempted,
        "live_result_count": live_result_count,
        "live_search_providers": live_search_providers,
        "community_related": community_related,
        "live_search": {"status": "local"},
        **{key: value for key, value in search_data.items() if key != "results"},
        "i18n": i18n,
        "languages": LANGUAGES,
    })


@app.get("/opportunity/{opp_id}", response_class=HTMLResponse)
async def opportunity_detail(request: Request, opp_id: str):
    """
    Detail page for a single opportunity.
    """
    user = get_current_user_from_request(request)
    opportunity = get_opportunity_by_id(opp_id)
    
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if opportunity.get("provider") == "user_post":
        return RedirectResponse(f"/posts/{opp_id}", status_code=302)
    resume = get_resume(user["id"], active_only=True) if user else None
    if resume:
        opportunity["resume_match"] = match_resume(opportunity, resume)

    if opportunity.get("provider") != "user_post":
        opportunity["community_post_url"] = build_community_post_url(opportunity)
    
    # Track click behavior
    if user:
        record_user_behavior(user["id"], "click", opp_id, "opportunity")
    
    # Parse hiring signals
    try:
        opportunity["hiring_signals_list"] = json.loads(opportunity.get("hiring_signals") or "[]")
    except:
        opportunity["hiring_signals_list"] = []
    
    # Check if saved
    if user:
        opportunity["is_saved"] = is_opportunity_saved(user["id"], opp_id)
    else:
        opportunity["is_saved"] = False
    
    i18n = get_i18n(request)
    return templates.TemplateResponse("detail.html", {
        "request": request,
        "user": user,
        "opportunity": opportunity,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


# ============== API Routes ==============

@app.get("/api/opportunities")
async def api_get_opportunities(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    source_type: str = Query(None),
    min_quality: int = Query(0, ge=0, le=100),
):
    """
    API endpoint to get opportunities with filters.
    """
    opportunities = get_opportunities(
        limit=limit,
        offset=offset,
        source_type=source_type,
        min_quality=min_quality,
    )
    
    return JSONResponse({
        "opportunities": opportunities,
        "count": len(opportunities),
        "total": get_opportunity_count(source_type=source_type, min_quality=min_quality),
    })


@app.get("/api/search")
async def api_search(
    request: Request,
    q: str = Query("", max_length=300, description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    source: str = Query(None, description="Source filter"),
    page: int = Query(1, ge=1),
    refresh: bool = Query(False),
    live: bool = Query(True, description="Supplement the first page with live sources"),
    sort: Literal["relevance", "resume"] = Query("relevance"),
):
    """The same verified, paginated, multi-platform search used by the website."""
    q = q.strip()
    if not q and sort != "resume":
        raise HTTPException(status_code=422, detail="Search query must not be blank")
    filter_source = validated_source(source)
    resume = get_resume(require_user(request)["id"], active_only=True) if sort == "resume" else None
    if sort == "resume" and not resume:
        raise HTTPException(422, detail="请先上传并确认简历，启用个人匹配。")
    user = get_current_user_from_request(request)
    data = await run_opportunity_search(
        q, filter_source, page, limit, refresh, live, resume,
        request=request, user_id=user["id"] if user else None,
    )
    return JSONResponse({
        "query": q, "results": data["results"], "count": len(data["results"]),
        "total": data["total_count"], "page": page,
        "total_pages": (data["total_count"] + limit - 1) // limit,
        "source": filter_source, "source_counts": data["source_hit_counts"],
        "sort": sort,
        "live_search": data["live_search"],
    })


class DraftRequest(BaseModel):
    opportunity_id: str = Field(min_length=1, max_length=100)
    student_name: str = Field(default="", max_length=100)
    school: str = Field(default="", max_length=200)
    interest: str = Field(default="", max_length=500)
    resume_text: str = Field(default="", max_length=6000)
    focus_area: str = Field(default="", max_length=500)
    tone: Literal["professional", "enthusiastic", "concise"] = "professional"
    language: Literal["zh", "en"] = "en"
    use_ai: StrictBool = False
    include_social_media: StrictBool = False


def _mail_opportunity(user_id: int, opportunity_id: str) -> Optional[Dict]:
    return get_opportunity_by_id(opportunity_id) or get_saved_application(user_id, opportunity_id)


@app.get("/mail", response_class=HTMLResponse)
async def mail_page(request: Request, opportunity: str = Query(None, max_length=100), draft: int = Query(None, ge=1)):
    user = get_current_user_from_request(request)
    if not user:
        from urllib.parse import quote
        return RedirectResponse("/login?next=" + quote(str(request.url.path) + ("?" + request.url.query if request.url.query else ""), safe=""), status_code=302)
    item = _mail_opportunity(user["id"], opportunity) if opportunity else None
    if opportunity and not item:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if item:
        item["professor_email"] = item.get("contact_email") or extract_email(item.get("description", "")) or ""
    return templates.TemplateResponse("mail.html", {
        "request": request, "user": user, "opportunity": item,
        "draft_id": draft, "capabilities": rag_workbench.drafting_capabilities(),
        "csrf_token": csrf_token_for(request), "i18n": get_i18n(request), "languages": LANGUAGES,
    })


@app.get("/api/mail/capabilities")
async def mail_capabilities(request: Request):
    require_user(request)
    return rag_workbench.drafting_capabilities()


@app.get("/api/mail/context/{opportunity_id}")
async def mail_context(opportunity_id: str, request: Request):
    user = require_user(request)
    item = _mail_opportunity(user["id"], opportunity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    item["professor_email"] = item.get("contact_email") or extract_email(item.get("description", "")) or ""
    return {"opportunity": item}


@app.post("/api/mail/generate")
@app.post("/api/generate-rag-draft")
async def generate_rag_draft(request: Request, payload: DraftRequest):
    user = require_csrf(request)
    opportunity = _mail_opportunity(user["id"], payload.opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    if payload.use_ai:
        enforce_rate_limit(request, "mail_ai", 20, 3600, user["id"])
    draft_data = await run_in_threadpool(
        rag_workbench.generate_context_aware_draft,
        {"name": payload.student_name.strip() or user["username"], "school": payload.school,
         "interest": payload.interest},
        {"name": opportunity.get("professor_name") or "", "title": opportunity["title"],
         "snippet": opportunity.get("description") or "", "source_url": opportunity.get("source_url") or ""},
        include_social_media=payload.include_social_media,
        resume_text=payload.resume_text, tone=payload.tone, focus_area=payload.focus_area,
        use_ai=payload.use_ai, language=payload.language,
    )
    return {"draft": draft_data, "recipient": opportunity.get("contact_email") or extract_email(opportunity.get("description", "")) or ""}


@app.post("/api/collect")
async def trigger_collection(
    request: Request,
    query: str = Query(None, max_length=300, description="Optional specific query to collect")
):
    """
    Manually trigger a data collection job.
    Useful for testing or on-demand updates.
    """
    require_admin_token(request)
    from app.scheduler import DataCollectionScheduler
    
    scheduler = DataCollectionScheduler()
    
    if query:
        stats = await run_in_threadpool(scheduler.run_collection_job, query)
    else:
        # Run with just a few default queries for quick update
        scheduler.queries = scheduler.queries[:3]
        stats = await run_in_threadpool(scheduler.run_collection_job)
    
    return JSONResponse(stats)


class MatchAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(default="", max_length=300)
    opportunity_id: str = Field(min_length=1, max_length=100)


@app.post("/api/analyze-match")
async def analyze_match_api(request: Request, payload: MatchAnalysisRequest):
    """Return local evidence, optionally enhanced when explicitly configured."""
    user = require_csrf(request)
    enforce_rate_limit(request, "match_analysis", 30, 3600, user["id"])
    opportunity = get_opportunity_by_id(payload.opportunity_id) or get_saved_application(user["id"], payload.opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    use_llm = bool(os.getenv("GEMINI_API_KEY")) and os.getenv("ENABLE_MATCH_AI", "0").strip().lower() in {"1", "true", "yes", "on"}
    analysis = await run_in_threadpool(research_agent.analyze_match, payload.query, opportunity, use_llm=use_llm)
    return JSONResponse(analysis)


@app.get("/api/stats")
async def get_stats():
    """
    Get platform statistics.
    """
    db = get_db()
    
    total_opportunities = get_opportunity_count()
    
    # Count by source
    source_counts = {}
    try:
        for row in db.execute("""
            SELECT source_type, COUNT(*) as cnt
            FROM opportunities
            WHERE (source_url LIKE 'http://%' OR source_url LIKE 'https://%')
              AND (coalesce(provider, '') != 'user_post' OR (post_status = 'published' AND coalesce(post_kind, 'recruitment') = 'recruitment'))
            GROUP BY source_type
        """).fetchall():
            source_counts[row[0]] = row[1]
    except:
        pass
    
    return JSONResponse({
        "total_opportunities": total_opportunities,
        "by_source": source_counts,
        "last_updated": datetime.datetime.now().isoformat(),
    })


@app.get("/api/source-preview/{opp_id}")
async def source_preview_api(opp_id: str):
    """
    Fetch a short, public excerpt from an opportunity source URL.
    Does not generate content and does not mirror full posts/pages.
    """
    opportunity = get_opportunity_by_id(opp_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    preview = await run_in_threadpool(fetch_source_preview, opportunity.get("source_url", ""))
    return JSONResponse(preview)


# ============== User API Routes ==============

class RecommendationFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["hide", "unhide", "restore", "set_learning", "clear_history"]
    opportunity_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    enabled: Optional[StrictBool] = None


class SavedProgressUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress_stage: Optional[Literal["preparing", "contacted", "applied", "replied", "interviewing", "completed"]] = None
    reminder_days: Optional[int] = Field(default=None, ge=1, le=30)
    progress_notes: Optional[str] = Field(default=None, min_length=0, max_length=500)
    application_method: Optional[Literal["email", "form", "other"]] = None
    next_action_at: Optional[datetime.datetime] = None
    outcome: Optional[Literal["accepted", "rejected", "withdrawn", "no_response"]] = None


class SavedImportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=3000)


@app.post("/api/recommendations/feedback")
async def recommendation_feedback(request: Request, payload: RecommendationFeedback):
    user = require_csrf(request)
    if payload.action in {"hide", "unhide"}:
        if not payload.opportunity_id or payload.enabled is not None:
            raise HTTPException(422, "请选择要隐藏或恢复的机会。")
        if payload.action == "hide" and not get_opportunity_by_id(payload.opportunity_id):
            raise HTTPException(404, "机会已不存在或尚未公开。")
    elif payload.action == "set_learning":
        if payload.enabled is None or payload.opportunity_id is not None:
            raise HTTPException(422, "请选择是否使用浏览与搜索记录。")
    elif payload.opportunity_id is not None or payload.enabled is not None:
        raise HTTPException(422, "不支持的反馈参数。")
    update_recommendation_feedback(user["id"], payload.action, payload.opportunity_id, payload.enabled)
    state = get_recommendation_state(user["id"])
    return {"success": True, "use_behavior": state["use_behavior"], "hidden_count": len(state["hidden"])}


@app.post("/api/save/{opportunity_id}")
async def save_opportunity_api(request: Request, opportunity_id: str):
    """Save/bookmark an opportunity."""
    user = require_csrf(request)
    opportunity = get_opportunity_by_id(opportunity_id)
    if not opportunity:
        raise HTTPException(404, "机会已不存在或尚未公开。")
    method = "form" if opportunity.get("direct_apply_link") or opportunity.get("has_direct_apply") else "email"
    success = is_opportunity_saved(user["id"], opportunity_id) or db_save_opportunity(user["id"], opportunity_id, application_method=method)

    return JSONResponse({"success": success})


@app.post("/api/saved/import")
async def import_saved_opportunity_api(request: Request, payload: SavedImportInput):
    """Save a user-supplied link privately without fetching or indexing it."""
    user = require_csrf(request)
    if any(ord(char) < 32 or char.isspace() or char == "\\" for char in payload.source_url):
        raise HTTPException(422, "请填写完整、有效的 http(s) 链接。")
    source_url = canonicalize_source_url(payload.source_url)
    try:
        parsed = urlsplit(source_url)
        parsed.port
    except ValueError:
        raise HTTPException(422, "请填写完整、有效的 http(s) 链接。") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(422, "请填写完整、有效的 http(s) 链接。")
    saved, created = import_saved_opportunity(user["id"], source_url, payload.title, payload.summary)
    application = get_saved_application(user["id"], saved["opportunity_id"])
    if not application:
        raise HTTPException(500, "导入失败，请稍后重试。")
    _attach_saved_progress(application, application)
    return JSONResponse({"created": created, "application": application}, status_code=201 if created else 200)


@app.delete("/api/save/{opportunity_id}")
async def unsave_opportunity_api(request: Request, opportunity_id: str):
    """Remove a saved opportunity."""
    user = require_csrf(request)
    success = unsave_opportunity(user["id"], opportunity_id)
    return JSONResponse({"success": success})


@app.post("/api/saved/{opportunity_id}/progress")
async def update_saved_progress_api(request: Request, opportunity_id: str, payload: SavedProgressUpdate):
    """Update progress, reminder days and notes for a saved opportunity."""
    user = require_csrf(request)

    editable = {"progress_stage", "reminder_days", "progress_notes", "application_method", "next_action_at", "outcome"}
    changed = payload.model_fields_set & editable
    if not changed:
        raise HTTPException(422, "请至少填写一个更新项。")
    for required_value in ("progress_stage", "reminder_days", "progress_notes", "application_method"):
        if required_value in changed and getattr(payload, required_value) is None:
            raise HTTPException(422, f"{required_value} 不能为空。")

    saved_entry = get_user_saved_entry(user["id"], opportunity_id)
    if not saved_entry:
        raise HTTPException(404, "该机会未收藏。")

    notes = payload.progress_notes
    if notes is not None:
        notes = notes.strip()

    updates = {
        "stage": payload.progress_stage,
        "reminder_days": payload.reminder_days,
        "notes": notes,
        "application_method": payload.application_method,
    }
    if "next_action_at" in changed:
        updates["next_action_at"] = payload.next_action_at.isoformat() if payload.next_action_at else None
    if "outcome" in changed:
        updates["outcome"] = payload.outcome
    success = update_saved_progress(user["id"], opportunity_id, **updates)

    if not success:
        raise HTTPException(500, "收藏进度保存失败，请稍后重试。")

    item = get_user_saved_entry(user["id"], opportunity_id)
    application = get_saved_application(user["id"], opportunity_id)
    if application:
        _attach_saved_progress(application, item or saved_entry)
    return {
        "success": True,
        "opportunity_id": opportunity_id,
        "progress_stage": (item or saved_entry).get("progress_stage", "preparing"),
        "reminder_days": (item or saved_entry).get("reminder_days", 7),
        "progress_notes": (item or saved_entry).get("progress_notes", ""),
        "application": application,
    }


@app.get("/api/saved")
async def get_saved_api(request: Request):
    """Get user's saved opportunities."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    
    opportunities = _load_applications(user["id"], get_resume(user["id"], active_only=True))
    
    return JSONResponse({"saved": opportunities})


@app.post("/api/track")
async def track_behavior(
    request: Request,
    action_type: Literal["search", "click"] = Form(...),
    target_id: str = Form(..., min_length=1, max_length=300),
    target_type: Literal["opportunity", "search"] = Form(...),
    metadata: str = Form("{}", max_length=1000),
):
    """Only validated local browsing/search signals contribute to recommendations."""
    user = require_csrf(request)
    if target_type != ("opportunity" if action_type == "click" else "search"):
        raise HTTPException(422, "行为类型与目标不符。")
    if action_type == "click" and not get_opportunity_by_id(target_id):
        raise HTTPException(404, "机会不存在或尚未公开。")
    record_user_behavior(user["id"], action_type, target_id, target_type)
    return JSONResponse({"tracked": get_recommendation_state(user["id"])["use_behavior"]})


@app.get("/api/profile")
async def get_profile_api(request: Request):
    """Get current user's profile."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    
    profile = get_user_profile(user["id"])
    
    # Parse JSON fields
    if profile:
        for field in ["target_types", "majors", "research_fields", "region_preferences"]:
            try:
                profile[field] = json.loads(profile.get(field) or "[]")
            except:
                profile[field] = []
    
    return JSONResponse({"user": user, "profile": profile})


@app.get("/applications/compare", response_class=HTMLResponse)
async def compare_applications_page(request: Request, ids: List[str] = Query(default=[])):
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login?next=/applications", status_code=302)
    normalized = [item.strip() for value in ids for item in value.split(",") if item.strip()]
    if not 2 <= len(normalized) <= 4 or len(set(normalized)) != len(normalized) or any(len(item) > 100 for item in normalized):
        raise HTTPException(422, "请选择 2 至 4 个不同的申请进行比较。")
    applications = []
    resume = get_resume(user["id"], active_only=True)
    for opportunity_id in normalized:
        item = get_saved_application(user["id"], opportunity_id)
        if not item:
            raise HTTPException(404, "申请不存在或不属于当前账号。")
        _attach_saved_progress(item, item)
        item["professor_email"] = item.get("contact_email") or extract_email(item.get("description", ""))
        item["verification_fields"] = [field for field in ("deadline", "stipend", "requirements") if not item.get(field)]
        try:
            item["research_focus"] = research_agent.analyze_match("", item).get("research_focus", "")
        except Exception:
            item["research_focus"] = ""
        if resume:
            item["resume_match"] = match_resume(item, resume)
        applications.append(item)
    i18n = get_i18n(request)
    return templates.TemplateResponse("applications_compare.html", {
        "request": request, "user": user, "applications": applications,
        "has_resume": bool(resume), "i18n": i18n, "languages": LANGUAGES,
    })


def _ics_escape(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n").replace(";", "\\;").replace(",", "\\,")


def _ics_uri(value) -> str:
    return "".join(character for character in str(value or "")
                   if ord(character) >= 32 and not 127 <= ord(character) <= 159)


def _ics_fold_line(line: str) -> str:
    folded, current, octets = [], [], 0
    for character in line:
        size = len(character.encode("utf-8"))
        if current and octets + size > 75:
            folded.append("".join(current))
            current, octets = [" ", character], 1 + size
        else:
            current.append(character)
            octets += size
    folded.append("".join(current))
    return "\r\n".join(folded)


@app.get("/api/applications/calendar.ics")
async def applications_calendar(request: Request, opportunity_id: Optional[str] = Query(default=None, max_length=100)):
    user = require_user(request)
    if opportunity_id:
        application = get_saved_application(user["id"], opportunity_id)
        if not application:
            raise HTTPException(404, "申请不存在或不属于当前账号。")
        applications = [application]
    else:
        applications = _load_applications(user["id"])
    generated = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Summer Research Platform//Applications//EN", "CALSCALE:GREGORIAN"]
    for item in applications:
        start = _calendar_date(item.get("deadline"))
        if not start:
            continue
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:{_ics_escape(item['id'])}-{user['id']}@summer-research.local",
            f"DTSTAMP:{generated}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{(start + datetime.timedelta(days=1)).strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_escape('申请截止：' + (item.get('title') or '科研机会'))}",
            f"DESCRIPTION:{_ics_escape((item.get('description') or '')[:500])}",
            *([f"URL:{_ics_uri(item.get('source_url'))}"] if item.get("source_url") else []),
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(_ics_fold_line(line) for line in lines) + "\r\n", media_type="text/calendar; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="summer-applications.ics"', "Cache-Control": "no-store",
    })


@app.get("/saved", response_class=HTMLResponse)
@app.get("/applications", response_class=HTMLResponse)
async def saved_page(request: Request):
    """Application workspace; /saved remains as a compatible entry point."""
    user = get_current_user_from_request(request)
    if not user:
        next_path = "/applications" if request.url.path == "/applications" else "/saved"
        return RedirectResponse(url=f"/login?next={next_path}", status_code=302)
    i18n = get_i18n(request)
    resume = get_resume(user["id"], active_only=True)
    opportunities = _load_applications(user["id"], resume)
    application_groups, today_tasks = _group_applications(opportunities, i18n)
    return templates.TemplateResponse("saved.html", {
        "request": request,
        "user": user,
        "opportunities": opportunities,
        "application_groups": application_groups,
        "today_tasks": today_tasks,
        "has_resume": bool(resume),
        "i18n": i18n,
        "languages": LANGUAGES,
    })


if __name__ == "__main__":
    # Ensure DB exists
    if not os.path.exists(DB_NAME):
        init_db()
        
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
