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

    def search_social_media(self, query: str, platform: str = "rednote") -> List[Dict]:
        """
        Uses Google Search to find posts indexed by Google.
        Supports: rednote, linkedin, instagram, wechat, twitter, zhihu, bilibili, tiktok, facebook, discord
        """
        if not self.api_key or not GoogleSearch:
            return self._mock_social_data(query, platform)

        site_map = {
            "rednote": "site:xiaohongshu.com",
            "linkedin": 'site:linkedin.com/in OR site:linkedin.com/jobs "PhD student" OR "Professor"',
            "instagram": 'site:instagram.com "lab" OR "research group"',
            "wechat": 'site:mp.weixin.qq.com "暑研" OR "summer research"',
            "twitter": 'site:twitter.com OR site:x.com "phd position" OR "looking for students"',
            "zhihu": 'site:zhihu.com "暑研" OR "导师评价"',
            "bilibili": 'site:bilibili.com "暑研" OR "科研"',
            "tiktok": 'site:tiktok.com "research" OR "phd life"',
            "facebook": 'site:facebook.com "research group" OR "lab"',
            "discord": 'site:discord.com "invite" "research" OR "study group"'
        }
        
        site_operator = site_map.get(platform, "site:xiaohongshu.com")
        
        try:
            params = {
                "engine": "google",
                "q": f'{site_operator} "{query}"',
                "api_key": self.api_key
            }
            search = GoogleSearch(params)
            results = search.get_dict()
            
            organic_results = results.get("organic_results", [])
            return [{
                "title": item.get("title"),
                "author": item.get("source", f"{platform.title()} User"),
                "url": item.get("link"),
                "source": f"{platform.title()} (via Google)",
                "snippet": item.get("snippet")
            } for item in organic_results]
        except Exception as e:
            logger.error(f"SerpApi Social Search Error ({platform}): {e}")
            return self._mock_social_data(query, platform)

    def _mock_social_data(self, query, platform="rednote"):
        if platform == "linkedin":
            return [{
                "title": f"PhD Student in {query} at Stanford",
                "author": "Jane Doe",
                "url": "https://www.linkedin.com/in/janedoe",
                "source": "LinkedIn (Mock)",
                "snippet": f"Researching {query} and Agents..."
            }]
        elif platform == "instagram":
             return [{
                "title": f"{query} Lab Group Photo",
                "author": "mit_csail",
                "url": "https://www.instagram.com/p/...",
                "source": "Instagram (Mock)",
                "snippet": "Our team presenting at CVPR..."
            }]
        elif platform == "wechat":
             return [{
                "title": f"CS Top 50 Summer Research Opportunities ({query})",
                "author": "CS_Go_Abroad",
                "url": "https://mp.weixin.qq.com/s/...",
                "source": "WeChat Official Account (Mock)",
                "snippet": f"Summary of {query} labs hiring interns..."
            }]
        elif platform == "twitter":
             return [{
                "title": f"Hiring PhD students in {query}!",
                "author": "Prof_AI",
                "url": "https://twitter.com/...",
                "source": "Twitter (Mock)",
                "snippet": "My lab is looking for 2 PhD students..."
            }]
        elif platform == "discord":
             return [{
                "title": f"{query} Research Community Invite",
                "author": "Discord",
                "url": "https://discord.com/invite/...",
                "source": "Discord (Mock)",
                "snippet": "Join our server to discuss recent papers in..."
            }]
        else:
            return [{
                "title": f"Summer Research 2025 at MIT ({query})",
                "author": "StudyAbroad_Helper",
                "url": "https://www.xiaohongshu.com/discovery/item/...",
                "source": f"{platform.title()} (Mock)",
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
        # results.extend(self.nsf_scraper.scrape_listings()) 
        
        # 2. Search Scholar
        results.extend(self.serp_scraper.search_scholar(query))
        
        # 3. Search Global Social Media
        platforms = ["rednote", "linkedin", "instagram", "wechat", "twitter", "zhihu", "bilibili", "discord"]
        for p in platforms:
             results.extend(self.serp_scraper.search_social_media(query, p))
        
        return results

if __name__ == "__main__":
    scraper = AcademicScraper()
    # print(scraper.nsf_scraper.scrape_listings())
    print(scraper.search_all_sources("NLP"))
