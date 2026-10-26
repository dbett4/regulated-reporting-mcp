"""Offline approval simulation with ephemeral keys, journal, and receipts.

This script simulates the reviewer for demonstration only. Real deployments
must keep the private key outside the agent/server environment.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from workiva_mcp.approvals import CorrectionPlan, canonical_plan
from workiva_mcp.reporting_demo import demo_rules


async def run() -> None:
    with TemporaryDirectory(prefix="workiva-approval-demo-") as directory:
        root = Path(directory)
        key = Ed25519PrivateKey.generate()
        public = root / "reviewer.pub"
        public.write_text(key.public_key().public_bytes_raw().hex())
        with patch.dict(
            os.environ,
            {
                "WORKIVA_MCP_MOCK": "1",
                "WORKIVA_MCP_MOCK_POLL_TICKS": "0",
                "WORKIVA_REGION": "us",
                "WORKIVA_APPROVAL_AUDIENCE": "riverton-offline-demo",
                "WORKIVA_REVIEW_PUBLIC_KEY": str(public),
                "WORKIVA_APPROVAL_DB": str(root / "claims.sqlite3"),
                "WORKIVA_MCP_RECEIPT_DIR": str(root / "receipts"),
                "WORKIVA_MCP_RESULT_DIR": str(root / "results"),
            },
        ):
            from workiva_mcp import compact_tools, mock_transport

            mock_transport.reset(rearm_429=False)
            rules = demo_rules()
            target = rules[1]["reported"]
            request = {
                "target": target,
                "new_value": "4195000",
                "reason": "Resolve the synthetic General Fund cash discrepancy",
                "supporting": rules[1]["supporting"],
            }

            async def propose():
                response = json.loads(
                    await compact_tools.call_tool("workiva_preview_correction", request)
                )
                return response["plan"]

            async def execute(plan, signature):
                return json.loads(
                    await compact_tools.call_tool(
                        "workiva_execute_correction",
                        {"plan": plan, "signature": signature},
                        confirm_write=True,
                    )
                )

            def simulated_review(plan):
                return key.sign(canonical_plan(CorrectionPlan.model_validate(plan))).hex()

            plan = await propose()
            print("Synthetic proposal: General Fund cash 4,200,000 -> 4,195,000 USD")
            unsigned = await execute(plan, "")
            assert unsigned["state"] == "blocked"
            print("Unsigned proposal: BLOCKED")

            signature = simulated_review(plan)
            grid = mock_transport._grids[(target["spreadsheet_id"], target["sheet_id"])]
            grid[(3, 1)] = "4200001"  # Simulate an independent operator edit.
            stale = await execute(plan, signature)
            assert stale["state"] == "blocked" and mock_transport.stats["writes"] == 0
            print("Simulated reviewer signature + intervening operator edit: BLOCKED, zero writes")

            fresh = await propose()
            fresh_signature = simulated_review(fresh)
            result = await execute(fresh, fresh_signature)
            assert result["state"] == "verified" and grid[(3, 1)] == "4195000"
            print("New proposal + new simulated reviewer signature: VERIFIED")
            replay = await execute(fresh, fresh_signature)
            assert replay["state"] == "blocked" and mock_transport.stats["writes"] == 1
            print("Replayed approval: BLOCKED")

            report = json.loads(
                await compact_tools.call_tool(
                    "workiva_check_tie_outs",
                    {"rules": rules},
                    max_chars=100000,
                    store_over_chars=100000,
                )
            )
            assert report["summary"] == {"passed": 2, "failed": 1, "not_evaluated": 1}
            print(
                "Tie-out rerun: cash now passes; the unrelated rollforward and missing support remain open"
            )
            print(
                "APPROVAL_PASS unsigned=blocked stale=blocked corrected=verified replay=blocked writes=1"
            )

            # A separate scenario: unwind as if the process disappeared after
            # receiving 202. Unit tests also exercise real os._exit subprocesses.
            from workiva_mcp.tools import corrections

            class SimulatedProcessExit(BaseException):
                pass

            mock_transport.reset(rearm_429=False)
            interrupted = await propose()
            interrupted_signature = simulated_review(interrupted)
            try:
                with patch.object(corrections, "poll_operation", side_effect=SimulatedProcessExit):
                    await execute(interrupted, interrupted_signature)
            except SimulatedProcessExit:
                print("Recovery scenario: simulated process interruption after 202 acceptance")
            else:
                raise AssertionError("Expected interruption before polling")

            listing = json.loads(
                await compact_tools.call_tool("workiva_list_correction_attempts", {"limit": 1})
            )
            attempt = listing["attempts"][0]
            assert attempt["plan_id"] == interrupted["plan_id"]
            observed = json.loads(
                await compact_tools.call_tool(
                    "workiva_inspect_correction",
                    {
                        "plan_id": attempt["plan_id"],
                    },
                )
            )
            assert observed["execution_state"] == "indeterminate"
            assert observed["execution_phase"] == "polling" and observed["operation_location"]
            assert observed["evidence"][0]["classification"] == "matches_approved"
            assert observed["retry_allowed"] is False
            assert (await execute(interrupted, interrupted_signature))["state"] == "blocked"
            assert mock_transport.stats["writes"] == 1
            print("Recovery: target matches approved value; original attempt remains indeterminate")
            print(
                "RECOVERY_PASS plan=retained operation=retained original=indeterminate replay=blocked additional_writes=0"
            )


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
