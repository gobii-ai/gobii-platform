import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Optional


CONSISTENCY = {
    "excellent": 0.95,
    "good": 0.80,
    "fair": 0.60,
    "poor": 0.40,
}

SPARSITY = {
    "dense": 0.05,
    "normal": 0.15,
    "sparse": 0.30,
}

DEPTH = {
    "flat": 2,
    "moderate": 5,
    "deep": 10,
}

KEY_PATTERNS = {
    "semantic": re.compile(
        r"^[a-z][a-z0-9]*([A-Z][a-z0-9]*)*$|^[a-z][a-z0-9]*(_[a-z0-9]+)*$"
    ),
    "numeric_suffix": re.compile(r"^.*[_-]?\d+$"),
    "opaque": re.compile(r"^[a-fA-F0-9]{8,}$|^[A-Za-z0-9+/=]{16,}$|^[a-zA-Z0-9]{12,}$"),
    "single_char": re.compile(r"^[a-zA-Z]$"),
}


@dataclass(frozen=True)
class TypeDistribution:
    strings: float = 0.0
    numbers: float = 0.0
    booleans: float = 0.0
    nulls: float = 0.0
    objects: float = 0.0
    arrays: float = 0.0

    def to_compact(self) -> str:
        parts = []
        if self.strings > 0.01:
            parts.append(f"str:{self.strings:.0%}")
        if self.numbers > 0.01:
            parts.append(f"num:{self.numbers:.0%}")
        if self.booleans > 0.01:
            parts.append(f"bool:{self.booleans:.0%}")
        if self.nulls > 0.01:
            parts.append(f"null:{self.nulls:.0%}")
        if self.objects > 0.01:
            parts.append(f"obj:{self.objects:.0%}")
        if self.arrays > 0.01:
            parts.append(f"arr:{self.arrays:.0%}")
        return " ".join(parts)


@dataclass(frozen=True)
class JsonDigest:
    bytes_raw: int
    bytes_data: int
    density: float
    depth_max: int
    depth_avg: float
    breadth_max: int
    root_type: str
    type_distribution: str
    total_values: int
    total_keys: int
    total_arrays: int
    total_objects: int
    key_style: str
    key_convention: str
    top_keys: str
    array_consistency: float
    array_consistency_verdict: str
    dominant_array_type: str
    sparsity: float
    sparsity_verdict: str
    hotspot_path: str
    hotspot_pct: float
    schema_hint: str
    verdict: str
    action: str
    flags: str
    sample_path: str
    sample_value: str

    def summary_line(self) -> str:
        parts = [
            f"root={self.root_type}",
            f"verdict={self.verdict}",
            f"action={self.action}",
            f"consistency={self.array_consistency:.2f}",
            f"sparsity={self.sparsity:.2f}",
            f"keys={self.key_style}",
        ]
        if self.flags:
            parts.append(f"flags={self.flags}")
        return " ".join(parts)

    def to_prompt(self) -> str:
        return (
            "<json_digest>\n"
            f"size: {self._human_bytes(self.bytes_raw)} raw, "
            f"{self._human_bytes(self.bytes_data)} data ({self.density:.0%} density)\n"
            f"shape: {self.root_type} | depth: {self.depth_max} (avg {self.depth_avg:.1f}) "
            f"| breadth: {self.breadth_max}\n"
            f"counts: {self.total_values:,} values, {self.total_keys} unique keys, "
            f"{self.total_arrays} arrays, {self.total_objects} objects\n"
            f"types: {self.type_distribution}\n"
            f"keys: {self.key_style} ({self.key_convention}) | top: {self.top_keys}\n"
            f"arrays: {self.array_consistency_verdict} consistency "
            f"({self.array_consistency:.0%}) | contains: {self.dominant_array_type}\n"
            f"sparsity: {self.sparsity_verdict} ({self.sparsity:.0%} null/empty)\n"
            f"hotspot: {self.hotspot_path} ({self.hotspot_pct:.0%} of data)\n"
            f"schema: {self.schema_hint}\n"
            f"VERDICT: {self.verdict} -> {self.action}\n"
            f"{f'flags: {self.flags}' if self.flags else ''}\n"
            f"sample: {self.sample_path} = {self.sample_value}\n"
            "</json_digest>"
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def _human_bytes(b: int) -> str:
        if b < 1024:
            return f"{b}B"
        if b < 1024 * 1024:
            return f"{b / 1024:.1f}KB"
        return f"{b / (1024 * 1024):.1f}MB"


class JsonDigestor:
    MAX_KEYS_TRACK = 1000
    MAX_ARRAY_SAMPLE = 100
    MAX_PATH_DEPTH = 50
    MAX_SAMPLE_VALUE_LEN = 80

    def digest(self, data: Any, raw_json: Optional[str] = None) -> JsonDigest:
        if raw_json is not None:
            bytes_raw = len(raw_json.encode("utf-8"))
        else:
            bytes_raw = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        if data is None or data == {} or data == []:
            return self._empty_digest(bytes_raw)

        stats = _TraversalStats()
        self._traverse(data, stats, path="$", depth=0)
        return self._build_digest(data, stats, bytes_raw)

    def digest_from_string(self, json_string: str) -> JsonDigest:
        try:
            data = json.loads(json_string)
            return self.digest(data, raw_json=json_string)
        except json.JSONDecodeError as exc:
            return self._error_digest(str(exc), len(json_string))

    def _traverse(
        self,
        node: Any,
        stats: "_TraversalStats",
        path: str,
        depth: int,
    ) -> str:
        if depth > self.MAX_PATH_DEPTH:
            stats.flags.add("extremely_deep")
            return "truncated"

        stats.max_depth = max(stats.max_depth, depth)

        if node is None:
            stats.type_counts["null"] += 1
            stats.null_count += 1
            stats.depth_sum += depth
            stats.leaf_count += 1
            self._maybe_update_sample(stats, path, "null", depth)
            return "null"

        if isinstance(node, bool):
            stats.type_counts["boolean"] += 1
            stats.depth_sum += depth
            stats.leaf_count += 1
            self._maybe_update_sample(stats, path, str(node).lower(), depth)
            return "bool"

        if isinstance(node, (int, float)):
            stats.type_counts["number"] += 1
            stats.depth_sum += depth
            stats.leaf_count += 1
            stats.data_chars += len(str(node))
            self._maybe_update_sample(stats, path, str(node), depth)
            return "number"

        if isinstance(node, str):
            stats.type_counts["string"] += 1
            stats.depth_sum += depth
            stats.leaf_count += 1
            stats.data_chars += len(node)
            if not node:
                stats.empty_string_count += 1
            self._maybe_update_sample(stats, path, node, depth)
            return "string"

        if isinstance(node, dict):
            stats.type_counts["object"] += 1
            stats.object_count += 1
            stats.max_breadth = max(stats.max_breadth, len(node))

            if not node:
                stats.empty_object_count += 1
                return "{}"

            for key in node:
                if len(stats.key_counts) < self.MAX_KEYS_TRACK:
                    stats.key_counts[key] += 1
                self._analyze_key(key, stats)

            child_sigs = []
            for key, value in node.items():
                child_path = f"{path}.{key}"
                sig = self._traverse(value, stats, child_path, depth + 1)
                child_sigs.append(f"{key}:{sig}")

            return "{" + ",".join(sorted(child_sigs)[:10]) + "}"

        if isinstance(node, list):
            stats.type_counts["array"] += 1
            stats.array_count += 1
            stats.max_breadth = max(stats.max_breadth, len(node))

            if not node:
                stats.empty_array_count += 1
                return "[]"

            array_info = {
                "path": path,
                "length": len(node),
                "element_sigs": [],
                "element_types": Counter(),
            }
            sample_indices = self._sample_indices(len(node), self.MAX_ARRAY_SAMPLE)

            for i, item in enumerate(node):
                child_path = f"{path}[{i}]"
                sig = self._traverse(item, stats, child_path, depth + 1)
                if i in sample_indices:
                    array_info["element_sigs"].append(sig)
                    base_type = sig.split("{")[0].split("[")[0]
                    array_info["element_types"][base_type] += 1

            stats.arrays.append(array_info)

            if array_info["element_sigs"]:
                dominant = array_info["element_types"].most_common(1)[0][0]
                return f"[{dominant}*{len(node)}]"
            return "[]"

        stats.type_counts["unknown"] += 1
        return "unknown"

    def _analyze_key(self, key: str, stats: "_TraversalStats") -> None:
        if KEY_PATTERNS["opaque"].match(key):
            stats.key_styles["opaque"] += 1
        elif KEY_PATTERNS["single_char"].match(key):
            stats.key_styles["single_char"] += 1
        elif KEY_PATTERNS["numeric_suffix"].match(key):
            stats.key_styles["numeric_suffix"] += 1
        elif KEY_PATTERNS["semantic"].match(key):
            stats.key_styles["semantic"] += 1
        else:
            stats.key_styles["other"] += 1

        if "_" in key and key == key.lower():
            stats.naming_conventions["snake_case"] += 1
        elif re.match(r"^[a-z]+([A-Z][a-z0-9]*)*$", key):
            stats.naming_conventions["camelCase"] += 1
        elif re.match(r"^[A-Z][a-z0-9]*([A-Z][a-z0-9]*)*$", key):
            stats.naming_conventions["PascalCase"] += 1
        elif key == key.upper() and len(key) > 1:
            stats.naming_conventions["UPPER_CASE"] += 1
        else:
            stats.naming_conventions["other"] += 1

    def _maybe_update_sample(
        self,
        stats: "_TraversalStats",
        path: str,
        value: str,
        depth: int,
    ) -> None:
        score = depth
        if value and value not in ("null", "true", "false"):
            score += 2
        if len(value) > 10:
            score += 1

        if score > stats.best_sample_score:
            stats.best_sample_score = score
            stats.best_sample_path = path
            stats.best_sample_value = value[:self.MAX_SAMPLE_VALUE_LEN]

    def _sample_indices(self, length: int, max_sample: int) -> set:
        if length <= max_sample:
            return set(range(length))
        step = length / max_sample
        return {int(i * step) for i in range(max_sample)}

    def _build_digest(
        self,
        data: Any,
        stats: "_TraversalStats",
        bytes_raw: int,
    ) -> JsonDigest:
        root_type = self._classify_root(data)

        total_types = sum(stats.type_counts.values())
        type_dist = TypeDistribution(
            strings=stats.type_counts["string"] / max(1, total_types),
            numbers=stats.type_counts["number"] / max(1, total_types),
            booleans=stats.type_counts["boolean"] / max(1, total_types),
            nulls=stats.type_counts["null"] / max(1, total_types),
            objects=stats.type_counts["object"] / max(1, total_types),
            arrays=stats.type_counts["array"] / max(1, total_types),
        )

        key_style = self._determine_key_style(stats)
        key_convention = self._determine_naming_convention(stats)
        top_keys = self._get_top_keys(stats)

        array_consistency, array_verdict, dominant_array_type = (
            self._analyze_array_consistency(stats)
        )

        total_values = stats.leaf_count
        empty_count = stats.null_count + stats.empty_string_count
        sparsity = empty_count / max(1, total_values)
        sparsity_verdict = self._classify_sparsity(sparsity)

        bytes_data = stats.data_chars
        density = bytes_data / max(1, bytes_raw)

        depth_avg = stats.depth_sum / max(1, stats.leaf_count)

        hotspot_path, hotspot_pct = self._find_hotspot(stats, total_values)

        schema_hint = self._infer_schema_hint(data, stats, root_type)

        verdict, action = self._determine_verdict(
            array_consistency, sparsity, key_style, stats
        )

        flags = self._compile_flags(stats, stats.max_depth, array_consistency)

        return JsonDigest(
            bytes_raw=bytes_raw,
            bytes_data=bytes_data,
            density=round(density, 3),
            depth_max=stats.max_depth,
            depth_avg=round(depth_avg, 2),
            breadth_max=stats.max_breadth,
            root_type=root_type,
            type_distribution=type_dist.to_compact(),
            total_values=total_values,
            total_keys=len(stats.key_counts),
            total_arrays=stats.array_count,
            total_objects=stats.object_count,
            key_style=key_style,
            key_convention=key_convention,
            top_keys=top_keys,
            array_consistency=round(array_consistency, 3),
            array_consistency_verdict=array_verdict,
            dominant_array_type=dominant_array_type,
            sparsity=round(sparsity, 3),
            sparsity_verdict=sparsity_verdict,
            hotspot_path=hotspot_path,
            hotspot_pct=round(hotspot_pct, 3),
            schema_hint=schema_hint,
            verdict=verdict,
            action=action,
            flags=flags,
            sample_path=stats.best_sample_path or "$",
            sample_value=self._truncate_sample(stats.best_sample_value or ""),
        )

    def _classify_root(self, data: Any) -> str:
        if isinstance(data, dict):
            return "object"
        if isinstance(data, list):
            if not data:
                return "empty_array"
            if all(isinstance(x, dict) for x in data[:10]):
                return "array_of_objects"
            if all(isinstance(x, list) for x in data[:10]):
                return "array_of_arrays"
            if all(
                isinstance(x, (str, int, float, bool, type(None))) for x in data[:10]
            ):
                return "array_of_scalars"
            return "array_mixed"
        return "scalar"

    def _determine_key_style(self, stats: "_TraversalStats") -> str:
        total = sum(stats.key_styles.values())
        if total == 0:
            return "none"
        semantic = stats.key_styles["semantic"]
        opaque = stats.key_styles["opaque"]
        if semantic / max(1, total) > 0.7:
            return "semantic"
        if opaque / max(1, total) > 0.3:
            return "opaque"
        return "mixed"

    def _determine_naming_convention(self, stats: "_TraversalStats") -> str:
        if not stats.naming_conventions:
            return "unknown"
        most_common = stats.naming_conventions.most_common(1)[0]
        total = sum(stats.naming_conventions.values())
        if most_common[1] / total > 0.6:
            return most_common[0]
        return "mixed"

    def _get_top_keys(self, stats: "_TraversalStats") -> str:
        if not stats.key_counts:
            return "none"
        top = stats.key_counts.most_common(5)
        return ", ".join(k for k, _ in top)

    def _analyze_array_consistency(
        self, stats: "_TraversalStats"
    ) -> tuple[float, str, str]:
        if not stats.arrays:
            return 1.0, "n/a", "none"

        non_empty = [a for a in stats.arrays if a["element_sigs"]]
        if not non_empty:
            return 1.0, "n/a", "none"

        consistencies = []
        all_types: Counter = Counter()

        for arr in non_empty:
            sigs = arr["element_sigs"]
            types = arr["element_types"]
            all_types.update(types)

            if sigs:
                sig_counts = Counter(sigs)
                most_common_count = sig_counts.most_common(1)[0][1]
                consistencies.append(most_common_count / len(sigs))

        avg_consistency = (
            sum(consistencies) / len(consistencies) if consistencies else 1.0
        )

        if avg_consistency >= CONSISTENCY["excellent"]:
            verdict = "excellent"
        elif avg_consistency >= CONSISTENCY["good"]:
            verdict = "good"
        elif avg_consistency >= CONSISTENCY["fair"]:
            verdict = "fair"
        elif avg_consistency >= CONSISTENCY["poor"]:
            verdict = "poor"
        else:
            verdict = "chaotic"

        dominant = all_types.most_common(1)[0][0] if all_types else "none"
        return avg_consistency, verdict, dominant

    def _classify_sparsity(self, sparsity: float) -> str:
        if sparsity <= SPARSITY["dense"]:
            return "dense"
        if sparsity <= SPARSITY["normal"]:
            return "normal"
        if sparsity <= SPARSITY["sparse"]:
            return "sparse"
        return "very_sparse"

    def _find_hotspot(
        self, stats: "_TraversalStats", total_values: int
    ) -> tuple[str, float]:
        if not stats.arrays:
            return "$", 1.0
        largest = max(stats.arrays, key=lambda a: a["length"])
        pct = largest["length"] / max(1, total_values)
        return largest["path"], pct

    def _infer_schema_hint(
        self, data: Any, stats: "_TraversalStats", root_type: str
    ) -> str:
        if root_type == "array_of_objects":
            if isinstance(data, list) and data and isinstance(data[0], dict):
                keys = list(data[0].keys())[:5]
                return "{" + ", ".join(keys) + ", ...}[]"
        if root_type == "object":
            if isinstance(data, dict):
                keys = list(data.keys())[:5]
                return "{" + ", ".join(keys) + ", ...}"
        if root_type == "array_of_scalars":
            return "scalar[]"
        if root_type == "array_of_arrays":
            return "[][]"
        return root_type

    def _determine_verdict(
        self,
        array_consistency: float,
        sparsity: float,
        key_style: str,
        stats: "_TraversalStats",
    ) -> tuple[str, str]:
        if array_consistency < CONSISTENCY["poor"]:
            return "chaotic", "inspect_manually"

        if array_consistency < CONSISTENCY["fair"]:
            return "messy", "normalize_first"

        score = 0.0
        score += array_consistency * 0.4
        score += (1 - min(1.0, sparsity)) * 0.2

        if key_style == "semantic":
            score += 0.2
        elif key_style == "mixed":
            score += 0.1
        elif key_style == "opaque":
            score += 0.05

        if stats.max_depth <= DEPTH["moderate"]:
            score += 0.1
        elif stats.max_depth <= DEPTH["deep"]:
            score += 0.05

        if len(stats.flags) == 0:
            score += 0.1

        if score >= 0.75:
            return "structured", "parse_directly"
        if score >= 0.55:
            return "usable", "parse_with_care"
        if score >= 0.35:
            return "messy", "normalize_first"
        return "chaotic", "inspect_manually"

    def _compile_flags(
        self,
        stats: "_TraversalStats",
        max_depth: int,
        array_consistency: float,
    ) -> str:
        flags = list(stats.flags)
        if max_depth > DEPTH["deep"]:
            flags.append("deep_nesting")
        if array_consistency < CONSISTENCY["poor"]:
            flags.append("inconsistent_arrays")
        if stats.empty_array_count + stats.empty_object_count > 10:
            flags.append("many_empties")
        if stats.object_count > 0 and len(stats.key_counts) / stats.object_count > 20:
            flags.append("high_key_variety")
        return ",".join(flags) if flags else ""

    def _truncate_sample(self, value: str) -> str:
        if len(value) <= self.MAX_SAMPLE_VALUE_LEN:
            return f"\"{value}\"" if not value.startswith("\"") else value
        return f"\"{value[:self.MAX_SAMPLE_VALUE_LEN - 3]}...\""

    def _empty_digest(self, bytes_raw: int) -> JsonDigest:
        return JsonDigest(
            bytes_raw=bytes_raw,
            bytes_data=0,
            density=0,
            depth_max=0,
            depth_avg=0,
            breadth_max=0,
            root_type="empty",
            type_distribution="",
            total_values=0,
            total_keys=0,
            total_arrays=0,
            total_objects=0,
            key_style="none",
            key_convention="none",
            top_keys="none",
            array_consistency=1.0,
            array_consistency_verdict="n/a",
            dominant_array_type="none",
            sparsity=0,
            sparsity_verdict="n/a",
            hotspot_path="$",
            hotspot_pct=0,
            schema_hint="empty",
            verdict="minimal",
            action="skip",
            flags="empty",
            sample_path="$",
            sample_value="null",
        )

    def _error_digest(self, error: str, bytes_raw: int) -> JsonDigest:
        return JsonDigest(
            bytes_raw=bytes_raw,
            bytes_data=0,
            density=0,
            depth_max=0,
            depth_avg=0,
            breadth_max=0,
            root_type="invalid",
            type_distribution="",
            total_values=0,
            total_keys=0,
            total_arrays=0,
            total_objects=0,
            key_style="none",
            key_convention="none",
            top_keys="none",
            array_consistency=0,
            array_consistency_verdict="n/a",
            dominant_array_type="none",
            sparsity=0,
            sparsity_verdict="n/a",
            hotspot_path="$",
            hotspot_pct=0,
            schema_hint="invalid",
            verdict="chaotic",
            action="skip",
            flags="parse_error",
            sample_path="$",
            sample_value=f"error: {error[:50]}",
        )


class _TraversalStats:
    def __init__(self) -> None:
        self.max_depth = 0
        self.depth_sum = 0
        self.leaf_count = 0
        self.max_breadth = 0
        self.type_counts: Counter = Counter()
        self.key_counts: Counter = Counter()
        self.key_styles: Counter = Counter()
        self.naming_conventions: Counter = Counter()
        self.arrays: list[dict] = []
        self.array_count = 0
        self.object_count = 0
        self.null_count = 0
        self.empty_string_count = 0
        self.empty_array_count = 0
        self.empty_object_count = 0
        self.data_chars = 0
        self.best_sample_path = ""
        self.best_sample_value = ""
        self.best_sample_score = -1.0
        self.flags: set[str] = set()


_digestor = JsonDigestor()


def digest(data: Any, raw_json: Optional[str] = None) -> JsonDigest:
    return _digestor.digest(data, raw_json)


def digest_string(json_string: str) -> JsonDigest:
    return _digestor.digest_from_string(json_string)


def digest_to_prompt(data: Any) -> str:
    return _digestor.digest(data).to_prompt()
