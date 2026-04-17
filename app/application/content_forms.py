"""Registry of asset `content_form` vocabulary — how an asset was produced /
what format it takes.

Loads from two sources:
  1. Shipped defaults:  app/apps/content_forms/*.md
  2. User overlay:      {STORAGE_BASE_PATH}/content_forms/*.md  (docker volume)

Same-id overlay wins; new-id overlay adds.

Used by:
  - vision LLM auto-tagging prompt (closed-vocabulary classification)
  - REST GET /apps/asset-tagging/content-forms
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

_CONTENT_FORMS_DIR = Path(__file__).parent.parent / "apps" / "content_forms"
_USER_CONTENT_FORMS_DIR = Path(settings.STORAGE_BASE_PATH) / "content_forms"


@dataclass
class ContentForm:
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


_REGISTRY: dict[str, ContentForm] | None = None


def _load_dir(d: Path, registry: dict[str, ContentForm], source: str) -> None:
    if not d.is_dir():
        return
    for md_file in sorted(d.glob("*.md")):
        try:
            cf = _parse_content_form_md(md_file)
            if cf:
                if cf.id in registry:
                    logger.info("ContentForm %r from %s overrides shipped default", cf.id, source)
                registry[cf.id] = cf
        except Exception:
            logger.warning("Failed to load content_form from %s", md_file, exc_info=True)


def _load_all() -> dict[str, ContentForm]:
    registry: dict[str, ContentForm] = {}
    _load_dir(_CONTENT_FORMS_DIR, registry, source="shipped")
    _load_dir(_USER_CONTENT_FORMS_DIR, registry, source="user")
    return registry


def _parse_content_form_md(path: Path) -> ContentForm | None:
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
    return ContentForm(
        id=fm["id"],
        name_zh=fm.get("name_zh", fm["id"]),
        name_en=fm.get("name_en", fm.get("name_zh", fm["id"])),
        emoji=fm.get("emoji", "🏷️"),
        description_zh=fm.get("description_zh", ""),
        description_en=fm.get("description_en", fm.get("description_zh", "")),
        body=body,
    )


def _registry() -> dict[str, ContentForm]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def list_content_forms() -> list[ContentForm]:
    return list(_registry().values())


def get_content_form(content_form_id: str) -> ContentForm | None:
    return _registry().get(content_form_id)
