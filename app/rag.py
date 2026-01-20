import logging
from typing import Dict, List, Optional
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RAGWorkbench:
    """
    Retrieval-Augmented Generation (RAG) Workbench for Academic Applications.
    This module is responsible for generating high-quality, citation-backed
    application materials (Cold Emails, SOPs) by retrieving context from
    professor papers and student CVs.
    """
    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def generate_context_aware_draft(self, student_profile: Dict, professor_profile: Dict) -> Dict:
        """
        Generates a sophisticated email draft using RAG principles.
        Instead of a generic template, it injects specific "Contrastive Examples"
        and citation markers.
        """
        prof_name = professor_profile.get('name', 'Professor')
        paper_title = professor_profile.get('recent_paper', '')
        paper_context = professor_profile.get('snippet', 'Research in this field...')
        student_interest = student_profile.get('interest', '')
        
        # 1. Retrieval Step (Simulated)
        # In a real system, this would query a Vector DB for specific paragraphs in the paper
        # Here, we use the snippet/abstract we scraped.
        
        # 2. Logic & Reasoning (Chain of Thought)
        # We identify the "Intersection" between Student Skill & Prof Need
        
        subject = f"Research Inquiry: {student_interest} & {paper_title[:30]}..."
        
        # 3. Drafting with Citations
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
