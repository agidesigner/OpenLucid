"""Registry of publishing platform definitions.

Loads from two sources (user overlays ship):
  1. Shipped defaults:  app/apps/platforms/*.md  (in git, updated by pulls)
  2. User customizations: {STORAGE_BASE_PATH}/platforms/*.md  (docker volume, persistent)

A user .md with the same `id` as a shipped one wins. A user .md with a new id
adds a new platform. Users never need to edit shipped files → no git conflicts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.apps.registry import _parse_yaml_simple
from app.config import settings

logger = logging.getLogger(__name__)

_PLATFORMS_DIR = Path(__file__).parent.parent / "apps" / "platforms"
_USER_PLATFORMS_DIR = Path(settings.STORAGE_BASE_PATH) / "platforms"


@dataclass
class ScriptPlatform:
    id: str
    name_zh: str
    name_en: str
    emoji: str
    region: str                    # zh | en | global
    content_type: str              # video | text_post
    aspect_ratio: str | None       # portrait | landscape | square (video only)
    max_script_chars: int
    body: str                      # platform writing guide, injected into prompt

    @property
    def is_video(self) -> bool:
        return self.content_type == "video"

    def localized_name(self, lang: str) -> str:
        if lang == "en" and self.name_en:
            return self.name_en
        return self.name_zh


_REGISTRY: dict[str, ScriptPlatform] | None = None


def _load_dir(d: Path, registry: dict[str, ScriptPlatform], source: str) -> None:
    """Load all .md files from a directory into registry (later entries override earlier)."""
    if not d.is_dir():
        return
    for md_file in sorted(d.glob("*.md")):
        try:
            p = _parse_platform_md(md_file)
            if p:
                if p.id in registry:
                    logger.info("Platform %r from %s overrides shipped default", p.id, source)
                registry[p.id] = p
        except Exception:
            logger.warning("Failed to load platform from %s", md_file, exc_info=True)


def _load_all() -> dict[str, ScriptPlatform]:
    registry: dict[str, ScriptPlatform] = {}
    # 1. Shipped defaults first
    _load_dir(_PLATFORMS_DIR, registry, source="shipped")
    # 2. User overlays win (override by id, or add new)
    _load_dir(_USER_PLATFORMS_DIR, registry, source="user")
    return registry


def _parse_platform_md(path: Path) -> ScriptPlatform | None:
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
    return ScriptPlatform(
        id=fm["id"],
        name_zh=fm.get("name_zh", fm["id"]),
        name_en=fm.get("name_en", fm.get("name_zh", fm["id"])),
        emoji=fm.get("emoji", "📱"),
        region=fm.get("region", "global"),
        content_type=fm.get("content_type", "video"),
        aspect_ratio=fm.get("aspect_ratio") or None,
        max_script_chars=int(fm.get("max_script_chars", 600)),
        body=body,
    )


def _registry() -> dict[str, ScriptPlatform]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_all()
    return _REGISTRY


def list_platforms() -> list[ScriptPlatform]:
    return list(_registry().values())


def get_platform(platform_id: str) -> ScriptPlatform | None:
    return _registry().get(platform_id)


DEFAULT_PLATFORM_ID = "douyin"
