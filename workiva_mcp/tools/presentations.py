"""
Presentation tools — get details, manage slides.
"""

from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate
from workiva_mcp.server import mcp


@mcp.tool()
async def workiva_get_presentation(
    presentation_id: str,
) -> str:
    """Get presentation details.

    Args:
        presentation_id: The presentation UUID
    """
    s, _, body = api_request(
        f"/presentations/{presentation_id}",
        tool_name="workiva_get_presentation",
    )
    if s != 200:
        return _error(s, body, hint="verify presentation_id via workiva_list_files" if s == 404 else "")
    return (
        f"Name: {body.get('name', 'N/A')}\n"
        f"ID: {body.get('id', 'N/A')}\n"
        f"Modified: {body.get('modified', {}).get('dateTime', 'N/A')}"
    )


@mcp.tool()
async def workiva_list_presentation_layouts(presentation_id: str) -> str:
    """List layouts in a presentation.

    Args:
        presentation_id: The presentation UUID
    """
    try:
        items = paginate(
            f"/presentations/{presentation_id}/layouts",
            tool_name="workiva_list_presentation_layouts",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No layouts found."
    lines = [f"Layouts ({len(items)}):"]
    for l in items:
        lines.append(f"  {l.get('name', '?')}  (id: {l.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_list_presentation_slides(presentation_id: str) -> str:
    """List slides in a presentation.

    Args:
        presentation_id: The presentation UUID
    """
    try:
        items = paginate(
            f"/presentations/{presentation_id}/slides",
            tool_name="workiva_list_presentation_slides",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No slides found."
    lines = [f"Slides ({len(items)}):"]
    for s in items:
        lines.append(f"  Slide {s.get('index', '?')}  (id: {s.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_presentation_slide(
    presentation_id: str,
    slide_id: str,
) -> str:
    """Get a single presentation slide.

    Args:
        presentation_id: The presentation UUID
        slide_id: The slide ID
    """
    if not slide_id:
        return _error(400, {}, cause="slide_id is required")
    s, _, body = api_request(
        f"/presentations/{presentation_id}/slides/{slide_id}",
        tool_name="workiva_get_presentation_slide",
    )
    if s != 200:
        return _error(s, body, hint="verify slide_id via workiva_list_presentation_slides" if s == 404 else "")
    return str(body)


@mcp.tool()
async def workiva_update_presentation_slide(
    presentation_id: str,
    slide_id: str,
    patches: list[dict] | None = None,
) -> str:
    """Update a presentation slide via JSON Patch.

    Args:
        presentation_id: The presentation UUID
        slide_id: The slide ID
        patches: JSON Patch array
    """
    if not slide_id or not patches:
        return _error(400, {}, cause="slide_id and patches are required")
    s, _, body = api_request(
        f"/presentations/{presentation_id}/slides/{slide_id}",
        method="PATCH",
        data=patches,
        content_type="application/json-patch+json",
        tool_name="workiva_update_presentation_slide",
    )
    if s == 200:
        return "Slide updated."
    return _error(s, body, hint="verify slide_id via workiva_list_presentation_slides" if s == 404 else "")


@mcp.tool()
async def workiva_presentation_slides(
    presentation_id: str,
    operation: str = "list_slides",
    slide_id: str = "",
    patches: list[dict] | None = None,
) -> str:
    """# DEPRECATED — use workiva_list_presentation_layouts / workiva_list_presentation_slides / workiva_get_presentation_slide / workiva_update_presentation_slide.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list_layouts":
        return await workiva_list_presentation_layouts(presentation_id)
    if operation == "list_slides":
        return await workiva_list_presentation_slides(presentation_id)
    if operation == "get_slide":
        return await workiva_get_presentation_slide(presentation_id, slide_id)
    if operation == "update_slide":
        return await workiva_update_presentation_slide(presentation_id, slide_id, patches)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list_layouts', 'list_slides', 'get_slide', or 'update_slide'")
