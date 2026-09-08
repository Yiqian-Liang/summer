"""
Scheduler for periodic data collection from all sources.
Runs scraping jobs and stores results in the opportunities database.
"""

import logging
import datetime
import json
import time
from typing import List, Dict, Optional
import os
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from app.scrapers import AcademicScraper
from app.matching import matcher
from app.agents import ResearchAgent
from app.database import (
    init_db, get_db, upsert_opportunity, 
    generate_opportunity_id, get_opportunity_count, is_real_source_url
)
from app.search_utils import canonicalize_source_url, detect_source_type, text_contains_term


class OpportunityProcessor:
    """
    Processes raw scraped data into standardized opportunities
    with quality scores and embeddings.
    """
    
    def __init__(self):
        self.agent = ResearchAgent()

    @staticmethod
    def content_text(item: Dict) -> str:
        """Only source content is evidence; discovery queries and URLs are not."""
        return " ".join(str(item.get(field) or "") for field in (
            "title", "name", "description", "snippet", "interests", "requirements",
        )).lower()
    
    def calculate_quality_score(self, item: Dict) -> int:
        """
        Calculates a quality score (0-100) based on hiring signals,
        source reliability, and content completeness.
        """
        score = 50  # Base score
        
        text = self.content_text(item)
        
        # Hiring signal boosts
        hiring_keywords = {
            "hiring": 15,
            "openings": 15,
            "looking for": 12,
            "position available": 15,
            "phd position": 12,
            "summer research": 10,
            "summer intern": 10,
            "reu": 10,
            "funded": 10,
            "暑研": 12,
            "科研实习": 12,
            "博士招生": 15,
            "课题组招生": 15,
            "招募": 12,
        }
        
        for keyword, boost in hiring_keywords.items():
            if text_contains_term(text, keyword):
                score += boost
                break  # Only apply one hiring boost
        
        # Funding signal boosts
        funding_keywords = ["nsf", "grant", "funding", "fellowship", "stipend", "paid"]
        if any(text_contains_term(text, k) for k in funding_keywords):
            score += 10
        
        # New lab/assistant professor boost
        if "assistant professor" in text or "new lab" in text:
            score += 8
        
        # Source reliability
        source = f"{item.get('source', '')} {item.get('source_type', '')}".lower()
        source_scores = {
            "nsf reu": 15,
            "university": 12,
            "professor_site": 12,
            "academicjobs": 10,
            "euraxess": 10,
            "findaphd": 9,
            "mediacrawler": 6,
            "linkedin": 5,
            "wechat": 3,
            "xiaohongshu": 3,
            "zhihu": 2,
            "bilibili": 2,
            "twitter": 2,
            "google scholar": -5,
        }
        for src_key, src_boost in source_scores.items():
            if src_key in source:
                score += src_boost
                break
        
        # Content completeness
        if item.get("professor_name") or item.get("name"):
            score += 5
        if item.get("university") or item.get("institution"):
            score += 3
        if item.get("description") or item.get("snippet") or item.get("interests"):
            score += 3
        
        # Cap at 100
        return min(score, 100)
    
    def extract_hiring_signals(self, item: Dict) -> List[str]:
        """
        Extracts human-readable hiring signals from the item.
        """
        signals = []
        text = self.content_text(item)
        
        if any(text_contains_term(text, k) for k in ("nsf", "grant", "funding")):
            signals.append("Recent Funding Detected (High Hiring Probability)")
        
        if "assistant professor" in text or "new lab" in text:
            signals.append("New Lab / Assistant Professor (Likely seeking students)")
        
        if any(text_contains_term(text, k) for k in ["hiring", "looking for", "openings", "position", "招生", "招募"]):
            signals.append("Explicitly Hiring")
        
        if any(text_contains_term(text, k) for k in ["reu", "summer research", "summer intern", "暑研", "科研实习"]):
            signals.append("Summer Research Program")
        
        if any(text_contains_term(text, k) for k in ["stipend", "paid", "fellowship"]):
            signals.append("Funded Position")
        
        return signals
    
    def detect_direct_apply(self, item: Dict) -> tuple:
        """
        Detects if there's a direct application form link.
        Returns (has_direct_apply: bool, link: str or None)
        """
        text = json.dumps(item)
        form_link = self.agent.detect_application_form(text)
        if form_link:
            return (True, form_link)
        return (False, None)
    
    def normalize_item(self, item: Dict, source_type: str) -> Dict:
        """
        Normalizes a scraped item into the standard opportunity format.
        """
        # Determine source URL
        source_url = canonicalize_source_url(
            item.get("url") or 
            item.get("recent_paper") or 
            item.get("link") or 
            item.get("website")
        )
        if not is_real_source_url(source_url):
            raise ValueError("Skipping item without a real source URL.")
        
        # Extract title
        title = item.get("title") or item.get("name")
        if not title:
            raise ValueError("Skipping item without a source-provided title.")
        
        # Extract description
        description = (
            item.get("description") or 
            item.get("snippet") or 
            item.get("interests") or 
            ""
        )
        
        # Stored embeddings were not consumed by the search path and made every
        # import pay model-startup cost. Keep them opt-in for offline experiments.
        embedding_json = None
        if os.getenv("STORE_EMBEDDINGS", "").lower() in {"1", "true", "yes"}:
            text_for_embedding = f"{title} {description}"
            try:
                embedding = matcher.encode_text(text_for_embedding)
                embedding_json = json.dumps(embedding) if embedding else None
            except Exception as e:
                logger.warning(f"Failed to generate embedding: {e}")
        
        # Detect direct apply
        has_apply, apply_link = self.detect_direct_apply(item)
        
        # Calculate quality and signals
        scored_item = {**item, "source_type": source_type}
        quality_score = self.calculate_quality_score(scored_item)
        hiring_signals = self.extract_hiring_signals(item)
        
        now = datetime.datetime.now().isoformat()
        
        return {
            "title": str(title)[:500],  # Truncate long titles
            "source_type": source_type,
            "source_url": source_url,
            "professor_name": item.get("professor_name") or item.get("professor") or item.get("author") or "",
            "university": item.get("university") or item.get("institution") or "",
            "department": item.get("department") or "",
            "description": str(description)[:2000],  # Truncate long descriptions
            "requirements": str(item.get("requirements") or "")[:1000],
            "embedding": embedding_json,
            "hiring_signals": json.dumps(hiring_signals),
            "has_direct_apply": 1 if has_apply else 0,
            "direct_apply_link": apply_link or "",
            "deadline": str(item.get("deadline") or "")[:200],
            "published_at": str(
                item.get("published_at") or item.get("created_time") or item.get("time") or ""
            )[:100],
            "provider": str(item.get("provider") or item.get("search_provider") or "")[:50],
            "source_query": str(item.get("source_query") or "")[:500],
            "quality_score": quality_score,
            "created_at": now,
            "updated_at": now,
        }
    
    def process_scholar_results(self, items: List[Dict]) -> int:
        """Processes and stores Google Scholar results."""
        count = 0
        for item in items:
            try:
                normalized = self.normalize_item(item, "scholar")
                if upsert_opportunity(normalized):
                    count += 1
            except Exception as e:
                logger.error(f"Error processing scholar item: {e}")
        return count
    
    def process_social_results(self, items: List[Dict], platform: str) -> int:
        """Processes and stores social media results."""
        count = 0
        for item in items:
            try:
                normalized = self.normalize_item(item, platform)
                if upsert_opportunity(normalized):
                    count += 1
            except Exception as e:
                logger.error(f"Error processing {platform} item: {e}")
        return count
    
    def process_nsf_results(self, items: List[Dict]) -> int:
        """Processes and stores NSF REU results."""
        count = 0
        for item in items:
            try:
                normalized = self.normalize_item(item, "nsf")
                if upsert_opportunity(normalized):
                    count += 1
            except Exception as e:
                logger.error(f"Error processing NSF item: {e}")
        return count


class DataCollectionScheduler:
    """
    Manages scheduled data collection jobs.
    """

    # WeChat article keywords collected through Sogou (no login or API key).
    WECHAT_DEFAULT_QUERIES = [
        "暑研",
        "暑期科研",
        "科研实习",
        "暑研申请",
        "课题组招生",
        "博士招生",
        "科研助理",
    ]

    # Default search queries for broad coverage; dates are set when a job starts.
    DEFAULT_QUERIES = [
        "summer research {year}",
        "summer research {next_year}",
        "PhD student position",
        "research intern",
        "暑研",
        "科研实习",
        "NLP machine learning",
        "computer vision",
        "robotics",
        "artificial intelligence research",
        "reinforcement learning",
        "large language models",
        "generative ai",
        "computational biology",
        "human computer interaction",
        "software engineering research",
        "systems research",
        "data science internship",
        "machine learning engineer intern",
        "deep learning research",
        "autonomous driving research",
        "medical image analysis",
        "natural language processing",
        "computer graphics research",
        "security and privacy research",
        "blockchain research",
        "quantum computing research",
        "bioinformatics research",
        "neuroscience research",
        "cognitive science research",
        "psychology research assistant",
        "economics research assistant",
        "finance research assistant",
        "quantitative research intern",
        "algorithmic trading intern",
        "policy research intern",
        "sociology research",
        "political science research",
        "history research assistant",
        "digital humanities research",
        "climate change research",
        "environmental science research",
        "sustainable energy research",
        "materials science research",
        "nanotechnology research",
        "chemical engineering research",
        "biomedical engineering research",
        "electrical engineering research",
        "mechanical engineering research",
        "civil engineering research",
        "aerospace engineering research",
    ]
    
    def __init__(self, queries: List[str] = None):
        year = datetime.datetime.now().year
        self.queries = queries if queries is not None else [
            query.format(year=year, next_year=year + 1) for query in self.DEFAULT_QUERIES
        ]
        self.processor = OpportunityProcessor()
        self.scraper = AcademicScraper()
    
    def run_collection_job(self, query: str = None) -> Dict:
        """
        Runs a single collection job for a given query.
        If no query provided, runs for all default queries.
        """
        queries_to_run = [query] if query else self.queries
        
        stats = {
            "queries_processed": 0,
            "total_new": 0,
            "by_source": {},
            "started_at": datetime.datetime.now().isoformat(),
        }
        
        for q in queries_to_run:
            logger.info(f"Running collection for query: {q}")
            
            try:
                # Collect from all sources
                results = self.scraper.search_all_sources(q)
                
                # Process results
                for item in results:
                    source = item.get("source", "unknown").lower()
                    source_type = item.get("source_type") or detect_source_type(
                        source,
                        item.get("url") or item.get("recent_paper") or item.get("link") or "",
                    )
                    
                    try:
                        normalized = self.processor.normalize_item(item, source_type)
                        if upsert_opportunity(normalized):
                            stats["total_new"] += 1
                            stats["by_source"][source_type] = stats["by_source"].get(source_type, 0) + 1
                    except Exception as e:
                        logger.error(f"Error processing item: {e}")
                
                stats["queries_processed"] += 1
                
                # Rate limiting between queries
                time.sleep(1)
                
            except Exception as e:
                logger.error(f"Error in collection job for query '{q}': {e}")
        
        stats["completed_at"] = datetime.datetime.now().isoformat()
        stats["total_in_db"] = get_opportunity_count()
        
        logger.info(f"Collection job completed: {stats}")
        return stats
    
    def run_nsf_collection(self) -> Dict:
        """
        Specifically collects from NSF REU listings.
        """
        logger.info("Running NSF REU collection...")
        stats = {"source": "nsf", "new": 0}
        
        try:
            results = self.scraper.nsf_scraper.scrape_listings()
            stats["new"] = self.processor.process_nsf_results(results)
        except Exception as e:
            logger.error(f"NSF collection error: {e}")
        
        return stats

    def run_wechat_collection(self, keywords: List[str] = None) -> Dict:
        """
        Collects WeChat official-account articles through Sogou's WeChat index.

        Needs no login and no API key, but Sogou rate-limits aggressively:
        requests are spaced out and the job stops early when Sogou starts
        returning nothing (a sign the antispider wall kicked in).
        """
        queries = keywords or self.WECHAT_DEFAULT_QUERIES
        sogou = self.scraper.sogou_scraper
        stats = {"source": "wechat", "queries_processed": 0, "new": 0, "rate_limited": False}

        if not sogou.enabled():
            logger.info("Sogou WeChat search disabled (ENABLE_SOGOU_WECHAT=0).")
            return stats

        for query in queries:
            try:
                results = sogou.search_wechat(query)
            except Exception as e:
                logger.error(f"WeChat collection error for '{query}': {e}")
                results = []
            if not results:
                # An empty page after warm-up usually means Sogou is
                # rate-limiting; continuing just hammers the wall.
                logger.warning(f"No WeChat results for '{query}'; stopping early.")
                stats["rate_limited"] = True
                break
            stats["new"] += self.processor.process_social_results(results, "wechat")
            stats["queries_processed"] += 1
            time.sleep(3)

        return stats


def run_full_collection():
    """
    Convenience function to run a full collection job.
    Can be called from command line or cron.
    """
    # Ensure DB is initialized
    init_db()
    
    scheduler = DataCollectionScheduler()
    
    # Run main collection
    stats = scheduler.run_collection_job()
    
    # Also run NSF collection
    nsf_stats = scheduler.run_nsf_collection()
    stats["nsf"] = nsf_stats

    # WeChat articles need no API key, so this also works key-free
    stats["wechat"] = scheduler.run_wechat_collection()
    
    return stats


if __name__ == "__main__":
    # Run collection when called directly
    print("Starting data collection...")
    stats = run_full_collection()
    print(f"\nCollection complete!")
    print(f"New opportunities added: {stats['total_new']}")
    print(f"Total in database: {stats.get('total_in_db', 'unknown')}")
    print(f"By source: {stats.get('by_source', {})}")
