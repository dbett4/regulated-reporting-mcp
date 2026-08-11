"""
Document management tools — list, get, export, sections CRUD, rich text.
"""

from typing import Literal

from workiva_mcp.config import MAX_LIST_ITEMS
from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp


def _operation_location(headers: dict, body: object) -> str:
    try:
        return operation_location_from_parts(headers, body)
    except WorkivaOperationError:
        return ""


@mcp.tool()
async def workiva_list_documents(
    filter: str = "",
    max_results: int = 50,
) -> str:
    """List documents in the workspace.

    Args:
        filter: OData filter (e.g., "name contains 'ACFR'")
        max_results: Maximum results to return
    """
    params = []
    if filter:
        params.append(f"$filter={filter}")
    params.append(f"$maxpagesize={min(max_results, MAX_LIST_ITEMS)}")

    path = "/documents"
    if params:
        path += "?" + "&".join(params)

    try:
        items = paginate(path, tool_name="workiva_list_documents")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]

    if not items:
        return "No documents found."

    lines = [f"Found {len(items)} document(s):\n"]
    for doc in items:
        name = doc.get("name", "Untitled")
        did = doc.get("id", "")
        modified = doc.get("modified", {}).get("dateTime", "")[:10]
        lines.append(f"  {name}  (id: {did})  modified: {modified}")

    return "\n".join(lines)


@mcp.tool()
async def workiva_get_document(
    document_id: str,
    expand: str = "",
) -> str:
    """Get document details.

    Args:
        document_id: The document UUID
        expand: Comma-separated subresources (e.g., "sections")
    """
    path = f"/documents/{document_id}"
    if expand:
        path += f"?$expand={expand}"

    s, _, body = api_request(path, tool_name="workiva_get_document")
    if s != 200:
        return _error(s, body, hint="verify document_id via workiva_list_documents" if s == 404 else "")

    lines = [
        f"Name: {body.get('name', 'N/A')}",
        f"ID: {body.get('id', 'N/A')}",
        f"Modified: {body.get('modified', {}).get('dateTime', 'N/A')}",
    ]

    sections = body.get("sections", {}).get("data", [])
    if sections:
        lines.append(f"\nSections ({len(sections)}):")
        for sec in sections:
            rt = sec.get("body", {}).get("richText", {})
            rt_id = rt.get("id", "N/A") if isinstance(rt, dict) else "N/A"
            lines.append(f"  {sec.get('name', '?')}  (section: {sec.get('id', '?')}, richText: {rt_id})")

    return "\n".join(lines)


@mcp.tool()
async def workiva_export_document(
    document_id: str,
    format: Literal["pdf", "docx", "xhtml"] = "pdf",
    options: dict | None = None,
) -> str:
    """Export a document.

    Args:
        document_id: The document UUID
        format: Export format — pdf, docx, or xhtml
        options: Format-specific options (optional)
    """
    if format not in {"pdf", "docx", "xhtml"}:
        return _error(400, {}, cause=f"invalid format: {format}", hint="use one of pdf, docx, xhtml")
    payload = {"format": format}
    if options:
        payload.update(options)

    s, headers, body = api_request(
        f"/documents/{document_id}/export",
        method="POST",
        data=payload,
        tool_name="workiva_export_document",
    )

    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Export document", tool_name="workiva_export_document")
        if isinstance(result, dict):
            return f"Export ready: {result.get('downloadUrl', result.get('url', ''))}"

    if s in (200, 201) and isinstance(body, dict):
        return f"Export ready: {body.get('downloadUrl', body.get('url', ''))}"

    return _error(s, body, hint="verify document_id via workiva_list_documents" if s == 404 else "")


# ── Document sections ─────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_list_document_sections(document_id: str) -> str:
    """List sections within a document.

    Args:
        document_id: The document UUID
    """
    base = f"/documents/{document_id}/sections"
    try:
        items = paginate(base, tool_name="workiva_list_document_sections")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No sections found."

    lines = [f"Sections ({len(items)}):"]
    for sec in items:
        rt = sec.get("body", {}).get("richText", {})
        rt_id = rt.get("id", "N/A") if isinstance(rt, dict) else "N/A"
        lines.append(f"  {sec.get('name', '?')}  (section: {sec.get('id', '?')}, richText: {rt_id})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_document_section(document_id: str, section_id: str) -> str:
    """Get a single document section.

    Args:
        document_id: The document UUID
        section_id: The section UUID
    """
    if not section_id:
        return _error(400, {}, cause="section_id is required")
    base = f"/documents/{document_id}/sections"
    s, _, body = api_request(f"{base}/{section_id}", tool_name="workiva_get_document_section")
    if s != 200:
        return _error(s, body, hint="verify section_id via workiva_list_document_sections" if s == 404 else "")

    rt = body.get("body", {}).get("richText", {})
    rt_id = rt.get("id", "N/A") if isinstance(rt, dict) else "N/A"
    rev = rt.get("revision", "N/A") if isinstance(rt, dict) else "N/A"
    return (
        f"Section: {body.get('name', 'N/A')}\n"
        f"Section ID: {body.get('id', 'N/A')}\n"
        f"RichText ID: {rt_id}\n"
        f"Revision: {rev}"
    )


@mcp.tool()
async def workiva_create_document_section(
    document_id: str,
    name: str = "",
    index: int = -1,
) -> str:
    """Create a new section within a document.

    Args:
        document_id: The document UUID
        name: Section name (optional)
        index: Position index (-1 = append)
    """
    base = f"/documents/{document_id}/sections"
    payload = {}
    if name:
        payload["name"] = name
    if index >= 0:
        payload["index"] = index

    s, headers, body = api_request(
        base, method="POST", data=payload, tool_name="workiva_create_document_section"
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Create section", tool_name="workiva_create_document_section")
        if isinstance(result, dict):
            return f"Created section: {result.get('name', name)} (id: {result.get('id', '?')})"
    if s in (200, 201):
        return f"Created section: {body.get('name', name)} (id: {body.get('id', '?')})"
    return _error(s, body, hint="verify document_id via workiva_list_documents" if s == 404 else "")


@mcp.tool()
async def workiva_delete_document_section(document_id: str, section_id: str) -> str:
    """Delete a document section.

    Args:
        document_id: The document UUID
        section_id: The section UUID
    """
    if not section_id:
        return _error(400, {}, cause="section_id is required")
    base = f"/documents/{document_id}/sections"
    s, _, body = api_request(
        f"{base}/{section_id}", method="DELETE", tool_name="workiva_delete_document_section"
    )
    if s in (200, 204):
        return f"Section {section_id} deleted."
    return _error(s, body, hint="verify section_id via workiva_list_document_sections" if s == 404 else "")


@mcp.tool()
async def workiva_copy_document_section(
    document_id: str,
    section_id: str,
    target_document_id: str = "",
) -> str:
    """Copy a document section within or to another document.

    Args:
        document_id: Source document UUID
        section_id: Section UUID to copy
        target_document_id: Destination document UUID (empty = same document)
    """
    if not section_id:
        return _error(400, {}, cause="section_id is required")
    base = f"/documents/{document_id}/sections"
    payload = {}
    if target_document_id:
        payload["document"] = target_document_id

    s, headers, body = api_request(
        f"{base}/{section_id}/copy", method="POST", data=payload,
        tool_name="workiva_copy_document_section",
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Copy section", tool_name="workiva_copy_document_section")
        if isinstance(result, dict):
            return f"Copied section: {result.get('name', '?')} (id: {result.get('id', '?')})"
    if s in (200, 201):
        return f"Copied section (id: {body.get('id', '?')})"
    return _error(s, body, hint="verify section_id via workiva_list_document_sections" if s == 404 else "")


@mcp.tool()
async def workiva_bulk_edit_document_sections(
    document_id: str,
    edits: list[dict] | None = None,
) -> str:
    """Bulk-edit sections within a document.

    Args:
        document_id: The document UUID
        edits: Bulk edit operations
    """
    if not edits:
        return _error(400, {}, cause="edits list is required for bulk_edit")
    base = f"/documents/{document_id}/sections"
    s, _, body = api_request(
        f"{base}/bulkEdit", method="POST", data={"data": edits},
        tool_name="workiva_bulk_edit_document_sections",
    )
    if s in (200, 204):
        return "Bulk edit complete."
    return _error(s, body, hint="verify document_id via workiva_list_documents" if s == 404 else "")


@mcp.tool()
async def workiva_document_sections(
    document_id: str,
    operation: str = "list",
    section_id: str = "",
    name: str = "",
    index: int = -1,
    target_document_id: str = "",
    edits: list[dict] | None = None,
) -> str:
    """# DEPRECATED — use workiva_list_document_sections / workiva_get_document_section / workiva_create_document_section / workiva_delete_document_section / workiva_copy_document_section / workiva_bulk_edit_document_sections.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_document_sections(document_id)
    if operation == "get":
        return await workiva_get_document_section(document_id, section_id)
    if operation == "create":
        return await workiva_create_document_section(document_id, name, index)
    if operation == "delete":
        return await workiva_delete_document_section(document_id, section_id)
    if operation == "copy":
        return await workiva_copy_document_section(document_id, section_id, target_document_id)
    if operation == "bulk_edit":
        return await workiva_bulk_edit_document_sections(document_id, edits)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'create', 'get', 'delete', 'copy', or 'bulk_edit'")


# ── Rich text ─────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_read_rich_text_paragraphs(rich_text_id: str) -> str:
    """Read paragraphs from a rich text resource.

    Args:
        rich_text_id: The richText UUID
    """
    # Path segment is SINGULAR ("richText") — the 2026-01-01 content API 404s on the plural.
    base = f"/content/richText/{rich_text_id}"
    try:
        items = paginate(f"{base}/paragraphs", tool_name="workiva_read_rich_text_paragraphs")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No paragraphs found."

    lines = [f"Paragraphs ({len(items)}):"]
    for i, para in enumerate(items):
        content = para.get("content", [])
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                ts = item.get("textSpan", {})
                if isinstance(ts, dict):
                    text_parts.append(ts.get("text", ""))
        text = "".join(text_parts)
        if len(text) > 200:
            text = text[:200] + "..."
        lines.append(f"  [{i}] {text}")

    return "\n".join(lines)


@mcp.tool()
async def workiva_batch_edit_rich_text(
    rich_text_id: str,
    revision: str = "",
    data: list[dict] | None = None,
) -> str:
    """Batch edit rich text paragraphs.

    Args:
        rich_text_id: The richText UUID
        revision: Current revision
        data: Edit operations
    """
    if not data:
        return _error(400, {}, cause="data list is required for batch_edit")
    # Path segment is SINGULAR ("richText") — the 2026-01-01 content API 404s on the plural.
    base = f"/content/richText/{rich_text_id}"
    payload = {"revision": revision, "data": data}
    s, _, body = api_request(
        f"{base}/paragraphs",
        method="PATCH",
        data=payload,
        tool_name="workiva_batch_edit_rich_text",
    )
    if s == 200 and isinstance(body, dict):
        return f"Rich text updated. New revision: {body.get('revision', '?')}"
    return _error(s, body, hint="verify rich_text_id via workiva_list_document_sections" if s == 404 else "")


@mcp.tool()
async def workiva_duplicate_rich_text(rich_text_id: str) -> str:
    """Duplicate a rich text resource.

    Args:
        rich_text_id: The richText UUID
    """
    # Path segment is SINGULAR ("richText") — the 2026-01-01 content API 404s on the plural.
    base = f"/content/richText/{rich_text_id}"
    s, headers, body = api_request(
        f"{base}/duplicate", method="POST", tool_name="workiva_duplicate_rich_text"
    )
    if s == 202:
        loc = _operation_location(headers, body)
        if not loc:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(loc, "Duplicate rich text", tool_name="workiva_duplicate_rich_text")
        if isinstance(result, dict):
            return f"Duplicated richText: {result.get('id', '?')}"
    if s in (200, 201):
        return f"Duplicated richText: {body.get('id', '?')}"
    return _error(s, body, hint="verify rich_text_id via workiva_list_document_sections" if s == 404 else "")


@mcp.tool()
async def workiva_rich_text(
    rich_text_id: str,
    operation: str = "read_paragraphs",
    revision: str = "",
    data: list[dict] | None = None,
) -> str:
    """# DEPRECATED — use workiva_read_rich_text_paragraphs / workiva_batch_edit_rich_text / workiva_duplicate_rich_text.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "read_paragraphs":
        return await workiva_read_rich_text_paragraphs(rich_text_id)
    if operation == "batch_edit":
        return await workiva_batch_edit_rich_text(rich_text_id, revision, data)
    if operation == "duplicate":
        return await workiva_duplicate_rich_text(rich_text_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'read_paragraphs', 'batch_edit', or 'duplicate'")
