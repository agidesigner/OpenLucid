from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.application.setting_service import (
    activate_llm_config,
    create_llm_config,
    delete_llm_config,
    fetch_llm_models,
    get_scene_configs,
    list_llm_configs,
    update_llm_config,
    update_scene_configs,
    validate_llm_connection,
)
from app.schemas.setting import (
    LLMConfigCreate,
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMFetchModelsRequest,
    LLMFetchModelsResponse,
    LLMSceneConfigsResponse,
    LLMSceneConfigsUpdate,
    LLMValidateRequest,
    McpTokenCreate,
    McpTokenCreatedResponse,
    McpTokenResponse,
)

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/llm", response_model=list[LLMConfigResponse])
async def list_llm(db: AsyncSession = Depends(get_db)):
    return await list_llm_configs(db)


@router.post("/llm", response_model=LLMConfigResponse, status_code=201)
async def create_llm(data: LLMConfigCreate, db: AsyncSession = Depends(get_db)):
    return await create_llm_config(db, data)


@router.post("/llm/fetch-models", response_model=LLMFetchModelsResponse)
async def fetch_llm_models_endpoint(data: LLMFetchModelsRequest):
    models, recommended = await fetch_llm_models(data.api_key, data.base_url, data.provider)
    return LLMFetchModelsResponse(models=models, recommended=recommended)


@router.post("/llm/validate")
async def validate_llm(data: LLMValidateRequest):
    await validate_llm_connection(data.api_key, data.base_url, data.model_name, data.provider)
    return {"ok": True}


@router.get("/llm/scenes", response_model=LLMSceneConfigsResponse)
async def get_llm_scenes(db: AsyncSession = Depends(get_db)):
    return await get_scene_configs(db)


@router.put("/llm/scenes", response_model=LLMSceneConfigsResponse)
async def update_llm_scenes(data: LLMSceneConfigsUpdate, db: AsyncSession = Depends(get_db)):
    return await update_scene_configs(db, data)


@router.put("/llm/{config_id}", response_model=LLMConfigResponse)
async def update_llm(
    config_id: uuid.UUID, data: LLMConfigUpdate, db: AsyncSession = Depends(get_db)
):
    return await update_llm_config(db, config_id, data)


@router.delete("/llm/{config_id}", status_code=204)
async def delete_llm(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    await delete_llm_config(db, config_id)


@router.post("/llm/{config_id}/activate", response_model=LLMConfigResponse)
async def activate_llm(config_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await activate_llm_config(db, config_id)


# ── MCP Tokens ──────────────────────────────────────────────────


@router.get("/mcp-tokens", response_model=list[McpTokenResponse])
async def list_mcp_tokens(db: AsyncSession = Depends(get_db)):
    import hashlib
    from sqlalchemy import select
    from app.models.mcp_token import McpToken

    result = await db.execute(select(McpToken).order_by(McpToken.created_at.desc()))
    tokens = result.scalars().all()
    return [
        McpTokenResponse(
            id=str(t.id),
            label=t.label,
            token_preview=f"••••{t.token_hash[:8]}",
            created_at=t.created_at.isoformat() if t.created_at else "",
        )
        for t in tokens
    ]


@router.post("/mcp-tokens", response_model=McpTokenCreatedResponse, status_code=201)
async def create_mcp_token(data: McpTokenCreate, db: AsyncSession = Depends(get_db)):
    import hashlib
    import secrets
    from app.models.mcp_token import McpToken

    raw_token = secrets.token_hex(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()

    token = McpToken(label=data.label, token_hash=token_hash)
    db.add(token)
    await db.commit()
    await db.refresh(token)

    return McpTokenCreatedResponse(
        id=str(token.id),
        label=token.label,
        token_preview=f"••••{token_hash[:8]}",
        created_at=token.created_at.isoformat() if token.created_at else "",
        raw_token=raw_token,
    )


@router.delete("/mcp-tokens/{token_id}", status_code=204)
async def delete_mcp_token(token_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    from app.models.mcp_token import McpToken

    token = await db.get(McpToken, token_id)
    if not token:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Token not found")
    await db.delete(token)
    await db.commit()


# ── Version check ──────────────────────────────────────────────────


@router.get("/version")
async def check_version():
    """Return current version and check GitHub for latest.
    Uses git tags as source of truth — no need to manually update VERSION."""
    import pathlib
    import subprocess
    import httpx
    from packaging.version import Version, InvalidVersion

    REPO = "agidesigner/OpenLucid"

    # Get current version: .version file (Docker) → git tag (dev) → config.py (fallback)
    current = None
    try:
        v = pathlib.Path("/app/.version").read_text().strip()
        if v:
            current = v.lstrip("v")
    except Exception:
        pass
    if not current:
        try:
            proc = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                current = proc.stdout.strip().lstrip("v")
        except Exception:
            pass
    if not current:
        from app.config import VERSION
        current = VERSION

    result = {"current": current, "latest": None, "update_available": False, "check_failed": False, "release_url": None, "release_notes": None}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Try releases first (has release notes)
            resp = await client.get(
                f"https://api.github.com/repos/{REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code == 200:
                data = resp.json()
                latest = data.get("tag_name", "").lstrip("v")
                result["latest"] = latest
                result["release_url"] = data.get("html_url")
                result["release_notes"] = data.get("body", "")[:500]
            else:
                # No releases — get latest tag from GitHub API
                resp2 = await client.get(
                    f"https://api.github.com/repos/{REPO}/tags?per_page=1",
                    headers={"Accept": "application/vnd.github+json"},
                )
                if resp2.status_code == 200:
                    tags = resp2.json()
                    if tags:
                        latest = tags[0]["name"].lstrip("v")
                        result["latest"] = latest
                        result["release_url"] = f"https://github.com/{REPO}"
    except Exception:
        pass

    # Fallback: fetch git smart HTTP refs (works when API is blocked but git clone isn't)
    if not result["latest"]:
        try:
            import re
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"https://github.com/{REPO}.git/info/refs?service=git-upload-pack",
                )
                if resp.status_code == 200:
                    tags = re.findall(r"refs/tags/(v[\d.]+)\n", resp.text)
                    if tags:
                        # Sort semantically, pick highest
                        tags.sort(key=lambda t: Version(t.lstrip("v")), reverse=True)
                        latest = tags[0].lstrip("v")
                        result["latest"] = latest
                        result["release_url"] = f"https://github.com/{REPO}"
        except Exception:
            pass

    # Compare versions semantically
    if result["latest"] and current:
        try:
            result["update_available"] = Version(result["latest"]) > Version(current)
        except InvalidVersion:
            result["update_available"] = result["latest"] != current

    if not result["latest"]:
        result["check_failed"] = True

    return result


@router.get("/logs")
async def get_logs(n: int = Query(100, le=200)):
    from app.libs.log_buffer import get_log_handler
    lines = get_log_handler().get_recent(n)
    return {"lines": lines}


@router.get("/logs/export")
async def export_logs():
    from app.libs.log_buffer import get_log_handler

    lines = get_log_handler().get_recent(100)
    content = "\n".join(lines) if lines else "No log entries available."
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": f'attachment; filename="openlucid-logs-{ts}.txt"'},
    )
