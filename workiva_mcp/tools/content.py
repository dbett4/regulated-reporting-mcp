"""
Content tools — table properties, style guides.
"""

from workiva_mcp.http_client import _error, api_request, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp


def _operation_location(headers: dict, body: object) -> str:
    try:
        return operation_location_from_parts(headers, body)
    except WorkivaOperationError:
        return ""


# ── Table properties ──────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_get_table_properties(table_id: str) -> str:
    """Get table properties.

    Args:
        table_id: The table UUID
    """
    s, _, body = api_request(
        f"/content/tables/{table_id}",
        tool_name="workiva_get_table_properties",
    )
    if s != 200:
        return _error(s, body, hint="verify table_id via workiva_list_sheets" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_update_table_properties(
    table_id: str,
    patches: list[dict] | None = None,
) -> str:
    """Update table properties via JSON Patch.

    Args:
        table_id: The table UUID
        patches: JSON Patch array
    """
    if not patches:
        return _error(400, {}, cause="patches are required for update")
    s, _, body = api_request(
        f"/content/tables/{table_id}",
        method="PATCH",
        data=patches,
        content_type="application/json-patch+json",
        tool_name="workiva_update_table_properties",
    )
    if s == 200:
        return "Table properties updated."
    return _error(s, body, hint="verify table_id via workiva_list_sheets" if s == 404 else "")


@mcp.tool()
async def workiva_get_table_columns(table_id: str) -> str:
    """Get column properties for a table.

    Args:
        table_id: The table UUID
    """
    s, _, body = api_request(
        f"/content/tables/{table_id}/columns",
        tool_name="workiva_get_table_columns",
    )
    if s != 200:
        return _error(s, body, hint="verify table_id via workiva_list_sheets" if s == 404 else "")
    cols = body.get("data", []) if isinstance(body, dict) else []
    if not cols:
        return "No column data."
    lines = [f"Columns ({len(cols)}):"]
    for c in cols:
        lines.append(f"  Col {c.get('index', '?')}: width={c.get('width', '?')}, hidden={c.get('hidden', False)}")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_table_rows(table_id: str) -> str:
    """Get row properties for a table.

    Args:
        table_id: The table UUID
    """
    s, _, body = api_request(
        f"/content/tables/{table_id}/rows",
        tool_name="workiva_get_table_rows",
    )
    if s != 200:
        return _error(s, body, hint="verify table_id via workiva_list_sheets" if s == 404 else "")
    rows = body.get("data", []) if isinstance(body, dict) else []
    if not rows:
        return "No row data."
    lines = [f"Rows ({len(rows)}):"]
    for r in rows[:50]:
        lines.append(f"  Row {r.get('index', '?')}: height={r.get('height', '?')}, hidden={r.get('hidden', False)}")
    if len(rows) > 50:
        lines.append(f"  ... {len(rows) - 50} more rows")
    return "\n".join(lines)


@mcp.tool()
async def workiva_table_properties(
    table_id: str,
    operation: str = "get",
    patches: list[dict] | None = None,
) -> str:
    """# DEPRECATED — use workiva_get_table_properties / workiva_update_table_properties / workiva_get_table_columns / workiva_get_table_rows.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "get":
        return await workiva_get_table_properties(table_id)
    if operation == "update":
        return await workiva_update_table_properties(table_id, patches)
    if operation == "get_columns":
        return await workiva_get_table_columns(table_id)
    if operation == "get_rows":
        return await workiva_get_table_rows(table_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'get', 'update', 'get_columns', or 'get_rows'")


# ── Style guides ──────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_get_style_guide(style_guide_id: str) -> str:
    """Get style guide details.

    Args:
        style_guide_id: The style guide UUID
    """
    s, _, body = api_request(
        f"/styleGuides/{style_guide_id}",
        tool_name="workiva_get_style_guide",
    )
    if s != 200:
        return _error(s, body, hint="verify style_guide_id" if s == 404 else "")
    return f"Style Guide: {body.get('name', 'N/A')}\nID: {body.get('id', 'N/A')}"


@mcp.tool()
async def workiva_export_style_guide(style_guide_id: str) -> str:
    """Export a style guide.

    Args:
        style_guide_id: The style guide UUID
    """
    s, headers, body = api_request(
        f"/styleGuides/{style_guide_id}/export",
        method="POST",
        tool_name="workiva_export_style_guide",
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Export style guide", tool_name="workiva_export_style_guide")
        if isinstance(result, dict):
            return f"Export ready: {result.get('downloadUrl', result.get('url', ''))}"
    if s in (200, 201) and isinstance(body, dict):
        return f"Export ready: {body.get('downloadUrl', body.get('url', ''))}"
    return _error(s, body, hint="verify style_guide_id" if s == 404 else "")


@mcp.tool()
async def workiva_import_style_guide(style_guide_id: str) -> str:
    """Import a style guide.

    Args:
        style_guide_id: The style guide UUID
    """
    s, headers, body = api_request(
        f"/styleGuides/{style_guide_id}/import",
        method="POST",
        tool_name="workiva_import_style_guide",
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        poll_operation(loc, "Import style guide", tool_name="workiva_import_style_guide")
        return "Style guide imported."
    if s in (200, 204):
        return "Style guide imported."
    return _error(s, body, hint="verify style_guide_id" if s == 404 else "")


@mcp.tool()
async def workiva_style_guide(
    style_guide_id: str,
    operation: str = "get",
) -> str:
    """# DEPRECATED — use workiva_get_style_guide / workiva_export_style_guide / workiva_import_style_guide.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "get":
        return await workiva_get_style_guide(style_guide_id)
    if operation == "export":
        return await workiva_export_style_guide(style_guide_id)
    if operation == "import":
        return await workiva_import_style_guide(style_guide_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'get', 'export', or 'import'")
