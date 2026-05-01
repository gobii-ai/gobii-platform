import json
import logging
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

import sqlparse
from django.db import transaction

from api.agent.tools.sqlite_guardrails import (
    clear_guarded_connection,
    open_guarded_sqlite_connection,
    start_query_timer,
    stop_query_timer,
)
from api.agent.tools.sqlite_state import agent_sqlite_db_snapshot
from api.models import (
    PersistentAgent,
    PersistentAgentDashboard,
    PersistentAgentDashboardWidget,
)

logger = logging.getLogger(__name__)

MAX_DASHBOARD_WIDGETS = 8
MAX_WIDGET_ROWS = 100
MAX_WIDGET_RESULT_BYTES = 40_000
MAX_WIDGET_SQL_BYTES = 12_000
DASHBOARD_QUERY_TIMEOUT_SECONDS = 5.0

EPHEMERAL_OR_INTERNAL_TABLE_RE = re.compile(
    r'(?i)(?:^|[^A-Za-z0-9_])["`]?(__[A-Za-z0-9_]+|sqlite_master|sqlite_schema)["`]?'
)


class DashboardValidationError(ValueError):
    """Raised when a dashboard definition is unsafe or not renderable."""


def _clean_text(value: Any, *, max_length: int, field_name: str) -> str:
    if not isinstance(value, str):
        raise DashboardValidationError(f"{field_name} must be a string.")
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        raise DashboardValidationError(f"{field_name} is required.")
    return cleaned[:max_length]


def _clean_optional_text(value: Any, *, max_length: int, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise DashboardValidationError(f"{field_name} must be a string.")
    cleaned = re.sub(r"\s+", " ", value.strip())
    return cleaned[:max_length]


def _normalize_sql(sql: Any) -> str:
    if not isinstance(sql, str):
        raise DashboardValidationError("Widget SQL must be a string.")
    cleaned = sql.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()
    if not cleaned:
        raise DashboardValidationError("Widget SQL is required.")
    if len(cleaned.encode("utf-8")) > MAX_WIDGET_SQL_BYTES:
        raise DashboardValidationError("Widget SQL is too large.")

    statements = [statement.strip() for statement in sqlparse.split(cleaned) if statement.strip()]
    if len(statements) != 1:
        raise DashboardValidationError("Widget SQL must be a single SELECT statement.")
    statement = statements[0].rstrip(";").strip()
    upper = statement.upper()
    if not (upper.startswith("SELECT") or upper.startswith("WITH")):
        raise DashboardValidationError("Widget SQL must start with SELECT or WITH.")
    if EPHEMERAL_OR_INTERNAL_TABLE_RE.search(statement):
        raise DashboardValidationError("Dashboard widgets cannot query internal or ephemeral SQLite tables.")
    return statement


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float)) or value is None:
        return value
    if isinstance(value, bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<binary {len(value)} bytes>"
    if isinstance(value, memoryview):
        return f"<binary {len(value)} bytes>"
    return str(value)


def _is_numericish(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        try:
            float(value.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def execute_dashboard_select(db_path: str, sql: str) -> dict[str, Any]:
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = open_guarded_sqlite_connection(
            db_path,
            timeout_seconds=DASHBOARD_QUERY_TIMEOUT_SECONDS,
        )
        conn.execute("PRAGMA query_only = ON;")
        cursor = conn.cursor()
        start_query_timer(conn)
        cursor.execute(sql)
        if cursor.description is None:
            raise DashboardValidationError("Widget SQL must return rows.")

        columns = [str(column[0]) for column in cursor.description]
        raw_rows = cursor.fetchmany(MAX_WIDGET_ROWS + 1)
        truncated = len(raw_rows) > MAX_WIDGET_ROWS
        raw_rows = raw_rows[:MAX_WIDGET_ROWS]

        rows = [
            {
                columns[index]: _json_safe(value)
                for index, value in enumerate(row)
            }
            for row in raw_rows
        ]
        while rows:
            encoded = json.dumps(rows, default=str).encode("utf-8")
            if len(encoded) <= MAX_WIDGET_RESULT_BYTES:
                break
            rows.pop()
            truncated = True

        return {
            "status": "ok",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
        }
    except sqlite3.Error as exc:
        return {
            "status": "error",
            "message": f"Query failed: {exc}",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }
    except RuntimeError as exc:
        return {
            "status": "error",
            "message": f"Query failed: {exc}",
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }
    except DashboardValidationError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
        }
    finally:
        if conn is not None:
            stop_query_timer(conn)
            try:
                clear_guarded_connection(conn)
                conn.close()
            except sqlite3.Error:
                logger.debug("Failed to close dashboard SQLite connection", exc_info=True)


def _validated_widget_from_result(widget: dict[str, Any], result: dict[str, Any], position: int) -> dict[str, Any]:
    widget_type = widget.get("type") or widget.get("widget_type")
    if widget_type not in {
        PersistentAgentDashboardWidget.WidgetType.METRIC,
        PersistentAgentDashboardWidget.WidgetType.TABLE,
        PersistentAgentDashboardWidget.WidgetType.BAR,
    }:
        raise DashboardValidationError("Widget type must be one of: metric, table, bar.")

    title = _clean_text(widget.get("title"), max_length=160, field_name="Widget title")
    sql = _normalize_sql(widget.get("sql") or widget.get("query"))

    if result.get("status") != "ok":
        raise DashboardValidationError(f"{title}: {result.get('message') or 'query failed validation'}")

    columns = result.get("columns") or []
    rows = result.get("rows") or []
    if not columns:
        raise DashboardValidationError(f"{title}: query must return at least one column.")

    display_config = widget.get("display_config") or widget.get("displayConfig") or {}
    if not isinstance(display_config, dict):
        raise DashboardValidationError(f"{title}: display_config must be an object.")
    display_config = {
        str(key): value
        for key, value in display_config.items()
        if isinstance(key, str) and isinstance(value, (str, int, float, bool)) and len(key) <= 64
    }

    if widget_type == PersistentAgentDashboardWidget.WidgetType.METRIC:
        value_column = display_config.get("value_column") or display_config.get("valueColumn") or columns[0]
        if value_column not in columns:
            raise DashboardValidationError(f"{title}: metric value column does not exist.")
        display_config["value_column"] = value_column

    if widget_type == PersistentAgentDashboardWidget.WidgetType.BAR:
        if len(columns) < 2:
            raise DashboardValidationError(f"{title}: bar widgets need at least two columns.")
        x_column = display_config.get("x") or display_config.get("x_column") or columns[0]
        y_column = display_config.get("y") or display_config.get("y_column") or columns[1]
        if x_column not in columns or y_column not in columns:
            raise DashboardValidationError(f"{title}: chart x/y columns must exist in the query result.")
        if rows and not any(_is_numericish(row.get(y_column)) for row in rows):
            raise DashboardValidationError(f"{title}: chart y column must contain numeric values.")
        display_config["x"] = x_column
        display_config["y"] = y_column

    return {
        "title": title,
        "widget_type": widget_type,
        "sql": sql,
        "display_config": display_config,
        "position": position,
    }


def validate_dashboard_widgets(
    agent: PersistentAgent,
    widgets: Any,
    *,
    db_path: Optional[str] = None,
) -> list[dict[str, Any]]:
    if not isinstance(widgets, list):
        raise DashboardValidationError("widgets must be a list.")
    if not widgets:
        raise DashboardValidationError("At least one widget is required.")
    if len(widgets) > MAX_DASHBOARD_WIDGETS:
        raise DashboardValidationError(f"Dashboards can have at most {MAX_DASHBOARD_WIDGETS} widgets.")

    def validate_against_path(path: Optional[str]) -> list[dict[str, Any]]:
        if not path:
            raise DashboardValidationError("The agent SQLite database does not exist yet.")
        normalized: list[dict[str, Any]] = []
        for index, widget in enumerate(widgets):
            if not isinstance(widget, dict):
                raise DashboardValidationError("Each widget must be an object.")
            sql = _normalize_sql(widget.get("sql") or widget.get("query"))
            result = execute_dashboard_select(path, sql)
            normalized.append(_validated_widget_from_result({**widget, "sql": sql}, result, index))
        return normalized

    if db_path:
        return validate_against_path(db_path)

    with agent_sqlite_db_snapshot(str(agent.id)) as snapshot_path:
        return validate_against_path(snapshot_path)


def create_or_update_dashboard(
    agent: PersistentAgent,
    *,
    title: Any,
    description: Any = "",
    widgets: Any,
    db_path: Optional[str] = None,
    created_by_agent: bool = True,
) -> PersistentAgentDashboard:
    normalized_title = _clean_text(title, max_length=160, field_name="Dashboard title")
    normalized_description = _clean_optional_text(
        description,
        max_length=1_000,
        field_name="Dashboard description",
    )
    normalized_widgets = validate_dashboard_widgets(agent, widgets, db_path=db_path)

    with transaction.atomic():
        dashboard, _created = PersistentAgentDashboard.objects.update_or_create(
            agent=agent,
            defaults={
                "title": normalized_title,
                "description": normalized_description,
                "created_by_agent": created_by_agent,
            },
        )
        dashboard.widgets.all().delete()
        for widget in normalized_widgets:
            PersistentAgentDashboardWidget.objects.create(
                dashboard=dashboard,
                title=widget["title"],
                widget_type=widget["widget_type"],
                sql=widget["sql"],
                display_config=widget["display_config"],
                position=widget["position"],
            )
    return dashboard


def _serialize_widget_result(widget: PersistentAgentDashboardWidget, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "id": str(widget.id),
        "title": widget.title,
        "type": widget.widget_type,
        "position": widget.position,
        "displayConfig": widget.display_config or {},
        "result": result,
    }
    if widget.widget_type == PersistentAgentDashboardWidget.WidgetType.METRIC and result.get("status") == "ok":
        value_column = (widget.display_config or {}).get("value_column")
        rows = result.get("rows") or []
        value = rows[0].get(value_column) if rows and value_column else None
        payload["result"] = {
            **result,
            "value": value,
        }
    return payload


def render_dashboard(dashboard: PersistentAgentDashboard) -> dict[str, Any]:
    widgets = list(dashboard.widgets.all().order_by("position", "created_at"))
    rendered_widgets: list[dict[str, Any]] = []

    with agent_sqlite_db_snapshot(str(dashboard.agent_id)) as db_path:
        for widget in widgets:
            if db_path:
                result = execute_dashboard_select(db_path, widget.sql)
            else:
                result = {
                    "status": "error",
                    "message": "The agent SQLite database does not exist yet.",
                    "columns": [],
                    "rows": [],
                    "row_count": 0,
                    "truncated": False,
                }
            rendered_widgets.append(_serialize_widget_result(widget, result))

    return {
        "id": str(dashboard.id),
        "title": dashboard.title,
        "description": dashboard.description,
        "createdByAgent": dashboard.created_by_agent,
        "createdAt": dashboard.created_at.isoformat() if dashboard.created_at else None,
        "updatedAt": dashboard.updated_at.isoformat() if dashboard.updated_at else None,
        "widgets": rendered_widgets,
    }
