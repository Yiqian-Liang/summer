"""Small persistent limits for public and paid-capability write paths."""
import hashlib
import os
import time

from fastapi import HTTPException, Request

from app.database import get_db


def init_rate_limit_table():
    db = get_db()
    try:
        with db.conn:
            db.execute("""CREATE TABLE IF NOT EXISTS request_limits (
                bucket TEXT NOT NULL, subject TEXT NOT NULL,
                window_start INTEGER NOT NULL, request_count INTEGER NOT NULL,
                PRIMARY KEY (bucket, subject, window_start)
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS request_limits_window ON request_limits(window_start)")
    finally:
        db.conn.close()


def _subject(request: Request, user_id=None) -> str:
    if user_id:
        return f"user:{int(user_id)}"
    address = request.client.host if request.client else "unknown"
    salt = os.getenv("RATE_LIMIT_SALT") or "summer-rate-limit"
    return "ip:" + hashlib.sha256(f"{salt}|{address}".encode()).hexdigest()


def enforce_rate_limit(request: Request, bucket: str, limit: int, window_seconds: int, user_id=None):
    """Count one request and reject when the fixed window is exhausted."""
    now = int(time.time())
    window_start = now - now % window_seconds
    subject = _subject(request, user_id)
    db = get_db()
    try:
        with db.conn:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS request_limits (
                bucket TEXT NOT NULL, subject TEXT NOT NULL,
                window_start INTEGER NOT NULL, request_count INTEGER NOT NULL,
                PRIMARY KEY (bucket, subject, window_start)
            )""")
            row = db.execute(
                "SELECT request_count FROM request_limits WHERE bucket = ? AND subject = ? AND window_start = ?",
                [bucket, subject, window_start],
            ).fetchone()
            if row and row[0] >= limit:
                retry_after = max(1, window_start + window_seconds - now)
                raise HTTPException(429, "请求过于频繁，请稍后再试。", headers={"Retry-After": str(retry_after)})
            db.execute("""INSERT INTO request_limits(bucket, subject, window_start, request_count)
                VALUES (?, ?, ?, 1) ON CONFLICT(bucket, subject, window_start)
                DO UPDATE SET request_count = request_count + 1""", [bucket, subject, window_start])
            # ponytail: fixed windows are enough at current traffic; use Redis only for multi-host deployment.
            db.execute("DELETE FROM request_limits WHERE window_start < ?", [now - 172800])
    finally:
        db.conn.close()
