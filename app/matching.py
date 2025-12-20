from sentence_transformers import SentenceTransformer, util
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchMatcher:
    def __init__(self, model_name='all-MiniLM-L6-v2'):
        """
        Initializes the matcher with a specific SentenceTransformer model.
        'all-MiniLM-L6-v2' is chosen for its speed and good performance on semantic search.
        For academic specific, 'allenai/specter' is better but slower.
        """
        logger.info(f"Loading AI Model: {model_name}...")
        try:
            self.model = SentenceTransformer(model_name)
            logger.info("AI Model loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load AI model: {e}")
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
        Each candidate must have a 'text_for_matching' field or we combine relevant fields.
        """
        if not self.model or not query:
            return candidates

        query_emb = self.model.encode(query)
        
        scored_candidates = []
        for cand in candidates:
            # Construct text representation if not present
            text = cand.get('text_for_matching')
            if not text:
                # Fallback: combine name, interests, description
                parts = [
                    cand.get('name', ''),
                    cand.get('interests', ''),
                    cand.get('description', ''),
                    cand.get('title', '')
                ]
                text = " ".join([str(p) for p in parts if p])
            
            cand_emb = self.model.encode(text)
            score = util.cos_sim(query_emb, cand_emb).item()
            
            # Add score to candidate
            cand['match_score'] = int(score * 100) # Convert 0-1 to 0-100
            scored_candidates.append(cand)
        
        # Sort by score descending
        return sorted(scored_candidates, key=lambda x: x['match_score'], reverse=True)

# Singleton instance
matcher = ResearchMatcher()

if __name__ == "__main__":
    # Test
    query = "Natural Language Processing"
    candidates = [
        {"name": "Dr. AI", "interests": "Deep Learning, Transformers, Text Generation"},
        {"name": "Dr. Bio", "interests": "Genetics, Cell Biology"},
        {"name": "Dr. Stat", "interests": "Regression, Probability, Data Analysis"}
    ]
    ranked = matcher.rank_candidates(query, candidates)
    for r in ranked:
        print(f"{r['name']}: {r['match_score']}")
