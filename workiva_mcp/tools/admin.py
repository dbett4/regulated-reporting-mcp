"""
Admin tools — organizations, users, workspaces, groups.
"""

from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate
from workiva_mcp.server import mcp

# ── Organizations ─────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_list_organizations() -> str:
    """List organizations."""
    try:
        items = paginate(
            "/organizations", api="platform", tool_name="workiva_list_organizations"
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No organizations found."
    lines = [f"Organizations ({len(items)}):"]
    for o in items:
        lines.append(f"  {o.get('name', '?')}  (id: {o.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_organization(org_id: str) -> str:
    """Get organization details.

    Args:
        org_id: Organization ID
    """
    if not org_id:
        return _error(400, {}, cause="org_id is required")
    s, _, body = api_request(
        f"/organizations/{org_id}", api="platform", tool_name="workiva_get_organization"
    )
    if s != 200:
        return _error(s, body, hint="verify org_id via workiva_list_organizations" if s == 404 else "")
    return f"Organization: {body.get('name', 'N/A')}\nID: {body.get('id', 'N/A')}"


@mcp.tool()
async def workiva_list_organization_users(org_id: str) -> str:
    """List users in an organization.

    Args:
        org_id: Organization ID
    """
    if not org_id:
        return _error(400, {}, cause="org_id is required")
    try:
        items = paginate(
            f"/organizations/{org_id}/users",
            api="platform", tool_name="workiva_list_organization_users",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No users found."
    lines = [f"Users ({len(items)}):"]
    for u in items:
        name = f"{u.get('firstName', '')} {u.get('lastName', '')}".strip()
        lines.append(f"  {name}  (id: {u.get('id', '?')}, email: {u.get('email', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_list_organization_roles(org_id: str) -> str:
    """List roles in an organization.

    Args:
        org_id: Organization ID
    """
    if not org_id:
        return _error(400, {}, cause="org_id is required")
    try:
        items = paginate(
            f"/organizations/{org_id}/roles",
            api="platform", tool_name="workiva_list_organization_roles",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No roles found."
    lines = [f"Roles ({len(items)}):"]
    for r in items:
        lines.append(f"  {r.get('name', '?')}  (id: {r.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_list_organization_solutions(org_id: str) -> str:
    """List solutions enabled for an organization.

    Args:
        org_id: Organization ID
    """
    if not org_id:
        return _error(400, {}, cause="org_id is required")
    try:
        items = paginate(
            f"/organizations/{org_id}/solutions",
            api="platform", tool_name="workiva_list_organization_solutions",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No solutions found."
    lines = [f"Solutions ({len(items)}):"]
    for s_item in items:
        lines.append(f"  {s_item.get('name', '?')}  (id: {s_item.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_organization(
    operation: str = "list",
    org_id: str = "",
    user_id: str = "",
) -> str:
    """# DEPRECATED — use workiva_list_organizations / workiva_get_organization / workiva_list_organization_users / workiva_list_organization_roles / workiva_list_organization_solutions.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_organizations()
    if operation == "get":
        return await workiva_get_organization(org_id)
    if operation == "users":
        return await workiva_list_organization_users(org_id)
    if operation == "roles":
        return await workiva_list_organization_roles(org_id)
    if operation == "solutions":
        return await workiva_list_organization_solutions(org_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'get', 'users', 'roles', or 'solutions'")


# ── Workspaces ────────────────────────────────────────────────────────────────

@mcp.tool()
async def workiva_list_workspaces() -> str:
    """List workspaces."""
    try:
        items = paginate(
            "/workspaces", api="platform", tool_name="workiva_list_workspaces"
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No workspaces found."
    lines = [f"Workspaces ({len(items)}):"]
    for w in items:
        lines.append(f"  {w.get('name', '?')}  (id: {w.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_workspace(workspace_id: str) -> str:
    """Get workspace details.

    Args:
        workspace_id: Workspace ID
    """
    if not workspace_id:
        return _error(400, {}, cause="workspace_id is required")
    s, _, body = api_request(
        f"/workspaces/{workspace_id}", api="platform", tool_name="workiva_get_workspace"
    )
    if s != 200:
        return _error(s, body, hint="verify workspace_id via workiva_list_workspaces" if s == 404 else "")
    return f"Workspace: {body.get('name', 'N/A')}\nID: {body.get('id', 'N/A')}"


@mcp.tool()
async def workiva_list_workspace_groups(workspace_id: str) -> str:
    """List groups in a workspace.

    Args:
        workspace_id: Workspace ID
    """
    if not workspace_id:
        return _error(400, {}, cause="workspace_id is required")
    try:
        items = paginate(
            f"/workspaces/{workspace_id}/groups",
            api="platform", tool_name="workiva_list_workspace_groups",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No groups found."
    lines = [f"Groups ({len(items)}):"]
    for g in items:
        lines.append(f"  {g.get('name', '?')}  (id: {g.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_list_workspace_memberships(
    workspace_id: str,
    group_id: str = "",
) -> str:
    """List memberships in a workspace or workspace group.

    Args:
        workspace_id: Workspace ID
        group_id: Group ID (optional — limits to a group's memberships)
    """
    if not workspace_id:
        return _error(400, {}, cause="workspace_id is required")
    path = f"/workspaces/{workspace_id}/groups/{group_id}/memberships" if group_id else f"/workspaces/{workspace_id}/memberships"
    try:
        items = paginate(path, api="platform", tool_name="workiva_list_workspace_memberships")
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No memberships found."
    lines = [f"Memberships ({len(items)}):"]
    for m in items:
        lines.append(f"  User: {m.get('user', '?')}  Role: {m.get('role', '?')}")
    return "\n".join(lines)


@mcp.tool()
async def workiva_workspace(
    operation: str = "list",
    workspace_id: str = "",
    group_id: str = "",
) -> str:
    """# DEPRECATED — use workiva_list_workspaces / workiva_get_workspace / workiva_list_workspace_groups / workiva_list_workspace_memberships.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_workspaces()
    if operation == "get":
        return await workiva_get_workspace(workspace_id)
    if operation == "groups":
        return await workiva_list_workspace_groups(workspace_id)
    if operation == "memberships":
        return await workiva_list_workspace_memberships(workspace_id, group_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'get', 'groups', or 'memberships'")
