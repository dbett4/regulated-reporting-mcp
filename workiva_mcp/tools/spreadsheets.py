"""
Spreadsheet management tools — list, get, sheets CRUD, export, publish links.
"""

import time
from pathlib import Path
from typing import Literal

from workiva_mcp.config import MAX_LIST_ITEMS
from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp

# Where exported file bytes get written. The Export endpoint's async operation
# resolves to a signed blob-storage URL whose body IS the file (xlsx/csv/pdf
# bytes), not a JSON envelope with a downloadUrl — poll_operation already
# fetches that resourceUrl for us, so by the time we see it, the "download"
# has already happened. There is no separate URL left to hand back.
# Exports land under the user data dir, never inside the package tree.
EXPORTS_DIR = Path.home() / ".workiva_mcp" / "exports"


def _operation_location(headers: dict, body: object) -> str:
    try:
        return operation_location_from_parts(headers, body)
    except WorkivaOperationError:
        return ""


@mcp.tool()
async def workiva_list_spreadsheets(
    filter: str = "",
    max_results: int = 50,
) -> str:
    """List spreadsheets in the workspace.

    Args:
        filter: OData filter (e.g., "name contains 'ACFR'")
        max_results: Maximum results to return
    """
    params = []
    if filter:
        params.append(f"$filter={filter}")
    params.append(f"$maxpagesize={min(max_results, MAX_LIST_ITEMS)}")

    path = "/spreadsheets"
    if params:
        path += "?" + "&".join(params)

    try:
        items = paginate(path, tool_name="workiva_list_spreadsheets")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]

    if not items:
        return "No spreadsheets found."

    lines = [f"Found {len(items)} spreadsheet(s):\n"]
    for ss in items:
        name = ss.get("name", "Untitled")
        sid = ss.get("id", "")
        modified = ss.get("modified", {}).get("dateTime", "")[:10]
        lines.append(f"  {name}  (id: {sid})  modified: {modified}")

    return "\n".join(lines)


@mcp.tool()
async def workiva_get_spreadsheet(
    spreadsheet_id: str,
    expand: str = "",
) -> str:
    """Get spreadsheet details.

    Args:
        spreadsheet_id: The spreadsheet UUID
        expand: Comma-separated subresources to include (e.g., "sheets,milestones")
    """
    path = f"/spreadsheets/{spreadsheet_id}"
    if expand:
        path += f"?$expand={expand}"

    s, _, body = api_request(path, tool_name="workiva_get_spreadsheet")
    if s != 200:
        return _error(s, body, hint="verify spreadsheet_id via workiva_list_spreadsheets" if s == 404 else "")

    lines = [
        f"Name: {body.get('name', 'N/A')}",
        f"ID: {body.get('id', 'N/A')}",
        f"Modified: {body.get('modified', {}).get('dateTime', 'N/A')}",
    ]

    # List sheets if expanded or available
    sheets = body.get("sheets", {}).get("data", [])
    if sheets:
        lines.append(f"\nSheets ({len(sheets)}):")
        for sh in sheets:
            table_info = sh.get("table", {})
            table_id = table_info.get("table", "N/A") if isinstance(table_info, dict) else "N/A"
            lines.append(f"  {sh.get('name', '?')}  (sheet: {sh.get('id', '?')}, table: {table_id})")

    return "\n".join(lines)


@mcp.tool()
async def workiva_list_sheets(spreadsheet_id: str) -> str:
    """List sheets within a spreadsheet.

    Args:
        spreadsheet_id: The spreadsheet UUID
    """
    base = f"/spreadsheets/{spreadsheet_id}/sheets"
    try:
        items = paginate(base, tool_name="workiva_list_sheets")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No sheets found."

    lines = [f"Sheets in spreadsheet ({len(items)}):"]
    for sh in items:
        table_info = sh.get("table", {})
        table_id = table_info.get("table", "N/A") if isinstance(table_info, dict) else "N/A"
        lines.append(f"  {sh.get('name', '?')}  (sheet: {sh.get('id', '?')}, table: {table_id})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_sheet(spreadsheet_id: str, sheet_id: str) -> str:
    """Get details for a single sheet.

    Args:
        spreadsheet_id: The spreadsheet UUID
        sheet_id: The sheet UUID
    """
    if not sheet_id:
        return _error(400, {}, cause="sheet_id is required")
    s, _, body = api_request(
        f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}",
        tool_name="workiva_get_sheet",
    )
    if s != 200:
        return _error(s, body, hint="verify sheet_id via workiva_list_sheets" if s == 404 else "")

    table_info = body.get("table", {})
    table_id = table_info.get("table", "N/A") if isinstance(table_info, dict) else "N/A"
    revision = table_info.get("revision", "N/A") if isinstance(table_info, dict) else "N/A"
    return (
        f"Sheet: {body.get('name', 'N/A')}\n"
        f"Sheet ID: {body.get('id', 'N/A')}\n"
        f"Table ID: {table_id}\n"
        f"Revision: {revision}"
    )


@mcp.tool()
async def workiva_create_sheet(
    spreadsheet_id: str,
    name: str = "",
    index: int = -1,
) -> str:
    """Create a new sheet in a spreadsheet.

    Args:
        spreadsheet_id: The spreadsheet UUID
        name: Sheet name (optional — Workiva assigns one if omitted)
        index: Position index (-1 = append)
    """
    payload = {}
    if name:
        payload["name"] = name
    if index >= 0:
        payload["index"] = index

    base = f"/spreadsheets/{spreadsheet_id}/sheets"
    s, headers, body = api_request(
        base, method="POST", data=payload, tool_name="workiva_create_sheet"
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Create sheet", tool_name="workiva_create_sheet")
        if isinstance(result, dict):
            return f"Created sheet: {result.get('name', name)} (id: {result.get('id', 'N/A')})"
    if s in (200, 201):
        return f"Created sheet: {body.get('name', name)} (id: {body.get('id', 'N/A')})"
    return _error(s, body, hint="verify spreadsheet_id via workiva_list_spreadsheets" if s == 404 else "")


@mcp.tool()
async def workiva_delete_sheet(spreadsheet_id: str, sheet_id: str) -> str:
    """Delete a sheet from a spreadsheet.

    Args:
        spreadsheet_id: The spreadsheet UUID
        sheet_id: The sheet UUID to delete
    """
    if not sheet_id:
        return _error(400, {}, cause="sheet_id is required")
    s, _, body = api_request(
        f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}",
        method="DELETE",
        tool_name="workiva_delete_sheet",
    )
    if s in (200, 204):
        return f"Sheet {sheet_id} deleted."
    return _error(s, body, hint="verify sheet_id via workiva_list_sheets" if s == 404 else "")


@mcp.tool()
async def workiva_copy_sheet(
    spreadsheet_id: str,
    sheet_id: str,
    target_spreadsheet_id: str = "",
) -> str:
    """Copy a sheet within the same spreadsheet or into a target spreadsheet.

    Args:
        spreadsheet_id: The source spreadsheet UUID
        sheet_id: The sheet UUID to copy
        target_spreadsheet_id: Destination spreadsheet UUID (empty = copy within source)
    """
    if not sheet_id:
        return _error(400, {}, cause="sheet_id is required")
    payload = {}
    if target_spreadsheet_id:
        payload["spreadsheet"] = target_spreadsheet_id

    s, headers, body = api_request(
        f"/spreadsheets/{spreadsheet_id}/sheets/{sheet_id}/copy",
        method="POST",
        data=payload,
        tool_name="workiva_copy_sheet",
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Copy sheet", tool_name="workiva_copy_sheet")
        if isinstance(result, dict):
            return f"Copied sheet: {result.get('name', '?')} (id: {result.get('id', 'N/A')})"
    if s in (200, 201):
        return f"Copied sheet: {body.get('name', '?')} (id: {body.get('id', 'N/A')})"
    return _error(s, body, hint="verify sheet_id via workiva_list_sheets" if s == 404 else "")


@mcp.tool()
async def workiva_spreadsheet_sheets(
    spreadsheet_id: str,
    operation: str = "list",
    sheet_id: str = "",
    name: str = "",
    index: int = -1,
    target_spreadsheet_id: str = "",
) -> str:
    """# DEPRECATED — use workiva_list_sheets / workiva_get_sheet / workiva_create_sheet / workiva_delete_sheet / workiva_copy_sheet.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_sheets(spreadsheet_id)
    if operation == "get":
        return await workiva_get_sheet(spreadsheet_id, sheet_id)
    if operation == "create":
        return await workiva_create_sheet(spreadsheet_id, name, index)
    if operation == "delete":
        return await workiva_delete_sheet(spreadsheet_id, sheet_id)
    if operation == "copy":
        return await workiva_copy_sheet(spreadsheet_id, sheet_id, target_spreadsheet_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'create', 'get', 'delete', or 'copy'")


@mcp.tool()
async def workiva_export_spreadsheet(
    spreadsheet_id: str,
    format: Literal["xlsx", "pdf", "csv"] = "xlsx",
) -> str:
    """Export a spreadsheet.

    Args:
        spreadsheet_id: The spreadsheet UUID
        format: Export format — xlsx, pdf, or csv
    """
    if format not in {"xlsx", "pdf", "csv"}:
        return _error(400, {}, cause=f"invalid format: {format}", hint="use one of xlsx, pdf, csv")
    payload = {"format": format}
    s, headers, body = api_request(
        f"/spreadsheets/{spreadsheet_id}/export",
        method="POST",
        data=payload,
        tool_name="workiva_export_spreadsheet",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Export spreadsheet", tool_name="workiva_export_spreadsheet")
        if isinstance(result, dict):
            return f"Export ready: {result.get('downloadUrl', result.get('url', ''))}"
        if isinstance(result, (bytes, bytearray)):
            # poll_operation already fetched the resourceUrl's bytes — the
            # export file itself, not a pointer to it. Persist it locally and
            # hand back the path (root-cause fix: previously this fell through
            # to the generic error branch below with the *original* 202 body,
            # which read like the operation never completed).
            EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
            ext = "xlsx" if format == "xlsx" else format
            out_path = EXPORTS_DIR / f"{spreadsheet_id}_{int(time.time())}.{ext}"
            out_path.write_bytes(result)
            return f"Export ready: {out_path}"
        if isinstance(result, str) and result:
            return f"Export ready: {result}"

    if s in (200, 201) and isinstance(body, dict):
        return f"Export ready: {body.get('downloadUrl', body.get('url', ''))}"

    return _error(s, body, hint="verify spreadsheet_id via workiva_list_spreadsheets" if s == 404 else "")


@mcp.tool()
async def workiva_publish_links(
    file_id: str,
    file_type: Literal["spreadsheet", "document", "presentation"] = "spreadsheet",
    publish_type: Literal["ownLinks", "allLinks", "selectedLinks"] = "ownLinks",
) -> str:
    """Publish links in a spreadsheet, document, or presentation.

    Args:
        file_id: The file UUID
        file_type: spreadsheet, document, or presentation
        publish_type: ownLinks (links owned by this file), allLinks, or selectedLinks
    """
    if file_type not in {"spreadsheet", "document", "presentation"}:
        return _error(400, {}, cause=f"invalid file_type: {file_type}", hint="use one of spreadsheet, document, presentation")
    if publish_type not in {"ownLinks", "allLinks", "selectedLinks"}:
        return _error(400, {}, cause=f"invalid publish_type: {publish_type}", hint="use one of ownLinks, allLinks, selectedLinks")
    type_map = {
        "spreadsheet": "spreadsheets",
        "document": "documents",
        "presentation": "presentations",
    }
    resource = type_map.get(file_type, "spreadsheets")

    s, headers, body = api_request(
        f"/{resource}/{file_id}/publishLinks",
        method="POST",
        data={"publishType": publish_type},
        tool_name="workiva_publish_links",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        poll_operation(loc, "Publish links", tool_name="workiva_publish_links")
        return f"Links published for {file_type} {file_id}."

    if s in (200, 204):
        return f"Links published for {file_type} {file_id}."

    return _error(s, body, hint=f"verify file_id for {file_type}" if s == 404 else "")


@mcp.tool()
async def workiva_reapply_filters(
    file_id: str,
    file_type: Literal["spreadsheet", "document", "presentation", "table"] = "spreadsheet",
) -> str:
    """Reapply filters on a file.

    Args:
        file_id: The file UUID
        file_type: spreadsheet, document, presentation, or table
    """
    if file_type not in {"spreadsheet", "document", "presentation", "table"}:
        return _error(400, {}, cause=f"invalid file_type: {file_type}", hint="use one of spreadsheet, document, presentation, table")
    type_map = {
        "spreadsheet": "spreadsheets",
        "document": "documents",
        "presentation": "presentations",
        "table": "tables",
    }
    resource = type_map.get(file_type, "spreadsheets")

    s, headers, body = api_request(
        f"/{resource}/{file_id}/reapplyFilters",
        method="POST",
        tool_name="workiva_reapply_filters",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        poll_operation(loc, "Reapply filters", tool_name="workiva_reapply_filters")
        return "Filters reapplied."

    if s in (200, 204):
        return "Filters reapplied."

    return _error(s, body, hint=f"verify file_id for {file_type}" if s == 404 else "")
