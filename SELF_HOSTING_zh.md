# 自部署运维指南

本指南面向**自部署 OpenLucid 的运维**——找回管理员账号、配置邮箱、检查健康状态等。
如果你想看的是「OpenLucid 怎么用」，请回到 [README_zh.md](README_zh.md)。

---

## OpenLucid 有两个 CLI —— 看场景挑

OpenLucid 同时带两条命令行入口。它们外观相似，但解决**正好相反的故障模式**，
不要选错。

### 1. `openlucid` —— host 端，走 HTTP

```bash
openlucid list-merchants
openlucid kb-qa --offer xxx --question "..."
```

- 跑在**你的笔记本 / 服务器上**，容器外
- 通过 REST API 跟运行中的 app 通信
- 需要登录态（token 缓存在 `~/.openlucid.json`）
- 通过 `bash tools/install.sh` 安装（在 `~/.local/bin/` 放个 symlink）
- 每条命令都有 1:1 对应的 MCP tool —— AI Agent 看到一样的 surface
- **用于日常数据操作** —— 查 offer、写脚本、跑 topic studio 等等，
  Web UI 能做的它都能做

### 2. `python -m app.cli` —— 容器内，直连 DB

```bash
docker compose exec app python -m app.cli <subcommand>
```

- 跑在**容器内部**
- **直连 PostgreSQL**，绕开 HTTP / 鉴权 / 邮件
- 不需要登录、不需要 token —— `docker exec` 进容器**就是**信任边界
- **用于救场操作** —— 普通 CLI 通不到 app 的时候（忘密、邮件未配置、
  还没建第一个管理员等等）

> **为什么是两个 CLI？** 一条 binary 不可能既「走 HTTP」（要求 app 正常）
> 又「绕 HTTP」（要求 bypass 同一条路径）。两种故障模式相反，路径必然分裂。
> Dify 的拆分一样：`flask <command>` 直 DB 救场，`dify-cloud` API 跑日常。

---

## 账号恢复

需要 `python -m app.cli` 多半就是账号出了问题。下面三条命令都绕开 HTTP / 邮件 / 鉴权。

### 忘记管理员密码

如果 `docker/.env` 里配了 `RESEND_API_KEY` / `SMTP_HOST`，直接走登录页的
**忘记密码** 即可 —— 重置链接会发到你邮箱。

如果**没配邮件**，登录页会出红色错误提示让你看这里。运行：

```bash
docker compose exec app python -m app.cli reset-password \
  --email admin@example.com --new-password NewSecurePw123
```

密码要求：至少 8 位，**包含至少一个字母和一个数字**。

### 失去原邮箱访问权限

如果绑在管理员账号的邮箱不能再收信（换工作、域名废弃等），不走邮件验证直接改：

```bash
docker compose exec app python -m app.cli reset-email \
  --email old@example.com \
  --new-email new@example.com \
  --email-confirm new@example.com
```

`--email-confirm` 必须跟 `--new-email` 一字不差 —— 防手抖。

### Headless 首次部署（IaC / CI）

`/install.html` 装好向导对人友好，对自动化不友好。脚本里直接建第一个管理员：

```bash
docker compose exec app python -m app.cli create-admin \
  --email admin@example.com --password InitialPw123
```

建好之后，installer 路由自动重定向到 `/signin.html`。所以这条命令在
bootstrap pipeline 里**幂等可重跑** —— 重复跑会以 `user already exists` 退出
（exit code 2）。

---

## 自定义内容生成提示词

OpenLucid 给五类提示词都留了**用户自定义层** —— **平台**（公众号 / 博客 /
小红书 / ...）、**人设**（语气风格）、**叙事结构**（hook-body-cta 等）、
**内容形态**、**营销活动类型**。容器**首次启动时**会把所有 shipped `.md` 复制
进一个持久化 docker volume，这样你直接编辑这份副本，**`git pull` 不会冲突**。

### 编辑路径

| 类别 | 容器内路径 | 主机典型路径 |
|---|---|---|
| 平台 | `/app/uploads/platforms/<id>.md` | `docker/uploads/platforms/<id>.md` |
| 人设 | `/app/uploads/personas/<id>.md` | `docker/uploads/personas/<id>.md` |
| 叙事结构 | `/app/uploads/script_structures/<id>.md` | `docker/uploads/script_structures/<id>.md` |
| 内容形态 | `/app/uploads/content_forms/<id>.md` | `docker/uploads/content_forms/<id>.md` |
| 营销活动类型 | `/app/uploads/campaign_types/<id>.md` | `docker/uploads/campaign_types/<id>.md` |

主机路径都是 `$STORAGE_BASE_PATH/<category>/` 下面。

### 工作流

```bash
# 1. 编辑你的 overlay 副本（不是 app/apps/ 下的 shipped 文件）
vim docker/uploads/platforms/wechat_gzh.md

# 2. 重启容器让 loader 读新内容（registry 是 process 内 cache，启动时加载）
docker compose restart app

# 3. （可选）回滚到最新 shipped 版本 —— 删 overlay，重启，
#    seed 步骤会自动重新复制 shipped 版本
rm docker/uploads/platforms/wechat_gzh.md
docker compose restart app
```

### 覆盖机制

每个 registry 加载时会读两个目录：

1. **Shipped 默认**（`app/apps/<category>/*.md`）—— git 跟踪，`git pull` 会更新。
   **不要编辑这里**；每个 shipped 文件顶部都有一行 yaml 注释提醒你。
2. **用户 overlay**（`$STORAGE_BASE_PATH/<category>/*.md`）—— 持久化 docker volume。
   同 `id` 的 user 文件**胜出 shipped**。

新版本通过 `git pull` 升级 shipped 默认，**完全不动你的自定义**。
如果你想用最新版 shipped 默认，`rm` 你的 overlay 文件再重启 —— seed 步骤会重新复制。

### 暂时不能自定义的

下列提示词还硬编码在 Python 里，不能 overlay 覆盖（要改只能 fork）：

- 内容目标 Goals（`app/application/script_goals.py`）
- 知识推断系统提示词（`app/adapters/ai.py`）
- MCP 服务器指令（`app/mcp_server.py`）

这些后续会逐步搬到 overlay 体系。

---

## 邮件配置

OpenLucid 用邮件做密码重置（可选还有 in-app 反馈）。**未配置时**，
忘记密码流程会**快速失败并给出明确错误**，而不是悄悄把链接落到容器日志里。

在 `docker/.env` 配二选一：

**Resend（HTTP API，最简单）**：

```
RESEND_API_KEY=re_xxxxxxxxxxxxxxxxxxxx
MAIL_FROM=OpenLucid <noreply@your-domain.com>
```

**SMTP（任意提供商）**：

```
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@example.com
SMTP_PASSWORD=...
MAIL_FROM=OpenLucid <noreply@example.com>
```

然后 `docker compose up -d --force-recreate app` 重启读 env。
Mail provider 自动识别 —— 只有**两种都配**且想强制走某一个时才设
`MAIL_TYPE=resend` 或 `MAIL_TYPE=smtp`。

---

## 健康检查

`/health` 端点（无鉴权）用于 docker liveness probe，返回 DB 连接状态：

```bash
$ curl -s http://localhost/health
{"status": "ok", "version": "1.3.7", "database": "connected"}
```

`docker/docker-compose.yml` 已配 healthcheck 调这个端点，所以 `docker compose ps`
显示 `healthy` 是真的能服务流量了，不只是进程起来了。

---

## 升级

数据库迁移在容器启动时**自动执行**（`app/main.py` 的 lifespan 钩子里有
`alembic upgrade head`）。多数升级只需要：

```bash
cd docker
git pull
docker compose up -d --build
```

新镜像起来 → lifespan 跑迁移 → app 就绪。**没有单独的 migrate 命令**要记。

迁移失败时容器会停住，错误进 `docker compose logs app`；修了底下的问题再 up 一次即可。

---

## 备份与恢复

> 待补 —— `pg_dump` 备份方案 + 素材目录备份。
> 暂时用标准 Docker volume 备份工具备份 `docker_db_data` + `docker_uploads` 两个 volume 即可。

---

## 反馈问题

碰到本指南没覆盖的自部署场景，或者上面哪一条不灵，请到
<https://github.com/agidesigner/OpenLucid/issues> 开 issue，附上：

- 操作系统 / Docker 版本
- `docker compose ps` 输出
- `docker compose logs app` 相关行
