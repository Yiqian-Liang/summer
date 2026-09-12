# 搜索、排序与推荐机制说明

## 检索链路（`/search` 与 `/api/search` 共用）

1. 先执行本地检索与概念匹配（SQLite FTS + 字段验证）。
2. 若查询开启 `live=true` 且第一页，会尝试在线补充（受 `LIVE_SEARCH_MAX_REQUESTS` 与超时限制）。
3. 合并候选后做失效/来源过滤、概念校验。
4. 语义重排只对前 200 条执行（`matcher.rank_candidates`）。
5. 合并回全量分页输出。

约束：

- `q` 最长 300 字。
- `page`/`limit`：`1 <= page`，`1 <= limit <= 100`。
- 仅第一页会尝试 `live`。

## 查询参数

- `source`：按来源过滤（`university/community/linkedin/.../wechat/xiaohongshu/.../all`）
- `live`：是否允许首屏补充。
- `refresh=true`：强制刷新实时抓取。
- `sort=relevance`（默认）或 `sort=resume`。

### 排序与筛选

- `sort=resume`：需登录且已启用简历。
  - 仍可配 `q` 与 `source`。
  - `q` 可省略；此时浏览所有与已确认简历存在共同证据的本地机会，不触发在线搜索。
- 本地排序权重包含：
  - 关键词命中覆盖度（`lexical_score`）
  - 招募信号、可信源加权
  - 质量与时效
  - 语义重排提升
- 来源去重策略：URL/标题-机构组合去重。

## 推荐（首页）

- 相关入口：`/` 与 `POST /api/recommendations/feedback`。
- 主要输入：用户偏好（profile）、收藏/点击行为、隐藏记录、简历关键词。
- 排序项（参考）：
  - 简历匹配（优先）
  - 偏好匹配（研究方向/学科）
  - 收藏与浏览行为
  - 最近搜索行为（90 天内，去重）
  - 来源可靠度
  - 质量分与时效
- 首页前 200 条会做来源/机构去重增强，多元化分散展示。

## 行为与反馈

- 本人搜索/点击：`POST /api/track`，只接受与目标类型一致的 `search/click`。
- 收藏：`POST /api/save/{opportunity_id}`，移除：`DELETE /api/save/{opportunity_id}`，获取：`GET /api/saved`
  - `/applications` 是当前申请工作台，`/saved` 保留兼容。
  - 每条申请可在 `POST /api/saved/{opportunity_id}/progress` 更新阶段（准备中/已联系/已申请/收到回复/面试/完成）、申请方式、下一步时间、结果、提醒天数与备注。
  - `POST /api/saved/import` 保存站外 URL、标题和可选摘要，只进入当前账号的申请表，不抓取页面，也不进入公共检索或推荐。
- 推荐反馈：`POST /api/recommendations/feedback`
  - `hide/unhide`
  - `restore`
  - `clear_history`
  - `set_learning`（开启/关闭行为学习）

## 公开机会过滤

- 非招募帖（如社区经验帖/讨论）默认不入推荐与机会检索。
- 资料类 profile 页面（如 social profile）会在一些来源自动过滤。
- 私有外部导入不写入公共机会表，因此不会出现在任何用户的搜索、首页推荐或公开统计中。

## 设计说明

- 平台不做“录取概率预测”；给出的只是当前证据/偏好的匹配排序。
- 个人信息不跨用户共享；不做全局行为协同（当前版本无训练模型写回）。

## 申请进度、站内待办与日历

- 首页和申请页会展示站内“今天该做什么”：下一步已到期、截止日期进入用户设置的提醒窗口，或私有导入仍待核验。
- 申请页按今天要处理、准备中、已联系、已申请、有回复、已结束分组，并保留每一步的累计时间点用于以后计算申请漏斗。
- `GET /api/applications/calendar.ics` 导出本人所有有明确截止日期的申请；传 `opportunity_id` 可导出单项。滚动申请或无法解析的日期不会生成事件。
- 提醒天数只影响页面待办，不影响公共排序。当前不通过已绑定邮箱自动发送提醒；日历文件也必须由用户主动下载。

## 匹配分析保护

- `POST /api/analyze-match` 要求登录、CSRF 并使用 SQLite 持久化限流。
- `ENABLE_MATCH_AI=0` 时只运行本地文字证据分析；只有管理员设置 `ENABLE_MATCH_AI=1`、提供有效 `GEMINI_API_KEY`，且用户主动发起分析时才允许 LLM 补充。
- 本地分析与简历检查都不预测录取概率；简历检查详情见 [简历上传与个人化匹配](resume-matching.md)。
