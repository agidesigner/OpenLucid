API_VERSION = "v1"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

SUPPORTED_LOCALES = ("zh-CN", "en-US")

CONTENT_SUBJECT_TAGS = frozenset({
    "product", "person", "scene", "testimonial", "packaging",
    "talking_head", "broll", "before_after", "ui_demo",
})

USAGE_TAGS = frozenset({
    "hook", "proof", "explanation", "trust", "cta", "transition",
})

CHANNEL_TAGS = frozenset({
    "douyin", "xiaohongshu", "kuaishou", "video_account", "general",
})
