"""Guarded entry point for ``python -m workiva_mcp`` and ``workiva-mcp``."""

import os

_FULL_CATALOG_OPT_IN = "WORKIVA_MCP_ALLOW_UNGATED_FULL_CATALOG"


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _load_mcp():
    mode = os.getenv("WORKIVA_MCP_CATALOG_MODE", "compact").strip().lower()
    if mode in {"compact", "lean", "search"}:
        from workiva_mcp.compact_server import mcp
    elif mode == "full":
        if not _enabled(_FULL_CATALOG_OPT_IN):
            raise RuntimeError(
                "Refusing to expose the raw full catalog: it bypasses compact-dispatch "
                "confirmation and receipt enforcement. Use compact mode (the default), or "
                f"set {_FULL_CATALOG_OPT_IN}=1 only for an isolated trusted client."
            )
        from workiva_mcp.server import mcp
    else:
        raise ValueError(
            "WORKIVA_MCP_CATALOG_MODE must be compact (recommended) or full (unsafe opt-in)"
        )
    return mcp


def main():
    _load_mcp().run(transport="stdio")


if __name__ == "__main__":
    main()
