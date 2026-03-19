import json
import posixpath
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings


class ViteManifestError(RuntimeError):
    """Base error for problems resolving Vite assets."""


class ViteManifestNotFound(ViteManifestError):
    pass


class ViteAssetNotFound(ViteManifestError):
    pass


class ViteAssetReleaseNotFound(ViteManifestError):
    pass


@dataclass(frozen=True)
class ViteAsset:
    scripts: tuple[str, ...]
    styles: tuple[str, ...]
    inline_modules: tuple[str, ...] = ()


def _static_url(relative_path: str) -> str:
    base = settings.STATIC_URL.rstrip('/')
    joined = posixpath.join(base or '/', 'frontend', relative_path)
    return joined if joined.startswith('/') else f'/{joined}'


def _release_asset_url(relative_path: str) -> str:
    base_url = settings.VITE_ASSET_BASE_URL.strip().rstrip('/')
    if not base_url:
        return _static_url(relative_path)

    release_id = _get_release_id()
    return f"{base_url}/{release_id}/{relative_path.lstrip('/')}"


def _read_release_id() -> str:
    explicit_release_id = settings.VITE_ASSET_RELEASE_ID.strip()
    if explicit_release_id:
        return explicit_release_id

    release_file: Path = settings.VITE_ASSET_RELEASE_ID_FILE
    if release_file.exists():
        return release_file.read_text(encoding='utf-8').strip()

    return ""


@lru_cache(maxsize=1)
def _get_release_id() -> str:
    release_file: Path = settings.VITE_ASSET_RELEASE_ID_FILE
    release_id = _read_release_id()
    if release_id and release_id.lower() != "unknown":
        return release_id

    message = (
        "Vite asset release ID is required when VITE_ASSET_BASE_URL is configured. "
        f"Checked VITE_ASSET_RELEASE_ID and {release_file}."
    )
    if settings.DEBUG:
        return ""

    raise ViteAssetReleaseNotFound(message)


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
    _get_release_id.cache_clear()


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

    file_path = chunk['file']
    css_paths = tuple(chunk.get('css', []))

    url_builder = _static_url
    if settings.VITE_ASSET_BASE_URL.strip():
        release_id = _get_release_id()
        if release_id:
            url_builder = _release_asset_url

    file_url = url_builder(file_path)
    css_urls = tuple(url_builder(path) for path in css_paths)

    return ViteAsset(scripts=(file_url,), styles=css_urls)
