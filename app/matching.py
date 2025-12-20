from sentence_transformers import SentenceTransformer, util
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchMatcher:
    def __init__(self, model_name='sentence-transformers/allenai-specter'):
        """
        Initializes the matcher with the SPECTER model (via HuggingFace).
        SPECTER is specialized for scientific document similarity.
        """
        logger.info(f"Loading Academic AI Model: {model_name}...")
        try:
            # We use the HuggingFace ID for SPECTER
            self.model = SentenceTransformer(model_name)
            logger.info("Academic AI Model (SPECTER) loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load AI model: {e}")
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

    def rank_candidates(self, query: str, candidates: list):
        """
        Ranks a list of candidate dictionaries based on semantic similarity to the query.
        """
        if not self.model or not query:
            return candidates

        # For SPECTER, queries should ideally be paper titles/abstracts, 
        # but keywords work reasonably well too.
        query_emb = self.model.encode(query)
        
        scored_candidates = []
        for cand in candidates:
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
            score = util.cos_sim(query_emb, cand_emb).item()
            
            # Add score to candidate
            cand['match_score'] = int(score * 100) 
            scored_candidates.append(cand)
        
        # Sort by score descending
        return sorted(scored_candidates, key=lambda x: x['match_score'], reverse=True)

# Singleton instance
matcher = ResearchMatcher()

if __name__ == "__main__":
    # Test
    query = "Large Language Models"
    candidates = [
        {"name": "Prof. Smith", "interests": "Natural Language Processing, BERT, Transformers"}, # Should match high
        {"name": "Prof. Doe", "interests": "Computer Vision, Image Segmentation"}, # Should match low
    ]
    ranked = matcher.rank_candidates(query, candidates)
    for r in ranked:
        print(f"{r['name']}: {r['match_score']}")
