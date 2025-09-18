from __future__ import annotations

import time
from typing import Any, Dict, Optional


class FakePipeline:
    """Collect Redis commands and replay them synchronously when executed."""

    def __init__(self, client: "FakeRedis") -> None:
        self._client = client
        self._ops: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def hset(self, *args, **kwargs):  # noqa: ANN002 - mirror redis signature
        self._ops.append(("hset", args, kwargs))
        return self

    def expire(self, *args, **kwargs):  # noqa: ANN002
        self._ops.append(("expire", args, kwargs))
        return self

    def set(self, *args, **kwargs):  # noqa: ANN002
        self._ops.append(("set", args, kwargs))
        return self

    def delete(self, *args, **kwargs):  # noqa: ANN002
        self._ops.append(("delete", args, kwargs))
        return self

    def execute(self):
        for name, args, kwargs in self._ops:
            getattr(self._client, name)(*args, **kwargs)
        self._ops.clear()
        return True


class FakeRedis:
    """A lightweight, in-memory Redis replacement for unit tests."""

    def __init__(self) -> None:
        self._kv: Dict[str, Any] = {}
        self._hash: Dict[str, Dict[str, Any]] = {}
        self._ttl: Dict[str, int] = {}
        self._lists: Dict[str, list] = {}
        self._streams: Dict[str, list[tuple[str, Dict[str, Any]]]] = {}
        self._stream_seq: Dict[str, int] = {}
        self._stream_cursors: Dict[str, int] = {}

    # Basic KV operations --------------------------------------------------
    def ping(self) -> bool:
        return True

    def get(self, key: str) -> Optional[Any]:
        return self._kv.get(key)

    def set(self, key: str, value: Any):
        self._kv[key] = value
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if key in self._kv or key in self._hash:
                removed += 1
            self._kv.pop(key, None)
            self._hash.pop(key, None)
            self._ttl.pop(key, None)
        return removed

    def exists(self, key: str) -> int:
        return 1 if key in self._kv or key in self._hash else 0

    def expire(self, key: str, ttl: int) -> bool:
        self._ttl[key] = ttl
        return True

    # Hash operations -----------------------------------------------------
    def hset(self, key: str, *args, **kwargs):  # noqa: ANN002
        mapping = self._hash.setdefault(key, {})
        if args and isinstance(args[0], dict):
            mapping.update(args[0])
        elif "mapping" in kwargs:
            mapping.update(kwargs["mapping"])
        elif len(args) >= 2:
            field, value = args[0], args[1]
            mapping[str(field)] = value
        elif len(args) == 1 and kwargs:
            for field, value in kwargs.items():
                mapping[str(field)] = value
        return True

    def hgetall(self, key: str) -> Dict[str, Any]:
        return dict(self._hash.get(key, {}))

    def hget(self, key: str, field: str) -> Optional[Any]:
        return self._hash.get(key, {}).get(field)

    def hincrby(self, key: str, field: str, amount: int = 1) -> int:
        current = self.hget(key, field)
        try:
            value = int(current) if current is not None else 0
        except Exception:
            value = 0
        value += int(amount)
        self.hset(key, field, value)
        return value

    def hdel(self, key: str, field: str) -> int:
        mapping = self._hash.get(key)
        if mapping and field in mapping:
            del mapping[field]
            return 1
        return 0

    # Eval used by AgentBudgetManager ------------------------------------
    def eval(self, script: str, numkeys: int, *args):  # noqa: ANN002
        if numkeys != 1:
            raise NotImplementedError("FakeRedis.eval only supports one key")
        steps_key = args[0]
        max_steps = int(args[1]) if len(args) > 1 else 0
        current = self.get(steps_key)
        try:
            value = int(current) if current is not None else 0
        except Exception:
            value = 0
        if value >= max_steps:
            return [0, value]
        value += 1
        self.set(steps_key, value)
        return [1, value]

    # Pipelines -----------------------------------------------------------
    def pipeline(self):
        return FakePipeline(self)

    # List helpers --------------------------------------------------------
    def rpush(self, key: str, value: Any) -> int:
        queue = self._lists.setdefault(key, [])
        queue.append(value)
        return len(queue)

    def blpop(self, keys, timeout: int = 0):  # noqa: ANN001
        key_list = keys if isinstance(keys, (list, tuple)) else (keys,)
        for key in key_list:
            queue = self._lists.get(key, [])
            if queue:
                return key, queue.pop(0)
        return None

    # Streams -------------------------------------------------------------
    @staticmethod
    def _parse_stream_id(stream_id: str) -> tuple[int, int]:
        try:
            ms, seq = stream_id.split("-", 1)
            return int(ms), int(seq)
        except Exception:
            return 0, 0

    def xadd(self, key: str, fields: Dict[str, Any], id: str | None = "*", maxlen: int | None = None, approximate: bool = True):  # noqa: ANN001, ARG002
        if id in (None, "*"):
            ms = int(time.time() * 1000)
            seq = self._stream_seq.get(key, 0) + 1
            self._stream_seq[key] = seq
            entry_id = f"{ms}-{seq}"
        else:
            entry_id = str(id)

        normalised = {str(k): str(v) for k, v in fields.items()}
        stream = self._streams.setdefault(key, [])
        stream.append((entry_id, normalised))

        if maxlen is not None:
            try:
                maxlen = int(maxlen)
                if maxlen >= 0 and len(stream) > maxlen:
                    self._streams[key] = stream[-maxlen:]
            except Exception:
                pass

        return entry_id

    def xread(self, streams: Dict[str, str], count: int | None = None, block: int | None = None):  # noqa: ANN001, ARG002
        results: list[tuple[str, list[tuple[str, Dict[str, Any]]]]] = []

        for key, last_id in streams.items():
            entries = self._streams.get(key, [])
            if not entries:
                if last_id == "$":
                    self._stream_cursors[key] = 0
                continue

            selected: list[tuple[str, Dict[str, Any]]] = []

            if last_id == "$":
                start_index = self._stream_cursors.get(key, len(entries))
                start_index = max(0, min(start_index, len(entries)))
                slice_ = entries[start_index:]
                if slice_:
                    selected.extend(slice_ if count is None else slice_[:count])
                self._stream_cursors[key] = len(entries)
            else:
                comparator = None if last_id in (None, "", "-", "0") else self._parse_stream_id(last_id)
                for entry_id, data in entries:
                    if comparator is None or self._parse_stream_id(entry_id) > comparator:
                        selected.append((entry_id, data))
                        if count is not None and len(selected) >= count:
                            break
                self._stream_cursors[key] = len(entries)

            if selected:
                results.append((key, selected))

        return results
