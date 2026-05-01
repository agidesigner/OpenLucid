"""Seed shipped prompts into the user-overlay directory on first run.

Why this exists: every prompt-style registry in `app/application/` is
already two-tier — shipped defaults at `app/apps/<category>/*.md`
(version-controlled, git-pull mutates) and a user overlay at
`$STORAGE_BASE_PATH/<category>/*.md` (persistent docker volume, where
users SHOULD edit). The user-file-with-same-id wins at load time.

The mechanism works; the *discovery* doesn't. Users who clone the repo
see only the shipped files in their checkout, edit those, and hit a
merge conflict on the next `git pull`. Seeding once on container start
gives them a writable copy at the right place — they ls the docker
volume and see what's there, instead of reaching for the only files
their IDE shows them in the source tree.

Idempotent: existing user files are never touched. New shipped files
(e.g. a new platform added in a release) are seeded into the overlay
on next boot so users can customize them too.

This is intentionally simpler than hermes-agent's manifest-with-hash
sync. We have no UI to surface "shipped has a new version, your
custom is N versions behind"; until we do, manifest tracking is empty
ROI. To pick up an updated shipped default, users delete the overlay
file and restart — `seed_user_overlay` re-copies the new version.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# Yaml-comment line at the top of every shipped .md that tells users
# to edit elsewhere. Meaningful in the shipped file (warns the user
# they're in the wrong place); confusing in the overlay copy where
# editing IS the intended workflow. Stripped at seed time.
_DEFAULT_NOTICE_RE = re.compile(
    r"^# DEFAULT — to customize.*?(?:\r?\n)", re.MULTILINE
)

# (category-name, shipped-dir absolute path). Mirrors the tuples in
# every <category> loader at app/application/script_*.py — keep in sync
# when adding a new prompt category.
_APPS_DIR = Path(__file__).resolve().parent.parent / "apps"

OVERLAY_CATEGORIES: list[tuple[str, Path]] = [
    ("platforms",         _APPS_DIR / "platforms"),
    ("personas",          _APPS_DIR / "personas"),
    ("script_structures", _APPS_DIR / "script_structures"),
    ("content_forms",     _APPS_DIR / "content_forms"),
    ("campaign_types",    _APPS_DIR / "campaign_types"),
]


def seed_user_overlay() -> None:
    """Copy any shipped .md not yet present in the user overlay.

    Safe to run on every boot — files that already exist in the
    overlay (because the user customized them, or a prior boot
    seeded them) are left untouched.
    """
    base = Path(settings.STORAGE_BASE_PATH)
    for cat, shipped_dir in OVERLAY_CATEGORIES:
        if not shipped_dir.is_dir():
            logger.debug("prompt overlay: shipped dir missing for %s, skipping", cat)
            continue
        user_dir = base / cat
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("prompt overlay: cannot create %s: %s", user_dir, e)
            continue
        seeded = 0
        for src in sorted(shipped_dir.glob("*.md")):
            dst = user_dir / src.name
            if dst.exists():
                continue
            # Atomic write: stage to a sibling .tmp then rename. If the
            # process is killed mid-write the dst stays absent (only a
            # stray .tmp lingers), so the next boot retries cleanly.
            # A direct write_text would leave a half-written dst that
            # the `if dst.exists()` skip above would then permanently
            # honor — the file would never self-heal.
            tmp = dst.with_suffix(dst.suffix + ".tmp")
            try:
                # Read+strip rather than shutil.copy2 — drop the
                # "DEFAULT — edit elsewhere" notice from the shipped
                # file's frontmatter so the overlay copy doesn't tell
                # the user not to edit the file they're meant to edit.
                src_text = src.read_text(encoding="utf-8")
                cleaned = _DEFAULT_NOTICE_RE.sub("", src_text, count=1)
                tmp.write_text(cleaned, encoding="utf-8")
                tmp.replace(dst)
                seeded += 1
            except OSError as e:
                logger.warning("prompt overlay: failed to seed %s: %s", dst, e)
                # Best-effort cleanup of the temp file we may have left
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        if seeded:
            logger.info("prompt overlay: seeded %d %s file(s) into %s", seeded, cat, user_dir)
