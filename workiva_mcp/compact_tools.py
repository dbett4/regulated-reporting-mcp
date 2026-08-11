"""Compact MCP catalog helpers.

The compact server exposes search + dispatch tools, then reaches the full-catalog
tool implementations on demand. This keeps the always-on MCP catalog small
(3 tools) while preserving the full implementation surface.

Discovery uses the *live* FastMCP registry of ``workiva_mcp.server`` as the
single source of truth: the compact catalog reflects exactly the tools the full
server registers, with their real input schemas — so the two surfaces cannot
drift. If the FastMCP internals are ever unavailable, discovery degrades to an
import-free AST scan of the modules ``server.py`` actually imports.
"""

from __future__ import annotations

import ast
import inspect
import json
import warnings
from collections import Counter
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Callable, Optional

from workiva_mcp.receipt_store import store_receipt
from workiva_mcp.result_store import store_result
from workiva_mcp.tool_contracts import ToolContract, get_contract

# ── Risk classification ──────────────────────────────────────────────────────
#
# Full-catalog tools publish no MCP annotations, so risk is inferred from the tool's
# *leading* verb (the first meaningful token after the namespace), not from any
# incidental token in the name. Leading-verb classification cannot be fooled by
# a write tool that merely contains a safe word (e.g. a future
# ``workiva_get_then_delete``), which the older any-token heuristic would have
# wrongly waved through. The gate stays deterministic and conservative: anything
# not provably read-only requires ``confirm_write``.

READ_VERBS = {
    "list", "get", "read", "describe", "validate", "search", "find",
    "preview", "render", "check", "status", "fetch",
}
DESTRUCTIVE_VERBS = {"delete", "remove", "clear", "drop", "purge", "reset", "truncate", "wipe", "unlink"}
EXTERNAL_VERBS = {"send", "publish", "notify", "post"}

SAFE_WORKIVA_VERBS = READ_VERBS  # retained for backward-compatible imports

COMPACT_TOOL_NAMES = ["workiva_search_tools", "workiva_call_tool", "workiva_batch_call"]

_TOOL_CACHE: Optional[dict[str, "ToolDefinition"]] = None
_DISCOVERY_SOURCE: Optional[str] = None


@dataclass(slots=True)
class RiskClass:
    level: str  # read | write | destructive | external
    requires_confirmation: bool
    reason: str


@dataclass(slots=True)
class ToolDefinition:
    name: str
    module_name: str
    family: str
    description: str
    parameters: list[dict[str, Any]]
    requires_confirmation: bool
    risk: str = "write"
    reason: str = ""
    contract: ToolContract | None = None
    func: Optional[Callable[..., Any]] = field(default=None, repr=False)
    input_schema: Optional[dict[str, Any]] = field(default=None, repr=False)


def _effective_verb(name: str) -> str:
    """Return the leading action verb, skipping the ``workiva``/``wdata`` namespace."""
    tokens = name.split("_")
    if not tokens:
        return ""
    head = tokens[0]
    rest = tokens[1:]
    if head == "workiva" and rest and rest[0] == "wdata":
        rest = rest[1:]
    return rest[0] if rest else head


def classify_tool(name: str) -> RiskClass:
    """Deterministically classify a tool's mutation risk from its name."""
    clean = (name or "").strip()
    tokens = clean.split("_")
    verb = _effective_verb(clean)

    if verb in EXTERNAL_VERBS or "send" in tokens:
        return RiskClass("external", True, f"May contact an external system (verb '{verb}'); confirm_write required.")
    if verb in DESTRUCTIVE_VERBS:
        return RiskClass("destructive", True, f"Destructive verb '{verb}' can delete or reset state; confirm_write required.")
    if verb in READ_VERBS:
        return RiskClass("read", False, f"Read-only verb '{verb}'.")
    if verb == "export":
        if "_to_" in clean:
            return RiskClass("write", True, "Exports into a destination (writes state); confirm_write required.")
        return RiskClass("read", False, "Read-only export (data out).")
    return RiskClass("write", True, f"May modify Workiva/PM state (verb '{verb}'); confirm_write required.")


def _contract_reason(name: str, contract: ToolContract | None, diagnostic: RiskClass) -> str:
    """Render the reviewed contract; name inference is only drift diagnostics."""
    if contract is None:
        return f"No reviewed contract. Name classifier suggests {diagnostic.level!r}; dispatch is blocked."
    return (
        f"Reviewed contract: {contract.effect} effect; "
        f"{contract.confirmation} confirmation; {contract.proof} proof required."
    )


# ── Discovery ────────────────────────────────────────────────────────────────


def get_tool_definitions() -> dict[str, ToolDefinition]:
    """Return discovered full-catalog MCP tools keyed by function name."""
    global _TOOL_CACHE
    if _TOOL_CACHE is None:
        _TOOL_CACHE = _discover_tools()
    return _TOOL_CACHE


def discovery_source() -> str:
    """Return how the active catalog was discovered: 'registry' or 'ast'."""
    get_tool_definitions()
    return _DISCOVERY_SOURCE or "uninitialized"


def _discover_tools() -> dict[str, ToolDefinition]:
    global _DISCOVERY_SOURCE
    try:
        definitions = _discover_from_registry()
        if definitions:
            _DISCOVERY_SOURCE = "registry"
            return definitions
    except Exception:  # pragma: no cover - defensive fallback if FastMCP internals move
        pass
    _DISCOVERY_SOURCE = "ast"
    return _discover_from_ast()


def _discover_from_registry() -> dict[str, ToolDefinition]:
    """Read the live FastMCP registry — the single source of truth.

    Restricted to tools defined in the modules ``server.py`` actually imports.
    FastMCP's registry is process-global mutable state: anything that imports a
    tool module (a stray import, a test) registers onto the same singleton. The
    full default server's catalog is defined by ``server.py``'s import list, so
    the compact catalog mirrors exactly that and ignores tools registered from
    modules the full server never loads (e.g. ``tools.capture``).
    """
    import_module("workiva_mcp.server")
    from workiva_mcp.server import mcp as full_server

    allowed_modules = {f"workiva_mcp.tools.{stem}" for stem in _registered_module_names()}
    tools = full_server._tool_manager.list_tools()
    definitions: dict[str, ToolDefinition] = {}
    for tool in tools:
        name = tool.name
        func = getattr(tool, "fn", None)
        module_name = getattr(func, "__module__", "") or ""
        if allowed_modules and module_name not in allowed_modules:
            continue
        schema = getattr(tool, "parameters", None) or {}
        contract = get_contract(name)
        diagnostic_risk = classify_tool(name)
        definitions[name] = ToolDefinition(
            name=name,
            module_name=module_name,
            family=_family_of(module_name),
            description=_clean_description(tool.description or ""),
            parameters=_params_from_schema(schema),
            requires_confirmation=contract.requires_confirmation if contract else True,
            risk=contract.effect if contract else "write",
            reason=_contract_reason(name, contract, diagnostic_risk),
            contract=contract,
            func=func,
            input_schema=schema or None,
        )
    return dict(sorted(definitions.items()))


def _discover_from_ast() -> dict[str, ToolDefinition]:
    """Import-free fallback: scan the modules server.py imports for @mcp.tool."""
    root = Path(__file__).resolve().parent
    tools_dir = root / "tools"
    definitions: dict[str, ToolDefinition] = {}

    for module_stem in _registered_module_names():
        module_name = f"workiva_mcp.tools.{module_stem}"
        path = tools_dir / f"{module_stem}.py"
        if not path.exists():
            continue
        for item in _decorated_functions(path):
            name = item["name"]
            contract = get_contract(name)
            diagnostic_risk = classify_tool(name)
            definitions[name] = ToolDefinition(
                name=name,
                module_name=module_name,
                family=module_stem,
                description=_clean_description(item["docstring"]),
                parameters=item["parameters"],
                requires_confirmation=contract.requires_confirmation if contract else True,
                risk=contract.effect if contract else "write",
                reason=_contract_reason(name, contract, diagnostic_risk),
                contract=contract,
            )
    return dict(sorted(definitions.items()))


def _registered_module_names() -> tuple[str, ...]:
    """Tool module names server.py imports — the authoritative module list."""
    server_path = Path(__file__).resolve().parent / "server.py"
    try:
        tree = ast.parse(server_path.read_text(encoding="utf-8"), filename=str(server_path))
    except (OSError, SyntaxError):  # pragma: no cover - server.py always parses
        return ()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "workiva_mcp.tools":
            names.extend(alias.name for alias in node.names)
    return tuple(dict.fromkeys(names))


# ── Catalog stats and benchmark ──────────────────────────────────────────────


def catalog_stats() -> dict[str, Any]:
    definitions = get_tool_definitions()
    full_count = len(definitions)
    compact_count = len(COMPACT_TOOL_NAMES)
    reduction = 0.0 if full_count == 0 else round((1 - compact_count / full_count) * 100, 1)
    risk_breakdown = Counter(definition.risk for definition in definitions.values())
    return {
        "full_catalog_tools": full_count,
        "compact_catalog_tools": compact_count,
        "tool_count_reduction_pct": reduction,
        "compact_tools": list(COMPACT_TOOL_NAMES),
        "discovery_source": _DISCOVERY_SOURCE or "uninitialized",
        "risk_breakdown": dict(sorted(risk_breakdown.items())),
    }


def catalog_benchmark() -> dict[str, Any]:
    """Measure the real tools/list payload: full catalog vs compact catalog.

    The headline win is context bytes/tokens sent to every client on connect,
    not just the tool count. Imports both servers; intended for proof/benchmark
    contexts, not the hot path.
    """
    import asyncio

    from workiva_mcp.compact_server import mcp as compact_server
    from workiva_mcp.server import mcp as full_server

    def _payload_bytes(tools: list[Any]) -> int:
        rendered = [tool.model_dump(mode="json", exclude_none=True) for tool in tools]
        return len(json.dumps(rendered).encode("utf-8"))

    full_tools = asyncio.run(full_server.list_tools())
    compact_tools = asyncio.run(compact_server.list_tools())
    full_bytes = _payload_bytes(full_tools)
    compact_bytes = _payload_bytes(compact_tools)
    reduction = round((1 - compact_bytes / full_bytes) * 100, 1) if full_bytes else 0.0
    return {
        "full_tools": len(full_tools),
        "compact_tools": len(compact_tools),
        "full_listing_bytes": full_bytes,
        "compact_listing_bytes": compact_bytes,
        "listing_byte_reduction_pct": reduction,
        "approx_full_tokens": full_bytes // 4,
        "approx_compact_tokens": compact_bytes // 4,
    }


# ── Search ───────────────────────────────────────────────────────────────────


def search_tools(
    query: str = "",
    family: str | None = None,
    detail: str = "summary",
    limit: int = 20,
) -> str:
    """Search Workiva tools without exposing them all up front."""
    detail = (detail or "summary").strip().lower()
    if detail == "stats":
        return json.dumps(catalog_stats(), indent=2)
    if detail not in {"name", "summary", "schema"}:
        return json.dumps({"error": "detail must be one of: name, summary, schema, stats"})

    limit = max(1, min(int(limit or 20), 100))
    family_filter = (family or "").strip().lower()
    query_tokens = [part for part in (query or "").lower().replace("_", " ").split() if part]

    matches: list[tuple[int, ToolDefinition]] = []
    for definition in get_tool_definitions().values():
        if family_filter and family_filter not in {
            definition.family.lower(),
            definition.module_name.rsplit(".", 1)[-1].lower(),
        }:
            continue
        score = _score(definition, query_tokens)
        if query_tokens and score == 0:
            continue
        matches.append((score, definition))

    matches.sort(key=lambda item: (-item[0], item[1].name))
    tools = [_render_tool(definition, detail) for _, definition in matches[:limit]]
    return json.dumps(
        {
            "count": len(matches),
            "returned": len(tools),
            "detail": detail,
            "tools": tools,
        },
        indent=2,
    )


# ── Dispatch ─────────────────────────────────────────────────────────────────


async def call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    *,
    confirm_write: bool = False,
    confirm_destructive: bool = False,
    confirm_external: bool = False,
    max_chars: int = 8000,
    store_result_artifact: bool = False,
    store_over_chars: int = 12000,
) -> str:
    """Call a full-catalog tool by name after optional write confirmation."""
    definitions = get_tool_definitions()
    definition = definitions.get((name or "").strip())
    if definition is None:
        return json.dumps({
            "error": f"Unknown tool: {name}",
            "hint": "Use workiva_search_tools to find the exact tool name.",
        })
    contract = definition.contract
    if contract is None:
        return json.dumps({
            "error": f"Tool lacks a reviewed execution contract: {definition.name}",
            "policy_blocked": True,
            "tool": definition.name,
            "hint": "Add a valid tool contract before enabling dispatch.",
        })
    confirmed = {
        "none": True,
        "write": confirm_write,
        "destructive": confirm_destructive,
        "external": confirm_external,
    }[contract.confirmation]
    if not confirmed:
        return json.dumps({
            "error": f"Tool requires confirmation: {definition.name}",
            "requires_confirmation": True,
            "tool": definition.name,
            "risk": contract.effect,
            "confirmation": contract.confirmation,
            "reason": definition.reason,
        })

    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        return json.dumps({"error": "arguments must be a JSON object"})

    try:
        func = _load_callable(definition)
        _validate_arguments(func, arguments)
        result = func(**arguments)
        if inspect.isawaitable(result):
            result = await result
        rendered = _serialize_result(
            result,
            tool_name=definition.name,
            arguments=arguments,
            max_chars=max_chars,
            store_result_artifact=store_result_artifact,
            store_over_chars=store_over_chars,
        )
        if contract.effect == "read":
            return rendered
        parsed = _maybe_json(rendered)
        state = _mutation_state(parsed)
        receipt = store_receipt(
            tool=definition.name,
            contract={"effect": contract.effect, "confirmation": contract.confirmation, "proof": contract.proof},
            arguments=arguments,
            state=state,
            result=parsed if parsed is not None else rendered,
        )
        if isinstance(parsed, dict):
            parsed.setdefault("state", state)
            parsed["receipt_uri"] = receipt["uri"]
            return json.dumps(parsed, default=str)
        return json.dumps({"state": state, "receipt_uri": receipt["uri"], "result": rendered}, default=str)
    except Exception as exc:  # pragma: no cover - defensive boundary for MCP clients
        return json.dumps({
            "error": str(exc),
            "type": type(exc).__name__,
            "tool": definition.name,
        })


def _mutation_state(parsed: Any) -> str:
    """Preserve tool-specific readback evidence without inventing verification."""
    if not isinstance(parsed, dict):
        return "applied_unverified"
    if parsed.get("error"):
        return "failed"
    state = str(parsed.get("state", "")).strip().lower()
    if state in {"verified", "verification_failed", "failed", "indeterminate", "applied"}:
        return "applied_unverified" if state == "applied" else state
    status = str(parsed.get("status", "")).strip().lower()
    if status == "verified":
        return "verified"
    if status == "mismatch":
        return "verification_failed"
    return "applied_unverified"


async def batch_call(
    steps: list[dict[str, Any]],
    *,
    confirm_write: bool = False,
    max_chars_per_step: int = 2000,
    store_results: bool = True,
    stop_on_error: bool = True,
) -> str:
    """Run multiple full-catalog tools in one compact MCP call.

    Each step is gated independently by its own risk class, so a single
    ``confirm_write`` does not blindly authorize unrelated destructive steps —
    the caller still sees per-step risk in the result, and read-only steps never
    needed confirmation in the first place.
    """
    if not isinstance(steps, list):
        return json.dumps({"error": "steps must be a list"})
    outputs: list[dict[str, Any]] = []
    definitions = get_tool_definitions()
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            outputs.append({"index": index, "error": "step must be an object"})
            if stop_on_error:
                break
            continue

        name = str(step.get("name", "")).strip()
        arguments = step.get("arguments") or {}
        definition = definitions.get(name)
        if definition is not None and definition.contract is not None and definition.contract.confirmation in {"destructive", "external"}:
            outputs.append({
                "index": index,
                "tool": name,
                "risk": definition.contract.effect,
                "error": "Destructive and external tools are not allowed in compact batches until plan-digest approval is implemented.",
                "policy_blocked": True,
            })
            if stop_on_error:
                break
            continue
        text = await call_tool(
            name,
            arguments,
            confirm_write=confirm_write,
            max_chars=max_chars_per_step,
            store_result_artifact=store_results,
            store_over_chars=0 if store_results else max_chars_per_step + 1,
        )
        parsed = _maybe_json(text)
        output = {
            "index": index,
            "tool": name,
            "risk": definition.risk if definition else "unknown",
            "result": parsed if parsed is not None else text,
        }
        outputs.append(output)
        if stop_on_error and isinstance(parsed, dict) and parsed.get("error"):
            break
    return json.dumps({"count": len(outputs), "results": outputs}, indent=2)


# ── AST fallback helpers ─────────────────────────────────────────────────────


def _decorated_functions(path: Path) -> list[dict[str, Any]]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    functions: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            continue
        functions.append({
            "name": node.name,
            "docstring": ast.get_docstring(node) or "",
            "parameters": _params_from_ast(node),
        })
    return functions


def _is_mcp_tool_decorator(decorator: ast.AST) -> bool:
    try:
        text = ast.unparse(decorator)
    except Exception:  # pragma: no cover - ast.unparse is always available here
        return False
    return "mcp.tool" in text


def _params_from_ast(node: ast.AST) -> list[dict[str, Any]]:
    args = node.args
    params: list[dict[str, Any]] = []
    positional = list(args.posonlyargs) + list(args.args)
    defaults = list(args.defaults)
    default_offset = len(positional) - len(defaults)
    for idx, arg in enumerate(positional):
        if arg.arg in {"self", "cls"}:
            continue
        has_default = idx >= default_offset
        default_node = defaults[idx - default_offset] if has_default else None
        params.append({
            "name": arg.arg,
            "required": not has_default,
            "default": _literal_default(default_node),
            "annotation": _unparse_annotation(arg.annotation),
            "description": "",
        })
    for arg, default_node in zip(args.kwonlyargs, args.kw_defaults):
        if arg.arg in {"self", "cls"}:
            continue
        params.append({
            "name": arg.arg,
            "required": default_node is None,
            "default": _literal_default(default_node),
            "annotation": _unparse_annotation(arg.annotation),
            "description": "",
        })
    return params


def _literal_default(node: ast.AST | None) -> Any:
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        try:
            return ast.unparse(node)
        except Exception:  # pragma: no cover
            return None


def _unparse_annotation(node: ast.AST | None) -> str:
    if node is None:
        return "Any"
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return "Any"


def _load_callable(definition: ToolDefinition):
    if definition.func is not None:
        return definition.func
    return _load_callable_by_name(definition.module_name, definition.name)


def _load_callable_by_name(module_name: str, name: str):
    # Importing the full server lets the existing @mcp.tool decorators and
    # module-level setup run exactly as they do in full-catalog mode.
    import_module("workiva_mcp.server")
    module = import_module(module_name)
    return getattr(module, name)


def _validate_arguments(func, arguments: dict[str, Any]) -> None:
    signature = inspect.signature(func)
    signature.bind(**arguments)


# ── Schema / rendering helpers ───────────────────────────────────────────────


def _family_of(module_name: str) -> str:
    if not module_name:
        return "unknown"
    return module_name.rsplit(".", 1)[-1]


def _params_from_schema(schema: dict[str, Any]) -> list[dict[str, Any]]:
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = set(schema.get("required", []) if isinstance(schema, dict) else [])
    params: list[dict[str, Any]] = []
    for pname, pdef in properties.items():
        pdef = pdef if isinstance(pdef, dict) else {}
        params.append({
            "name": pname,
            "required": pname in required,
            "default": pdef.get("default"),
            "annotation": _schema_type(pdef),
            "description": (pdef.get("description") or "")[:200],
        })
    return params


def _schema_type(pdef: dict[str, Any]) -> str:
    if "type" in pdef:
        type_name = pdef["type"]
        if type_name == "array":
            item_type = (pdef.get("items") or {}).get("type")
            return f"array[{item_type}]" if item_type else "array"
        return str(type_name)
    if "anyOf" in pdef:
        types = [opt.get("type") for opt in pdef["anyOf"] if isinstance(opt, dict) and opt.get("type")]
        non_null = [t for t in types if t and t != "null"]
        if non_null:
            return "|".join(non_null)
    return "any"


def _clean_description(docstring: str) -> str:
    lines = [line.strip() for line in (docstring or "").strip().splitlines()]
    body: list[str] = []
    for line in lines:
        if not line or line.lower() in {"args:", "arguments:", "returns:"}:
            break
        body.append(line)
    return " ".join(body)[:500]


def _score(definition: ToolDefinition, query_tokens: list[str]) -> int:
    if not query_tokens:
        return 1
    haystack = " ".join([
        definition.name.replace("_", " "),
        definition.family,
        definition.description,
        " ".join(param["name"] for param in definition.parameters),
        " ".join(str(param.get("description", "")) for param in definition.parameters),
    ]).lower()
    score = 0
    for token in query_tokens:
        if token in haystack:
            score += haystack.count(token)
        if token in definition.name.lower():
            score += 3
        if token == definition.family.lower():
            score += 5
    if all(token in haystack for token in query_tokens):
        score += 10
    return score


def _render_tool(definition: ToolDefinition, detail: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": definition.name,
        "family": definition.family,
        "risk": definition.risk,
        "requires_confirmation": definition.requires_confirmation,
    }
    if detail in {"summary", "schema"}:
        base["description"] = definition.description
        base["args"] = [param["name"] for param in definition.parameters]
        if definition.requires_confirmation:
            base["confirm_note"] = definition.reason
    if detail == "schema":
        base["module"] = definition.module_name
        base["parameters"] = definition.parameters
        if definition.input_schema:
            base["input_schema"] = definition.input_schema
    return base


def _serialize_result(
    result: Any,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    max_chars: int,
    store_result_artifact: bool,
    store_over_chars: int,
) -> str:
    max_chars = max(500, min(int(max_chars or 8000), 50000))
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, default=str, indent=2)
    should_store = (
        store_result_artifact
        or len(text) > max_chars
        or len(text) > max(0, int(store_over_chars or 0))
    )
    if should_store:
        artifact = store_result(
            tool_name,
            text,
            {
                "arguments": arguments,
                "preview_chars": min(len(text), max_chars),
            },
        )
        return json.dumps({
            "stored": True,
            "result_id": artifact["result_id"],
            "uri": artifact["uri"],
            "path": artifact["path"],
            "bytes": artifact["bytes"],
            "preview": text[:max_chars],
            "truncated": len(text) > max_chars,
        }, indent=2)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... [truncated]"


def _maybe_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None
