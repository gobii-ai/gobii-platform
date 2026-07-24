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


class UserPetValidationError(ValueError):
    pass


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
