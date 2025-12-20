import logging
from typing import Dict, List, Optional
import os
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchAgent:
    """
    An 'Analysis Agent' that simulates the reasoning capability of a human researcher.
    In a full production environment, this would call GPT-4o or Claude 3.5.
    Here, we simulate the Chain-of-Thought process or use a lightweight local heuristic.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def analyze_match(self, student_query: str, candidate_profile: Dict) -> Dict:
        """
        Analyzes WHY a student matches a professor.
        Returns a dictionary with 'reasoning', 'pros', 'cons'.
        """
        candidate_text = candidate_profile.get('text_for_matching', '')
        candidate_name = candidate_profile.get('name', 'Candidate')
        
        # 1. Check for specific keyword overlaps (Heuristic "Thinking")
        query_terms = set(student_query.lower().split())
        profile_terms = set(candidate_text.lower().split())
        common_terms = query_terms.intersection(profile_terms)
        
        # 2. Simulate "Reasoning" Output
        if self.api_key:
            return self._call_llm_reasoning(student_query, candidate_profile)
        else:
            # Fallback: Rule-based "Agent"
            if len(common_terms) > 0:
                reasoning = (
                    f"Found direct keyword overlap ({', '.join(common_terms)}). "
                    f"The candidate's work in {candidate_profile.get('interests', 'their field')} "
                    f"aligns with your interest in {student_query}."
                )
                verdict = "Strong Match"
            else:
                # If no keyword overlap but high semantic score (from SPECTER), the agent infers connection
                reasoning = (
                    f"Although exact keywords don't match, the semantic context of '{candidate_text[:50]}...' "
                    f"suggests a conceptual link to '{student_query}'. "
                    "This is likely an interdisciplinary opportunity."
                )
                verdict = "Potential Pivot"
            
            return {
                "verdict": verdict,
                "reasoning": reasoning,
                "suggested_action": "Read recent paper" if verdict == "Strong Match" else "Check lab website"
            }

    def _call_llm_reasoning(self, query, profile) -> Dict:
        """
        Real implementation for GPT-4o integration.
        """
        # Placeholder for actual API call
        # response = client.chat.completions.create(...)
        return {
            "verdict": "AI Analysis",
            "reasoning": "GPT-4o would analyze the specific paper abstract here...",
            "suggested_action": "Email Draft Prepared"
        }

    def summarize_research(self, raw_text: str) -> str:
        """
        Summarizes a long raw text (e.g., from a scraped website) into a 1-sentence focus.
        """
        # Simple extraction for MVP
        if len(raw_text) > 200:
            return raw_text[:197] + "..."
        return raw_text

if __name__ == "__main__":
    agent = ResearchAgent()
    analysis = agent.analyze_match("Reinforcement Learning", {
        "name": "Dr. Sutton",
        "text_for_matching": "We focus on temporal difference learning and decision making.",
        "interests": "AI, ML"
    })
    print(analysis)
