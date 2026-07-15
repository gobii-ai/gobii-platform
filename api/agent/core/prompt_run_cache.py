from collections import OrderedDict
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable


CONTACTS_SNAPSHOT = "contacts"
FILES_SNAPSHOT = "files"
MESSAGES_SNAPSHOT = "messages"
SNAPSHOT_DOMAINS = frozenset({CONTACTS_SNAPSHOT, FILES_SNAPSHOT, MESSAGES_SNAPSHOT})


class BoundedTokenCountCache:
    """Exact-text LRU bounded by entry count and retained source characters."""

    def __init__(self, *, max_entries: int = 2048, max_chars: int = 8_000_000):
        self.max_entries = max(1, max_entries)
        self.max_chars = max(1, max_chars)
        self._entries: OrderedDict[tuple[str, str], int] = OrderedDict()
        self._retained_chars = 0
        self.hits = 0
        self.misses = 0

    def count(self, model: str, text: str, compute: Callable[[str], int]) -> int:
        key = (model, text)
        cached = self._entries.pop(key, None)
        if cached is not None:
            self._entries[key] = cached
            self.hits += 1
            return cached

        self.misses += 1
        value = compute(text)
        text_chars = len(text)
        if text_chars <= self.max_chars:
            self._entries[key] = value
            self._retained_chars += text_chars
            self._evict_to_limits()
        return value

    def _evict_to_limits(self) -> None:
        while self._entries and (
            len(self._entries) > self.max_entries or self._retained_chars > self.max_chars
        ):
            evicted_key, _ = self._entries.popitem(last=False)
            self._retained_chars -= len(evicted_key[1])


@dataclass
class PromptRunCache:
    agent_id: str
    snapshot_reuse_enabled: bool = True
    token_counts: BoundedTokenCountCache = field(default_factory=BoundedTokenCountCache)
    _snapshots: dict[str, Any] = field(default_factory=dict)
    _dirty: set[str] = field(default_factory=lambda: set(SNAPSHOT_DOMAINS))
    _human_generation: int | None = None
    _lock: Any = field(default_factory=RLock, repr=False, compare=False)

    def get_or_build(self, domain: str, builder: Callable[[], Any]) -> tuple[Any, bool]:
        if domain not in SNAPSHOT_DOMAINS:
            raise ValueError(f"Unknown prompt snapshot domain: {domain}")
        with self._lock:
            if self.snapshot_reuse_enabled and domain not in self._dirty and domain in self._snapshots:
                return self._snapshots[domain], True

            value = builder()
            if self.snapshot_reuse_enabled:
                self._snapshots[domain] = value
                self._dirty.discard(domain)
            return value, False

    def invalidate(self, *domains: str) -> None:
        requested = set(domains or SNAPSHOT_DOMAINS)
        unknown = requested - SNAPSHOT_DOMAINS
        if unknown:
            raise ValueError(f"Unknown prompt snapshot domains: {sorted(unknown)}")
        with self._lock:
            self._dirty.update(requested)

    def observe_human_generation(self, generation: int) -> None:
        with self._lock:
            if self._human_generation is not None and generation != self._human_generation:
                self.invalidate(MESSAGES_SNAPSHOT, CONTACTS_SNAPSHOT)
            self._human_generation = generation

_active_prompt_run_cache: ContextVar[PromptRunCache | None] = ContextVar(
    "active_prompt_run_cache",
    default=None,
)


def bind_prompt_run_cache(cache: PromptRunCache) -> Token:
    return _active_prompt_run_cache.set(cache)


def reset_prompt_run_cache(token: Token) -> None:
    _active_prompt_run_cache.reset(token)


def invalidate_active_prompt_run_cache(agent_id: object | None, *domains: str) -> None:
    cache = _active_prompt_run_cache.get()
    if cache is None:
        return
    if agent_id is not None and str(agent_id) != cache.agent_id:
        return
    cache.invalidate(*domains)
