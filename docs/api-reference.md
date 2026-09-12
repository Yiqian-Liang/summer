# API 汇总（核心）

除登录/注册以外，以下是常用可文档化接口。

## 认证规则

- 读操作通常依赖会话 cookie。
- 写操作普遍要求登录 + `X-CSRF-Token`。
- 常见未登录响应码：`401`；缺 token：`403`。
- 注册、发帖、评论、AI 请求和在线搜索按用户或匿名地址写入 SQLite 固定时间窗计数，强制刷新使用更低配额；超限返回 `429` 和 `Retry-After`，计数不会因应用重启而立即丢失。
- 维护接口 `POST /api/collect` 不使用普通用户权限，必须在请求头提供与 `ADMIN_API_TOKEN` 一致的 `X-Admin-Token`；服务端未配置令牌时返回 `503`。

## 邮箱与邮件草稿

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/mail/accounts` | 已绑定邮箱列表、能力与限额 |
| `POST` | `/api/mail/accounts` | 新增 SMTP 邮箱 |
| `DELETE` | `/api/mail/accounts/{account_id}` | 删除邮箱 |
| `GET` | `/api/mail/drafts` | 草稿列表 |
| `POST` | `/api/mail/drafts` | 新建草稿 |
| `GET` | `/api/mail/drafts/{draft_id}` | 草稿详情 |
| `PUT` | `/api/mail/drafts/{draft_id}` | 更新草稿（乐观锁） |
| `DELETE` | `/api/mail/drafts/{draft_id}` | 删除草稿 |
| `POST` | `/api/mail/drafts/{draft_id}/send` | 提交发送（明确确认；仅确定成功时更新申请联系状态） |
| `POST` | `/api/mail/drafts/{draft_id}/attachments` | 上传附件（文件请求体，`filename` 查询参数，`X-Draft-Version` 版本头） |
| `GET` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 下载附件 |
| `DELETE` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 删除附件（版本头） |

### OAuth

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/mail/oauth/providers` | 可用 OAuth 提供方 |
| `POST` | `/api/mail/oauth/{provider}/start` | 创建授权 URL |
| `GET` | `/mail/oauth/{provider}/callback` | 回调入口 |

### 邮件页面与工具

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/mail` | 草稿页面 |
| `GET` | `/api/mail/context/{opportunity_id}` | 项目上下文 |
| `GET` | `/api/mail/capabilities` | AI 草稿能力 |
| `POST` | `/api/mail/generate` | 生成邮件（模板/可选 AI） |

## 社区帖

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/posts` | 公开帖子列表（可按类型/搜索/页码） |
| `GET` | `/posts/mine` | 我的帖子 |
| `GET` | `/posts/new` | 发布页 |
| `GET` | `/posts/{post_id}` | 帖子详情 |
| `GET` | `/posts/{post_id}/edit` | 编辑页（本人可见） |
| `POST` | `/api/posts` | 创建帖子（初始 draft） |
| `PUT` | `/api/posts/{post_id}` | 编辑帖子 |
| `POST` | `/api/posts/{post_id}/status` | 更新状态 |
| `POST` | `/api/posts/{post_id}/comments` | 发表评论 |
| `DELETE` | `/api/posts/{post_id}/comments/{comment_id}` | 删除自己的评论 |

## 简历与匹配

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/resume` | 读取简历 |
| `POST` | `/api/resume/parse` | 解析文件，返回 text/keywords |
| `PUT` | `/api/resume` | 保存简历（含 `updated_at` 并发保护） |
| `DELETE` | `/api/resume` | 删除简历 |
| `GET` | `/api/resume/review` | 本地规则检查；可传 `opportunity_id` 查看共同证据与待核对词 |

页面入口为 `/resume` 和 `/resume/review`。检查接口需要登录，不调用网络或 LLM，不返回数字评分；目标既可为公开机会，也可为属于当前用户的私有导入。

## 搜索与推荐

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/search` | 页面化搜索 |
| `GET` | `/api/search` | API 搜索（`q`,`source`,`sort`,`page`,`limit`,`live`,`refresh`） |
| `GET` | `/api/opportunities` | 基础列表分页 |
| `POST` | `/api/recommendations/feedback` | 反馈（hide/unhide/restore/clear_history/set_learning） |
| `POST` | `/api/save/{opportunity_id}` | 收藏 |
| `DELETE` | `/api/save/{opportunity_id}` | 取消收藏 |
| `POST` | `/api/saved/import` | 私有导入外部 URL、标题和可选摘要；不抓取、不进入公共搜索 |
| `POST` | `/api/saved/{opportunity_id}/progress` | 更新申请阶段、方式、下一步、结果、提醒天数和备注 |
| `GET` | `/api/saved` | 我的申请列表（兼容原收藏接口） |
| `GET` | `/applications` | 申请工作台；`/saved` 为兼容入口 |
| `GET` | `/applications/compare` | 对比本人 2–4 条不同申请，参数 `ids` 可重复传递或逗号分隔 |
| `GET` | `/api/applications/calendar.ics` | 导出本人截止日期；可传 `opportunity_id` 导出单项 |
| `GET` | `/api/source-preview/{opp_id}` | 源页面摘要 |
| `POST` | `/api/track` | 行为记录（search/click） |
| `POST` | `/api/analyze-match` | 登录用户的项目匹配分析；默认本地规则，受持久限流保护 |
| `POST` | `/api/collect` | 管理员令牌保护的手动采集任务 |

## 用户与配置（站点）

- `/register`, `/login`, `/logout`, `/forgot-password`, `/reset-password`
- `/onboarding`, `/api/profile`, `/set-lang/{lang}`
- `/api/stats`, `/applications`, `/saved`

## 说明

附件已完整接入草稿的上传、列出、下载、移除和真实 SMTP/OAuth 发送；当前每封最多 5 个、合计 2 MiB，每用户总存储 20 MiB。附件只属于当前用户的草稿，不交给 AI。

`ENABLE_MATCH_AI=0` 时 `/api/analyze-match` 只返回本地可回溯证据；只有显式设置为 `1` 且配置有效 `GEMINI_API_KEY` 时，用户主动请求才允许 AI 补充。站内待办和 `.ics` 都由用户主动查看或导出，不会使用绑定邮箱自动发送提醒。

完整返回字段和错误码见具体实现；敏感写操作落在“本用户会话 + CSRF + 乐观并发控制”路径，维护采集使用单独的管理员令牌。
