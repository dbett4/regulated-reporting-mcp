"""
Task management tools — CRUD + submit/approve.
"""

from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate
from workiva_mcp.server import mcp


@mcp.tool()
async def workiva_list_tasks(
    filter: str = "",
    max_results: int = 50,
) -> str:
    """List tasks in the workspace.

    Args:
        filter: OData filter (e.g., "status eq 'Open'")
        max_results: Max results to return
    """
    params = []
    if filter:
        params.append(f"$filter={filter}")
    params.append(f"$maxpagesize={min(max_results, 50)}")
    path = "/tasks" + ("?" + "&".join(params) if params else "")

    try:
        items = paginate(path, tool_name="workiva_list_tasks")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]

    if not items:
        return "No tasks found."

    lines = [f"Tasks ({len(items)}):"]
    for t in items:
        status = t.get("status", "?")
        lines.append(f"  [{status}] {t.get('title', t.get('name', '?'))}  (id: {t.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_task(task_id: str) -> str:
    """Get details for a single task.

    Args:
        task_id: The task UUID
    """
    if not task_id:
        return _error(400, {}, cause="task_id is required")
    s, _, body = api_request(f"/tasks/{task_id}", tool_name="workiva_get_task")
    if s != 200:
        return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")
    return (
        f"Task: {body.get('title', body.get('name', 'N/A'))}\n"
        f"ID: {body.get('id', 'N/A')}\n"
        f"Status: {body.get('status', 'N/A')}\n"
        f"Assignee: {body.get('assignee', 'N/A')}\n"
        f"Due: {body.get('dueDate', 'N/A')}\n"
        f"Description: {body.get('description', 'N/A')}"
    )


@mcp.tool()
async def workiva_create_task(
    name: str = "",
    assignee: str = "",
    approver: str = "",
    due_date: str = "",
    file: str = "",
    description: str = "",
) -> str:
    """Create a new task.

    Args:
        name: Task title (sent as `title` per the 2026-01-01 API; `name` is rejected)
        assignee: User ID to assign (wrapped as {id, type:"USER"})
        approver: User ID for the approval step (wrapped as a participant)
        due_date: Due date ISO string
        file: File ID to associate
        description: Task description

    Note: the v2026 Tasks API requires `title` (NOT `name`) and OBJECT assignees
    `[{"id":..,"type":"USER"}]` — confirmed against the live API 2026-05-31 with
    a readback-verified create probe. This tool maps the flat args to that shape
    so callers keep working.
    """
    payload: dict = {}
    if name:
        payload["title"] = name  # v2026: field is `title`, not `name`
    if assignee:
        payload["assignees"] = [{"id": assignee, "type": "USER"}]
    if approver:
        payload["approvalSteps"] = [{"participants": [{"id": approver, "type": "USER"}]}]
    if due_date:
        payload["dueDate"] = due_date
    if file:
        payload["file"] = file
    if description:
        payload["description"] = description

    s, _, body = api_request("/tasks", method="POST", data=payload, tool_name="workiva_create_task")
    if s in (200, 201):
        # v2026 returns the title under `title`; fall back to `name` then the input
        title = body.get("title", body.get("name", name))
        return f"Task created: {title} (id: {body.get('id', '?')})"
    return _error(s, body, cause=f"create task failed: {str(body)[:200]}")


@mcp.tool()
async def workiva_update_task(
    task_id: str,
    patches: list[dict] | None = None,
) -> str:
    """Update a task via JSON Patch.

    Args:
        task_id: The task UUID
        patches: JSON Patch array
    """
    if not task_id or not patches:
        return _error(400, {}, cause="task_id and patches are required")
    s, _, body = api_request(
        f"/tasks/{task_id}",
        method="PATCH",
        data=patches,
        content_type="application/json-patch+json",
        tool_name="workiva_update_task",
    )
    if s == 200:
        return f"Task updated: {body.get('title', body.get('name', '?'))}"
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_delete_task(task_id: str) -> str:
    """Delete a task.

    Args:
        task_id: The task UUID
    """
    if not task_id:
        return _error(400, {}, cause="task_id is required")
    s, _, body = api_request(f"/tasks/{task_id}", method="DELETE", tool_name="workiva_delete_task")
    if s in (200, 204):
        return f"Task {task_id} deleted."
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_tasks(
    operation: str = "list",
    task_id: str = "",
    filter: str = "",
    name: str = "",
    assignee: str = "",
    approver: str = "",
    due_date: str = "",
    file: str = "",
    description: str = "",
    patches: list[dict] | None = None,
    max_results: int = 50,
) -> str:
    """# DEPRECATED — use workiva_list_tasks / workiva_get_task / workiva_create_task / workiva_update_task / workiva_delete_task.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_tasks(filter, max_results)
    if operation == "get":
        return await workiva_get_task(task_id)
    if operation == "create":
        return await workiva_create_task(name, assignee, approver, due_date, file, description)
    if operation == "update":
        return await workiva_update_task(task_id, patches)
    if operation == "delete":
        return await workiva_delete_task(task_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'create', 'get', 'update', or 'delete'")


@mcp.tool()
async def workiva_submit_task(task_id: str) -> str:
    """Submit a task.

    Args:
        task_id: The task UUID
    """
    s, _, body = api_request(
        f"/tasks/{task_id}/submit", method="POST", tool_name="workiva_submit_task"
    )
    if s in (200, 204):
        return "Task submit successful."
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_approve_task(task_id: str) -> str:
    """Approve a task.

    Args:
        task_id: The task UUID
    """
    s, _, body = api_request(
        f"/tasks/{task_id}/approve", method="POST", tool_name="workiva_approve_task"
    )
    if s in (200, 204):
        return "Task approve successful."
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_reject_task(task_id: str) -> str:
    """Reject a task.

    Args:
        task_id: The task UUID
    """
    s, _, body = api_request(
        f"/tasks/{task_id}/reject", method="POST", tool_name="workiva_reject_task"
    )
    if s in (200, 204):
        return "Task reject successful."
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_reassign_task(task_id: str) -> str:
    """Reassign a task.

    Args:
        task_id: The task UUID
    """
    s, _, body = api_request(
        f"/tasks/{task_id}/reassign", method="POST", tool_name="workiva_reassign_task"
    )
    if s in (200, 204):
        return "Task reassign successful."
    return _error(s, body, hint="verify task_id via workiva_list_tasks" if s == 404 else "")


@mcp.tool()
async def workiva_task_actions(
    task_id: str,
    action: str = "submit",
) -> str:
    """# DEPRECATED — use workiva_submit_task / workiva_approve_task / workiva_reject_task / workiva_reassign_task.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if action == "submit":
        return await workiva_submit_task(task_id)
    if action == "approve":
        return await workiva_approve_task(task_id)
    if action == "reject":
        return await workiva_reject_task(task_id)
    if action == "reassign":
        return await workiva_reassign_task(task_id)
    return _error(400, {}, cause=f"unknown action: {action}", hint="use 'submit', 'approve', 'reject', or 'reassign'")
