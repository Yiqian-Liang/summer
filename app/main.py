from fastapi import FastAPI, Request, Query, Body
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
from typing import Optional, Dict
from app.scrapers import AcademicScraper
from app.matching import matcher # AI Matcher
from app.agents import ResearchAgent # Analysis Agent
from sqlite_utils import Database
import os
import datetime

app = FastAPI(title="Academic Match System")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

from app.rag import RAGWorkbench

# Initialize Scraper Facade & Agents
scraper_engine = AcademicScraper()
research_agent = ResearchAgent()
rag_workbench = RAGWorkbench()

# ... (Database Connection code)

# ... (Search Endpoint code)

@app.post("/api/generate-rag-draft")
async def generate_rag_draft(payload: Dict = Body(...)):
    """
    New API Endpoint for Context-Aware RAG Drafting.
    Uses app/rag.py logic to create citations and analyze hiring signals.
    """
    student_profile = payload.get("student_profile", {})
    professor_profile = payload.get("professor_profile", {})
    
    draft_data = rag_workbench.generate_context_aware_draft(
        student_profile, professor_profile
    )
    
    # Log draft to DB
    db = get_db()
    db["email_drafts"].insert({
        "student_name": student_profile.get("name", "Student"),
        "professor_name": professor_profile.get("name", "Unknown"),
        "professor_email": professor_profile.get("email", ""),
        "content": draft_data["body"],
        "status": "rag_generated",
        "created_at": datetime.datetime.now().isoformat()
    })
    
    return JSONResponse({"draft": draft_data})

if __name__ == "__main__":
    # Ensure DB exists
    if not os.path.exists(DB_NAME):
        from app.database import init_db
        init_db()
        
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
