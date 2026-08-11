"""
Linking tools — anchors, destination links, range links, full link flow.
"""

from typing import Literal

from workiva_mcp.http_client import _error, api_request, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp


def _operation_location(headers: dict, body: object) -> str:
    try:
        return operation_location_from_parts(headers, body)
    except WorkivaOperationError:
        return ""


def _create_anchor_structured(
    table_id: str,
    revision: str,
    start_row: int = 0,
    stop_row: int = 0,
    start_column: int = 0,
    stop_column: int = 0,
) -> dict:
    """Create a source anchor and return a structured result.

    Returns a dict with keys `anchor_id`, `revision`, and `error`.
    `error` is None on success and a JSON error string (from `_error`) on failure.
    This helper exists so callers like `workiva_full_link_flow` can chain
    without parsing formatted strings.
    """
    payload = {
        "revision": revision,
        "range": {
            "startRow": start_row,
            "stopRow": stop_row,
            "startColumn": start_column,
            "stopColumn": stop_column,
        },
    }

    s, headers, body = api_request(
        f"/content/tables/{table_id}/anchors/creation",
        method="POST",
        data=payload,
        tool_name="workiva_create_anchor",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return {
                "anchor_id": "",
                "revision": "",
                "error": _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True),
            }
        result = poll_operation(loc, "Create anchor", tool_name="workiva_create_anchor")
        if isinstance(result, dict):
            data = result.get("data", [])
            if data and isinstance(data, list):
                return {
                    "anchor_id": data[0].get("anchor", ""),
                    "revision": result.get("revision", ""),
                    "error": None,
                }
            return {
                "anchor_id": "",
                "revision": "",
                "error": _error(500, result, cause="anchor created but no ID in response"),
            }

    if s in (200, 201) and isinstance(body, dict):
        data = body.get("data", [])
        return {
            "anchor_id": data[0].get("anchor", "") if data else "",
            "revision": body.get("revision", ""),
            "error": None,
        }

    return {
        "anchor_id": "",
        "revision": "",
        "error": _error(s, body, hint="verify table_id via workiva_spreadsheet_sheets" if s == 404 else ""),
    }


@mcp.tool()
async def workiva_create_anchor(
    table_id: str,
    revision: str,
    start_row: int = 0,
    stop_row: int = 0,
    start_column: int = 0,
    stop_column: int = 0,
) -> str:
    """Create a source anchor on spreadsheet cells.

    Args:
        table_id: The table UUID (from workiva_spreadsheet_sheets)
        revision: Current table revision string
        start_row: Start row (0-based)
        stop_row: Stop row (0-based, same as start for single cell)
        start_column: Stop column (0-based, same as start for single cell)
        stop_column: Stop column (0-based, same as start for single cell)
    """
    r = _create_anchor_structured(
        table_id=table_id,
        revision=revision,
        start_row=start_row,
        stop_row=stop_row,
        start_column=start_column,
        stop_column=stop_column,
    )
    if r["error"]:
        return r["error"]
    return f"Anchor created: {r['anchor_id']}\nRevision: {r['revision']}"


@mcp.tool()
async def workiva_create_destination_link(
    target_type: Literal["richText", "table"],
    target_id: str,
    revision: str,
    source_anchor: str,
    paragraph_index: int = 0,
    offset: int = 0,
    row: int = 0,
    column: int = 0,
    source_range_link: str = "",
    source_table: str = "",
) -> str:
    """Insert a destination link in a document or table cell.

    Args:
        target_type: "richText" for document paragraphs, "table" for doc-table cells
        target_id: The richText UUID or doc-table UUID
        revision: Current revision of the target
        source_anchor: The source anchor ID for richText targets (from workiva_create_anchor)
        paragraph_index: Paragraph index for richText targets (0-based)
        offset: Character offset within the paragraph for richText targets
        row: Row index (deprecated for table targets; unused in range-link flow)
        column: Column index (deprecated for table targets; unused in range-link flow)
        source_range_link: (table branch only) ID of the source range link created via a
            createSource call on the spreadsheet table. Required when target_type=="table"
            (the full workflow is createSource → createDestination).
        source_table: (table branch only) ID of the spreadsheet source table. Required when
            target_type=="table".

    NOTE — table branch:
        The legacy insertCellDestinationLink / /content/tables/{id}/links/edit path is dead
        (proven 404 in production, 2026-06). The table branch now uses the proven
        range-link createDestination flow:
          POST /content/tables/{target_id}/rangeLinks/edit
          body: {"type":"createDestination","createDestination":{"sourceRangeLink": ..., "sourceTable": ...}}
        This requires a pre-created source range-link ID (from createSource on the spreadsheet table)
        and the source table ID.
    """
    if target_type not in {"richText", "table"}:
        return _error(400, {}, cause=f"invalid target_type: {target_type}", hint="use one of richText, table")
    if target_type == "richText":
        payload = {
            "revision": revision,
            "data": [{
                "type": "insertDestinationLink",
                "insertDestinationLink": {
                    "insertAt": {
                        "paragraphIndex": paragraph_index,
                        "offset": offset,
                    },
                    "sourceAnchor": source_anchor,
                },
            }],
        }
        endpoint = f"/content/richText/{target_id}/links/edit"

    elif target_type == "table":
        if not source_range_link:
            return _error(400, {}, cause="source_range_link is required for target_type='table'",
                          hint="create a source range link via createSource on the spreadsheet table first")
        if not source_table:
            return _error(400, {}, cause="source_table is required for target_type='table'",
                          hint="provide the spreadsheet source table ID")
        payload = {
            "type": "createDestination",
            "createDestination": {
                "sourceRangeLink": source_range_link,
                "sourceTable": source_table,
            },
        }
        endpoint = f"/content/tables/{target_id}/rangeLinks/edit"

    else:
        return _error(400, {}, cause=f"unknown target_type: {target_type}", hint="use 'richText' or 'table'")

    s, headers, body = api_request(
        endpoint,
        method="POST",
        data=payload,
        tool_name="workiva_create_destination_link",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Create destination link", tool_name="workiva_create_destination_link")
        if isinstance(result, dict):
            return f"Destination link created. Revision: {result.get('revision', '?')}"

    if s in (200, 201) and isinstance(body, dict):
        return f"Destination link created. Revision: {body.get('revision', '?')}"

    return _error(s, body, hint="verify target_id (richText or table UUID)" if s == 404 else "")


@mcp.tool()
async def workiva_list_range_links(content_id: str, table_id: str) -> str:
    """List range links on a table.

    Args:
        content_id: The content/spreadsheet UUID
        table_id: The table UUID
    """
    base = f"/content/{content_id}/tables/{table_id}/range-links"
    s, _, body = api_request(base, tool_name="workiva_list_range_links")
    if s != 200:
        return _error(s, body, hint="verify content_id and table_id" if s == 404 else "")
    items = body.get("data", []) if isinstance(body, dict) else []
    if not items:
        return "No range links found."
    lines = [f"Range links ({len(items)}):"]
    for rl in items:
        lines.append(f"  ID: {rl.get('id', '?')} — source: {rl.get('source', '?')}")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_range_link(
    content_id: str,
    table_id: str,
    range_link_id: str,
) -> str:
    """Get a single range link.

    Args:
        content_id: The content/spreadsheet UUID
        table_id: The table UUID
        range_link_id: The range link ID
    """
    if not range_link_id:
        return _error(400, {}, cause="range_link_id is required")
    base = f"/content/{content_id}/tables/{table_id}/range-links"
    s, _, body = api_request(f"{base}/{range_link_id}", tool_name="workiva_get_range_link")
    if s != 200:
        return _error(s, body, hint="verify range_link_id via workiva_list_range_links" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_get_range_link_destinations(
    content_id: str,
    table_id: str,
    range_link_id: str,
) -> str:
    """List destinations for a range link.

    Args:
        content_id: The content/spreadsheet UUID
        table_id: The table UUID
        range_link_id: The range link ID
    """
    if not range_link_id:
        return _error(400, {}, cause="range_link_id is required")
    base = f"/content/{content_id}/tables/{table_id}/range-links"
    s, _, body = api_request(
        f"{base}/{range_link_id}/destinations", tool_name="workiva_get_range_link_destinations"
    )
    if s != 200:
        return _error(s, body, hint="verify range_link_id via workiva_list_range_links" if s == 404 else "")
    items = body.get("data", []) if isinstance(body, dict) else []
    if not items:
        return "No destinations found."
    lines = [f"Destinations ({len(items)}):"]
    for d in items:
        lines.append(f"  {d.get('id', '?')} — table: {d.get('table', '?')}")
    return "\n".join(lines)


@mcp.tool()
async def workiva_edit_range_link(
    content_id: str,
    table_id: str,
    range_link_id: str,
    data: list[dict] | None = None,
) -> str:
    """Edit a range link via PATCH.

    Args:
        content_id: The content/spreadsheet UUID
        table_id: The table UUID
        range_link_id: The range link ID
        data: Edit data
    """
    if not range_link_id or not data:
        return _error(400, {}, cause="range_link_id and data are required for edit")
    base = f"/content/{content_id}/tables/{table_id}/range-links"
    s, _, body = api_request(
        f"{base}/{range_link_id}",
        method="PATCH",
        data={"data": data},
        tool_name="workiva_edit_range_link",
    )
    if s in (200, 204):
        return "Range link updated."
    return _error(s, body, hint="verify range_link_id via workiva_list_range_links" if s == 404 else "")


@mcp.tool()
async def workiva_range_links(
    content_id: str,
    table_id: str,
    operation: str = "list",
    range_link_id: str = "",
    data: list[dict] | None = None,
) -> str:
    """# DEPRECATED — use workiva_list_range_links / workiva_get_range_link / workiva_edit_range_link / workiva_get_range_link_destinations.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_range_links(content_id, table_id)
    if operation == "get":
        return await workiva_get_range_link(content_id, table_id, range_link_id)
    if operation == "get_destinations":
        return await workiva_get_range_link_destinations(content_id, table_id, range_link_id)
    if operation == "edit":
        return await workiva_edit_range_link(content_id, table_id, range_link_id, data)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'get', 'edit', or 'get_destinations'")


@mcp.tool()
async def workiva_full_link_flow(
    spreadsheet_id: str,
    table_id: str,
    table_revision: str,
    source_row: int,
    source_column: int,
    target_type: Literal["richText", "table"],
    target_id: str,
    target_revision: str,
    paragraph_index: int = 0,
    offset: int = 0,
    target_row: int = 0,
    target_column: int = 0,
    publish: bool = True,
) -> str:
    """End-to-end linking: create anchor on spreadsheet, insert destination in document/table, optionally publish.

    Args:
        spreadsheet_id: The spreadsheet UUID (for publish step)
        table_id: Source table UUID (where the anchor goes)
        table_revision: Current revision of the source table
        source_row: Row of the cell to anchor (0-based)
        source_column: Column of the cell to anchor (0-based)
        target_type: "richText" for document or "table" for table cell destination
        target_id: Target richText UUID or table UUID
        target_revision: Current revision of the target
        paragraph_index: For richText targets — paragraph index
        offset: For richText targets — character offset
        target_row: For table targets — destination row
        target_column: For table targets — destination column
        publish: Whether to publish links after creation (default True)
    """
    if target_type not in {"richText", "table"}:
        return _error(400, {}, cause=f"invalid target_type: {target_type}", hint="use one of richText, table")
    results = []

    # Step 1: Create anchor via structured helper (no string parsing)
    anchor = _create_anchor_structured(
        table_id=table_id,
        revision=table_revision,
        start_row=source_row,
        stop_row=source_row,
        start_column=source_column,
        stop_column=source_column,
    )
    if anchor["error"]:
        return f"Failed at step 1 (anchor creation): {anchor['error']}"

    anchor_id = anchor["anchor_id"]
    results.append(f"1. Anchor: {anchor_id} (revision: {anchor['revision']})")

    # Step 2: Create destination link
    dest_result = await workiva_create_destination_link(
        target_type=target_type,
        target_id=target_id,
        revision=target_revision,
        source_anchor=anchor_id,
        paragraph_index=paragraph_index,
        offset=offset,
        row=target_row,
        column=target_column,
    )
    results.append(f"2. Destination: {dest_result}")

    # Detect structured error JSON from _error()
    if dest_result.startswith('{"error": true'):
        return "\n".join(results) + "\nFailed at step 2 (destination link)."

    # Step 3: Publish (optional)
    if publish:
        pub_result = await workiva_publish_links(
            file_id=spreadsheet_id,
            file_type="spreadsheet",
            publish_type="allLinks",
        )
        results.append(f"3. Publish: {pub_result}")

    return "\n".join(results)


# Import for the publish step
from workiva_mcp.tools.spreadsheets import workiva_publish_links  # noqa: E402
