import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, tag

from config.storage import ReadThroughStorage


@tag("batch_agent_filesystem")
class ReadThroughStorageTests(SimpleTestCase):
    def setUp(self):
        self.primary_root = Path(tempfile.mkdtemp())
        self.fallback_root = Path(tempfile.mkdtemp())
        self.storage = ReadThroughStorage(
            primary={
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.primary_root, "base_url": "/primary/"},
            },
            fallback={
                "BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": self.fallback_root, "base_url": "/fallback/"},
            },
        )

    def tearDown(self):
        shutil.rmtree(self.primary_root, ignore_errors=True)
        shutil.rmtree(self.fallback_root, ignore_errors=True)

    def _write_primary(self, name: str, content: bytes) -> None:
        path = self.primary_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def _write_fallback(self, name: str, content: bytes) -> None:
        path = self.fallback_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def test_reads_prefer_primary_storage(self):
        self._write_primary("media/photo.png", b"primary")
        self._write_fallback("media/photo.png", b"fallback")

        self.assertTrue(self.storage.exists("media/photo.png"))
        with self.storage.open("media/photo.png", "rb") as handle:
            self.assertEqual(handle.read(), b"primary")
        self.assertEqual(self.storage.size("media/photo.png"), len(b"primary"))

    def test_reads_from_fallback_when_primary_missing(self):
        self._write_fallback("agent_fs/node/file.txt", b"from fallback")

        self.assertTrue(self.storage.exists("agent_fs/node/file.txt"))
        with self.storage.open("agent_fs/node/file.txt", "rb") as handle:
            self.assertEqual(handle.read(), b"from fallback")
        self.assertEqual(self.storage.size("agent_fs/node/file.txt"), len(b"from fallback"))

    def test_save_writes_only_to_primary_and_ignores_fallback_collision(self):
        self._write_fallback("agent_fs/node/file.txt", b"fallback original")

        saved_name = self.storage.save(
            "agent_fs/node/file.txt",
            ContentFile(b"overlay content"),
        )

        self.assertEqual(saved_name, "agent_fs/node/file.txt")
        self.assertEqual((self.primary_root / saved_name).read_bytes(), b"overlay content")
        self.assertEqual(
            (self.fallback_root / "agent_fs/node/file.txt").read_bytes(),
            b"fallback original",
        )

    def test_delete_only_removes_primary_object(self):
        self._write_primary("agent_fs/node/file.txt", b"primary")
        self._write_fallback("agent_fs/node/file.txt", b"fallback")

        self.storage.delete("agent_fs/node/file.txt")

        self.assertFalse((self.primary_root / "agent_fs/node/file.txt").exists())
        self.assertTrue((self.fallback_root / "agent_fs/node/file.txt").exists())
        self.assertTrue(self.storage.exists("agent_fs/node/file.txt"))
        with self.storage.open("agent_fs/node/file.txt", "rb") as handle:
            self.assertEqual(handle.read(), b"fallback")

    def test_delete_is_noop_for_fallback_only_object(self):
        self._write_fallback("agent_fs/node/file.txt", b"fallback")

        self.storage.delete("agent_fs/node/file.txt")

        self.assertTrue((self.fallback_root / "agent_fs/node/file.txt").exists())
        self.assertTrue(self.storage.exists("agent_fs/node/file.txt"))

    def test_get_available_name_only_considers_primary_storage(self):
        self._write_fallback("agent_fs/node/file.txt", b"fallback")

        self.assertEqual(
            self.storage.get_available_name("agent_fs/node/file.txt"),
            "agent_fs/node/file.txt",
        )

    def test_fallback_url_is_not_exposed_by_default(self):
        self._write_fallback("agent_fs/node/file.txt", b"fallback")

        self.assertEqual(
            self.storage.url("agent_fs/node/file.txt"),
            "/primary/agent_fs/node/file.txt",
        )

    def test_media_read_fallback_is_disabled_by_default(self):
        self.assertFalse(settings.MEDIA_READ_FALLBACK_ENABLED)
        self.assertNotEqual(
            settings.STORAGES["default"]["BACKEND"],
            "config.storage.ReadThroughStorage",
        )
