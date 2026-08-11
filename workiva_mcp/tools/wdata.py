"""
Wdata tools — tables, queries, query runs, connections, files.
"""

from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate
from workiva_mcp.server import mcp

# ── Tables ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_wdata_list_tables(max_results: int = 50) -> str:
    """List Wdata tables.

    Args:
        max_results: Max results to return
    """
    try:
        items = paginate(
            f"/tables?limit={min(max_results, 100)}",
            api="wdata",
            tool_name="workiva_wdata_list_tables",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]
    if not items:
        return "No Wdata tables found."

    lines = [f"Wdata tables ({len(items)}):"]
    for t in items:
        lines.append(f"  {t.get('name', '?')}  (id: {t.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_wdata_get_table(table_id: str) -> str:
    """Get a single Wdata table.

    Args:
        table_id: The Wdata table ID
    """
    if not table_id:
        return _error(400, {}, cause="table_id is required")
    s, _, body = api_request(
        f"/tables/{table_id}", api="wdata", tool_name="workiva_wdata_get_table"
    )
    if s != 200:
        return _error(s, body, hint="verify table_id via workiva_wdata_list_tables" if s == 404 else "")
    return (
        f"Table: {body.get('name', 'N/A')}\n"
        f"ID: {body.get('id', 'N/A')}\n"
        f"Type: {body.get('type', 'N/A')}\n"
        f"Row count: {body.get('rowCount', 'N/A')}"
    )


@mcp.tool()
async def workiva_wdata_create_table(
    name: str = "",
    table_type: str = "",
    schema: dict | None = None,
) -> str:
    """Create a Wdata table.

    Args:
        name: Table name
        table_type: Table type
        schema: Table schema definition
    """
    payload = {}
    if name:
        payload["name"] = name
    if table_type:
        payload["type"] = table_type
    if schema:
        payload["schema"] = schema

    s, _, body = api_request(
        "/tables", method="POST", data=payload,
        api="wdata", tool_name="workiva_wdata_create_table",
    )
    if s in (200, 201):
        return f"Table created: {body.get('name', name)} (id: {body.get('id', '?')})"
    return _error(s, body, cause=f"create table failed: {str(body)[:200]}")


@mcp.tool()
async def workiva_wdata_delete_table(table_id: str) -> str:
    """Delete a Wdata table.

    Args:
        table_id: The Wdata table ID
    """
    if not table_id:
        return _error(400, {}, cause="table_id is required")
    s, _, body = api_request(
        f"/tables/{table_id}", method="DELETE",
        api="wdata", tool_name="workiva_wdata_delete_table",
    )
    if s in (200, 204):
        return f"Table {table_id} deleted."
    return _error(s, body, hint="verify table_id via workiva_wdata_list_tables" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_update_table(
    table_id: str,
    patches: list[dict] | None = None,
) -> str:
    """Update a Wdata table via JSON Patch.

    Args:
        table_id: The Wdata table ID
        patches: JSON Patch array
    """
    if not table_id or not patches:
        return _error(400, {}, cause="table_id and patches are required")
    s, _, body = api_request(
        f"/tables/{table_id}", method="PATCH", data=patches,
        api="wdata", tool_name="workiva_wdata_update_table",
    )
    if s == 200:
        return f"Table updated: {body.get('name', '?')}"
    return _error(s, body, hint="verify table_id via workiva_wdata_list_tables" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_tables(
    operation: str = "list",
    table_id: str = "",
    name: str = "",
    table_type: str = "",
    schema: dict | None = None,
    patches: list[dict] | None = None,
    max_results: int = 50,
) -> str:
    """# DEPRECATED — use workiva_wdata_list_tables / workiva_wdata_get_table / workiva_wdata_create_table / workiva_wdata_delete_table / workiva_wdata_update_table.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_wdata_list_tables(max_results)
    if operation == "get":
        return await workiva_wdata_get_table(table_id)
    if operation == "create":
        return await workiva_wdata_create_table(name, table_type, schema)
    if operation == "delete":
        return await workiva_wdata_delete_table(table_id)
    if operation == "update":
        return await workiva_wdata_update_table(table_id, patches)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'create', 'get', 'delete', or 'update'")


# ── Queries ───────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_wdata_list_queries(max_results: int = 50) -> str:
    """List Wdata queries.

    Args:
        max_results: Max results to return
    """
    try:
        items = paginate(
            f"/queries?limit={min(max_results, 100)}",
            api="wdata", tool_name="workiva_wdata_list_queries",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]
    if not items:
        return "No queries found."

    lines = [f"Queries ({len(items)}):"]
    for q in items:
        lines.append(f"  {q.get('name', '?')}  (id: {q.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_wdata_get_query(query_id: str) -> str:
    """Get a single Wdata query.

    Args:
        query_id: The query ID
    """
    if not query_id:
        return _error(400, {}, cause="query_id is required")
    s, _, body = api_request(
        f"/queries/{query_id}", api="wdata", tool_name="workiva_wdata_get_query"
    )
    if s != 200:
        return _error(s, body, hint="verify query_id via workiva_wdata_list_queries" if s == 404 else "")
    return f"Query: {body.get('name', 'N/A')}\nID: {body.get('id', 'N/A')}\nSQL: {body.get('sql', 'N/A')}"


@mcp.tool()
async def workiva_wdata_create_query(
    name: str = "",
    sql: str = "",
) -> str:
    """Create a Wdata query.

    Args:
        name: Query name
        sql: SQL statement
    """
    payload = {}
    if name:
        payload["name"] = name
    if sql:
        payload["sql"] = sql

    s, _, body = api_request(
        "/queries", method="POST", data=payload,
        api="wdata", tool_name="workiva_wdata_create_query",
    )
    if s in (200, 201):
        return f"Query created: {body.get('name', name)} (id: {body.get('id', '?')})"
    return _error(s, body, cause=f"create query failed: {str(body)[:200]}")


@mcp.tool()
async def workiva_wdata_delete_query(query_id: str) -> str:
    """Delete a Wdata query.

    Args:
        query_id: The query ID
    """
    if not query_id:
        return _error(400, {}, cause="query_id is required")
    s, _, body = api_request(
        f"/queries/{query_id}", method="DELETE",
        api="wdata", tool_name="workiva_wdata_delete_query",
    )
    if s in (200, 204):
        return f"Query {query_id} deleted."
    return _error(s, body, hint="verify query_id via workiva_wdata_list_queries" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_validate_query(
    query_id: str = "",
    sql: str = "",
) -> str:
    """Validate a Wdata query by ID or raw SQL.

    Args:
        query_id: The query ID (optional — validates an existing query)
        sql: Raw SQL to validate (if query_id not provided)
    """
    payload = {"sql": sql} if sql else {}
    target = f"/queries/{query_id}/validate" if query_id else "/queries/validate"
    s, _, body = api_request(
        target, method="POST", data=payload,
        api="wdata", tool_name="workiva_wdata_validate_query",
    )
    if s == 200:
        return "Query is valid."
    return _error(s, body, cause=f"validation error: {str(body)[:200]}")


@mcp.tool()
async def workiva_wdata_describe_query(query_id: str) -> str:
    """Describe a Wdata query's output schema.

    Args:
        query_id: The query ID
    """
    if not query_id:
        return _error(400, {}, cause="query_id is required")
    s, _, body = api_request(
        f"/queries/{query_id}/describe",
        api="wdata", tool_name="workiva_wdata_describe_query",
    )
    if s != 200:
        return _error(s, body, hint="verify query_id via workiva_wdata_list_queries" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_wdata_queries(
    operation: str = "list",
    query_id: str = "",
    name: str = "",
    sql: str = "",
    patches: list[dict] | None = None,
    max_results: int = 50,
) -> str:
    """# DEPRECATED — use workiva_wdata_list_queries / workiva_wdata_get_query / workiva_wdata_create_query / workiva_wdata_delete_query / workiva_wdata_validate_query / workiva_wdata_describe_query.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_wdata_list_queries(max_results)
    if operation == "get":
        return await workiva_wdata_get_query(query_id)
    if operation == "create":
        return await workiva_wdata_create_query(name, sql)
    if operation == "delete":
        return await workiva_wdata_delete_query(query_id)
    if operation == "validate":
        return await workiva_wdata_validate_query(query_id, sql)
    if operation == "describe":
        return await workiva_wdata_describe_query(query_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'create', 'get', 'delete', 'update', 'validate', or 'describe'")


# ── Query runs ────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_wdata_run_query(query_id: str) -> str:
    """Start a Wdata query run.

    Args:
        query_id: The query UUID
    """
    s, _, body = api_request(
        f"/queries/{query_id}/runs",
        method="POST",
        api="wdata",
        tool_name="workiva_wdata_run_query",
    )
    if s in (200, 201, 202):
        rid = body.get("id", "?") if isinstance(body, dict) else "?"
        status = body.get("status", "?") if isinstance(body, dict) else "?"
        return f"Query run started. Run ID: {rid}, Status: {status}"
    return _error(s, body, hint="verify query_id via workiva_wdata_list_queries" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_get_query_result(query_id: str, run_id: str) -> str:
    """Get the result of a Wdata query run.

    Args:
        query_id: The query UUID
        run_id: The run ID
    """
    if not run_id:
        return _error(400, {}, cause="run_id is required")
    s, _, body = api_request(
        f"/queries/{query_id}/runs/{run_id}",
        api="wdata",
        tool_name="workiva_wdata_get_query_result",
    )
    if s != 200:
        return _error(s, body, hint="verify query_id and run_id" if s == 404 else "")
    status = body.get("status", "?") if isinstance(body, dict) else "?"
    if status == "completed":
        rows = body.get("rows", []) if isinstance(body, dict) else []
        cols = body.get("columns", []) if isinstance(body, dict) else []
        lines = [f"Status: {status}, Rows: {len(rows)}"]
        if cols:
            lines.append("Columns: " + ", ".join(str(c.get("name", c)) for c in cols))
        for row in rows[:50]:
            lines.append("\t".join(str(v) for v in row))
        if len(rows) > 50:
            lines.append(f"... {len(rows) - 50} more rows")
        return "\n".join(lines)
    return f"Status: {status}"


@mcp.tool()
async def workiva_wdata_cancel_query_run(query_id: str, run_id: str) -> str:
    """Cancel a Wdata query run.

    Args:
        query_id: The query UUID
        run_id: The run ID
    """
    if not run_id:
        return _error(400, {}, cause="run_id is required")
    s, _, body = api_request(
        f"/queries/{query_id}/runs/{run_id}/cancel",
        method="POST",
        api="wdata",
        tool_name="workiva_wdata_cancel_query_run",
    )
    if s in (200, 204):
        return "Query run cancelled."
    return _error(s, body, hint="verify query_id and run_id" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_query_run(
    query_id: str,
    operation: str = "run",
    run_id: str = "",
) -> str:
    """# DEPRECATED — use workiva_wdata_run_query / workiva_wdata_get_query_result / workiva_wdata_cancel_query_run.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "run":
        return await workiva_wdata_run_query(query_id)
    if operation == "get_result":
        return await workiva_wdata_get_query_result(query_id, run_id)
    if operation == "cancel":
        return await workiva_wdata_cancel_query_run(query_id, run_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'run', 'cancel', 'get_result', or 'download'")


# ── Connections ───────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_wdata_list_connections() -> str:
    """List Wdata connections."""
    try:
        items = paginate("/connections", api="wdata", tool_name="workiva_wdata_list_connections")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No connections found."
    lines = [f"Connections ({len(items)}):"]
    for c in items:
        lines.append(f"  {c.get('name', '?')}  (id: {c.get('id', '?')}, status: {c.get('status', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_wdata_get_connection(connection_id: str) -> str:
    """Get a Wdata connection.

    Args:
        connection_id: The connection ID
    """
    if not connection_id:
        return _error(400, {}, cause="connection_id is required")
    s, _, body = api_request(
        f"/connections/{connection_id}", api="wdata", tool_name="workiva_wdata_get_connection"
    )
    if s != 200:
        return _error(s, body, hint="verify connection_id via workiva_wdata_list_connections" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_wdata_refresh_connection(connection_id: str) -> str:
    """Refresh a single Wdata connection.

    Args:
        connection_id: The connection ID
    """
    if not connection_id:
        return _error(400, {}, cause="connection_id is required")
    s, _, body = api_request(
        f"/connections/{connection_id}/refresh",
        method="POST",
        api="wdata",
        tool_name="workiva_wdata_refresh_connection",
    )
    if s in (200, 202):
        return "Connection refresh started."
    return _error(s, body, hint="verify connection_id via workiva_wdata_list_connections" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_batch_refresh_connections(connection_ids: list[str] | None = None) -> str:
    """Batch refresh multiple Wdata connections by fanning out to the per-connection endpoint.

    The deprecated batch /connections/refresh endpoint (POST with {"connectionIds": [...]})
    is dead — it returns 400 for every request shape. This function fans out to the
    working per-connection endpoint POST /connections/{id}/refresh (same as
    workiva_wdata_refresh_connection) and aggregates results.

    Args:
        connection_ids: List of connection IDs to refresh
    """
    if not connection_ids:
        return _error(400, {}, cause="connection_ids is required")
    results = []
    succeeded = 0
    failed = 0
    for cid in connection_ids:
        s, _, body = api_request(
            f"/connections/{cid}/refresh",
            method="POST",
            api="wdata",
            tool_name="workiva_wdata_batch_refresh_connections",
        )
        if s in (200, 202):
            results.append({"id": cid, "success": True})
            succeeded += 1
        else:
            err_msg = str(body)[:200] if body else f"HTTP {s}"
            results.append({"id": cid, "success": False, "error": err_msg})
            failed += 1
    import json as _json
    summary = f"Batch refresh complete: {succeeded} succeeded, {failed} failed."
    return summary + "\n" + _json.dumps(results, indent=2)


@mcp.tool()
async def workiva_wdata_connections(
    operation: str = "list",
    connection_id: str = "",
    connection_ids: list[str] | None = None,
) -> str:
    """# DEPRECATED — use workiva_wdata_list_connections / workiva_wdata_get_connection / workiva_wdata_refresh_connection / workiva_wdata_batch_refresh_connections.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_wdata_list_connections()
    if operation == "get":
        return await workiva_wdata_get_connection(connection_id)
    if operation == "refresh":
        return await workiva_wdata_refresh_connection(connection_id)
    if operation == "batch_refresh":
        return await workiva_wdata_batch_refresh_connections(connection_ids)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'get', 'refresh', or 'batch_refresh'")


# ── Files ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_wdata_list_files() -> str:
    """List Wdata files."""
    try:
        items = paginate("/files", api="wdata", tool_name="workiva_wdata_list_files")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No Wdata files found."
    lines = [f"Wdata files ({len(items)}):"]
    for f in items:
        lines.append(f"  {f.get('name', '?')}  (id: {f.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_wdata_get_file(file_id: str) -> str:
    """Get a Wdata file.

    Args:
        file_id: The Wdata file ID
    """
    if not file_id:
        return _error(400, {}, cause="file_id is required")
    s, _, body = api_request(
        f"/files/{file_id}", api="wdata", tool_name="workiva_wdata_get_file"
    )
    if s != 200:
        return _error(s, body, hint="verify file_id via workiva_wdata_list_files" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_wdata_upload_file(file_path: str) -> str:
    """Upload a local file to Wdata.

    Args:
        file_path: Local file path (absolute)
    """
    if not file_path:
        return _error(400, {}, cause="file_path is required")
    from pathlib import Path
    local = Path(file_path)
    if not local.exists():
        return _error(400, {}, cause=f"file not found: {file_path}", hint="provide an absolute path to a local file")

    file_bytes = local.read_bytes()
    s, _, body = api_request(
        "/files",
        method="POST",
        data=file_bytes,
        content_type="application/octet-stream",
        api="wdata",
        tool_name="workiva_wdata_upload_file",
    )
    if s in (200, 201):
        return f"File uploaded: {body.get('id', '?') if isinstance(body, dict) else body}"
    return _error(s, body, cause=f"upload failed: {str(body)[:200]}")


@mcp.tool()
async def workiva_wdata_delete_file(file_id: str) -> str:
    """Delete a Wdata file.

    Args:
        file_id: The Wdata file ID
    """
    if not file_id:
        return _error(400, {}, cause="file_id is required")
    s, _, body = api_request(
        f"/files/{file_id}", method="DELETE",
        api="wdata", tool_name="workiva_wdata_delete_file",
    )
    if s in (200, 204):
        return f"Wdata file {file_id} deleted."
    return _error(s, body, hint="verify file_id via workiva_wdata_list_files" if s == 404 else "")


@mcp.tool()
async def workiva_wdata_files(
    operation: str = "list",
    file_id: str = "",
    file_path: str = "",
    spreadsheet_id: str = "",
    sheet_name: str = "",
) -> str:
    """# DEPRECATED — use workiva_wdata_list_files / workiva_wdata_get_file / workiva_wdata_upload_file / workiva_wdata_delete_file.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_wdata_list_files()
    if operation == "get":
        return await workiva_wdata_get_file(file_id)
    if operation == "upload":
        return await workiva_wdata_upload_file(file_path)
    if operation == "delete":
        return await workiva_wdata_delete_file(file_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'upload', 'get', 'delete', 'download', or 'export_to_spreadsheet'")
