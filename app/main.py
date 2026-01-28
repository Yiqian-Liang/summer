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
import uvicorn
from typing import Optional, Dict, List
import os
import datetime
import json

from app.scrapers import AcademicScraper
from app.matching import matcher
from app.agents import ResearchAgent
from app.rag import RAGWorkbench
from app.database import (
    DB_NAME, init_db, get_db, 
    get_opportunities, search_opportunities_fts, 
    get_opportunity_by_id, get_opportunity_count,
    upsert_opportunity,
    # User management
    init_user_tables, create_user, get_user_by_email, get_user_by_username,
    get_user_by_id, update_user_last_login, save_user_profile, get_user_profile,
    record_user_behavior, get_user_behaviors, save_opportunity as db_save_opportunity,
    unsave_opportunity, get_user_saved, is_opportunity_saved
)
from app.auth import (
    hash_password, verify_password, create_session, get_session, 
    destroy_session, get_current_user, validate_email, validate_username,
    validate_password, get_onboarding_options
)
from app.recommender import recommender
from app.i18n import I18n, LANGUAGES, DEFAULT_LANGUAGE

app = FastAPI(title="Summer Research Platform")

# Cookie names
SESSION_COOKIE = "session_token"
LANG_COOKIE = "lang"


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


# ============== Language Routes ==============

@app.get("/set-lang/{lang}")
async def set_language(request: Request, lang: str):
    """Set language preference and redirect back."""
    if lang not in LANGUAGES:
        lang = DEFAULT_LANGUAGE
    
    # Get the referer to redirect back
    referer = request.headers.get("referer", "/")
    
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
        return RedirectResponse(url=next, status_code=302)
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
    response: Response,
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
    token = create_session(user["id"], {
        "id": user["id"],
        "email": user["email"],
        "username": user["username"],
        "onboarding_complete": user["onboarding_complete"],
    })
    
    # Update last login
    update_user_last_login(user["id"])
    
    # Set cookie and redirect
    redirect_url = "/onboarding" if not user["onboarding_complete"] else next
    redirect_response = RedirectResponse(url=redirect_url, status_code=302)
    redirect_response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
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


@app.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request):
    """Onboarding page for new users to select interests."""
    user = get_current_user_from_request(request)
    if not user:
        return RedirectResponse(url="/login?next=/onboarding", status_code=302)
    
    # Get existing profile if any
    profile = get_user_profile(user["id"])
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
async def home(request: Request, page: int = Query(1, ge=1)):
    """
    Home page with opportunity feed.
    Shows personalized opportunities based on user preferences.
    """
    user = get_current_user_from_request(request)
    profile = None
    behaviors = None
    recommended_searches = []
    
    limit = 20
    offset = (page - 1) * limit
    
    # Get more opportunities for personalized ranking (if user is logged in)
    fetch_limit = 100 if user else limit
    
    opportunities = get_opportunities(
        limit=fetch_limit,
        offset=0,
        min_quality=0,
        order_by="quality_score desc, updated_at desc"
    )
    
    # If user is logged in, get their profile for personalization
    if user:
        profile = get_user_profile(user["id"])
        behaviors = get_user_behaviors(user["id"], limit=50)
        
        # Record page view behavior
        record_user_behavior(user["id"], "view", "home", "page")
        
        # Get personalized ranking
        if profile:
            opportunities = recommender.rank_opportunities(opportunities, profile, behaviors)
            recommended_searches = recommender.get_recommended_searches(profile)
    
    # Apply pagination after ranking
    total_count = len(opportunities) if user else get_opportunity_count()
    opportunities = opportunities[offset:offset + limit]
    
    # Parse hiring_signals JSON for template
    for opp in opportunities:
        try:
            opp["hiring_signals_list"] = json.loads(opp.get("hiring_signals") or "[]")
        except:
            opp["hiring_signals_list"] = []
        
        # Check if saved by user
        if user:
            opp["is_saved"] = is_opportunity_saved(user["id"], opp["id"])
        else:
            opp["is_saved"] = False
    
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    i18n = get_i18n(request)
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "user": user,
        "profile": profile,
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
    q: str = Query(None, description="Search query"),
    page: int = Query(1, ge=1)
):
    """
    Unified search page - aggregates results from all platforms,
    ranks them together by relevance (like a meta search engine).
    """
    user = get_current_user_from_request(request)
    
    limit = 20
    offset = (page - 1) * limit
    results = []
    total_count = 0
    
    if q:
        # Track search behavior
        if user:
            record_user_behavior(user["id"], "search", q, "search")
        # First, try database search
        db_results = search_opportunities_fts(q, limit=100)
        
        # Always do live scraping to get fresh results from multiple platforms
        try:
            from app.scheduler import OpportunityProcessor
            processor = OpportunityProcessor()
            
            # Live search from all scrapers
            live_results = scraper_engine.search_all_sources(q)
            
            # Process and store each result
            for item in live_results:
                # Detect source type from the source field
                source_str = item.get("source", "").lower()
                
                if "scholar" in source_str:
                    source_type = "scholar"
                elif "linkedin" in source_str:
                    source_type = "linkedin"
                elif "xiaohongshu" in source_str or "小红书" in source_str or "rednote" in source_str:
                    source_type = "xiaohongshu"
                elif "wechat" in source_str or "微信" in source_str or "weixin" in source_str:
                    source_type = "wechat"
                elif "twitter" in source_str or "x.com" in source_str:
                    source_type = "twitter"
                elif "instagram" in source_str:
                    source_type = "instagram"
                elif "zhihu" in source_str or "知乎" in source_str:
                    source_type = "zhihu"
                elif "nsf" in source_str:
                    source_type = "nsf"
                elif "discord" in source_str:
                    source_type = "discord"
                else:
                    source_type = "other"
                
                try:
                    normalized = processor.normalize_item(item, source_type)
                    upsert_opportunity(normalized)
                except Exception as e:
                    print(f"Error processing item: {e}")
            
            # Re-fetch from DB to get all results including newly added
            db_results = search_opportunities_fts(q, limit=100)
            
        except Exception as e:
            print(f"Live search error: {e}")
            import traceback
            traceback.print_exc()
        
        # Rank all results together
        if db_results:
            # Prepare candidates for ranking
            candidates = []
            for opp in db_results:
                cand = {**opp}
                cand["text_for_matching"] = f"{opp.get('title', '')} {opp.get('description', '')} {opp.get('professor_name', '')}"
                # Use quality_score as default match_score if not set
                if "match_score" not in cand:
                    cand["match_score"] = cand.get("quality_score", 50)
                candidates.append(cand)
            
            # Try semantic ranking (may fail if model not loaded)
            try:
                ranked = matcher.rank_candidates(q, candidates)
            except:
                # Fallback: sort by quality_score
                ranked = sorted(candidates, key=lambda x: x.get("quality_score", 0), reverse=True)
            
            # Ensure all results have match_score
            for r in ranked:
                if "match_score" not in r or r["match_score"] is None:
                    r["match_score"] = r.get("quality_score", 50)
            
            total_count = len(ranked)
            results = ranked[offset:offset + limit]
            
            # Add AI analysis for top results
            for result in results[:5]:
                try:
                    analysis = research_agent.analyze_match(q, result)
                    result["agent_analysis"] = analysis.get("reasoning", "")
                except:
                    result["agent_analysis"] = ""
            
            # Parse hiring_signals JSON and check saved status
            for result in results:
                try:
                    result["hiring_signals_list"] = json.loads(result.get("hiring_signals") or "[]")
                except:
                    result["hiring_signals_list"] = []
                
                # Check if saved by user
                if user:
                    result["is_saved"] = is_opportunity_saved(user["id"], result["id"])
                else:
                    result["is_saved"] = False
    
    total_pages = (total_count + limit - 1) // limit if total_count > 0 else 1
    i18n = get_i18n(request)
    
    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "user": user,
        "query": q or "",
        "results": results,
        "total_count": total_count,
        "current_page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
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
    limit: int = Query(20, le=100),
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
        "total": get_opportunity_count(),
    })


@app.get("/api/search")
async def api_search(
    q: str = Query(..., description="Search query"),
    limit: int = Query(20, le=100),
):
    """
    API endpoint for semantic search.
    """
    results = search_opportunities_fts(q, limit=limit)
    
    if results:
        # Rank by semantic similarity
        candidates = [
            {**r, "text_for_matching": f"{r.get('title', '')} {r.get('description', '')}"}
            for r in results
        ]
        results = matcher.rank_candidates(q, candidates)
    
    return JSONResponse({
        "query": q,
        "results": results,
        "count": len(results),
    })


@app.post("/api/generate-rag-draft")
async def generate_rag_draft(payload: Dict = Body(...)):
    """
    API Endpoint for Context-Aware RAG Drafting.
    Uses app/rag.py logic to create citations and analyze hiring signals.
    """
    student_profile = payload.get("student_profile", {})
    professor_profile = payload.get("professor_profile", {})
    include_social_media = payload.get("include_social_media", False)
    
    draft_data = rag_workbench.generate_context_aware_draft(
        student_profile, professor_profile, include_social_media
    )
    
    # Log draft to DB
    db = get_db()
    db["email_drafts"].insert({
        "student_name": student_profile.get("name", "Student"),
        "professor_name": professor_profile.get("name", "Unknown"),
        "professor_email": professor_profile.get("email", ""),
        "content": draft_data["body"],
        "status": "rag_generated",
        "created_at": datetime.datetime.now().isoformat()
    })
    
    return JSONResponse({"draft": draft_data})


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
        stats = scheduler.run_collection_job(query)
    else:
        # Run with just a few default queries for quick update
        scheduler.queries = scheduler.queries[:3]
        stats = scheduler.run_collection_job()
    
    return JSONResponse(stats)


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
        for row in db.execute("SELECT source_type, COUNT(*) as cnt FROM opportunities GROUP BY source_type").fetchall():
            source_counts[row[0]] = row[1]
    except:
        pass
    
    return JSONResponse({
        "total_opportunities": total_opportunities,
        "by_source": source_counts,
        "last_updated": datetime.datetime.now().isoformat(),
    })


# ============== User API Routes ==============

@app.post("/api/save/{opportunity_id}")
async def save_opportunity_api(request: Request, opportunity_id: str):
    """Save/bookmark an opportunity."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    
    success = db_save_opportunity(user["id"], opportunity_id)
    
    # Record behavior
    record_user_behavior(user["id"], "save", opportunity_id, "opportunity")
    
    return JSONResponse({"success": success})


@app.delete("/api/save/{opportunity_id}")
async def unsave_opportunity_api(request: Request, opportunity_id: str):
    """Remove a saved opportunity."""
    user = get_current_user_from_request(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    
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
    action_type: str = Form(...),
    target_id: str = Form(...),
    target_type: str = Form(...),
    metadata: str = Form("{}"),
):
    """Track user behavior for recommendations."""
    user = get_current_user_from_request(request)
    if not user:
        return JSONResponse({"tracked": False})
    
    try:
        meta = json.loads(metadata)
    except:
        meta = {}
    
    record_user_behavior(user["id"], action_type, target_id, target_type, meta)
    return JSONResponse({"tracked": True})


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
