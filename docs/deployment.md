# 邀请制 Alpha 部署

仓库使用 Docker 和 `render.yaml` 部署到 Render 新加坡区域。服务固定为一个进程和一个实例，SQLite 位于 `/data/summer.db`，`/data` 挂载 1 GB 持久磁盘。GitHub Actions 通过离线测试和容器健康检查后，Render 才自动发布对应提交。

## 费用与边界

- Render `0.5c-512mb` Web Service：当前为 7 美元/月。
- 1 GB 持久 SSD：当前为 0.25 美元/月。
- Render 免费 Web Service 没有持久磁盘，休眠、重启或重新部署会删除 SQLite，因此不能承载真实账号。
- 当前架构适合邀请制 Alpha。会话保存在单进程内，服务重启后用户需要重新登录；草稿、申请、简历和邮箱连接记录仍保留在 SQLite。

费用与产品限制可能变化，创建资源前以 Render 控制台显示为准。

## 首次部署

1. 在 Render Dashboard 选择 **New → Blueprint**，连接 GitHub 仓库 `Yiqian-Liang/summer`，分支选择 `main`。
2. Render 会读取仓库根目录的 `render.yaml`，创建新加坡区域的 Web Service 和 1 GB 持久磁盘。
3. 在 Blueprint 表单中填写 `SERPAPI_KEY` 与 `GEMINI_API_KEY`。两项都不写入 GitHub；若暂时留空，站内已有数据和本地规则仍可用，但在线补充来源会受限。
4. 确认控制台显示的月费后创建 Blueprint。首次构建会安装精简依赖、构建 Docker 镜像并访问 `/healthz`。
5. 部署成功后访问 Render 提供的 `https://*.onrender.com` 地址，完成注册、搜索、申请工作台和模板邮件冒烟测试。

`MAIL_ENCRYPTION_KEY`、`ADMIN_API_TOKEN` 与 `RATE_LIMIT_SALT` 由 Render 在首次创建时生成。不要重新生成或覆盖 `MAIL_ENCRYPTION_KEY`，否则已连接邮箱需要重新授权。

## 邮箱配置

QQ、网易和允许应用专用密码的学校邮箱只依赖已生成的 `MAIL_ENCRYPTION_KEY`。Google 与 Microsoft OAuth 需要在各自控制台创建 Web 应用，然后在 Render Environment 中添加：

- `GOOGLE_CLIENT_ID`、`GOOGLE_CLIENT_SECRET`
- `MICROSOFT_CLIENT_ID`、`MICROSOFT_CLIENT_SECRET`

回调地址分别为：

```text
https://<Render 域名>/mail/oauth/google/callback
https://<Render 域名>/mail/oauth/microsoft/callback
```

应用不读取收件箱，只申请身份信息和发信权限。正式开放邮箱前，应使用专门测试账号完成真实发送、附件、失败与结果未知场景验证。

## 发布与回滚

- 推送到 `main` 后，GitHub Actions 先运行完整离线测试并构建/启动生产 Docker 镜像。
- 只有检查全部通过，Render 才自动部署。
- `/healthz` 同时检查 Web 进程和 SQLite 可用性；新版本探针失败时不会接管流量。
- 回滚应用版本不会回滚 SQLite。涉及数据结构的更改应先备份 `/data/summer.db`。

## 上线后检查

1. `/healthz` 返回 `{"status":"ok"}`。
2. 注册、登录、退出与再次登录正常。
3. 搜索结果能打开原文，在线来源失败时页面明确降级。
4. 外部导入、比较、简历检查、邮件附件和申请进度可完成一遍。
5. 未配置 Google/Microsoft OAuth 时，页面明确显示不可连接；不得声称已经启用。
6. Render 磁盘快照和服务告警已开启，管理员令牌没有出现在日志或前端。
