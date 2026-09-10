# 邮箱工作台文档

本平台的邮件工作台用于在站内完成“草稿编辑 → 选择发件账号 → 附件处理 → 手动确认发送”，不需要离开站点。

## 入口与页面

- 页面：`/mail`
  - `opportunity=<项目ID>`：从项目页进入时会预填联系人与上下文。
  - `draft=<草稿ID>`：打开特定草稿进行编辑。
- 页面交互：
  - 可切换语言（受站点语言影响）。
  - 所有草稿、附件与发送操作默认是**本人可见**。
  - 发送前有确认弹窗，不会自动发送。

## 邮箱绑定与账户

支持两类账户方式：

- SMTP（QQ/网易/Google/Microsoft/自定义）。
- OAuth（Google、Microsoft）。

核心约束：

- 每个用户最多绑定 **5** 个邮箱。
- 绑定/发件前都需要登录；仅草稿不依赖邮箱配置也可保存。
- 发送凭证使用 `MAIL_ENCRYPTION_KEY` 对称加密，未配置时：
  - `/api/mail/accounts` 会返回 `configured=false` 和原因；
  - 绑定账号会被拒绝，但保存草稿仍可用。

### 接口总览

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/mail/accounts` | 当前用户绑定账号列表 + 是否可发件 |
| `POST` | `/api/mail/accounts` | 新增 SMTP 账号 |
| `DELETE` | `/api/mail/accounts/{account_id}` | 断开账号（待发送邮件会阻止） |

### OAuth 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/mail/oauth/providers` | 可用 OAuth 提供方 |
| `POST` | `/api/mail/oauth/{provider}/start` | 拉起授权 URL |
| `GET` | `/mail/oauth/{provider}/callback` | OAuth 回调回站 |

配置要求：

- `SITE_BASE_URL` 必须为站点根路径（本地可用 `http://localhost:8000`）。
- 回调固定为：
  - `SITE_BASE_URL/mail/oauth/google/callback`
  - `SITE_BASE_URL/mail/oauth/microsoft/callback`
- 需要 `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` 或 `MICROSOFT_CLIENT_ID`/`MICROSOFT_CLIENT_SECRET`。

## 草稿与附件

### 草稿接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/mail/drafts` | 列表 |
| `POST` | `/api/mail/drafts` | 新建草稿（登录+CSRF） |
| `GET` | `/api/mail/drafts/{id}` | 获取草稿详情 |
| `PUT` | `/api/mail/drafts/{id}` | 更新草稿（带 `updated_at` 做并发防护） |
| `DELETE` | `/api/mail/drafts/{id}` | 删除草稿 |
| `POST` | `/api/mail/drafts/{id}/send` | 发送草稿（需 `confirm=true`） |

草稿字段限制：

- 收件人：单地址、RFC 5321 风格基础校验；不支持群发。
- 主题：1–250 字符，不能包含控制字符。
- 正文：最多 100000 字符，不允许空字符。
- 语言：`en` 或 `zh`。
- `recipient/subject/body/account_id/opportunity_id/updated_at` 需合法。

发送前置条件：

- `confirm=true`。
- 草稿已保存且未变更。
- 主题和正文均非空。
- 已选择发件账号。

### 附件接口（上传/下载/删除）

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/mail/drafts/{draft_id}/attachments?filename=...` | 上传 |
| `GET` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 下载 |
| `DELETE` | `/api/mail/drafts/{draft_id}/attachments/{attachment_id}` | 删除（需上传版本号头） |

附件规则：

- 每封邮件最多 **5** 个附件。
- 每封邮件总计大小上限 **2 MiB**。
- 单用户附件历史上限 **20 MiB**。
- 文件名不能为空、不能含路径分隔符、不能为 `.`/`..`、不能含控制字符。
- 下载流使用 `attachment; filename*=UTF-8''...`，并带有 `no-store` 等安全响应头。
- 删除/上传附件时需 `X-Draft-Version`，防止并发旧版本修改。

## 发送状态说明

草稿状态：`draft`、`failed`（可编辑）/`sending`（锁定中）/`sent`/`unknown`。

发送异常处理：

- OAuth/SMTP 拒绝会记录 `last_error`，状态通常为 `failed`。
- 网络/中断导致结果不确定时返回 `unknown`，系统不自动重试；请用户到真实邮箱核实后再决定。
- 发生发送中的并发操作时，草稿版本受保护，不能被删除/修改。

## 与 AI 草稿结合

`/mail` 页面调用 `POST /api/mail/generate` 生成草稿时：

- 默认使用免费本地模板。
- 只有在 `ENABLE_AI_DRAFTING=1` 且请求 `use_ai=true` 且配置 `GEMINI_API_KEY` 时才走 AI。
- AI 模式失败会自动回退到模板，并提示核对。

生成后的草稿默认不等于“已发送邮件”，仍需手动确认。
