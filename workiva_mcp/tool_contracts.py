"""Reviewed policy contracts for every registered Workiva MCP tool.

The compact dispatcher treats this manifest as the runtime authority.  Tool-name
classification remains a diagnostic and manifest-generation aid only; it does
not authorize a call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Effect = Literal["read", "write", "destructive", "external"]
Confirmation = Literal["none", "write", "destructive", "external"]
Proof = Literal["none", "receipt", "readback"]

_MANIFEST_PATH = Path(__file__).with_name("tool_contract_manifest.json")


@dataclass(frozen=True, slots=True)
class ToolContract:
    """Static execution policy for a registered Workiva MCP tool."""

    effect: Effect
    confirmation: Confirmation
    proof: Proof

    @property
    def requires_confirmation(self) -> bool:
        return self.confirmation != "none"


def _load_manifest() -> dict[str, ToolContract]:
    raw = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Tool contract manifest must be a JSON object")
    contracts: dict[str, ToolContract] = {}
    for name, item in raw.items():
        if not isinstance(name, str) or not isinstance(item, dict):
            raise ValueError("Tool contract manifest entries must be name/object pairs")
        effect = item.get("effect")
        confirmation = item.get("confirmation")
        proof = item.get("proof")
        if effect not in {"read", "write", "destructive", "external"}:
            raise ValueError(f"Invalid effect for {name}: {effect!r}")
        if confirmation not in {"none", "write", "destructive", "external"}:
            raise ValueError(f"Invalid confirmation for {name}: {confirmation!r}")
        if proof not in {"none", "receipt", "readback"}:
            raise ValueError(f"Invalid proof for {name}: {proof!r}")
        if effect == "read" and (confirmation != "none" or proof != "none"):
            raise ValueError(f"Read contract {name} must require no confirmation/proof")
        if effect != "read" and confirmation == "none":
            raise ValueError(f"Mutating contract {name} must require confirmation")
        if effect != "read" and proof == "none":
            raise ValueError(f"Mutating contract {name} must require receipt or readback proof")
        contracts[name] = ToolContract(effect=effect, confirmation=confirmation, proof=proof)
    return contracts


CONTRACTS = _load_manifest()


def get_contract(name: str) -> ToolContract | None:
    """Return the reviewed policy contract for a registered tool."""
    return CONTRACTS.get(name)


def manifest_tool_names() -> set[str]:
    """Return a copy-safe view of manifest coverage for tests and CI checks."""
    return set(CONTRACTS)
