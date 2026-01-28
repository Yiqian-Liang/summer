import logging
from typing import Dict, List, Optional
import os
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RAGWorkbench:
    """
    Retrieval-Augmented Generation (RAG) Workbench for Academic Applications.
    Now supports Hybrid Mode:
    1. Local Template Logic (Fast, Free)
    2. Gemini/GPT-4o API (Deep Reasoning, Paid)
    """
    def __init__(self, gemini_api_key: str = None):
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"
        
        # Tool Definition for Google Search (Grounding)
        self.tools = {
            "google_search_retrieval": {
                "dynamic_retrieval_config": {
                    "mode": "MODE_DYNAMIC",
                    "dynamic_threshold": 0.7,
                }
            }
        }

    def generate_context_aware_draft(self, student_profile: Dict, professor_profile: Dict, include_social_media: bool = False) -> Dict:
        """
        Generates a draft. If API key is present, uses Gemini for 'Deep Drafting'.
        Otherwise falls back to 'Template Drafting'.
        """
        if self.gemini_api_key:
            return self._generate_with_gemini(student_profile, professor_profile, include_social_media)
        else:
            return self._generate_with_template(student_profile, professor_profile)

    def _generate_with_gemini(self, student_profile: Dict, professor_profile: Dict, include_social_media: bool = False) -> Dict:
        """
        Uses Gemini 2.0 Flash to generate a highly personalized email.
        It performs 'Online Reasoning' if the model supports tool use (simulated here via prompt).
        """
        prof_name = professor_profile.get('name', 'Professor')
        paper_context = professor_profile.get('snippet', '')
        student_interest = student_profile.get('interest', '')
        
        social_prompt = ""
        if include_social_media:
            social_prompt = f"""
            5. [SOCIAL MEDIA DISCOVERY]: 
               - Use Google Search to find {prof_name}'s public posts on Instagram (site:instagram.com) or WeChat articles (site:weixin.qq.com).
               - Look for: Recent lab photos, conference trips, or informal thoughts on research.
               - If found, subtly mention it (e.g., "I saw your recent post about [Topic] on WeChat...") to show you did your homework.
               - If nothing relevant/professional is found, IGNORE this step.
            """

        prompt = f"""
        You are an expert academic mentor. Write a cold email for a student named {student_profile.get('name')} 
        interested in '{student_interest}' to Prof. {prof_name}.
        
        Context about the Professor's work: "{paper_context}"
        
        Requirements:
        1. Be concise (under 200 words).
        2. Reference the specific context provided.
        3. Do NOT sound like ChatGPT. Sound like a serious undergraduate researcher.
        4. Return output as JSON with 'subject' and 'body' fields.
        {social_prompt}
        """
        
        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "tools": [self.tools] if self.tools else [] # Enable Google Search Grounding
            }
            headers = {"Content-Type": "application/json"}
            params = {"key": self.gemini_api_key}
            
            response = requests.post(self.api_url, json=payload, headers=headers, params=params)
            data = response.json()
            
            # Simple parsing (In prod, use strict JSON mode)
            if 'candidates' not in data:
                logger.error(f"Gemini API Error: {data}")
                return self._generate_with_template(student_profile, professor_profile)

            raw_text = data['candidates'][0]['content']['parts'][0]['text']
            
            # Fallback parsing if JSON fails
            import json
            try:
                # Try to find JSON block
                json_str = raw_text.strip().replace("```json", "").replace("```", "")
                result = json.loads(json_str)
                return {
                    "subject": result.get("subject", "Research Inquiry"),
                    "body": result.get("body", raw_text),
                    "citations": ["Generated via Gemini (Grounded)"],
                    "hiring_signals": self._analyze_hiring_signals(professor_profile)
                }
            except:
                return {
                    "subject": f"Research Inquiry: {student_interest}",
                    "body": raw_text,
                    "citations": ["Generated via Gemini (Grounded)"],
                    "hiring_signals": self._analyze_hiring_signals(professor_profile)
                }
                
        except Exception as e:
            logger.error(f"Gemini API Error: {e}")
            return self._generate_with_template(student_profile, professor_profile)

    def _generate_with_template(self, student_profile: Dict, professor_profile: Dict) -> Dict:
        """
        Legacy Template Logic (Fast & Reliable).
        """
        prof_name = professor_profile.get('name', 'Professor')
        paper_title = professor_profile.get('recent_paper', '')
        paper_context = professor_profile.get('snippet', 'Research in this field...')
        student_interest = student_profile.get('interest', '')
        
        subject = f"Research Inquiry: {student_interest} & {paper_title[:30]}..."
        
        body = f"""
Dear Professor {prof_name},

I am a student with a strong background in **{student_interest}**, and I am writing to express my enthusiasm for your work on **{paper_title}**.

Specifically, I was intrigued by your approach to:
> "{paper_context[:150]}..." [1]

In my previous project, I encountered a similar challenge regarding [Specific Technical Problem], which I addressed using [Student's Method]. I believe this experience aligns well with your lab's current direction.

I would welcome the opportunity to discuss how my skills in [Skill 1] and [Skill 2] could contribute to your ongoing research.

Best regards,
{student_profile.get('name', 'Student')}

---
**References:**
[1] {paper_title} (Source: {professor_profile.get('source', 'Google Scholar')})
"""
        return {
            "subject": subject,
            "body": body,
            "citations": [paper_title],
            "hiring_signals": self._analyze_hiring_signals(professor_profile)
        }

    def _analyze_hiring_signals(self, profile: Dict) -> List[str]:
        """
        Predicts hiring intent based on available signals.
        """
        signals = []
        text = str(profile).lower()
        
        # Grant Signals
        if "nsf" in text or "grant" in text or "funding" in text:
            signals.append("💰 Recent Funding Detected (High Hiring Probability)")
            
        # Velocity Signals (New Lab)
        if "assistant professor" in text or "new lab" in text:
            signals.append("🚀 New Lab / Assistant Professor (Likely seeking students)")
            
        # Explicit Keywords
        if "hiring" in text or "looking for" in text or "openings" in text:
            signals.append("✅ Explicitly Hiring")
            
        return signals

if __name__ == "__main__":
    rag = RAGWorkbench()
    draft = rag.generate_context_aware_draft(
        {"name": "Alice", "interest": "RLHF"},
        {"name": "Dr. Bob", "recent_paper": "Proximal Policy Optimization", "snippet": "We introduce a new objective function that enables multiple epochs of minibatch updates...", "source": "Scholar"}
    )
    print(draft['body'])
    print(draft['hiring_signals'])
