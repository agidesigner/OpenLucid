import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

import aiofiles

from app.config import settings


class StorageAdapter(ABC):
    @abstractmethod
    async def save_file(self, file_content: bytes, file_name: str, sub_path: str = "") -> str:
        """Save file and return storage URI."""

    @abstractmethod
    async def get_file(self, storage_uri: str) -> bytes:
        """Read file content by storage URI."""

    @abstractmethod
    async def delete_file(self, storage_uri: str) -> None:
        """Delete file by storage URI."""

    @abstractmethod
    def get_url(self, storage_uri: str) -> str:
        """Get accessible URL for the file."""

    @abstractmethod
    def get_absolute_path(self, storage_uri: str) -> str:
        """Get absolute filesystem path for the file."""


class LocalStorageAdapter(StorageAdapter):
    def __init__(self, base_path: str | None = None):
        self.base_path = Path(base_path or settings.STORAGE_BASE_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)

    async def save_file(self, file_content: bytes, file_name: str, sub_path: str = "") -> str:
        unique_name = f"{uuid.uuid4().hex}_{file_name}"
        dir_path = self.base_path / sub_path
        dir_path.mkdir(parents=True, exist_ok=True)
        file_path = dir_path / unique_name
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(file_content)
        return str(file_path.relative_to(self.base_path))

    async def get_file(self, storage_uri: str) -> bytes:
        file_path = self.base_path / storage_uri
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def delete_file(self, storage_uri: str) -> None:
        file_path = self.base_path / storage_uri
        if file_path.exists():
            os.remove(file_path)

    def get_url(self, storage_uri: str) -> str:
        return f"/files/{storage_uri}"

    def get_absolute_path(self, storage_uri: str) -> str:
        return str((self.base_path / storage_uri).resolve())
