import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional


SAMPLE_SIZE = 1000
MAX_TABLES_DETAIL = 20
MAX_COLUMNS_DETAIL = 50
MAX_UNIQUE_VALUES = 500
MAX_STRING_SAMPLE_LEN = 200

CARDINALITY = {
    "unique": 0.95,
    "high": 0.50,
    "medium": 0.10,
    "low": 0.01,
}

CONTENT_PATTERNS = {
    "uuid": re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.I,
    ),
    "email": re.compile(r"^[^@]+@[^@]+\.[^@]+$"),
    "url": re.compile(r"^https?://\S+$"),
    "json_object": re.compile(r"^\s*\{.*\}\s*$", re.S),
    "json_array": re.compile(r"^\s*\[.*\]\s*$", re.S),
    "iso_datetime": re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"),
    "iso_date": re.compile(r"^\d{4}-\d{2}-\d{2}$"),
    "unix_timestamp": re.compile(r"^1[0-9]{9}$"),
    "base64": re.compile(r"^[A-Za-z0-9+/]{20,}={0,2}$"),
    "hex": re.compile(r"^[0-9a-fA-F]{16,}$"),
    "numeric_string": re.compile(r"^-?\d+\.?\d*$"),
    "empty": re.compile(r"^[\s\n]*$"),
    "path": re.compile(r"^(/|[A-Za-z]:\\)[\\w./\\\\-]+$"),
}

STRING_ENTROPY = {
    "prose": (3.5, 4.8),
    "code": (4.5, 5.5),
    "id": (4.0, 5.0),
    "noise": (5.5, 8.0),
}


@dataclass
class ColumnDigest:
    name: str
    declared_type: str
    actual_type: str
    null_pct: float
    unique_pct: float
    cardinality_class: str
    sample_values: str
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    avg_length: Optional[float] = None
    content_pattern: Optional[str] = None
    entropy: Optional[float] = None
    is_primary_key: bool = False
    is_foreign_key: bool = False
    is_indexed: bool = False

    def to_compact(self) -> str:
        flags = []
        if self.is_primary_key:
            flags.append("PK")
        if self.is_foreign_key:
            flags.append("FK")
        if self.is_indexed and not self.is_primary_key:
            flags.append("IDX")
        flag_str = f" [{','.join(flags)}]" if flags else ""
        type_str = self.actual_type
        if self.content_pattern and self.content_pattern != self.actual_type:
            type_str = f"{self.actual_type}({self.content_pattern})"
        return f"{self.name}: {type_str} | null:{self.null_pct:.0%} uniq:{self.unique_pct:.0%}{flag_str}"


@dataclass
class TableDigest:
    name: str
    row_count: int
    column_count: int
    size_bytes: int
    columns: list[ColumnDigest] = field(default_factory=list)
    primary_key: Optional[str] = None
    foreign_keys: list[str] = field(default_factory=list)
    indexes: list[str] = field(default_factory=list)
    null_density: float = 0.0
    duplicate_rows_pct: float = 0.0
    is_junction: bool = False
    is_lookup: bool = False
    is_log: bool = False

    def to_compact(self) -> str:
        flags = []
        if self.is_junction:
            flags.append("junction")
        if self.is_lookup:
            flags.append("lookup")
        if self.is_log:
            flags.append("log")
        flag_str = f" ({', '.join(flags)})" if flags else ""
        col_summary = ", ".join(c.name for c in self.columns[:5])
        if len(self.columns) > 5:
            col_summary += f", +{len(self.columns) - 5} more"
        return f"{self.name}: {self.row_count:,} rows x {self.column_count} cols{flag_str} | {col_summary}"


@dataclass
class RelationshipDigest:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    relation_type: str
    confidence: float

    def to_compact(self) -> str:
        suffix = f" ({self.confidence:.0%})" if self.relation_type == "implicit_fk" else ""
        return f"{self.from_table}.{self.from_column} -> {self.to_table}.{self.to_column}{suffix}"


@dataclass(frozen=True)
class SQLiteDigest:
    file_size: int
    page_count: int
    table_count: int
    view_count: int
    index_count: int
    trigger_count: int
    total_rows: int
    total_columns: int
    tables_summary: str
    largest_tables: str
    explicit_fk_count: int
    implicit_fk_count: int
    relationships_summary: str
    overall_null_pct: float
    overall_null_verdict: str
    type_consistency: float
    type_consistency_verdict: str
    detected_json_columns: int
    detected_datetime_columns: int
    detected_id_columns: int
    has_junction_tables: bool
    has_lookup_tables: bool
    has_log_tables: bool
    has_soft_deletes: bool
    has_timestamps: bool
    has_audit_fields: bool
    schema_pattern: str
    verdict: str
    action: str
    flags: str
    sample_table: str

    def summary_line(self) -> str:
        parts = [
            f"tables={self.table_count}",
            f"rows={self.total_rows}",
            f"verdict={self.verdict}",
            f"action={self.action}",
            f"schema={self.schema_pattern}",
        ]
        if self.flags:
            parts.append(f"flags={self.flags}")
        return " ".join(parts)

    def to_prompt(self) -> str:
        return (
            "<sqlite_digest>\n"
            f"file: {self._human_bytes(self.file_size)} | {self.page_count} pages\n"
            f"schema: {self.table_count} tables, {self.view_count} views, "
            f"{self.index_count} indexes, {self.trigger_count} triggers\n"
            f"data: {self.total_rows:,} total rows across {self.total_columns} columns\n"
            "\n"
            f"tables: {self.tables_summary}\n"
            f"largest: {self.largest_tables}\n"
            "\n"
            f"relationships: {self.explicit_fk_count} explicit FK, {self.implicit_fk_count} implicit\n"
            f"  {self.relationships_summary}\n"
            "\n"
            f"quality: nulls={self.overall_null_verdict} ({self.overall_null_pct:.0%}) | "
            f"types={self.type_consistency_verdict} ({self.type_consistency:.0%})\n"
            f"content: {self.detected_json_columns} json cols, "
            f"{self.detected_datetime_columns} datetime cols, {self.detected_id_columns} id cols\n"
            "\n"
            f"patterns: {self._pattern_flags()}\n"
            f"schema_style: {self.schema_pattern}\n"
            "\n"
            f"VERDICT: {self.verdict} -> {self.action}\n"
            f"{f'flags: {self.flags}' if self.flags else ''}\n"
            "\n"
            f"sample_table: {self.sample_table}\n"
            "</sqlite_digest>"
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
        if b < 1024 * 1024 * 1024:
            return f"{b / (1024 * 1024):.1f}MB"
        return f"{b / (1024 * 1024 * 1024):.1f}GB"

    def _pattern_flags(self) -> str:
        patterns = []
        if self.has_junction_tables:
            patterns.append("junction_tables")
        if self.has_lookup_tables:
            patterns.append("lookup_tables")
        if self.has_log_tables:
            patterns.append("log_tables")
        if self.has_soft_deletes:
            patterns.append("soft_deletes")
        if self.has_timestamps:
            patterns.append("timestamps")
        if self.has_audit_fields:
            patterns.append("audit_fields")
        return ", ".join(patterns) if patterns else "none detected"


class SQLiteDigestor:
    def __init__(self, sample_size: int = SAMPLE_SIZE) -> None:
        self.sample_size = sample_size

    def digest(self, db_path: str) -> SQLiteDigest:
        path = Path(db_path)
        if not path.exists():
            return self._error_digest(f"File not found: {db_path}")

        file_size = path.stat().st_size

        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            return self._analyze(conn, file_size)
        except sqlite3.Error as exc:
            return self._error_digest(str(exc))
        finally:
            if "conn" in locals():
                conn.close()

    def digest_connection(self, conn: sqlite3.Connection) -> SQLiteDigest:
        return self._analyze(conn, file_size=0)

    def _analyze(self, conn: sqlite3.Connection, file_size: int) -> SQLiteDigest:
        cur = conn.cursor()

        page_count = self._get_pragma(cur, "page_count")
        tables = self._get_tables(cur)
        views = self._get_views(cur)
        indexes = self._get_indexes(cur)
        triggers = self._get_triggers(cur)

        if not tables:
            return self._empty_digest(file_size, page_count)

        table_digests: list[TableDigest] = []
        all_columns: list[ColumnDigest] = []

        for table_name in tables[:MAX_TABLES_DETAIL]:
            td = self._analyze_table(cur, table_name)
            table_digests.append(td)
            all_columns.extend(td.columns)

        explicit_fks = self._get_explicit_fks(cur, tables)
        implicit_fks = self._detect_implicit_fks(table_digests)
        all_relationships = explicit_fks + implicit_fks

        total_rows = sum(t.row_count for t in table_digests)
        total_columns = sum(t.column_count for t in table_digests)

        null_pcts = [c.null_pct for c in all_columns if c.null_pct is not None]
        overall_null = statistics.mean(null_pcts) if null_pcts else 0
        null_verdict = self._classify_null_density(overall_null)

        type_consistent = sum(1 for c in all_columns if c.actual_type != "MIXED")
        type_consistency = type_consistent / max(1, len(all_columns))
        type_verdict = self._classify_type_consistency(type_consistency)

        json_cols = sum(1 for c in all_columns if c.content_pattern == "json")
        datetime_cols = sum(
            1
            for c in all_columns
            if c.content_pattern in ("datetime", "date", "timestamp")
        )
        id_cols = sum(
            1
            for c in all_columns
            if c.content_pattern in ("uuid", "hash", "id")
        )

        has_junction = any(t.is_junction for t in table_digests)
        has_lookup = any(t.is_lookup for t in table_digests)
        has_log = any(t.is_log for t in table_digests)

        all_col_names = [c.name.lower() for c in all_columns]
        has_soft_deletes = any(
            name in all_col_names for name in ("deleted_at", "is_deleted", "deleted", "removed_at")
        )
        has_timestamps = any(
            name in all_col_names for name in ("created_at", "updated_at", "modified_at", "timestamp")
        )
        has_audit = any(
            name in all_col_names for name in ("created_by", "updated_by", "modified_by", "author_id")
        )

        schema_pattern = self._classify_schema_pattern(table_digests, all_relationships)

        verdict, action = self._determine_verdict(
            type_consistency,
            overall_null,
            len(all_relationships),
            len(tables),
            schema_pattern,
            table_digests,
        )

        flags = self._compile_flags(table_digests, all_columns, all_relationships)

        tables_summary = self._build_tables_summary(table_digests, len(tables))
        largest_tables = self._build_largest_tables(table_digests)
        relationships_summary = self._build_relationships_summary(all_relationships)
        sample_table = self._build_sample_table(table_digests)

        return SQLiteDigest(
            file_size=file_size,
            page_count=page_count,
            table_count=len(tables),
            view_count=len(views),
            index_count=len(indexes),
            trigger_count=len(triggers),
            total_rows=total_rows,
            total_columns=total_columns,
            tables_summary=tables_summary,
            largest_tables=largest_tables,
            explicit_fk_count=len(explicit_fks),
            implicit_fk_count=len(implicit_fks),
            relationships_summary=relationships_summary,
            overall_null_pct=round(overall_null, 3),
            overall_null_verdict=null_verdict,
            type_consistency=round(type_consistency, 3),
            type_consistency_verdict=type_verdict,
            detected_json_columns=json_cols,
            detected_datetime_columns=datetime_cols,
            detected_id_columns=id_cols,
            has_junction_tables=has_junction,
            has_lookup_tables=has_lookup,
            has_log_tables=has_log,
            has_soft_deletes=has_soft_deletes,
            has_timestamps=has_timestamps,
            has_audit_fields=has_audit,
            schema_pattern=schema_pattern,
            verdict=verdict,
            action=action,
            flags=flags,
            sample_table=sample_table,
        )

    def _get_pragma(self, cur: sqlite3.Cursor, pragma: str) -> int:
        try:
            cur.execute(f"PRAGMA {pragma}")
            result = cur.fetchone()
            return result[0] if result else 0
        except Exception:
            return 0

    def _get_tables(self, cur: sqlite3.Cursor) -> list[str]:
        cur.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """
        )
        return [row[0] for row in cur.fetchall()]

    def _get_views(self, cur: sqlite3.Cursor) -> list[str]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='view'")
        return [row[0] for row in cur.fetchall()]

    def _get_indexes(self, cur: sqlite3.Cursor) -> list[str]:
        cur.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type='index' AND name NOT LIKE 'sqlite_%'
        """
        )
        return [row[0] for row in cur.fetchall()]

    def _get_triggers(self, cur: sqlite3.Cursor) -> list[str]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        return [row[0] for row in cur.fetchall()]

    def _analyze_table(self, cur: sqlite3.Cursor, table_name: str) -> TableDigest:
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            row_count = cur.fetchone()[0]
        except Exception:
            row_count = 0

        cur.execute(f'PRAGMA table_info("{table_name}")')
        columns_info = cur.fetchall()

        cur.execute(f'PRAGMA index_list("{table_name}")')
        index_list = cur.fetchall()
        indexed_columns = set()
        for idx in index_list:
            try:
                cur.execute(f'PRAGMA index_info("{idx[1]}")')
                for col_info in cur.fetchall():
                    indexed_columns.add(col_info[2])
            except Exception:
                continue

        cur.execute(f'PRAGMA foreign_key_list("{table_name}")')
        fk_list = cur.fetchall()
        fk_columns = {fk[3]: f"{fk[2]}.{fk[4]}" for fk in fk_list}

        pk_columns = [col[1] for col in columns_info if col[5] > 0]
        primary_key = pk_columns[0] if len(pk_columns) == 1 else (
            f"({', '.join(pk_columns)})" if pk_columns else None
        )

        sample_data = self._sample_table(cur, table_name, columns_info)

        column_digests = []
        for col in columns_info[:MAX_COLUMNS_DETAIL]:
            col_name = col[1]
            col_type = col[2] or "NONE"
            is_pk = col[5] > 0
            cd = self._analyze_column(
                col_name,
                col_type,
                sample_data.get(col_name, []),
                row_count,
                is_pk=is_pk,
                is_fk=col_name in fk_columns,
                is_indexed=col_name in indexed_columns,
            )
            column_digests.append(cd)

        null_counts = [c.null_pct for c in column_digests]
        null_density = statistics.mean(null_counts) if null_counts else 0

        size_bytes = self._estimate_table_size(cur, table_name)

        is_junction = self._is_junction_table(column_digests, fk_list)
        is_lookup = self._is_lookup_table(row_count, column_digests)
        is_log = self._is_log_table(table_name, column_digests)

        return TableDigest(
            name=table_name,
            row_count=row_count,
            column_count=len(columns_info),
            size_bytes=size_bytes,
            columns=column_digests,
            primary_key=primary_key,
            foreign_keys=[f"{k} -> {v}" for k, v in fk_columns.items()],
            indexes=[idx[1] for idx in index_list],
            null_density=null_density,
            is_junction=is_junction,
            is_lookup=is_lookup,
            is_log=is_log,
        )

    def _sample_table(
        self,
        cur: sqlite3.Cursor,
        table_name: str,
        columns_info: list,
    ) -> dict[str, list]:
        col_names = [col[1] for col in columns_info[:MAX_COLUMNS_DETAIL]]
        if not col_names:
            return {}

        cols_sql = ", ".join(f'"{c}"' for c in col_names)
        try:
            cur.execute(
                f"""
                SELECT {cols_sql} FROM "{table_name}"
                ORDER BY RANDOM()
                LIMIT {self.sample_size}
            """
            )
            rows = cur.fetchall()
        except Exception:
            return {}

        result: dict[str, list] = {name: [] for name in col_names}
        for row in rows:
            for idx, name in enumerate(col_names):
                if idx < len(row):
                    result[name].append(row[idx])
        return result

    def _analyze_column(
        self,
        name: str,
        declared_type: str,
        values: list,
        total_rows: int,
        *,
        is_pk: bool = False,
        is_fk: bool = False,
        is_indexed: bool = False,
    ) -> ColumnDigest:
        if not values:
            return ColumnDigest(
                name=name,
                declared_type=declared_type,
                actual_type="UNKNOWN",
                null_pct=1.0,
                unique_pct=0.0,
                cardinality_class="unknown",
                sample_values="(no data)",
                is_primary_key=is_pk,
                is_foreign_key=is_fk,
                is_indexed=is_indexed,
            )

        null_count = sum(1 for v in values if v is None)
        null_pct = null_count / len(values)
        non_null = [v for v in values if v is not None]

        if not non_null:
            return ColumnDigest(
                name=name,
                declared_type=declared_type,
                actual_type="NULL",
                null_pct=1.0,
                unique_pct=0.0,
                cardinality_class="constant",
                sample_values="(all null)",
                is_primary_key=is_pk,
                is_foreign_key=is_fk,
                is_indexed=is_indexed,
            )

        unique_count = len(set(str(v)[:100] for v in non_null[:MAX_UNIQUE_VALUES]))
        unique_pct = unique_count / len(non_null)
        cardinality_class = self._classify_cardinality(unique_pct)

        actual_type, content_pattern = self._detect_actual_type(non_null)

        min_val = max_val = avg_length = entropy = None
        if actual_type in ("INTEGER", "FLOAT"):
            numeric_vals = [v for v in non_null if isinstance(v, (int, float))]
            if numeric_vals:
                min_val = min(numeric_vals)
                max_val = max(numeric_vals)

        if actual_type in ("TEXT", "JSON", "UUID", "EMAIL", "URL", "PATH"):
            str_vals = [str(v) for v in non_null]
            lengths = [len(s) for s in str_vals[:100]]
            avg_length = statistics.mean(lengths) if lengths else 0
            combined = "".join(str_vals[:50])[:5000]
            entropy = self._calculate_entropy(combined) if combined else 0

        sample_vals = self._build_sample_values(non_null[:10])

        return ColumnDigest(
            name=name,
            declared_type=declared_type,
            actual_type=actual_type,
            null_pct=round(null_pct, 3),
            unique_pct=round(unique_pct, 3),
            cardinality_class=cardinality_class,
            sample_values=sample_vals,
            min_val=min_val,
            max_val=max_val,
            avg_length=round(avg_length, 1) if avg_length is not None else None,
            content_pattern=content_pattern,
            entropy=round(entropy, 2) if entropy is not None else None,
            is_primary_key=is_pk,
            is_foreign_key=is_fk,
            is_indexed=is_indexed,
        )

    def _detect_actual_type(self, values: list) -> tuple[str, Optional[str]]:
        type_counts: Counter = Counter()
        pattern_counts: Counter = Counter()

        for val in values[:200]:
            if isinstance(val, bool):
                type_counts["BOOLEAN"] += 1
            elif isinstance(val, int):
                type_counts["INTEGER"] += 1
            elif isinstance(val, float):
                type_counts["FLOAT"] += 1
            elif isinstance(val, bytes):
                type_counts["BLOB"] += 1
            elif isinstance(val, str):
                type_counts["TEXT"] += 1
                val_sample = val[:MAX_STRING_SAMPLE_LEN]
                for pattern_name, pattern in CONTENT_PATTERNS.items():
                    if pattern.match(val_sample):
                        pattern_counts[pattern_name] += 1
                        break
            else:
                type_counts["UNKNOWN"] += 1

        if not type_counts:
            return "UNKNOWN", None

        dominant_type, dominant_count = type_counts.most_common(1)[0]
        total = sum(type_counts.values())

        if dominant_count / total < 0.8:
            actual_type = "MIXED"
        else:
            actual_type = dominant_type

        content_pattern = None
        if pattern_counts:
            top_pattern, pattern_count = pattern_counts.most_common(1)[0]
            text_count = type_counts.get("TEXT", 0)
            if text_count > 0 and pattern_count / text_count > 0.5:
                content_pattern = top_pattern
                if top_pattern in ("json_object", "json_array"):
                    actual_type = "JSON"
                    content_pattern = "json"
                elif top_pattern == "uuid":
                    actual_type = "UUID"
                elif top_pattern == "email":
                    actual_type = "EMAIL"
                elif top_pattern == "url":
                    actual_type = "URL"
                elif top_pattern == "path":
                    actual_type = "PATH"
                elif top_pattern in ("iso_datetime", "iso_date", "unix_timestamp"):
                    actual_type = "DATETIME"
                    if top_pattern == "iso_date":
                        content_pattern = "date"
                    elif top_pattern == "unix_timestamp":
                        content_pattern = "timestamp"
                    else:
                        content_pattern = "datetime"
                elif top_pattern in ("base64", "hex"):
                    content_pattern = "hash"

        return actual_type, content_pattern

    def _calculate_entropy(self, text: str) -> float:
        if not text:
            return 0.0
        freq = Counter(text)
        n = len(text)
        return -sum((c / n) * math.log2(c / n) for c in freq.values() if c > 0)

    def _classify_cardinality(self, unique_pct: float) -> str:
        if unique_pct >= CARDINALITY["unique"]:
            return "unique"
        if unique_pct >= CARDINALITY["high"]:
            return "high"
        if unique_pct >= CARDINALITY["medium"]:
            return "medium"
        if unique_pct >= CARDINALITY["low"]:
            return "low"
        return "constant"

    def _build_sample_values(self, values: list) -> str:
        samples = []
        for val in values[:5]:
            if val is None:
                continue
            s = str(val)
            if len(s) > 30:
                s = s[:27] + "..."
            samples.append(s)
        if not samples:
            return "(empty)"
        result = ", ".join(samples)
        if len(result) > 80:
            result = result[:77] + "..."
        return result

    def _estimate_table_size(self, cur: sqlite3.Cursor, table_name: str) -> int:
        try:
            cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
            row_count = cur.fetchone()[0]
            cur.execute(f'PRAGMA table_info("{table_name}")')
            col_count = len(cur.fetchall())
            return row_count * col_count * 50
        except Exception:
            return 0

    def _is_junction_table(self, columns: list[ColumnDigest], fk_list: list) -> bool:
        if len(columns) < 2 or len(columns) > 5:
            return False
        fk_count = sum(1 for c in columns if c.is_foreign_key)
        return fk_count >= 2

    def _is_lookup_table(self, row_count: int, columns: list[ColumnDigest]) -> bool:
        if row_count > 100 or row_count < 1:
            return False
        if len(columns) < 2 or len(columns) > 5:
            return False
        has_id = any(c.is_primary_key or "id" in c.name.lower() for c in columns)
        has_name = any(
            name in c.name.lower()
            for c in columns
            for name in ("name", "label", "value", "code", "title")
        )
        return has_id and has_name

    def _is_log_table(self, table_name: str, columns: list[ColumnDigest]) -> bool:
        name_lower = table_name.lower()
        if any(pat in name_lower for pat in ("log", "audit", "history", "event", "activity")):
            return True
        col_names = [c.name.lower() for c in columns]
        has_timestamp = any(
            name in col_names for name in ("timestamp", "created_at", "logged_at", "event_time")
        )
        has_action = any(name in col_names for name in ("action", "event", "type", "operation"))
        return has_timestamp and has_action

    def _get_explicit_fks(self, cur: sqlite3.Cursor, tables: list[str]) -> list[RelationshipDigest]:
        relationships = []
        for table in tables:
            try:
                cur.execute(f'PRAGMA foreign_key_list("{table}")')
                for fk in cur.fetchall():
                    relationships.append(
                        RelationshipDigest(
                            from_table=table,
                            from_column=fk[3],
                            to_table=fk[2],
                            to_column=fk[4],
                            relation_type="explicit_fk",
                            confidence=1.0,
                        )
                    )
            except Exception:
                continue
        return relationships

    def _detect_implicit_fks(self, table_digests: list[TableDigest]) -> list[RelationshipDigest]:
        relationships = []
        pk_index: dict[str, ColumnDigest] = {}
        for td in table_digests:
            for cd in td.columns:
                if cd.is_primary_key:
                    pk_index[td.name] = cd

        for td in table_digests:
            for cd in td.columns:
                if cd.is_foreign_key or cd.is_primary_key:
                    continue
                col_lower = cd.name.lower()
                if col_lower.endswith("_id"):
                    potential_table = col_lower[:-3]
                    for other_td in table_digests:
                        if other_td.name.lower() == potential_table:
                            if other_td.name in pk_index:
                                relationships.append(
                                    RelationshipDigest(
                                        from_table=td.name,
                                        from_column=cd.name,
                                        to_table=other_td.name,
                                        to_column=pk_index[other_td.name].name,
                                        relation_type="implicit_fk",
                                        confidence=0.8,
                                    )
                                )
                            break
        return relationships[:20]

    def _classify_null_density(self, null_pct: float) -> str:
        if null_pct < 0.05:
            return "dense"
        if null_pct < 0.20:
            return "normal"
        if null_pct < 0.50:
            return "sparse"
        return "very_sparse"

    def _classify_type_consistency(self, consistency: float) -> str:
        if consistency >= 0.95:
            return "excellent"
        if consistency >= 0.80:
            return "good"
        if consistency >= 0.60:
            return "fair"
        return "poor"

    def _classify_schema_pattern(
        self,
        tables: list[TableDigest],
        relationships: list[RelationshipDigest],
    ) -> str:
        if not tables:
            return "empty"
        if len(tables) == 1:
            return "flat"

        rel_count = len(relationships)
        table_count = len(tables)

        if rel_count == 0:
            return "flat"

        rel_density = rel_count / table_count
        junction_count = sum(1 for t in tables if t.is_junction)

        if junction_count > 0 and rel_density > 1:
            return "normalized"

        if rel_density > 2:
            fk_targets = Counter(r.to_table for r in relationships)
            if fk_targets:
                top_target_count = fk_targets.most_common(1)[0][1]
                if top_target_count >= len(tables) * 0.5:
                    return "star"

        if rel_density > 1:
            return "normalized"
        if rel_density > 0.3:
            return "relational"
        return "loosely_coupled"

    def _determine_verdict(
        self,
        type_consistency: float,
        null_pct: float,
        rel_count: int,
        table_count: int,
        schema_pattern: str,
        tables: list[TableDigest],
    ) -> tuple[str, str]:
        score = 0.0
        score += type_consistency * 0.3
        score += (1 - null_pct) * 0.2
        if rel_count > 0:
            score += 0.2
        if schema_pattern in ("normalized", "star", "relational"):
            score += 0.2
        elif schema_pattern == "flat":
            score += 0.1
        if any(t.is_lookup for t in tables):
            score += 0.05
        if any(t.is_log for t in tables):
            score += 0.05

        if score >= 0.75:
            return "clean", "query_directly"
        if score >= 0.55:
            return "usable", "inspect_schema"
        if score >= 0.35:
            return "messy", "needs_cleaning"
        return "chaotic", "investigate"

    def _compile_flags(
        self,
        tables: list[TableDigest],
        columns: list[ColumnDigest],
        relationships: list[RelationshipDigest],
    ) -> str:
        flags = []
        large_tables = [t for t in tables if t.row_count > 1_000_000]
        if large_tables:
            flags.append(f"large_tables({len(large_tables)})")
        mixed_cols = [c for c in columns if c.actual_type == "MIXED"]
        if len(mixed_cols) > 3:
            flags.append(f"mixed_types({len(mixed_cols)})")
        json_cols = [c for c in columns if c.actual_type == "JSON"]
        if json_cols:
            flags.append(f"has_json({len(json_cols)})")
        high_null = [c for c in columns if c.null_pct > 0.5]
        if len(high_null) > 3:
            flags.append(f"high_nulls({len(high_null)})")
        if not relationships and len(tables) > 1:
            flags.append("no_relationships")
        return ",".join(flags) if flags else ""

    def _build_tables_summary(self, tables: list[TableDigest], total_tables: int) -> str:
        parts = []
        for table in tables[:8]:
            parts.append(f"{table.name}({table.column_count})")
        if total_tables > 8:
            parts.append(f"+{total_tables - 8} more")
        return ", ".join(parts)

    def _build_largest_tables(self, tables: list[TableDigest]) -> str:
        sorted_tables = sorted(tables, key=lambda t: t.row_count, reverse=True)
        parts = []
        for table in sorted_tables[:3]:
            parts.append(f"{table.name}: {table.row_count:,}")
        return ", ".join(parts) if parts else "none"

    def _build_relationships_summary(self, relationships: list[RelationshipDigest]) -> str:
        if not relationships:
            return "none detected"
        parts = [rel.to_compact() for rel in relationships[:5]]
        if len(relationships) > 5:
            parts.append(f"+{len(relationships) - 5} more")
        return "; ".join(parts)

    def _build_sample_table(self, tables: list[TableDigest]) -> str:
        if not tables:
            return "no tables"
        candidates = sorted(
            tables,
            key=lambda t: (
                1 if 100 < t.row_count < 10000 else 0,
                len(t.foreign_keys),
                t.column_count,
            ),
            reverse=True,
        )
        sample = candidates[0]
        return sample.to_compact()

    def _empty_digest(self, file_size: int, page_count: int) -> SQLiteDigest:
        return SQLiteDigest(
            file_size=file_size,
            page_count=page_count,
            table_count=0,
            view_count=0,
            index_count=0,
            trigger_count=0,
            total_rows=0,
            total_columns=0,
            tables_summary="none",
            largest_tables="none",
            explicit_fk_count=0,
            implicit_fk_count=0,
            relationships_summary="none",
            overall_null_pct=0,
            overall_null_verdict="n/a",
            type_consistency=1.0,
            type_consistency_verdict="n/a",
            detected_json_columns=0,
            detected_datetime_columns=0,
            detected_id_columns=0,
            has_junction_tables=False,
            has_lookup_tables=False,
            has_log_tables=False,
            has_soft_deletes=False,
            has_timestamps=False,
            has_audit_fields=False,
            schema_pattern="empty",
            verdict="minimal",
            action="skip",
            flags="empty",
            sample_table="none",
        )

    def _error_digest(self, error: str) -> SQLiteDigest:
        return SQLiteDigest(
            file_size=0,
            page_count=0,
            table_count=0,
            view_count=0,
            index_count=0,
            trigger_count=0,
            total_rows=0,
            total_columns=0,
            tables_summary="error",
            largest_tables="error",
            explicit_fk_count=0,
            implicit_fk_count=0,
            relationships_summary="error",
            overall_null_pct=0,
            overall_null_verdict="error",
            type_consistency=0,
            type_consistency_verdict="error",
            detected_json_columns=0,
            detected_datetime_columns=0,
            detected_id_columns=0,
            has_junction_tables=False,
            has_lookup_tables=False,
            has_log_tables=False,
            has_soft_deletes=False,
            has_timestamps=False,
            has_audit_fields=False,
            schema_pattern="error",
            verdict="error",
            action="investigate",
            flags=f"error: {error[:50]}",
            sample_table="error",
        )


_digestor = SQLiteDigestor()


def digest(db_path: str) -> SQLiteDigest:
    return _digestor.digest(db_path)


def digest_connection(conn: sqlite3.Connection) -> SQLiteDigest:
    return _digestor.digest_connection(conn)


def digest_to_prompt(db_path: str) -> str:
    return _digestor.digest(db_path).to_prompt()
