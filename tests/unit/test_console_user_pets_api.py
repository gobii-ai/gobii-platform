import json
import shutil
import tempfile
from io import BytesIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings, tag
from django.urls import reverse
from PIL import Image, ImageDraw

from api.models import UserPet, UserPreference
from api.services.user_pets import (
    PET_COLUMNS,
    PET_FRAME_HEIGHT,
    PET_FRAME_WIDTH,
    PET_SPRITESHEET_HEIGHT,
    PET_SPRITESHEET_WIDTH,
    PET_USED_COLUMNS_BY_ROW,
    validate_user_pet_spritesheet,
)
from console.user_pets_api import BUILTIN_PET_ID, BUILTIN_PETS


def _valid_pet_webp(*, populate_unused_frame=False):
    image = Image.new(
        "RGBA",
        (PET_SPRITESHEET_WIDTH, PET_SPRITESHEET_HEIGHT),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(image)
    for row, used_columns in enumerate(PET_USED_COLUMNS_BY_ROW):
        for column in range(used_columns):
            left = column * PET_FRAME_WIDTH + 10
            top = row * PET_FRAME_HEIGHT + 10
            draw.rectangle((left, top, left + 9, top + 9), fill=(125, 80, 230, 255))
    if populate_unused_frame:
        left = (PET_COLUMNS - 1) * PET_FRAME_WIDTH + 10
        draw.rectangle((left, 10, left + 9, 19), fill=(255, 0, 0, 255))

    output = BytesIO()
    image.save(output, format="WEBP", lossless=True)
    return output.getvalue()


@tag("batch_console_api")
class ConsoleUserPetsApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.media_root = tempfile.mkdtemp(prefix="gobii-user-pets-")
        cls.settings_override = override_settings(MEDIA_ROOT=cls.media_root)
        cls.settings_override.enable()
        cls.valid_webp = _valid_pet_webp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.settings_override.disable()
        shutil.rmtree(cls.media_root, ignore_errors=True)

    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="pet-owner",
            email="pet-owner@example.com",
            password="password123",
        )
        self.other_user = user_model.objects.create_user(
            username="other-pet-owner",
            email="other-pet-owner@example.com",
            password="password123",
        )
        self.client.force_login(self.user)
        self.url = reverse("console_user_pets")

    def _upload(self, *, name="Orbit", payload=None):
        spritesheet = SimpleUploadedFile(
            "pet.webp",
            payload if payload is not None else self.valid_webp,
            content_type="image/webp",
        )
        return self.client.post(
            self.url,
            {
                "displayName": name,
                "description": "A custom companion",
                "spritesheet": spritesheet,
            },
        )

    def _update_preferences(self, preferences):
        return self.client.patch(
            reverse("console_user_preferences"),
            data=json.dumps({"preferences": preferences}),
            content_type="application/json",
        )

    def test_get_returns_builtin_pets_and_default_preferences(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["maxCustomPets"], settings.USER_PET_MAX_CUSTOM_PETS)
        self.assertEqual(
            payload["pets"],
            [
                {
                    "id": BUILTIN_PET_ID,
                    "kind": "builtin",
                    "displayName": "Gobii",
                    "description": "The official mascot of Gobii.",
                    "spritesheetUrl": "/static/images/pets/gobii-fish-v2.webp",
                },
                {
                    "id": "builtin:eevee",
                    "kind": "builtin",
                    "displayName": "Eevee",
                    "description": "Matt's runt-of-the-litter corgi.",
                    "spritesheetUrl": "/static/images/pets/eevee-v2.webp",
                },
                {
                    "id": "builtin:grizzly",
                    "kind": "builtin",
                    "displayName": "Grizzly",
                    "description": "A brown dog named Grizzly.",
                    "spritesheetUrl": "/static/images/pets/grizzly-v2.webp",
                },
                {
                    "id": "builtin:smudge",
                    "kind": "builtin",
                    "displayName": "Smudge",
                    "description": "A black cat named Smudge.",
                    "spritesheetUrl": "/static/images/pets/smudge-v2.webp",
                },
                {
                    "id": "builtin:maggie",
                    "kind": "builtin",
                    "displayName": "Maggie",
                    "description": "A black-and-white dog named Maggie.",
                    "spritesheetUrl": "/static/images/pets/maggie-v2.webp",
                },
                {
                    "id": "builtin:clementine",
                    "kind": "builtin",
                    "displayName": "Clementine",
                    "description": "A calico cat named Clementine.",
                    "spritesheetUrl": "/static/images/pets/clementine-v2.webp",
                },
                {
                    "id": "builtin:riby",
                    "kind": "builtin",
                    "displayName": "Riby",
                    "description": "A German shepherd named Riby.",
                    "spritesheetUrl": "/static/images/pets/riby-v2.webp",
                },
                {
                    "id": "builtin:chewie",
                    "kind": "builtin",
                    "displayName": "Chewie",
                    "description": "A Yorkshire terrier named Chewie.",
                    "spritesheetUrl": "/static/images/pets/chewie-v2.webp",
                },
                {
                    "id": "builtin:violet",
                    "kind": "builtin",
                    "displayName": "Violet",
                    "description": "A lovable dog.",
                    "spritesheetUrl": "/static/images/pets/violet-v2.webp",
                },
                {
                    "id": "builtin:jimothy",
                    "kind": "builtin",
                    "displayName": "Jimothy",
                    "description": "The world famous racoon.",
                    "spritesheetUrl": "/static/images/pets/jimothy-v2.webp",
                },
            ],
        )
        self.assertEqual(
            payload["preferences"],
            {
                "enabled": True,
                "selectedPetId": BUILTIN_PET_ID,
                "size": "medium",
                "position": None,
            },
        )

    def test_builtin_pet_spritesheets_are_valid(self):
        for pet in BUILTIN_PETS:
            with self.subTest(pet_id=pet["id"]):
                asset_path = settings.BASE_DIR / "static" / pet["spritesheetUrl"]
                self.assertTrue(asset_path.is_file())
                payload = asset_path.read_bytes()
                uploaded_file = SimpleUploadedFile(
                    asset_path.name,
                    payload,
                    content_type="image/webp",
                )

                self.assertEqual(
                    validate_user_pet_spritesheet(
                        uploaded_file,
                        max_bytes=settings.USER_PET_MAX_UPLOAD_BYTES,
                    ),
                    payload,
                )

    def test_uploads_valid_pet_and_keeps_spritesheet_private(self):
        response = self._upload()

        self.assertEqual(response.status_code, 201)
        pet = UserPet.objects.get(user=self.user)
        custom_pet = next(item for item in response.json()["pets"] if item["kind"] == "custom")
        self.assertEqual(custom_pet["id"], str(pet.id))
        self.assertEqual(custom_pet["displayName"], "Orbit")
        self.assertTrue(response.json()["preferences"]["enabled"])
        self.assertEqual(response.json()["preferences"]["selectedPetId"], str(pet.id))

        asset_response = self.client.get(custom_pet["spritesheetUrl"])
        self.assertEqual(asset_response.status_code, 200)
        self.assertEqual(asset_response["Content-Type"], "image/webp")
        self.assertEqual(asset_response["Cache-Control"], "private, max-age=86400")

        other_client = Client()
        other_client.force_login(self.other_user)
        forbidden_response = other_client.get(custom_pet["spritesheetUrl"])
        self.assertEqual(forbidden_response.status_code, 404)

    def test_rejects_populated_unused_frame(self):
        response = self._upload(payload=_valid_pet_webp(populate_unused_frame=True))

        self.assertEqual(response.status_code, 400)
        self.assertIn("must be transparent", response.json()["error"])
        self.assertFalse(UserPet.objects.exists())

    def test_updates_selection_size_visibility_and_position(self):
        self.assertEqual(self._upload().status_code, 201)
        pet = UserPet.objects.get(user=self.user)

        response = self._update_preferences(
            {
                UserPreference.KEY_USER_PET_ENABLED: False,
                UserPreference.KEY_USER_PET_SELECTED_ID: str(pet.id),
                UserPreference.KEY_USER_PET_SIZE: "large",
                UserPreference.KEY_USER_PET_POSITION: {"x": 0.25, "y": 0.75},
            }
        )

        self.assertEqual(response.status_code, 200)
        preferences = response.json()["preferences"]
        self.assertFalse(preferences[UserPreference.KEY_USER_PET_ENABLED])
        self.assertEqual(preferences[UserPreference.KEY_USER_PET_SELECTED_ID], str(pet.id))
        self.assertEqual(preferences[UserPreference.KEY_USER_PET_SIZE], "large")
        self.assertEqual(
            preferences[UserPreference.KEY_USER_PET_POSITION],
            {"x": 0.25, "y": 0.75},
        )

    def test_selects_an_included_non_default_pet(self):
        for pet_id in (
            "builtin:eevee",
            "builtin:grizzly",
            "builtin:smudge",
            "builtin:maggie",
            "builtin:clementine",
            "builtin:riby",
            "builtin:chewie",
            "builtin:violet",
            "builtin:jimothy",
        ):
            with self.subTest(pet_id=pet_id):
                response = self._update_preferences(
                    {UserPreference.KEY_USER_PET_SELECTED_ID: pet_id}
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json()["preferences"][UserPreference.KEY_USER_PET_SELECTED_ID],
                    pet_id,
                )

    def test_cannot_select_another_users_pet(self):
        other_pet = UserPet.objects.create(
            user=self.other_user,
            display_name="Private pet",
            spritesheet="user_pets/private.webp",
        )

        response = self._update_preferences(
            {UserPreference.KEY_USER_PET_SELECTED_ID: str(other_pet.id)}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content.decode(), "Select a pet from your library.")
        self.assertFalse(UserPreference.objects.filter(user=self.user).exists())

    def test_pet_library_endpoint_does_not_update_preferences(self):
        response = self.client.patch(
            self.url,
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 405)

    def test_rejects_upload_after_ten_custom_pets(self):
        UserPet.objects.bulk_create(
            [
                UserPet(
                    user=self.user,
                    display_name=f"Pet {index}",
                    spritesheet=f"user_pets/pet-{index}.webp",
                )
                for index in range(settings.USER_PET_MAX_CUSTOM_PETS)
            ]
        )

        response = self._upload(name="One too many")

        self.assertEqual(response.status_code, 400)
        self.assertIn("up to 10 custom pets", response.json()["error"])

    def test_deleting_selected_pet_resets_to_builtin(self):
        self.assertEqual(self._upload().status_code, 201)
        pet = UserPet.objects.get(user=self.user)
        UserPreference.update_known_preferences(
            self.user,
            {UserPreference.KEY_USER_PET_SELECTED_ID: str(pet.id)},
        )

        response = self.client.delete(
            reverse("console_user_pet_detail", kwargs={"pet_id": pet.id}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["preferences"]["selectedPetId"], BUILTIN_PET_ID)
        self.assertFalse(UserPet.objects.filter(pk=pet.id).exists())
