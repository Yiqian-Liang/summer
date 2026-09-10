# 社区帖子系统（recruitment/experience/discussion）

用户可发布/管理自己的帖子，用于补齐站内招聘资源和经验讨论。

## 入口

- 列表页：`/posts`
  - 参数：`page`、`kind=recruitment|experience|discussion`、`q`
- 个人页：`/posts/mine`
- 发布页：`/posts/new`
- 详情页：`/posts/{post_id}`
- 编辑页：`/posts/{post_id}/edit`

## 帖子类型

- `recruitment`：科研招募（默认）
- `experience`：经验分享
- `discussion`：讨论帖

## 数据模型与校验（`PostInput`）

- 必填：`title`（1–180，单行）
- 可选：`university`、`professor_name`、`description`、`requirements`、`deadline`、`contact_email`、`direct_apply_link`
- `deadline`：`YYYY-MM-DD`
- `contact_email` 要求合法邮箱（`experience/discussion` 可留空）
- `direct_apply_link`：必须是 `http/https` URL

## 发布状态和权限

- `create_post` 初始状态始终是 `draft`。
- 可访问 API：`POST /api/posts`，`PUT /api/posts/{id}`，`POST /api/posts/{id}/status`。
- `status`：`draft`、`published`、`closed`。
- 招募帖发布前必须满足：
  - 正文 ≥ 20 字
  - 有机构信息
  - 至少提供 `contact_email` 或 `direct_apply_link`
- 仅发布中的帖会在公开路径 `/posts` 中显示；`closed` 不再对外可见。
- 页面与 API 均要求登录，登录 + `X-CSRF-Token` 用于所有写操作。

## 与搜索的关系

- 招募帖在数据库里使用 `provider=user_post`、`source_type=community`。
- 只有 `post_status='published'` 且 `post_kind='recruitment'` 的帖子才会进入 `/api/search` / 首页机会列表。
- 经验帖/讨论帖仅在社区页展示，不参与机会检索。

## 评论能力

- `POST /api/posts/{post_id}/comments`
- `DELETE /api/posts/{post_id}/comments/{comment_id}`

评论规则：

- 只允许在已发布帖子下新增。
- 内容 1–2000 字；支持 HTML 转义。
- 只能删除自己发布的评论。
- 评论分页：`/posts/{id}?page=N`，每页 50 条。

## 与项目页联动

- `POST` 为招募帖后会生成 `source_url`（指向 `/posts/{id}`）。
- 已发布帖子详情页会显示“起草联系邮件”按钮，跳转到 `/mail?opportunity={post_id}`。
