from __future__ import annotations

import heapq
import json
import math
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── helpers ────────────────────────────────────────────────────────────────
def _default_estimator(s: str) -> int:
    """Very rough token estimator: 1 token ≈ 1 word."""
    return len(s.split())


# ── byte‑level shrinkers (stateless) ───────────────────────────────────────
def hmt(txt: str, k: float) -> str:
    """
    Head–Mid–Tail shrinker that keeps ≈ k fraction of *bytes*.
    Adds gap markers with exact byte counts removed.
    Operates purely on bytes; UTF‑8 boundary corruption is tolerated.
    """
    if k >= 0.99:
        return txt

    raw = txt.encode()
    n = len(raw)
    keep = max(1, int(n * k))

    head_len = max(1, keep // 4)
    mid_len = keep // 4
    tail_len = keep - head_len - mid_len

    head_bytes = raw[:head_len]
    mid_start = max(0, n // 2 - mid_len // 2)
    mid_bytes = raw[mid_start : mid_start + mid_len]
    tail_bytes = raw[-tail_len:] if tail_len > 0 else b""

    gap1 = mid_start - head_len
    gap2 = n - (mid_start + mid_len) - tail_len

    parts: List[str] = [head_bytes.decode("utf-8", "ignore")]
    if gap1 > 0:
        parts.append(f"[{gap1} BYTES TRUNCATED]")
    parts.append(mid_bytes.decode("utf-8", "ignore"))
    if gap2 > 0:
        parts.append(f"[{gap2} BYTES TRUNCATED]")
    parts.append(tail_bytes.decode("utf-8", "ignore"))

    return " ".join(parts)


# ── data model ─────────────────────────────────────────────────────────────
@dataclass
class _Node:
    name: str
    weight: int
    renderer: Any = None
    shrinker: str | Callable[[str, float], str] | None = None
    use_jinja2: bool = False
    children: List["_Node"] = field(default_factory=list)
    text: str = ""
    rendered_content: str = ""
    tokens: int = 0
    shrunk: bool = False
    non_shrinkable: bool = False
    _prompt: Optional["Prompt"] = None  # Reference to parent prompt for accessing shrinkers
    
    def group(self, name: str, *, weight: int = 1) -> "_Node":
        """Create a sub-group within this node."""
        child = _Node(name, weight, _prompt=self._prompt)
        self.children.append(child)
        return child
    
    def section(
        self,
        name: str,
        renderer: Any,
        *,
        weight: int = 1,
        shrinker: str | Callable[[str, float], str] | None = "hmt",
        use_jinja2: bool = False,
        non_shrinkable: bool = False,
    ) -> None:
        """Add a section to this group."""
        self.children.append(
            _Node(name, weight, renderer, shrinker, use_jinja2, non_shrinkable=non_shrinkable, _prompt=self._prompt)
        )

    def section_text(
        self,
        name: str,
        txt: str,
        *,
        weight: int = 1,
        shrinker: str | Callable[[str, float], str] | None = "hmt",
        use_jinja2: bool = False,
        non_shrinkable: bool = False,
    ) -> None:
        """Add a text section to this group."""
        self.section(
            name, txt, weight=weight, shrinker=shrinker, use_jinja2=use_jinja2, non_shrinkable=non_shrinkable
        )


# ── main object ────────────────────────────────────────────────────────────
class Prompt:
    """
    Tree‑structured prompt builder that globally allocates the token budget.
    Client usage remains unchanged, but `render()` now returns minified JSON.
    """

    # ------------------------------------------------------------------ #
    def __init__(
        self,
        token_estimator: Callable[[str], int] = _default_estimator,
        extra_shrinkers: Optional[
            Dict[str, Callable[[str, float], str]]
        ] = None,
    ):
        self.token_estimator = token_estimator
        self.shrinkers: Dict[str, Callable[[str, float], str]] = {
            "hmt": hmt,
        }
        if extra_shrinkers:
            self.shrinkers.update(extra_shrinkers)

        self.root = _Node("root", 1)
        self._last: List[_Node] = []
        self._tokens_before_fitting: int = 0
        self._tokens_after_fitting: int = 0

    # builder shortcuts ---------------------------------------------------
    def group(self, name: str, *, weight: int = 1) -> _Node:
        child = _Node(name, weight, _prompt=self)
        self.root.children.append(child)
        return child

    def section(
        self,
        name: str,
        renderer: Any,
        *,
        weight: int = 1,
        shrinker: str | Callable[[str, float], str] | None = "hmt",
        use_jinja2: bool = False,
        non_shrinkable: bool = False,
    ) -> None:
        self.root.children.append(
            _Node(name, weight, renderer, shrinker, use_jinja2, non_shrinkable=non_shrinkable, _prompt=self)
        )

    def section_text(
        self,
        name: str,
        txt: str,
        *,
        weight: int = 1,
        shrinker: str | Callable[[str, float], str] | None = "hmt",
        use_jinja2: bool = False,
        non_shrinkable: bool = False,
    ) -> None:
        self.section(
            name, txt, weight=weight, shrinker=shrinker, use_jinja2=use_jinja2, non_shrinkable=non_shrinkable
        )

    # plug‑in API ---------------------------------------------------------
    def register_shrinker(
        self, fn: Callable[[str, float], str], *, name: Optional[str] = None
    ):
        self.shrinkers[name or fn.__name__] = fn
        return fn

    # public --------------------------------------------------------------
    def render(self, max_tokens: int, **ctx) -> str:
        # Pass 1: render everything in full
        self._render(self.root, ctx)
        leaves = self._flat(self.root)

        # Pre‑compute overhead (wrapper tag) & total tokens
        for n in leaves:
            overhead = self._tok(f"<{n.name}></{n.name}>")
            n._overhead_tokens = overhead  # type: ignore[attr-defined]
            n._length = n.tokens + overhead  # type: ignore[attr-defined]

        self._tokens_before_fitting = sum(n._length for n in leaves)  # type: ignore[attr-defined]

        # Pass 2: global allocation
        budgets = self._allocate(leaves, max_tokens)

        # Pass 3: shrink individual leaves as needed
        for n, budget in zip(leaves, budgets):
            overhead = n._overhead_tokens  # type: ignore[attr-defined]

            if n.tokens + overhead <= budget:
                # Fits unshrunk: just wrap
                n.rendered_content = n.text
                n.text = f"<{n.name}>{n.text}</{n.name}>"
                n.tokens = self._tok(n.text)
                n.shrunk = False
            else:
                shrinker_fn: Optional[Callable[[str, float], str]] = None
                if n.shrinker:
                    shrinker_fn = (
                        n.shrinker
                        if callable(n.shrinker)
                        else self.shrinkers.get(n.shrinker)
                    )
                self._shrink(n, shrinker_fn, budget)

        self._tokens_after_fitting = sum(n.tokens for n in leaves)
        self._last = leaves

        # Assemble nested output with group tags
        def _merge(container: OrderedDict, key: str, value: Any) -> None:
            if key in container:
                existing = container[key]
                if isinstance(existing, list):
                    existing.append(value)
                else:
                    container[key] = [existing, value]
            else:
                container[key] = value

        def _assemble(node: _Node):
            if node.children:
                obj = OrderedDict()
                for child in node.children:
                    child_payload = _assemble(child)
                    _merge(obj, child.name, child_payload)
                return obj
            return node.rendered_content

        assembled = _assemble(self.root)
        if isinstance(assembled, OrderedDict):
            output = json.dumps(assembled, separators=(",", ":"), ensure_ascii=False)
        else:
            output = json.dumps({"root": assembled}, separators=(",", ":"), ensure_ascii=False)

        self._tokens_after_fitting = self._tok(output)
        return output

    def report(self) -> str:
        return (
            "section            tokens  shrunk\n"
            + "\n".join(
                f"{n.name:<18}{n.tokens:>6}  {'✔' if n.shrunk else ''}"
                for n in self._last
            )
        )

    def get_tokens_before_fitting(self) -> int:
        return self._tokens_before_fitting

    def get_tokens_after_fitting(self) -> int:
        return self._tokens_after_fitting

    # internals -----------------------------------------------------------
    def _tok(self, txt: str) -> int:
        return self.token_estimator(txt)

    def _render(self, n: _Node, ctx: Dict[str, Any]):
        if n.children:
            for c in n.children:
                self._render(c, ctx)
            n.tokens = sum(c.tokens for c in n.children)
            return

        raw = n.renderer(ctx) if callable(n.renderer) else str(n.renderer)
        if n.use_jinja2:
            try:
                import jinja2

                raw = jinja2.Template(raw).render(**ctx)
            except ImportError:
                pass

        n.text = raw
        n.rendered_content = raw
        n.tokens = self._tok(raw)

    def _flat(self, n: _Node) -> List[_Node]:
        return (
            [n]
            if not n.children
            else [leaf for c in n.children for leaf in self._flat(c)]
        )

    # ── global allocation (water‑filling) ───────────────────────────────
    def _allocate(self, leaves: List[_Node], budget: int) -> List[int]:
        """
        Continuous water‑filling allocator.
        Each leaf i with weight w_i and length L_i gets r_i = min(L_i, μ·w_i).
        μ is chosen so Σ r_i = budget.
        """
        if budget <= 0 or not leaves:
            return [0] * len(leaves)

        weights = [max(1, leaf.weight) for leaf in leaves]
        lengths = [leaf._length for leaf in leaves]  # type: ignore[attr-defined]

        # Pre-allocate full length for non-shrinkable leaves
        fixed_indices = [i for i, leaf in enumerate(leaves) if leaf.non_shrinkable]
        if fixed_indices:
            fixed_lengths = {i: lengths[i] for i in fixed_indices}
            fixed_total = sum(fixed_lengths.values())
            # If non-shrinkable leaves already exhaust the budget, early return.
            if fixed_total >= budget:
                return [lengths[i] for i in range(len(leaves))]
            budget -= fixed_total  # Remaining budget for shrinkable leaves
            for i in fixed_indices:
                weights[i] = 0
                lengths[i] = 0
        n = len(leaves)

        # Binary‑search μ
        candidates = [l / w for l, w in zip(lengths, weights) if w > 0]
        if not candidates:
            # Only non-shrinkable leaves present
            if 'fixed_lengths' in locals():
                return [fixed_lengths.get(i, 0) for i in range(len(leaves))]
            return [0] * len(leaves)
        low, high = 0.0, max(candidates)
        for _ in range(40):
            μ = (low + high) / 2
            alloc = [min(l, int(μ * w)) for l, w in zip(lengths, weights)]
            s = sum(alloc)
            if s > budget:
                high = μ
            else:
                low = μ

        μ = low
        alloc = [min(l, int(μ * w)) for l, w in zip(lengths, weights)]

        # Distribute any remaining slack to leaves that still need it,
        # favouring higher weight then larger remaining gap.
        slack = budget - sum(alloc)
        if slack > 0:
            heap: List[tuple[float, int]] = []
            for i, (l, a, w) in enumerate(zip(lengths, alloc, weights)):
                gap = l - a
                if gap > 0:
                    # Priority key: −weight, −gap to pop biggest first
                    heapq.heappush(heap, (-w, -gap, i))
            while slack > 0 and heap:
                _, _, i = heapq.heappop(heap)
                alloc[i] += 1
                slack -= 1
                gap = lengths[i] - alloc[i]
                if gap > 0:
                    heapq.heappush(heap, (-weights[i], -gap, i))

        # Restore allocations for non-shrinkable leaves
        if 'fixed_lengths' in locals():
            for i, l in fixed_lengths.items():
                alloc[i] = l
        return alloc

    # ── leaf‑level shrinking --------------------------------------------
    def _shrink(
        self, n: _Node, fn: Optional[Callable[[str, float], str]], budget: int
    ):
        base = n.text
        if fn:
            r = min(1.0, budget / max(1, n.tokens))
            for _ in range(12):  # iterative refinement
                shrunk_text = fn(base, r)
                wrapped = f"<{n.name}>{shrunk_text}</{n.name}>"
                current = self._tok(wrapped)
                if current <= budget:
                    n.text = wrapped
                    n.rendered_content = shrunk_text
                    n.tokens = current
                    break
                r *= 0.80
            else:
                n.text = wrapped
                n.rendered_content = shrunk_text
                n.tokens = self._tok(wrapped)
        else:
            self._hard_truncate(n, budget)

        n.shrunk = True

    def _hard_truncate(self, n: _Node, budget: int):
        """Byte‑level tail‑preserving hard truncate."""
        tag_open = f"<{n.name}>"
        tag_close = f"</{n.name}>"
        tag_overhead = self._tok(tag_open + tag_close)

        marker_tpl = "[{d} BYTES TRUNCATED]"
        dummy_marker = marker_tpl.format(d=0)
        marker_tokens = self._tok(dummy_marker)

        content_budget = max(0, budget - tag_overhead - marker_tokens)

        raw_bytes = n.text.encode()
        if len(raw_bytes) <= content_budget:
            kept_bytes = raw_bytes
        else:
            kept_bytes = raw_bytes[-content_budget:]

        kept_text = kept_bytes.decode("utf-8", "ignore")
        dropped = max(0, len(raw_bytes) - len(kept_bytes))
        marker = marker_tpl.format(d=dropped)

        content = f"{marker} {kept_text}".strip()
        final = f"{tag_open}{content}{tag_close}"
        n.text = final
        n.rendered_content = content
        n.tokens = self._tok(final)

        # Last‑ditch clamp if estimator is off
        if n.tokens > budget:
            words = content.split()
            trimmed_content = " ".join(words[:budget])
            final = f"{tag_open}{trimmed_content}{tag_close}"
            n.text = final
            n.rendered_content = trimmed_content
            n.tokens = self._tok(final)
