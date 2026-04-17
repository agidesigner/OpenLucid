---
app_id: asset_tagging
name: 素材打标
name_en: Asset Tagging
slug: asset-tagging
description: 给素材自动生成结构化标签——内容形态、促销机制、主体、用途、渠道适配等。供库存盘点、B-roll 匹配、agent 检索使用。
description_en: Auto-tag assets with structured metadata — content form, campaign type, subject, usage, channel fit — for inventory review, B-roll matching, and agent retrieval.
icon: "🏷️"
category: asset_management
task_type: classification
required_entities: [asset]
required_capabilities: [vision_understanding]
entry_modes: [background]
status: active
required_model_types: [vision_llm]
---

# Asset Tagging

Vision-LLM driven classification applied to every uploaded asset. Writes into `Asset.tags_json` across multiple axes:

- **subject / usage / channel_fit** — free-form AI-generated tags
- **selling_point / scenario** — preferably reuse exact phrases from the offer's KB
- **content_form** — closed vocabulary enum (15 forms: unboxing, vlog, scripted_skit, …)
- **campaign_type** — closed vocabulary enum (12 mechanics: flash_sale, bundle_discount, trial, …)

Invoked automatically on asset upload; also exposed through MCP `get_app_config(app_id="asset_tagging")` so agents can see the valid enum IDs before they retrieve / filter assets.
