"""Helpers for deriving provider hints from model identifiers."""


def provider_hint_from_model(model_name: str | None) -> str | None:
    """Return provider prefix from `provider/model` identifiers."""
    if not isinstance(model_name, str):
        return None
    if "/" not in model_name:
        return None
    return model_name.split("/", 1)[0]


__all__ = ["provider_hint_from_model"]
