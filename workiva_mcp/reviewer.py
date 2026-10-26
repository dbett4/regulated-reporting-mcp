"""Human-operated, offline Ed25519 review CLI. Never registered with MCP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workiva_mcp._io import atomic_create_text
from workiva_mcp.approvals import CorrectionPlan, canonical_plan, check_fresh, plan_digest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser(
        "keygen", help="Generate a reviewer-only private key and server public key"
    )
    keygen.add_argument("--private-key", type=Path, required=True)
    keygen.add_argument("--public-key", type=Path, required=True)
    approve = sub.add_parser("approve", help="Display and sign one exact proposal, offline")
    approve.add_argument("proposal", type=Path)
    approve.add_argument("--private-key", type=Path, required=True)
    approve.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "keygen":
            if args.private_key.exists() or args.public_key.exists():
                raise ValueError("Refusing to overwrite an existing key")
            key = Ed25519PrivateKey.generate()
            atomic_create_text(args.private_key, key.private_bytes_raw().hex() + "\n")
            atomic_create_text(args.public_key, key.public_key().public_bytes_raw().hex() + "\n")
            print("Keys created. Keep the private key outside the agent/server environment.")
            return

        document = json.loads(args.proposal.read_text())
        if not isinstance(document, dict):
            raise ValueError("Proposal must be a JSON object")
        plan = CorrectionPlan.model_validate(document.get("plan", document))
        check_fresh(plan)
        digest = plan_digest(plan)
        # JSON escaping prevents untrusted reasons/values from injecting terminal controls.
        print(json.dumps(plan.model_dump(), indent=2, ensure_ascii=True))
        print(
            "One literal cell correction. Check audience, target, before/after, supporting cells and expiry."
        )
        print("This signs the displayed plan; it does not establish the accounting justification.")
        if input(f"Type APPROVE {digest} to sign: ").strip() != f"APPROVE {digest}":
            raise ValueError("Approval canceled; no signature written")
        check_fresh(plan)
        key = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(args.private_key.read_text().strip())
        )
        envelope = {"plan": plan.model_dump(), "signature": key.sign(canonical_plan(plan)).hex()}
        atomic_create_text(args.output, json.dumps(envelope, indent=2) + "\n")
        print(f"Signed approval written to {args.output}")
    except (ValueError, OSError, EOFError) as exc:
        parser.exit(1, f"Review failed: {exc}\n")


if __name__ == "__main__":
    main()
