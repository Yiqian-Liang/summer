from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
from typing import Optional
from app.scrapers import AcademicScraper

app = FastAPI(title="Academic Match System")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Initialize Scraper Facade
scraper_engine = AcademicScraper()

# Mock Data (In real app, this comes from DB/Scraper)
MOCK_PROFESSORS = [
    {
        "id": 1,
        "name": "Dr. Alice Smith",
        "university": "Stanford University",
        "department": "Computer Science",
        "interests": "NLP, Deep Learning, Agents",
        "recent_paper": "Large Language Models for Reasoning",
        "match_score": 95,
        "source": "University Website"
    },
    {
        "id": 2,
        "name": "Prof. Bob Johnson",
        "university": "MIT",
        "department": "EECS",
        "interests": "Robotics, Computer Vision",
        "recent_paper": "Visual Navigation in Unstructured Environments",
        "match_score": 88,
        "source": "University Website"
    },
    {
        "id": 3,
        "name": "Dr. Carol Williams",
        "university": "UC Berkeley",
        "department": "AI Lab",
        "interests": "Reinforcement Learning, Control Theory",
        "recent_paper": "Efficient RL with Human Feedback",
        "match_score": 92,
        "source": "University Website"
    }
]

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/search", response_class=HTMLResponse)
async def search(request: Request, q: Optional[str] = Query(None)):
    query = q or ""
    results = []
    
    if query:
        # 1. Search Local Mock DB
        for prof in MOCK_PROFESSORS:
            if (query.lower() in prof['interests'].lower() or 
                query.lower() in prof['name'].lower() or 
                query.lower() in prof['university'].lower()):
                results.append(prof)
        
        # 2. Live Fetch from External Sources (Scholar/Social)
        # This demonstrates the "All Platforms" capability
        live_results = scraper_engine.search_all_sources(query)
        for item in live_results:
            # Normalize to match frontend template
            results.append({
                "id": len(results) + 1,
                "name": item.get('name', item.get('title', 'Unknown')),
                "university": item.get('source', 'External Source'),
                "department": "External Match",
                "interests": ", ".join(item.get('interests', ['Relevance Match'])),
                "recent_paper": item.get('url', 'Link Available'),
                "match_score": 75 # Default for external
            })
            
    else:
        results = MOCK_PROFESSORS # Show all if no query

    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "results": results,
        "query": query
    })

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
