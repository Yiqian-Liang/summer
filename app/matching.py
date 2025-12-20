from sentence_transformers import SentenceTransformer, util
import logging
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchMatcher:
    def __init__(self, model_name='Alibaba-NLP/gte-Qwen2-1.5B-instruct', trust_remote_code=True):
        """
        Initializes the matcher with the Qwen2-1.5B model.
        Qwen is a state-of-the-art multilingual model that excels at long-context retrieval.
        It supports Chinese and English perfectly.
        
        Note on Model Selection (2025 Strategy):
        - We use 'Alibaba-NLP/gte-Qwen2-1.5B-instruct' as the primary model because it handles 
          Multilingual (Chinese/English) data from Xiaohongshu/LinkedIn better than SPECTER.
        - Future Upgrade Path: Consider a hybrid approach using 'allenai/specter2' 
          specifically for the citation-graph analysis step if the academic matching needs 
          to be more rigorous on citation networks.
        """
        logger.info(f"Loading Enterprise AI Model: {model_name}...")
        try:
            # trust_remote_code is needed for some new architectures, though sentence-transformers usually handles it
            self.model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code)
            logger.info(f"Enterprise AI Model ({model_name}) loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load Qwen model: {e}")
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

        # Qwen-instruct models perform better with a prompt for the query
        # Format: "Instruct: <task>\nQuery: <query>"
        prompted_query = f"Instruct: Retrieve relevant academic professors and research opportunities based on the student's interest.\nQuery: {query}"
        
        query_emb = self.model.encode(prompted_query)
        
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
    query = "大语言模型 (Large Language Models)" # Test multilingual capability
    candidates = [
        {"name": "Prof. Smith", "interests": "Natural Language Processing, BERT, Transformers"}, # Should match high
        {"name": "Prof. Doe", "interests": "Computer Vision, Image Segmentation"}, # Should match low
        {"name": "李教授", "interests": "人工智能, 深度学习, 对话系统"}, # Should match high (Chinese)
    ]
    ranked = matcher.rank_candidates(query, candidates)
    for r in ranked:
        print(f"{r['name']}: {r['match_score']}")
