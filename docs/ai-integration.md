# AI 集成说明（含免费/付费边界）

## 当前默认策略：免费版可用

- 草稿生成默认使用平台内模板。
- 开启 AI 仅在管理端环境变量允许时触发：
  - `ENABLE_AI_DRAFTING=1`
  - `GEMINI_API_KEY` 有效
- 未开启 AI 时，`/api/mail/generate` 仍可用，返回可编辑模板，不会阻断用户流程。

## 可用 AI 场景

### 邮件草稿（站内）

- 路径：`POST /api/mail/generate`、`POST /api/generate-rag-draft`
- 请求字段包含 `use_ai`：`true/false`
- 返回结果始终可编辑，模式信息可见。
- AI 失败时自动回退模板。
- 发送链接可选 `include_social_media`，如需联网公开检索并非必须。

### 项目分析（增强字段）

- 路径：`POST /api/analyze-match`
- `research_agent.analyze_match(..., use_llm=True)` 供前端二次展示，属于可选增强。

### 在线检索

- 语义排序/重排依赖 `gemini` 的可选后端；无可用凭据会回退为证据排序。

## 与平台功能的边界

- 默认不做付费配额管理、全站自动付费调用或自动发送。
- 站内邮件发送结果、行为日志、站外投递不会由 AI 自动执行。
- AI 调用不应泄露内部凭证；OAuth 发信只与用户选择邮箱与权限绑定。

## 环境变量对照

- `GEMINI_API_KEY`：RAG 与语义分析主钥
- `GEMINI_MODEL`：默认 `gemini-2.5-flash`
- `ENABLE_AI_DRAFTING=1` 开启 AI 草稿
- `ENABLE_DEV_PASSWORD_RESET`、`DISABLE_LIVE_SEARCH` 等不影响邮件草稿主流程

## 未来扩展建议（当前未默认启用）

- 付费场景可按“触发开关 + 次数配额 + 用户账单位”实现；当前不打散给所有接口自动透支调用。
