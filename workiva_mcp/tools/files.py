"""
File management tools — list, get, create, import, export, update, permissions.
"""

from typing import Literal

from workiva_mcp.auth import ensure_token
from workiva_mcp.config import MAX_LIST_ITEMS
from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate, poll_operation
from workiva_mcp.reapi import WorkivaOperationError, operation_location_from_parts
from workiva_mcp.server import mcp

FILE_KINDS = {
    "spreadsheet": "Spreadsheet",
    "document": "Document",
    "presentation": "Presentation",
    "folder": "Folder",
}
FILE_DELETE_UNSUPPORTED = (
    "File deletion is not supported via the Workiva REST API (UI-only operation)."
)


def _operation_location(headers: dict, body: object) -> str:
    """Extract Workiva async operation location from headers or body.

    Phase 1 reverse-engineering receipts confirmed export operations can return
    the operation URL as body.operationLocation, not only as an HTTP header.
    Keep this local helper until the broader tools layer is migrated to reapi.
    """

    try:
        return operation_location_from_parts(headers, body)
    except WorkivaOperationError:
        return ""


def _normalize_file_kind(kind: str) -> str:
    value = str(kind or "").strip()
    if not value:
        return value
    return FILE_KINDS.get(value.lower(), value[:1].upper() + value[1:].lower())


def create_workiva_file(name: str, kind: str = "Document", container: str = "") -> dict:
    """Create a Workiva file and return the API body."""
    ensure_token()
    normalized_kind = _normalize_file_kind(kind)
    payload: dict = {"name": name, "kind": normalized_kind}
    if container:
        payload["container"] = container
    status, headers, body = api_request(
        "/files", method="POST", data=payload, tool_name="create_workiva_file"
    )
    if status == 202:
        location = _operation_location(headers, body)
        if not location:
            raise WorkivaAPIError(202, "202 accepted but no operation location in headers or body")
        result = poll_operation(
            location, f"Create {normalized_kind}", tool_name="create_workiva_file"
        )
        if isinstance(result, dict):
            return result
        raise WorkivaAPIError(500, f"unexpected poll result: {result!r}")
    if status in (200, 201) and isinstance(body, dict):
        return body
    raise WorkivaAPIError(status, body if isinstance(body, str) else str(body))


@mcp.tool()
async def workiva_list_files(
    filter: str = "",
    order_by: str = "modified.dateTime desc",
    max_results: int = 50,
) -> str:
    """List files in the Workiva workspace.

    Args:
        filter: OData filter (e.g., "name contains 'budget'", "kind eq 'Spreadsheet'")
        order_by: Sort order (e.g., "modified.dateTime desc", "name asc")
        max_results: Maximum files to return (default 50)
    """
    params = []
    if filter:
        params.append(f"$filter={filter}")
    if order_by:
        params.append(f"$orderBy={order_by}")
    params.append(f"$maxpagesize={min(max_results, MAX_LIST_ITEMS)}")

    path = "/files"
    if params:
        path += "?" + "&".join(params)

    try:
        items = paginate(path, tool_name="workiva_list_files")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]

    if not items:
        return "No files found."

    lines = [f"Found {len(items)} file(s):\n"]
    for f in items:
        kind = f.get("kind", "?")
        name = f.get("name", "Untitled")
        fid = f.get("id", "")
        modified = f.get("modified", {}).get("dateTime", "")[:10]
        lines.append(f"  [{kind}] {name}  (id: {fid})  modified: {modified}")

    return "\n".join(lines)


@mcp.tool()
async def workiva_get_file(file_id: str) -> str:
    """Get details for a specific file by ID.

    Args:
        file_id: The file UUID
    """
    s, _, body = api_request(f"/files/{file_id}", tool_name="workiva_get_file")
    if s != 200:
        return _error(s, body, hint="verify file_id via workiva_list_files" if s == 404 else "")

    lines = [
        f"Name: {body.get('name', 'N/A')}",
        f"Kind: {body.get('kind', 'N/A')}",
        f"ID: {body.get('id', 'N/A')}",
        f"Created: {body.get('created', {}).get('dateTime', 'N/A')}",
        f"Modified: {body.get('modified', {}).get('dateTime', 'N/A')}",
        f"Container: {body.get('container', 'N/A')}",
    ]
    return "\n".join(lines)


@mcp.tool()
async def workiva_create_file(
    name: str,
    kind: Literal["Spreadsheet", "Document", "Presentation", "Folder"] = "Spreadsheet",
    container: str = "",
) -> str:
    """Create a new file or folder in Workiva.

    Args:
        name: File name
        kind: File type — Spreadsheet, Document, Presentation, or Folder
        container: Parent folder ID (optional)
    """
    normalized_kind = _normalize_file_kind(kind)
    payload = {"name": name, "kind": normalized_kind}
    if container:
        payload["container"] = container

    s, headers, body = api_request(
        "/files", method="POST", data=payload, tool_name="workiva_create_file"
    )

    if s == 202:
        location = _operation_location(headers, body)
        if not location:
            return _error(
                202,
                body,
                cause="202 accepted but no operation location in headers or body",
                retry_safe=True,
            )
        result = poll_operation(
            location, f"Create {normalized_kind}", tool_name="workiva_create_file"
        )
        if isinstance(result, dict):
            return (
                f"Created {normalized_kind}: {result.get('name', name)} "
                f"(id: {result.get('id', 'N/A')})"
            )
        return str(result)

    if s in (200, 201):
        return (
            f"Created {normalized_kind}: {body.get('name', name)} "
            f"(id: {body.get('id', 'N/A')})"
        )

    return _error(s, body)


def delete_file(file_id: str) -> None:
    """File deletion is unavailable via Workiva REST; UI action only."""
    raise WorkivaAPIError(405, FILE_DELETE_UNSUPPORTED)


@mcp.tool()
async def workiva_delete_file(file_id: str) -> str:
    """Return the explicit REST unsupported message for Workiva file deletion."""
    raise WorkivaAPIError(405, FILE_DELETE_UNSUPPORTED)


@mcp.tool()
async def workiva_import_file(
    name: str,
    file_name: str,
    kind: Literal["Spreadsheet", "Document"] = "Spreadsheet",
    file_path: str = "",
) -> str:
    """Import a local file (xlsx/docx) into Workiva.

    Args:
        name: Display name in Workiva
        file_name: Original filename (e.g., "report.xlsx")
        kind: Spreadsheet or Document
        file_path: Local path to the file to upload
    """
    if kind not in {"Spreadsheet", "Document"}:
        return _error(400, {}, cause=f"invalid kind: {kind}", hint="use one of Spreadsheet, Document")
    if not file_path:
        return _error(400, {}, cause="file_path is required")

    from pathlib import Path
    local = Path(file_path)
    if not local.exists():
        return _error(400, {}, cause=f"file not found: {file_path}", hint="provide an absolute path to a local file")

    # Step 1: Initiate import
    payload = {"name": name, "fileName": file_name, "kind": kind}
    s, headers, body = api_request(
        "/files/import", method="POST", data=payload, tool_name="workiva_import_file"
    )

    if s not in (200, 201, 202):
        return _error(s, body, cause=f"import initiate failed: {str(body)[:200]}")

    upload_url = body.get("uploadUrl") if isinstance(body, dict) else None
    op_location = _operation_location(headers, body)

    if not upload_url:
        return _error(s, body, cause="no uploadUrl in response", hint="Workiva did not return an upload URL — check file kind and name")

    # Step 2: Upload bytes
    from workiva_mcp.http_client import _fix_url
    upload_url = _fix_url(upload_url)
    file_bytes = local.read_bytes()

    s2, _, upload_body = api_request(
        upload_url,
        method="PUT",
        data=file_bytes,
        content_type="application/octet-stream",
        timeout=120,
        tool_name="workiva_import_file",
    )

    if s2 not in (200, 201, 202, 204):
        return _error(s2, upload_body, cause=f"upload failed: {str(upload_body)[:200]}")

    # Step 3: Poll for completion
    if not op_location:
        op_location = _operation_location({}, upload_body)

    if op_location:
        result = poll_operation(op_location, "Import", tool_name="workiva_import_file")
        if isinstance(result, dict):
            file_id = result.get("id") or result.get("fileId")
            return f"Imported successfully!\n  Name: {result.get('name', name)}\n  File ID: {file_id}"
        return str(result)

    return f"Import submitted. Check Workiva for '{name}'."


@mcp.tool()
async def workiva_export_file(
    file_id: str,
    format: Literal["xlsx", "pdf", "csv", "docx", "xhtml"] = "xlsx",
) -> str:
    """Export a file from Workiva. Returns a download URL.

    Args:
        file_id: The file UUID
        format: Export format (xlsx, pdf, csv, docx, xhtml)
    """
    if format not in {"xlsx", "pdf", "csv", "docx", "xhtml"}:
        return _error(400, {}, cause=f"invalid format: {format}", hint="use one of xlsx, pdf, csv, docx, xhtml")
    payload = {"format": format}
    s, headers, body = api_request(
        f"/files/{file_id}/export",
        method="POST",
        data=payload,
        tool_name="workiva_export_file",
    )

    if s == 202:
        location = _operation_location(headers, body)
        if not location:
            return _error(202, body, cause="202 accepted but no operation location in headers or body", retry_safe=True)
        result = poll_operation(location, "Export", tool_name="workiva_export_file")
        if isinstance(result, dict):
            url = result.get("downloadUrl") or result.get("url", "")
            return f"Export ready: {url}"
        return str(result)

    if s in (200, 201) and isinstance(body, dict):
        url = body.get("downloadUrl") or body.get("url", "")
        return f"Export ready: {url}"

    return _error(s, body, hint="verify file_id exists and format is supported (xlsx, pdf, csv, docx, xhtml)" if s == 404 else "")


@mcp.tool()
async def workiva_update_file(
    file_id: str,
    patches: list[dict],
) -> str:
    """Update a file using JSON Patch operations (rename, move, etc.).

    Args:
        file_id: The file UUID
        patches: JSON Patch array, e.g. [{"op": "replace", "path": "/name", "value": "New Name"}]
    """
    s, _, body = api_request(
        f"/files/{file_id}",
        method="PATCH",
        data=patches,
        content_type="application/json-patch+json",
        tool_name="workiva_update_file",
    )

    if s == 200:
        return f"Updated: {body.get('name', 'N/A')} (id: {file_id})"
    return _error(s, body, hint="verify file_id and JSON Patch path/op are valid" if s in (400, 404) else "")


@mcp.tool()
async def workiva_file_permissions(
    file_id: str,
    operation: Literal["get", "modify"] = "get",
    to_assign: list[dict] | None = None,
    to_revoke: list[dict] | None = None,
) -> str:
    """Get or modify file permissions.

    Args:
        file_id: The file UUID
        operation: "get" to list permissions, "modify" to change them
        to_assign: Permissions to assign, e.g. [{"permission": "Edit", "principal": "user-id"}]
        to_revoke: Permissions to revoke, e.g. [{"permission": "Edit", "principal": "user-id"}]
    """
    if operation not in {"get", "modify"}:
        return _error(400, {}, cause=f"invalid operation: {operation}", hint="use one of get, modify")
    if operation == "get":
        s, _, body = api_request(
            f"/files/{file_id}/permissions",
            tool_name="workiva_file_permissions",
        )
        if s != 200:
            return _error(s, body, hint="verify file_id via workiva_list_files" if s == 404 else "")

        perms = body.get("data", []) if isinstance(body, dict) else []
        if not perms:
            return "No permissions found."

        lines = [f"Permissions for file {file_id}:"]
        for p in perms:
            lines.append(f"  {p.get('principal', '?')}: {p.get('permission', '?')}")
        return "\n".join(lines)

    elif operation == "modify":
        payload = {}
        if to_assign:
            payload["toAssign"] = to_assign
        if to_revoke:
            payload["toRevoke"] = to_revoke

        s, _, body = api_request(
            f"/files/{file_id}/permissions",
            method="PATCH",
            data=payload,
            tool_name="workiva_file_permissions",
        )
        if s in (200, 204):
            return "Permissions updated."
        return _error(s, body)

    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'get' or 'modify'")
