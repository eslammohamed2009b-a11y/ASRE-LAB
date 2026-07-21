"""
Durable file storage abstraction (Module 1 production hardening).

Replaces raw filesystem-path handling in `app.module1_design.router` with a
proper interface that can be backed by the local filesystem (development /
CI, no external dependency) or Supabase Storage (production), without the
route/task layer ever touching a path or bucket directly.

Object keys are ALWAYS generated server-side (never accepted from a
client) and always namespaced per user/experiment/design:

    users/{user_id}/experiments/{experiment_id}/designs/{design_id}/{filename}

This is the only interface allowed to read/write exported design files.
Nothing else in the codebase should build a storage path/key by hand.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from app.core.config import settings

logger = logging.getLogger(__name__)

# users/{id}/experiments/{id}/designs/{id}/{safe filename}
# Identifiers are typically uuids in production (Supabase user id, generated
# experiment/design ids) but are not strictly required to be - JWT `sub`
# claims used in tests/dev (e.g. "user-test") are plain safe strings, so this
# accepts any safe identifier shape rather than hex-only.
_OBJECT_KEY_PATTERN = re.compile(
    r"^users/[A-Za-z0-9_-]{1,128}/experiments/[A-Za-z0-9_-]{1,128}/"
    r"(?:designs|simulations)/[A-Za-z0-9_-]{1,128}/"
    r"[A-Za-z0-9._-]{1,255}$"
)


class StorageError(Exception):
    """Raised for any storage-backend failure. Never leaks backend-specific
    internals (paths, credentials, provider stack traces) to callers - the
    message must always be safe to show to a client or log."""


def build_object_key(user_id: str, experiment_id: str, design_id: str, filename: str) -> str:
    """The ONLY place object keys are constructed. Never accept a
    caller/client-supplied key directly - always rebuild it from validated
    identifiers plus a sanitized filename."""
    # Path(...).name discards any directory components (e.g. "../../etc/passwd"
    # -> "passwd") before further sanitizing, so path traversal segments in a
    # supplied filename can never survive into the constructed key.
    base_name = Path(filename).name or "file"
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", base_name).replace("..", "_").lstrip(".")
    if not safe_filename:
        safe_filename = "file"
    key = f"users/{user_id}/experiments/{experiment_id}/designs/{design_id}/{safe_filename}"
    if not _OBJECT_KEY_PATTERN.match(key):
        # user_id/experiment_id/design_id are expected to be safe identifiers
        # already validated upstream; this is a fail-closed backstop.
        raise StorageError("Refusing to build an unsafe storage object key")
    return key


def build_simulation_object_key(
    user_id: str, experiment_id: str, simulation_id: str, filename: str
) -> str:
    """Build a server-owned key for a simulation result artifact."""
    base_name = Path(filename).name or "field-result.npz"
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", base_name).replace("..", "_").lstrip(".")
    if not safe_filename:
        safe_filename = "field-result.npz"
    key = (
        f"users/{user_id}/experiments/{experiment_id}/simulations/"
        f"{simulation_id}/{safe_filename}"
    )
    if not _OBJECT_KEY_PATTERN.match(key):
        raise StorageError("Refusing to build an unsafe storage object key")
    return key


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileStorage(ABC):
    """Durable storage for generated design files (STL/STEP/etc.)."""

    def validate_object_key(self, object_key: str) -> None:
        """Fail closed on anything that isn't a well-formed, namespaced key.
        Blocks path traversal (`..`), absolute paths, and free-form keys."""
        if ".." in object_key or object_key.startswith("/") or "\\" in object_key:
            raise StorageError("Invalid object key")
        if not _OBJECT_KEY_PATTERN.match(object_key):
            raise StorageError("Invalid object key")

    @abstractmethod
    def save_file(self, object_key: str, source_path: Path) -> None:
        """Copy/upload the local file at `source_path` into durable storage
        under `object_key`. Must not leave a partial object on failure."""

    @abstractmethod
    def file_exists(self, object_key: str) -> bool:
        ...

    @abstractmethod
    def open_bytes(self, object_key: str) -> bytes:
        """Read the full contents of a stored object. Raises StorageError
        (not a raw backend exception) if missing/unreadable."""

    @abstractmethod
    def delete_file(self, object_key: str) -> None:
        """Best-effort delete; must not raise if the object is already gone."""

    @abstractmethod
    def create_download_response(self, object_key: str, download_filename: str, media_type: str) -> Response:
        """Return a FastAPI response that streams the object back to an
        already-authorized, already-ownership-checked caller. Must never
        expose a public/unauthenticated URL."""

    def calculate_checksum(self, source_path: Path) -> str:
        return sha256_of_file(source_path)


class LocalFileStorage(FileStorage):
    """Filesystem-backed storage rooted at a fixed directory. Used in
    development, CI, and whenever Supabase Storage is not configured."""

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_within_root(self, object_key: str) -> Path:
        self.validate_object_key(object_key)
        target = (self.root_dir / object_key).resolve()
        try:
            target.relative_to(self.root_dir)
        except ValueError as exc:
            raise StorageError("Invalid object key") from exc
        return target

    def save_file(self, object_key: str, source_path: Path) -> None:
        target = self._resolve_within_root(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".upload-", suffix=".tmp")
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            tmp_path.write_bytes(source_path.read_bytes())
            os.replace(tmp_path, target)  # atomic on the same filesystem
        except OSError as exc:
            tmp_path.unlink(missing_ok=True)
            raise StorageError("Failed to persist file to local storage") from exc

    def file_exists(self, object_key: str) -> bool:
        try:
            target = self._resolve_within_root(object_key)
        except StorageError:
            return False
        return target.is_file()

    def open_bytes(self, object_key: str) -> bytes:
        target = self._resolve_within_root(object_key)
        if not target.is_file():
            raise StorageError("Object not found")
        return target.read_bytes()

    def delete_file(self, object_key: str) -> None:
        try:
            target = self._resolve_within_root(object_key)
        except StorageError:
            return
        target.unlink(missing_ok=True)

    def create_download_response(self, object_key: str, download_filename: str, media_type: str) -> Response:
        target = self._resolve_within_root(object_key)
        if not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        return FileResponse(path=target, filename=download_filename, media_type=media_type)


class SupabaseStorage(FileStorage):
    """Supabase Storage-backed implementation. Always streams bytes through
    the backend (never returns a public/unauthenticated URL) so ownership
    checks made before this is called remain the sole gate to file access."""

    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    def _bucket_api(self):
        return self._client.storage.from_(self._bucket)

    def save_file(self, object_key: str, source_path: Path) -> None:
        self.validate_object_key(object_key)
        try:
            data = source_path.read_bytes()
            self._bucket_api().upload(
                object_key,
                data,
                {"content-type": "application/octet-stream", "upsert": "true"},
            )
        except Exception as exc:  # pragma: no cover - depends on live Supabase
            logger.error("Supabase Storage upload failed for a design file", exc_info=True)
            raise StorageError("Failed to upload file to remote storage") from exc

    def file_exists(self, object_key: str) -> bool:
        self.validate_object_key(object_key)
        try:
            self.open_bytes(object_key)
            return True
        except StorageError:
            return False

    def open_bytes(self, object_key: str) -> bytes:
        self.validate_object_key(object_key)
        try:
            return self._bucket_api().download(object_key)
        except Exception as exc:  # pragma: no cover - depends on live Supabase
            raise StorageError("Object not found") from exc

    def delete_file(self, object_key: str) -> None:
        try:
            self.validate_object_key(object_key)
            self._bucket_api().remove([object_key])
        except Exception:  # pragma: no cover - depends on live Supabase
            logger.warning("Supabase Storage delete failed (best-effort)", exc_info=True)

    def create_download_response(self, object_key: str, download_filename: str, media_type: str) -> Response:
        data = self.open_bytes(object_key)
        headers = {"Content-Disposition": f'attachment; filename="{download_filename}"'}
        return Response(content=data, media_type=media_type, headers=headers)


def default_local_storage_root() -> Path:
    if settings.LOCAL_STORAGE_ROOT:
        return Path(settings.LOCAL_STORAGE_ROOT)
    return Path(tempfile.gettempdir()) / "asre_lab_storage"


def get_storage() -> FileStorage:
    """Factory mirroring `app.core.repository.get_repository()`: resolves
    fresh each call (no caching) so tests can flip env vars between calls
    for isolation, and so a missing/misconfigured Supabase client falls
    back to local storage rather than crashing the request path."""
    from app.core.persistence import persistence_service  # local import: avoid cycles

    if persistence_service.enabled and getattr(persistence_service, "client", None) is not None:
        return SupabaseStorage(persistence_service.client, settings.SUPABASE_STORAGE_BUCKET)
    return LocalFileStorage(default_local_storage_root())


def new_design_id() -> str:
    return str(uuid.uuid4())
