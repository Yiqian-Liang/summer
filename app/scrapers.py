import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
import time
import logging
from typing import List, Dict, Optional
import os
try:
    from serpapi import GoogleSearch
except ImportError:
    GoogleSearch = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class BaseScraper:
    def __init__(self):
        self.ua = UserAgent()
        self.headers = {
            'User-Agent': self.ua.random
        }

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetches a URL with a random User-Agent and error handling."""
        try:
            logger.info(f"Fetching {url}...")
            response = requests.get(url, headers=self.headers, timeout=10)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

class NSFReuScraper(BaseScraper):
    """
    Scrapes the official NSF REU Search page.
    Target: https://www.nsf.gov/crssprgm/reu/reu_search.cfm
    """
    BASE_URL = "https://www.nsf.gov/crssprgm/reu/list_result.jsp?unitid=5049" # Example for CISE (CS)

    def scrape_listings(self) -> List[Dict]:
        # For MVP, we will scrape the Computer Science (CISE) listing
        # In a full app, we would iterate through all unitids
        html = self.fetch_page(self.BASE_URL)
        if not html:
            return []

        soup = BeautifulSoup(html, 'html.parser')
        results = []
        
        # NSF listings are usually in tables or stylized divs
        # Note: This selector is heuristic based on common government site patterns
        rows = soup.select('table tr') 
        
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 3:
                continue
                
            try:
                link_tag = cols[0].find('a')
                if not link_tag:
                    continue
                    
                title = link_tag.get_text(strip=True)
                url = link_tag['href']
                institution = cols[1].get_text(strip=True) if len(cols) > 1 else "Unknown"
                location = cols[2].get_text(strip=True) if len(cols) > 2 else "Unknown"
                
                results.append({
                    "title": title,
                    "institution": institution,
                    "location": location,
                    "url": url,
                    "source": "NSF REU",
                    "tags": "NSF, Research, Undergraduate"
                })
            except Exception as e:
                logger.error(f"Error parsing row: {e}")
                continue
                
        return results

class SerpApiScraper(BaseScraper):
    """
    Handles fetching data from Google Scholar and generic Google Search via SerpApi.
    """
    def __init__(self, api_key: str = None):
        super().__init__()
        self.api_key = api_key or os.getenv("SERPAPI_KEY")

    def search_scholar(self, query: str) -> List[Dict]:
        if not self.api_key or not GoogleSearch:
            logger.warning("SerpApi Key missing or library not installed. Returning mock data.")
            return self._mock_scholar_data(query)

        try:
            params = {
                "engine": "google_scholar",
                "q": query,
                "api_key": self.api_key
            }
            search = GoogleSearch(params)
            results = search.get_dict()
            
            organic_results = results.get("organic_results", [])
            return [{
                "name": item.get("title"),
                "university": "Scholar Result",
                "department": "N/A",
                "interests": item.get("snippet"),
                "recent_paper": item.get("link"),
                "source": "Google Scholar (Live)",
                "match_score": 80
            } for item in organic_results]
        except Exception as e:
            logger.error(f"SerpApi Error: {e}")
            return self._mock_scholar_data(query)

    def search_social_media(self, query: str) -> List[Dict]:
        """
        Uses Google Search to find Xiaohongshu/WeChat posts indexed by Google.
        Query format: site:xiaohongshu.com "query"
        """
        if not self.api_key or not GoogleSearch:
            return self._mock_social_data(query)

        try:
            params = {
                "engine": "google",
                "q": f'site:xiaohongshu.com "{query}" summer research',
                "api_key": self.api_key
            }
            search = GoogleSearch(params)
            results = search.get_dict()
            
            organic_results = results.get("organic_results", [])
            return [{
                "title": item.get("title"),
                "author": item.get("source", "Rednote User"),
                "url": item.get("link"),
                "source": "Rednote (via Google)",
                "snippet": item.get("snippet")
            } for item in organic_results]
        except Exception as e:
            logger.error(f"SerpApi Social Search Error: {e}")
            return self._mock_social_data(query)

    def _mock_scholar_data(self, query):
        return [{
            "name": f"Scholar matching '{query}'",
            "university": "Mock University",
            "department": "Computer Science",
            "interests": f"Deep Learning, {query}",
            "recent_paper": "https://scholar.google.com",
            "source": "Google Scholar (Mock)",
            "match_score": 70
        }]

    def _mock_social_data(self, query):
        return [{
            "title": f"Summer Research 2025 at MIT ({query})",
            "author": "StudyAbroad_Helper",
            "url": "https://www.xiaohongshu.com/discovery/item/...",
            "source": "Rednote (Mock)",
            "snippet": "Detailed guide on applying to MIT SGI..."
        }]

class AcademicScraper:
    """Facade to coordinate multiple scrapers."""
    def __init__(self):
        self.nsf_scraper = NSFReuScraper()
        self.serp_scraper = SerpApiScraper()

    def search_all_sources(self, query: str) -> List[Dict]:
        results = []
        
        # 1. Official NSF REU (Cached/Scraped)
        # Note: In production, this should run periodically, not per request
        # results.extend(self.nsf_scraper.scrape_listings()) 
        
        # 2. Search Scholar
        results.extend(self.serp_scraper.search_scholar(query))
        
        # 3. Search Social Media
        results.extend(self.serp_scraper.search_social_media(query))
        
        return results

if __name__ == "__main__":
    scraper = AcademicScraper()
    # print(scraper.nsf_scraper.scrape_listings())
    print(scraper.search_all_sources("NLP"))
