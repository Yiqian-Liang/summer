"""Import MediaCrawler content exports into the opportunity search index.

This module consumes MediaCrawler's public JSON, JSONL, or CSV output. It does
not copy or invoke MediaCrawler internals, so login state and platform request
signing remain isolated in the upstream tool.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Dict, Iterable, Iterator, Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from app.database import init_db, upsert_opportunity
from app.scheduler import OpportunityProcessor
from app.search_utils import canonicalize_source_url


logger = logging.getLogger(__name__)

PLATFORM_ALIASES = {
    "xhs": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "rednote": "xiaohongshu",
    "zhihu": "zhihu",
    "bili": "bilibili",
    "bilibili": "bilibili",
    "wb": "weibo",
    "weibo": "weibo",
    "tieba": "tieba",
}

PLATFORM_DOMAINS = {
    "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
    "zhihu": ("zhihu.com",),
    "bilibili": ("bilibili.com", "b23.tv"),
    "weibo": ("weibo.com", "weibo.cn"),
    "tieba": ("tieba.baidu.com",),
}


def _clean_html(value) -> str:
    text = str(value or "").strip()
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    return " ".join(text.split())


def _infer_platform(record: Dict, path: Path) -> Optional[str]:
    url = str(
        record.get("note_url")
        or record.get("content_url")
        or record.get("video_url")
        or ""
    ).lower()
    # Trust the actual source over the export folder's name.
    host = (urlsplit(url).hostname or "").lower()
    for platform, domains in PLATFORM_DOMAINS.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return platform
    for part in reversed(path.parts):
        if part.lower() in PLATFORM_ALIASES:
            return PLATFORM_ALIASES[part.lower()]
    # Field-signature fallback for records without a URL.
    if record.get("content_id"):
        return "zhihu"
    if record.get("video_id"):
        return "bilibili"
    if record.get("tieba_name") or record.get("publish_time"):
        return "tieba"
    if record.get("create_date_time"):
        return "weibo"
    if record.get("note_id"):
        return "xiaohongshu"
    return None


def _iter_json_records(path: Path) -> Iterator[Optional[Dict]]:
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    logger.warning("Skipping malformed JSONL row %s:%s: %s", path, line_number, error)
                    yield None
                    continue
                yield value if isinstance(value, dict) else None
        return

    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if isinstance(value, dict):
        value = value.get("contents", value.get("results", [value]))
    if isinstance(value, list):
        yield from (item if isinstance(item, dict) else None for item in value)
    else:
        yield None


def iter_export_records(path: Path) -> Iterator[Optional[Dict]]:
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        yield from _iter_json_records(path)
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            yield from csv.DictReader(handle)


def discover_export_files(path: Path) -> Iterable[Path]:
    if path.is_file():
        return [path] if path.suffix.lower() in {".json", ".jsonl", ".csv"} and not any(
            word in path.name.lower() for word in ("comment", "creator", "word")
        ) else []
    if not path.is_dir():
        return []
    files = []
    for candidate in path.rglob("*"):
        name = candidate.name.lower()
        if not candidate.is_file() or candidate.suffix.lower() not in {".json", ".jsonl", ".csv"}:
            continue
        if "comment" in name or "creator" in name or "word" in name:
            continue
        if "content" in name or "search" in name:
            files.append(candidate)
    return sorted(files)


def media_record_to_item(record: Dict, platform: str) -> Optional[Dict]:
    """Map one MediaCrawler content row to the app's provider-neutral shape."""
    author = record.get("nickname") or record.get("user_nickname") or ""
    if platform == "xiaohongshu":
        description = _clean_html(record.get("desc"))
        title = _clean_html(record.get("title")) or description[:160]
        url = record.get("note_url")
        if not url and record.get("note_id"):
            url = f"https://www.xiaohongshu.com/explore/{record['note_id']}"
        published_at = record.get("time") or record.get("last_update_time")
        source_label = "Xiaohongshu (MediaCrawler)"
    elif platform == "zhihu":
        description = _clean_html(record.get("content_text") or record.get("desc"))
        title = _clean_html(record.get("title")) or description[:160]
        url = record.get("content_url")
        if not url and record.get("content_id"):
            content_id = record["content_id"]
            if record.get("content_type") == "article":
                url = f"https://zhuanlan.zhihu.com/p/{content_id}"
            elif record.get("content_type") == "answer" and record.get("question_id"):
                url = f"https://www.zhihu.com/question/{record['question_id']}/answer/{content_id}"
            elif record.get("content_type") == "zvideo":
                url = f"https://www.zhihu.com/zvideo/{content_id}"
        published_at = record.get("created_time") or record.get("updated_time")
        source_label = "Zhihu (MediaCrawler)"
    elif platform == "bilibili":
        description = _clean_html(record.get("desc"))
        title = _clean_html(record.get("title")) or description[:160]
        url = record.get("video_url")
        if not url and record.get("video_id"):
            video_id = str(record["video_id"])
            url = f"https://www.bilibili.com/video/{video_id if video_id.startswith(('BV', 'av')) else 'av' + video_id}"
        published_at = record.get("create_time")
        source_label = "Bilibili (MediaCrawler)"
    elif platform == "weibo":
        # Weibo posts have no title; lead with the post text.
        description = _clean_html(record.get("content") or record.get("desc"))
        title = description[:80]
        url = record.get("note_url")
        if not url and record.get("note_id"):
            url = f"https://m.weibo.cn/detail/{record['note_id']}"
        published_at = record.get("create_time") or record.get("create_date_time")
        source_label = "Weibo (MediaCrawler)"
    elif platform == "tieba":
        description = _clean_html(record.get("desc"))
        title = _clean_html(record.get("title")) or description[:160]
        url = record.get("note_url")
        if not url and record.get("note_id"):
            url = f"https://tieba.baidu.com/p/{record['note_id']}"
        published_at = record.get("publish_time")
        source_label = "Tieba (MediaCrawler)"
    else:
        return None

    url = canonicalize_source_url(url)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if not title or parsed.scheme not in {"http", "https"} or not any(
        host == domain or host.endswith("." + domain) for domain in PLATFORM_DOMAINS[platform]
    ):
        return None
    return {
        "title": title,
        "description": description,
        "author": author or "",
        "url": url,
        "source": source_label,
        "source_type": platform,
        "provider": "mediacrawler",
        "source_query": record.get("source_keyword") or "",
        "published_at": published_at or "",
    }


def import_mediacrawler_path(
    path,
    processor: OpportunityProcessor = None,
    upsert=upsert_opportunity,
    dry_run: bool = False,
) -> Dict[str, int]:
    root = Path(path).expanduser().resolve()
    processor = processor or OpportunityProcessor()
    stats = {"files": 0, "read": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": 0}
    export_files = list(discover_export_files(root))
    if not export_files:
        raise FileNotFoundError(f"No MediaCrawler content exports found at {root}")

    for export_file in export_files:
        stats["files"] += 1
        try:
            records = iter_export_records(export_file)
            for record in records:
                stats["read"] += 1
                try:
                    if not isinstance(record, dict):
                        stats["errors"] += 1
                        continue
                    platform = _infer_platform(record, export_file)
                    item = media_record_to_item(record, platform) if platform else None
                    if not item:
                        stats["skipped"] += 1
                        continue
                    normalized = processor.normalize_item(item, platform)
                    if dry_run:
                        stats["inserted"] += 1
                    elif upsert(normalized):
                        stats["inserted"] += 1
                    else:
                        stats["updated"] += 1
                except Exception as error:
                    stats["errors"] += 1
                    logger.warning("Could not import MediaCrawler row from %s: %s", export_file, error)
        except (OSError, UnicodeError, csv.Error, json.JSONDecodeError) as error:
            stats["errors"] += 1
            logger.warning("Could not read MediaCrawler export %s: %s", export_file, error)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Import MediaCrawler JSON/JSONL/CSV content exports")
    parser.add_argument("path", help="MediaCrawler data directory or one export file")
    parser.add_argument("--dry-run", action="store_true", help="Parse and normalize without writing to SQLite")
    args = parser.parse_args()

    if not args.dry_run:
        init_db()
    stats = import_mediacrawler_path(args.path, dry_run=args.dry_run)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
