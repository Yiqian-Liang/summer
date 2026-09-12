"""User-reviewed private resume text and explainable, local opportunity matching."""
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import BoundedSemaphore
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from starlette.concurrency import run_in_threadpool

from app.auth import csrf_token_for, get_current_user, require_csrf, require_user
from app.database import get_db, get_opportunity_by_id, get_saved_application
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
SECTION_ALIASES = {
    "education": ("education", "academic background", "educational background", "教育", "教育背景", "教育经历", "学历"),
    "experience": ("research experience", "research and project experience", "research & project experience",
        "experience", "projects", "selected projects", "research projects", "project experience",
        "科研经历", "研究经历", "项目经历", "工作经历"),
    "skills": ("skills", "technical skills", "skills and interests", "专业技能", "技能", "研究技能"),
}
SECTION_LABELS = {
    "education": ("教育背景", "Education"),
    "experience": ("研究或项目经历", "Research or project experience"),
    "skills": ("技能", "Skills"),
}
BULLET_RE = re.compile(r"^\s*(?:[-*•▪◦‣]|\d+[.)])\s+(.+)$")
ACTION_RE = re.compile(
    r"^(?:(?:built|developed|designed|implemented|created|conducted|analy[sz]ed|trained|evaluated|"
    r"optimi[sz]ed|led|published|deployed|collected|modelled|modeled|researched|applied|"
    r"improved|increased|reduced|proposed|reproduced)\b|(?:构建|开发|设计|实现|创建|开展|分析|训练|"
    r"评估|优化|主导|发表|部署|采集|建模|研究|复现|使用|提出|完成))", re.I)
RESULT_RE = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|x\b|ms\b|seconds?\b|hours?\b|days?\b|weeks?\b|"
    r"samples?\b|images?\b|datasets?\b|papers?\b)|\d+(?:\.\d+)?\s*(?:倍|毫秒|小时|天|周|月|"
    r"人|篇|项|个|次)|\b(?:outperformed|published|accepted|deployed)\b|发表|录用|上线|开源)", re.I)
METHOD_CUE_RE = re.compile(r"\b(?:using|with|via|through|based on)\b|使用|采用|通过|基于", re.I)
VAGUE_PHRASES = {
    "responsible for": "responsible for", "participated in": "participated in",
    "helped with": "helped with", "familiar with": "familiar with",
    "hard-working": "hard-working", "team player": "team player",
    "负责": "负责", "参与": "参与", "协助": "协助", "熟悉": "熟悉",
    "学习能力强": "学习能力强", "认真负责": "认真负责", "团队精神": "团队精神",
}
DATE_TOKEN_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?:19|20)\d{2}\b|"
    r"(?:19|20)\d{2}年(?:0?[1-9]|1[0-2])月|"
    r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])\b|"
    r"\b(?:19|20)\d{2}\b", re.I)
MONTHS = {name.casefold(): index for index, names in enumerate((
    ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
    ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
    ("sep", "september"), ("oct", "october"), ("nov", "november"), ("dec", "december")), 1)
    for name in names}


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


def _message(code, zh, en, evidence_text=None):
    item = {"code": code, "message_zh": zh, "message_en": en}
    if evidence_text:
        item["evidence"] = evidence_text
    return item


def _section_checks(lines):
    checks = []
    for key, names in SECTION_ALIASES.items():
        found = None
        for line in lines:
            if _heading_key(line) == key:
                found = line[:240]
                break
        zh, en = SECTION_LABELS[key]
        checks.append({"key": key, "label_zh": zh, "label_en": en,
            "status": "found" if found else "review", "evidence": found})
    return checks


def _heading_key(line):
    normalized = re.sub(r"\s+", " ", line.strip().strip("#*:：|-— ")).casefold()
    if len(normalized) > 80:
        return None
    return next((key for key, names in SECTION_ALIASES.items()
        if normalized in {name.casefold() for name in names}), None)


def _date_value(token):
    token = token.strip().rstrip(".")
    named = re.match(r"([A-Za-z]+)\.?\s+((?:19|20)\d{2})", token)
    if named:
        return int(named.group(2)), MONTHS[named.group(1).casefold()]
    values = re.findall(r"\d+", token)
    return (int(values[0]), int(values[1]) if len(values) > 1 else 0)


def _date_style(token):
    if "年" in token:
        return "chinese"
    if re.search(r"[A-Za-z]", token):
        return "named-month"
    if "/" in token:
        return "slash"
    if "." in token:
        return "dot"
    if "-" in token:
        return "hyphen"
    return "year"


def _date_checks(lines):
    tokens, issues, month_styles = [], [], set()
    for line in lines:
        found = list(DATE_TOKEN_RE.finditer(line))
        for match in found:
            token = match.group(0)
            tokens.append(token)
            style = _date_style(token)
            if style != "year":
                month_styles.add(style)
        invalid = re.search(r"(?:19|20)\d{2}(?:[-/.]|年)(\d{1,2})(?:月)?", line)
        if invalid and int(invalid.group(1)) > 12:
            issues.append(_message("invalid_month", "月份超出 1–12，请核对。", "A month is outside 1–12; review it.", line[:240]))
        if len(found) >= 2:
            between = line[found[0].end():found[1].start()]
            if re.search(r"(?:\s[-~]\s|[–—至]|\bto\b)", between, re.I) and _date_value(found[0].group()) > _date_value(found[1].group()):
                issues.append(_message("reversed_range", "日期区间的结束时间早于开始时间，请核对。", "A date range ends before it starts; review it.", line[:240]))
    if len(month_styles) > 1:
        issues.append(_message("mixed_formats", "月份格式混用，建议统一后再投递。", "Month formats are mixed; make them consistent before applying.", ", ".join(sorted(month_styles))))
    if not tokens:
        issues.append(_message("dates_missing", "未识别到经历日期，请核对教育和研究经历的时间。", "No dates were detected; review the dates for education and research experience."))
    return {"status": "review" if issues else "consistent", "formats": sorted(month_styles),
        "evidence": list(dict.fromkeys(tokens))[:20], "issues": issues}


def _method_terms(text, resume_keywords):
    labels = list(dict.fromkeys([*(resume_keywords or []), *KEYWORDS]))
    return [label for label in labels if evidence(text, label)][:8]


def _experience_checks(lines, resume_keywords):
    start = next((index for index, line in enumerate(lines) if _heading_key(line) == "experience"), None)
    if start is not None:
        end = next((index for index in range(start + 1, len(lines)) if _heading_key(lines[index])), len(lines))
        lines = lines[start + 1:end]
    items = []
    for line in lines:
        explicit = BULLET_RE.match(line)
        body = (explicit.group(1) if explicit else line).strip()
        action = ACTION_RE.match(body)
        if not explicit and not action:
            continue
        methods = _method_terms(body, resume_keywords)
        method_cue = METHOD_CUE_RE.search(body)
        if not methods and method_cue:
            methods = [method_cue.group(0)]
        result = RESULT_RE.search(body)
        review = []
        if not action:
            review.append("action")
        if not methods:
            review.append("method")
        if not result:
            review.append("result")
        items.append({"text": body[:500], "action_evidence": action.group(0) if action else None,
            "method_evidence": methods, "result_evidence": result.group(0) if result else None,
            "review": review})
        if len(items) == 30:
            break
    return {"count": len(items), "items": items}


def _vague_phrase_checks(text):
    folded = text.casefold()
    items = []
    for phrase, label in VAGUE_PHRASES.items():
        count = folded.count(phrase.casefold())
        if count >= 2:
            items.append({"phrase": label, "count": count, "status": "review"})
    return items


def review_resume(resume, opportunity=None):
    """Return deterministic, evidence-backed checks; this function performs no I/O."""
    lines = [line.strip() for line in resume["text"].splitlines() if line.strip()]
    sections = _section_checks(lines)
    dates = _date_checks(lines)
    experience = _experience_checks(lines, resume.get("keywords"))
    vague = _vague_phrase_checks(resume["text"])
    recommendations = []
    for section in sections:
        if section["status"] == "review":
            recommendations.append(_message(f"missing_{section['key']}",
                f"补充清晰的“{section['label_zh']}”栏目标题。",
                f"Add a clear “{section['label_en']}” section heading."))
    recommendations.extend(dates["issues"])
    if not experience["items"]:
        recommendations.append(_message("bullets_missing", "把研究或项目经历拆成简洁的要点。", "Turn research or project experience into concise bullets."))
    elif any(item["review"] for item in experience["items"]):
        recommendations.append(_message("strengthen_bullets", "逐条补全行动、方法和可验证结果；只写真实发生的内容。", "Add the action, method and verifiable result to each bullet, using only facts that are true."))
    if vague:
        recommendations.append(_message("repeated_vague_phrases", "减少重复的空泛表达，用具体行动和结果替换。", "Replace repeated vague phrases with specific actions and results."))

    result = {"sections": sections, "date_consistency": dates, "experience_bullets": experience,
        "vague_phrases": vague, "recommendations": recommendations}
    if opportunity:
        source = "\n".join(str(opportunity.get(key) or "") for key in ("title", "description", "requirements"))
        matched = match_resume(opportunity, resume)
        missing_by_term = {}
        for label in KEYWORDS:
            found = evidence(source, label)
            own = evidence(resume["text"], label)
            if found and not own:
                term_key = found["term"].casefold()
                item = {"keyword": label, "term": found["term"],
                    "opportunity_evidence": found["excerpt"], "status": "review",
                    "label_zh": "请核对", "label_en": "Review"}
                current = missing_by_term.get(term_key)
                if not current or label.casefold() == term_key:
                    missing_by_term[term_key] = item
        missing = list(missing_by_term.values())[:20]
        result["opportunity"] = {
            "id": opportunity.get("id"), "title": opportunity.get("title") or "",
            "source_url": opportunity.get("source_url") or "", "university": opportunity.get("university") or "",
            "professor_name": opportunity.get("professor_name") or "", "deadline": opportunity.get("deadline") or "",
            "private_import": bool(opportunity.get("private_import")),
            "matched_evidence": matched["evidence"], "missing_terms_for_review": missing,
        }
    return result


def _review_opportunity(user_id, opportunity_id):
    opportunity = get_opportunity_by_id(opportunity_id)
    if opportunity:
        return opportunity
    opportunity = get_saved_application(user_id, opportunity_id)
    if opportunity and opportunity.get("is_external"):
        opportunity["private_import"] = True
        return opportunity
    raise HTTPException(404, "机会不存在，或该私有导入不属于当前用户。")


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


@router.get("/resume/review")
def resume_review_page(request: Request, opportunity_id: Optional[str] = Query(default=None, min_length=1, max_length=100)):
    user = get_current_user(request.cookies.get("session_token"))
    if not user:
        next_path = "/resume/review" + (f"?opportunity_id={opportunity_id}" if opportunity_id else "")
        return RedirectResponse(f"/login?next={quote(next_path, safe='')}", status_code=302)
    resume = get_resume(user["id"])
    opportunity = _review_opportunity(user["id"], opportunity_id) if resume and opportunity_id else None
    language = request.query_params.get("lang") or request.cookies.get("lang", DEFAULT_LANGUAGE)
    return templates.TemplateResponse("resume_review.html", {"request": request, "user": user,
        "resume": resume, "review": review_resume(resume, opportunity) if resume else None,
        "i18n": I18n(language if language in LANGUAGES else DEFAULT_LANGUAGE), "languages": LANGUAGES})


@router.get("/api/resume")
def resume_get(request: Request):
    return {"resume": get_resume(require_user(request)["id"])}


@router.get("/api/resume/review")
def resume_review_get(request: Request, opportunity_id: Optional[str] = Query(default=None, min_length=1, max_length=100)):
    user = require_user(request)
    resume = get_resume(user["id"])
    if not resume:
        raise HTTPException(404, "请先上传并保存简历。")
    opportunity = _review_opportunity(user["id"], opportunity_id) if opportunity_id else None
    return {"resume": {"filename": resume["filename"], "updated_at": resume["updated_at"],
        "enabled": resume["enabled"]}, "review": review_resume(resume, opportunity)}


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
