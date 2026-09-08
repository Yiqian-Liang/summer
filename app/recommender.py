"""
Recommendation engine for personalized opportunity ranking.
Combines user profile preferences and behavior history for intelligent sorting.
"""

import json
import logging
import math
from functools import lru_cache
from datetime import datetime, timezone
from typing import Dict, List, Optional
from collections import Counter

from app.search_utils import text_contains_term
from app.search_ranking import is_available_opportunity, deduplicate_results, SOURCE_RELIABILITY, _parse_date, _number, _url_key, _title_key

logger = logging.getLogger(__name__)

# Keyword mappings for matching
FIELD_KEYWORDS = {
    "nlp": ["nlp", "natural language", "语言处理", "text", "language model", "llm", "chatgpt", "gpt", "bert", "transformer"],
    "cv": ["computer vision", "cv", "image", "视觉", "object detection", "segmentation", "recognition", "cnn", "图像"],
    "ml": ["machine learning", "ml", "机器学习", "deep learning", "neural network", "learning", "模型"],
    "robotics": ["robot", "robotics", "robotic", "机器人", "manipulation", "motion planning", "control", "autonomous"],
    "systems": ["system", "系统", "distributed", "operating system", "database", "storage", "kernel"],
    "theory": ["theory", "理论", "algorithm", "complexity", "optimization", "math"],
    "hci": ["hci", "human computer", "人机交互", "interaction", "user interface", "ux", "accessibility"],
    "security": ["security", "安全", "privacy", "cryptography", "malware", "vulnerability"],
    "graphics": ["graphics", "图形", "rendering", "3d", "animation", "visualization", "game"],
    "database": ["database", "数据库", "sql", "query", "data management", "storage"],
    "networks": ["network", "网络", "wireless", "protocol", "internet", "5g", "communication"],
    "ai_general": ["ai", "artificial intelligence", "人工智能", "intelligent", "agent"],
    "bioinformatics": ["bio", "biology", "bioinformatics", "biomedical", "生物", "genomics", "protein", "medical", "health", "drug"],
    "quantum": ["quantum", "量子", "qubit"],
}

MAJOR_KEYWORDS = {
    "cs": ["computer science", "cs", "计算机", "software", "programming", "coding"],
    "ee": ["electrical", "ee", "电子", "电气", "circuit", "signal", "embedded"],
    "math": ["math", "mathematics", "数学", "algebra", "calculus", "geometry", "statistics"],
    "physics": ["physics", "物理", "quantum", "mechanics", "particle"],
    "biology": ["biology", "生物", "cell", "genetics", "molecular"],
    "chemistry": ["chemistry", "化学", "molecular", "reaction", "synthesis"],
    "me": ["mechanical", "机械", "engineering", "design", "manufacturing"],
    "stats": ["statistics", "统计", "probability", "bayesian", "inference"],
    "economics": ["economics", "经济", "finance", "market", "policy"],
    "psychology": ["psychology", "心理", "cognitive", "behavior", "brain"],
}

REGION_KEYWORDS = {
    "north_america": ["usa", "us", "united states", "canada", "america", "stanford", "mit", "berkeley", "cmu", "harvard", "princeton"],
    "europe": ["uk", "germany", "france", "switzerland", "netherlands", "europe", "oxford", "cambridge", "eth", "epfl"],
    "asia": ["china", "japan", "korea", "singapore", "hong kong", "tsinghua", "peking", "中国", "日本", "韩国"],
}

TARGET_KEYWORDS = {
    "summer_research": ["summer", "暑期", "暑研", "intern", "internship", "实习", "undergraduate research"],
    "phd": ["phd", "doctoral", "博士", "graduate student", "ph.d"],
    "intern": ["intern", "internship", "实习", "research assistant", "ra position"],
    "visiting": ["visiting", "访问", "exchange", "交换"],
}


@lru_cache(maxsize=512)
def _posting_topics(text):
    """Cache only public posting text and fixed topic labels, never personal inputs."""
    from app.resumes import KEYWORDS, evidence
    return frozenset(word for word in KEYWORDS if evidence(text, word))


class Recommender:
    """Personalized recommendation engine."""
    
    def calculate_preference_score(self, opportunity: Dict, profile: Dict) -> float:
        """
        Calculate how well an opportunity matches user preferences.
        Returns a score from 0-100.
        """
        if not profile:
            return 50.0  # Neutral score for anonymous users
        from app.resumes import TOPIC_NAMES, evidence
        
        score = 50.0  # Base score
        text = self._get_searchable_text(opportunity).lower()
        
        # 1. Research field matching (up to +25 points)
        research_fields = self._parse_json_field(profile.get("research_fields"))
        field_matches = 0
        for field in research_fields:
            if field in TOPIC_NAMES and evidence(text, TOPIC_NAMES[field]):
                field_matches += 1
        if research_fields:
            score += min(25, (field_matches / len(research_fields)) * 25)
        
        # 2. Major matching (up to +15 points)
        majors = self._parse_json_field(profile.get("majors"))
        major_matches = 0
        for major in majors:
            keywords = MAJOR_KEYWORDS.get(major, [])
            if any(evidence(text, kw) for kw in keywords):
                major_matches += 1
        if majors:
            score += min(15, (major_matches / len(majors)) * 15)
        
        # 3. Target type matching (up to +20 points)
        target_types = self._parse_json_field(profile.get("target_types"))
        target_matches = 0
        for target in target_types:
            keywords = TARGET_KEYWORDS.get(target, [])
            if any(evidence(text, kw) for kw in keywords):
                target_matches += 1
        if target_types:
            score += min(20, (target_matches / len(target_types)) * 20)
        
        # 4. Region matching (up to +10 points)
        regions = self._parse_json_field(profile.get("region_preferences"))
        if "any" not in regions:
            region_matches = 0
            for region in regions:
                keywords = REGION_KEYWORDS.get(region, [])
                if any(evidence(text, kw) for kw in keywords):
                    region_matches += 1
            if regions:
                score += min(10, (region_matches / len(regions)) * 10)
        
        return min(100, score)
    
    def calculate_behavior_score(self, opportunity, behaviors, now=None):
        """Recent distinct queries; old or repeated searches cannot accumulate unlimited weight."""
        now = now or datetime.now(timezone.utc)
        text = self._get_searchable_text(opportunity)
        seen, score = set(), 0.0
        searches = [item for item in (behaviors or []) if item.get("action_type") == "search"][:20]
        for item in searches:
            term = str(item.get("target_id") or "").strip().casefold()
            age = max(0, (now - (_parse_date(item.get("created_at")) or now)).total_seconds() / 86400)
            if term and term not in seen and age <= 90 and text_contains_term(text, term):
                score += 2 * 2 ** (-age / 30)
            seen.add(term)
        return min(20, score)

    def rank_opportunities(self, opportunities, profile=None, behaviors=None, resume=None,
                           saved=None, hidden=None, personal=True, now=None):
        """Local content/feedback ranking. Weights are a baseline, not trained admission odds."""
        # Local import reuses reviewed skill aliases without a module import cycle.
        from app.resumes import KEYWORDS, TOPIC_NAMES, evidence, match_resume
        now = now or datetime.now(timezone.utc)
        hidden = set(hidden or [])
        hidden_urls = {_url_key(row) for row in opportunities if row.get("id") in hidden} - {""}
        hidden_titles = {_title_key(row) for row in opportunities if row.get("id") in hidden} - {""}
        available = [dict(row) for row in opportunities if is_available_opportunity(row, now=now)]
        for row in available:
            row["quality_score"] = min(100, max(0, _number(row.get("quality_score"))))
        by_id = {row["id"]: row for row in available}
        rows = deduplicate_results([row for row in available if row["id"] not in hidden
            and _url_key(row) not in hidden_urls and _title_key(row) not in hidden_titles])
        if not personal:
            return sorted(rows, key=lambda row: (_parse_date(row.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
                _number(row.get("quality_score")), str(row.get("id"))), reverse=True)

        # ponytail: 512 public text entries, at most 20000 characters each; use a persistent feature index when the catalog outgrows this cache.
        keywords = list(dict.fromkeys([*KEYWORDS, *((resume or {}).get("keywords") or [])])) if profile or resume or behaviors or saved else []
        features = {}
        for key, row in by_id.items():
            text = "\n".join(str(row.get(field) or "") for field in ("title", "description", "requirements"))
            public_topics = (_posting_topics(text) if len(text) <= 20000 else _posting_topics.__wrapped__(text)) if keywords else frozenset()
            features[key] = set(public_topics) | {word for word in keywords if word not in KEYWORDS and evidence(text, word)}
        frequency = Counter(word for row in rows for word in features[row["id"]])
        idf = {word: 1 + math.log((1 + len(rows)) / (1 + frequency[word])) for word in keywords}
        interest, saved_topics, click_topics, seen = Counter(), set(), set(), set()
        confidence = 0.0
        saved_ids = {item["opportunity_id"] for item in (saved or [])[:50]}
        seeds = [(item, 4.0, saved_topics) for item in sorted(saved_ids)]
        for item in behaviors or []:
            if item.get("action_type") != "click" or item.get("target_id") in saved_ids:
                continue
            age = max(0, (now - (_parse_date(item.get("created_at")) or now)).total_seconds() / 86400)
            if age <= 90:
                seeds.append((item.get("target_id"), 2 ** (-age / 30), click_topics))
        for target, weight, bucket in seeds:
            row = by_id.get(target)
            if not row or target in hidden:
                continue
            key = _url_key(row) or target
            if key in seen:
                continue
            seen.add(key)
            words = features[target]
            bucket.update(words)
            if words:
                confidence += weight
            for word in words:
                interest[word] += weight / max(1, len(words))
        interest_total = sum(interest[word] * idf[word] for word in interest)
        resume_total = sum(idf[word] for word in (resume or {}).get("keywords", []))
        ranked = []
        for row in rows:
            words = features[row["id"]]
            match = match_resume(row, resume) if resume else None
            resume_score = sum(idf[word] for word in match["matched_keywords"]) / resume_total if resume_total else 0
            preference = max(0, self.calculate_preference_score(row, profile) - 50) / 50 if profile else 0
            activity = sum(interest[word] * idf[word] for word in words) / interest_total if interest_total else 0
            activity *= min(1, confidence / 8)
            search = self.calculate_behavior_score(row, behaviors, now) / 20
            published = _parse_date(row.get("published_at"))
            fresh = 2 ** (-max(0, (now - published).days) / 60) if published else 0
            components = {"resume": 35 * resume_score, "preferences": 25 * preference,
                "saved_and_browsed": 15 * activity, "searches": 5 * search,
                "source": 8 * SOURCE_RELIABILITY.get(row.get("source_type"), 2) / 10,
                "quality": 7 * min(100, max(0, _number(row.get("quality_score")))) / 100,
                "freshness": 5 * fresh}
            reasons = []
            if match and match["count"]:
                row["resume_match"] = match
                reasons.append({"kind": "resume", "terms": match["matched_keywords"][:3]})
            preferred = [TOPIC_NAMES[key] for key in self._parse_json_field((profile or {}).get("research_fields"))
                if key in TOPIC_NAMES and TOPIC_NAMES[key] in words]
            if preferred:
                reasons.append({"kind": "preferences", "terms": preferred[:3]})
            elif preference:
                reasons.append({"kind": "goals", "terms": []})
            for kind, bucket in (("saved", saved_topics), ("browsed", click_topics)):
                overlap = sorted(words & bucket, key=lambda word: (-interest[word] * idf[word], word))
                if overlap:
                    reasons.append({"kind": kind, "terms": overlap[:3]})
            if search:
                reasons.append({"kind": "search", "terms": []})
            if not reasons:
                reasons.append({"kind": "discovery", "terms": []})
            row.update(personalized_score=round(sum(components.values()), 6),
                recommendation_components=components, recommendation_reasons=reasons, recommendation_version="content-feedback-v1")
            ranked.append(row)
        ranked.sort(key=lambda row: (-row["personalized_score"], str(row.get("id"))))
        # ponytail: diversify the first 200, looking ahead only 20 near-scoring items; the full tail remains pageable.
        queue, result = ranked[:200], []
        while queue:
            recent = result[-5:]
            best = queue[0]["personalized_score"]
            options = [(i, row) for i, row in enumerate(queue[:20]) if row["personalized_score"] >= best - 8]
            index, selected = max(options, key=lambda pair: (pair[1]["personalized_score"]
                - 3 * sum(bool(pair[1].get("university")) and str(prev.get("university", "")).casefold() == str(pair[1]["university"]).casefold() for prev in recent)
                - sum(prev.get("source_type") == pair[1].get("source_type") for prev in recent), -pair[0]))
            result.append(queue.pop(index))
        return result + ranked[200:]

    def get_recommended_searches(self, profile: Dict) -> List[str]:
        """
        Generate recommended search queries based on user profile.
        """
        suggestions = []
        
        research_fields = self._parse_json_field(profile.get("research_fields"))
        target_types = self._parse_json_field(profile.get("target_types"))
        
        # Generate suggestions based on fields + targets
        field_names = {
            "nlp": "NLP",
            "cv": "Computer Vision",
            "ml": "Machine Learning",
            "robotics": "Robotics",
            "ai_general": "AI",
        }
        
        target_names = {
            "summer_research": "summer research",
            "phd": "PhD position",
            "intern": "research intern",
        }
        
        for field in research_fields[:3]:
            field_name = field_names.get(field, field)
            for target in target_types[:2]:
                target_name = target_names.get(target, target)
                suggestions.append(f"{field_name} {target_name}")
        
        return suggestions[:6]  # Return top 6 suggestions
    
    def _get_searchable_text(self, opportunity: Dict) -> str:
        """Combine all searchable fields into one string."""
        fields = [
            opportunity.get("title", ""),
            opportunity.get("description", ""),
            opportunity.get("professor_name", ""),
            opportunity.get("university", ""),
            opportunity.get("department", ""),
            opportunity.get("requirements", ""),
        ]
        return "\n".join(str(f) for f in fields if f)
    
    def _parse_json_field(self, field) -> List[str]:
        """Parse a JSON field that might be a string or already a list."""
        if not field:
            return []
        if isinstance(field, list):
            return field
        try:
            value = json.loads(field)
            return value if isinstance(value, list) else []
        except (TypeError, ValueError):
            return []


# Singleton instance
recommender = Recommender()
