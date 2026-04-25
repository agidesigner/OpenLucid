<h1 align="center">🧭 OpenLucid —— 给 AI 用的"营销世界模型"</h1>

<p align="center"><a href="README.md">English</a> | <b>中文</b></p>

<p align="center">
  <a href="https://github.com/agidesigner/OpenLucid/stargazers"><img src="https://img.shields.io/github/stars/agidesigner/OpenLucid.svg?style=flat&color=FFD700" alt="Stargazers"></a>
  <a href="https://github.com/agidesigner/OpenLucid/issues"><img src="https://img.shields.io/github/issues/agidesigner/OpenLucid.svg" alt="Issues"></a>
  <a href="https://github.com/agidesigner/OpenLucid/network/members"><img src="https://img.shields.io/github/forks/agidesigner/OpenLucid.svg" alt="Forks"></a>
  <a href="https://github.com/agidesigner/OpenLucid/blob/main/LICENSE"><img src="https://img.shields.io/github/license/agidesigner/OpenLucid.svg" alt="License"></a>
  <a href="https://x.com/ajinpro"><img src="https://img.shields.io/badge/𝕏-@ajinpro-000000?style=flat" alt="X / Twitter"></a>
</p>

<p align="center">
  <b>把营销数据结构化，AI 才找得到、看得懂、用得起来。</b>
</p>

<br/>

大多数营销工具的数据存储方式 —— 一段段散落在 10 个 SaaS 里的文字、躺在云盘里的原始素材、藏在 PDF 里的品牌规范 —— 让这些数据**对 AI 不可见**。OpenLucid 是一个自托管的中间层，把这些信息全部归集、结构化，再通过三种一等公民接口暴露：**MCP 给 AI Agent，REST API 给自动化，Web UI 给营销团队。**

同一份品牌知识，既能驱动 Web UI，也能直接被 Claude Code、Cursor、OpenClaw 和你自己的 Agent 调用 —— 不需要任何复制粘贴。

<br/>

> ⭐ **如果 OpenLucid 对你有帮助，请点个 Star 支持** —— 这对项目成长真的很重要。

<br/>

## ✨ OpenLucid 的核心价值

- 🧠 **AI 可读为先** —— 知识、人群、场景、品牌规范、素材，每个字段都是结构化、可打分、可打标的，而不是原始文件 + 自由文本。
- 🔌 **MCP 优先** —— 外部 Agent 才是大脑。OpenLucid 提供提示词纪律和数据层；Claude / GPT / 你自己的 LLM 始终掌控推理。
- 🛡️ **规则服务端兜底** —— 像 merchant 默认值、字段冲突这种约束，靠服务端强制，而不是寄希望于 Agent 看了说明就照做。
- ⚙️ **自托管 + 单租户** —— 你的数据、你的数据库、你的模型 Key。两个容器搞定，不需要 Redis、消息队列。
- 🌐 **三套接口、一份事实** —— Web UI、REST API、MCP、CLI。任何一个入口写入，其他几个立刻可见。

<br/>

## 🧩 核心模块

| 模块 | 做什么 |
|---|---|
| 🧠 **知识库** | 结构化的商家知识：卖点、人群洞察、使用场景、FAQ、异议处理。手动录入或让 AI 推理。|
| 📁 **素材库** | 上传图片、视频、文档，AI 自动提取元数据、打标、评分。支持按内容形态（开箱/测评/教程）和活动类型（限时秒杀/买一赠一）筛选。|
| 🎯 **策略单元** | 定义"人群 × 场景 × 营销目标 × 渠道"组合，从宽泛知识聚焦到具体内容方向。|
| 🎨 **品牌套件** | Logo、品牌调性、配色、字体、人设预设 —— 确保所有产出不偏离品牌。|
| 💡 **选题工作室** | 基于知识库 + 素材库，生成多平台选题方案（标题、开头钩子、要点、推荐素材）。|
| ✍️ **脚本 / 图文生成** | 平台 × 人设 × 结构预设，一键产出短视频口播或社媒文案，可直接喂入选题。|
| 🎬 **视频生成** | 选题 → 脚本 → 数字人视频（蝉镜 / Jogg：Avatar + Voice + 比例 + 可选 B-roll + 字幕）。|
| 📚 **创作库** | 每一版完整产出（文案 / 脚本 / 邮件 / 钩子）自动归档，方便复用、对比、供 Agent 引用。|
| ❓ **知识问答** | 基于知识库的 AI 问答，**引用来源、不编造**。|

<br/>

## 🔌 接入你的 Agent

三种接口层，按需选择 —— 它们读写的是同一份数据：

| 接口 | 适用场景 | 接入方式 |
|------|---------|---------|
| **MCP Server** | Claude Code、Cursor、OpenClaw、AI IDE | 通过 MCP 协议连接，AI 直接读取营销数据 |
| **RESTful API** | 自定义 Agent、自动化流程 | 完整 API，交互式文档见 `/docs` |
| **CLI 工具** | Agent 脚本、运维查询 | 通过 HTTP 调 REST 的独立 Python 脚本，零项目依赖 |
| **Web UI** | 营销团队日常使用 | 可视化界面，管理知识、素材、品牌套件、选题 |

<br/>

## 🚀 快速开始

**前置条件：** 已安装并启动 [Docker](https://docs.docker.com/get-docker/) 和 Docker Compose。

```bash
git clone https://github.com/agidesigner/OpenLucid.git
cd OpenLucid/docker
cp .env.example .env
docker compose up -d
```

启动后打开 **http://localhost** ：

1. 首次访问会进入 Setup 页 —— 创建管理员账号
2. 进入 **Settings**，配置你的 LLM（任何 OpenAI 兼容 API 都行）
3. 创建第一个商品 / Offer，开始策划
4. （可选）安装 CLI，让 AI Agent 能查询营销数据：
   ```bash
   bash tools/install.sh
   openlucid setup
   ```

> 💡 只有 2 个容器（PostgreSQL + App）。无 Redis、无消息队列、无额外依赖。

<br/>

## 🤖 让 Agent 用起来

OpenLucid 是 MCP-first 设计。运行 `bash tools/install.sh` 后，OpenLucid skill 会注册到全局 skill 目录，任何项目目录下的 Agent 都能自动发现：

| Agent | Skill 安装位置 |
|-------|---------------|
| Claude Code | `~/.claude/skills/openlucid/SKILL.md` |
| Cursor | `~/.cursor/skills/openlucid/SKILL.md` |
| Codex / OpenHands | `~/.agents/skills/openlucid/SKILL.md` |

之后你可以这样让 Agent 工作：

- *"列出我所有的商家和商品"*
- *"把这个产品页 URL 导入 OpenLucid，AI 推断知识库"*
- *"PrivacyCrop 的核心卖点有哪些？"*
- *"用 PrivacyCrop 的品牌调性，写一篇小红书文案"*

Agent 通过 OpenLucid MCP 读结构化数据，调你自己的 LLM 推理，再用 `add_knowledge_item` / `save_creation` 写回 —— 数据始终在你自己手里。

<br/>

## 🛠️ 常用命令

在 `docker/` 目录下运行：

```bash
docker compose up -d        # 启动
docker compose down         # 停止
docker compose restart      # 重启
docker compose logs -f app  # 查看日志
docker compose ps           # 查看状态
```

<br/>

## 🔧 配置

所有设置在 `docker/.env`（模板：`docker/.env.example`）：

| 变量 | 默认值 | 说明 |
|------|-------|------|
| `DB_USER` | openlucid | 数据库用户名 |
| `DB_PASSWORD` | openlucid | 数据库密码（**生产环境务必修改！**） |
| `DB_NAME` | openlucid | 数据库名 |
| `APP_PORT` | 80 | 主机暴露端口 |
| `SECRET_KEY` | change-me-in-production | JWT 密钥（**生产环境务必修改！**） |
| `LOG_LEVEL` | INFO | 日志级别 |

LLM 配置在 Web UI 的 Settings → LLM 中管理 —— 支持多模型、场景路由、可视化切换。

<br/>

## ❓ 常见问题

**Q：为什么叫"营销世界模型"？**
新一代营销应用需要关于商品、品牌调性、人群、素材的结构化"事实底座"才能合理推理。OpenLucid 就是这一层 —— 设计目标是被 AI Agent 查询和更新，不仅仅给人用。

**Q：必须用某个特定 LLM 吗？**
不用。OpenLucid 走的是 OpenAI Chat Completions 协议，任何兼容它的 LLM 都行（Claude、GPT、DeepSeek、Qwen、Gemini、Ollama、自己部署的）。在 Web UI 里配模型 + Key 即可。

**Q：除了 Claude Code，其他 MCP 客户端能用吗？**
能。任何说 MCP 的客户端（Cursor、OpenClaw、自定义客户端）都通过同一个 `/mcp` 端点连接，工具列表、提示词、资源完全一致。

**Q：数据怎么存？**
PostgreSQL 16，跑在你自己的容器里。无 SaaS、无第三方向量数据库、无遥测上报。素材文件落在主机磁盘的 `uploads/` 下。

**Q：能离线 / VPN 内运行吗？**
能 —— 依赖装好后，唯一的对外流量只有你配置的 LLM 端点（如果用了视频生成功能，还会到蝉镜 / Jogg）。

<br/>

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 后端 | Python 3.11 · FastAPI · SQLAlchemy 2.0（async）· Alembic |
| 数据库 | PostgreSQL 16 |
| 前端 | HTML · Tailwind CSS · Alpine.js（无构建步骤） |
| AI | OpenAI SDK（兼容任何 OpenAI 格式 LLM API） |
| 部署 | Docker Compose |

<br/>

## 💬 加入交流

扫码加我微信，**备注「OpenLucid」**，一起交流营销 AI、自托管 Agent 实践、和这个项目的更新：

<p align="center">
  <img src="assets/wechat.png" alt="加 Ajin 微信交流（备注 OpenLucid）" width="280" />
</p>

或者通过邮箱 **ajin@jogg.ai** 联系（合作 / 商业咨询）。

<br/>

## 📢 反馈与支持

- 🐛 **遇到问题** → [提交 Issue](https://github.com/agidesigner/OpenLucid/issues)
- 💡 **功能建议** → [提交 Feature Request](https://github.com/agidesigner/OpenLucid/issues)
- ⭐ **觉得有用** → **[给个 Star](https://github.com/agidesigner/OpenLucid)**，让更多人看到！

<br/>

## 📝 License

本项目采用基于 [Apache License 2.0](LICENSE) 修订的协议（增加了多租户使用与品牌相关条款），详情请见 [LICENSE](LICENSE)。

<br/>

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=agidesigner/OpenLucid&type=Date)](https://star-history.com/#agidesigner/OpenLucid&Date)
