"""
Entry point for the MCP server: python -m workiva_mcp
"""

import os


def _load_mcp():
    mode = os.getenv("WORKIVA_MCP_CATALOG_MODE", "full").strip().lower()
    if mode in {"compact", "lean", "search"}:
        from workiva_mcp.compact_server import mcp
    else:
        from workiva_mcp.server import mcp
    return mcp


def main():
    _load_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
