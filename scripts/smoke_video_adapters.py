#!/usr/bin/env python3
"""End-to-end smoke test for VideoProvider adapters (Chanjing + Jogg).

Usage:
    # Chanjing — credentials via env vars or CLI args
    CHANJING_APP_ID=xxx CHANJING_SECRET_KEY=yyy \\
        python scripts/smoke_video_adapters.py --provider chanjing

    # Jogg
    JOGG_API_KEY=xxx \\
        python scripts/smoke_video_adapters.py --provider jogg

    # Skip the actual video creation step (only list avatars/voices)
    python scripts/smoke_video_adapters.py --provider chanjing --skip-create

    # Override script + which avatar/voice to use
    python scripts/smoke_video_adapters.py --provider jogg \\
        --avatar-id 81 --voice-id en-US-ChristopherNeural \\
        --script "Hello, this is a smoke test."

What it does:
    1. list_avatars(page=1, page_size=5) — print first 5
    2. list_voices(page=1, page_size=5) — print first 5
    3. create_avatar_video(...) — submit task, print returned task_id
    4. get_video_status(task_id) — poll every 10s up to 5 min, print final result

Exits non-zero on any failure. Designed to be run manually after wiring changes.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# Make `app.*` imports work when running this script directly from the repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.video import (  # noqa: E402
    CreateVideoRequest,
    get_video_provider,
)
from app.adapters.video.base import VideoProvider  # noqa: E402

POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 300

DEFAULT_SCRIPTS = {
    "chanjing": "大家好，这是一段来自 OpenLucid 的烟测视频，正在测试蝉镜数字人接口。",
    "jogg": "Hi everyone, this is a smoke-test clip from OpenLucid verifying the Jogg.ai integration.",
}


def _build_provider(args: argparse.Namespace) -> VideoProvider:
    if args.provider == "chanjing":
        app_id = args.app_id or os.environ.get("CHANJING_APP_ID", "")
        secret_key = args.secret_key or os.environ.get("CHANJING_SECRET_KEY", "")
        if not app_id or not secret_key:
            sys.exit(
                "ERROR: Chanjing requires --app-id and --secret-key "
                "(or CHANJING_APP_ID + CHANJING_SECRET_KEY env vars)"
            )
        return get_video_provider(
            "chanjing",
            {"app_id": app_id, "secret_key": secret_key},
        )
    if args.provider == "jogg":
        api_key = args.api_key or os.environ.get("JOGG_API_KEY", "")
        if not api_key:
            sys.exit("ERROR: Jogg requires --api-key (or JOGG_API_KEY env var)")
        return get_video_provider("jogg", {"api_key": api_key})
    sys.exit(f"Unknown provider: {args.provider}")


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


async def _run(args: argparse.Namespace) -> int:
    provider = _build_provider(args)
    print(f"Using provider: {provider.provider_name}")

    # 1. list_avatars
    _print_section("Step 1: list_avatars(page=1, page_size=5)")
    try:
        avatars = await provider.list_avatars(page=1, page_size=5)
    except Exception as e:
        print(f"FAIL: list_avatars raised {type(e).__name__}: {e}")
        return 1
    print(f"OK: got {len(avatars)} avatars")
    for a in avatars:
        print(f"  - id={a.id!r:30}  name={a.name!r:20}  gender={a.gender}")
        print(f"    preview_image={a.preview_image_url}")
    if not avatars:
        print("FAIL: avatar list is empty")
        return 1

    # 2. list_voices
    _print_section("Step 2: list_voices(page=1, page_size=5)")
    try:
        voices = await provider.list_voices(page=1, page_size=5)
    except Exception as e:
        print(f"FAIL: list_voices raised {type(e).__name__}: {e}")
        return 1
    print(f"OK: got {len(voices)} voices")
    for v in voices:
        print(f"  - id={v.id!r:40}  name={v.name!r:20}  lang={v.language}")
    if not voices:
        print("FAIL: voice list is empty")
        return 1

    if args.skip_create:
        print("\n--skip-create flag set, stopping after list calls.")
        return 0

    # 3. create_avatar_video
    _print_section("Step 3: create_avatar_video")
    avatar_id = args.avatar_id or avatars[0].id
    voice_id = args.voice_id or voices[0].id
    script = args.script or DEFAULT_SCRIPTS.get(args.provider, "Hello world.")

    # Resolve provider_extras from the chosen avatar (if known) — Chanjing
    # needs figure_type. If --avatar-id was passed but doesn't match what we
    # listed, look it up; otherwise use the first listed avatar's extras.
    chosen_avatar = next((a for a in avatars if a.id == avatar_id), avatars[0])
    provider_extras = dict(chosen_avatar.extras or {})

    print(f"  avatar_id      = {avatar_id!r}")
    print(f"  voice_id       = {voice_id!r}")
    print(f"  aspect         = {args.aspect_ratio}")
    print(f"  provider_extras= {provider_extras}")
    print(f"  script         = {script!r}")
    req = CreateVideoRequest(
        avatar_id=avatar_id,
        voice_id=voice_id,
        script=script,
        aspect_ratio=args.aspect_ratio,
        caption=True,
        name="OpenLucid smoke test",
        provider_extras=provider_extras,
    )
    try:
        task_id = await provider.create_avatar_video(req)
    except Exception as e:
        print(f"FAIL: create_avatar_video raised {type(e).__name__}: {e}")
        return 1
    print(f"OK: task_id = {task_id!r}")

    # 4. poll get_video_status
    _print_section(f"Step 4: poll get_video_status (up to {POLL_TIMEOUT_SECONDS}s)")
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    last_status = None
    while time.time() < deadline:
        try:
            vs = await provider.get_video_status(task_id)
        except Exception as e:
            print(f"FAIL: get_video_status raised {type(e).__name__}: {e}")
            return 1
        if vs.status != last_status:
            print(
                f"  [{int(time.time()) % 100000:>6}] status={vs.status:11} "
                f"progress={vs.progress}  video_url={vs.video_url}"
            )
            last_status = vs.status
        if vs.status in ("completed", "failed"):
            break
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    else:
        print(f"FAIL: did not finish within {POLL_TIMEOUT_SECONDS}s, last status={last_status}")
        return 1

    if vs.status == "failed":
        print(f"FAIL: provider reported failure: {vs.error_message}")
        return 1

    print()
    print(f"  status         = {vs.status}")
    print(f"  video_url      = {vs.video_url}")
    print(f"  cover_url      = {vs.cover_url}")
    print(f"  duration       = {vs.duration_seconds}s")
    print(f"  progress       = {vs.progress}")
    print()
    print("SUCCESS: end-to-end video generation completed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test VideoProvider adapters")
    parser.add_argument("--provider", choices=["chanjing", "jogg"], required=True)
    parser.add_argument("--app-id", help="Chanjing app_id (or env CHANJING_APP_ID)")
    parser.add_argument("--secret-key", help="Chanjing secret_key (or env CHANJING_SECRET_KEY)")
    parser.add_argument("--api-key", help="Jogg api_key (or env JOGG_API_KEY)")
    parser.add_argument("--avatar-id", help="Override avatar_id (default: first from list)")
    parser.add_argument("--voice-id", help="Override voice_id (default: first from list)")
    parser.add_argument("--script", help="Override script text")
    parser.add_argument(
        "--aspect-ratio",
        choices=["portrait", "landscape", "square"],
        default="portrait",
    )
    parser.add_argument(
        "--skip-create",
        action="store_true",
        help="Only list avatars/voices, do not create a video task",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(_run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
