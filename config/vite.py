from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Tuple

from django.conf import settings


class ViteManifestError(RuntimeError):
    """Base error for problems resolving Vite assets."""


class ViteManifestNotFound(ViteManifestError):
    pass


class ViteAssetNotFound(ViteManifestError):
    pass


@dataclass(frozen=True)
class ViteAsset:
    scripts: Tuple[str, ...]
    styles: Tuple[str, ...]
    inline_modules: Tuple[str, ...] = ()


def _static_url(relative_path: str) -> str:
    base = settings.STATIC_URL.rstrip('/')
    joined = posixpath.join(base or '/', 'frontend', relative_path)
    return joined if joined.startswith('/') else f'/{joined}'


@lru_cache(maxsize=1)
def _load_manifest() -> dict[str, dict]:
    manifest_path: Path = settings.VITE_MANIFEST_PATH
    if not manifest_path.exists():
        raise ViteManifestNotFound(
            f"Vite manifest not found at {manifest_path}. Run `npm run build` in the frontend directory."
        )

    with manifest_path.open('r', encoding='utf-8') as manifest_file:
        return json.load(manifest_file)


def clear_manifest_cache() -> None:
    _load_manifest.cache_clear()


def get_vite_asset(entry: str | None = None) -> ViteAsset:
    entry_point = entry or settings.VITE_ASSET_ENTRY

    if settings.VITE_USE_DEV_SERVER:
        origin = settings.VITE_DEV_SERVER_URL.rstrip('/')
        preamble = (
            f"import RefreshRuntime from '{origin}/@react-refresh';\n"
            "RefreshRuntime.injectIntoGlobalHook(window);\n"
            "window.$RefreshReg$ = () => {};\n"
            "window.$RefreshSig$ = () => (type) => type;\n"
            "window.__vite_plugin_react_preamble_installed__ = true;\n"
        )

        return ViteAsset(
            scripts=(
                f"{origin}/@vite/client",
                f"{origin}/{entry_point.lstrip('/')}",
            ),
            styles=(),
            inline_modules=(preamble,),
        )

    manifest = _load_manifest()

    try:
        chunk = manifest[entry_point]
    except KeyError as exc:
        raise ViteAssetNotFound(f"No manifest entry for {entry_point}") from exc

    file_url = _static_url(chunk['file'])
    css_urls = tuple(_static_url(path) for path in chunk.get('css', []))

    return ViteAsset(scripts=(file_url,), styles=css_urls)
