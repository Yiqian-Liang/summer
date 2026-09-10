# API 汇总（核心）

除登录/注册以外，以下是常用可文档化接口。

## 认证规则

- 读操作通常依赖会话 cookie。
- 写操作普遍要求登录 + `X-CSRF-Token`。
- 常见未登录响应码：`401`；缺 token：`403`。

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
| `POST` | `/api/mail/drafts/{draft_id}/send` | 提交发送（确认） |
| `POST` | `/api/mail/drafts/{draft_id}/attachments` | 上传附件 |
| `GET` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 下载附件 |
| `DELETE` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 删除附件（版本头） |

OAuth |

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/mail/oauth/providers` | 可用 OAuth 提供方 |
| `POST` | `/api/mail/oauth/{provider}/start` | 创建授权 URL |
| `GET` | `/mail/oauth/{provider}/callback` | 回调入口 |

邮件页面与工具 |

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

## 搜索与推荐

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/search` | 页面化搜索 |
| `GET` | `/api/search` | API 搜索（`q`,`source`,`sort`,`page`,`limit`,`live`,`refresh`） |
| `GET` | `/api/opportunities` | 基础列表分页 |
| `POST` | `/api/recommendations/feedback` | 反馈（hide/unhide/restore/clear_history/set_learning） |
| `POST` | `/api/save/{opportunity_id}` | 收藏 |
| `DELETE` | `/api/save/{opportunity_id}` | 取消收藏 |
| `POST` | `/api/saved/{opportunity_id}/progress` | 更新收藏进度（阶段/提醒天数/备注） |
| `GET` | `/api/saved` | 我的收藏列表 |
| `GET` | `/api/source-preview/{opp_id}` | 源页面摘要 |
| `POST` | `/api/track` | 行为记录（search/click） |
| `POST` | `/api/analyze-match` | 文本匹配分析（可选 LLM） |
| `POST` | `/api/collect` | 手动触发采集任务 |

## 用户与配置（站点）

- `/register`, `/login`, `/logout`, `/forgot-password`, `/reset-password`
- `/onboarding`, `/api/profile`, `/set-lang/{lang}`
- `/api/stats`, `/saved`

## 说明

完整返回字段和错误码见具体实现；所有敏感操作默认落在“本用户会话 + CSRF + 乐观并发控制”路径。
