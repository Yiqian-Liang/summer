import sqlite3
from sqlite_utils import Database
import logging
import hashlib
import json
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = "summer.db"


def get_db() -> Database:
    """Returns a database connection (singleton pattern for FastAPI)."""
    return Database(DB_NAME)


def generate_opportunity_id(source_url: str) -> str:
    """Generates a unique ID for an opportunity based on source URL."""
    return hashlib.md5(source_url.encode()).hexdigest()[:16]


def init_db():
    """Initializes the SQLite database with required tables."""
    db = Database(DB_NAME)
    
    # 1. Professors/Researchers Table
    if "professors" not in db.table_names():
        db["professors"].create({
            "id": int,
            "name": str,
            "university": str,
            "department": str,
            "interests": str,
            "email": str,
            "website": str,
            "source": str, # 'university', 'scholar', 'manual'
            "last_updated": str,
            "embedding": str, # JSON string of vector
            "agent_analysis": str # AI generated reason for match
        }, pk="id")
        logger.info("Created 'professors' table.")
    else:
        # Migration: Add columns if not exists
        if "embedding" not in db["professors"].columns_dict:
            db["professors"].add_column("embedding", str)
            logger.info("Added 'embedding' column to professors.")
        if "agent_analysis" not in db["professors"].columns_dict:
            db["professors"].add_column("agent_analysis", str)
            logger.info("Added 'agent_analysis' column to professors.")

    # 2. Programs (Summer Research/PhD Positions) Table
    if "programs" not in db.table_names():
        db["programs"].create({
            "id": int,
            "title": str,
            "institution": str,
            "location": str,
            "description": str,
            "deadline": str,
            "stipend": str,
            "url": str,
            "source": str, # 'nsf_reu', 'mit_sgi', 'social_media'
            "tags": str, # JSON string or comma-separated
            "embedding": str, # JSON string of vector
            "agent_analysis": str # AI generated reason for match
        }, pk="id")
        # Enable full-text search on title and description
        db["programs"].enable_fts(["title", "description", "tags"])
        logger.info("Created 'programs' table with FTS.")
    else:
        # Migration: Add columns if not exists
        if "embedding" not in db["programs"].columns_dict:
            db["programs"].add_column("embedding", str)
            logger.info("Added 'embedding' column to programs.")
        if "agent_analysis" not in db["programs"].columns_dict:
            db["programs"].add_column("agent_analysis", str)
            logger.info("Added 'agent_analysis' column to programs.")

    # 3. Email Drafts (New Table)
    if "email_drafts" not in db.table_names():
        db["email_drafts"].create({
            "id": int,
            "student_name": str,
            "professor_name": str,
            "professor_email": str,
            "content": str,
            "status": str, # 'draft', 'sent'
            "created_at": str
        }, pk="id")
        logger.info("Created 'email_drafts' table.")
    else:
        if "professor_email" not in db["email_drafts"].columns_dict:
            db["email_drafts"].add_column("professor_email", str)
            logger.info("Added 'professor_email' column to email_drafts.")

    # 4. Opportunities Table (Aggregated from all sources)
    if "opportunities" not in db.table_names():
        db["opportunities"].create({
            "id": str,  # hash of source_url
            "title": str,
            "source_type": str,  # scholar|nsf|xiaohongshu|wechat|linkedin|university|twitter|...
            "source_url": str,
            "professor_name": str,  # nullable
            "university": str,
            "department": str,
            "description": str,
            "embedding": str,  # JSON string of vector
            "hiring_signals": str,  # JSON array of signals
            "has_direct_apply": int,  # 0 or 1
            "direct_apply_link": str,
            "deadline": str,  # nullable
            "quality_score": int,  # 0-100
            "created_at": str,
            "updated_at": str,
        }, pk="id")
        # Enable full-text search on title and description
        db["opportunities"].enable_fts(["title", "description", "professor_name", "university"])
        logger.info("Created 'opportunities' table with FTS.")
    else:
        # Migration: Add columns if not exists
        cols = db["opportunities"].columns_dict
        if "quality_score" not in cols:
            db["opportunities"].add_column("quality_score", int, not_null_default=0)
            logger.info("Added 'quality_score' column to opportunities.")
        if "hiring_signals" not in cols:
            db["opportunities"].add_column("hiring_signals", str)
            logger.info("Added 'hiring_signals' column to opportunities.")
        if "has_direct_apply" not in cols:
            db["opportunities"].add_column("has_direct_apply", int, not_null_default=0)
            logger.info("Added 'has_direct_apply' column to opportunities.")

    # Seed with some initial data if empty
    if db["programs"].count == 0:
        db["programs"].insert_all([
            {
                "title": "Summer Geometry Initiative",
                "institution": "MIT",
                "location": "Remote / Cambridge, MA",
                "description": "A six-week paid summer research program introducing undergraduate and graduate students to the field of geometry processing.",
                "deadline": "2025-02-15",
                "stipend": "Paid",
                "url": "https://sgi.mit.edu",
                "source": "manual",
                "tags": "geometry, vision, graphics"
            }
        ])
        logger.info("Seeded initial program data.")

def upsert_opportunity(opportunity: Dict) -> bool:
    """
    Inserts or updates an opportunity in the database.
    Uses source_url hash as unique ID for deduplication.
    Returns True if inserted, False if updated.
    """
    db = get_db()
    source_url = opportunity.get("source_url", "")
    if not source_url:
        logger.warning("Opportunity missing source_url, skipping.")
        return False
    
    opp_id = generate_opportunity_id(source_url)
    opportunity["id"] = opp_id
    
    # Check if exists
    existing = list(db["opportunities"].rows_where("id = ?", [opp_id]))
    
    if existing:
        # Update
        db["opportunities"].update(opp_id, opportunity)
        logger.info(f"Updated opportunity: {opportunity.get('title', opp_id)[:50]}")
        return False
    else:
        # Insert
        db["opportunities"].insert(opportunity)
        logger.info(f"Inserted opportunity: {opportunity.get('title', opp_id)[:50]}")
        return True


def get_opportunities(
    limit: int = 50,
    offset: int = 0,
    source_type: Optional[str] = None,
    min_quality: int = 0,
    order_by: str = "quality_score desc, updated_at desc"
) -> List[Dict]:
    """
    Retrieves opportunities from the database with optional filters.
    """
    db = get_db()
    
    where_clauses = []
    params = []
    
    if source_type:
        where_clauses.append("source_type = ?")
        params.append(source_type)
    
    if min_quality > 0:
        where_clauses.append("quality_score >= ?")
        params.append(min_quality)
    
    where_sql = " AND ".join(where_clauses) if where_clauses else None
    
    rows = db["opportunities"].rows_where(
        where=where_sql,
        where_args=params if params else None,
        order_by=order_by,
        limit=limit,
        offset=offset
    )
    
    return list(rows)


def search_opportunities_fts(query: str, limit: int = 50) -> List[Dict]:
    """
    Search opportunities using LIKE queries (more reliable than FTS for this use case).
    Searches across title, description, professor_name, and university.
    """
    db = get_db()
    try:
        # Use LIKE search which is more reliable
        search_pattern = f"%{query}%"
        sql = """
            SELECT * FROM opportunities 
            WHERE title LIKE ? 
               OR description LIKE ? 
               OR professor_name LIKE ? 
               OR university LIKE ?
            ORDER BY quality_score DESC
            LIMIT ?
        """
        rows = list(db.execute(sql, [search_pattern, search_pattern, search_pattern, search_pattern, limit]).fetchall())
        
        # Convert to list of dicts
        if rows:
            columns = [col[0] for col in db.execute("PRAGMA table_info(opportunities)").fetchall()]
            column_names = [c[1] for c in db.execute("PRAGMA table_info(opportunities)").fetchall()]
            return [dict(zip(column_names, row)) for row in rows]
        return []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


def get_opportunity_by_id(opp_id: str) -> Optional[Dict]:
    """
    Retrieves a single opportunity by ID.
    """
    db = get_db()
    rows = list(db["opportunities"].rows_where("id = ?", [opp_id]))
    return rows[0] if rows else None


def get_opportunity_count() -> int:
    """Returns total number of opportunities."""
    db = get_db()
    return db["opportunities"].count


# ============== User Management ==============

def init_user_tables():
    """Initialize user-related tables."""
    db = get_db()
    
    # 1. Users table - basic authentication
    if "users" not in db.table_names():
        db["users"].create({
            "id": int,
            "email": str,
            "username": str,
            "password_hash": str,
            "created_at": str,
            "last_login": str,
            "is_active": int,  # 0 or 1
            "onboarding_complete": int,  # 0 or 1
        }, pk="id")
        db["users"].create_index(["email"], unique=True)
        db["users"].create_index(["username"], unique=True)
        logger.info("Created 'users' table.")
    
    # 2. User profiles - preferences and interests
    if "user_profiles" not in db.table_names():
        db["user_profiles"].create({
            "user_id": int,
            "education_level": str,  # undergraduate, masters, phd, graduated
            "school_tier": str,  # 985, 211, overseas, other
            "grade": str,  # freshman, sophomore, junior, senior, grad1, grad2, etc.
            "target_types": str,  # JSON array: summer_research, phd, intern, visiting
            "majors": str,  # JSON array of majors
            "research_fields": str,  # JSON array: nlp, cv, ml, robotics, etc.
            "region_preferences": str,  # JSON array: north_america, europe, asia, any
            "updated_at": str,
        }, pk="user_id", foreign_keys=[("user_id", "users", "id")])
        logger.info("Created 'user_profiles' table.")
    
    # 3. User behaviors - tracking actions for recommendations
    if "user_behaviors" not in db.table_names():
        db["user_behaviors"].create({
            "id": int,
            "user_id": int,
            "action_type": str,  # search, click, save, apply, view
            "target_id": str,  # opportunity_id or search query
            "target_type": str,  # opportunity, search, professor
            "metadata": str,  # JSON: extra data like duration, position, etc.
            "created_at": str,
        }, pk="id", foreign_keys=[("user_id", "users", "id")])
        db["user_behaviors"].create_index(["user_id", "action_type"])
        logger.info("Created 'user_behaviors' table.")
    
    # 4. User saved items (favorites/bookmarks)
    if "user_saved" not in db.table_names():
        db["user_saved"].create({
            "id": int,
            "user_id": int,
            "opportunity_id": str,
            "notes": str,
            "created_at": str,
        }, pk="id", foreign_keys=[("user_id", "users", "id")])
        db["user_saved"].create_index(["user_id", "opportunity_id"], unique=True)
        logger.info("Created 'user_saved' table.")


def create_user(email: str, username: str, password_hash: str) -> Optional[int]:
    """Create a new user. Returns user_id or None if failed."""
    db = get_db()
    try:
        result = db["users"].insert({
            "email": email,
            "username": username,
            "password_hash": password_hash,
            "created_at": datetime.now().isoformat(),
            "last_login": None,
            "is_active": 1,
            "onboarding_complete": 0,
        })
        return result.last_pk
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return None


def get_user_by_email(email: str) -> Optional[Dict]:
    """Get user by email."""
    db = get_db()
    rows = list(db["users"].rows_where("email = ?", [email]))
    return rows[0] if rows else None


def get_user_by_username(username: str) -> Optional[Dict]:
    """Get user by username."""
    db = get_db()
    rows = list(db["users"].rows_where("username = ?", [username]))
    return rows[0] if rows else None


def get_user_by_id(user_id: int) -> Optional[Dict]:
    """Get user by ID."""
    db = get_db()
    rows = list(db["users"].rows_where("id = ?", [user_id]))
    return rows[0] if rows else None


def update_user_last_login(user_id: int):
    """Update user's last login time."""
    db = get_db()
    db["users"].update(user_id, {"last_login": datetime.now().isoformat()})


def save_user_profile(user_id: int, profile: Dict) -> bool:
    """Save or update user profile."""
    db = get_db()
    profile["user_id"] = user_id
    profile["updated_at"] = datetime.now().isoformat()
    
    # Check if profile exists
    existing = list(db["user_profiles"].rows_where("user_id = ?", [user_id]))
    
    if existing:
        db["user_profiles"].update(user_id, profile)
    else:
        db["user_profiles"].insert(profile)
    
    # Mark onboarding as complete
    db["users"].update(user_id, {"onboarding_complete": 1})
    
    return True


def get_user_profile(user_id: int) -> Optional[Dict]:
    """Get user profile."""
    db = get_db()
    rows = list(db["user_profiles"].rows_where("user_id = ?", [user_id]))
    return rows[0] if rows else None


def record_user_behavior(user_id: int, action_type: str, target_id: str, 
                         target_type: str, metadata: Dict = None):
    """Record a user behavior for recommendations."""
    db = get_db()
    db["user_behaviors"].insert({
        "user_id": user_id,
        "action_type": action_type,
        "target_id": target_id,
        "target_type": target_type,
        "metadata": json.dumps(metadata or {}),
        "created_at": datetime.now().isoformat(),
    })


def get_user_behaviors(user_id: int, action_type: str = None, limit: int = 100) -> List[Dict]:
    """Get user behaviors, optionally filtered by action type."""
    db = get_db()
    if action_type:
        rows = db["user_behaviors"].rows_where(
            "user_id = ? AND action_type = ?", 
            [user_id, action_type],
            order_by="-created_at",
            limit=limit
        )
    else:
        rows = db["user_behaviors"].rows_where(
            "user_id = ?", 
            [user_id],
            order_by="-created_at",
            limit=limit
        )
    return list(rows)


def save_opportunity(user_id: int, opportunity_id: str, notes: str = "") -> bool:
    """Save/bookmark an opportunity for a user."""
    db = get_db()
    try:
        db["user_saved"].insert({
            "user_id": user_id,
            "opportunity_id": opportunity_id,
            "notes": notes,
            "created_at": datetime.now().isoformat(),
        })
        return True
    except Exception as e:
        logger.error(f"Error saving opportunity: {e}")
        return False


def unsave_opportunity(user_id: int, opportunity_id: str) -> bool:
    """Remove a saved opportunity."""
    db = get_db()
    try:
        db["user_saved"].delete_where("user_id = ? AND opportunity_id = ?", [user_id, opportunity_id])
        return True
    except Exception as e:
        logger.error(f"Error unsaving opportunity: {e}")
        return False


def get_user_saved(user_id: int) -> List[Dict]:
    """Get all saved opportunities for a user."""
    db = get_db()
    rows = list(db["user_saved"].rows_where("user_id = ?", [user_id], order_by="-created_at"))
    return rows


def is_opportunity_saved(user_id: int, opportunity_id: str) -> bool:
    """Check if an opportunity is saved by user."""
    db = get_db()
    rows = list(db["user_saved"].rows_where(
        "user_id = ? AND opportunity_id = ?", 
        [user_id, opportunity_id]
    ))
    return len(rows) > 0


from datetime import datetime

if __name__ == "__main__":
    init_db()
    init_user_tables()