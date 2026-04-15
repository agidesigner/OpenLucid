"""Registry of script structure definitions.

Loads shipped defaults, then overlays user files from the storage volume
({STORAGE_BASE_PATH}/script_structures/*.md).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.apps.registry import _parse_yaml_simple
from app.config import settings

logger = logging.getLogger(__name__)

_STRUCTURES_DIR = Path(__file__).parent.parent / "apps" / "script_structures"
_USER_STRUCTURES_DIR = Path(settings.STORAGE_BASE_PATH) / "script_structures"


@dataclass
class ScriptStructure:
    id: str
    name_zh: str
    name_en: str
    emoji: str
    description_zh: str
    description_en: str
    section_ids: list[str]  # e.g. ["hook", "body", "cta"]
    body: str               # per-section guidance, injected into prompt

    def localized_name(self, lang: str) -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_zh

    def localized_description(self, lang: str) -> str:
        if lang == "en" and self.description_en:
            return self.description_en
        return self.description_zh


_REGISTRY: dict[str, ScriptStructure] | None = None


def _load_dir(d: Path, registry: dict[str, ScriptStructure], source: str) -> None:
    if not d.is_dir():
        return
    for md_file in sorted(d.glob("*.md")):
        try:
            s = _parse_structure_md(md_file)
            if s:
                if s.id in registry:
                    logger.info("Structure %r from %s overrides shipped default", s.id, source)
                registry[s.id] = s
        except Exception:
            logger.warning("Failed to load structure from %s", md_file, exc_info=True)


def _load_all() -> dict[str, ScriptStructure]:
    registry: dict[str, ScriptStructure] = {}
    _load_dir(_STRUCTURES_DIR, registry, source="shipped")
    _load_dir(_USER_STRUCTURES_DIR, registry, source="user")
    return registry


def _parse_structure_md(path: Path) -> ScriptStructure | None:
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
    section_ids = fm.get("section_ids", [])
    if isinstance(section_ids, str):
        section_ids = [s.strip() for s in section_ids.split(",") if s.strip()]
    return ScriptStructure(
        id=fm["id"],
        name_zh=fm.get("name_zh", fm["id"]),
        name_en=fm.get("name_en", fm.get("name_zh", fm["id"])),
        emoji=fm.get("emoji", "📋"),
        description_zh=fm.get("description_zh", ""),
        description_en=fm.get("description_en", ""),
        section_ids=section_ids,
        body=body,
    )


def _registry() -> dict[str, ScriptStructure]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def list_structures() -> list[ScriptStructure]:
    return list(_registry().values())


def get_structure(structure_id: str) -> ScriptStructure | None:
    return _registry().get(structure_id)


DEFAULT_STRUCTURE_ID = "hook_body_cta"
