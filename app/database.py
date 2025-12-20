import sqlite3
from sqlite_utils import Database
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_NAME = "summer.db"

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
            "content": str,
            "status": str, # 'draft', 'sent'
            "created_at": str
        }, pk="id")
        logger.info("Created 'email_drafts' table.")

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

if __name__ == "__main__":
    init_db()
