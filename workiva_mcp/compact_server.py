"""Compact Workiva MCP server.

Expose three MCP tools that front the full tool catalog:
- `workiva_search_tools` — discover Workiva tools on demand
- `workiva_call_tool` — dispatch one selected tool by name
- `workiva_batch_call` — run a small plan in one round trip

Large outputs and stored-result metadata live behind MCP resources rather than
being dumped into model context.
"""

from __future__ import annotations

import json
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from workiva_mcp.compact_tools import batch_call, call_tool, search_tools
from workiva_mcp.receipt_store import list_receipts, read_receipt
from workiva_mcp.result_store import list_results, read_result, read_result_metadata

load_dotenv()

mcp = FastMCP(
    "workiva-mcp-compact",
    instructions=(
        "Compact Workiva MCP. Use `workiva_search_tools` to discover "
        "Workiva tools (pass detail=stats for catalog size, detail=schema "
        "for full input schemas), `workiva_call_tool` for one operation, and "
        "`workiva_batch_call` to run a small plan in one MCP round trip. Each tool "
        "carries a reviewed risk level (read/write/destructive/external): read "
        "tools run freely; ordinary writes require `confirm_write=true`; destructive "
        "and external operations require their specific confirmation field. Large results are "
        "stored as `workiva-result://{id}` artifacts (metadata at "
        "`workiva-result-meta://{id}`, recent index at `workiva-results://index`) "
        "instead of flooding chat."
    ),
)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Search Workiva Tools",
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
)
def workiva_search_tools(
    query: str = "",
    family: str | None = None,
    detail: str = "summary",
    limit: int = 20,
) -> str:
    """Search Workiva tool definitions on demand.

    Args:
        query: Search terms such as "read cells", "range links", or "export spreadsheet".
        family: Optional module family filter, such as cells, wdata, linking.
        detail: name, summary, schema, or stats.
        limit: Maximum results to return, capped at 100.
    """
    return search_tools(query=query, family=family, detail=detail, limit=limit)


@mcp.tool(
    annotations=ToolAnnotations(
        title="Call Workiva Tool",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def workiva_call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    confirm_write: bool = False,
    confirm_destructive: bool = False,
    confirm_external: bool = False,
    max_chars: int = 8000,
    store_result_artifact: bool = False,
    store_over_chars: int = 12000,
) -> str:
    """Call one Workiva tool by exact name.

    Args:
        name: Exact tool name from workiva_search_tools.
        arguments: JSON object passed to the tool.
        confirm_write: Required for tools that may write, run, refresh, send, or structurally change state.
        max_chars: Maximum response characters returned to the model.
    """
    return await call_tool(
        name=name,
        arguments=arguments,
        confirm_write=confirm_write,
        confirm_destructive=confirm_destructive,
        confirm_external=confirm_external,
        max_chars=max_chars,
        store_result_artifact=store_result_artifact,
        store_over_chars=store_over_chars,
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Batch Workiva Tool Calls",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
)
async def workiva_batch_call(
    steps: list[dict[str, Any]],
    confirm_write: bool = False,
    max_chars_per_step: int = 2000,
    store_results: bool = True,
    stop_on_error: bool = True,
) -> str:
    """Run multiple Workiva tools in one MCP round trip.

    Args:
        steps: List of {name, arguments} objects.
        confirm_write: Required if any step may write, run, refresh, send, or structurally change state.
        max_chars_per_step: Preview size for each step result.
        store_results: Store full step outputs under workiva-result:// artifacts.
        stop_on_error: Stop after the first step returning an error.
    """
    return await batch_call(
        steps,
        confirm_write=confirm_write,
        max_chars_per_step=max_chars_per_step,
        store_results=store_results,
        stop_on_error=stop_on_error,
    )


@mcp.resource(
    "workiva-result://{result_id}",
    name="Workiva MCP Result",
    title="Workiva MCP Result Artifact",
    description="Read a full compact-mode result artifact by result_id.",
    mime_type="text/plain",
)
def workiva_result(result_id: str) -> str:
    return read_result(result_id)


@mcp.resource(
    "workiva-result-meta://{result_id}",
    name="Workiva MCP Result Metadata",
    title="Workiva MCP Result Metadata",
    description="Read the metadata sidecar (tool, args, bytes, timestamp) for a stored result.",
    mime_type="application/json",
)
def workiva_result_meta(result_id: str) -> str:
    return json.dumps(read_result_metadata(result_id), indent=2, default=str)


@mcp.resource(
    "workiva-results://index",
    name="Workiva MCP Result Index",
    title="Recent Workiva MCP Result Artifacts",
    description="List recent stored result artifacts (newest first) with their metadata.",
    mime_type="application/json",
)
def workiva_results_index() -> str:
    return json.dumps({"results": list_results(limit=25)}, indent=2, default=str)


@mcp.resource(
    "workiva-receipt://{receipt_id}",
    name="Workiva MCP Execution Receipt",
    title="Workiva MCP Execution Receipt",
    description="Read a redacted immutable mutation receipt by receipt_id.",
    mime_type="application/json",
)
def workiva_receipt(receipt_id: str) -> str:
    return json.dumps(read_receipt(receipt_id), indent=2, default=str)


@mcp.resource(
    "workiva-receipts://index",
    name="Workiva MCP Receipt Index",
    title="Recent Workiva MCP Execution Receipts",
    description="List recent redacted mutation receipts (newest first).",
    mime_type="application/json",
)
def workiva_receipts_index() -> str:
    return json.dumps({"receipts": list_receipts(limit=25)}, indent=2, default=str)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
