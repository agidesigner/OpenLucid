"""Chanjing AI Creation model catalog.

This is the authoritative registry of every AI generation model exposed
through chanjing's `/open/v1/ai_creation/task/submit` endpoint. The catalog
is documentation-only on chanjing's side (no listing API, no machine spec
in their OpenAPI YAML), so we maintain it here.

Two consumers:
  - Settings UI (`/setting.html?section=media-providers`) — to show users
    what they get when connecting chanjing.
  - Future creation pipeline — picks model_code, knows aspect_ratio /
    duration / clarity options, knows quirks (Hailuo ignores aspect_ratio,
    Kling 2.1 image disables ref_img_url, etc.).

When chanjing publishes new models (their changelog at
https://doc.chanjing.cc/changelog/api-changelog.html), bump this file —
no other place needs to change.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Endpoint surface (unified across all models) ─────────────────────

CHANJING_AI_CREATION_BASE_URL = "https://open-api.chanjing.cc"
CHANJING_AI_CREATION_SUBMIT_PATH = "/open/v1/ai_creation/task/submit"
CHANJING_AI_CREATION_LIST_PATH = "/open/v1/ai_creation/task/page"
CHANJING_AI_CREATION_DETAIL_PATH = "/open/v1/ai_creation/task"

# creation_type values used in submit / list bodies
CREATION_TYPE_IMAGE = 3
CREATION_TYPE_VIDEO = 4


@dataclass
class ChanjingModel:
    """A single AI creation model exposed via chanjing.

    `code` is sent verbatim as `model_code` — case sensitive (Doubao-* and
    MiniMax-* keep their capital letters).
    """

    code: str                                  # chanjing's `model_code`
    name_en: str
    name_zh: str
    kind: str                                  # "video" | "image"
    modes: list[str]                           # subset of "t2v","i2v","t2i","i2i"
    vendor: str                                # "chanjing" | "bytedance" | "kling" | "minimax" | "vidu" | "alibaba"
    tier: str                                  # "in-house" | "standard" | "pro" | "variable"
    aspect_ratios: list[str] = field(default_factory=list)  # empty when model ignores it
    clarity: list[int] = field(default_factory=list)        # supported resolution tiers
    durations: list[int] = field(default_factory=list)      # video only, seconds
    quality_modes: list[str] = field(default_factory=list)  # subset of "std","pro"
    supports_ref_image: bool = False
    selling_points_en: list[str] = field(default_factory=list)
    selling_points_zh: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)         # "new" | "flagship" | "popular"
    quirks_en: list[str] = field(default_factory=list)      # human-readable caveats (en)
    quirks_zh: list[str] = field(default_factory=list)      # human-readable caveats (zh)
    doc_url: str = ""

    def to_public_dict(self, lang: str = "zh") -> dict:
        """Serialize for the `/media-providers/catalog` API response."""
        is_zh = lang.startswith("zh")
        return {
            "code": self.code,
            "name": self.name_zh if is_zh else self.name_en,
            "kind": self.kind,
            "modes": self.modes,
            "vendor": self.vendor,
            "tier": self.tier,
            "aspect_ratios": self.aspect_ratios,
            "clarity": self.clarity,
            "durations": self.durations,
            "quality_modes": self.quality_modes,
            "supports_ref_image": self.supports_ref_image,
            "selling_points": self.selling_points_zh if is_zh else self.selling_points_en,
            "badges": self.badges,
            "quirks": self.quirks_zh if is_zh else self.quirks_en,
            "doc_url": self.doc_url,
        }


# ── Video models (creation_type = 4) ─────────────────────────────────

VIDEO_MODELS: list[ChanjingModel] = [
    ChanjingModel(
        code="happyhorse-1.0-t2v",
        name_en="HappyHorse 1.0 (T2V)",
        name_zh="HappyHorse 1.0 文生视频",
        kind="video",
        modes=["t2v"],
        vendor="chanjing",
        tier="standard",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        selling_points_en=[
            "Cinematic camera-motion prompts — low-angle tracking, light flares",
            "Strong fit for narrative shots and storytelling beats",
        ],
        selling_points_zh=[
            "电影级运镜 —— 低角度跟随、镜头光晕",
            "适合叙事型镜头与故事节奏",
        ],
        badges=["new", "flagship"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-happyhorse-1.0-t2v.html",
    ),
    ChanjingModel(
        code="happyhorse-1.0-i2v",
        name_en="HappyHorse 1.0 (I2V)",
        name_zh="HappyHorse 1.0 首帧图生视频",
        kind="video",
        modes=["i2v"],
        vendor="chanjing",
        tier="standard",
        clarity=[720, 1080],
        durations=[5, 10],
        supports_ref_image=True,
        selling_points_en=[
            "Animate a still while keeping lighting natural and stable",
            "Image-driven sibling of the HappyHorse 1.0 T2V model",
        ],
        selling_points_zh=[
            "用首帧静图驱动视频，光影自然不漂移",
            "HappyHorse 1.0 文生视频的图生视频姊妹版",
        ],
        badges=["new"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-happyhorse-1.0-i2v.html",
    ),
    ChanjingModel(
        code="Doubao-Seedance-1.0-pro",
        name_en="Doubao Seedance 1.0 Pro",
        name_zh="豆包 Seedance 1.0 Pro",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="bytedance",
        tier="pro",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        supports_ref_image=True,
        selling_points_en=[
            "ByteDance's highest-quality video tier",
            "Strong cinematography + tight prompt adherence",
            "Best-in-class for aerial / wide-shot prompts",
        ],
        selling_points_zh=[
            "字节跳动旗舰视频模型，最高质量档",
            "电影感运镜 + 强 prompt 遵循",
            "航拍 / 大全景 prompt 表现极佳",
        ],
        badges=["popular"],
        quirks_en=["model_code is case-sensitive"],
        quirks_zh=["model_code 区分大小写"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-doubao-seedance-1.0-pro.html",
    ),
    ChanjingModel(
        code="Doubao-Seedance-1.0-lite-i2v",
        name_en="Doubao Seedance 1.0 Lite (I2V)",
        name_zh="豆包 Seedance 1.0 Lite 图生视频",
        kind="video",
        modes=["i2v"],
        vendor="bytedance",
        tier="standard",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        supports_ref_image=True,
        selling_points_en=[
            "Faster + cheaper Doubao for I2V",
            "Supports first+last frame interpolation (Pro doesn't)",
            "Optimized for portrait-mode social product shots",
        ],
        selling_points_zh=[
            "更快更便宜的豆包图生视频",
            "支持首+尾帧插值（Pro 版无此功能）",
            "竖屏社媒产品镜头首选",
        ],
        quirks_en=["model_code is case-sensitive"],
        quirks_zh=["model_code 区分大小写"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-doubao-seedance-1.0-lite-i2v.html",
    ),
    ChanjingModel(
        code="kling1.6",
        name_en="Kling 1.6",
        name_zh="可灵 1.6",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="kling",
        tier="standard",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        quality_modes=["std", "pro"],
        supports_ref_image=True,
        selling_points_en=[
            "Cheapest Kling tier — broad compatibility",
            "Optional prompt (server picks defaults)",
            "Std / Pro quality switch built in",
        ],
        selling_points_zh=[
            "可灵入门档，兼容性最好",
            "Prompt 可省略 —— 服务端自动补",
            "Std / Pro 质量切换",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-kling1.6.html",
    ),
    ChanjingModel(
        code="kling-v2-1-master",
        name_en="Kling v2.1 Master",
        name_zh="可灵 v2.1 大师版",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="kling",
        tier="pro",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        quality_modes=["std", "pro"],
        supports_ref_image=True,
        selling_points_en=[
            "Kling's best-quality tier — narrative cinematography",
            "Smooth camera moves, strong storytelling feel",
        ],
        selling_points_zh=[
            "可灵质量天花板 —— 大师级叙事运镜",
            "平滑运镜 + 强故事感",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-kling-v2-1-master.html",
    ),
    ChanjingModel(
        code="kling2.5",
        name_en="Kling 2.5",
        name_zh="可灵 2.5",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="kling",
        tier="pro",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[5, 10],
        quality_modes=["std", "pro"],
        supports_ref_image=True,
        selling_points_en=[
            "Kling's newest model — fast-paced motion",
            "Future-city aesthetic, light trails, complex scenes",
        ],
        selling_points_zh=[
            "可灵最新版 —— 高速运动 + 复杂场景",
            "未来都市美学、光轨、繁复场景",
        ],
        badges=["new"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-kling2.5.html",
    ),
    ChanjingModel(
        code="MiniMax-Hailuo-02",
        name_en="MiniMax Hailuo 02",
        name_zh="海螺 02",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="minimax",
        tier="standard",
        clarity=[768, 1080],
        durations=[6, 10],
        supports_ref_image=True,
        selling_points_en=[
            "Best-in-class for natural human motion",
            "Strong camera intelligence — auto framing decisions",
        ],
        selling_points_zh=[
            "自然人物动作天花板",
            "强镜头智能 —— 自动构图决策",
        ],
        quirks_en=[
            "model_code is case-sensitive",
            "Ignores aspect_ratio and quality_mode parameters",
            "Default duration is 6 seconds (other models default to 5)",
        ],
        quirks_zh=[
            "model_code 区分大小写",
            "忽略 aspect_ratio 与 quality_mode 参数",
            "默认时长 6 秒（其他模型默认 5 秒）",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-minimax-hailuo-02.html",
    ),
    ChanjingModel(
        code="viduq1",
        name_en="Vidu Q1",
        name_zh="Vidu Q1",
        kind="video",
        modes=["t2v", "i2v"],
        vendor="vidu",
        tier="standard",
        aspect_ratios=["9:16", "16:9", "1:1"],
        clarity=[720, 1080],
        durations=[6],
        supports_ref_image=True,
        selling_points_en=[
            "Stylized / sci-fi / animation aesthetic",
            "Use when you want a different look from Kling/Doubao",
        ],
        selling_points_zh=[
            "风格化 / 科幻 / 动画美学",
            "想跳出可灵 / 豆包的视觉时首选",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/video-viduq1.html",
    ),
]


# ── Image models (creation_type = 3) ─────────────────────────────────

IMAGE_MODELS: list[ChanjingModel] = [
    ChanjingModel(
        code="doubao-seedream-3.0-t2i",
        name_en="Seedream 3.0",
        name_zh="豆包 Seedream 3.0",
        kind="image",
        modes=["t2i"],
        vendor="bytedance",
        tier="standard",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        clarity=[1024, 2048, 4096],
        selling_points_en=["Solid generic text-to-image"],
        selling_points_zh=["稳定通用的文生图"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-seedream-3.0.html",
    ),
    ChanjingModel(
        code="doubao-seedream-4.0",
        name_en="Seedream 4.0",
        name_zh="豆包 Seedream 4.0",
        kind="image",
        modes=["t2i", "i2i"],
        vendor="bytedance",
        tier="standard",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        clarity=[1024, 2048, 4096],
        supports_ref_image=True,
        selling_points_en=[
            "Reference-image support over 3.0",
            "Style transfer + character consistency",
        ],
        selling_points_zh=[
            "在 3.0 基础上加参考图能力",
            "风格迁移 + 角色一致性",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-seedream-4.0.html",
    ),
    ChanjingModel(
        code="doubao-seedream-4.5",
        name_en="Seedream 4.5",
        name_zh="豆包 Seedream 4.5",
        kind="image",
        modes=["t2i", "i2i"],
        vendor="bytedance",
        tier="pro",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        clarity=[2048, 4096],
        supports_ref_image=True,
        selling_points_en=[
            "Best Doubao image quality — native 4K",
            "Complex multi-element scene composition",
        ],
        selling_points_zh=[
            "豆包图像最高画质 —— 原生 4K",
            "复杂多元素场景构图",
        ],
        badges=["new"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-seedream-4.5.html",
    ),
    ChanjingModel(
        code="kling-v2",
        name_en="Kling v2 (image)",
        name_zh="可灵 2.0 图片",
        kind="image",
        modes=["t2i", "i2i"],
        vendor="kling",
        tier="standard",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        clarity=[1024, 2048],
        supports_ref_image=True,
        selling_points_en=[
            "Commercial-photography aesthetic",
            "Minimal product posters, soft shadows",
        ],
        selling_points_zh=[
            "商业摄影美学",
            "极简产品海报、柔和阴影",
        ],
        quirks_en=["Up to 1 reference image"],
        quirks_zh=["最多 1 张参考图"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-kling-v2.html",
    ),
    ChanjingModel(
        code="kling-v2-1",
        name_en="Kling v2.1 (image)",
        name_zh="可灵 2.1 图片",
        kind="image",
        modes=["t2i"],
        vendor="kling",
        tier="pro",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        clarity=[1024, 2048],
        supports_ref_image=False,
        selling_points_en=[
            "Newest Kling image — fashion / magazine-cover aesthetic",
            "Soft light, editorial composition",
        ],
        selling_points_zh=[
            "可灵最新图像 —— 时尚 / 杂志封面美学",
            "柔光、编辑级构图",
        ],
        badges=["new"],
        quirks_en=["Reference images not supported (T2I only)"],
        quirks_zh=["不支持参考图（仅 T2I）"],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-kling-v2-1.html",
    ),
    ChanjingModel(
        code="wan2.2-t2i",
        name_en="Wan 2.2",
        name_zh="Wan 2.2 文生图",
        kind="image",
        modes=["t2i"],
        vendor="alibaba",
        tier="variable",
        aspect_ratios=["1:1", "3:4", "4:3", "9:16", "16:9"],
        quality_modes=["std", "pro"],
        selling_points_en=[
            "Material realism — interior / architectural renders",
            "Std / Pro quality switch (cheap vs premium)",
        ],
        selling_points_zh=[
            "材质真实感 —— 室内 / 建筑渲染",
            "Std / Pro 质量切换（性价比 vs 高画质）",
        ],
        doc_url="https://doc.chanjing.cc/api/ai-creation/pic-wan2.2-t2i.html",
    ),
]


# ── Convenience accessors ────────────────────────────────────────────


def all_chanjing_models() -> list[ChanjingModel]:
    return [*VIDEO_MODELS, *IMAGE_MODELS]


def get_chanjing_model(code: str) -> ChanjingModel | None:
    for m in all_chanjing_models():
        if m.code == code:
            return m
    return None
