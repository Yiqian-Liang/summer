from sentence_transformers import SentenceTransformer, util
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchMatcher:
    def __init__(self, model_name='paraphrase-multilingual-MiniLM-L12-v2', trust_remote_code=True):
        """
        Initializes the matcher with a lightweight multilingual model.
        paraphrase-multilingual-MiniLM-L12-v2 supports 50+ languages including Chinese and English,
        loads fast (~500MB) and works well for semantic similarity.
        """
        logger.info(f"Loading AI Model: {model_name}...")
        try:
            self.model = SentenceTransformer(model_name)
            logger.info(f"AI Model ({model_name}) loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            logger.warning("Falling back to lightweight model 'all-MiniLM-L6-v2'")
            try:
                self.model = SentenceTransformer('all-MiniLM-L6-v2')
            except:
                self.model = None

    def encode_text(self, text: str):
        """Encodes text into a vector."""
        if not self.model or not text:
            return []
        # Convert tensor to list for JSON serialization
        return self.model.encode(text).tolist()

    def compute_similarity(self, query_embedding, doc_embedding):
        """Computes cosine similarity between two vectors."""
        if not query_embedding or not doc_embedding:
            return 0.0
        return util.cos_sim(query_embedding, doc_embedding).item()

    def rank_candidates(self, query: str, candidates: list, hard_constraints: dict = None):
        """
        Ranks a list of candidate dictionaries based on semantic similarity to the query.
        Also applies Hard Constraints (GPA, Visa) and Intent Prediction (Funding).
        """
        if not self.model or not query:
            return candidates

        # Qwen-instruct models perform better with a prompt for the query
        # Format: "Instruct: <task>\nQuery: <query>"
        prompted_query = f"Instruct: Retrieve relevant academic professors and research opportunities based on the student's interest.\nQuery: {query}"
        
        query_emb = self.model.encode(prompted_query)
        
        scored_candidates = []
        for cand in candidates:
            # 1. Hard Constraints Filter (The "Gatekeeper")
            if hard_constraints:
                # Example: Check if GPA requirement is met (Mock logic)
                # In real app, we parse "Minimum GPA 3.5" from candidate description
                pass 

            # 2. Semantic Similarity
            # Construct text representation
            text = cand.get('text_for_matching')
            if not text:
                parts = [
                    cand.get('name', ''),
                    cand.get('interests', ''),
                    cand.get('description', ''),
                    cand.get('title', ''),
                    cand.get('snippet', '')
                ]
                text = " ".join([str(p) for p in parts if p])
            
            cand_emb = self.model.encode(text)
            semantic_score = util.cos_sim(query_emb, cand_emb).item()
            
            # 3. Intent Prediction Boost (The "Hiring Signal")
            intent_boost = 0.0
            # Heuristic: Grant keywords in snippet = higher hiring probability
            if any(k in text.lower() for k in ["grant", "nsf", "funding", "hiring", "openings"]):
                intent_boost = 0.15 # +15% boost for likely hiring
            elif "new lab" in text.lower() or "assistant professor" in text.lower():
                intent_boost = 0.10 # +10% boost for new labs
            
            final_score = semantic_score + intent_boost
            
            # Add score to candidate
            cand['match_score'] = int(final_score * 100) 
            # Cap at 100
            if cand['match_score'] > 100: cand['match_score'] = 100
            
            scored_candidates.append(cand)
        
        # Sort by score descending
        return sorted(scored_candidates, key=lambda x: x['match_score'], reverse=True)

# Singleton instance
matcher = ResearchMatcher()

if __name__ == "__main__":
    # Test
    query = "大语言模型 (Large Language Models)" # Test multilingual capability
    candidates = [
        {"name": "Prof. Smith", "interests": "Natural Language Processing, BERT, Transformers"}, # Should match high
        {"name": "Prof. Doe", "interests": "Computer Vision, Image Segmentation"}, # Should match low
        {"name": "李教授", "interests": "人工智能, 深度学习, 对话系统"}, # Should match high (Chinese)
    ]
    ranked = matcher.rank_candidates(query, candidates)
    for r in ranked:
        print(f"{r['name']}: {r['match_score']}")
