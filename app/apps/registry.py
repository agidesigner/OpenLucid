from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AppDefinition:
    app_id: str
    name: str
    slug: str
    description: str
    icon: str
    category: str
    task_type: str
    required_entities: list[str]
    required_capabilities: list[str]
    entry_modes: list[str]
    status: str
    required_model_types: list[str] = field(default_factory=lambda: ["text_llm"])
    is_builtin: bool = True
    version: str = "1.0.0"


class AppRegistry:
    _apps: dict[str, AppDefinition] = {}
    _loaded: bool = False

    @classmethod
    def register(cls, app: AppDefinition) -> None:
        cls._apps[app.app_id] = app

    @classmethod
    def list_apps(cls) -> list[AppDefinition]:
        cls._ensure_loaded()
        return list(cls._apps.values())

    @classmethod
    def get_app(cls, app_id: str) -> AppDefinition | None:
        cls._ensure_loaded()
        return cls._apps.get(app_id)

    @classmethod
    def _ensure_loaded(cls) -> None:
        if not cls._loaded:
            cls._load_markdown_definitions()
            cls._loaded = True

    @classmethod
    def _load_markdown_definitions(cls) -> None:
        """Scan definitions/ directory for .md files with YAML frontmatter."""
        defs_dir = Path(__file__).parent / "definitions"
        if not defs_dir.is_dir():
            return

        for md_file in sorted(defs_dir.glob("*.md")):
            try:
                app = _parse_app_markdown(md_file)
                if app:
                    cls._apps[app.app_id] = app
            except Exception:
                logger.warning("Failed to load app definition from %s", md_file, exc_info=True)


def _parse_app_markdown(path: Path) -> AppDefinition | None:
    """Parse a markdown file with YAML frontmatter into an AppDefinition."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None

    # Split frontmatter
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    frontmatter = _parse_yaml_simple(parts[1])
    if not frontmatter or "app_id" not in frontmatter:
        return None

    def _as_list(val: Any) -> list[str]:
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    return AppDefinition(
        app_id=frontmatter["app_id"],
        name=frontmatter.get("name", frontmatter["app_id"]),
        slug=frontmatter.get("slug", frontmatter["app_id"].replace("_", "-")),
        description=frontmatter.get("description", ""),
        icon=frontmatter.get("icon", "📦"),
        category=frontmatter.get("category", "general"),
        task_type=frontmatter.get("task_type", "general"),
        required_entities=_as_list(frontmatter.get("required_entities", [])),
        required_capabilities=_as_list(frontmatter.get("required_capabilities", [])),
        entry_modes=_as_list(frontmatter.get("entry_modes", ["global"])),
        status=frontmatter.get("status", "active"),
        required_model_types=_as_list(frontmatter.get("required_model_types", ["text_llm"])),
        is_builtin=frontmatter.get("is_builtin", True),
        version=frontmatter.get("version", "1.0.0"),
    )


def _parse_yaml_simple(text: str) -> dict[str, Any]:
    """Minimal YAML-like frontmatter parser (no PyYAML dependency).

    Handles: scalars, inline lists ``[a, b, c]``, and quoted strings.
    """
    result: dict[str, Any] = {}
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # Strip surrounding quotes
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
            result[key] = items
        elif value.lower() in ("true", "yes"):
            result[key] = True
        elif value.lower() in ("false", "no"):
            result[key] = False
        else:
            result[key] = value

    return result
