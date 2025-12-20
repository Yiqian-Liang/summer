from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
from typing import Optional
from app.scrapers import AcademicScraper
from app.matching import matcher # AI Matcher
from app.agents import ResearchAgent # Analysis Agent
from sqlite_utils import Database
import os

app = FastAPI(title="Academic Match System")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Initialize Scraper Facade & Agents
scraper_engine = AcademicScraper()
research_agent = ResearchAgent()

# Database Connection
DB_NAME = "summer.db"
def get_db():
    return Database(DB_NAME)

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: Optional[str] = Query(None)):
    query = q or ""
    results = []
    
    if query:
        # 1. Search Local Database (Programs & Professors)
        db = get_db()
        
        # Search Programs Table (Full Text Search)
        try:
            program_matches = db["programs"].search(query)
            for prog in program_matches:
                results.append({
                    "id": prog["id"],
                    "name": prog["title"],
                    "university": prog["institution"],
                    "department": prog["location"], # Mapping for UI
                    "interests": prog["tags"],
                    "recent_paper": prog["url"], # Mapping URL to paper link slot
                    "match_score": 0, # Placeholder, will be AI ranked
                    "source": prog["source"],
                    "text_for_matching": f"{prog['title']} {prog['tags']} {prog['description']}"
                })
        except Exception as e:
            print(f"DB Search Error: {e}")

        # 2. Live Fetch from External Sources (Scholar/Social)
        # In a real production app, this should be a background task that populates the DB
        # For this demo, we fetch live and merge.
        try:
            live_results = scraper_engine.search_all_sources(query)
            for item in live_results:
                results.append({
                    "id": len(results) + 1,
                    "name": item.get('name', item.get('title', 'Unknown')),
                    "university": item.get('source', 'External Source'),
                    "department": "External Match",
                    "interests": str(item.get('interests', 'Relevance Match')),
                    "recent_paper": item.get('recent_paper', item.get('url', '#')),
                    "match_score": 0, # Placeholder
                    "source": item.get('source', 'External'),
                    "text_for_matching": f"{item.get('name', '')} {item.get('interests', '')} {item.get('snippet', '')}"
                })
        except Exception as e:
            print(f"Scraper Error: {e}")

        # 3. AI Semantic Ranking & Agent Reasoning
        # Re-rank all results based on embedding similarity to the query
        if results:
            # Step A: Semantic Rank (SPECTER)
            results = matcher.rank_candidates(query, results)
            
            # Step B: Agent Analysis (Why match?)
            # Only analyze top 3 results to save time/tokens
            for i in range(min(3, len(results))):
                analysis = research_agent.analyze_match(query, results[i])
                results[i]['agent_analysis'] = analysis['reasoning']
                results[i]['agent_verdict'] = analysis['verdict']
            
    else:
        # Show all local programs if no query
        db = get_db()
        for prog in db["programs"].rows:
             results.append({
                "id": prog["id"],
                "name": prog["title"],
                "university": prog["institution"],
                "department": prog["location"],
                "interests": prog["tags"],
                "recent_paper": prog["url"],
                "match_score": 0,
                "source": prog["source"]
            })

    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "results": results,
        "query": query
    })

if __name__ == "__main__":
    # Ensure DB exists
    if not os.path.exists(DB_NAME):
        from app.database import init_db
        init_db()
        
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
