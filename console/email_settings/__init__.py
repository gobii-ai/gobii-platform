_VIEW_EXPORTS = {
    "AgentEmailSettingsAPIView",
    "AgentEmailSettingsEnsureAccountAPIView",
    "AgentEmailSettingsTestAPIView",
}

__all__ = sorted(_VIEW_EXPORTS)


def __getattr__(name):
    if name in _VIEW_EXPORTS:
        from . import views

        return getattr(views, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
