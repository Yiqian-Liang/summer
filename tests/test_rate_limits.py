import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app import database
from app.rate_limits import enforce_rate_limit


class RateLimitTests(unittest.TestCase):
    def test_persistent_limit_is_scoped_by_bucket_and_subject(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, "DB_NAME", str(Path(directory) / "limits.db")), patch("app.rate_limits.time.time", return_value=3601):
            request = Request({"type": "http", "client": ("127.0.0.1", 1234), "headers": []})
            enforce_rate_limit(request, "register", 2, 3600)
            enforce_rate_limit(request, "register", 2, 3600)
            with self.assertRaises(HTTPException) as rejected:
                enforce_rate_limit(request, "register", 2, 3600)
            self.assertEqual(rejected.exception.status_code, 429)
            self.assertEqual(rejected.exception.headers["Retry-After"], "3599")
            enforce_rate_limit(request, "comments", 1, 3600)
            enforce_rate_limit(request, "register", 1, 3600, user_id=7)


if __name__ == "__main__":
    unittest.main()
