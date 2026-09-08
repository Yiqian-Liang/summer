"""Deterministic relevance, freshness, and deduplication for opportunity search."""

from __future__ import annotations

import datetime as dt
import math
import re
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.search_utils import (
    build_query_concepts,
    canonicalize_source_url,
    detect_source_type,
    normalize_source_filter,
    text_contains_term,
)


OPPORTUNITY_TERMS = [
    "summer research", "summer internship", "research internship", "research intern",
    "undergraduate research", "reu", "phd position", "ph.d position", "doctoral position", "studentship",
    "phd opening", "doctoral opening", "phd scholarship", "research assistant", "postdoc", "hiring", "recruiting",
    "open position", "open positions", "opening", "openings", "join my lab",
    "looking for students", "prospective students", "apply", "application deadline",
    "accepting students", "seeking students", "applications open", "applications are open",
    "funded position", "stipend", "fellowship", "暑研", "科研实习", "博士招生",
    "硕士招生", "课题组招生", "导师招生", "招生", "招募", "招聘", "博士职位", "博士岗位", "申请截止", "全奖",
]

ROLLING_DEADLINE_TERMS = {
    "rolling", "open until filled", "until filled", "ongoing", "随时", "滚动", "招满即止",
}

SOURCE_RELIABILITY = {
    "community": 2,
    "university": 10,
    "professor_site": 10,
    "nsf": 10,
    "pathways": 9,
    "mitacs": 9,
    "daad": 9,
    "academicjobs": 9,
    "euraxess": 9,
    "findaphd": 8,
    "nature_careers": 8,
    "jobs_ac_uk": 8,
    "cnrs": 9,
    "inria": 9,
    "max_planck": 9,
    "linkedin": 5,
    "twitter": 4,
    "xiaohongshu": 5,
    "wechat": 6,
    "zhihu": 4,
    "bilibili": 4,
    "weibo": 4,
    "tieba": 3,
    "scholar": 3,
    "other": 2,
}

INFORMATIONAL_FILTERS = {"scholar", "zhihu", "bilibili"}


def _text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _candidate_fields(candidate: Dict) -> List[Tuple[str, str, float]]:
    return [
        ("title", _text(candidate.get("title") or candidate.get("name")), 10.0),
        ("organization", _text(" ".join(str(value) for value in [
            candidate.get("professor_name") or candidate.get("author"),
            candidate.get("university") or candidate.get("institution"),
            candidate.get("department"),
        ] if value)), 8.5),
        ("description", _text(candidate.get("description") or candidate.get("snippet") or candidate.get("desc")), 6.0),
        ("requirements", _text(candidate.get("requirements")), 5.0),
    ]


def _candidate_haystack(candidate: Dict) -> str:
    return " ".join(value for _, value, _ in _candidate_fields(candidate) if value).lower()


def _matched_opportunity_terms(text: str) -> List[str]:
    return [term for term in OPPORTUNITY_TERMS if text_contains_term(text, term)]


def _parse_date(value) -> Optional[dt.datetime]:
    if value is None or value == "":
        return None
    if re.fullmatch(r"20\d{2}", str(value).strip()):
        return None  # A year alone is not a Unix timestamp or an exact deadline.
    if isinstance(value, (int, float)) or str(value).strip().isdigit():
        try:
            timestamp = float(value)
            if timestamp > 10_000_000_000:
                timestamp /= 1000
            return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None

    text = _text(value)
    if not text:
        return None
    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(iso_candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        pass

    for pattern in (
        "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%B %d, %Y", "%b %d, %Y",
        "%d %B %Y", "%d %b %Y",
    ):
        try:
            return dt.datetime.strptime(text, pattern).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue

    date_match = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})(?:日|\b)", text)
    if date_match:
        try:
            return dt.datetime(
                int(date_match.group(1)),
                int(date_match.group(2)),
                int(date_match.group(3)),
                tzinfo=dt.timezone.utc,
            )
        except ValueError:
            return None
    return None


def _deadline_is_expired(candidate: Dict, now: dt.datetime) -> bool:
    deadline = _text(candidate.get("deadline"))
    if any(term in deadline.lower() for term in ROLLING_DEADLINE_TERMS):
        return False
    if deadline.lower() in {"closed", "expired", "已截止", "已结束", "招满"}:
        return True
    parsed = _parse_date(deadline)
    if parsed:
        return parsed.date() < now.date()

    title = _text(candidate.get("title"))
    years = [int(value) for value in re.findall(r"(?<!\d)20\d{2}(?!\d)", f"{title} {deadline}")]
    return bool(years and max(years) < now.year)


def _freshness_score(candidate: Dict, now: dt.datetime) -> float:
    published = _parse_date(candidate.get("published_at"))
    if not published:
        return 0.0
    age_days = max(0, (now - published).days)
    if age_days <= 30:
        return 5.0
    if age_days <= 180:
        return 3.0
    if age_days <= 365:
        return 1.0
    if age_days > 730:
        return -5.0
    return -1.0


def _profile_only(candidate: Dict, opportunity_terms: List[str]) -> bool:
    url = canonicalize_source_url(candidate.get("source_url") or candidate.get("url") or candidate.get("link") or "")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    title = _text(candidate.get("title")).lower()

    if "linkedin.com" in host and (path.startswith("/in/") or path.startswith("/pub/")):
        return True
    if host in {"x.com", "twitter.com", "www.x.com", "www.twitter.com"} and "/status/" not in path:
        return True
    if title.endswith("/ posts / x"):
        return True
    return False


def _concept_evidence(candidate: Dict, concept: List[str]) -> Tuple[float, Optional[str], Optional[str]]:
    best = (0.0, None, None)
    fields = _candidate_fields(candidate)
    for term_index, term in enumerate(concept):
        alias_factor = 1.0 if term_index == 0 else 0.86
        for field_name, value, field_weight in fields:
            if text_contains_term(value, term):
                score = field_weight * alias_factor
                if score > best[0]:
                    best = (score, term, field_name)
    return best


def score_search_result(
    query: str,
    candidate: Dict,
    source_filter: Optional[str] = None,
    include_expired: bool = False,
    now: Optional[dt.datetime] = None,
) -> Optional[Dict]:
    """Score one candidate, returning None when it is not a valid result."""
    now = now or dt.datetime.now(tz=dt.timezone.utc)
    if candidate.get("provider") == "user_post" and (candidate.get("post_status") != "published" or candidate.get("post_kind", "recruitment") not in (None, "recruitment")):
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    concepts = build_query_concepts(query)
    if not concepts:
        return None

    evidence = [_concept_evidence(candidate, concept) for concept in concepts]
    if any(item[0] <= 0 for item in evidence):
        return None

    haystack = _candidate_haystack(candidate)
    opportunity_terms = _matched_opportunity_terms(haystack)
    # A member explicitly publishing in recruitment is an opportunity signal.
    if candidate.get("provider") == "user_post":
        opportunity_terms.append("community recruitment")
    source_filter = normalize_source_filter(source_filter)
    source_type = normalize_source_filter(candidate.get("source_type")) or detect_source_type(
        candidate.get("source", ""), candidate.get("source_url") or candidate.get("url") or candidate.get("link") or ""
    )
    if source_filter and source_type != source_filter:
        return None
    informational_mode = source_filter in INFORMATIONAL_FILTERS
    if source_type == "scholar" and source_filter != "scholar":
        return None
    if _profile_only(candidate, opportunity_terms):
        return None
    if not informational_mode and not opportunity_terms and candidate.get("has_direct_apply") not in (True, 1, "1"):
        return None

    expired = _deadline_is_expired(candidate, now)
    if expired and not include_expired and not informational_mode:
        return None

    matched_terms = []
    matched_fields = []
    for _, term, field_name in evidence:
        if term and term.lower() not in {value.lower() for value in matched_terms}:
            matched_terms.append(term)
        if field_name and field_name not in matched_fields:
            matched_fields.append(field_name)

    lexical = sum(item[0] for item in evidence) / len(evidence)
    lexical_score = min(35.0, lexical * 3.5)
    exact_query_bonus = 8.0 if text_contains_term(haystack, query) else 0.0
    opportunity_score = min(12.0, 5.0 + len(opportunity_terms) * 1.5) if opportunity_terms else 0.0
    source_score = float(SOURCE_RELIABILITY.get(source_type, 2))
    quality = min(max(_number(candidate.get("quality_score")), 0), 100)
    quality_score = quality * 0.05
    freshness_score = _freshness_score(candidate, now)

    url = canonicalize_source_url(candidate.get("source_url") or candidate.get("url") or candidate.get("link") or "")
    url_penalty = 0.0
    if "vertexaisearch.cloud.google.com/grounding-api-redirect" in url:
        url_penalty = 3.0

    score = (
        25.0
        + lexical_score
        + exact_query_bonus
        + opportunity_score
        + source_score
        + quality_score
        + freshness_score
        - url_penalty
        - (25.0 if expired else 0.0)
    )

    result = dict(candidate)
    result["quality_score"] = quality
    result["source_type"] = source_type
    result["source_url"] = url or candidate.get("source_url") or candidate.get("url")
    result["lexical_score"] = round(lexical_score + exact_query_bonus, 2)
    result["search_score"] = max(0, min(100, int(round(score))))
    result["match_score"] = result["search_score"]
    result["matched_terms"] = matched_terms
    result["matched_fields"] = matched_fields
    result["is_expired"] = expired
    result["match_reasons"] = [
        "all_concepts",
        *( ["exact_phrase"] if exact_query_bonus else [] ),
        *( ["opportunity_signal"] if opportunity_terms else [] ),
        *( ["trusted_source"] if source_score >= 8 else [] ),
        *( ["recent"] if freshness_score >= 3 else [] ),
    ]
    return result


def _url_key(candidate: Dict) -> str:
    url = canonicalize_source_url(candidate.get("source_url") or candidate.get("url") or candidate.get("link") or "")
    if not url:
        return ""
    parsed = urlsplit(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in {"xsec_token", "xsec_source"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(sorted(query, key=lambda pair: pair[0])), ""))


def _title_key(candidate: Dict) -> str:
    title = _text(candidate.get("title") or candidate.get("name")).lower()
    if not title:
        return ""
    title = re.sub(r"[^\w\u4e00-\u9fff]+", " ", title)
    organization = _text(candidate.get("university") or candidate.get("institution")).lower()
    if not organization:
        # Equal titles alone do not establish identity across unknown institutions.
        return ""
    return f"{' '.join(title.split())}|{' '.join(organization.split())}"


def deduplicate_results(candidates: Iterable[Dict]) -> List[Dict]:
    """Keep the strongest item for each canonical URL or title/organization pair."""
    group_by_key = {}
    groups = {}
    for index, candidate in enumerate(candidates):
        url_key = _url_key(candidate)
        title_key = _title_key(candidate)
        keys = [key for key in (
            f"url:{url_key}" if url_key else "",
            f"title:{title_key}" if title_key else "",
        ) if key]
        matches = {group_by_key[key] for key in keys if key in group_by_key}
        # Merge smaller duplicate groups into the largest so bridge records join
        # every alias without repeatedly scanning all previous results.
        group_id = max(matches, key=lambda key: (len(groups[key][1]), -key)) if matches else index
        group = groups.setdefault(group_id, [candidate, set(), set()])
        prior_sources = candidate.get("matched_sources")
        sources = prior_sources if isinstance(prior_sources, (list, tuple, set)) else []
        for source in [candidate.get("source_type"), *sources]:
            normalized_source = normalize_source_filter(source)
            if normalized_source:
                group[2].add(normalized_source)
        contenders = [group[0], candidate]
        for matched_id in matches - {group_id}:
            other = groups.pop(matched_id)
            contenders.append(other[0])
            keys.extend(other[1])
            group[2].update(other[2])
        group[0] = max(contenders, key=lambda row: (
            _number(row.get("search_score", row.get("quality_score", 0))),
            _number(row.get("quality_score")),
        ))
        group[1].update(keys)
        for key in keys:
            group_by_key[key] = group_id
    return [dict(group[0], matched_sources=sorted(group[2])) for group in groups.values()]


def rank_search_results(
    query: str,
    candidates: Iterable[Dict],
    source_filter: Optional[str] = None,
    limit: Optional[int] = None,
    include_expired: bool = False,
    now: Optional[dt.datetime] = None,
) -> List[Dict]:
    scored = []
    for candidate in candidates:
        result = score_search_result(
            query,
            candidate,
            source_filter=source_filter,
            include_expired=include_expired,
            now=now,
        )
        if result:
            scored.append(result)

    scored = deduplicate_results(scored)
    scored.sort(
        key=lambda item: (
            _number(item.get("search_score")),
            _parse_date(item.get("published_at")) or dt.datetime.min.replace(tzinfo=dt.timezone.utc),
            _number(item.get("quality_score")),
        ),
        reverse=True,
    )
    return scored[:limit] if limit is not None else scored
