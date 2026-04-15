from __future__ import annotations

from pydantic import BaseModel

MODEL_TYPE_LABELS: dict[str, str] = {
    "text_llm": "文本 LLM",
    "vision_llm": "视觉 LLM",
    "video_gen": "视频生成",
    "image_gen": "图像生成",
    "tts": "语音合成",
    "embedding": "向量模型",
}

# System-level scenes (not tied to any app)
SYSTEM_SCENES: dict[str, dict] = {
    "knowledge": {"label": "知识库建设", "icon": "📚", "model_types": ["text_llm"]},
    "asset_tagging": {"label": "素材打标", "icon": "🏷️", "model_types": ["vision_llm"]},
    "brandkit_extract": {"label": "品牌规范提取", "icon": "🎨", "model_types": ["text_llm"]},
}


class ModelTypeConfig(BaseModel):
    model_type: str
    model_type_label: str
    config_id: str | None
    config_label: str | None


class SceneSection(BaseModel):
    scene_key: str
    label: str
    icon: str
    scene_type: str  # "system" | "app"
    model_configs: list[ModelTypeConfig]


class LLMSceneConfigsResponse(BaseModel):
    sections: list[SceneSection]


class SceneConfigUpdate(BaseModel):
    scene_key: str
    model_type: str
    config_id: str | None = None


class LLMSceneConfigsUpdate(BaseModel):
    updates: list[SceneConfigUpdate]


# ── Media capability defaults (image / video / TTS) ────────────────

class MediaCapabilityOption(BaseModel):
    """A selectable (provider + model/voice) combo for a capability dropdown."""
    provider_config_id: str
    provider: str            # chanjing | jogg
    provider_label: str
    model_code: str | None   # for image_gen / video_gen
    voice_id: str | None     # for tts
    display_label: str       # "🎬 蝉镜 · Doubao-Seedance-1.0-pro"


class MediaCapabilityConfig(BaseModel):
    capability: str          # image_gen | video_gen | tts
    label: str
    icon: str
    description: str
    current_provider_config_id: str | None
    current_model_code: str | None
    current_voice_id: str | None
    options: list[MediaCapabilityOption]


class MediaCapabilitiesResponse(BaseModel):
    capabilities: list[MediaCapabilityConfig]


class MediaCapabilityUpdate(BaseModel):
    capability: str
    provider_config_id: str | None = None
    model_code: str | None = None
    voice_id: str | None = None


class MediaCapabilitiesUpdateRequest(BaseModel):
    updates: list[MediaCapabilityUpdate]


class LLMConfigCreate(BaseModel):
    label: str
    provider: str  # openai | minimax | custom
    api_key: str
    base_url: str
    model_name: str


class LLMConfigUpdate(BaseModel):
    label: str | None = None
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_name: str | None = None


class LLMConfigResponse(BaseModel):
    id: str
    label: str
    provider: str
    api_key_masked: str
    base_url: str
    model_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class LLMValidateRequest(BaseModel):
    api_key: str
    base_url: str
    model_name: str
    provider: str = "custom"


class LLMFetchModelsRequest(BaseModel):
    api_key: str
    base_url: str
    provider: str  # openai | minimax | anthropic | deepseek | custom


class LLMFetchModelsResponse(BaseModel):
    models: list[str]
    recommended: str


# ── MCP Token ────────────────────────────────────────────────────

class McpTokenCreate(BaseModel):
    label: str


class McpTokenResponse(BaseModel):
    id: str
    label: str
    token_preview: str  # "••••xxxx"
    created_at: str

    model_config = {"from_attributes": True}


class McpTokenCreatedResponse(McpTokenResponse):
    raw_token: str  # shown only once
