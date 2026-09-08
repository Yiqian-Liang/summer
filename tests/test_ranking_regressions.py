import datetime as dt
import unittest
from unittest.mock import Mock, patch

from app.matching import ResearchMatcher
from app.search_ranking import deduplicate_results, rank_search_results
from app.search_utils import build_live_search_queries, build_query_concepts, text_contains_term


class RankingRegressionTests(unittest.TestCase):
    def opportunity(self, **changes):
        return {
            "title": "Machine learning research internship",
            "description": "We are hiring research interns in France.",
            "source_url": "https://example.edu/openings",
            "source_type": "university",
            **changes,
        }

    def test_scraper_query_is_not_evidence(self):
        unrelated = self.opportunity(title="GPU course", description="Computer architecture lectures", source_query="法国 博士")
        self.assertEqual(rank_search_results("法国 博士", [unrelated]), [])
        no_opening = self.opportunity(title="Machine learning tutorial", description="Lecture notes", source_query="research internship")
        self.assertEqual(rank_search_results("machine learning", [no_opening]), [])

    def test_degree_in_biography_is_not_a_recruitment_signal(self):
        biography = self.opportunity(title="Jane Doe, PhD", description="Machine learning researcher in France")
        self.assertEqual(rank_search_results("machine learning", [biography]), [])

    def test_phd_query_does_not_expand_to_masters(self):
        masters = self.opportunity(title="Master's graduate student opening", description="Fully funded in France")
        self.assertEqual(rank_search_results("法国 博士", [masters]), [])

    def test_live_query_variants_keep_all_constraints(self):
        for variant in build_live_search_queries("法国 博士 机器学习"):
            for concept in build_query_concepts("法国 博士 机器学习"):
                self.assertTrue(any(text_contains_term(variant, term) for term in concept), variant)

    def test_plural_and_hyphen_queries_have_recall_aliases(self):
        concepts = build_query_concepts("large-language-models research internships")
        self.assertIn("large language model", concepts[0])
        self.assertIn("research internship", concepts[1])
        result = self.opportunity(title="Large language model research internship")
        self.assertEqual(len(rank_search_results("large-language-models research internships", [result])), 1)
        self.assertTrue(text_contains_term("large-language-models", "large language model"))
        self.assertFalse(text_contains_term("email", "AI"))

    def test_common_chinese_request_words_do_not_become_constraints(self):
        concepts = build_query_concepts("帮我找法国的计算机科学暑研项目")
        self.assertEqual([concept[0] for concept in concepts], ["法国", "计算机科学", "暑研"])
        self.assertEqual(build_query_concepts("the and for"), [])

    def test_past_year_without_deadline_and_chinese_dates(self):
        now = dt.datetime(2026, 9, 8, tzinfo=dt.timezone.utc)
        old = self.opportunity(title="2025暑研 Machine learning")
        self.assertEqual(rank_search_results("machine learning", [old], now=now), [])
        self.assertTrue(rank_search_results("machine learning", [old], now=now, include_expired=True)[0]["is_expired"])
        old["deadline"] = "2027年1月15日"
        self.assertEqual(len(rank_search_results("machine learning", [old], now=now)), 1)
        old["deadline"] = "2026年8月31日"
        self.assertEqual(rank_search_results("machine learning", [old], now=now), [])
        old["deadline"] = "rolling"
        self.assertEqual(len(rank_search_results("machine learning", [old], now=now)), 1)
        old["deadline"] = "2027"
        self.assertEqual(len(rank_search_results("machine learning", [old], now=now)), 1)

    def test_source_filters_apply_in_shared_ranker(self):
        candidate = self.opportunity()
        self.assertEqual(rank_search_results("machine learning", [candidate], source_filter="小红书"), [])
        self.assertEqual(len(rank_search_results("machine learning", [candidate], source_filter="official")), 1)

    def test_real_linkedin_post_is_not_a_profile(self):
        post = self.opportunity(title="Machine learning internship on LinkedIn", source_type="linkedin", source_url="https://www.linkedin.com/posts/professor-recruitment-123")
        self.assertEqual(len(rank_search_results("machine learning", [post])), 1)

    def test_invalid_quality_does_not_abort_results(self):
        for quality in ("unknown", "nan", float("inf")):
            results = rank_search_results("machine learning", [self.opportunity(quality_score=quality)])
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["quality_score"], 0)

    def test_bridge_duplicates_collapse_to_best_result(self):
        candidates = [
            self.opportunity(title="Opening A", university="Example", source_url="https://example.edu/a", search_score=70),
            self.opportunity(title="Opening B", university="Example", source_url="https://example.edu/b", search_score=90, source_type="linkedin"),
            self.opportunity(title="Opening B", university="Example", source_url="https://example.edu/a", search_score=80, source_type="微信"),
            self.opportunity(title="Opening C", university="Example", source_url="https://example.edu/b", search_score=60, source_type=None, matched_sources=["nsf"]),
        ]
        results = deduplicate_results(candidates)
        self.assertEqual(results, [dict(candidates[1], matched_sources=["linkedin", "nsf", "university", "wechat"])])
        self.assertIsNot(results[0], candidates[1])
        self.assertNotIn("matched_sources", candidates[1])
        self.assertEqual(candidates[3]["matched_sources"], ["nsf"])
        self.assertEqual(deduplicate_results([{"title": "Raw title", "url": "https://example.org/raw"}])[0]["matched_sources"], [])

    def test_unknown_institutions_and_case_sensitive_urls_remain_distinct(self):
        rows = [self.opportunity(source_url=url) for url in (
            "https://a.example/job", "https://b.example/job", "https://a.example/JOB",
        )]
        self.assertEqual(len(deduplicate_results(rows)), 3)
        rows = [self.opportunity(source_url=url) for url in (
            "https://example.edu/job?a=1&b=2", "https://example.edu/job?b=2&a=1&utm_source=x",
        )]
        self.assertEqual(len(deduplicate_results(rows)), 1)

    def test_query_embedding_failure_falls_back_to_existing_scores(self):
        matcher = ResearchMatcher()
        matcher._load_attempted = True
        matcher.model = Mock()
        matcher.model.encode.side_effect = RuntimeError("model unavailable")
        candidates = [self.opportunity(search_score=40), self.opportunity(search_score=80)]
        self.assertEqual(matcher.rank_candidates("machine learning", candidates), list(reversed(candidates)))

    def test_semantic_head_keeps_comparable_scores_and_does_not_mutate_input(self):
        matcher = ResearchMatcher()
        matcher._load_attempted = True
        matcher.model = Mock()
        similarity = Mock()
        similarity.tolist.return_value = [0.0] * 199 + [1.0]
        util = Mock()
        util.cos_sim.return_value = [similarity]
        candidates = [self.opportunity(id=index, search_score=70, match_score=70) for index in range(200)]
        tail = self.opportunity(search_score=69, match_score=69)
        with patch("app.matching.util", util):
            ranked = matcher.rank_candidates("machine learning", candidates)
        self.assertEqual((ranked[0]["id"], ranked[0]["match_score"]), (199, 75))
        self.assertGreaterEqual(min(row["match_score"] for row in ranked), tail["match_score"])
        self.assertTrue(all(row["match_score"] == 70 and "semantic_score" not in row for row in candidates))


if __name__ == "__main__":
    unittest.main()
