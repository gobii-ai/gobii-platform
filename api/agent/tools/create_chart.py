"""
Chart generation tool for persistent agents.

Creates beautiful, publication-quality charts directly from SQL queries.
Tightly integrated with SQLite - just pass a SELECT statement and column names
become chart data keys. Outputs SVG saved in filespace and returns variable
placeholders for embedding in chat, email, or PDFs.
"""
from datetime import datetime
import io
import logging
import uuid
from typing import Any, Dict, List, Optional, Union

from api.models import PersistentAgent
from .sqlite_query_runner import run_sqlite_select
from .agent_variables import set_agent_variable

logger = logging.getLogger(__name__)

# Beautiful, accessible color palette (colorblind-friendly)
DEFAULT_COLORS = [
    "#4C78A8",  # Steel blue
    "#F58518",  # Orange
    "#E45756",  # Red
    "#72B7B2",  # Teal
    "#54A24B",  # Green
    "#EECA3B",  # Yellow
    "#B279A2",  # Purple
    "#FF9DA6",  # Pink
    "#9D755D",  # Brown
    "#BAB0AC",  # Gray
]

CHART_TYPES = {
    "bar", "horizontal_bar", "stacked_bar",
    "line", "area", "stacked_area",
    "pie", "donut",
    "scatter",
}


def _execute_query_for_data(query: str) -> tuple[List[Dict], Optional[str]]:
    """Execute a SQL query and return results as a list of dicts."""

    data, _cols, error = run_sqlite_select(query)
    return data, error


def _extract_values(data: List[Dict], key: str) -> List[Any]:
    """Extract values for a given key from list of dicts."""
    return [row.get(key) for row in data]


def _setup_style():
    """Configure matplotlib with beautiful defaults."""
    import matplotlib.pyplot as plt

    # Use a clean style
    plt.style.use('seaborn-v0_8-whitegrid')

    # Override with our preferences
    plt.rcParams.update({
        'figure.facecolor': 'white',
        'axes.facecolor': 'white',
        'axes.edgecolor': '#333333',
        'axes.labelcolor': '#333333',
        'axes.titlecolor': '#333333',
        'xtick.color': '#333333',
        'ytick.color': '#333333',
        'text.color': '#333333',
        'font.family': 'sans-serif',
        'font.size': 11,
        'axes.titlesize': 14,
        'axes.labelsize': 12,
        'xtick.labelsize': 10,
        'ytick.labelsize': 10,
        'legend.fontsize': 10,
        'figure.titlesize': 16,
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.grid': True,
        'grid.alpha': 0.3,
        'grid.linestyle': '-',
        'grid.linewidth': 0.5,
    })


def _create_bar_chart(ax, x_vals, y_vals, colors, horizontal=False, **kwargs):
    """Create a bar chart."""
    if horizontal:
        bars = ax.barh(x_vals, y_vals, color=colors[0], edgecolor='white', linewidth=0.5)
        ax.invert_yaxis()  # Labels read top-to-bottom
    else:
        bars = ax.bar(x_vals, y_vals, color=colors[0], edgecolor='white', linewidth=0.5)
    return bars


def _create_stacked_bar_chart(ax, x_vals, y_data: Dict[str, List], colors, **kwargs):
    """Create a stacked bar chart with multiple series."""
    import numpy as np

    x = np.arange(len(x_vals))
    bottom = np.zeros(len(x_vals))

    bars_list = []
    for i, (label, values) in enumerate(y_data.items()):
        color = colors[i % len(colors)]
        bars = ax.bar(x, values, bottom=bottom, label=label, color=color,
                      edgecolor='white', linewidth=0.5)
        bottom += np.array(values)
        bars_list.append(bars)

    ax.set_xticks(x)
    ax.set_xticklabels(x_vals)
    ax.legend()
    return bars_list


def _create_line_chart(ax, x_vals, y_vals, colors, multi_series=None, **kwargs):
    """Create a line chart."""
    if multi_series:
        lines = []
        for i, (label, values) in enumerate(multi_series.items()):
            color = colors[i % len(colors)]
            line, = ax.plot(x_vals, values, marker='o', markersize=6,
                           color=color, linewidth=2, label=label)
            lines.append(line)
        ax.legend()
        return lines
    else:
        line, = ax.plot(x_vals, y_vals, marker='o', markersize=6,
                       color=colors[0], linewidth=2)
        return line


def _create_area_chart(ax, x_vals, y_vals, colors, stacked=False, multi_series=None, **kwargs):
    """Create an area chart."""
    if stacked and multi_series:
        import numpy as np
        labels = list(multi_series.keys())
        values = np.array([multi_series[k] for k in labels])
        ax.stackplot(range(len(x_vals)), values, labels=labels,
                    colors=colors[:len(labels)], alpha=0.8)
        ax.set_xticks(range(len(x_vals)))
        ax.set_xticklabels(x_vals)
        ax.legend(loc='upper left')
    elif multi_series:
        for i, (label, values) in enumerate(multi_series.items()):
            color = colors[i % len(colors)]
            ax.fill_between(range(len(x_vals)), values, alpha=0.5, color=color, label=label)
            ax.plot(range(len(x_vals)), values, color=color, linewidth=2)
        ax.set_xticks(range(len(x_vals)))
        ax.set_xticklabels(x_vals)
        ax.legend()
    else:
        ax.fill_between(range(len(x_vals)), y_vals, alpha=0.5, color=colors[0])
        ax.plot(range(len(x_vals)), y_vals, color=colors[0], linewidth=2)
        ax.set_xticks(range(len(x_vals)))
        ax.set_xticklabels(x_vals)


def _create_pie_chart(ax, values, labels, colors, donut=False, **kwargs):
    """Create a pie or donut chart."""
    import matplotlib.patches as mpatches

    # Filter out zero/None values
    filtered = [(v, l) for v, l in zip(values, labels) if v and v > 0]
    if not filtered:
        ax.text(0.5, 0.5, 'No data', ha='center', va='center', fontsize=14)
        return None

    values, labels = zip(*filtered)

    wedge_colors = [colors[i % len(colors)] for i in range(len(values))]

    wedges, texts, autotexts = ax.pie(
        values,
        labels=labels,
        colors=wedge_colors,
        autopct='%1.1f%%',
        pctdistance=0.75 if donut else 0.6,
        startangle=90,
        wedgeprops={'edgecolor': 'white', 'linewidth': 1.5}
    )

    # Style the percentage text
    for autotext in autotexts:
        autotext.set_fontsize(9)
        autotext.set_fontweight('bold')

    if donut:
        # Create donut hole
        centre_circle = mpatches.Circle((0, 0), 0.50, fc='white')
        ax.add_patch(centre_circle)

    ax.axis('equal')
    return wedges


def _create_scatter_chart(ax, x_vals, y_vals, colors, size=None, **kwargs):
    """Create a scatter plot."""
    scatter = ax.scatter(x_vals, y_vals, c=colors[0], s=size or 60,
                        alpha=0.7, edgecolors='white', linewidth=0.5)
    return scatter


def _generate_chart(
    chart_type: str,
    data: List[Dict],
    x: Optional[str] = None,
    y: Optional[Union[str, List[str]]] = None,
    values: Optional[str] = None,
    labels: Optional[str] = None,
    title: Optional[str] = None,
    xlabel: Optional[str] = None,
    ylabel: Optional[str] = None,
    colors: Optional[List[str]] = None,
    legend: bool = True,
    figsize: tuple = (10, 6),
) -> bytes:
    """Generate a chart and return SVG bytes."""
    import matplotlib
    matplotlib.use('Agg')  # Non-interactive backend
    import matplotlib.pyplot as plt

    _setup_style()

    # Use provided colors or defaults
    chart_colors = colors or DEFAULT_COLORS

    # Create figure
    fig, ax = plt.subplots(figsize=figsize)

    # Extract data based on chart type
    if chart_type in ("pie", "donut"):
        val_data = _extract_values(data, values) if values else []
        label_data = _extract_values(data, labels) if labels else []
        _create_pie_chart(ax, val_data, label_data, chart_colors, donut=(chart_type == "donut"))
    else:
        x_vals = _extract_values(data, x) if x else list(range(len(data)))

        # Handle single or multiple y series
        if isinstance(y, list):
            # Multiple series
            y_data = {key: _extract_values(data, key) for key in y}

            if chart_type == "stacked_bar":
                _create_stacked_bar_chart(ax, x_vals, y_data, chart_colors)
            elif chart_type == "stacked_area":
                _create_area_chart(ax, x_vals, None, chart_colors, stacked=True, multi_series=y_data)
            elif chart_type == "area":
                _create_area_chart(ax, x_vals, None, chart_colors, multi_series=y_data)
            elif chart_type == "line":
                _create_line_chart(ax, x_vals, None, chart_colors, multi_series=y_data)
            else:
                # Default to grouped display
                _create_line_chart(ax, x_vals, None, chart_colors, multi_series=y_data)
        else:
            # Single series
            y_vals = _extract_values(data, y) if y else []

            if chart_type == "bar":
                _create_bar_chart(ax, x_vals, y_vals, chart_colors)
            elif chart_type == "horizontal_bar":
                _create_bar_chart(ax, x_vals, y_vals, chart_colors, horizontal=True)
            elif chart_type == "line":
                _create_line_chart(ax, x_vals, y_vals, chart_colors)
            elif chart_type == "area":
                _create_area_chart(ax, x_vals, y_vals, chart_colors)
            elif chart_type == "scatter":
                _create_scatter_chart(ax, x_vals, y_vals, chart_colors)

    # Set labels and title
    if title:
        ax.set_title(title, pad=15, fontweight='bold')

    if chart_type not in ("pie", "donut"):
        if xlabel:
            ax.set_xlabel(xlabel)
        elif x:
            ax.set_xlabel(x.replace("_", " ").title())

        if ylabel:
            ax.set_ylabel(ylabel)
        elif y and isinstance(y, str):
            ax.set_ylabel(y.replace("_", " ").title())

    # Rotate x-axis labels if they're long
    if chart_type not in ("pie", "donut", "horizontal_bar"):
        x_vals = _extract_values(data, x) if x else []
        if x_vals and any(isinstance(v, str) and len(str(v)) > 8 for v in x_vals):
            plt.xticks(rotation=45, ha='right')

    # Tight layout to prevent clipping
    plt.tight_layout()

    # Export to SVG
    buf = io.BytesIO()
    fig.savefig(buf, format='svg', bbox_inches='tight', facecolor='white', edgecolor='none')
    buf.seek(0)
    svg_bytes = buf.read()

    plt.close(fig)

    return svg_bytes


def get_create_chart_tool() -> Dict[str, Any]:
    """Return the create_chart tool definition."""
    return {
        "type": "function",
        "function": {
            "name": "create_chart",
            "description": (
                "Create a beautiful chart from a SQL query. The query runs against your SQLite database "
                "and results become chart data. Column names in your SELECT become the keys for x/y/values/labels. "
                "Types: bar, horizontal_bar, stacked_bar, line, area, stacked_area, pie, donut, scatter. "
                "For pie/donut: use 'values' and 'labels'. For others: use 'x' and 'y' (y can be a list for multi-series). "
                "Returns `file`, `inline`, `inline_html`, and `attach` with variable placeholders—use them directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": list(CHART_TYPES),
                        "description": "Chart type."
                    },
                    "query": {
                        "type": "string",
                        "description": "SQL SELECT query. Column names become data keys for x/y/values/labels."
                    },
                    "x": {
                        "type": "string",
                        "description": "Column name for x-axis (bar, line, area, scatter)."
                    },
                    "y": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}}
                        ],
                        "description": "Column name(s) for y-axis. Array for multi-series."
                    },
                    "values": {
                        "type": "string",
                        "description": "Column name for numeric values (pie/donut)."
                    },
                    "labels": {
                        "type": "string",
                        "description": "Column name for labels (pie/donut)."
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title (recommended—charts without titles look unfinished)."
                    },
                    "xlabel": {
                        "type": "string",
                        "description": "X-axis label."
                    },
                    "ylabel": {
                        "type": "string",
                        "description": "Y-axis label."
                    },
                    "colors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Custom hex colors. Beautiful defaults if omitted."
                    },
                },
                "required": ["type", "query"],
            },
        },
    }


def _build_chart_save_path(chart_type: str) -> str:
    """Generate a unique chart path so rapid tool calls never overwrite each other."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_suffix = uuid.uuid4().hex[:8]
    return f"/charts/{chart_type}_{timestamp}_{unique_suffix}.svg"


def execute_create_chart(agent: PersistentAgent, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the create_chart tool."""
    chart_type = params.get("type")
    query = params.get("query")

    # Validate required params
    if not chart_type:
        return {"status": "error", "message": "Missing required parameter: type"}
    if chart_type not in CHART_TYPES:
        return {"status": "error", "message": f"Invalid chart type: {chart_type}. Must be one of: {', '.join(sorted(CHART_TYPES))}"}
    if not query:
        return {"status": "error", "message": "Missing required parameter: query"}

    # Execute query to get data
    data, error = _execute_query_for_data(query)
    if error:
        return {"status": "error", "message": error}
    if not data:
        return {"status": "error", "message": "Query returned no rows - nothing to chart"}

    # Validate chart-specific params
    if chart_type in ("pie", "donut"):
        if not params.get("values"):
            return {"status": "error", "message": f"Chart type '{chart_type}' requires 'values' parameter"}
        if not params.get("labels"):
            return {"status": "error", "message": f"Chart type '{chart_type}' requires 'labels' parameter"}
    else:
        if not params.get("x"):
            return {"status": "error", "message": f"Chart type '{chart_type}' requires 'x' parameter"}
        if not params.get("y"):
            return {"status": "error", "message": f"Chart type '{chart_type}' requires 'y' parameter"}

    try:
        svg_bytes = _generate_chart(
            chart_type=chart_type,
            data=data,
            x=params.get("x"),
            y=params.get("y"),
            values=params.get("values"),
            labels=params.get("labels"),
            title=params.get("title"),
            xlabel=params.get("xlabel"),
            ylabel=params.get("ylabel"),
            colors=params.get("colors"),
        )
    except Exception as e:
        logger.exception("Failed to generate chart for agent %s: %s", agent.id, e)
        return {"status": "error", "message": f"Failed to generate chart: {str(e)}"}

    # Save to filespace - this is the source of truth
    try:
        from api.agent.files.filespace_service import write_bytes_to_dir
        from api.agent.files.attachment_helpers import build_signed_filespace_download_url

        save_path = _build_chart_save_path(chart_type)

        save_result = write_bytes_to_dir(
            agent=agent,
            content_bytes=svg_bytes,
            extension=".svg",
            mime_type="image/svg+xml",
            path=save_path,
            overwrite=True,
        )

        if save_result.get("status") != "ok":
            return {"status": "error", "message": f"Failed to save chart: {save_result.get('message', 'unknown error')}"}

        node_id = save_result.get("node_id")
        signed_url = build_signed_filespace_download_url(
            agent_id=str(agent.id),
            node_id=node_id,
        )

        # Set variable using path as name (unique, human-readable)
        path = save_result.get("path")
        set_agent_variable(path, signed_url)

        # Return with ready-to-use references
        var_ref = f"$[{path}]"
        return {
            "status": "ok",
            "file": var_ref,
            "inline": f"![]({var_ref})",
            "inline_html": f"<img src='{var_ref}'>",
            "attach": var_ref,
        }

    except Exception as e:
        logger.exception("Failed to save chart for agent %s: %s", agent.id, e)
        return {"status": "error", "message": f"Failed to save chart: {str(e)}"}
