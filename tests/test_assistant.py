import os
import unittest
from unittest.mock import patch

from app.agents import ResearchAgent
from app.rag import RAGWorkbench
from app.recommender import Recommender


class AssistantTests(unittest.TestCase):
    def test_api_keys_stay_with_their_provider(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "openai-test-key"}, clear=True):
            self.assertIsNone(ResearchAgent().api_key)
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gemini-test-key"}, clear=True):
            self.assertEqual(ResearchAgent().api_key, "gemini-test-key")
            self.assertIn("/gemini-2.5-flash:", ResearchAgent().gemini_url)
            self.assertIn("/gemini-2.5-flash:", RAGWorkbench().api_url)
        with patch.dict(os.environ, {"GEMINI_MODEL": "gemini-custom"}, clear=True):
            self.assertIn("/gemini-custom:", ResearchAgent().gemini_url)
            self.assertIn("/gemini-custom:", RAGWorkbench(gemini_api_key="test").api_url)

    def test_tags_and_preferences_match_whole_terms(self):
        agent = ResearchAgent(api_key="test")
        self.assertNotIn("#全奖", agent._generate_tags("Unpaid research internship", ""))
        self.assertNotIn("强化学习", agent._extract_research_focus("Early career biology opportunities", ""))
        self.assertIn("强化学习", agent._extract_research_focus("RL research", ""))
        self.assertIn("#暑研", agent._generate_tags("Undergraduate robotics internship", ""))
        self.assertIn("#机器人", agent._generate_tags("Undergraduate robotics internship", ""))
        recommender = Recommender()
        profile = {"region_preferences": ["north_america"]}
        self.assertEqual(recommender.calculate_preference_score({"title": "Campus research position", "university": "Oxford University"}, profile), 50)
        self.assertEqual(recommender.calculate_preference_score({"title": "US research position"}, profile), 60)
        self.assertEqual(recommender.calculate_behavior_score({"title": "Email systems"}, [{"action_type": "search", "target_id": "AI"}]), 0)
        history = [{"action_type": "search", "target_id": "AI"}] + [{"action_type": "search", "target_id": "unrelated"}] * 20
        self.assertEqual(recommender.calculate_behavior_score({"title": "AI research"}, history), 2)



if __name__ == "__main__":
    unittest.main()
