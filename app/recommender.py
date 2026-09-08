"""
Recommendation engine for personalized opportunity ranking.
Combines user profile preferences and behavior history for intelligent sorting.
"""

import json
import logging
from typing import Dict, List, Optional
from collections import Counter

from app.search_utils import text_contains_term

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


class Recommender:
    """Personalized recommendation engine."""
    
    def __init__(self):
        pass
    
    def calculate_preference_score(self, opportunity: Dict, profile: Dict) -> float:
        """
        Calculate how well an opportunity matches user preferences.
        Returns a score from 0-100.
        """
        if not profile:
            return 50.0  # Neutral score for anonymous users
        
        score = 50.0  # Base score
        text = self._get_searchable_text(opportunity).lower()
        
        # 1. Research field matching (up to +25 points)
        research_fields = self._parse_json_field(profile.get("research_fields"))
        field_matches = 0
        for field in research_fields:
            keywords = FIELD_KEYWORDS.get(field, [])
            if any(text_contains_term(text, kw) for kw in keywords):
                field_matches += 1
        if research_fields:
            score += min(25, (field_matches / len(research_fields)) * 25)
        
        # 2. Major matching (up to +15 points)
        majors = self._parse_json_field(profile.get("majors"))
        major_matches = 0
        for major in majors:
            keywords = MAJOR_KEYWORDS.get(major, [])
            if any(text_contains_term(text, kw) for kw in keywords):
                major_matches += 1
        if majors:
            score += min(15, (major_matches / len(majors)) * 15)
        
        # 3. Target type matching (up to +20 points)
        target_types = self._parse_json_field(profile.get("target_types"))
        target_matches = 0
        for target in target_types:
            keywords = TARGET_KEYWORDS.get(target, [])
            if any(text_contains_term(text, kw) for kw in keywords):
                target_matches += 1
        if target_types:
            score += min(20, (target_matches / len(target_types)) * 20)
        
        # 4. Region matching (up to +10 points)
        regions = self._parse_json_field(profile.get("region_preferences"))
        if "any" not in regions:
            region_matches = 0
            for region in regions:
                keywords = REGION_KEYWORDS.get(region, [])
                if any(text_contains_term(text, kw) for kw in keywords):
                    region_matches += 1
            if regions:
                score += min(10, (region_matches / len(regions)) * 10)
        else:
            score += 5  # Partial bonus for "any" region
        
        return min(100, score)
    
    def calculate_behavior_score(self, opportunity: Dict, behaviors: List[Dict]) -> float:
        """
        Calculate score boost based on user's past behavior.
        Returns a score modifier from -10 to +20.
        """
        if not behaviors:
            return 0.0
        
        text = self._get_searchable_text(opportunity).lower()
        score_modifier = 0.0
        
        # Analyze search history
        searches = [b for b in behaviors if b.get("action_type") == "search"]
        search_terms = [b.get("target_id", "").lower() for b in searches[:20]]  # History is newest first.
        
        # Count keyword frequency in searches
        for term in search_terms:
            if term and text_contains_term(text, term):
                score_modifier += 2.0  # Bonus for matching recent search
        
        # Analyze click history
        clicks = [b for b in behaviors if b.get("action_type") == "click"]
        # Could analyze clicked opportunities to find patterns
        
        # Analyze saves
        saves = [b for b in behaviors if b.get("action_type") == "save"]
        # Could boost similar opportunities to saved ones
        
        return min(20, max(-10, score_modifier))
    
    def rank_opportunities(
        self, 
        opportunities: List[Dict], 
        profile: Optional[Dict] = None,
        behaviors: Optional[List[Dict]] = None
    ) -> List[Dict]:
        """
        Rank opportunities based on user profile and behavior.
        Returns opportunities sorted by personalized score.
        """
        if not opportunities:
            return []
        
        for opp in opportunities:
            # Start with quality score
            base_score = opp.get("quality_score", 50)
            
            # Add preference score
            pref_score = self.calculate_preference_score(opp, profile) if profile else 50
            
            # Add behavior score
            behav_score = self.calculate_behavior_score(opp, behaviors) if behaviors else 0
            
            # Calculate final personalized score
            # Weight: 40% quality, 40% preference, 20% behavior
            opp["personalized_score"] = (
                base_score * 0.4 + 
                pref_score * 0.4 + 
                (50 + behav_score) * 0.2
            )
        
        # Sort by personalized score
        return sorted(opportunities, key=lambda x: x.get("personalized_score", 0), reverse=True)
    
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
        ]
        return " ".join(str(f) for f in fields if f)
    
    def _parse_json_field(self, field) -> List[str]:
        """Parse a JSON field that might be a string or already a list."""
        if not field:
            return []
        if isinstance(field, list):
            return field
        try:
            return json.loads(field)
        except:
            return []


# Singleton instance
recommender = Recommender()
