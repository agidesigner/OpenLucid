---
name: openlucid-cli
description: Use this skill whenever the user wants to query, operate, or create marketing data — products, merchants, selling points, audiences, knowledge base, assets, topics, or content creations. Also use when the user mentions OpenLucid, openlucid-cli, marketing data, 营销数据, 商品, 商户, 选题, 知识库, or 素材.
---

# OpenLucid CLI — AI Agent Guide

`openlucid-cli` is a standalone command-line tool that queries the OpenLucid REST API over HTTP. All output is JSON. It uses only Python standard library — no pip install needed.

## Command Reference

| Command | Purpose |
|---------|---------|
| `list-merchants` | List all merchants |
| `list-offers --merchant-id <id>` | List products/services under a merchant |
| `get-merchant --id <id>` | Get a single merchant by UUID |
| `get-offer --id <id>` | Get a single product by UUID |
| `offer-context --id <id>` | Full product context (selling points, audiences, knowledge, assets) |
| `extract-text --url "..."` | Extract page text from a product URL |
| `create-offer --merchant-id <id> --name "..."` | Create a new product/service offer |
| `create-offer-from-url --merchant-id <id> --name "..." --url "..."` | Extract URL text, infer knowledge, create an offer, and save inferred knowledge |
| `kb-qa --offer-id <id> --question "..."` | Answer questions from knowledge base |
| `topic-studio --offer-id <id>` | Generate NEW topic plans |
| `list-topic-plans --offer-id <id>` | List topic plans from Topic Studio (选题工坊历史选题) |
| `list-creations --offer-id <id>` | List SAVED creations (manually saved content pieces) |
| `get-creation --id <id>` | Get a single creation by UUID |
| `save-creation --title "..." --content "..."` | Save a content piece |
| `search-assets --scope-type offer --scope-id <id>` | Search images, videos, documents |
| `list-knowledge --scope-type offer --scope-id <id>` | List structured knowledge (selling points, audiences, FAQ) |
| `add-knowledge --scope-type offer --scope-id <id> --title "..."` | Add a knowledge item |
| `list-strategy-units --offer-id <id>` | List marketing strategy units |
| `list-apps` | List available AI apps |
| `login --email x --password y` | Authenticate with email/password |
| `setup` | Interactive first-time setup (URL + auth + verify) |
| `version` | Print CLI version |

## Easily Confused Commands — READ THIS

| What you want | Correct command | WRONG command |
|---------------|----------------|---------------|
| Topic Studio history (选题工坊历史选题) | `list-topic-plans` | ~~list-creations~~ ~~list-knowledge~~ |
| Manually saved content (手动保存的创作) | `list-creations` | ~~list-topic-plans~~ |
| Import a product from URL with AI-filled knowledge | `create-offer-from-url` | ~~extract-text only~~ |
| Browse knowledge base (selling points, FAQ, audiences) | `list-knowledge` | ~~list-creations~~ |
| Generate NEW topics | `topic-studio` | ~~list-creations~~ |
| View marketing strategy config | `list-strategy-units` | ~~list-creations~~ ~~list-knowledge~~ |

## Typical Workflow

```
1. openlucid-cli list-merchants
   → Get merchant UUIDs

2. openlucid-cli list-offers --merchant-id <merchant_uuid>
   → Get product/offer UUIDs

3. openlucid-cli offer-context --id <offer_uuid>
   → Full context: description, selling points, audiences, knowledge, assets

0. Or create a new offer first:
   - extract-text --url "..."                        → extract page text
   - create-offer --merchant-id <id> --name "..."   → create manually
   - create-offer-from-url --merchant-id <id> --name "..." --url "..."
                                                    → extract + create in one step

4. Task-specific commands:
   - kb-qa --offer-id <id> --question "..."      → Knowledge Q&A
   - topic-studio --offer-id <id>                 → Generate new topics
   - list-creations --offer-id <id>               → Browse past topics/creations
   - search-assets --scope-type offer --scope-id <id> --q "keyword"
   - list-knowledge --scope-type offer --scope-id <id>

5. openlucid-cli save-creation --title "..." --content "..." --offer-id <id>
   → Save final outputs back to OpenLucid
```

## Detailed Command Examples

### Pagination
Most list commands support `--page` and `--page-size`:
```bash
openlucid-cli list-offers --merchant-id <id> --page 1 --page-size 50
```

### Offer Creation
```bash
# Create an offer directly
openlucid-cli create-offer \
  --merchant-id <merchant_uuid> \
  --name "公仔牌顽渍净洗衣粉" \
  --offer-type product \
  --description "主打轻松搓洗去污渍..." \
  --selling-points "3倍洁净力,除菌除螨去黄,冷水易溶解" \
  --audiences "家庭日常洗衣用户,关注卫生的消费者" \
  --scenarios "日常洗衣,顽渍清洁"

# Extract text from a product page
openlucid-cli extract-text --url "https://example.com/product-page"

# Extract text, infer knowledge, create the offer, and save inferred knowledge
openlucid-cli create-offer-from-url \
  --merchant-id <merchant_uuid> \
  --name "抖音导入商品" \
  --url "https://example.com/product-page"
```

### Knowledge Base
```bash
# List knowledge for an offer
openlucid-cli list-knowledge --scope-type offer --scope-id <offer_uuid>

# List knowledge for a merchant
openlucid-cli list-knowledge --scope-type merchant --scope-id <merchant_uuid>

# Add knowledge
openlucid-cli add-knowledge --scope-type offer --scope-id <offer_uuid> \
  --title "核心卖点" --content "3倍洁净去渍力" --type selling_point
```

Knowledge types: `brand`, `audience`, `scenario`, `selling_point`, `objection`, `proof`, `faq`, `general`

### Creations (Topics, Scripts, Posts)
```bash
# List all creations for a product
openlucid-cli list-creations --offer-id <offer_uuid>

# Filter by content type
openlucid-cli list-creations --offer-id <offer_uuid> --content-type post

# Filter by source app
openlucid-cli list-creations --offer-id <offer_uuid> --source-app topic-studio

# Search in title and content
openlucid-cli list-creations --q "洗衣粉"

# Save a new creation
openlucid-cli save-creation --title "标题" --content "正文内容" \
  --offer-id <offer_uuid> --type post --tags "选题,洗护"
```

Content types: `post`, `script`, `email`, `caption`, `hook`, `general`

### Assets
```bash
# Search by keyword
openlucid-cli search-assets --scope-type offer --scope-id <id> --q "logo"

# Filter by type
openlucid-cli search-assets --scope-type offer --scope-id <id> --asset-type image

# Filter by tags
openlucid-cli search-assets --scope-type offer --scope-id <id> --tags "产品图"
```

Asset types: `image`, `video`, `document`

### Topic Studio
```bash
# Generate 5 topics (default)
openlucid-cli topic-studio --offer-id <offer_uuid>

# Generate 10 topics with a specific strategy
openlucid-cli topic-studio --offer-id <offer_uuid> --count 10 --strategy-unit-id <id>
```

## Authentication

Two auth methods (stored in `~/.openlucid.json`):

1. **Cookie auth** — `openlucid-cli login` (session, expires 168h)
2. **API token** — from Web UI Settings > MCP > Access Tokens (long-lived, recommended)

```json
{"url": "http://your-server", "token": "your-mcp-token"}
```

Config priority: `--url` flag > `OPENLUCID_URL` env > `~/.openlucid.json` > `http://localhost`

## Error Handling

| Error | Fix |
|-------|-----|
| `401 Not authenticated` | Run `openlucid-cli login` or `openlucid-cli setup` |
| `Connection refused` | Start server: `cd docker && docker compose up -d` |
| `command not found` | Run `bash tools/install.sh` or `export PATH="$HOME/.local/bin:$PATH"` |

## Notes

- All output is JSON — pipe to `jq` for filtering
- Run `openlucid-cli COMMAND --help` for full parameter details
- The script uses only Python standard library
