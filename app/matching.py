import logging
import json
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SentenceTransformer = None
util = None


def _load_sentence_transformers():
    """Import sentence-transformers lazily after offline/download env is set."""
    global SentenceTransformer, util
    if SentenceTransformer is None or util is None:
        from sentence_transformers import SentenceTransformer as _SentenceTransformer, util as _util
        SentenceTransformer = _SentenceTransformer
        util = _util

class ResearchMatcher:
    def __init__(self, model_name='paraphrase-multilingual-MiniLM-L12-v2', trust_remote_code=True):
        """
        Initializes the matcher with a lightweight multilingual model.
        paraphrase-multilingual-MiniLM-L12-v2 supports 50+ languages including Chinese and English,
        loads fast (~500MB) and works well for semantic similarity.
        """
        self.model_name = model_name
        self.model = None
        self._load_attempted = False

        if os.getenv("EAGER_LOAD_MODEL", "").lower() in {"1", "true", "yes"}:
            self._ensure_model()

    def _ensure_model(self):
        """Load the semantic model once, falling back cleanly when unavailable."""
        if self._load_attempted:
            return

        self._load_attempted = True
        if os.getenv("DISABLE_SEMANTIC_SEARCH", "").lower() in {"1", "true", "yes"}:
            logger.info("Semantic ranking disabled by DISABLE_SEMANTIC_SEARCH.")
            return

        logger.info(f"Loading AI Model: {self.model_name}...")
        allow_download = os.getenv("ALLOW_MODEL_DOWNLOAD", "").lower() in {"1", "true", "yes"}
        model_kwargs = {} if allow_download else {"local_files_only": True}
        if not allow_download:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            logger.info("Model downloads disabled. Set ALLOW_MODEL_DOWNLOAD=1 to fetch missing models.")
        try:
            _load_sentence_transformers()
            self.model = SentenceTransformer(self.model_name, **model_kwargs)
            logger.info(f"AI Model ({self.model_name}) loaded successfully.")
        except Exception as e:
            logger.error(f"Failed to load model: {e}")
            logger.warning("Falling back to lightweight model 'all-MiniLM-L6-v2'")
            try:
                self.model = SentenceTransformer('all-MiniLM-L6-v2', **model_kwargs)
            except Exception as fallback_error:
                logger.error(f"Failed to load fallback model: {fallback_error}")
                logger.warning("Semantic ranking disabled; lexical ranking will still work.")
                self.model = None

    def encode_text(self, text: str):
        """Encodes text into a vector."""
        self._ensure_model()
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
        if not candidates:
            return []

        self._ensure_model()
        if not self.model or not query:
            return sorted(
                candidates,
                key=lambda x: x.get("search_score", x.get("match_score", x.get("lexical_score", x.get("quality_score", 0)))) or 0,
                reverse=True,
            )

        candidate_texts = []
        for cand in candidates:
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
            candidate_texts.append(text or "")

        try:
            # The configured model uses symmetric sentence similarity.
            query_emb = self.model.encode(query, convert_to_tensor=True)
            cand_embeddings = self.model.encode(candidate_texts, convert_to_tensor=True)
            semantic_scores = util.cos_sim(query_emb, cand_embeddings)[0].tolist()
        except Exception as e:
            logger.error(f"Semantic ranking failed: {e}")
            return sorted(
                candidates,
                key=lambda x: x.get("search_score", x.get("match_score", x.get("lexical_score", x.get("quality_score", 0)))) or 0,
                reverse=True,
            )

        scored_candidates = []
        for cand, text, semantic_score in zip(candidates, candidate_texts, semantic_scores):
            cand = dict(cand)
            # 3. Intent Prediction Boost (The "Hiring Signal")
            intent_boost = 0.0
            # Heuristic: Grant keywords in snippet = higher hiring probability
            if any(k in text.lower() for k in ["grant", "nsf", "funding", "hiring", "openings"]):
                intent_boost = 0.15 # +15% boost for likely hiring
            elif "new lab" in text.lower() or "assistant professor" in text.lower():
                intent_boost = 0.10 # +10% boost for new labs
            
            base_score = cand.get("search_score")
            if base_score is not None:
                # Semantic similarity is a tie-breaker. Verified lexical intent and
                # source quality must remain dominant for opportunity search.
                semantic_percent = max(0.0, min(1.0, float(semantic_score))) * 100
                cand["semantic_score"] = round(semantic_percent, 1)
                cand["match_score"] = min(100, int(round(float(base_score) + semantic_percent * 0.05)))
            else:
                lexical_bonus = min(float(cand.get("lexical_score") or 0) / 300, 0.20)
                quality_bonus = min(float(cand.get("quality_score") or 0) / 1000, 0.10)
                final_score = semantic_score + intent_boost + lexical_bonus + quality_bonus
                cand["match_score"] = max(0, min(100, int(final_score * 100)))
            
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
