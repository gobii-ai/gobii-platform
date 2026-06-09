import logging
from collections.abc import Mapping
from typing import Any

from django.core.files.storage import Storage
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class ReadThroughStorage(Storage):
    """
    Storage overlay for forked non-prod databases.

    Reads prefer the primary storage and fall back to a read-only source storage.
    Writes, name allocation, and deletes only use the primary storage so non-prod
    can read forked prod references without mutating prod objects.
    """

    def __init__(
        self,
        *,
        primary: Storage | Mapping[str, Any],
        fallback: Storage | Mapping[str, Any],
        fallback_url_enabled: bool = False,
    ) -> None:
        self.primary_storage = self._build_storage(primary)
        self.fallback_storage = self._build_storage(fallback)
        self.fallback_url_enabled = fallback_url_enabled

    @staticmethod
    def _build_storage(config: Storage | Mapping[str, Any]) -> Storage:
        if isinstance(config, Storage):
            return config
        if not isinstance(config, Mapping):
            raise TypeError("Storage config must be a Storage instance or mapping.")
        backend_path = config.get("BACKEND")
        if not backend_path:
            raise ValueError("Storage config requires BACKEND.")
        options = dict(config.get("OPTIONS") or {})
        backend = import_string(str(backend_path))
        return backend(**options)

    @staticmethod
    def _is_write_mode(mode: str) -> bool:
        return any(flag in mode for flag in ("w", "a", "x", "+"))

    def _storage_for_read(self, name: str) -> Storage:
        if self.primary_storage.exists(name):
            return self.primary_storage
        if self.fallback_storage.exists(name):
            logger.debug("Reading %s from fallback media storage.", name)
            return self.fallback_storage
        return self.primary_storage

    def _open(self, name: str, mode: str = "rb"):
        if self._is_write_mode(mode):
            return self.primary_storage.open(name, mode)
        return self._storage_for_read(name).open(name, mode)

    def save(self, name, content, max_length=None):
        return self.primary_storage.save(name, content, max_length=max_length)

    def delete(self, name):
        if self.primary_storage.exists(name):
            return self.primary_storage.delete(name)
        return None

    def exists(self, name: str) -> bool:
        return self.primary_storage.exists(name) or self.fallback_storage.exists(name)

    def listdir(self, path: str):
        return self.primary_storage.listdir(path)

    def size(self, name: str) -> int:
        return self._storage_for_read(name).size(name)

    def url(self, name: str) -> str:
        storage = self._storage_for_read(name)
        if storage is self.fallback_storage and not self.fallback_url_enabled:
            return self.primary_storage.url(name)
        return storage.url(name)

    def path(self, name: str) -> str:
        return self._storage_for_read(name).path(name)

    def get_accessed_time(self, name: str):
        return self._storage_for_read(name).get_accessed_time(name)

    def get_created_time(self, name: str):
        return self._storage_for_read(name).get_created_time(name)

    def get_modified_time(self, name: str):
        return self._storage_for_read(name).get_modified_time(name)

    def get_valid_name(self, name: str) -> str:
        return self.primary_storage.get_valid_name(name)

    def get_available_name(self, name: str, max_length=None) -> str:
        return self.primary_storage.get_available_name(name, max_length=max_length)

    def get_alternative_name(self, file_root: str, file_ext: str) -> str:
        return self.primary_storage.get_alternative_name(file_root, file_ext)

    def generate_filename(self, filename: str) -> str:
        return self.primary_storage.generate_filename(filename)

    def is_name_available(self, name: str, max_length=None) -> bool:
        return self.primary_storage.is_name_available(name, max_length=max_length)

    def __getattr__(self, name: str):
        return getattr(self.primary_storage, name)
