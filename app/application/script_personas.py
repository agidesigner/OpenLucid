"""Registry of persona style definitions.

Loads shipped defaults from app/apps/personas/*.md, then overlays user files
from {STORAGE_BASE_PATH}/personas/*.md (docker volume — not in git).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.apps.registry import _parse_yaml_simple
from app.config import settings

logger = logging.getLogger(__name__)

_PERSONAS_DIR = Path(__file__).parent.parent / "apps" / "personas"
_USER_PERSONAS_DIR = Path(settings.STORAGE_BASE_PATH) / "personas"


@dataclass
class ScriptPersona:
    id: str
    name_zh: str
    name_en: str
    emoji: str
    description_zh: str
    description_en: str
    tags: list[str]
    body: str   # style instructions, injected into prompt

    def localized_name(self, lang: str) -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_zh

    def localized_description(self, lang: str) -> str:
        if lang == "en" and self.description_en:
            return self.description_en
        return self.description_zh


_REGISTRY: dict[str, ScriptPersona] | None = None


def _load_dir(d: Path, registry: dict[str, ScriptPersona], source: str) -> None:
    if not d.is_dir():
        return
    for md_file in sorted(d.glob("*.md")):
        try:
            p = _parse_persona_md(md_file)
            if p:
                if p.id in registry:
                    logger.info("Persona %r from %s overrides shipped default", p.id, source)
                registry[p.id] = p
        except Exception:
            logger.warning("Failed to load persona from %s", md_file, exc_info=True)


def _load_all() -> dict[str, ScriptPersona]:
    registry: dict[str, ScriptPersona] = {}
    _load_dir(_PERSONAS_DIR, registry, source="shipped")
    _load_dir(_USER_PERSONAS_DIR, registry, source="user")
    return registry


def _parse_persona_md(path: Path) -> ScriptPersona | None:
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
    tags = fm.get("tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return ScriptPersona(
        id=fm["id"],
        name_zh=fm.get("name_zh", fm["id"]),
        name_en=fm.get("name_en", fm.get("name_zh", fm["id"])),
        emoji=fm.get("emoji", "🎭"),
        description_zh=fm.get("description_zh", ""),
        description_en=fm.get("description_en", ""),
        tags=tags,
        body=body,
    )


def _registry() -> dict[str, ScriptPersona]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def list_personas() -> list[ScriptPersona]:
    return list(_registry().values())


def get_persona(persona_id: str) -> ScriptPersona | None:
    return _registry().get(persona_id)


DEFAULT_PERSONA_ID = "friendly_storyteller"
