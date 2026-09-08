"""Local, read-only bridge from the Summer search page to OpenCLI.

The bridge binds to loopback only, accepts requests from explicit Summer
origins, and invokes OpenCLI with an argument list (never a shell). Cookies
remain inside the user's Chrome/OpenCLI session.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Iterable, List, Tuple
from urllib.parse import urlsplit


HOST = "127.0.0.1"
DEFAULT_PORT = 19826
DEFAULT_ORIGINS = ("http://localhost:8000", "http://127.0.0.1:8000")
MAX_BODY_BYTES = 4096
MAX_QUERY_LENGTH = 200
MAX_RESULTS = 100
logger = logging.getLogger(__name__)


class CompanionError(Exception):
    def __init__(self, code: str, message: str, status: int = HTTPStatus.BAD_GATEWAY):
        super().__init__(message)
        self.code = code
        self.status = status


def normalize_origin(value: str) -> str:
    """Return a canonical HTTP(S) origin or raise ValueError."""
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("origin must be an http(s) origin")
    if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("origin must not contain credentials, a path, query, or fragment")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("origin has an invalid port") from error
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = (parsed.scheme.lower() == "http" and port == 80) or (
        parsed.scheme.lower() == "https" and port == 443
    )
    return f"{parsed.scheme.lower()}://{host}{f':{port}' if port and not default_port else ''}"


def validate_search_payload(payload: Any) -> Tuple[str, int]:
    if not isinstance(payload, dict):
        raise CompanionError("INVALID_REQUEST", "JSON body must be an object", HTTPStatus.BAD_REQUEST)
    query = payload.get("query")
    if not isinstance(query, str) or not query.strip():
        raise CompanionError("INVALID_QUERY", "query must be a non-empty string", HTTPStatus.BAD_REQUEST)
    query = " ".join(query.split())
    if len(query) > MAX_QUERY_LENGTH:
        raise CompanionError("INVALID_QUERY", f"query must be at most {MAX_QUERY_LENGTH} characters", HTTPStatus.BAD_REQUEST)
    limit = payload.get("limit", MAX_RESULTS)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
        raise CompanionError("INVALID_LIMIT", f"limit must be between 1 and {MAX_RESULTS}", HTTPStatus.BAD_REQUEST)
    return query, limit


def _clean(value: Any, max_length: int) -> str:
    return " ".join(str(value or "").split())[:max_length]


def normalize_results(payload: Any, limit: int) -> List[Dict[str, Any]]:
    """Keep only the small, display-safe result shape used by Summer."""
    if not isinstance(payload, list):
        raise CompanionError("INVALID_OPENCLI_OUTPUT", "OpenCLI returned an unexpected result shape")
    results: List[Dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = _clean(item.get("title"), 300)
        url = _clean(item.get("url"), 1000)
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if not title or parsed.scheme != "https" or not (
            hostname == "xiaohongshu.com" or hostname.endswith(".xiaohongshu.com")
        ):
            continue
        results.append(
            {
                "rank": len(results) + 1,
                "title": title,
                "author": _clean(item.get("author"), 120),
                "likes": _clean(item.get("likes"), 40),
                "published_at": _clean(item.get("published_at"), 20),
                "url": url,
            }
        )
        if len(results) >= limit:
            break
    return results


def resolve_opencli(executable: str) -> str:
    path = shutil.which(executable)
    if not path:
        raise CompanionError(
            "OPENCLI_NOT_FOUND",
            "OpenCLI is not installed or is not available on PATH",
            HTTPStatus.SERVICE_UNAVAILABLE,
        )
    return path


def run_xiaohongshu_search(query: str, limit: int, executable: str, timeout: int) -> List[Dict[str, Any]]:
    command = [
        resolve_opencli(executable),
        "xiaohongshu",
        "search",
        query,
        "--limit",
        str(limit),
        "--window",
        "background",
        "-f",
        "json",
    ]
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["OPENCLI_BROWSER_CONNECT_TIMEOUT"] = "8"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise CompanionError("OPENCLI_TIMEOUT", "OpenCLI search timed out", HTTPStatus.GATEWAY_TIMEOUT) from error
    if completed.returncode == 66:
        return []
    if completed.returncode:
        detail = completed.stderr.strip()
        logger.warning("OpenCLI Xiaohongshu search failed: %s", detail[:2000])
        lowered = detail.lower()
        if completed.returncode == 77 or "auth_required" in lowered or "login wall" in lowered:
            code, message = "LOGIN_REQUIRED", "Log in to Xiaohongshu in Chrome first"
        elif completed.returncode == 69:
            code, message = "EXTENSION_REQUIRED", "OpenCLI Browser Bridge is not connected"
        elif completed.returncode == 75:
            code, message = "OPENCLI_TIMEOUT", "OpenCLI search timed out or is temporarily busy"
        else:
            code, message = "OPENCLI_FAILED", "OpenCLI search failed; run opencli doctor locally"
        raise CompanionError(code, message)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        logger.warning("OpenCLI returned non-JSON stdout")
        raise CompanionError("INVALID_OPENCLI_OUTPUT", "OpenCLI did not return valid JSON") from error
    return normalize_results(payload, limit)


SearchRunner = Callable[[str, int, str, int], List[Dict[str, Any]]]


class CompanionServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        port: int,
        allowed_origins: Iterable[str],
        executable: str,
        timeout: int,
        search_runner: SearchRunner = run_xiaohongshu_search,
    ):
        super().__init__((HOST, port), CompanionHandler)
        self.allowed_origins = frozenset(allowed_origins)
        self.executable = executable
        self.timeout = timeout
        self.search_runner = search_runner


class CompanionHandler(BaseHTTPRequestHandler):
    server: CompanionServer

    def _allowed_origin(self) -> str:
        try:
            origin = normalize_origin(self.headers.get("Origin", ""))
        except ValueError:
            return ""
        return origin if origin in self.server.allowed_origins else ""

    def _cors_headers(self, origin: str) -> None:
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")

    def _send_json(self, status: int, payload: Dict[str, Any], origin: str = "") -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if origin:
            self._cors_headers(origin)
        self.end_headers()
        self.wfile.write(body)

    def _require_origin(self) -> str:
        origin = self._allowed_origin()
        if not origin:
            self._send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": {"code": "ORIGIN_DENIED"}})
        return origin

    def do_OPTIONS(self) -> None:
        origin = self._require_origin()
        if not origin:
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers(origin)
        self.end_headers()

    def do_GET(self) -> None:
        origin = self._require_origin()
        if not origin:
            return
        if self.path != "/v1/status":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"code": "NOT_FOUND"}}, origin)
            return
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "service": "summer-opencli-companion",
                "opencli_ready": bool(shutil.which(self.server.executable)),
            },
            origin,
        )

    def do_POST(self) -> None:
        origin = self._require_origin()
        if not origin:
            return
        if self.path != "/v1/search/xiaohongshu":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"code": "NOT_FOUND"}}, origin)
            return
        if self.headers.get_content_type() != "application/json":
            self._send_json(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                {"ok": False, "error": {"code": "CONTENT_TYPE_REQUIRED"}},
                origin,
            )
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 0 < length <= MAX_BODY_BYTES:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": {"code": "INVALID_BODY"}},
                origin,
            )
            return
        try:
            payload = json.loads(self.rfile.read(length))
            query, limit = validate_search_payload(payload)
            results = self.server.search_runner(query, limit, self.server.executable, self.server.timeout)
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "query": query, "count": len(results), "results": results},
                origin,
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": {"code": "INVALID_JSON", "message": "Body must be valid JSON"}},
                origin,
            )
        except CompanionError as error:
            self._send_json(
                error.status,
                {"ok": False, "error": {"code": error.code, "message": str(error)}},
                origin,
            )

    def log_message(self, format_string: str, *args: Any) -> None:
        logger.info("%s - %s", self.client_address[0], format_string % args)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local Summer-to-OpenCLI Xiaohongshu bridge")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--origin", action="append", default=[], help="Additional allowed Summer origin")
    parser.add_argument("--opencli", default=os.getenv("OPENCLI_BIN", "opencli"))
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not 5 <= args.timeout <= 300:
        parser.error("--timeout must be between 5 and 300 seconds")
    try:
        allowed_origins = {normalize_origin(origin) for origin in (*DEFAULT_ORIGINS, *args.origin)}
    except ValueError as error:
        parser.error(str(error))

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    server = CompanionServer(args.port, allowed_origins, args.opencli, args.timeout)
    opencli_status = shutil.which(args.opencli) or "not found"
    print(f"Summer OpenCLI companion: http://{HOST}:{args.port}")
    print(f"Allowed origins: {', '.join(sorted(allowed_origins))}")
    print(f"OpenCLI: {opencli_status}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
