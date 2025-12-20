# Global Academic Recruitment & Research Match System

An AI-driven platform for matching students with Summer Research and PhD opportunities.

## 🚀 Vision
To automate the "academic maze" of finding research positions by aggregating data from University websites, Google Scholar, and social media, and using AI (SPECTER/SciBERT) to match research interests.

## 🛠 Tech Stack
*   **Backend:** Python 3.9 + FastAPI
*   **Frontend:** Jinja2 Templates + Tailwind CSS (No Node.js required)
*   **Database:** SQLite (MVP) -> PostgreSQL (Production)
*   **Scraping:** Requests + BeautifulSoup (Playwright ready)
*   **AI:** Ready for HuggingFace Transformers integration

**Note on Architecture:**
Originally planned for Next.js + Rails 8. Due to local environment constraints (missing Node.js and outdated Ruby), the stack was adapted to a Python-centric monolith. This aligns well with the heavy data science/AI requirements of the project.

## 📦 Installation

1.  **Clone the repository** (if not already):
    ```bash
    git clone git@github.com:Yiqian-Liang/summer.git
    cd summer
    ```

2.  **Create Virtual Environment**:
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    ```

3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

## 🏃‍♂️ Running the App

Start the server with hot-reload enabled:

```bash
./venv/bin/uvicorn app.main:app --reload
```

Visit `http://localhost:8000` in your browser.

## 📁 Project Structure

```
summer/
├── app/
│   ├── main.py            # FastAPI entry point
│   ├── scrapers.py        # Scraping logic
│   ├── templates/         # HTML templates
│   │   ├── base.html
│   │   ├── index.html
│   │   └── search_results.html
│   └── static/            # Static assets
├── requirements.txt       # Python dependencies
└── README.md              # Documentation
```

## 🤖 Features Implemented (MVP)
-   **Search Interface:** Clean, responsive UI using Tailwind.
-   **Mock Matching Engine:** Demonstrates how results are ranked.
-   **Scraper Foundation:** Modular scraper class structure.

## 📝 Next Steps
1.  Integrate **Playwright** for dynamic JS-heavy university sites.
2.  Implement **SPECTER** model for real semantic matching.
3.  Add **PostgreSQL** for persistent storage.
4.  Deploy to a cloud provider (Hetzner/AWS).
