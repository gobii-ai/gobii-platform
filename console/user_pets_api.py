import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, HttpRequest, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404
from django.templatetags.static import static
from django.urls import reverse
from django.views import View

from api.models import UserPet, UserPreference
from api.services.user_pets import UserPetValidationError, validate_user_pet_spritesheet
from console.api_helpers import ApiLoginRequiredMixin, _parse_json_body


BUILTIN_PET_ID = "builtin:gobii-fish"


def _owned_pet_exists(user, pet_id: object) -> bool:
    if not isinstance(pet_id, str):
        return False
    try:
        normalized_id = uuid.UUID(pet_id.strip())
    except (ValueError, AttributeError):
        return False
    return UserPet.objects.filter(user=user, pk=normalized_id).exists()


def _serialize_pet(pet: UserPet) -> dict[str, object]:
    return {
        "id": str(pet.id),
        "kind": "custom",
        "displayName": pet.display_name,
        "description": pet.description,
        "spritesheetUrl": reverse("console_user_pet_spritesheet", kwargs={"pet_id": pet.id}),
    }


def _resolved_pet_preferences(user) -> dict[str, object]:
    preferences = UserPreference.resolve_known_preferences(user)
    selected_id = preferences[UserPreference.KEY_USER_PET_SELECTED_ID]
    if selected_id != BUILTIN_PET_ID and not _owned_pet_exists(user, selected_id):
        selected_id = BUILTIN_PET_ID
    return {
        "enabled": preferences[UserPreference.KEY_USER_PET_ENABLED],
        "selectedPetId": selected_id,
        "size": preferences[UserPreference.KEY_USER_PET_SIZE],
        "position": preferences[UserPreference.KEY_USER_PET_POSITION],
    }


def _serialize_pet_library(user) -> dict[str, object]:
    pets = [
        {
            "id": BUILTIN_PET_ID,
            "kind": "builtin",
            "displayName": "Pixel Gobii Fish",
            "description": "A cheerful pixel-art purple robot fish.",
            "spritesheetUrl": static("images/pets/gobii-fish-v2.webp"),
        },
        *[_serialize_pet(pet) for pet in UserPet.objects.filter(user=user)],
    ]
    return {
        "pets": pets,
        "preferences": _resolved_pet_preferences(user),
        "maxCustomPets": settings.USER_PET_MAX_CUSTOM_PETS,
    }


class UserPetListAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get", "post", "patch"]

    def get(self, request: HttpRequest, *args, **kwargs):
        return JsonResponse(_serialize_pet_library(request.user))

    @transaction.atomic
    def post(self, request: HttpRequest, *args, **kwargs):
        user = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if UserPet.objects.filter(user=user).count() >= settings.USER_PET_MAX_CUSTOM_PETS:
            return JsonResponse(
                {"error": f"You can keep up to {settings.USER_PET_MAX_CUSTOM_PETS} custom pets."},
                status=400,
            )

        spritesheet = request.FILES.get("spritesheet")
        if spritesheet is None:
            return JsonResponse({"error": "Choose a v2 WebP spritesheet to upload."}, status=400)

        display_name = (request.POST.get("displayName") or "").strip()
        description = (request.POST.get("description") or "").strip()
        if not display_name:
            return JsonResponse({"error": "Pet name is required."}, status=400)
        if len(display_name) > UserPet._meta.get_field("display_name").max_length:
            return JsonResponse({"error": "Pet name must be 80 characters or fewer."}, status=400)
        if len(description) > UserPet._meta.get_field("description").max_length:
            return JsonResponse({"error": "Pet description must be 240 characters or fewer."}, status=400)

        try:
            payload = validate_user_pet_spritesheet(
                spritesheet,
                max_bytes=settings.USER_PET_MAX_UPLOAD_BYTES,
            )
        except UserPetValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        pet = UserPet(user=user, display_name=display_name, description=description)
        pet.spritesheet.save("spritesheet.webp", ContentFile(payload), save=True)
        UserPreference.update_known_preferences(
            user,
            {
                UserPreference.KEY_USER_PET_ENABLED: True,
                UserPreference.KEY_USER_PET_SELECTED_ID: str(pet.id),
            },
        )
        return JsonResponse(_serialize_pet_library(user), status=201)

    def patch(self, request: HttpRequest, *args, **kwargs):
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("JSON body must be an object.")

        allowed_fields = {"enabled", "selectedPetId", "size", "position"}
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            return HttpResponseBadRequest(f"Unknown fields: {', '.join(unknown_fields)}")
        if not payload:
            return HttpResponseBadRequest("Provide at least one pet preference.")

        updates = {}
        field_map = {
            "enabled": UserPreference.KEY_USER_PET_ENABLED,
            "selectedPetId": UserPreference.KEY_USER_PET_SELECTED_ID,
            "size": UserPreference.KEY_USER_PET_SIZE,
            "position": UserPreference.KEY_USER_PET_POSITION,
        }
        if "selectedPetId" in payload:
            selected_id = payload["selectedPetId"]
            if selected_id != BUILTIN_PET_ID and not _owned_pet_exists(request.user, selected_id):
                return JsonResponse({"error": "Select a pet from your library."}, status=400)
        for field, preference_key in field_map.items():
            if field in payload:
                updates[preference_key] = payload[field]
        try:
            UserPreference.update_known_preferences(request.user, updates)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        return JsonResponse(_serialize_pet_library(request.user))


class UserPetDetailAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["patch", "delete"]

    def patch(self, request: HttpRequest, pet_id, *args, **kwargs):
        pet = get_object_or_404(UserPet, pk=pet_id, user=request.user)
        try:
            payload = _parse_json_body(request)
        except ValueError as exc:
            return HttpResponseBadRequest(str(exc))
        if not isinstance(payload, dict):
            return HttpResponseBadRequest("JSON body must be an object.")
        unknown_fields = sorted(set(payload) - {"displayName", "description"})
        if unknown_fields:
            return HttpResponseBadRequest(f"Unknown fields: {', '.join(unknown_fields)}")

        update_fields = []
        if "displayName" in payload:
            display_name = payload["displayName"]
            if not isinstance(display_name, str) or not display_name.strip():
                return JsonResponse({"error": "Pet name is required."}, status=400)
            display_name = display_name.strip()
            if len(display_name) > UserPet._meta.get_field("display_name").max_length:
                return JsonResponse({"error": "Pet name must be 80 characters or fewer."}, status=400)
            pet.display_name = display_name
            update_fields.append("display_name")
        if "description" in payload:
            description = payload["description"]
            if not isinstance(description, str):
                return JsonResponse({"error": "Pet description must be text."}, status=400)
            description = description.strip()
            if len(description) > UserPet._meta.get_field("description").max_length:
                return JsonResponse({"error": "Pet description must be 240 characters or fewer."}, status=400)
            pet.description = description
            update_fields.append("description")
        if update_fields:
            pet.save(update_fields=[*update_fields, "updated_at"])
        return JsonResponse(_serialize_pet_library(request.user))

    @transaction.atomic
    def delete(self, request: HttpRequest, pet_id, *args, **kwargs):
        pet = get_object_or_404(UserPet, pk=pet_id, user=request.user)
        preferences = UserPreference.resolve_known_preferences(request.user)
        if preferences[UserPreference.KEY_USER_PET_SELECTED_ID] == str(pet.id):
            UserPreference.update_known_preferences(
                request.user,
                {UserPreference.KEY_USER_PET_SELECTED_ID: BUILTIN_PET_ID},
            )
        pet.delete()
        return JsonResponse(_serialize_pet_library(request.user))


class UserPetSpritesheetAPIView(ApiLoginRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request: HttpRequest, pet_id, *args, **kwargs):
        pet = get_object_or_404(UserPet, pk=pet_id, user=request.user)
        response = FileResponse(pet.spritesheet.open("rb"), content_type="image/webp")
        response["Cache-Control"] = "private, max-age=86400"
        response["X-Content-Type-Options"] = "nosniff"
        return response
