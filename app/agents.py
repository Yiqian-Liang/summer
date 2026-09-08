import logging
from typing import Dict, List, Optional
import os
import requests
import re
import urllib.parse

from app.search_utils import text_contains_term

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def extract_email(text: str) -> Optional[str]:
    """Extract email address from text using regex."""
    if not text:
        return None
    # Common academic email patterns
    email_pattern = r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.(edu|ac\.[a-z]{2}|edu\.[a-z]{2}|[a-z]{2,})'
    matches = re.findall(email_pattern, text.lower())
    if matches:
        # Return the first full match
        full_matches = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text.lower())
        if full_matches:
            return full_matches[0]
    return None


def generate_email_search_url(professor_name: str, university: str = None) -> str:
    """Generate a Google search URL to find professor's email."""
    query_parts = []
    if professor_name:
        query_parts.append(professor_name)
    if university:
        query_parts.append(university)
    query_parts.append("email")

    query = " ".join(query_parts)
    return f"https://www.google.com/search?q={urllib.parse.quote(query)}"


def generate_homepage_search_url(professor_name: str, university: str = None) -> str:
    """Generate a Google search URL to find professor's homepage."""
    query_parts = []
    if professor_name:
        query_parts.append(professor_name)
    if university:
        query_parts.append(university)
    query_parts.append("homepage")

    query = " ".join(query_parts)
    return f"https://www.google.com/search?q={urllib.parse.quote(query)}"


class ResearchAgent:
    """
    An 'Analysis Agent' that simulates the reasoning capability of a human researcher.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash"
        self.gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def detect_application_form(self, text: str) -> Optional[str]:
        """
        Scans text for Google Forms, Typeform, or Qualtrics links.
        Returns the first valid form URL found, or None.
        """
        if not text:
            return None
            
        # Regex patterns for common form and scheduling services used by labs.
        patterns = [
            r'https?://docs\.google\.com/forms/d/[\w-]+[\w/?=&.-]*',
            r'https?://forms\.gle/[\w-]+',
            r'https?://[\w-]+\.typeform\.com/to/[\w-]+[\w/?=&.-]*',
            r'https?://[\w.-]+\.qualtrics\.com/[\w/?=&./-]+',
            r'https?://airtable\.com/[\w/?=&.-]+',
            r'https?://forms\.office\.com/[\w/?=&.-]+',
            r'https?://(?:www\.)?jotform\.com/[\w/?=&.-]+',
            r'https?://(?:www\.)?tally\.so/r/[\w-]+',
            r'https?://[\w.-]*formstack\.com/forms/[\w/?=&.-]+',
            r'https?://(?:www\.)?surveymonkey\.com/r/[\w-]+',
            r'https?://calendly\.com/[\w/?=&.-]+',
            r'https?://(?:www\.)?notion\.site/[\w/?=&.-]+',
            r'https?://wj\.qq\.com/[\w/?=&.-]+' # Tencent Questionnaire
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        
        return None

    def analyze_match(self, student_query: str, candidate_profile: Dict, use_llm: bool = False) -> Dict:
        """
        Generates tags and extracts key info for the opportunity.
        Returns a dictionary with 'tags' and 'research_focus'.
        """
        candidate_text = candidate_profile.get('text_for_matching', '') or candidate_profile.get('description', '')
        title = candidate_profile.get('title', '')

        # Generate tags based on content
        tags = self._generate_tags(title, candidate_text)

        # Extract research focus/topic
        research_focus = self._extract_research_focus(title, candidate_text)

        result = {
            "verdict": "",
            "reasoning": "",
            "tags": tags,
            "research_focus": research_focus,
            "suggested_action": ""
        }

        if use_llm and self.api_key and student_query:
            llm_result = self._call_llm_reasoning(student_query, candidate_profile)
            result.update(llm_result)
            
        return result

    def _extract_research_focus(self, title: str, text: str) -> str:
        """Extract the main research focus/topic from the content."""
        title = title or ""
        text = text or ""
        combined = (title + " " + text).lower()

        # Research area keywords mapping
        focus_keywords = {
            "大语言模型": ["llm", "large language model", "gpt", "chatgpt", "language model"],
            "自然语言处理": ["nlp", "natural language processing", "text mining", "sentiment"],
            "计算机视觉": ["computer vision", "image", "video", "object detection", "segmentation"],
            "机器学习": ["machine learning", "deep learning", "neural network"],
            "强化学习": ["reinforcement learning", "rl", "decision making"],
            "机器人学": ["robotics", "robot", "autonomous", "manipulation"],
            "数据挖掘": ["data mining", "data science", "analytics"],
            "安全与隐私": ["security", "privacy", "cryptography", "adversarial"],
            "人机交互": ["hci", "human computer interaction", "user experience", "ux"],
            "图形学": ["graphics", "3d", "rendering", "visualization"],
            "生物信息学": ["bioinformatics", "genomics", "biology", "medical"],
            "语音识别": ["speech recognition", "speech", "audio", "asr"],
            "推荐系统": ["recommendation", "recommender", "personalization"],
            "知识图谱": ["knowledge graph", "knowledge base", "ontology"],
            "多模态": ["multimodal", "multi-modal", "vision-language"],
        }

        found_focuses = []
        for focus_zh, keywords in focus_keywords.items():
            for kw in keywords:
                if text_contains_term(combined, kw):
                    found_focuses.append(focus_zh)
                    break

        if found_focuses:
            return "、".join(found_focuses[:2])  # Max 2 focuses
        return ""

    def _generate_tags(self, title: str, text: str) -> List[str]:
        """Generate relevant tags based on content analysis."""
        title = title or ""
        text = text or ""
        tags = []
        combined = (title + " " + text).lower()

        # Target audience tags
        if any(text_contains_term(combined, kw) for kw in ['phd', 'doctoral', '博士', 'ph.d']):
            tags.append('#PhD')
        if any(text_contains_term(combined, kw) for kw in ['master', '硕士', 'ms ', 'msc', 'graduate student']):
            tags.append('#硕士')
        if any(text_contains_term(combined, kw) for kw in ['undergrad', 'undergraduate', '本科', 'bachelor', 'reu', 'summer research', '暑研', 'intern', 'internship']):
            tags.append('#暑研')
        if any(text_contains_term(combined, kw) for kw in ['postdoc', 'postdoctoral', '博后', 'post-doc']):
            tags.append('#博后')

        # Funding tags
        if any(text_contains_term(combined, kw) for kw in ['fully funded', 'full funding', '全奖', 'scholarship', 'stipend', 'paid']):
            tags.append('#全奖')

        # Field tags
        if any(text_contains_term(combined, kw) for kw in ['nlp', 'natural language', '自然语言', 'language model', 'llm', 'chatgpt', 'gpt']):
            tags.append('#NLP')
        if any(text_contains_term(combined, kw) for kw in ['machine learning', 'ml', '机器学习', 'deep learning', '深度学习']):
            tags.append('#ML')
        if any(text_contains_term(combined, kw) for kw in ['computer vision', 'cv', '计算机视觉', 'image', 'video']):
            tags.append('#CV')
        if any(text_contains_term(combined, kw) for kw in ['robot', 'robotics', 'robotic', '机器人', 'autonomous', 'control']):
            tags.append('#机器人')
        if any(text_contains_term(combined, kw) for kw in ['data science', '数据科学', 'analytics', 'big data']):
            tags.append('#数据科学')
        if any(text_contains_term(combined, kw) for kw in ['security', '安全', 'crypto', 'privacy']):
            tags.append('#安全')
        if any(text_contains_term(combined, kw) for kw in ['system', '系统', 'distributed', 'cloud', 'network']):
            tags.append('#系统')
        if any(text_contains_term(combined, kw) for kw in ['biology', '生物', 'bioinformatics', 'genomic', 'medical', '医学']):
            tags.append('#生物')
        if any(text_contains_term(combined, kw) for kw in ['graphics', '图形', '3d', 'rendering', 'visualization']):
            tags.append('#图形学')
        if any(text_contains_term(combined, kw) for kw in ['hci', 'human computer', '人机交互', 'user experience', 'ux']):
            tags.append('#HCI')

        # Location tags
        if any(text_contains_term(combined, kw) for kw in ['usa', 'united states', '美国', 'america']):
            tags.append('#美国')
        if any(text_contains_term(combined, kw) for kw in ['uk', 'united kingdom', '英国', 'britain']):
            tags.append('#英国')
        if any(text_contains_term(combined, kw) for kw in ['canada', '加拿大']):
            tags.append('#加拿大')
        if any(text_contains_term(combined, kw) for kw in ['europe', '欧洲', 'germany', 'france', 'switzerland']):
            tags.append('#欧洲')
        if any(text_contains_term(combined, kw) for kw in ['singapore', '新加坡']):
            tags.append('#新加坡')
        if any(text_contains_term(combined, kw) for kw in ['hong kong', '香港']):
            tags.append('#香港')

        # Limit to 5 most relevant tags
        return tags[:5] if tags else ['#招生']


    def _call_llm_reasoning(self, query: str, profile: Dict) -> Dict:
        """
        Uses Gemini to analyze the match between the student's query and the opportunity profile.
        """
        if not self.api_key:
            return {
                "verdict": "",
                "reasoning": "",
                "suggested_action": ""
            }

        candidate_text = profile.get('text_for_matching', '') or profile.get('description', '')
        title = profile.get('title', '')

        prompt = f"""
        Act as a research advisor. Analyze if this opportunity matches the student's interest.

        Student Interest: "{query}"
        Opportunity Title: "{title}"
        Description: "{candidate_text[:1000]}"

        Provide a JSON response with:
        1. verdict: "High Match", "Potential Match", or "Low Match"
        2. reasoning: A 1-sentence explanation of why.
        3. suggested_action: "Apply Now", "Email Professor", or "Skip".
        """

        try:
            payload = {
                "contents": [{"parts": [{"text": prompt}]}]
            }
            headers = {"Content-Type": "application/json"}
            params = {"key": self.api_key}

            response = requests.post(self.gemini_url, json=payload, headers=headers, params=params, timeout=10)
            data = response.json()

            if 'candidates' in data:
                text = data['candidates'][0]['content']['parts'][0]['text']
                # Try to parse JSON
                import json
                try:
                    text = text.replace("```json", "").replace("```", "").strip()
                    result = json.loads(text)
                    return result
                except:
                    return {
                        "verdict": "AI Analysis",
                        "reasoning": text[:100],
                        "suggested_action": "Check details"
                    }
        except Exception as e:
            logger.error(f"LLM Reasoning Error: {e}")

        return {
            "verdict": "",
            "reasoning": "",
            "suggested_action": ""
        }

    def summarize_research(self, raw_text: str) -> str:
        """
        Summarizes a long raw text (e.g., from a scraped website) into a 1-sentence focus.
        """
        # Simple extraction for MVP
        if len(raw_text) > 200:
            return raw_text[:197] + "..."
        return raw_text

    def generate_email_draft(self, student_name: str, student_interest: str, professor_profile: Dict) -> str:
        """
        Generates a high-quality, personalized email draft.
        This follows the 'Safe Cold Emailing' best practices:
        - Specific reference to recent work
        - Clear connection to student's background
        - Clear call to action (read paper/chat)
        """
        prof_name = professor_profile.get('name', 'Professor')
        prof_email = professor_profile.get('email', '[Email Not Found]')
        paper_topic = professor_profile.get('recent_paper', 'your recent work')
        interests = professor_profile.get('interests', 'your research area')
        
        # In a real app with LLM access, this would be generated by GPT-4o
        # Here we use a structured template system
        
        header = f"To: {prof_email}\n"
        subject_line = f"Subject: Inquiry from {student_name}: Research on {interests}"
        body = f"""
Dear {prof_name},

I hope this email finds you well.

My name is {student_name}, and I am writing to express my strong interest in your research group, particularly your work on {interests}. I recently read about "{paper_topic}" and found the approach to be fascinating.

I have a background in {student_interest}, and I see a strong alignment between my skills and your lab's direction. I would love the opportunity to briefly discuss your ongoing projects and potential summer research opportunities.

Thank you for your time and consideration.

Best regards,
{student_name}
"""
        return f"{header}{subject_line}\n{body}"

if __name__ == "__main__":
    agent = ResearchAgent()
    analysis = agent.analyze_match("Reinforcement Learning", {
        "name": "Dr. Sutton",
        "text_for_matching": "We focus on temporal difference learning and decision making.",
        "interests": "AI, ML"
    })
    print(analysis)
    
    draft = agent.generate_email_draft("Alice", "Deep RL", {
        "name": "Dr. Sutton",
        "email": "rich@ualberta.ca",
        "interests": "Temporal Difference Learning",
        "recent_paper": "The Bitter Lesson"
    })
    print("\n--- Email Draft ---\n")
    print(draft)
