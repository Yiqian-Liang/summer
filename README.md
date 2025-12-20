# Global Academic Recruitment & Research Match System (全球学术招聘与研究匹配系统)

An AI-driven platform for matching students with Summer Research and PhD opportunities.
这是一个由 AI 驱动的平台，旨在为学生匹配全球暑期科研和博士申请机会。

## 🇨🇳 中文介绍 (Chinese Introduction)

### 🚀 核心亮点 (2025.12.20 更新)
我们不仅仅是一个爬虫，更是一个**“全能学术助理” (Agentic Academic Assistant)**。

#### 1. 🌍 全球全平台数据覆盖 (Global Data Coverage)
我们不再局限于大学官网，系统现在实时监控以下 10+ 个平台的信息：
*   **🇨🇳 中文社区**: 微信公众号 (WeChat)、小红书 (Xiaohongshu/Rednote)、知乎 (Zhihu)、B站 (Bilibili)。
*   **🇺🇸 国际社区**: LinkedIn (领英)、Twitter (X)、Instagram、Facebook、TikTok。
*   **🎮 极客社区**: Discord (Server Invite Discovery)。
*   **📚 学术硬指标**: Google Scholar (谷歌学术)、NSF REU 官网。

#### 2. 🧠 企业级 AI 匹配引擎 (Qwen 2.5 + Agent)
*   **双语大模型**: 弃用了传统的关键词匹配，改用 **Alibaba Qwen2-1.5B-instruct** 模型。它能同时读懂中文的小红书笔记和英文的 LinkedIn 招聘贴，实现跨语言语义检索。
*   **深度推理 Agent**: 系统不仅给出一个“匹配度分数”，还会生成一段 **Agent Analysis**，告诉您：“虽然该教授主要做物理，但他最近这篇关于‘量子机器学习’的论文与您的 CS 背景高度契合。”

#### 3. 🛡️ 智能申请保护 (Smart Application Safety)
*   **表单优先 (Direct Apply First)**: 自动检测 Google Forms、Typeform、Qualtrics 等申请链接。如果发现，系统会**警告**不要发邮件，而是直接填表，避免被教授拉黑。
*   **安全邮件助理 (Safe Email Drafter)**: 如果必须发邮件，系统提供 `/api/generate-draft` 接口，根据教授最近的论文自动生成**高度个性化、符合学术礼仪**的邮件草稿（含收件人、主题），绝不自动群发。

### 🛠 技术架构
*   **后端**: Python 3.9 + FastAPI
*   **AI 模型**: Sentence-Transformers (Qwen2-1.5B) + Heuristic Agents
*   **数据采集**: SerpApi (Google Search Engine) + BeautifulSoup
*   **数据库**: SQLite (内置向量存储与 Agent 分析记录)

---

## 🇺🇸 English Introduction

### 🚀 Key Features (Updated 2025.12.20)
This is no longer just a scraper; it is an **Agentic Academic Assistant**.

#### 1. 🌍 Global Multi-Platform Coverage
We monitor 10+ platforms in real-time to find hidden opportunities:
*   **Academic**: Google Scholar, NSF REU.
*   **Professional**: LinkedIn, Twitter (X).
*   **Social & Community**: Instagram, Facebook, Discord Servers.
*   **China-Specific**: WeChat Official Accounts, Xiaohongshu (Rednote), Zhihu, Bilibili.

#### 2. 🧠 Enterprise-Grade AI Engine (Qwen 2.5)
*   **Bilingual LLM**: Powered by **Alibaba Qwen2-1.5B-instruct**, capable of understanding both Chinese social posts and English academic papers in the same vector space.
*   **Reasoning Agent**: The system provides an **"Agent Analysis"** for each match, explaining *why* a professor is a good fit based on their recent publications.

#### 3. 🛡️ Application Safety & Etiquette
*   **Direct Form Detection**: Automatically detects Google Forms/Typeform links. If found, it prioritizes the form and warns the student *not* to send a cold email.
*   **AI Email Drafter**: For professors without forms, the Agent generates a **highly personalized draft** referencing their specific recent work, ensuring high response rates without spamming.

### 📦 Installation

1.  **Clone the repository**:
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
