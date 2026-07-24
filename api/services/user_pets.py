import uuid
from io import BytesIO

from PIL import Image, UnidentifiedImageError


PET_FRAME_WIDTH = 192
PET_FRAME_HEIGHT = 208
PET_COLUMNS = 8
PET_ROWS = 11
PET_SPRITESHEET_WIDTH = PET_FRAME_WIDTH * PET_COLUMNS
PET_SPRITESHEET_HEIGHT = PET_FRAME_HEIGHT * PET_ROWS
# V2 reserves row 0, column 6 as the dedicated neutral/front look frame.
PET_USED_COLUMNS_BY_ROW = (7, 8, 8, 4, 5, 8, 6, 6, 6, 8, 8)
PET_MIN_VISIBLE_PIXELS = 50
DEFAULT_BUILTIN_PET_ID = "builtin:gobii-fish"
BUILTIN_PET_IDS = frozenset({DEFAULT_BUILTIN_PET_ID, "builtin:eevee"})


class UserPetValidationError(ValueError):
    pass


def normalize_user_pet_selector(key: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid value for '{key}'. Expected a pet identifier.")
    normalized_value = value.strip()
    if normalized_value in BUILTIN_PET_IDS:
        return normalized_value
    try:
        return str(uuid.UUID(normalized_value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid value for '{key}'. Expected a pet identifier.") from exc


def normalize_user_pet_position(key: str, value: object) -> dict[str, float] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise ValueError(f"Invalid value for '{key}'. Expected normalized x and y coordinates.")
    normalized = {}
    for axis in ("x", "y"):
        coordinate = value[axis]
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise ValueError(f"Invalid value for '{key}'. Expected normalized x and y coordinates.")
        if not 0.0 <= (coordinate := float(coordinate)) <= 1.0:
            raise ValueError(f"Invalid value for '{key}'. Coordinates must be between 0 and 1.")
        normalized[axis] = round(coordinate, 6)
    return normalized


def validate_user_pet_spritesheet(uploaded_file, *, max_bytes: int) -> bytes:
    if uploaded_file.size > max_bytes:
        raise UserPetValidationError(f"Pet spritesheets must be {max_bytes // (1024 * 1024)} MB or smaller.")

    try:
        uploaded_file.seek(0)
        payload = uploaded_file.read(max_bytes + 1)
    except OSError as exc:
        raise UserPetValidationError("Unable to read the uploaded pet spritesheet.") from exc
    if len(payload) > max_bytes:
        raise UserPetValidationError(f"Pet spritesheets must be {max_bytes // (1024 * 1024)} MB or smaller.")

    try:
        with Image.open(BytesIO(payload)) as image:
            if image.format != "WEBP":
                raise UserPetValidationError("Pet spritesheets must be WebP images.")
            if image.size != (PET_SPRITESHEET_WIDTH, PET_SPRITESHEET_HEIGHT):
                raise UserPetValidationError(
                    f"Pet spritesheets must be exactly {PET_SPRITESHEET_WIDTH}x{PET_SPRITESHEET_HEIGHT} pixels."
                )
            if getattr(image, "n_frames", 1) != 1:
                raise UserPetValidationError("Animated WebP files are not supported.")
            rgba = image.convert("RGBA")
            rgba.load()
    except UserPetValidationError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
        raise UserPetValidationError("The uploaded file is not a valid WebP pet spritesheet.") from exc

    alpha = rgba.getchannel("A")
    for row, used_columns in enumerate(PET_USED_COLUMNS_BY_ROW):
        for column in range(PET_COLUMNS):
            bounds = (
                column * PET_FRAME_WIDTH,
                row * PET_FRAME_HEIGHT,
                (column + 1) * PET_FRAME_WIDTH,
                (row + 1) * PET_FRAME_HEIGHT,
            )
            cell_alpha = alpha.crop(bounds)
            visible_pixels = sum(cell_alpha.histogram()[1:])
            if column < used_columns and visible_pixels < PET_MIN_VISIBLE_PIXELS:
                raise UserPetValidationError(
                    f"Required pet frame at row {row}, column {column} is empty or too sparse."
                )
            if column >= used_columns and visible_pixels:
                raise UserPetValidationError(
                    f"Unused pet frame at row {row}, column {column} must be transparent."
                )

    return payload
