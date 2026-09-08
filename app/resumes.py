"""User-reviewed private resume text and explainable, local opportunity matching."""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth import csrf_token_for, get_current_user, require_csrf, require_user
from app.database import get_db
from app.i18n import DEFAULT_LANGUAGE, I18n, LANGUAGES
from app.recommender import FIELD_KEYWORDS
from app.resume_parser import MAX_BYTES, MAX_TEXT
from app.search_ranking import score_search_result, deduplicate_results
from app.search_utils import text_contains_term

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
# ponytail: two local extraction workers per server process; use a job queue only at higher load.
parser_slots = BoundedSemaphore(2)
AMBIGUOUS = {"cv", "text", "image", "recognition", "learning", "模型", "system", "systems", "control", "interaction", "bio", "health", "medical", "game", "network", "agent"}
TOPIC_NAMES = {"nlp": "Natural Language Processing", "cv": "Computer Vision", "ml": "Machine Learning", "robotics": "Robotics", "systems": "Distributed Systems", "theory": "Algorithms", "hci": "HCI", "security": "Security", "graphics": "Computer Graphics", "database": "Databases", "networks": "Computer Networks", "ai_general": "Artificial Intelligence", "bioinformatics": "Bioinformatics", "quantum": "Quantum Computing"}
KEYWORDS = {TOPIC_NAMES[key]: tuple(word for word in words if word.lower() not in AMBIGUOUS) for key, words in FIELD_KEYWORDS.items()}
KEYWORDS.update({
    "Python": ("python",), "PyTorch": ("pytorch",), "TensorFlow": ("tensorflow",),
    "C++": ("c++", "cpp"), "Java": ("java",), "JavaScript": ("javascript",),
    "R": ("r",), "MATLAB": ("matlab",), "SQL": ("sql",), "Linux": ("linux",),
    "ROS": ("ros", "ros2", "robot operating system"), "Git": ("git",),
    "Statistics": ("statistics", "statistical", "统计"),
    "Astronomy": ("astronomy", "astrophysics", "天文学", "天体物理"),
    "Physics": ("physics", "物理"), "Chemistry": ("chemistry", "化学"),
    "Biology": ("biology", "biological", "生物学"),
    "Economics": ("economics", "经济学"), "Psychology": ("psychology", "心理学"),
    "PCR": ("pcr", "polymerase chain reaction"), "Cell Culture": ("cell culture", "细胞培养"),
    "Microscopy": ("microscopy", "显微成像", "显微镜"), "Genomics": ("genomics", "基因组"),
})


def init_resume_table():
    db = get_db()
    try:
        with db.conn:
            db.execute("""CREATE TABLE IF NOT EXISTS user_resumes (
                user_id INTEGER PRIMARY KEY, filename TEXT NOT NULL,
                text TEXT NOT NULL, keywords TEXT NOT NULL,
                enabled INTEGER NOT NULL, updated_at TEXT NOT NULL
            )""")
    finally:
        db.conn.close()


def get_resume(user_id, active_only=False):
    if not user_id:
        return None
    db = get_db()
    try:
        if "user_resumes" not in db.table_names():
            return None
        rows = list(db.query("SELECT * FROM user_resumes WHERE user_id = ?", [user_id]))
    finally:
        db.conn.close()
    if not rows or (active_only and not rows[0]["enabled"]):
        return None
    result = rows[0]
    result["keywords"] = json.loads(result["keywords"])
    result["enabled"] = bool(result["enabled"])
    return result


def aliases(keyword):
    return next((values + (label,) for label, values in KEYWORDS.items() if label.casefold() == keyword.casefold()), (keyword,))


def evidence(text, keyword):
    for line in text.splitlines():
        # Negated skills are not treated as positive evidence.
        if re.search(r"\b(?:no|not|without|lack|lacking)\b|不会|不熟悉|无经验|尚未|没有", line, re.I):
            continue
        for term in aliases(keyword):
            if text_contains_term(line, term):
                excerpt = next((line[i:i + 240] for i in range(0, len(line), 160) if text_contains_term(line[i:i + 240], term)), line[:240])
                return {"term": term, "excerpt": excerpt}
    return None


def suggestions(text):
    return [label for label in KEYWORDS if evidence(text, label)][:20]


def match_resume(opportunity, resume):
    source = "\n".join(str(opportunity.get(key) or "") for key in ("title", "description", "requirements"))
    matches = []
    for keyword in resume["keywords"]:
        own, found = evidence(resume["text"], keyword), evidence(source, keyword)
        if own and found:
            matches.append({"keyword": keyword, "term": found["term"], "resume_evidence": own["excerpt"], "opportunity_evidence": found["excerpt"]})
    return {"matched_keywords": [item["keyword"] for item in matches], "evidence": matches, "count": len(matches)}


def rank_by_resume(opportunities, resume, browse=False, source=None):
    # ponytail: scan local postings against at most 20 reviewed keywords; index these if the catalog outgrows this scan.
    ranked = []
    for original in opportunities:
        item = dict(original)
        match = match_resume(item, resume)
        if browse:
            if not match["evidence"]:
                continue
            verified = score_search_result(match["evidence"][0]["term"], item, source_filter=source)
            if not verified:
                continue
            item = verified
        item["resume_match"] = match
        ranked.append(item)
    if browse:
        ranked = deduplicate_results(ranked)
    return sorted(ranked, key=lambda row: (-row["resume_match"]["count"], -float(row.get("match_score") or row.get("search_score") or row.get("quality_score") or 0), str(row.get("id"))))


class ResumeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    filename: str = Field(default="粘贴的简历", max_length=180)
    text: str = Field(min_length=20, max_length=MAX_TEXT)
    keywords: List[str] = Field(min_length=1, max_length=20)
    enabled: StrictBool = True
    updated_at: Optional[str] = Field(default=None, max_length=100)

    @field_validator("keywords")
    @classmethod
    def valid_keywords(cls, items):
        result = []
        for item in items:
            item = item.strip()
            if not 1 <= len(item) <= 60 or any(ord(c) < 32 or c in "@/\\" for c in item) or not any(c.isalpha() for c in item):
                raise ValueError("匹配词应为研究方向或技能，每项最多 60 字，不含联系方式。")
            if item.casefold() not in {word.casefold() for word in result}:
                result.append(item)
        return result


def parse_local(filename, content):
    if not parser_slots.acquire(blocking=False):
        raise HTTPException(429, "当前正在解析其他简历，请稍后重试。")
    try:
        result = subprocess.run([sys.executable, "-m", "app.resume_parser", filename], input=content,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, cwd=str(Path(__file__).parent.parent))
        if result.returncode != 0:
            raise ValueError()
        data = json.loads(result.stdout)
        if "error" in data:
            raise HTTPException(422, data["error"])
        return data["text"]
    except (subprocess.TimeoutExpired, ValueError, KeyError, OSError):
        raise HTTPException(422, "文档解析失败或超时，请改用简化版 PDF/TXT 或粘贴正文。") from None
    finally:
        parser_slots.release()


@router.get("/resume")
def resume_page(request: Request):
    user = get_current_user(request.cookies.get("session_token"))
    if not user:
        return RedirectResponse("/login?next=/resume", status_code=302)
    language = request.query_params.get("lang") or request.cookies.get("lang", DEFAULT_LANGUAGE)
    return templates.TemplateResponse("resume.html", {"request": request, "user": user,
        "csrf_token": csrf_token_for(request), "i18n": I18n(language if language in LANGUAGES else DEFAULT_LANGUAGE), "languages": LANGUAGES})


@router.get("/api/resume")
def resume_get(request: Request):
    return {"resume": get_resume(require_user(request)["id"])}


@router.post("/api/resume/parse")
async def resume_parse(request: Request, filename: str = Query(..., min_length=1, max_length=180)):
    require_csrf(request)
    if Path(filename).suffix.lower() not in {".pdf", ".docx", ".txt"} or any(c in filename for c in ("/", "\\", "\r", "\n", "\x00")):
        raise HTTPException(422, "支持 PDF、DOCX 和 TXT 文件。")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "简历最大 2 MiB。")
        content.extend(chunk)
    text = await run_in_threadpool(parse_local, filename, bytes(content))
    return {"filename": filename, "text": text, "keywords": suggestions(text)}


@router.put("/api/resume")
def resume_save(request: Request, payload: ResumeInput):
    user_id = require_csrf(request)["id"]
    if "\x00" in payload.text:
        raise HTTPException(422, "正文不能包含空字符。")
    if any(not evidence(payload.text, word) for word in payload.keywords):
        raise HTTPException(422, "每个匹配词都应能在核对后的正文找到依据；请删除误识别或否定的技能。")
    db = get_db()
    try:
        with db.conn:
            db.execute("BEGIN IMMEDIATE")
            current = list(db.query("SELECT updated_at FROM user_resumes WHERE user_id = ?", [user_id]))
            if (current[0]["updated_at"] if current else None) != payload.updated_at:
                raise HTTPException(409, "简历已在其他页面更新，请先复制当前编辑内容，再重新载入。")
            db.execute("""INSERT INTO user_resumes (user_id, filename, text, keywords, enabled, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                filename=excluded.filename, text=excluded.text, keywords=excluded.keywords,
                enabled=excluded.enabled, updated_at=excluded.updated_at""",
                [user_id, payload.filename, payload.text, json.dumps(payload.keywords), int(payload.enabled), datetime.now(timezone.utc).isoformat()])
    finally:
        db.conn.close()
    return {"resume": get_resume(user_id)}


@router.delete("/api/resume")
def resume_delete(request: Request):
    user_id = require_csrf(request)["id"]
    db = get_db()
    try:
        with db.conn:
            db.execute("DELETE FROM user_resumes WHERE user_id = ?", [user_id])
    finally:
        db.conn.close()
    return {"ok": True}
