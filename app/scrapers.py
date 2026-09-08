import requests
from bs4 import BeautifulSoup
import time
import logging
from typing import List, Dict, Optional
import os
import concurrent.futures
import json
import re
import threading
import urllib.parse
from copy import deepcopy
from app.search_utils import (
    build_platform_search_query,
    canonicalize_source_url,
    detect_source_type,
    normalize_source_filter,
)
from app.search_ranking import deduplicate_results
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _redact_error(error: Exception) -> str:
    """Prevent API keys from leaking into logs via request exception URLs."""
    text = str(error)
    text = re.sub(r"([?&](?:api_key|key)=)[^&\s)]+", r"\1[REDACTED]", text)
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]", text)
    return text


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))

class BaseScraper:
    DEFAULT_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(self, session: requests.Session = None):
        self.session = session or requests.Session()
        self.headers = {
            "User-Agent": os.getenv("SEARCH_USER_AGENT", self.DEFAULT_USER_AGENT),
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
        self.request_timeout = _bounded_env_int("SEARCH_REQUEST_TIMEOUT", 15, 3, 60)

    def fetch_page(self, url: str, raise_errors: bool = False) -> Optional[str]:
        """Fetches a URL with a random User-Agent and error handling."""
        try:
            logger.info(f"Fetching {url}...")
            response = self.session.get(
                url,
                headers=self.headers,
                timeout=(5, self.request_timeout),
            )
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            logger.error(f"Error fetching {url}: {_redact_error(e)}")
            if raise_errors:
                raise
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
            return []

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
            
            response = self.session.post(self.api_url, json=payload, headers=headers, timeout=(5, self.request_timeout))
            response.raise_for_status()
            data = response.json()
            
            if data.get("success"):
                return data["data"]["llm_extraction"]["professors"]
            else:
                logger.error(f"Firecrawl Error: {data.get('error')}")
                return []
                
        except Exception as e:
            logger.error(f"Firecrawl Exception: {_redact_error(e)}")
            return []

class NSFReuScraper(BaseScraper):
    """
    Scrapes the official NSF REU Search page.
    Target: https://www.nsf.gov/crssprgm/reu/reu_search.cfm
    """
    BASE_URL = "https://www.nsf.gov/crssprgm/reu/list_result.jsp?unitid=5049" # Example for CISE (CS)

    def scrape_listings(self, raise_errors: bool = False) -> List[Dict]:
        # For MVP, we will scrape the Computer Science (CISE) listing
        # In a full app, we would iterate through all unitids
        html = self.fetch_page(self.BASE_URL, raise_errors=raise_errors)
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
                    "url": urllib.parse.urljoin(self.BASE_URL, url),
                    "source": "NSF REU",
                    "tags": "NSF, Research, Undergraduate"
                })
            except Exception as e:
                logger.error(f"Error parsing row: {e}")
                continue
                
        return results

class SerpApiScraper(BaseScraper):
    """Fetch Google and Scholar results through SerpApi's HTTP API."""

    SEARCH_ENDPOINT = "https://serpapi.com/search.json"

    def __init__(self, api_key: str = None, session: requests.Session = None):
        super().__init__(session=session)
        self.api_key = api_key or os.getenv("SERPAPI_KEY")

    def _search(self, engine: str, query: str, num: int = 8) -> Dict:
        if not self.api_key:
            return {}
        response = self.session.get(
            self.SEARCH_ENDPOINT,
            params={
                "engine": engine,
                "q": query,
                "api_key": self.api_key,
                "num": max(1, min(num, 20)),
                "hl": "en",
            },
            headers=self.headers,
            timeout=(5, self.request_timeout),
        )
        response.raise_for_status()
        payload = response.json()
        # SerpApi uses this error field for a successful search with no matches.
        if payload.get("error") and payload["error"] != "Google hasn't returned any results for this query.":
            raise RuntimeError(str(payload["error"]))
        return payload

    def search_scholar(self, query: str, raise_errors: bool = False) -> List[Dict]:
        if not self.api_key:
            return []
        try:
            results = self._search("google_scholar", query, num=8)
            return [{
                "title": item.get("title"),
                "description": item.get("snippet") or "",
                "url": canonicalize_source_url(item.get("link")),
                "source": "Google Scholar (SerpApi)",
                "source_type": "scholar",
                "provider": "serpapi",
                "source_query": query,
                "published_at": item.get("publication_info", {}).get("summary", ""),
            } for item in results.get("organic_results", []) if item.get("title") and item.get("link")]
        except Exception as e:
            logger.error(f"SerpApi Error: {_redact_error(e)}")
            if raise_errors:
                raise
            return []

    def search_social_media(self, query: str, platform: str = "xiaohongshu", raise_errors: bool = False) -> List[Dict]:
        """Search one platform through Google's index."""
        if not self.api_key:
            return []
        google_query = build_platform_search_query(query, platform)
        try:
            results = self._search("google", google_query, num=8)
            normalized = []
            for item in results.get("organic_results", []):
                url = canonicalize_source_url(item.get("link"))
                if not item.get("title") or not url:
                    continue
                source_type = detect_source_type(platform, url)
                if platform == "professor_site":
                    source_type = "professor_site"
                elif source_type == "other":
                    source_type = platform
                normalized.append({
                    "title": item.get("title"),
                    "url": url,
                    "source": f"{platform} (SerpApi)",
                    "source_type": source_type,
                    "description": item.get("snippet") or "",
                    "published_at": item.get("date") or "",
                    "provider": "serpapi",
                    "source_query": query,
                })
            return normalized
        except Exception as e:
            logger.error(f"SerpApi search error ({platform}): {_redact_error(e)}")
            if raise_errors:
                raise
            return []

class SogouWechatScraper(BaseScraper):
    """
    Search WeChat official-account articles through Sogou's WeChat index.

    Sogou is the only open web index of mp.weixin.qq.com articles (Google
    coverage is sparse). It needs no login and no API key, but it rate-limits
    aggressively: when it answers with an antispider page we log and return
    no results instead of failing the whole search.
    """

    HOME_URL = "https://weixin.sogou.com/"
    SEARCH_URL = "https://weixin.sogou.com/weixin"
    ANTISPIDER_MARKERS = ("antispider", "验证码", "您的访问过于频繁", "异常访问")

    def __init__(self, session: requests.Session = None):
        super().__init__(session=session)
        self._warmed = False

    @staticmethod
    def enabled() -> bool:
        return os.getenv("ENABLE_SOGOU_WECHAT", "1").strip().lower() not in {"0", "false", "no"}

    def _warm_session(self):
        """Grab Sogou's cookies once; searches without them hit the antispider wall."""
        if self._warmed:
            return
        try:
            self.session.get(self.HOME_URL, headers=self.headers, timeout=(5, self.request_timeout))
        except requests.RequestException:
            pass
        self._warmed = True

    @staticmethod
    def parse_results(html: str, query: str, max_results: int = 10) -> List[Dict]:
        """Parse one Sogou WeChat search page into normalized items."""
        if max_results <= 0:
            return []
        soup = BeautifulSoup(html or "", "html.parser")
        items = []
        for li in soup.select("ul.news-list li[id^='sogou_vr']"):
            title_tag = li.select_one("h3 a")
            if not title_tag:
                continue
            title = title_tag.get_text("", strip=True)
            href = (title_tag.get("href") or "").replace("&amp;", "&")
            url = urllib.parse.urljoin(SogouWechatScraper.HOME_URL, href)
            if not title or not href or not url.startswith(("http://", "https://")):
                continue

            summary_tag = li.select_one("p.txt-info")
            description = summary_tag.get_text(" ", strip=True) if summary_tag else ""

            meta = li.select_one("div.s-p")
            account = ""
            published_at = ""
            if meta:
                account_tag = meta.select_one(".all-time-y2") or meta.select_one("a.account") or meta.select_one("a[href*='websearch']")
                if account_tag:
                    account = account_tag.get_text(strip=True)
                epoch = meta.get("t") or ""
                if not epoch:
                    epoch_match = re.search(r"timeConvert\('(\d+)'\)", str(meta))
                    epoch = epoch_match.group(1) if epoch_match else ""
                if epoch.isdigit():
                    try:
                        published_at = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(int(epoch)))
                    except (ValueError, OverflowError, OSError):
                        pass

            items.append({
                "title": title,
                "description": description,
                "url": canonicalize_source_url(url),
                "source": "微信公众号 (Sogou)",
                "source_type": "wechat",
                "provider": "sogou",
                "author": account,
                "published_at": published_at,
                "source_query": query,
            })
            if len(items) >= max_results:
                break
        return items

    def search_wechat(self, query: str, max_results: int = 10, raise_errors: bool = False) -> List[Dict]:
        """Search WeChat articles for one query; never raises."""
        if not self.enabled():
            return []
        self._warm_session()
        try:
            response = self.session.get(
                self.SEARCH_URL,
                params={"type": "2", "query": query, "ie": "utf8"},
                headers={**self.headers, "Referer": self.HOME_URL},
                timeout=(5, self.request_timeout),
            )
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Sogou WeChat search error: {_redact_error(e)}")
            if raise_errors:
                raise
            return []

        response_url = getattr(response, "url", "") or ""
        if any(marker in response_url for marker in ("antispider",)) or any(
            marker in response.text for marker in self.ANTISPIDER_MARKERS
        ):
            logger.warning("Sogou WeChat rate-limited the request; skipping live WeChat results.")
            if raise_errors:
                raise RuntimeError("Sogou WeChat rate-limited the request")
            return []
        return self.parse_results(response.text, query, max_results=max_results)


class GeminiSearcher:
    """Use Gemini grounding as one bounded search provider."""

    def __init__(self, api_key: str = None, session: requests.Session = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.session = session or requests.Session()
        self.model = os.getenv("GEMINI_SEARCH_MODEL") or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.request_timeout = _bounded_env_int("SEARCH_REQUEST_TIMEOUT", 15, 5, 60)

    @staticmethod
    def _extract_json_items(raw_text: str) -> List[Dict]:
        text = (raw_text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        decoder = json.JSONDecoder()
        starts = [index for index in (text.find("["), text.find("{")) if index >= 0]
        if not starts:
            return []
        try:
            value, _ = decoder.raw_decode(text[min(starts):])
        except json.JSONDecodeError:
            return []
        if isinstance(value, dict):
            value = value.get("results") or value.get("opportunities") or []
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @staticmethod
    def _grounding_chunks(candidate: Dict) -> List[Dict]:
        chunks = []
        metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
        for chunk in metadata.get("groundingChunks", []) or metadata.get("grounding_chunks", []):
            web = chunk.get("web") or {}
            if web.get("uri"):
                chunks.append({"url": web.get("uri"), "title": web.get("title") or ""})
        return chunks

    @staticmethod
    def _title_terms(title: str) -> set:
        words = {word for word in re.findall(r"[a-z0-9]+", title.lower()) if len(word) > 2}
        words.difference_update({"phd", "research", "summer", "position", "positions", "university", "internship"})
        for phrase in re.findall(r"[\u4e00-\u9fff]+", title):
            words.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
        return words

    @classmethod
    def parse_response(cls, data: Dict, query: str) -> List[Dict]:
        candidates = data.get("candidates") or []
        if not candidates:
            return []
        candidate = candidates[0]
        raw_text = "\n".join(
            part.get("text", "")
            for part in (candidate.get("content") or {}).get("parts", [])
            if isinstance(part, dict) and part.get("text")
        )
        items = cls._extract_json_items(raw_text)
        chunks = cls._grounding_chunks(candidate)
        normalized = []
        for item in items:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            url = canonicalize_source_url(item.get("url") or "")
            matched = next((chunk for chunk in chunks if url and canonicalize_source_url(chunk["url"]) == url), None)
            if matched is None:
                title_terms = cls._title_terms(title)
                # ponytail: title overlap links generated summaries to citations;
                # use provider citation spans if ambiguous titles become common.
                best_overlap = 0.0
                for chunk in chunks:
                    overlap = len(title_terms & cls._title_terms(chunk["title"])) / max(1, len(title_terms))
                    if overlap >= 0.5 and overlap > best_overlap:
                        matched, best_overlap = chunk, overlap
            if matched is None:
                continue
            # Only store URLs returned by grounding, never an invented model URL.
            url = canonicalize_source_url(matched["url"])
            if not url.startswith(("http://", "https://")):
                continue

            source_label = str(item.get("source") or "Gemini grounded search")
            source_type = detect_source_type(source_label, url)
            normalized.append({
                "title": title,
                "professor_name": item.get("professor_name") or item.get("professor") or item.get("organization") or "",
                "university": item.get("university") or item.get("company") or "",
                "description": item.get("description") or "",
                "requirements": item.get("requirements") or "",
                "url": url,
                "deadline": item.get("deadline") or "",
                "published_at": item.get("published_at") or item.get("date") or "",
                "source": source_label,
                "source_type": source_type,
                "provider": "gemini",
                "source_query": query,
            })
        return normalized

    def search_opportunities(self, query: str, raise_errors: bool = False) -> List[Dict]:
        if not self.api_key:
            return []
        prompt = f"""Search for active, specific research opportunities related to: {query}

Prioritize exact university/lab postings, funded PhD openings, research internships,
REU programs, and posts that explicitly say they are hiring. Exclude generic university
homepages, personal profiles, news, and papers. Preserve each source's original language.
Return only a JSON array with keys: title, professor_name, university, description,
requirements, url, deadline, published_at, source. Every item must cite an exact page URL."""
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        try:
            response = self.session.post(
                url,
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "tools": [{"google_search": {}}],
                    "generationConfig": {"temperature": 0.1},
                },
                headers={"Content-Type": "application/json"},
                params={"key": self.api_key},
                timeout=(5, self.request_timeout),
            )
            response.raise_for_status()
            results = self.parse_response(response.json(), query)
            logger.info("Gemini grounded search found %s results for %r", len(results), query)
            return results
        except Exception as e:
            logger.error(f"Gemini Search Error: {_redact_error(e)}")
            if raise_errors:
                raise
            return []


from app.agents import ResearchAgent

class AcademicScraper:
    """Bounded, cached coordinator for live search providers."""

    def __init__(self, serp_scraper=None, gemini_searcher=None, sogou_scraper=None):
        self.nsf_scraper = NSFReuScraper()
        self.serp_scraper = serp_scraper or SerpApiScraper()
        self.firecrawl_scraper = FirecrawlScraper()
        self.gemini_searcher = gemini_searcher or GeminiSearcher()
        self.sogou_scraper = sogou_scraper or SogouWechatScraper()
        self.agent = ResearchAgent()
        self._cache = {}
        self._cache_lock = threading.Lock()

    @staticmethod
    def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            value = default
        return max(minimum, min(maximum, value))

    def has_live_search_credentials(self, query: str = None, source_filter: str = None) -> bool:
        """Whether a configured provider can actually handle this search."""
        return bool(self.live_search_providers(query, source_filter))

    def live_search_providers(self, query: str = None, source_filter: str = None) -> List[str]:
        if os.getenv("DISABLE_LIVE_SEARCH", "").strip().lower() in {"1", "true", "yes"}:
            return []
        source_filter = normalize_source_filter(source_filter)
        plan = self._platform_plan(query or "", source_filter)
        providers = []
        sogou_enabled = self.sogou_scraper.enabled()
        if self.serp_scraper.api_key and any(platform != "wechat" or not sogou_enabled for platform in plan):
            providers.append("SerpApi")
        if self.gemini_searcher.api_key and source_filter in {None, "gemini"}:
            providers.append("Gemini")
        if sogou_enabled and ("wechat" in plan or query is None and source_filter is None):
            providers.append("Sogou微信")
        if source_filter == "nsf" and not self.serp_scraper.api_key:
            providers.append("NSF")
        return providers

    @staticmethod
    def _platform_plan(query: str, source_filter: str = None) -> List[str]:
        if source_filter == "gemini":
            return []
        if source_filter and source_filter not in {"all", "gemini", "scholar", "nsf"}:
            return [source_filter]
        if source_filter == "scholar":
            return ["scholar"]
        if source_filter == "nsf":
            return ["nsf"]

        query_lower = query.lower()
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", query))
        summer_intent = any(term in query_lower for term in ["summer", "reu", "intern", "暑研", "实习", "本科"])
        phd_intent = any(term in query_lower for term in ["phd", "doctoral", "博士", "全奖"])

        if summer_intent:
            academic = ["nsf", "university", "professor_site", "pathways", "mitacs"]
        elif phd_intent:
            academic = ["euraxess", "findaphd", "academicjobs", "university", "professor_site"]
        else:
            academic = ["university", "professor_site", "academicjobs", "euraxess"]

        social = ["xiaohongshu", "wechat", "zhihu", "bilibili", "weibo", "tieba"] if has_chinese else ["linkedin", "twitter"]
        # Reserve early slots for official postings as well as social leads.
        plan = [*social[:2], *academic[:2], *social[2:], *academic[2:]] if has_chinese else [*academic, *social]
        return list(dict.fromkeys(plan))

    def deep_research(
        self,
        query: str,
        source_filter: str = None,
        use_gemini: bool = True,
        force_refresh: bool = False,
        return_status: bool = False,
    ):
        query = " ".join((query or "").split())
        source_filter = normalize_source_filter(source_filter)
        logger.info("Starting bounded live search for %r (source=%r)", query, source_filter)

        if not query or not self.has_live_search_credentials(query, source_filter):
            logger.info("No live search credentials configured. Skipping remote search.")
            outcome = {"results": [], "status": "unavailable", "providers": [], "failed_sources": [], "cached": False}
            return outcome if return_status else []

        max_requests = self._env_int("LIVE_SEARCH_MAX_REQUESTS", 6, 1, 12)
        cache_key = (query.lower(), source_filter or "", bool(use_gemini), max_requests,
                     tuple(self.live_search_providers(query, source_filter)))
        cache_ttl = self._env_int("LIVE_SEARCH_CACHE_SECONDS", 600, 0, 86400)
        if not force_refresh and cache_ttl:
            with self._cache_lock:
                cached = self._cache.get(cache_key)
            if cached and time.monotonic() - cached[0] < cache_ttl:
                outcome = deepcopy(cached[1])
                outcome.update(status="cached", cached=True)
                return outcome if return_status else outcome["results"]

        tasks = []
        if (
            use_gemini
            and self.gemini_searcher.api_key
            and source_filter in {None, "", "all", "gemini"}
        ):
            tasks.append(("gemini", "Gemini", self.gemini_searcher.search_opportunities, (query,)))

        platform_plan = self._platform_plan(query, source_filter)
        # Sogou covers WeChat without login or API key and does not consume the
        # bounded SerpApi/Gemini request budget.
        use_sogou_wechat = "wechat" in platform_plan and self.sogou_scraper.enabled()
        free_tasks = []
        if use_sogou_wechat:
            free_tasks.append(("wechat", "Sogou微信", self.sogou_scraper.search_wechat, (query,)))

        if self.serp_scraper.api_key:
            for platform in platform_plan:
                if platform == "scholar":
                    tasks.append((platform, "SerpApi", self.serp_scraper.search_scholar, (query,)))
                elif platform == "wechat" and use_sogou_wechat:
                    continue  # Sogou indexes WeChat far better than Google does
                else:
                    tasks.append((platform, "SerpApi", self.serp_scraper.search_social_media, (query, platform)))
        elif source_filter == "nsf":
            tasks.append(("nsf", "NSF", self.nsf_scraper.scrape_listings, ()))
        tasks = tasks[:max_requests] + free_tasks

        raw_results = []
        failed_sources = []
        successful_tasks = 0
        if tasks:
            workers = min(4, len(tasks))
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
            future_map = {
                executor.submit(function, *args, raise_errors=True): label
                for label, provider, function, args in tasks
            }
            total_timeout = self._env_int("LIVE_SEARCH_TOTAL_TIMEOUT", 25, 5, 60)
            done, pending = concurrent.futures.wait(future_map, timeout=total_timeout)
            # Task order makes duplicate selection deterministic across runs.
            for future in future_map:
                if future not in done:
                    continue
                try:
                    result = future.result()
                    if not isinstance(result, list):
                        raise ValueError("Search provider returned a non-list result")
                    raw_results.extend(result)
                    successful_tasks += 1
                except Exception as e:
                    failed_sources.append(future_map[future])
                    logger.error("Search task %s failed: %s", future_map[future], _redact_error(e))
            for future in pending:
                future.cancel()
                failed_sources.append(future_map[future])
                logger.warning("Search task %s exceeded the total timeout", future_map[future])
            executor.shutdown(wait=False, cancel_futures=True)

        valid_results = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = item.get("title") or item.get("name")
            if not isinstance(title, str) or not title.strip():
                continue
            url = canonicalize_source_url(item.get("url") or item.get("link"))
            try:
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
            except ValueError:
                continue
            description = item.get("description") or item.get("snippet") or ""
            valid_results.append({
                **item, "url": url, "title": title.strip(),
                "description": description if isinstance(description, str) else "",
            })
        unique_results = deduplicate_results(valid_results)
        for item in unique_results:
            title = item.get("title") or item.get("name") or ""
            item["tags"] = self.agent._generate_tags(title, item.get("description", ""))
            item["research_focus"] = self.agent._extract_research_focus(title, item.get("description", ""))

        status = ("partial" if successful_tasks else "error") if failed_sources else ("fresh" if tasks else "unavailable")
        outcome = {
            "results": unique_results,
            "status": status,
            "providers": list(dict.fromkeys(task[1] for task in tasks)),
            "failed_sources": sorted(set(failed_sources)),
            "cached": False,
        }
        if cache_ttl and status == "fresh":
            with self._cache_lock:
                now = time.monotonic()
                self._cache = {key: value for key, value in self._cache.items() if now - value[0] < cache_ttl}
                if len(self._cache) >= 128:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = (now, deepcopy(outcome))
        logger.info("Bounded live search found %s unique results", len(unique_results))
        return outcome if return_status else unique_results

    def search_all_sources(
        self,
        query: str,
        use_gemini: bool = True,
        source_filter: str = None,
        force_refresh: bool = False,
        return_status: bool = False,
    ):
        return self.deep_research(
            query,
            source_filter=source_filter,
            use_gemini=use_gemini,
            force_refresh=force_refresh,
            return_status=return_status,
        )


if __name__ == "__main__":
    scraper = AcademicScraper()
    # print(scraper.nsf_scraper.scrape_listings())
    print(scraper.search_all_sources("NLP"))
