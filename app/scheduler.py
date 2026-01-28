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
    generate_opportunity_id, get_opportunity_count
)


class OpportunityProcessor:
    """
    Processes raw scraped data into standardized opportunities
    with quality scores and embeddings.
    """
    
    def __init__(self):
        self.agent = ResearchAgent()
        self.scraper = AcademicScraper()
    
    def calculate_quality_score(self, item: Dict) -> int:
        """
        Calculates a quality score (0-100) based on hiring signals,
        source reliability, and content completeness.
        """
        score = 50  # Base score
        
        text = json.dumps(item).lower()
        
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
        }
        
        for keyword, boost in hiring_keywords.items():
            if keyword in text:
                score += boost
                break  # Only apply one hiring boost
        
        # Funding signal boosts
        funding_keywords = ["nsf", "grant", "funding", "fellowship", "stipend", "paid"]
        if any(k in text for k in funding_keywords):
            score += 10
        
        # New lab/assistant professor boost
        if "assistant professor" in text or "new lab" in text:
            score += 8
        
        # Source reliability
        source = item.get("source", "").lower()
        source_scores = {
            "nsf reu": 15,
            "google scholar": 10,
            "linkedin": 5,
            "university": 8,
            "wechat": 3,
            "xiaohongshu": 3,
            "twitter": 2,
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
        text = json.dumps(item).lower()
        
        if "nsf" in text or "grant" in text or "funding" in text:
            signals.append("Recent Funding Detected (High Hiring Probability)")
        
        if "assistant professor" in text or "new lab" in text:
            signals.append("New Lab / Assistant Professor (Likely seeking students)")
        
        if any(k in text for k in ["hiring", "looking for", "openings", "position"]):
            signals.append("Explicitly Hiring")
        
        if any(k in text for k in ["reu", "summer research", "summer intern"]):
            signals.append("Summer Research Program")
        
        if any(k in text for k in ["stipend", "paid", "fellowship"]):
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
        source_url = (
            item.get("url") or 
            item.get("recent_paper") or 
            item.get("link") or 
            item.get("website") or
            f"unknown://{hash(json.dumps(item))}"
        )
        
        # Extract title
        title = (
            item.get("title") or 
            item.get("name") or 
            f"{item.get('professor_name', 'Research')} at {item.get('university', 'University')}"
        )
        
        # Extract description
        description = (
            item.get("description") or 
            item.get("snippet") or 
            item.get("interests") or 
            ""
        )
        
        # Generate embedding
        text_for_embedding = f"{title} {description}"
        try:
            embedding = matcher.encode_text(text_for_embedding)
            embedding_json = json.dumps(embedding) if embedding else None
        except Exception as e:
            logger.warning(f"Failed to generate embedding: {e}")
            embedding_json = None
        
        # Detect direct apply
        has_apply, apply_link = self.detect_direct_apply(item)
        
        # Calculate quality and signals
        quality_score = self.calculate_quality_score(item)
        hiring_signals = self.extract_hiring_signals(item)
        
        now = datetime.datetime.now().isoformat()
        
        return {
            "title": title[:500],  # Truncate long titles
            "source_type": source_type,
            "source_url": source_url,
            "professor_name": item.get("professor_name") or item.get("name") or item.get("author"),
            "university": item.get("university") or item.get("institution") or "",
            "department": item.get("department") or "",
            "description": description[:2000],  # Truncate long descriptions
            "embedding": embedding_json,
            "hiring_signals": json.dumps(hiring_signals),
            "has_direct_apply": 1 if has_apply else 0,
            "direct_apply_link": apply_link or "",
            "deadline": item.get("deadline"),
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
    
    # Default search queries for broad coverage
    DEFAULT_QUERIES = [
        "summer research 2025",
        "summer research 2026",
        "PhD student position",
        "research intern",
        "暑研",
        "科研实习",
        "NLP machine learning",
        "computer vision",
        "robotics",
        "artificial intelligence research",
    ]
    
    def __init__(self, queries: List[str] = None):
        self.queries = queries or self.DEFAULT_QUERIES
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
                    
                    # Determine source type
                    if "scholar" in source:
                        source_type = "scholar"
                    elif "linkedin" in source:
                        source_type = "linkedin"
                    elif "xiaohongshu" in source or "rednote" in source:
                        source_type = "xiaohongshu"
                    elif "wechat" in source or "weixin" in source:
                        source_type = "wechat"
                    elif "twitter" in source or "x.com" in source:
                        source_type = "twitter"
                    elif "instagram" in source:
                        source_type = "instagram"
                    elif "discord" in source:
                        source_type = "discord"
                    elif "nsf" in source:
                        source_type = "nsf"
                    else:
                        source_type = "other"
                    
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
    
    return stats


if __name__ == "__main__":
    # Run collection when called directly
    print("Starting data collection...")
    stats = run_full_collection()
    print(f"\nCollection complete!")
    print(f"New opportunities added: {stats['total_new']}")
    print(f"Total in database: {stats.get('total_in_db', 'unknown')}")
    print(f"By source: {stats.get('by_source', {})}")
