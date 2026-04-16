[English](README.md) | [中文](README_zh.md)

# OpenLucid

**营销世界模型** — AI 找得到、看得懂、用得起来你的数据。

---

### 是什么？

面向商家的营销世界模型。商品、服务、品牌规范、受众、卖点、素材 — 整理成一个 AI 能推理的结构化数据层。

### 解决什么？

让营销数据真正能被 AI 使用：

- **找得到** — 知识、素材、品牌规范集中在一个地方，而不是分散在 10 个工具里
- **看得懂** — 结构化、打标签、有评分，而不是原始文件和自由文本
- **用得起来** — 随时可供 Agent、内容生成、下游工作流调用

### 怎么接入？

三种接口层，按需选择：

| 接口 | 适用场景 | 方式 |
|------|---------|------|
| **MCP Server (SSE)** | Claude Code、Cursor、AI IDE（远程/Docker） | 通过 HTTP SSE 连接，AI 直接读取营销数据 |
| **RESTful API** | 自定义 Agent、自动化流程 | 完整 API，交互式文档见 `/docs` |
| **CLI 工具** | Agent 脚本调用、运维查询 | 通过 HTTP 调用 REST API 的命令行工具，零依赖 |
| **Web App** | 营销团队日常使用 | 可视化界面，管理知识、素材、品牌套件、选题 |

---

## 核心模块

- **知识库** — 结构化的商家知识：卖点、受众洞察、使用场景、FAQ、异议处理。手动录入或让 AI 从商品数据中推理
- **素材库** — 上传图片、视频、文档，AI 自动提取元数据、智能打标、评分
- **策略单元** — 定义"人群 × 场景 × 营销目标 × 渠道"组合，从宽泛知识聚焦到具体内容方向
- **品牌套件** — 品牌调性、视觉规范、人设定义。确保所有产出不偏离品牌
- **选题工作室** — 基于知识库 + 素材库，生成多平台选题方案（标题、开头钩子、要点、推荐素材）
- **知识问答** — 基于知识库的 AI 问答，引用来源、不编造

## 快速开始

**前置条件：** 已安装并启动 [Docker](https://docs.docker.com/get-docker/) 和 Docker Compose。

```bash
git clone https://github.com/agidesigner/OpenLucid.git
cd OpenLucid/docker
cp .env.example .env
docker compose up -d
```

启动完成后，打开 **http://localhost**：

1. 首次访问进入安装页面，创建管理员账号
2. 进入「设置」页面，配置 LLM（支持任意 OpenAI 兼容 API）
3. 创建第一个商品，开始使用
4. （可选）安装 CLI 工具，让 AI Agent 查询营销数据：
   ```bash
   bash tools/install.sh
   openlucid-cli setup
   ```

> 仅需 2 个容器（PostgreSQL + App），无需 Redis、消息队列等额外依赖。

## 常用命令

在 `docker/` 目录下执行：

```bash
docker compose up -d        # 启动
docker compose down          # 停止
docker compose restart       # 重启
docker compose logs -f app   # 查看日志
docker compose ps            # 查看状态
```

## 升级

```bash
cd OpenLucid
git pull origin main
cd docker
docker compose up -d --build
```

数据库迁移在应用启动时自动执行，无需手动操作。

如果升级后 `.env.example` 有新增变量，请手动添加到你的 `.env` 中。

> **警告：** 切勿使用 `docker compose down -v` — `-v` 参数会删除所有数据。升级后如果页面无法访问，请先查看日志：`docker compose logs app`。

## 配置说明

所有配置项在 `docker/.env` 中管理，模板见 `docker/.env.example`：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DB_USER` | openlucid | 数据库用户名 |
| `DB_PASSWORD` | openlucid | 数据库密码（生产环境务必修改） |
| `DB_NAME` | openlucid | 数据库名 |
| `APP_PORT` | 80 | 对外暴露端口 |
| `SECRET_KEY` | change-me-in-production | JWT 密钥（生产环境务必修改） |
| `LOG_LEVEL` | INFO | 日志级别 |

**LLM 配置在 Web UI 的「设置」页面管理**，不在 .env 文件中 — 支持多模型、多场景路由，可视化配置更直观。

## 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic |
| 数据库 | PostgreSQL 16 |
| 前端 | HTML · Tailwind CSS · Alpine.js（无构建步骤） |
| AI 集成 | OpenAI SDK（兼容任意 OpenAI API 格式的大模型） |
| 部署 | Docker Compose |

## 项目结构

```
app/                    # 后端代码
├── api/                #   API 路由
├── application/        #   业务逻辑
├── adapters/           #   外部服务适配器（AI、存储）
├── models/             #   数据模型
├── schemas/            #   Pydantic 校验模型
├── apps/definitions/   #   应用定义（选题工作室、知识问答等）
└── config.py           #   配置

frontend/               # 前端页面（纯静态，FastAPI StaticFiles 托管）

docker/                 # 生产部署
├── docker-compose.yml  #   生产编排
└── .env.example        #   配置模板

docker-compose.yml      # 开发用（挂载源码 + 热更新）
Dockerfile              # 镜像构建
```

## 本地开发

```bash
# 在项目根目录（不是 docker/）执行，使用根目录的 docker-compose.yml（挂载源码 + 热更新）
docker compose up -d
```

或不使用 Docker：

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 编辑 DATABASE_URL 指向本地 PostgreSQL
uvicorn app.main:app --reload
```

API 文档：http://localhost:8000/docs

### CLI 工具（通过 HTTP 调用 REST API）

`tools/openlucid-cli` 是一个独立的 Python 脚本，通过 HTTP 调用 REST API，无需安装项目依赖。适合 Agent 脚本调用或运维查询。

```bash
# 一键安装（复制到 ~/.local/bin，自动加入 PATH）
bash tools/install.sh

# 首次配置（交互式引导：输入 URL、登录、验证连通）
openlucid-cli setup

# 使用
openlucid-cli list-merchants
openlucid-cli list-offers --merchant-id <id>
openlucid-cli offer-context --id <offer_id>
openlucid-cli extract-text --url "https://example.com/product-page"
openlucid-cli create-offer --merchant-id <id> --name "商品名"
openlucid-cli create-offer-from-url --merchant-id <id> --name "商品名" --url "https://example.com/product-page"  # 提取文本 + AI 推断 + 创建商品 + 保存知识
openlucid-cli kb-qa --offer-id <id> --question "产品有哪些卖点？"
openlucid-cli topic-studio --offer-id <id>
```

支持两种认证方式：
- **Cookie 认证**：`openlucid-cli login`（会话制，168h 过期）
- **API Token 认证**：在 Web 设置页 > MCP > 访问令牌 创建 token，写入 `~/.openlucid.json`（长期有效，推荐 Agent 使用）

配置优先级：`--url` 参数 > 环境变量 `OPENLUCID_URL` > `~/.openlucid.json` > 默认 `http://localhost`  
可选：环境变量 `OPENLUCID_TOKEN` 用于 Bearer 认证（与 `~/.openlucid.json` 中的 token 一致）。

运行 `openlucid-cli --help` 查看全部子命令。

### 让 AI Agent 在任意目录使用 CLI

`install.sh` 会自动把 OpenLucid skill 安装到全局 skills 目录，这样 AI Agent **在任何项目目录下**都能发现并使用 `openlucid-cli`：

| Agent | 自动安装的 skill |
|-------|------------------|
| Claude Code | `~/.claude/skills/openlucid-cli/SKILL.md` |
| Cursor | `~/.cursor/skills/openlucid-cli/SKILL.md` |
| Codex / OpenHands | `~/.agents/skills/openlucid-cli/SKILL.md` |

项目中的唯一说明源文件位于 `skills/openlucid-cli/SKILL.md`。

安装完成后，你可以在任何项目目录打开 Claude Code / Cursor / Codex，直接用自然语言指示 Agent 操作营销数据，例如：
- "帮我查看所有商户和产品"
- "这个产品的核心卖点有哪些？"
- "为这个商品生成 5 个选题"

Agent 会自动调用 `openlucid-cli` 完成操作。

## 许可证

OpenLucid 采用修改版 [Apache License 2.0](LICENSE)，对多租户使用和品牌标识有附加条件。详见 [LICENSE](LICENSE)。

## 联系我们

如有问题、建议或合作意向，请联系 **ajin@jogg.ai**。
