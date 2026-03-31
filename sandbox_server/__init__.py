from importlib import import_module
import sys

_MODULE_NAMES = (
    "config",
    "workspace",
    "manifest",
    "files",
    "run",
    "tools",
    "sync",
    "mcp",
    "app",
)

for _name in _MODULE_NAMES:
    _module = import_module(f".server.{_name}", __name__)
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

application = sys.modules[f"{__name__}.app"].application

__all__ = ["application"]
