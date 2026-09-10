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
  - `q` 不能为空。
- 本地排序权重包含：
  - 关键词命中覆盖度（`lexical_score`）
  - 招募信号、可信源加权
  - 质量与时效
  - 语义重排提升
- 来源去重策略：URL/标题-机构组合去重。

## 推荐（首页）

- 相关入口：`/` 与 `GET /api/recommendations/feedback`。
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

- 点击保存：`POST /api/track`
- 收藏：`POST /api/save/{opportunity_id}`，移除：`DELETE /api/save/{opportunity_id}`，获取：`GET /api/saved`
- 推荐反馈：`POST /api/recommendations/feedback`
  - `hide/unhide`
  - `restore`
  - `clear_history`
  - `set_learning`（开启/关闭行为学习）

## 公开机会过滤

- 非招募帖（如社区经验帖/讨论）默认不入推荐与机会检索。
- 资料类 profile 页面（如 social profile）会在一些来源自动过滤。

## 设计说明

- 平台不做“录取概率预测”；给出的只是当前证据/偏好的匹配排序。
- 个人信息不跨用户共享；不做全局行为协同（当前版本无训练模型写回）。
