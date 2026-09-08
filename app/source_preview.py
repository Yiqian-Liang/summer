"""
Source preview helpers.

This module fetches a short public excerpt from an opportunity's source URL.
It deliberately returns a limited excerpt instead of mirroring full pages/posts.
That keeps the UI traceable without pretending to own or reproduce the source.
"""

import ipaddress
import logging
import socket
from typing import Dict
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.database import is_real_source_url

logger = logging.getLogger(__name__)

MAX_EXCERPT_CHARS = 1200
MAX_DOWNLOAD_BYTES = 1_000_000
MAX_REDIRECTS = 5
BLOCKED_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}


def _is_public_host(hostname: str) -> bool:
    if not hostname:
        return False

    host = hostname.strip().lower()
    if host in BLOCKED_HOSTS:
        return False

    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global and not ip.is_multicast
    except ValueError:
        pass

    try:
        addresses = socket.getaddrinfo(host, None)
        for info in addresses:
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global or ip.is_multicast:
                return False
    except (OSError, UnicodeError):
        return False

    return bool(addresses)


def is_previewable_url(url: str) -> bool:
    """Validate that a URL is a real public web URL suitable for preview fetch."""
    try:
        if not is_real_source_url(url):
            return False
        parsed = urlparse(url)
        # Force port validation as well as hostname validation.
        parsed.port
        if parsed.username or parsed.password:
            return False
        return _is_public_host(parsed.hostname or "")
    except ValueError:
        return False


def _clean_text(text: str) -> str:
    return " ".join((text or "").split())


def _extract_excerpt(html: str) -> Dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript", "svg", "form", "nav", "footer", "header"]):
        tag.decompose()

    title = _clean_text(soup.title.get_text(" ")) if soup.title else ""

    meta_description = ""
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        meta_description = _clean_text(meta["content"])

    paragraphs = []
    for element in soup.find_all(["p", "li"], limit=30):
        text = _clean_text(element.get_text(" "))
        if len(text) >= 40:
            paragraphs.append(text)

    excerpt = "\n\n".join([part for part in [meta_description] + paragraphs if part])
    return {
        "title": title[:240],
        "excerpt": excerpt[:MAX_EXCERPT_CHARS],
    }


def fetch_source_preview(url: str) -> Dict[str, str]:
    """Fetch and extract a short source preview from a public URL."""
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            if not is_previewable_url(url):
                return {
                    "ok": False,
                    "reason": "This source URL cannot be safely previewed inline.",
                }
            with requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 SummerResearchHub/1.0 (+source preview)",
                    "Accept": "text/html,application/xhtml+xml",
                },
                timeout=8,
                stream=True,
                allow_redirects=False,
            ) as response:
                response.raise_for_status()
                if response.is_redirect:
                    if redirect_count == MAX_REDIRECTS:
                        break
                    url = urljoin(url, response.headers["Location"])
                    continue

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml" not in content_type:
                    return {
                        "ok": False,
                        "reason": "This source is not an HTML page, so inline text preview is unavailable.",
                    }

                chunks = []
                downloaded = 0
                for chunk in response.iter_content(chunk_size=16384, decode_unicode=False):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_DOWNLOAD_BYTES:
                        break
                    chunks.append(chunk)

                encoding = response.encoding or "utf-8"
                html = b"".join(chunks).decode(encoding, errors="replace")
                preview = _extract_excerpt(html)
                if not preview["excerpt"]:
                    return {
                        "ok": False,
                        "reason": "No readable public excerpt was found on this page.",
                    }

                return {
                    "ok": True,
                    "title": preview["title"],
                    "excerpt": preview["excerpt"],
                    "final_url": response.url,
                }
        return {
            "ok": False,
            "reason": "This source redirected too many times to preview inline.",
        }
    except requests.RequestException as exc:
        logger.info("Source preview fetch failed: %s", exc)
        return {
            "ok": False,
            "reason": "The source page could not be fetched. It may require login or block automated previews.",
        }
