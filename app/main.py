from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
from typing import Optional

app = FastAPI(title="Academic Match System")

# Mount static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Templates
templates = Jinja2Templates(directory="app/templates")

# Mock Data (In real app, this comes from DB/Scraper)
MOCK_PROFESSORS = [
    {
        "id": 1,
        "name": "Dr. Alice Smith",
        "university": "Stanford University",
        "department": "Computer Science",
        "interests": "NLP, Deep Learning, Agents",
        "recent_paper": "Large Language Models for Reasoning",
        "match_score": 95
    },
    {
        "id": 2,
        "name": "Prof. Bob Johnson",
        "university": "MIT",
        "department": "EECS",
        "interests": "Robotics, Computer Vision",
        "recent_paper": "Visual Navigation in Unstructured Environments",
        "match_score": 88
    },
    {
        "id": 3,
        "name": "Dr. Carol Williams",
        "university": "UC Berkeley",
        "department": "AI Lab",
        "interests": "Reinforcement Learning, Control Theory",
        "recent_paper": "Efficient RL with Human Feedback",
        "match_score": 92
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
        # Simple keyword matching for MVP
        for prof in MOCK_PROFESSORS:
            if (query.lower() in prof['interests'].lower() or 
                query.lower() in prof['name'].lower() or 
                query.lower() in prof['university'].lower()):
                results.append(prof)
    else:
        results = MOCK_PROFESSORS # Show all if no query

    return templates.TemplateResponse("search_results.html", {
        "request": request,
        "results": results,
        "query": query
    })

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
