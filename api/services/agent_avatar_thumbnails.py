import io

from botocore.exceptions import ClientError
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from google.api_core.exceptions import NotFound
from PIL import Image, ImageOps, UnidentifiedImageError


AGENT_AVATAR_THUMBNAIL_SIZE = 128
AGENT_AVATAR_THUMBNAIL_CONTENT_TYPE = "image/png"


class AvatarThumbnailUnavailable(Exception):
    pass


def _is_missing_s3_object(exc: ClientError) -> bool:
    code = str(exc.response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}


def agent_avatar_thumbnail_name(agent_id, avatar_version: str) -> str:
    return f"agent_avatar_thumbnails/{agent_id}/{avatar_version}.png"


def generate_agent_avatar_thumbnail(storage, original_name: str, thumbnail_name: str) -> None:
    try:
        with storage.open(original_name, "rb") as original_file:
            with Image.open(original_file) as image:
                image = ImageOps.exif_transpose(image)
                thumbnail = ImageOps.fit(
                    image,
                    (AGENT_AVATAR_THUMBNAIL_SIZE, AGENT_AVATAR_THUMBNAIL_SIZE),
                    method=Image.Resampling.LANCZOS,
                )
                output = io.BytesIO()
                thumbnail.convert("RGBA").save(output, format="PNG", optimize=True)
    except (FileNotFoundError, OSError, NotFound, UnidentifiedImageError) as exc:
        raise AvatarThumbnailUnavailable("Avatar not found.") from exc
    except ClientError as exc:
        if not _is_missing_s3_object(exc):
            raise
        raise AvatarThumbnailUnavailable("Avatar not found.") from exc

    saved_name = default_storage.save(thumbnail_name, ContentFile(output.getvalue()))
    if saved_name != thumbnail_name:
        try:
            default_storage.delete(saved_name)
        except OSError:
            pass


def open_agent_avatar_thumbnail(agent):
    file_field = getattr(agent, "avatar", None)
    if not file_field or not getattr(file_field, "name", None):
        raise AvatarThumbnailUnavailable("Avatar not found.")

    avatar_version = agent.get_avatar_version()
    if not avatar_version:
        raise AvatarThumbnailUnavailable("Avatar not found.")
    thumbnail_version = agent.get_avatar_thumbnail_version() or avatar_version
    thumbnail_name = agent_avatar_thumbnail_name(agent.id, thumbnail_version)
    try:
        return default_storage.open(thumbnail_name, "rb")
    except (FileNotFoundError, OSError, NotFound):
        generate_agent_avatar_thumbnail(file_field.storage, file_field.name, thumbnail_name)
    except ClientError as exc:
        if not _is_missing_s3_object(exc):
            raise
        generate_agent_avatar_thumbnail(file_field.storage, file_field.name, thumbnail_name)
    try:
        return default_storage.open(thumbnail_name, "rb")
    except (FileNotFoundError, OSError, NotFound) as exc:
        raise AvatarThumbnailUnavailable("Avatar thumbnail not found.") from exc
    except ClientError as exc:
        if not _is_missing_s3_object(exc):
            raise
        raise AvatarThumbnailUnavailable("Avatar thumbnail not found.") from exc
