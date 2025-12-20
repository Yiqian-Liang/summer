# Global Academic Recruitment & Research Match System (全球学术招聘与研究匹配系统)

An AI-driven platform for matching students with Summer Research and PhD opportunities.
这是一个由 AI 驱动的平台，旨在为学生匹配暑期科研和博士申请机会。

## 🇨🇳 中文介绍 (Chinese Introduction)

### 🚀 愿景
自动化解决寻找科研职位的“学术迷宫”问题。通过聚合**大学官网**、**谷歌学术 (Google Scholar)** 和 **社交媒体 (微信/小红书)** 的数据，并利用 AI (SPECTER/SciBERT) 模型来精准匹配科研兴趣。

### 🛠 技术栈
*   **后端:** Python 3.9 + FastAPI (高性能，适合 AI/爬虫任务)
*   **前端:** Jinja2 模板 + Tailwind CSS (无需 Node.js，轻量级)
*   **数据库:** SQLite (MVP阶段) -> PostgreSQL (生产环境)
*   **数据采集:** Requests + BeautifulSoup (已预留 Playwright 接口)
*   **AI 匹配:** 预留 HuggingFace Transformers (SPECTER) 集成接口

### 📦 功能现状 (MVP)
目前我们已经完成了**最小可行性产品 (MVP)** 的核心架构搭建，实现了以下功能：
1.  **Web 平台架构**: 基于 FastAPI 的高性能后端，支持高并发数据请求。
2.  **搜索界面**: 响应式 UI 设计，支持关键词搜索。
3.  **爬虫框架**: 建立了 `AcademicScraper` 基础类，支持模块化扩展（目前包含基础网页抓取逻辑）。
4.  **模拟匹配引擎**: 演示了如何根据兴趣标签进行排序和匹配。

### ⚠️ 关于“全平台信息”的说明
您提到的“综合各大官方暑研program”以及社交媒体数据，目前处于**架构就绪**状态。由于谷歌学术和社交媒体的反爬虫机制非常严格（通常需要 SerpApi 或特定账号池），我们目前搭建了**数据接入的接口**，下一步将具体实现针对这些平台的采集逻辑。

---

## 🇺🇸 English Introduction

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
