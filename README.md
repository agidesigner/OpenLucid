<h1 align="center">🧭 OpenLucid — Marketing World Model for AI</h1>

<p align="center"><b>English</b> | <a href="README_zh.md">中文</a></p>

<p align="center">
  <a href="https://github.com/agidesigner/OpenLucid/stargazers"><img src="https://img.shields.io/github/stars/agidesigner/OpenLucid.svg?style=flat&color=FFD700" alt="Stargazers"></a>
  <a href="https://github.com/agidesigner/OpenLucid/issues"><img src="https://img.shields.io/github/issues/agidesigner/OpenLucid.svg" alt="Issues"></a>
  <a href="https://github.com/agidesigner/OpenLucid/network/members"><img src="https://img.shields.io/github/forks/agidesigner/OpenLucid.svg" alt="Forks"></a>
  <a href="https://github.com/agidesigner/OpenLucid/blob/main/LICENSE"><img src="https://img.shields.io/github/license/agidesigner/OpenLucid.svg" alt="License"></a>
  <a href="https://x.com/ajinpro"><img src="https://img.shields.io/badge/𝕏-@ajinpro-000000?style=flat" alt="X / Twitter"></a>
</p>

<p align="center">
  <b>Structure your marketing data so AI can find it, understand it, and put it to work.</b>
</p>

<br/>

The way most marketing tools store data — fragments of text in 10 different SaaS products, raw files in cloud drives, brand rules buried in PDFs — makes them **invisible to AI**. OpenLucid is a self-hosted layer that pulls all of it together, structures it, and exposes it through three first-class interfaces: **MCP for AI agents, REST API for automation, and a Web UI for marketing teams.**

The same brand knowledge that powers your Web UI directly powers Claude Code, Cursor, OpenClaw, and any custom agent — without copy-paste.

<br/>

> ⭐ **If OpenLucid helps you, please give us a star** — it really helps the project grow.

<br/>

## ✨ Why OpenLucid

- 🧠 **AI-readable by design** — Knowledge, audiences, scenarios, brand rules, assets — every field is structured, scored, and tagged. Not raw files and free text.
- 🔌 **MCP-first** — External agents are the brain. OpenLucid contributes the prompt discipline + the data layer; Claude / GPT / your-own-LLM stays in charge of reasoning.
- 🛡️ **Server-enforced rules** — Things like merchant defaulting and source-of-truth conflicts are guarded server-side, not by hoping the agent reads instructions.
- ⚙️ **Self-hosted, single-tenant** — Your data, your DB, your model keys. Two containers, no Redis, no message queue.
- 🌐 **Three interfaces, one source of truth** — Web UI, REST API, MCP, and a CLI. Add an offer in any of them — the others see it instantly.

<br/>

## 🧩 Core Modules

| Module | What it does |
|---|---|
| 🧠 **Knowledge Base** | Structured merchant knowledge: selling points, audience insights, usage scenarios, FAQs, objection handling. Manual or AI-inferred. |
| 📁 **Asset Library** | Upload images, videos, documents. AI auto-extracts metadata, tags, scores. Filter by content-form (unboxing/review/tutorial) or campaign-type (flash sale/BOGO). |
| 🎯 **Strategy Units** | Define `audience × scenario × goal × channel` combos to focus content direction. |
| 🎨 **Brand Kit** | Logo, brand voice, colors, fonts, persona presets — guardrails that keep all output on-brand. |
| 💡 **Topic Studio** | Generate multi-platform topic plans grounded in your KB and assets. |
| ✍️ **Script Writer / Content Studio** | Produce short-video scripts or social copy with platform × persona × structure presets. |
| 🎬 **Video Generation** | Turn a script into a digital-human video via Chanjing / Jogg (avatar + voice + aspect + B-roll + captions). |
| 📚 **Creations Library** | Every finished piece (post / script / email / hook) auto-saved for reuse, comparison, and agent referral. |
| ❓ **KB Q&A** | AI-powered Q&A that **cites your knowledge base** without fabricating. |

<br/>

## 🔌 Plug In Your Agent

Three interfaces, pick what fits — they all read and write the same data:

| Interface | For | How |
|-----------|-----|-----|
| **MCP Server** | Claude Code, Cursor, OpenClaw, AI IDEs | Connect via MCP protocol; AI reads your marketing data directly |
| **RESTful API** | Custom agents, automation | Full API with interactive docs at `/docs` |
| **CLI Tool** | Agent scripting, ops queries | Standalone Python script over HTTP, zero project deps |
| **Web UI** | Marketing teams | Visual UI for managing knowledge, assets, brand kits, topics |

<br/>

## 🚀 Quick Start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed and running.

```bash
git clone https://github.com/agidesigner/OpenLucid.git
cd OpenLucid/docker
cp .env.example .env
docker compose up -d
```

Once started, open **http://localhost** :

1. First visit lands on the setup page — create your admin account
2. Go to **Settings** to configure your LLM (any OpenAI-compatible API)
3. Create your first product / offer and start planning
4. (Optional) Install the CLI so AI agents can query your marketing data:
   ```bash
   bash tools/install.sh
   openlucid setup
   ```

> 💡 Only 2 containers (PostgreSQL + App). No Redis, no message queue, no extra dependencies.

<br/>

## 🤖 Connecting AI Agents

OpenLucid is built MCP-first. After running `bash tools/install.sh`, the OpenLucid skill is registered globally so any agent in any project directory discovers it automatically:

| Agent | Skill installed at |
|-------|--------------------|
| Claude Code | `~/.claude/skills/openlucid/SKILL.md` |
| Cursor | `~/.cursor/skills/openlucid/SKILL.md` |
| Codex / OpenHands | `~/.agents/skills/openlucid/SKILL.md` |

Now ask your agent things like:

- *"List all merchants and offers for my workspace"*
- *"Import this product URL into OpenLucid and infer the knowledge base"*
- *"What are the key selling points of [product]?"*
- *"Draft a Xiaohongshu post for [product] using its brand voice"*

The agent uses the OpenLucid MCP to read structured data, calls the LLM to reason, and writes the result back via `add_knowledge_item` / `save_creation` — all under your data sovereignty.

<br/>

## 🛠️ Common Commands

Run from the `docker/` directory:

```bash
docker compose up -d        # Start
docker compose down          # Stop
docker compose restart       # Restart
docker compose logs -f app   # View logs
docker compose ps            # Check status
```

<br/>

## 🔧 Configuration

All settings are managed in `docker/.env` (template at `docker/.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_USER` | openlucid | Database username |
| `DB_PASSWORD` | openlucid | Database password (**change in production!**) |
| `DB_NAME` | openlucid | Database name |
| `APP_PORT` | 80 | Port exposed on host |
| `SECRET_KEY` | change-me-in-production | JWT secret (**change in production!**) |
| `LOG_LEVEL` | INFO | Log level |

LLM configuration lives in the Web UI (Settings → LLM) — supports multiple models, scene-based routing, visual switching.

<br/>

## ❓ FAQ

**Q: Why "Marketing World Model"?**
A new marketing app needs structured ground-truth about products, brand voice, audiences, and assets to reason well. OpenLucid is that ground-truth layer — designed to be queried and updated by AI agents, not just humans.

**Q: Do I need a specific LLM?**
No. OpenLucid speaks the OpenAI Chat Completions API; any compatible LLM works (Claude, GPT, DeepSeek, Qwen, Gemini, Ollama, your own). You configure model + key in the Web UI.

**Q: Does it talk to MCP clients other than Claude Code?**
Yes. Anything that speaks MCP (Cursor, OpenClaw, custom clients) connects via the same `/mcp` endpoint. Tool list, prompts, and resources are identical across clients.

**Q: How is data stored?**
PostgreSQL 16 in your own container. No SaaS, no third-party vector DB, no telemetry. Asset files live on the host disk under `uploads/`.

**Q: Can I run it offline / behind a VPN?**
Yes — once dependencies are installed, the only outbound traffic is to your configured LLM endpoint (and optional video provider Chanjing/Jogg if you use Video Generation).

**Q: I forgot my admin password / lost access to my email / need to provision the first admin from a script.**
See the [Self-Hosting Operations Guide](SELF_HOSTING.md#account-recovery) — there's a direct-DB CLI (`docker compose exec app python -m app.cli ...`) that bypasses email and HTTP entirely for these recovery cases.

<br/>

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic |
| Database | PostgreSQL 16 |
| Frontend | HTML · Tailwind CSS · Alpine.js (no build step) |
| AI | OpenAI SDK (compatible with any OpenAI-format LLM API) |
| Deployment | Docker Compose |

<br/>

## 💬 Community

Find me on **𝕏 / Twitter** — I share notes on building OpenLucid, marketing AI patterns, and self-hosted agent setups:

👉 **[https://x.com/ajinpro](https://x.com/ajinpro)**

Or reach out at **ajin@jogg.ai** for partnership / commercial inquiries.

<br/>

## 📢 Feedback & Support

- 🐛 **Found a bug** → [open an Issue](https://github.com/agidesigner/OpenLucid/issues)
- 💡 **Feature idea** → [open a discussion or feature request](https://github.com/agidesigner/OpenLucid/issues)
- ⭐ **Liked it?** → **[Star this repo](https://github.com/agidesigner/OpenLucid)** so more people find it!

<br/>

## 📝 License

OpenLucid is available under a modified [Apache License 2.0](LICENSE) with additional conditions for multi-tenant use and branding. See [LICENSE](LICENSE) for details.

<br/>

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/svg?repos=agidesigner/OpenLucid&type=Date)](https://star-history.com/#agidesigner/OpenLucid&Date)
