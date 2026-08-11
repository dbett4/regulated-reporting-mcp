"""
Chain management tools — list, get, trigger runs.
"""

from workiva_mcp.http_client import WorkivaAPIError, _error, api_request, paginate
from workiva_mcp.server import mcp


@mcp.tool()
async def workiva_list_chains(max_results: int = 50) -> str:
    """List Wdata chains.

    Args:
        max_results: Max results to return
    """
    try:
        items = paginate(
            "/chains", api="chains", tool_name="workiva_list_chains"
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    items = items[:max_results]
    if not items:
        return "No chains found."
    lines = [f"Chains ({len(items)}):"]
    for c in items:
        lines.append(f"  {c.get('name', '?')}  (id: {c.get('id', '?')})")
    return "\n".join(lines)


@mcp.tool()
async def workiva_get_chain(chain_id: str) -> str:
    """Get chain details.

    Args:
        chain_id: The chain UUID
    """
    if not chain_id:
        return _error(400, {}, cause="chain_id is required")
    s, _, body = api_request(
        f"/chains/{chain_id}", api="chains", tool_name="workiva_get_chain"
    )
    if s != 200:
        return _error(s, body, hint="verify chain_id via workiva_list_chains" if s == 404 else "")
    return (
        f"Chain: {body.get('name', 'N/A')}\n"
        f"ID: {body.get('id', 'N/A')}\n"
        f"Status: {body.get('status', 'N/A')}"
    )


@mcp.tool()
async def workiva_list_chain_runs(chain_id: str) -> str:
    """List runs of a chain.

    Args:
        chain_id: The chain UUID
    """
    if not chain_id:
        return _error(400, {}, cause="chain_id is required")
    try:
        items = paginate(
            f"/chains/{chain_id}/runs",
            api="chains", tool_name="workiva_list_chain_runs",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No runs found."
    lines = [f"Runs ({len(items)}):"]
    for r in items:
        lines.append(
            f"  {r.get('id', '?')} — status: {r.get('status', '?')}, "
            f"started: {r.get('startedAt', 'N/A')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def workiva_list_chain_run_nodes(chain_id: str, run_id: str) -> str:
    """List nodes within a chain run.

    Args:
        chain_id: The chain UUID
        run_id: The chain run ID
    """
    if not chain_id or not run_id:
        return _error(400, {}, cause="chain_id and run_id are required")
    try:
        items = paginate(
            f"/chains/{chain_id}/runs/{run_id}/nodes",
            api="chains", tool_name="workiva_list_chain_run_nodes",
        )
    except WorkivaAPIError as e:
        return _error(e.status, e.body)
    if not items:
        return "No nodes found."
    lines = [f"Nodes ({len(items)}):"]
    for n in items:
        lines.append(
            f"  {n.get('name', '?')} — status: {n.get('status', '?')}, "
            f"duration: {n.get('duration', 'N/A')}"
        )
    return "\n".join(lines)


@mcp.tool()
async def workiva_chains(
    operation: str = "list",
    chain_id: str = "",
    run_id: str = "",
    filter: str = "",
    max_results: int = 50,
) -> str:
    """# DEPRECATED — use workiva_list_chains / workiva_get_chain / workiva_list_chain_runs / workiva_list_chain_run_nodes.

    Kept as a backward-compatibility shim; dispatches to the single-verb tools above.
    """
    if operation == "list":
        return await workiva_list_chains(max_results)
    if operation == "get":
        return await workiva_get_chain(chain_id)
    if operation == "runs":
        return await workiva_list_chain_runs(chain_id)
    if operation == "run_nodes":
        return await workiva_list_chain_run_nodes(chain_id, run_id)
    return _error(400, {}, cause=f"unknown operation: {operation}", hint="use 'list', 'get', 'runs', or 'run_nodes'")


@mcp.tool()
async def workiva_run_chain(
    chain_id: str,
    environment_id: str = "",
    runtime_inputs: dict | None = None,
) -> str:
    """Trigger a chain run.

    Args:
        chain_id: The chain UUID
        environment_id: Environment ID (optional)
        runtime_inputs: Runtime input parameters as key-value pairs (optional)
    """
    payload = {}
    if environment_id:
        payload["environmentId"] = environment_id
    if runtime_inputs:
        payload["runtimeInputs"] = runtime_inputs

    s, _, body = api_request(
        f"/chains/{chain_id}/runs",
        method="POST",
        data=payload,
        api="chains",
        tool_name="workiva_run_chain",
    )

    if s in (200, 201, 202):
        rid = body.get("id", "?") if isinstance(body, dict) else "?"
        status = body.get("status", "?") if isinstance(body, dict) else "?"
        return f"Chain run started. Run ID: {rid}, Status: {status}"
    return _error(s, body, hint="verify chain_id via workiva_list_chains" if s == 404 else "")


@mcp.tool()
async def workiva_chain_run(
    chain_id: str,
    environment_id: str = "",
    runtime_inputs: dict | None = None,
) -> str:
    """# DEPRECATED — use workiva_run_chain.

    Kept as a backward-compatibility shim; dispatches to the single-verb tool above.
    """
    return await workiva_run_chain(chain_id, environment_id, runtime_inputs)
