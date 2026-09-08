import json
import os
import unittest
from unittest.mock import Mock, patch

import requests

from app.rag import RAGWorkbench


class DraftingTests(unittest.TestCase):
    student = {"name": "Alice", "school": "Example University", "education_level": "undergraduate", "interest": "robotics"}
    project = {"name": "Dr. Chen", "title": "Summer robotics internship", "snippet": "Robot learning with visual inputs", "source_url": "https://example.edu/openings"}

    def response(self, subject="Research inquiry", body="Dear Dr. Chen, my name is Alice. I am interested in the Summer robotics internship. Best regards, Alice"):
        response = Mock()
        response.json.return_value = {"candidates": [{"content": {"parts": [{"text": json.dumps({"subject": subject, "body": body})}]}}]}
        return response

    def test_default_never_calls_ai_even_with_a_key_and_platform_enabled(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "secret", "ENABLE_AI_DRAFTING": "1"}), patch("app.rag.requests.post") as post:
            result = RAGWorkbench().generate_context_aware_draft(self.student, self.project)
        post.assert_not_called()
        self.assertEqual(result["mode"], "template")
        self.assertEqual(result["requested_mode"], "template")

    def test_capabilities_and_explicit_ai_unavailable_fallback(self):
        for env, reason in (({}, "disabled"), ({"ENABLE_AI_DRAFTING": "1"}, "missing_api_key")):
            with self.subTest(reason=reason), patch.dict(os.environ, env, clear=True), patch("app.rag.requests.post") as post:
                workbench = RAGWorkbench()
                self.assertEqual(workbench.drafting_capabilities()["reason"], reason)
                result = workbench.generate_context_aware_draft(self.student, self.project, use_ai=True)
                self.assertEqual(result["mode"], "template")
                self.assertEqual(result["requested_mode"], "ai")
                self.assertIn("warning", result)
                post.assert_not_called()
        with patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1"}, clear=True):
            self.assertEqual(RAGWorkbench("secret").drafting_capabilities(), {"ai_enabled": True, "ai_available": True, "provider": "gemini", "reason": "configured"})

    def test_free_templates_use_actual_facts_in_both_languages_without_invention(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                result = RAGWorkbench().generate_context_aware_draft(
                    self.student, self.project, resume_text="Built a ROS navigation demo.",
                    focus_area="visual navigation", language=language,
                )
                for fact in ("Alice", "Example University", "robotics", "ROS navigation", "visual navigation", self.project["title"]):
                    self.assertIn(fact, result["body"])
                for unsupported in ("**", "[Skill", "[技能", "Professor Dr.", "your paper", "attached", "strong background"):
                    self.assertNotIn(unsupported, result["body"])
                self.assertEqual(result["citations"], [self.project["source_url"]])
                self.assertNotIn("AI", result["body"])

    def test_templates_allow_missing_recipient_and_name_without_fake_signature(self):
        for language in ("en", "zh"):
            result = RAGWorkbench().generate_context_aware_draft({}, {}, language=language)
            self.assertGreater(len(result["body"]), 40)
            self.assertNotIn("Professor", result["body"])
            self.assertNotIn("Student", result["body"])
            self.assertNotIn("[", result["body"])
        result = RAGWorkbench()._generate_with_template(self.student, {"title": "Open position", "recent_paper": "A real paper"})
        self.assertIn("Open position", result["body"])
        self.assertNotIn("A real paper", result["body"])

    def test_tone_changes_free_template_and_concise_is_shorter(self):
        workbench = RAGWorkbench()
        drafts = {tone: workbench.generate_context_aware_draft(self.student, self.project, tone=tone, resume_text="A real skill. " * 70)["body"] for tone in ("professional", "enthusiastic", "concise")}
        self.assertIn("excited", drafts["enthusiastic"])
        self.assertLess(len(drafts["concise"]), len(drafts["professional"]))

    @patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1"})
    def test_ai_is_explicit_and_grounded_without_search_by_default(self):
        with patch("app.rag.requests.post", return_value=self.response()) as post:
            result = RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, use_ai=True, resume_text="PRIVATE_CV")
        self.assertEqual(result["mode"], "ai")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [])
        self.assertIn("PRIVATE_CV", post.call_args.kwargs["json"]["contents"][0]["parts"][0]["text"])
        self.assertEqual(post.call_args.kwargs["timeout"], (5, 30))

    @patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1"})
    def test_opt_in_search_never_receives_student_details(self):
        public_response = Mock()
        public_response.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Public lab information"}]}}]}
        with patch("app.rag.requests.post", side_effect=[public_response, self.response()]) as post:
            result = RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, use_ai=True, include_social_media=True, resume_text="PRIVATE_CV")
        self.assertEqual(result["mode"], "ai")
        search, drafting = [call.kwargs["json"] for call in post.call_args_list]
        self.assertTrue(search["tools"])
        for private in ("PRIVATE_CV", "Alice", "Example University"):
            self.assertNotIn(private, json.dumps(search))
        self.assertEqual(drafting["tools"], [])
        self.assertIn("Public lab information", json.dumps(drafting))

    @patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1"})
    def test_invalid_and_failed_ai_results_fall_back_without_logging_secrets(self):
        responses = [
            self.response(subject=[]), self.response(body=[]), self.response(body=""),
            self.response(subject="x" * 201), self.response(subject="Header\nInjection"),
            self.response(body="Unrelated generated text without supplied context. " * 3),
            self.response(body="Alice Summer robotics internship " + "x" * 12000),
        ]
        for response in responses:
            with self.subTest(response=response), patch("app.rag.requests.post", return_value=response):
                result = RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, use_ai=True)
                self.assertEqual(result["mode"], "template")
                self.assertEqual(result["requested_mode"], "ai")
                self.assertIn("warning", result)
        with patch("app.rag.requests.post", side_effect=requests.Timeout("SECRET_KEY PRIVATE_CV")), self.assertLogs("app.rag", level="WARNING") as logs:
            result = RAGWorkbench("SECRET_KEY").generate_context_aware_draft(self.student, self.project, use_ai=True, resume_text="PRIVATE_CV")
        self.assertEqual(result["mode"], "template")
        self.assertNotIn("SECRET_KEY", " ".join(logs.output))
        self.assertNotIn("PRIVATE_CV", " ".join(logs.output))

    @patch.dict(os.environ, {"ENABLE_AI_DRAFTING": "1"})
    def test_malformed_provider_responses_and_optional_search_failure(self):
        for data in (None, {}, [], {"candidates": []}, {"candidates": [{"content": {"parts": [None, 3, {}]}}]}):
            response = Mock()
            response.json.return_value = data
            with self.subTest(data=data), patch("app.rag.requests.post", return_value=response):
                result = RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, use_ai=True)
                self.assertEqual(result["mode"], "template")
                self.assertIn("warning", result)
        with patch("app.rag.requests.post", side_effect=[requests.Timeout, self.response()]):
            result = RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, use_ai=True, include_social_media=True)
        self.assertEqual(result["mode"], "ai")
        self.assertIn("Public research", result["warning"])

    def test_input_options_are_validated_before_any_request(self):
        for options in ({"use_ai": "false"}, {"include_social_media": "false"}, {"language": "unknown"}, {"tone": "unknown"}):
            with self.subTest(options=options), patch("app.rag.requests.post") as post, self.assertRaises(ValueError):
                RAGWorkbench("secret").generate_context_aware_draft(self.student, self.project, **options)
            post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
