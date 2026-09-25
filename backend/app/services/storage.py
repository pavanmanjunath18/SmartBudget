"""Where uploaded files are kept.

Behind a tiny interface so the local-disk version can be swapped for S3 on AWS
without touching the import logic.
"""

from pathlib import Path
from typing import Protocol

from app.core.config import get_settings


class FileStorage(Protocol):
    def save(self, key: str, content: bytes) -> None:
        """Store `content` under `key` (e.g. "org-3/imports/<sha>.csv")."""
        ...


class LocalFileStorage:
    """Writes files under a base directory. Used for local development and tests."""

    def __init__(self, base_dir: str) -> None:
        self.base_dir = Path(base_dir)

    def save(self, key: str, content: bytes) -> None:
        path = (self.base_dir / key).resolve()
        if not path.is_relative_to(self.base_dir.resolve()):
            raise ValueError("Storage key escapes the upload directory.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def get_storage() -> FileStorage:
    """FastAPI dependency returning the configured storage backend."""
    return LocalFileStorage(get_settings().upload_dir)
