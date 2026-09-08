import datetime as dt
import tempfile
import unittest
from pathlib import Path

import app.database as database
from app.search_ranking import rank_search_results
from app.search_utils import (
    build_query_concepts,
    build_platform_search_query,
    canonicalize_source_url,
    text_contains_term,
)


class SearchTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_name = database.DB_NAME
        database.DB_NAME = str(Path(self.temp_dir.name) / "test.db")
        database.init_db()

    def tearDown(self):
        database.DB_NAME = self.original_db_name
        self.temp_dir.cleanup()

    def add_opportunity(self, title, description, url, source_type="university", **extra):
        now = "2026-07-13T00:00:00+00:00"
        row = {
            "title": title,
            "source_type": source_type,
            "source_url": url,
            "professor_name": "",
            "university": extra.pop("university", "Example University"),
            "department": "",
            "description": description,
            "requirements": "",
            "embedding": None,
            "hiring_signals": "[]",
            "has_direct_apply": 0,
            "direct_apply_link": "",
            "deadline": "",
            "published_at": "",
            "provider": "test",
            "source_query": "",
            "quality_score": 70,
            "created_at": now,
            "updated_at": now,
            **extra,
        }
        database.upsert_opportunity(row)

    def test_mixed_language_query_requires_every_concept(self):
        self.add_opportunity(
            "Fully funded PhD position in France",
            "A doctoral opening in a French computer vision lab.",
            "https://example.edu/france-phd",
        )
        self.add_opportunity(
            "Research internship in France",
            "A paid summer research internship.",
            "https://example.edu/france-internship",
        )
        self.add_opportunity(
            "Fully funded PhD position in Germany",
            "A doctoral opening.",
            "https://example.edu/germany-phd",
        )

        results = database.search_opportunities_fts("法国 博士", limit=20)
        self.assertEqual([row["source_url"] for row in results], ["https://example.edu/france-phd"])

    def test_short_terms_use_word_boundaries(self):
        self.add_opportunity(
            "Email systems research internship",
            "Open summer research position.",
            "https://example.edu/email",
        )
        self.add_opportunity(
            "AI research internship",
            "Open summer research position in artificial intelligence.",
            "https://example.edu/ai",
        )

        results = database.search_opportunities_fts("AI", limit=20)
        self.assertEqual([row["source_url"] for row in results], ["https://example.edu/ai"])
        self.assertFalse(text_contains_term("email systems", "ai"))

    def test_profile_pages_are_not_opportunities(self):
        self.add_opportunity(
            "Jane Doe - GPU PhD Student",
            "GPU computing research.",
            "https://www.linkedin.com/in/jane-doe",
            source_type="linkedin",
        )
        self.add_opportunity(
            "GPU Research Intern",
            "We are hiring a research intern for GPU systems.",
            "https://www.linkedin.com/jobs/view/123",
            source_type="linkedin",
        )

        results = database.search_opportunities_fts("GPU", limit=20)
        self.assertEqual([row["source_url"] for row in results], ["https://www.linkedin.com/jobs/view/123"])

    def test_expired_results_are_filtered_and_duplicates_collapse(self):
        now = dt.datetime(2026, 7, 13, tzinfo=dt.timezone.utc)
        candidates = [
            {
                "title": "Materials Science PhD Position",
                "description": "Fully funded doctoral opening.",
                "source_type": "university",
                "source_url": "https://example.edu/job?utm_source=a",
                "deadline": "2025-12-01",
                "quality_score": 90,
            },
            {
                "title": "Materials Science Research Internship 2026",
                "description": "Applications are open for a research internship.",
                "source_type": "university",
                "source_url": "https://example.edu/intern?utm_source=a",
                "quality_score": 70,
            },
            {
                "title": "Materials Science Research Internship 2026",
                "description": "Applications are open for a research internship.",
                "source_type": "university",
                "source_url": "https://example.edu/intern?utm_source=b",
                "quality_score": 60,
            },
        ]
        results = rank_search_results("材料科学", candidates, now=now)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_url"], "https://example.edu/intern")

    def test_same_long_title_at_different_universities_is_not_deduplicated(self):
        candidates = [
            {
                "title": "2026 Fully Funded PhD Positions in Computer Science",
                "description": "Open PhD applications in computer science.",
                "university": university,
                "source_type": "university",
                "source_url": f"https://{slug}.example/jobs/1",
                "quality_score": 80,
            }
            for university, slug in [("University A", "a"), ("University B", "b")]
        ]

        results = rank_search_results("computer science phd", candidates)
        self.assertEqual({row["university"] for row in results}, {"University A", "University B"})

    def test_query_parser_and_google_redirect_canonicalization(self):
        concepts = build_query_concepts("法国博士 computer vision")
        self.assertEqual(concepts[0][0], "法国")
        self.assertEqual(concepts[1][0], "博士")
        self.assertEqual(concepts[2][0].lower(), "computer vision")
        self.assertEqual(
            canonicalize_source_url(
                "https://www.google.com/url?sa=D&q=https%3A%2F%2Fexample.edu%2Fjob%3Futm_source%3Dx"
            ),
            "https://example.edu/job",
        )
        live_query = build_platform_search_query("法国 博士", "euraxess")
        self.assertIn('("法国" OR "france"', live_query)
        self.assertIn('("博士" OR "phd"', live_query)


if __name__ == "__main__":
    unittest.main()
