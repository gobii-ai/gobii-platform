from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible


PUBLIC_TEMPLATE_SOCIAL_IMAGE_STORAGE_ALIAS = "public_template_social_images"


@deconstructible
class AliasedStorage(Storage):
    def __init__(self, alias):
        self.alias = alias

    @property
    def _storage(self):
        return storages[self.alias]

    def __getattribute__(self, name):
        if name.startswith("__") or name in {"alias", "_storage", "deconstruct", "_constructor_args"}:
            return super().__getattribute__(name)
        return getattr(super().__getattribute__("_storage"), name)
