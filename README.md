# Global Academic Recruitment & Research Match System (全球学术招聘与研究匹配系统)

A research application workspace for finding, comparing and following up on Summer Research, RA and PhD opportunities.
这是一个用于发现、比较和跟进全球暑研、RA 与博士机会的科研申请工作台。

## 🚧 项目状态：建设中的 Alpha

> **本项目仍在搭建中，尚未提供公开在线服务。** 下方“已实现”表示功能已经进入仓库并通过自动测试，可以在本地运行；不代表已经完成生产部署、第三方平台审核或大规模真实用户验证。

### ✅ 已经实现

- **机会搜索与排序**：本地索引、中英查询扩展、相关性证据、过期过滤、去重、来源筛选和可选语义重排。
- **申请行动工作台**：收藏与外部线索导入、阶段和下一步、结果记录、2–4 项比较、今日待办及 `.ics` 日历导出。
- **简历工具**：PDF/DOCX/TXT 本地解析、用户确认后保存、项目匹配证据及不调用 LLM 的规则检查。
- **邮件工作台**：中英文模板、私有草稿、多个邮箱、附件、发送前确认，以及 SMTP、Google 和 Microsoft 连接代码。
- **社区基础功能**：科研招募、经验分享、讨论帖、评论、编辑和下架；已发布招募可进入机会搜索。
- **个人推荐**：结合简历、偏好、收藏和近期行为的可解释排序，支持“不感兴趣”和关闭行为学习。
- **工程基础**：登录隔离、CSRF、防滥用限流、管理员采集权限、Docker、健康检查和 GitHub Actions 自动测试。

### 🔧 已实现，但需要外部配置或进一步实测

- SerpApi、Gemini 在线补充搜索和可选 AI 润色需要各自的 API Key；没有 Key 时使用本地数据、规则匹配和免费模板。
- Gmail、Outlook OAuth 需要管理员创建 OAuth 应用；QQ、网易和学校邮箱需要服务商允许的授权码或 SMTP 设置。
- MediaCrawler 与用户本机 OpenCLI 搜索需要单独安装、登录相应平台，并遵守平台条款和频率限制。
- 生产部署文件已经准备好，但目前没有创建付费云资源、公开域名或正式数据库备份。

### 🧭 尚未实现或仍在规划

- 导师/机构邮箱认证、实验室主页认领、结构化候选人处理和完整导师端。
- 通用站内私信、自动邮件提醒、移动端应用及成熟的通知中心。
- 科研合作需求板、能力与仪器目录、正式预约/支付，以及论文选题验证工作台。
- 基于跨用户结果数据的协同推荐或学习排序；当前只使用可解释的本地规则和本人数据。
- 订阅、付费墙、全平台自动 AI、用户自带 API Key 管理，以及面向机构的商业功能。
- 面向公开生产环境的完整安全审计、监控告警、灾备和多实例数据库架构。

**Project status (English):** active Alpha development. Core workflows are implemented and tested locally, while public hosting, provider credentials, institutional verification, production operations and the roadmap items above are not yet complete.

## 🇨🇳 中文介绍 (Chinese Introduction)

### 🚀 核心亮点（2026.10.05 更新）
平台把跨来源搜索、申请管理、简历检查、邮件与材料放在一条可追溯的工作流里。

#### 1. 🌍 全球全平台数据覆盖 (Global Data Coverage)
我们不再局限于大学官网，系统可从以下来源的公开网页索引检索信息；实际覆盖取决于已配置的搜索服务和导入数据，不代表已登录每个平台或持续后台监控：
*   **🇨🇳 中文社区**: 微信公众号 (WeChat)、小红书 (Xiaohongshu/Rednote)、知乎 (Zhihu)、B站 (Bilibili)。
*   **🇺🇸 国际社区**: LinkedIn (领英)、Twitter (X)、Instagram、Facebook、TikTok。
*   **🎮 极客社区**: Discord (Server Invite Discovery)。
*   **📚 学术硬指标**: Google Scholar (谷歌学术)、NSF REU 官网。

#### 2. 🧠 双语混合搜索与匹配
*   **可解释召回**: 中英文全部查询条件必须在原文内容中命中；兼容短词边界、复数和连字符，结合招聘信号、来源可信度和发布时间排序。爬取关键词只记来源，不作为内容证据。
*   **语义重排**: 可选使用 `paraphrase-multilingual-MiniLM-L12-v2` 对已通过规则校验的候选做轻量重排，不让低相关语义结果覆盖明确的关键词证据。
*   **项目匹配分析**: 默认使用本地、可回溯的文字证据，不预测录取概率。只有设置 `ENABLE_MATCH_AI=1`、配置有效密钥且用户主动请求时，才允许用 AI 补充分析。

#### 3. 🛡️ 智能申请保护 (Smart Application Safety)
*   **表单优先 (Direct Apply First)**: 自动检测 Google Forms、Typeform、Qualtrics 等申请链接。如果发现，系统会**警告**不要发邮件，而是直接填表，避免被教授拉黑。
*   **邮件工作台**: 项目详情可进入 `/mail`，用免费中英文模板起草、编辑和私有保存。可绑定多个邮箱，核对全文后逐封确认发送。AI 起草默认关闭，只有管理员启用且用户主动选择时才调用。
*   **社区**: `/posts` 支持科研招募、经验分享、讨论和评论。只有已发布招募进入项目搜索；草稿、下架帖和其他类型不会混入机会列表。发布者身份未经平台认证。
*   **申请工作台**: `/applications` 汇总站内收藏与私有外部线索，支持阶段、下一步、结果、待核验项、2–4 项对比、相关邮件/附件以及截止日期日历导出。`/saved` 保留为兼容入口。

### 🛠 技术架构
*   **后端**: Python 3.11 + FastAPI
*   **排序**: 默认使用可解释混合排序；Sentence-Transformers (Multilingual MiniLM) 为可选增强
*   **数据采集**: SerpApi + Gemini Grounding + MediaCrawler 导入 + BeautifulSoup
*   **数据库**: SQLite (内置向量存储与 Agent 分析记录)

## 文档索引

- [邮箱工作台（邮件起草/附件/OAuth）](docs/mail-workspace.md)
- [社区帖子与评论（招募/经验/讨论）](docs/community-posts.md)
- [简历上传与匹配（带证据链）](docs/resume-matching.md)
- [搜索与推荐机制（排序/反馈/冷启动）](docs/search-and-recommendation.md)
- [AI 集成策略（免费/付费边界）](docs/ai-integration.md)
- [接口速查（邮件/社区/搜索/推荐）](docs/api-reference.md)
- [推荐算法设计（2026-09-08）](docs/recommendation-algorithm-2026-09-08.md)
- [产品方向与分阶段实施状态（2026-09-08）](docs/product-direction-2026-09-08.md)
- [搜索完善检查记录（2026-09-07）](docs/search-improvements-2026-09-07.md)
- [GitHub 搜索栈回顾（2026-07-26）](docs/github-search-stack-review-2026-07-26.md)
- [邀请制 Alpha 部署（Docker / GitHub Actions / Render）](docs/deployment.md)

---

## 🇺🇸 English Introduction

### 🚀 Key Features (Updated 2026.10.05)
The platform joins cross-source search, application tracking, resume review, mail and materials in one traceable workflow.

#### 1. 🌍 Global Multi-Platform Coverage
We search public web indexes and imported records across multiple platforms. Coverage depends on configured providers; platform login and continuous background monitoring are separate workflows:
*   **Academic**: Google Scholar, NSF REU.
*   **Professional**: LinkedIn, Twitter (X).
*   **Social & Community**: Instagram, Facebook, Discord Servers.
*   **China-Specific**: WeChat Official Accounts, Xiaohongshu (Rednote), Zhihu, Bilibili.

#### 2. 🧠 Explainable Bilingual Search
*   **Evidence-based retrieval**: Every query concept must match source content, with bilingual aliases, token boundaries, expiry checks and deduplication. Optional multilingual MiniLM reranking adds at most five points to the verified score.
*   **Fit analysis**: Local, traceable text evidence is the default and never claims an admission probability. AI supplementation requires `ENABLE_MATCH_AI=1`, a valid key and an explicit user request.

#### 3. 🛡️ Application Safety & Etiquette
*   **Direct Form Detection**: Automatically detects Google Forms/Typeform links. If found, it prioritizes the form and warns the student *not* to send a cold email.
*   **Mail workspace**: Free editable templates, private drafts, multiple optional mailboxes, and explicit review before each send. AI assistance is opt-in and disabled by default. Drafts use supplied project and student facts; response rates are not guaranteed.
*   **Community**: Recruitment, experience sharing, discussions and comments. Only published recruitment posts enter opportunity search; author identity is not verified.
*   **Application workspace**: `/applications` combines saved and privately imported leads with stages, next actions, outcomes, verification gaps, 2–4 item comparison, related mail/attachments and calendar export. `/saved` remains compatible.

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

    需要本地语义重排时再安装可选模型依赖：

    ```bash
    pip install -r requirements-semantic.txt
    ```

## 🏃‍♂️ Running the App

Start the server with hot-reload enabled:

```bash
./venv/bin/uvicorn app.main:app --reload
```

Visit `http://localhost:8000` in your browser.

## 邮件与社区配置

草稿与社区无需 AI Key，也无需绑定邮箱。启动时会自动添加所需表和字段，已有爬虫机会保留。邮件正文在服务器保存，按登录用户隔离；邮箱授权码和 OAuth 令牌使用 Fernet 加密保存。

同一邮件工作台支持选择、下载、移除附件，连接邮箱前后布局一致。文件自动保存到私有草稿，发送确认会列出附件；SMTP、Gmail 和 Outlook 均发送实际文件内容。当前每封最多 5 个附件、合计 2 MiB，每用户附件存储 20 MiB，适合压缩后的简历/成绩单；较大材料可附分享链接。附件不交给 AI，下载需登录本人账号，发送后不可替换原附件。正文与附件按账号隔离，但数据库文件本身未加密。

真实邮箱连接需要设置 `.env.example` 中的 `MAIL_ENCRYPTION_KEY`。只生成一次并安全备份，服务器重启后保持不变；换掉或丢失它后，已有邮箱需要重新连接。生产站点使用 HTTPS。目前登录会话保存在进程内，请使用单个服务进程；重启需重新登录，草稿和邮箱记录仍保留。

| 邮箱 | 连接方式 | 管理员准备 |
| --- | --- | --- |
| QQ、网易 | SMTP 授权码或应用专用密码 | 加密密钥 |
| 学校及其他邮箱 | 自定义 SMTP 地址、登录名，465 TLS 或 587 STARTTLS | 加密密钥；服务器需能访问该公开邮件服务器 |
| Gmail / Google Workspace | Google OAuth，也可使用服务商允许的应用专用密码 | Google OAuth Web 应用、启用 Gmail API、客户端 ID/Secret |
| Outlook / Microsoft 365 | Microsoft OAuth | 支持个人和组织账号的应用注册、客户端 ID/Secret、委派 Mail.Send 和 User.Read 权限 |

`SITE_BASE_URL` 设置为站点 origin（例如 `https://summer.example`；本机开发可用 `http://localhost:8000`），在授权平台登记精确回调：

```text
SITE_BASE_URL/mail/oauth/google/callback
SITE_BASE_URL/mail/oauth/microsoft/callback
```

学校能否连接仍受学校管理员策略影响；只支持校内网或自有单点登录的邮箱可能无法连接。Google/Microsoft 授权不读取收件箱，连接测试不发送邮件。SMTP 服务商是否在“已发送”保存副本取决于服务商；平台保留提交记录。状态不确定时禁止自动重发，需去真实邮箱核实。

免费模板始终可用。可选 AI 邮件设置 `ENABLE_AI_DRAFTING=1` 和有效 `GEMINI_API_KEY`；选 AI 后个人背景会发送给 Gemini。此开关仅控制邮件，已有在线搜索和匹配分析有独立调用。暂不增加订阅、付费墙或全站自动 AI 调用；有实际需求和预算再接计费与配额。

公开部署还应设置 `ADMIN_API_TOKEN`；`POST /api/collect` 只接受请求头 `X-Admin-Token` 与该值一致的维护请求，未配置时接口不可用。项目匹配分析默认走本地证据规则；只有 `ENABLE_MATCH_AI=1` 且存在有效 `GEMINI_API_KEY` 时才允许可选 AI 补充。注册、发帖、评论、AI 请求和在线搜索使用 SQLite 持久化限流，强制刷新另有更低配额，重启后不会清空当前时间窗；`RATE_LIMIT_SALT` 可为匿名访问标识提供稳定的哈希盐。单机 SQLite 方案适合当前部署，多主机部署时再迁移共享限流存储。

授权设置参考：[Google OAuth](https://developers.google.com/identity/protocols/oauth2/web-server)、[Gmail 发送](https://developers.google.com/workspace/gmail/api/guides/sending)、[Microsoft OAuth](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)、[Graph 发送](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)。

离线验证：`python3 -m unittest discover -s tests -q`。测试只使用临时数据库，并模拟 AI、SMTP 和 OAuth 请求。

## 部署

仓库包含生产 Docker 镜像、GitHub Actions 自动测试和 Render Blueprint。邀请制 Alpha 的部署、费用、持久磁盘、邮箱配置、回滚与上线检查见 [部署说明](docs/deployment.md)。Render 免费实例会丢失本地 SQLite，真实账号必须使用带持久磁盘的单实例服务。

## 申请行动工作台

登录后打开 `/applications`。原 `/saved` 地址继续可用，主入口已升级为按“今天要处理、准备中、已联系、已申请、有回复、已结束”分组的申请工作台。每张申请卡可记录申请方式、下一步时间、备注、面试阶段和最终结果，并显示相关邮件与附件历史。

站外看到的机会可只用 URL、标题和可选摘要导入。平台会规范化 URL，并在同一账号内去重；该私有记录不会抓取登录态页面、不会写入公共机会表，也不会进入搜索或推荐。站外导入缺少的截止日期、资金和要求会明确显示为待核验。

用户可选择 2–4 条本人申请进行并排比较，查看研究方向证据、导师/机构、申请方式、原文信息、待核验字段和当前下一步。比较页不生成录取概率或综合分。首页和申请页提供站内“今天该做什么”，依据下一步日期、截止日期及私有线索待核验状态提示；这些是页面待办，不会通过已绑定邮箱自动发送提醒。

`GET /api/applications/calendar.ics` 导出本人所有有明确截止日期的申请；传 `opportunity_id` 可导出单项。无有效截止日期的记录会跳过。“今天该做什么”也会提示准备中的申请缺少当前简历。日历由用户主动下载，平台不会代替用户发送提醒邮件。

## 简历上传与个人匹配

登录后打开 `/resume`，上传 PDF、DOCX 或 UTF-8 TXT，也可直接粘贴研究经历。文件最大 2 MiB，PDF 最多 20 页，正文最多 30000 字；扫描件需要改用带文字层的版本。解析在服务器本地进行，不调用 AI、不上传外部服务，原文件不留存。

用户核对正文与方向/技能词后点击“确认并保存”，才会更新用于匹配的信息。每个账号保存一份，可更换、关闭匹配或删除；文字在数据库内按账号隔离，数据库本身未加密。解析失败或尚未确认的新文件不会覆盖原版本。此处的简历不会自动加入邮件附件；邮件中的材料仍由用户逐份选择。

“查看匹配机会”和搜索中的“我的简历优先”按共同方向与技能数量排序，展开可查看简历和项目原文的依据。内置词支持常见中英别名，也可补充正文中出现的自定义研究术语。仅凭关键词无法判断录取概率、学位、资金、签证或时间资格，不依据姓名、年龄或学校名气评分。

`/resume/review` 提供免费的本地规则检查：检查教育、研究/项目、技能等基础栏目，日期格式与区间，经历要点是否包含行动、方法和可验证结果，以及重复的空泛表达。带 `opportunity_id` 时还会显示双方已有的共同证据，以及项目提到但简历中未检出的已知方向/技能，并统一标为“请核对”，不直接推断用户不会。该检查不联网、不调用 LLM，也不输出数字评分；公开机会与本人私有导入均可使用。

网页与 API 均支持 `sort=resume`，需要本人登录且已启用简历。`/api/search?sort=resume` 不发起在线搜索；有 `q` 时先应用原有查询条件，再按个人匹配排序，分页和来源筛选保持一致。私有接口为 `GET/PUT/DELETE /api/resume`、`GET /api/resume/review` 和 `POST /api/resume/parse?filename=cv.pdf`（原始文件请求体）；写操作需要登录会话的 `X-CSRF-Token`，保存须携带当前版本的 `updated_at`，首次为 `null`。

## 首页推荐算法

首页“为你推荐”已接入本地内容与反馈排序：先检查是否仍在招募，结合已确认简历、主动选择的偏好、当前收藏及近期浏览/搜索，再在相近分数中分散机构与来源。推荐理由可展开查看，“按发布时间”可切换为不使用个人信息的排序。所有结果先完整排序再分页，不需要付费 AI。

“管理我的推荐”支持关闭浏览/搜索记录、清除历史及恢复隐藏机会；“不感兴趣”只隐藏首页的对应项目。收藏权重大于单次浏览，重复访问去重，近期行为按时间减弱。个人数据不参与跨用户推荐或共享缓存。算法权重、限制、性能测量与后续协同过滤方案见 [推荐算法设计](docs/recommendation-algorithm-2026-09-08.md)。

反馈接口 `POST /api/recommendations/feedback` 支持 `hide/unhide`（`opportunity_id`）、`restore`、`clear_history`、`set_learning`（布尔 `enabled`）。反馈、收藏增删及 `/api/track` 写入需要本人的登录会话和 `X-CSRF-Token`；收藏记录是兴趣依据，客户端不再可以提交任意行为类型冒充申请结果。

## 搜索架构

搜索分为三层：

1. SQLite 本地索引负责中英扩词、全部概念命中、过期过滤、个人主页过滤和去重；不会先按质量分截掉候选。全部通过校验的结果可以分页，语义重排仅作用于前 200 条。
2. 配置 `SERPAPI_KEY` 或 `GEMINI_API_KEY` 后，第一页进行有总超时和请求预算的在线补充，中文综合查询兼顾官方与社交来源。成功结果缓存 10 分钟、最多 128 个查询；失败和部分失败不缓存。页面区分新结果、缓存、部分来源失败和完全失败，手动刷新可跳过缓存。
3. 网页 `/search` 和 `/api/search` 共用采集、入库、验证、排序与分页逻辑。API 可传 `page`、`limit`（1–100）、`source`、`refresh=true`；只搜索本地内容时传 `live=false`。查询最多 300 字符。
4. 管理员可用 MediaCrawler 离线导入小红书、知乎、B 站、微博和贴吧结果；微信公众号文章通过搜狗微信索引实时搜索，无需登录或 API Key。
5. 搜索页的“用我的小红书登录态搜索”通过用户本机的 OpenCLI 运行，保持平台原生顺序，Cookie 和结果默认都不上传、不入库。

可调参数见 [`.env.example`](.env.example)。Gemini 默认 `gemini-2.5-flash`，可通过 `GEMINI_MODEL` 修改；搜索可单独覆盖 `GEMINI_SEARCH_MODEL`。旧的 2.0 默认值已移除，模型生命周期见 [Google 官方说明](https://ai.google.dev/gemini-api/docs/deprecations)。搜索评分是相关性分数，不代表录取概率。

```text
/api/search?q=法国%20博士&source=euraxess&page=1&limit=20&live=false
```

常用参数：

```dotenv
LIVE_SEARCH_MAX_REQUESTS=6
LIVE_SEARCH_TOTAL_TIMEOUT=25
LIVE_SEARCH_CACHE_SECONDS=600
SEARCH_REQUEST_TIMEOUT=15
ENABLE_SOGOU_WECHAT=1
```

## 用户登录态小红书搜索（OpenCLI）

这条链路不共享平台账号：每个用户复用自己 Chrome 中的小红书登录态。Summer 页面只在用户点击后访问本机只读 companion；companion 仅接受明确配置的 Summer 来源，并用固定参数调用 OpenCLI。

首次开发验证：

```bash
# 推荐安装 OpenCLIApp；也可使用 Node.js >= 20
npm install -g @jackwener/opencli
opencli doctor

# 在 Chrome 登录小红书后，从 Summer 项目目录启动本机 companion
python3 -m app.opencli_companion
```

随后打开 `http://localhost:8000/search?q=暑研&source=xiaohongshu`，点击“用我的小红书登录态搜索”。Chrome 首次可能询问本地网络访问权限；允许后，结果只临时显示在当前页面。

生产域名必须显式加入白名单：

```bash
python3 -m app.opencli_companion --origin https://summer.example.com
```

companion 固定绑定 `127.0.0.1:19826`，不接受任意 OpenCLI 命令，不把结果写入共享 SQLite。

## MediaCrawler 接入

MediaCrawler 使用自己的非商业学习许可证。请先确认使用场景符合其许可证、目标平台条款和频率限制。它支持小红书 (xhs)、知乎 (zhihu)、B站 (bili)、微博 (wb)、贴吧 (tieba)，本项目导入其结构化输出，不在 Web 请求里启动浏览器爬虫。

MediaCrawler 需要 **Python ≥ 3.11**（系统自带 3.9 无法运行）；推荐安装 [uv](https://docs.astral.sh/uv/)，它会自动准备解释器和依赖。

```bash
# 放在当前项目内；runner 也兼容旧的同级目录
git clone --depth 1 https://github.com/NanmiCoder/MediaCrawler.git MediaCrawler

# 一键爬取全部平台并导入（每个平台首次运行需扫码登录）
python3 -m app.mediacrawler_runner

# 也可以只跑部分平台/关键词
python3 -m app.mediacrawler_runner --platforms xhs,zhihu --keywords 暑研,科研实习

# 只导入已有导出文件（不启动浏览器）
python3 -m app.mediacrawler_runner --import-only
```

等价的原始命令（runner 内部就是逐个调用它们）：

```bash
cd MediaCrawler
uv sync
uv run main.py --platform xhs --lt qrcode --type search \
  --keywords "暑研,科研实习,博士招生" --get_comment false --save_data_option jsonl
cd ..
python3 -m app.mediacrawler_import MediaCrawler/data
```

也可以在 `.env` 设置 `MEDIACRAWLER_DATA_PATH=MediaCrawler/data`，应用启动时会增量导入。导入支持 JSON、JSONL 和 CSV，按原文 URL 幂等更新。

微信公众号文章不走 MediaCrawler：配置 `ENABLE_SOGOU_WECHAT=1`（默认开启）后，中文查询会实时搜索搜狗微信索引，无需任何 API Key；被限流时自动降级为仅本地结果。

## 验证

测试使用临时数据库和模拟搜索服务，不写入现有 `summer.db`。其中本机伴侣测试会短暂监听回环地址的随机端口。

```bash
python3 -m pip install -r requirements-dev.txt
PYTHONPYCACHEPREFIX=/tmp/summer-pycache \
DISABLE_LIVE_SEARCH=1 DISABLE_SEMANTIC_SEARCH=1 \
python3 -m unittest discover -s tests -v
```

本轮改动与剩余问题见 [搜索完善与项目检查记录](docs/search-improvements-2026-09-07.md)。
