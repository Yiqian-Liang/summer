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
from urllib.parse import urlencode
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
    unsave_opportunity, get_user_saved, is_opportunity_saved,
    # Password reset
    create_password_reset_token, verify_reset_token, use_reset_token, update_user_password
)
from app.auth import (
    hash_password, verify_password, create_session, get_session, 
    destroy_session, get_current_user, validate_email, validate_username,
    validate_password, get_onboarding_options
)
from app.auth import require_user, require_csrf, csrf_token_for, revoke_user_sessions
from app.recommender import recommender
from app.i18n import I18n, LANGUAGES, DEFAULT_LANGUAGE
from app.search_utils import (
    SOURCE_ALIASES,
    build_external_search_links,
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

app = FastAPI(title="Summer Research Platform")
app.include_router(mail_router)
app.include_router(mail_oauth_router)
app.include_router(posts_router)
app.include_router(resume_router)


@app.middleware("http")
async def private_workspace_headers(request: Request, call_next):
    request.state.csrf_token = csrf_token_for(request)
    response = await call_next(request)
    if request.cookies.get("session_token") or request.url.path in {"/mail", "/resume", "/api/generate-rag-draft", "/posts/mine", "/posts/new"} or request.url.path.startswith(("/api/mail", "/api/resume")) or request.url.path.endswith("/edit"):
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
) -> Dict:
    """Refresh, verify, rank, and finally paginate for both search entry points."""
    enabled = bool(query) and live and scraper_engine.has_live_search_credentials(query, source)
    report = {
        "status": "local" if enabled else "unavailable", "cached": False,
        "providers": scraper_engine.live_search_providers(query, source) if enabled else [],
        "failed_sources": [], "synced_count": 0,
    }
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
        search_data = await run_opportunity_search(q, filter_source, page, limit, refresh, resume=resume if sort == "resume" else None)
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
    data = await run_opportunity_search(q, filter_source, page, limit, refresh, live, resume)
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
    use_ai: bool = False
    include_social_media: bool = False


@app.get("/mail", response_class=HTMLResponse)
async def mail_page(request: Request, opportunity: str = Query(None, max_length=100), draft: int = Query(None, ge=1)):
    user = get_current_user_from_request(request)
    if not user:
        from urllib.parse import quote
        return RedirectResponse("/login?next=" + quote(str(request.url.path) + ("?" + request.url.query if request.url.query else ""), safe=""), status_code=302)
    item = get_opportunity_by_id(opportunity) if opportunity else None
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
    require_user(request)
    item = get_opportunity_by_id(opportunity_id)
    if not item:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    item["professor_email"] = item.get("contact_email") or extract_email(item.get("description", "")) or ""
    return {"opportunity": item}


@app.post("/api/mail/generate")
@app.post("/api/generate-rag-draft")
async def generate_rag_draft(request: Request, payload: DraftRequest):
    user = require_csrf(request)
    opportunity = get_opportunity_by_id(payload.opportunity_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
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
    query: str = Query(None, description="Optional specific query to collect")
):
    """
    Manually trigger a data collection job.
    Useful for testing or on-demand updates.
    """
    from app.scheduler import DataCollectionScheduler
    
    scheduler = DataCollectionScheduler()
    
    if query:
        stats = await run_in_threadpool(scheduler.run_collection_job, query)
    else:
        # Run with just a few default queries for quick update
        scheduler.queries = scheduler.queries[:3]
        stats = await run_in_threadpool(scheduler.run_collection_job)
    
    return JSONResponse(stats)


@app.post("/api/analyze-match")
async def analyze_match_api(payload: Dict = Body(...)):
    """
    Analyzes match using LLM.
    """
    query = payload.get("query", "")
    opp_id = payload.get("opportunity_id")

    opportunity = get_opportunity_by_id(opp_id)
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")

    analysis = await run_in_threadpool(research_agent.analyze_match, query, opportunity, use_llm=True)
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
    if not get_opportunity_by_id(opportunity_id):
        raise HTTPException(404, "机会已不存在或尚未公开。")
    success = is_opportunity_saved(user["id"], opportunity_id) or db_save_opportunity(user["id"], opportunity_id)

    return JSONResponse({"success": success})


@app.delete("/api/save/{opportunity_id}")
async def unsave_opportunity_api(request: Request, opportunity_id: str):
    """Remove a saved opportunity."""
    user = require_csrf(request)
    success = unsave_opportunity(user["id"], opportunity_id)
    return JSONResponse({"success": success})


@app.get("/api/saved")
async def get_saved_api(request: Request):
    """Get user's saved opportunities."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    
    saved = get_user_saved(user["id"])
    
    # Fetch full opportunity data
    opportunities = []
    for item in saved:
        opp = get_opportunity_by_id(item["opportunity_id"])
        if opp:
            opp["saved_at"] = item["created_at"]
            opp["notes"] = item["notes"]
            opportunities.append(opp)
    
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


@app.get("/saved", response_class=HTMLResponse)
async def saved_page(request: Request):
    """Page showing user's saved opportunities."""
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login?next=/saved", status_code=302)
    
    saved = get_user_saved(user["id"])
    
    # Fetch full opportunity data
    opportunities = []
    for item in saved:
        opp = get_opportunity_by_id(item["opportunity_id"])
        if opp:
            opp["saved_at"] = item["created_at"]
            opp["notes"] = item["notes"]
            try:
                opp["hiring_signals_list"] = json.loads(opp.get("hiring_signals") or "[]")
            except:
                opp["hiring_signals_list"] = []
            opportunities.append(opp)
    
    i18n = get_i18n(request)
    return templates.TemplateResponse("saved.html", {
        "request": request,
        "user": user,
        "opportunities": opportunities,
        "i18n": i18n,
        "languages": LANGUAGES,
    })


if __name__ == "__main__":
    # Ensure DB exists
    if not os.path.exists(DB_NAME):
        init_db()
        
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
