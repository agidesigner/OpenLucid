"""Video generation provider adapters (Chanjing, Jogg, ...).

All providers implement the VideoProvider Protocol from `base.py`.
Use `factory.get_video_provider(name, credentials)` to get an instance.
"""

from app.adapters.video.base import (
    Avatar,
    AspectRatio,
    CreateVideoRequest,
    JobStatus,
    Voice,
    VideoProvider,
    VideoStatus,
)
from app.adapters.video.factory import get_video_provider

__all__ = [
    "Avatar",
    "AspectRatio",
    "CreateVideoRequest",
    "JobStatus",
    "Voice",
    "VideoProvider",
    "VideoStatus",
    "get_video_provider",
]
