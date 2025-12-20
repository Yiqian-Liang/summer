from sentence_transformers import SentenceTransformer, util
import logging
import json
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResearchMatcher:
    def __init__(self, model_name=None, trust_remote_code=True):
        """
        Initializes the matcher.
        Supports switching between:
        1. 'Alibaba-NLP/gte-Qwen2-1.5B-instruct' (Best for Multilingual/Social/Long Context)
        2. 'allenai/specter2_base' (Best for Pure Academic Paper Citation Matching)
        """
        # Default to Qwen unless environment variable says otherwise
        if not model_name:
            model_name = os.getenv("EMBEDDING_MODEL", "Alibaba-NLP/gte-Qwen2-1.5B-instruct")
            
        self.model_name = model_name
        logger.info(f"Loading AI Model: {model_name}...")
        
        try:
            self.model = SentenceTransformer(model_name, trust_remote_code=trust_remote_code)
            logger.info(f"AI Model ({model_name}) loaded successfully.")
            
            # Special setup for SPECTER2 adapters if needed
            if "specter2" in model_name:
                # In a full implementation, we would load adapters here:
                # self.model.load_adapter("allenai/specter2_proximity", source="hf", set_active=True)
                pass
                
        except Exception as e:
            logger.error(f"Failed to load primary model {model_name}: {e}")
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

    def rank_candidates(self, query: str, candidates: list):
        """
        Ranks a list of candidate dictionaries based on semantic similarity to the query.
        """
        if not self.model or not query:
            return candidates

        # 1. Query Encoding Strategy
        if "Qwen" in self.model_name:
             # Qwen-instruct needs prompts
             prompted_query = f"Instruct: Retrieve relevant academic professors and research opportunities based on the student's interest.\nQuery: {query}"
             query_emb = self.model.encode(prompted_query)
        elif "specter2" in self.model_name:
             # SPECTER2 is best with "[Title] [SEP] [Abstract]" format, but for simple queries we use raw text
             # It implicitly handles citation proximity
             query_emb = self.model.encode(query)
        else:
             query_emb = self.model.encode(query)
        
        scored_candidates = []
        for cand in candidates:
            # 2. Document Encoding Strategy
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
            
            # For SPECTER2, if we had structured paper data, we'd format it as "Title [SEP] Abstract"
            # Here we just use the combined text
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
        {"name": "Prof. Smith", "interests": "Natural Language Processing, BERT, Transformers"},
        {"name": "Prof. Doe", "interests": "Computer Vision, Image Segmentation"},
        {"name": "李教授", "interests": "人工智能, 深度学习, 对话系统"},
    ]
    ranked = matcher.rank_candidates(query, candidates)
    for r in ranked:
        print(f"{r['name']}: {r['match_score']}")
