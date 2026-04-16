[English](README.md) | [中文](README_zh.md)

# OpenLucid

**Marketing World Model** — structure your data so AI can find it, understand it, and put it to work.

---

### What it is

A structured data layer for everything that drives your marketing — products, services, brand rules, audiences, selling points, and assets — so AI can actually reason about your business.

### What it solves

Your marketing data becomes AI-ready:

- **Discoverable** — knowledge, assets, and brand rules live in one place, not across 10 tools
- **Machine-readable** — structured, tagged, and scored, not raw files and free text
- **Actionable** — ready for agents, content generation, and downstream workflows

### How to plug in

Three interfaces, pick what fits:

| Interface | For | How |
|-----------|-----|-----|
| **MCP Server (SSE)** | Claude Code, Cursor, AI IDEs (remote/Docker) | Connect via HTTP SSE, AI reads your marketing data directly |
| **RESTful API** | Custom agents, automation | Full API with interactive docs at `/docs` |
| **CLI Tool** | Agent scripting, ops queries | Command-line tool that calls the REST API, zero dependencies |
| **Web App** | Marketing teams | Visual UI for managing knowledge, assets, brand kits, and topics |

---

## Core Modules

- **Knowledge Base** — Structured merchant knowledge: selling points, audience insights, usage scenarios, FAQs, objection handling. Input manually or let AI infer from product data
- **Asset Library** — Upload images, videos, documents. AI auto-extracts metadata, tags, and scores each asset
- **Strategy Units** — Define "audience × scenario × marketing goal × channel" combinations for focused content direction
- **Brand Kit** — Brand tone, visual guidelines, persona definitions. Guardrails that keep all output on-brand
- **Topic Studio** — Generate multi-platform topic plans grounded in your knowledge base and asset library
- **KB Q&A** — AI-powered Q&A that cites your knowledge base without fabricating

## Quick Start

**Prerequisites:** [Docker](https://docs.docker.com/get-docker/) and Docker Compose installed and running.

```bash
git clone https://github.com/agidesigner/OpenLucid.git
cd OpenLucid/docker
cp .env.example .env
docker compose up -d
```

Once started, open **http://localhost**:

1. First visit lands on the setup page — create your admin account
2. Go to **Settings** to configure your LLM (any OpenAI-compatible API)
3. Create your first product and start planning
4. (Optional) Install the CLI tool so AI agents can query your marketing data:
   ```bash
   bash tools/install.sh
   openlucid-cli setup
   ```

> Only 2 containers (PostgreSQL + App). No Redis, no message queue, no extra dependencies.

## Common Commands

Run from `docker/` directory:

```bash
docker compose up -d        # Start
docker compose down          # Stop
docker compose restart       # Restart
docker compose logs -f app   # View logs
docker compose ps            # Check status
```

## Upgrade

```bash
cd OpenLucid
git pull origin main
cd docker
docker compose up -d --build
```

Database migrations run automatically on app startup — no manual steps needed.

If `.env.example` has new variables after upgrading, add them to your `.env` manually.

> **Warning:** Never use `docker compose down -v` — the `-v` flag deletes all data. If the app fails to start after upgrading, check logs first: `docker compose logs app`.

## Configuration

All settings are managed in `docker/.env` (template: `docker/.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_USER` | openlucid | Database username |
| `DB_PASSWORD` | openlucid | Database password (**change in production!**) |
| `DB_NAME` | openlucid | Database name |
| `APP_PORT` | 80 | Port exposed on host |
| `SECRET_KEY` | change-me-in-production | JWT secret (**change in production!**) |
| `LOG_LEVEL` | INFO | Log level |

**LLM configuration is managed in the web UI (Settings page)**, not in .env — supports multiple models, scene-based routing, and visual configuration.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Alembic |
| Database | PostgreSQL 16 |
| Frontend | HTML · Tailwind CSS · Alpine.js (no build step) |
| AI | OpenAI SDK (compatible with any OpenAI-format LLM API) |
| Deployment | Docker Compose |

## Project Structure

```
app/                    # Backend
├── api/                #   API routes
├── application/        #   Business logic
├── adapters/           #   External service adapters (AI, storage)
├── models/             #   Data models
├── schemas/            #   Pydantic schemas
├── apps/definitions/   #   App definitions (Topic Studio, KB Q&A, etc.)
└── config.py           #   Configuration

frontend/               # Frontend (static HTML, served by FastAPI StaticFiles)

docker/                 # Production deployment
├── docker-compose.yml  #   Production compose
└── .env.example        #   Config template

docker-compose.yml      # Development (source mount + hot reload)
Dockerfile              # Image build
```

## Local Development

```bash
# From project root (not docker/), uses docker-compose.yml with source mount + hot reload
docker compose up -d
```

Or without Docker:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # Edit DATABASE_URL to point to your PostgreSQL
uvicorn app.main:app --reload
```

API docs: http://localhost:8000/docs

### CLI tool (HTTP-based)

`tools/openlucid-cli` is a standalone Python script that calls the REST API over HTTP. No project dependencies required — ideal for agent scripting or ops queries.

```bash
# One-line install (copies to ~/.local/bin, auto-adds to PATH)
bash tools/install.sh

# First-time setup (interactive: URL + login + verify)
openlucid-cli setup

# Use
openlucid-cli list-merchants
openlucid-cli list-offers --merchant-id <id>
openlucid-cli offer-context --id <offer_id>
openlucid-cli extract-text --url "https://example.com/product-page"
openlucid-cli create-offer --merchant-id <id> --name "Product name"
openlucid-cli create-offer-from-url --merchant-id <id> --name "Product name" --url "https://example.com/product-page"  # extract + AI infer + create offer + save knowledge
openlucid-cli kb-qa --offer-id <id> --question "What are the key selling points?"
openlucid-cli topic-studio --offer-id <id>
```

Two authentication methods:
- **Cookie auth**: `openlucid-cli login` (session-based, expires in 168h)
- **API Token auth**: Create a token in Web UI Settings > MCP > Access Tokens, then store in `~/.openlucid.json` (long-lived, recommended for agents)

Config priority: `--url` flag > `OPENLUCID_URL` env > `~/.openlucid.json` > `http://localhost`  
Optional: `OPENLUCID_TOKEN` for Bearer auth (same token as in `~/.openlucid.json`).

Run `openlucid-cli --help` for all available subcommands.

### Let AI Agents Use CLI from Any Directory

`install.sh` automatically installs the OpenLucid skill into global skill directories so AI agents discover `openlucid-cli` regardless of the current working directory:

| Agent | Skill installed |
|-------|-----------------|
| Claude Code | `~/.claude/skills/openlucid-cli/SKILL.md` |
| Cursor | `~/.cursor/skills/openlucid-cli/SKILL.md` |
| Codex / OpenHands | `~/.agents/skills/openlucid-cli/SKILL.md` |

The single source of truth lives in the repository at `skills/openlucid-cli/SKILL.md`.

After installation, you can open Claude Code / Cursor / Codex in **any project directory** and ask your agent to work with marketing data — the installed skill will guide it to use `openlucid-cli`.

Example prompts:
- "List all merchants and offers for my workspace"
- "What are the core selling points for this product?"
- "Import this product URL into OpenLucid and create the offer"

## License

OpenLucid is available under a modified [Apache License 2.0](LICENSE) with additional conditions for multi-tenant use and branding. See [LICENSE](LICENSE) for details.

## Contact

For questions, suggestions, or partnership inquiries, reach out to us at **ajin@jogg.ai**.
