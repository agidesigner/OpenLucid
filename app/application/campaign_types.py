"""Registry of asset `campaign_type` vocabulary — promotional mechanic a
creative supports.

Loads from two sources:
  1. Shipped defaults:  app/apps/campaign_types/*.md
  2. User overlay:      {STORAGE_BASE_PATH}/campaign_types/*.md  (docker volume)

Same-id overlay wins; new-id overlay adds.

Used by:
  - vision LLM auto-tagging prompt (closed-vocabulary classification)
  - REST GET /apps/asset-tagging/campaign-types
  - MCP get_app_config(app_id="asset_tagging")
  - Asset tag picker UI (offer.html)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.apps.registry import _parse_yaml_simple
from app.config import settings

logger = logging.getLogger(__name__)

_CAMPAIGN_TYPES_DIR = Path(__file__).parent.parent / "apps" / "campaign_types"
_USER_CAMPAIGN_TYPES_DIR = Path(settings.STORAGE_BASE_PATH) / "campaign_types"


@dataclass
class CampaignType:
    id: str
    name_zh: str
    name_en: str
    emoji: str
    description_zh: str
    description_en: str
    body: str  # "when to tag" guidance, injected into vision LLM prompt

    def localized_name(self, lang: str) -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_zh

    def localized_description(self, lang: str) -> str:
        if lang == "en" and self.description_en:
            return self.description_en
        return self.description_zh


_REGISTRY: dict[str, CampaignType] | None = None


def _load_dir(d: Path, registry: dict[str, CampaignType], source: str) -> None:
    if not d.is_dir():
        return
    for md_file in sorted(d.glob("*.md")):
        try:
            ct = _parse_campaign_type_md(md_file)
            if ct:
                if ct.id in registry:
                    logger.info("CampaignType %r from %s overrides shipped default", ct.id, source)
                registry[ct.id] = ct
        except Exception:
            logger.warning("Failed to load campaign_type from %s", md_file, exc_info=True)


def _load_all() -> dict[str, CampaignType]:
    registry: dict[str, CampaignType] = {}
    _load_dir(_CAMPAIGN_TYPES_DIR, registry, source="shipped")
    _load_dir(_USER_CAMPAIGN_TYPES_DIR, registry, source="user")
    return registry


def _parse_campaign_type_md(path: Path) -> CampaignType | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    fm = _parse_yaml_simple(parts[1])
    if not fm.get("id"):
        return None
    body = parts[2].strip()
    return CampaignType(
        id=fm["id"],
        name_zh=fm.get("name_zh", fm["id"]),
        name_en=fm.get("name_en", fm.get("name_zh", fm["id"])),
        emoji=fm.get("emoji", "🏷️"),
        description_zh=fm.get("description_zh", ""),
        description_en=fm.get("description_en", fm.get("description_zh", "")),
        body=body,
    )


def _registry() -> dict[str, CampaignType]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def list_campaign_types() -> list[CampaignType]:
    return list(_registry().values())


def get_campaign_type(campaign_type_id: str) -> CampaignType | None:
    return _registry().get(campaign_type_id)
