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

class FirecrawlScraper(BaseScraper):
    """
    Advanced scraper for dynamic University websites (JS-heavy).
    Uses Firecrawl (or similar Agentic Scraping API) to extract structured data
    from unstructured faculty pages using LLM capabilities.
    """
    def __init__(self, api_key: str = None):
        super().__init__()
        self.api_key = api_key or os.getenv("FIRECRAWL_API_KEY")
        self.api_url = "https://api.firecrawl.dev/v1/scrape"

    def scrape_department_directory(self, url: str, prompt: str = "Extract all professors, emails, and research interests") -> List[Dict]:
        """
        Sends a URL to Firecrawl to be scraped and parsed by an LLM.
        """
        if not self.api_key:
            logger.warning("Firecrawl API Key missing. Falling back to simple requests.")
            # Fallback logic could go here, or just return empty/mock
            return self._mock_extraction(url)

        try:
            payload = {
                "url": url,
                "pageOptions": {
                    "onlyMainContent": True
                },
                "extractorOptions": {
                    "mode": "llm-extraction",
                    "extractionPrompt": prompt,
                    "extractionSchema": {
                        "type": "object",
                        "properties": {
                            "professors": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "email": {"type": "string"},
                                        "interests": {"type": "string"},
                                        "website": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(self.api_url, json=payload, headers=headers)
            data = response.json()
            
            if data.get("success"):
                return data["data"]["llm_extraction"]["professors"]
            else:
                logger.error(f"Firecrawl Error: {data.get('error')}")
                return []
                
        except Exception as e:
            logger.error(f"Firecrawl Exception: {e}")
            return []

    def _mock_extraction(self, url):
        """Mock data for testing without API key"""
        return [
            {"name": "Dr. Firecrawl (Mock)", "email": "mock@univ.edu", "interests": "Agentic Scraping", "website": url}
        ]

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

    def _mock_scholar_data(self, query):
        """Mock Google Scholar data for testing without API key."""
        return [
            {
                "name": f"Deep Learning Approaches for {query}",
                "university": "Stanford University",
                "department": "Computer Science",
                "interests": f"Research on {query}, neural networks, and optimization methods for large-scale systems.",
                "recent_paper": "https://arxiv.org/abs/2401.12345",
                "source": "Google Scholar",
                "professor_name": "Dr. Andrew Ng",
            },
            {
                "name": f"Advances in {query}: A Comprehensive Survey",
                "university": "MIT",
                "department": "EECS",
                "interests": f"Survey paper covering recent advances in {query}, including transformers and attention mechanisms.",
                "recent_paper": "https://arxiv.org/abs/2402.54321",
                "source": "Google Scholar",
                "professor_name": "Dr. Regina Barzilay",
            },
            {
                "name": f"Efficient {query} for Resource-Constrained Devices",
                "university": "CMU",
                "department": "Machine Learning",
                "interests": f"Model compression, quantization, and efficient inference for {query} applications.",
                "recent_paper": "https://arxiv.org/abs/2403.11111",
                "source": "Google Scholar",
                "professor_name": "Dr. Ruslan Salakhutdinov",
            },
        ]

    def _mock_social_data(self, query, platform="rednote"):
        """Mock social media data for testing without API key."""
        mock_data = {
            "linkedin": [
                {
                    "title": f"Hiring PhD Students - {query} Lab",
                    "author": "Prof. Sarah Chen",
                    "url": "https://www.linkedin.com/posts/sarahchen-ml",
                    "source": "LinkedIn",
                    "snippet": f"🎓 My lab at Stanford is hiring 2 PhD students for Fall 2026! Looking for candidates with strong background in {query}. Fully funded positions with competitive stipend. DM me!"
                },
                {
                    "title": f"Summer Research Intern Openings - {query}",
                    "author": "Google DeepMind",
                    "url": "https://www.linkedin.com/jobs/view/123456",
                    "source": "LinkedIn",
                    "snippet": f"We're looking for summer interns to join our {query} team. Work on cutting-edge research with world-class researchers. Apply by March 15!"
                }
            ],
            "instagram": [
                {
                    "title": f"{query} Research Lab - Summer Program",
                    "author": "mit_csail",
                    "url": "https://www.instagram.com/p/xyz123",
                    "source": "Instagram",
                    "snippet": f"Applications now open for our {query} summer research program! 10 weeks, fully funded, work with top researchers. Link in bio 🔗"
                }
            ],
            "wechat": [
                {
                    "title": f"【招生】2026暑研汇总：{query}方向",
                    "author": "一亩三分地",
                    "url": "https://mp.weixin.qq.com/s/abc123",
                    "source": "微信公众号",
                    "snippet": f"整理了北美Top50学校{query}方向的暑研机会，包括MIT、Stanford、CMU等。内含申请时间线、套磁模板..."
                },
                {
                    "title": f"Stanford {query} Lab 招暑期实习生",
                    "author": "CS申请圈",
                    "url": "https://mp.weixin.qq.com/s/def456",
                    "source": "微信公众号",
                    "snippet": f"Stanford {query} Lab 现招收2026年暑期实习生，要求：本科在读，有相关研究经验，申请截止：3月1日"
                }
            ],
            "twitter": [
                {
                    "title": f"PhD positions in {query}",
                    "author": "@prof_ml_research",
                    "url": "https://twitter.com/prof_ml_research/status/123",
                    "source": "Twitter/X",
                    "snippet": f"📢 Fully funded PhD positions available! Looking for motivated students interested in {query}. Strong math background preferred. Apply: [link] #phdposition #academia"
                }
            ],
            "rednote": [
                {
                    "title": f"暑研申请经验分享 | {query}方向",
                    "author": "留学小助手",
                    "url": "https://www.xiaohongshu.com/explore/abc",
                    "source": "小红书",
                    "snippet": f"分享我申请{query}方向暑研的经验！从选校到套磁到面试，全流程攻略~今年拿到了Stanford和MIT的offer，选了Stanford..."
                },
                {
                    "title": f"CMU {query} 实验室招人啦！",
                    "author": "CS求职日记",
                    "url": "https://www.xiaohongshu.com/explore/def",
                    "source": "小红书",
                    "snippet": f"导师人超好！CMU的{query}实验室在招暑期实习生和PhD，不卡学校背景，看重研究潜力。有兴趣的同学可以发邮件..."
                }
            ],
            "zhihu": [
                {
                    "title": f"如何申请{query}方向的暑研？",
                    "author": "知乎用户",
                    "url": "https://www.zhihu.com/question/12345",
                    "source": "知乎",
                    "snippet": f"高赞回答：{query}方向暑研申请攻略。首先要明确目标学校和导师，然后准备好CV和研究计划..."
                }
            ],
        }
        return mock_data.get(platform, mock_data["rednote"])

class GeminiSearcher:
    """
    Uses Gemini 2.0 with Google Search Grounding to find research opportunities.
    This leverages Google's search infrastructure directly through Gemini.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"
    
    def search_opportunities(self, query: str) -> List[Dict]:
        """
        Uses Gemini with Google Search grounding to find summer research opportunities.
        """
        if not self.api_key:
            logger.warning("Gemini API key not set. Skipping Gemini search.")
            return []
        
        prompt = f"""
        Search for summer research opportunities, PhD positions, and research internships related to: "{query}"
        
        Focus on:
        1. Professor labs that are hiring
        2. Summer research programs (like REU, SURF, etc.)
        3. PhD positions at universities
        4. Research internships at companies
        
        For each opportunity found, extract:
        - Title/Position name
        - Professor or organization name
        - University or company
        - Brief description
        - URL if available
        - Any deadline mentioned
        
        Return the results as a JSON array with objects containing: title, professor_name, university, description, url, deadline, source
        Only return the JSON array, no other text.
        """
        
        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [{
                    "google_search_retrieval": {
                        "dynamic_retrieval_config": {
                            "mode": "MODE_DYNAMIC",
                            "dynamic_threshold": 0.3
                        }
                    }
                }]
            }
            
            headers = {"Content-Type": "application/json"}
            params = {"key": self.api_key}
            
            response = requests.post(self.api_url, json=payload, headers=headers, params=params, timeout=30)
            data = response.json()
            
            if 'candidates' not in data:
                logger.error(f"Gemini Search Error: {data}")
                return []
            
            raw_text = data['candidates'][0]['content']['parts'][0]['text']
            
            # Parse JSON from response
            import json
            try:
                # Try to extract JSON from the response
                json_str = raw_text.strip()
                if json_str.startswith("```"):
                    json_str = json_str.split("```")[1]
                    if json_str.startswith("json"):
                        json_str = json_str[4:]
                
                results = json.loads(json_str)
                
                # Normalize results
                normalized = []
                for item in results:
                    normalized.append({
                        "title": item.get("title", "Research Opportunity"),
                        "name": item.get("title", "Research Opportunity"),
                        "professor_name": item.get("professor_name") or item.get("professor"),
                        "university": item.get("university") or item.get("organization", ""),
                        "description": item.get("description", ""),
                        "snippet": item.get("description", ""),
                        "interests": item.get("description", ""),
                        "url": item.get("url", ""),
                        "recent_paper": item.get("url", ""),
                        "deadline": item.get("deadline"),
                        "source": "Gemini Search (Google)",
                    })
                
                logger.info(f"Gemini Search found {len(normalized)} results for '{query}'")
                return normalized
                
            except json.JSONDecodeError:
                logger.warning("Could not parse Gemini response as JSON, returning raw text")
                return [{
                    "title": f"Research opportunities in {query}",
                    "name": f"Research opportunities in {query}",
                    "description": raw_text[:500],
                    "snippet": raw_text[:500],
                    "source": "Gemini Search (Google)",
                    "url": ""
                }]
                
        except Exception as e:
            logger.error(f"Gemini Search Error: {e}")
            return []


class AcademicScraper:
    """Facade to coordinate multiple scrapers including SerpApi and Gemini."""
    def __init__(self):
        self.nsf_scraper = NSFReuScraper()
        self.serp_scraper = SerpApiScraper()
        self.firecrawl_scraper = FirecrawlScraper()
        self.gemini_searcher = GeminiSearcher()

    def search_all_sources(self, query: str, use_gemini: bool = True) -> List[Dict]:
        """
        Search all available sources and combine results.
        Uses SerpApi for structured search and optionally Gemini for AI-powered search.
        """
        results = []
        
        # 1. SerpApi: Google Scholar search
        logger.info(f"Searching Google Scholar via SerpApi for: {query}")
        scholar_results = self.serp_scraper.search_scholar(query)
        results.extend(scholar_results)
        logger.info(f"SerpApi Scholar: {len(scholar_results)} results")
        
        # 2. SerpApi: Social media platforms
        platforms = ["linkedin", "wechat", "rednote", "twitter"]
        for platform in platforms:
            platform_results = self.serp_scraper.search_social_media(query, platform)
            results.extend(platform_results)
            logger.info(f"SerpApi {platform}: {len(platform_results)} results")
        
        # 3. Gemini Search (AI-powered with Google Search grounding)
        if use_gemini and self.gemini_searcher.api_key:
            logger.info(f"Searching via Gemini for: {query}")
            gemini_results = self.gemini_searcher.search_opportunities(query)
            results.extend(gemini_results)
            logger.info(f"Gemini Search: {len(gemini_results)} results")
        
        logger.info(f"Total results from all sources: {len(results)}")
        return results


if __name__ == "__main__":
    scraper = AcademicScraper()
    # print(scraper.nsf_scraper.scrape_listings())
    print(scraper.search_all_sources("NLP"))
