from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible


PUBLIC_TEMPLATE_SOCIAL_IMAGE_STORAGE_ALIAS = "public_template_social_images"


@deconstructible
class AliasedStorage(Storage):
    def __init__(self, alias):
        self.alias = alias

    @property
    def _wrapped_storage(self):
        return storages[self.alias]

    def open(self, name, mode="rb"):
        return self._wrapped_storage.open(name, mode)

    def save(self, name, content, max_length=None):
        return self._wrapped_storage.save(name, content, max_length=max_length)

    def delete(self, name):
        return self._wrapped_storage.delete(name)

    def exists(self, name):
        return self._wrapped_storage.exists(name)

    def listdir(self, path):
        return self._wrapped_storage.listdir(path)

    def size(self, name):
        return self._wrapped_storage.size(name)

    def url(self, name):
        return self._wrapped_storage.url(name)

    def path(self, name):
        return self._wrapped_storage.path(name)

    def get_accessed_time(self, name):
        return self._wrapped_storage.get_accessed_time(name)

    def get_created_time(self, name):
        return self._wrapped_storage.get_created_time(name)

    def get_modified_time(self, name):
        return self._wrapped_storage.get_modified_time(name)

    def get_valid_name(self, name):
        return self._wrapped_storage.get_valid_name(name)

    def get_alternative_name(self, file_root, file_ext):
        return self._wrapped_storage.get_alternative_name(file_root, file_ext)

    def get_available_name(self, name, max_length=None):
        return self._wrapped_storage.get_available_name(name, max_length=max_length)

    def generate_filename(self, filename):
        return self._wrapped_storage.generate_filename(filename)

    def __getattr__(self, name):
        return getattr(self._wrapped_storage, name)
