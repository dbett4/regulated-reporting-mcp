"""Raw FastMCP tool-registration hub.

The compact dispatcher imports this registry to discover implementations. Directly
serving this object exposes the raw tools and bypasses compact-dispatch confirmation,
receipt, and result-artifact enforcement. The package entry point therefore defaults
to the compact server and requires an explicit unsafe opt-in for this surface.
"""

import logging
import sys
import weakref

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv()

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ── Session-scoped state ─────────────────────────────────────────────────────
#
# The MCP server dispatches every incoming message as its own anyio task
# (mcp/server/lowlevel/server.py:678). Per Python contextvars semantics, child
# tasks inherit a *copy* of the parent's Context — mutations don't propagate
# across tool calls and don't propagate back to siblings. A naive
# `ContextVar.set()` in a session-scoping tool would be discarded the moment
# that tool's task ended, making the whole "scope a session, stop re-passing
# the context" idiom silently a no-op.
#
# Instead, key session-scoped state on the MCP `ServerSession` object — same
# instance for every tool call within one MCP connection — held in a
# WeakKeyDictionary so closing the session lets the entry GC.

_session_scopes: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()

# Module-level fallback for callers running outside an MCP context (direct
# function calls in tests and scripts). When no session is active, set/get
# fall back to this slot.
_fallback_scope: str | None = None


def _active_session():
    """Return the current MCP ServerSession, or None if not in a tool call."""
    try:
        from mcp.server.lowlevel.server import request_ctx
    except ImportError:  # pragma: no cover — mcp always installed when server runs
        return None
    try:
        return request_ctx.get().session
    except LookupError:
        return None


def get_session_scope() -> str | None:
    """Return the scope slug bound to the current MCP session.

    Falls back to a module-level slot when called outside an MCP tool call.
    Returns None when neither is set.
    """
    sess = _active_session()
    if sess is not None:
        return _session_scopes.get(sess)
    return _fallback_scope


def set_session_scope(slug: str | None) -> None:
    """Set (or clear, when `slug` is None) the scope for the current MCP
    session. Outside an MCP tool call, writes the module-level fallback slot."""
    global _fallback_scope
    sess = _active_session()
    if sess is not None:
        if slug is None:
            _session_scopes.pop(sess, None)
        else:
            _session_scopes[sess] = slug
        return
    _fallback_scope = slug


mcp = FastMCP(
    "workiva-mcp",
    instructions=(
        "Raw Workiva implementation catalog for trusted development only. "
        "Direct calls on this surface bypass the compact dispatcher's confirmation "
        "and receipt middleware. Use workiva-mcp in its default compact mode for "
        "policy-enforced operation."
    ),
)

# Tool modules — each imports `mcp` from this file and registers tools via @mcp.tool()
from workiva_mcp.tools import (  # noqa: F401
    admin,
    cells,
    chains,
    content,
    documents,
    files,
    linking,
    presentations,
    spreadsheets,
    tasks,
    wdata,
)
