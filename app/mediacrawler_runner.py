"""Drive MediaCrawler for summer-research keywords and import its exports.

MediaCrawler stays a separate tool with its own license and login state. This
module only invokes its CLI as a subprocess (never from a web request) and then
imports the exported JSON/JSONL/CSV files, so one command refreshes coverage
for 小红书 / 知乎 / B站 / 微博 / 贴吧.

MediaCrawler requires Python >= 3.11; running it through `uv` is recommended
because uv provisions a suitable interpreter automatically.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

from app.database import init_db
from app.mediacrawler_import import import_mediacrawler_path


logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PLATFORMS = ["xhs", "zhihu", "bili", "wb", "tieba"]
DEFAULT_KEYWORDS = [
    "暑研",
    "暑期科研",
    "科研实习",
    "暑研申请",
    "课题组招生",
    "博士招生",
    "科研助理",
]


def mediacrawler_root() -> Path:
    """Locate the MediaCrawler checkout."""
    configured = os.getenv("MEDIACRAWLER_PATH", "").strip()
    bundled = PROJECT_ROOT / "MediaCrawler"
    root = Path(configured).expanduser() if configured else (
        bundled if bundled.is_dir() else PROJECT_ROOT.parent / "MediaCrawler"
    )
    return root.resolve()


def _split_env_list(value: str, fallback: List[str]) -> List[str]:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return list(dict.fromkeys(items or fallback))


def default_platforms() -> List[str]:
    return _split_env_list(os.getenv("MEDIACRAWLER_PLATFORMS", ""), DEFAULT_PLATFORMS)


def default_keywords() -> List[str]:
    return _split_env_list(os.getenv("MEDIACRAWLER_KEYWORDS", ""), DEFAULT_KEYWORDS)


def _runner_command(root: Path) -> List[str]:
    """Prefer `uv run` (auto-provisions Python 3.11); fall back to python3."""
    if shutil.which("uv"):
        return ["uv", "run", "main.py"]
    return [sys.executable, "main.py"]


def run_platform_crawl(
    root: Path,
    platform: str,
    keywords: List[str],
    login_type: str = "qrcode",
    max_notes: int = 20,
    headless: bool = False,
    timeout: int = 1800,
) -> int:
    """Run one MediaCrawler search crawl. Returns the process exit code."""
    if platform not in DEFAULT_PLATFORMS or login_type not in {"qrcode", "phone", "cookie"}:
        raise ValueError("Unsupported platform or login type")
    if max_notes < 1 or timeout < 1 or not keywords or not all(keyword.strip() for keyword in keywords):
        raise ValueError("Keywords, max_notes, and timeout must be non-empty and positive")
    command = [
        *_runner_command(root),
        "--platform", platform,
        "--lt", login_type,
        "--type", "search",
        "--keywords", ",".join(keywords),
        "--get_comment", "false",
        "--save_data_option", "jsonl",
        "--crawler_max_notes_count", str(max_notes),
        "--headless", "true" if headless else "false",
    ]
    logger.info("MediaCrawler crawl: platform=%s keywords=%s", platform, ",".join(keywords))
    try:
        with subprocess.Popen(command, cwd=str(root), start_new_session=True) as process:
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # Kill uv and its crawler/browser children together on timeout.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait()
                logger.error("MediaCrawler crawl timed out for platform %s", platform)
                return 124
    except OSError as error:
        logger.error("Could not start MediaCrawler (%s). Is it installed at %s?", error, root)
        return 127
    if returncode != 0:
        logger.warning("MediaCrawler exited with code %s for platform %s", returncode, platform)
    return returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl Chinese platforms with MediaCrawler and import results into the search index",
    )
    parser.add_argument("--platforms", default=None, help="Comma-separated platforms (default: env MEDIACRAWLER_PLATFORMS or xhs,zhihu,bili,wb,tieba)")
    parser.add_argument("--keywords", default=None, help="Comma-separated search keywords (default: env MEDIACRAWLER_KEYWORDS or a curated 暑研 list)")
    parser.add_argument("--login-type", choices=["qrcode", "phone", "cookie"], default=os.getenv("MEDIACRAWLER_LOGIN_TYPE", "qrcode"), help="MediaCrawler login type: qrcode|phone|cookie")
    parser.add_argument("--max-notes", type=int, default=os.getenv("MEDIACRAWLER_MAX_NOTES", "20"), help="Max notes per keyword per platform")
    parser.add_argument("--headless", action="store_true", help="Run the browser headless (login QR still needs a visible scan on first run)")
    parser.add_argument("--timeout", type=int, default=os.getenv("MEDIACRAWLER_TIMEOUT", "1800"), help="Per-platform crawl timeout in seconds")
    parser.add_argument("--import-only", action="store_true", help="Skip crawling; only import existing exports")
    parser.add_argument("--no-import", action="store_true", help="Crawl only; do not import afterwards")
    parser.add_argument("--dry-run", action="store_true", help="Parse and normalize without writing to SQLite (implies --import-only)")
    args = parser.parse_args()

    root = mediacrawler_root()
    platforms = _split_env_list(args.platforms or "", default_platforms())
    keywords = _split_env_list(args.keywords or "", default_keywords())
    if any(platform not in DEFAULT_PLATFORMS for platform in platforms):
        parser.error("Supported platforms: " + ", ".join(DEFAULT_PLATFORMS))
    if args.max_notes < 1 or args.timeout < 1:
        parser.error("--max-notes and --timeout must be positive")

    if not root.is_dir() and not (args.import_only or args.dry_run):
        raise SystemExit(
            f"MediaCrawler checkout not found at {root}.\n"
            "Clone it first, e.g.:\n"
            "  git clone --depth 1 https://github.com/NanmiCoder/MediaCrawler.git ../MediaCrawler\n"
            "or set MEDIACRAWLER_PATH to an existing checkout."
        )

    exit_codes: Dict[str, int] = {}
    if not (args.import_only or args.dry_run):
        if not shutil.which("uv"):
            logger.warning(
                "`uv` not found; falling back to the current interpreter. "
                "MediaCrawler needs Python >= 3.11 — install uv if the crawl fails."
            )
        logger.warning(
            "First run per platform requires interactive login (%s). "
            "Keep the terminal visible to scan the QR code.",
            args.login_type,
        )
        for platform in platforms:
            exit_codes[platform] = run_platform_crawl(
                root,
                platform,
                keywords,
                login_type=args.login_type,
                max_notes=args.max_notes,
                headless=args.headless,
                timeout=args.timeout,
            )

    stats = None
    if not args.no_import:
        if not args.dry_run:
            init_db()
        data_dir = root / "data"
        try:
            stats = import_mediacrawler_path(data_dir, dry_run=args.dry_run)
        except FileNotFoundError as error:
            raise SystemExit(str(error)) from None
        logger.info("Import finished: %s", stats)

    print(json.dumps({
        "mediacrawler_path": str(root),
        "platforms": platforms,
        "keywords": keywords,
        "crawl_exit_codes": exit_codes,
        "import_stats": stats,
    }, ensure_ascii=False, indent=2))

    if any(code not in (0,) for code in exit_codes.values()):
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
