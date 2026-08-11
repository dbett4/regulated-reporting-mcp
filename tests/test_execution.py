import json

import pytest

from workiva_mcp.execution import ExecutionState, execute_mutation
from workiva_mcp.tools import cells


def test_sync_success_is_applied_not_verified():
    result = execute_mutation(
        lambda *args, **kwargs: (200, {}, {"ok": True}),
        path="/test",
        method="POST",
        data={"x": 1},
        tool_name="test",
        label="test operation",
        poll=lambda *args, **kwargs: pytest.fail("sync result must not poll"),
    )
    assert result.state is ExecutionState.APPLIED
    assert result.status_code == 200
    assert "readback" in result.detail


def test_202_polls_body_operation_location_before_marking_applied():
    seen = {}

    def poll(location, **kwargs):
        seen["location"] = location
        seen["kwargs"] = kwargs
        return {"status": "completed"}

    result = execute_mutation(
        lambda *args, **kwargs: (202, {}, {"operationLocation": "/operations/123"}),
        path="/test",
        method="POST",
        data={},
        tool_name="test",
        label="mergeCells",
        poll=poll,
    )
    assert result.state is ExecutionState.APPLIED
    assert result.operation_location == "/operations/123"
    assert seen["location"] == "/operations/123"
    assert seen["kwargs"] == {"label": "mergeCells", "tool_name": "test"}


def test_202_without_operation_location_is_indeterminate():
    result = execute_mutation(
        lambda *args, **kwargs: (202, {}, {"accepted": True}),
        path="/test",
        method="POST",
        data={},
        tool_name="test",
        label="mergeCells",
        poll=lambda *args, **kwargs: pytest.fail("missing location must not poll"),
    )
    assert result.state is ExecutionState.INDETERMINATE
    assert result.status_code == 202


def test_poll_failure_is_indeterminate_not_false_success():
    result = execute_mutation(
        lambda *args, **kwargs: (202, {"Operation-Location": "/operations/123"}, {}),
        path="/test",
        method="POST",
        data={},
        tool_name="test",
        label="mergeCells",
        poll=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("timeout")),
    )
    assert result.state is ExecutionState.INDETERMINATE
    assert result.operation_location == "/operations/123"


def test_failed_http_response_is_failed():
    result = execute_mutation(
        lambda *args, **kwargs: (409, {}, {"message": "conflict"}),
        path="/test",
        method="POST",
        data={},
        tool_name="test",
        label="mergeCells",
        poll=lambda *args, **kwargs: pytest.fail("failed response must not poll"),
    )
    assert result.state is ExecutionState.FAILED
    assert result.status_code == 409


@pytest.mark.asyncio
async def test_edit_table_structure_uses_execution_and_preserves_caller_params(monkeypatch):
    captured = {}

    def request(path, **kwargs):
        captured["path"] = path
        captured.update(kwargs)
        return 202, {}, {"operationLocation": "/operations/merge"}

    monkeypatch.setattr(cells, "api_request", request)
    monkeypatch.setattr(cells, "poll_operation", lambda location, **kwargs: {"status": "completed"})
    params = {
        "revision": "caller-keeps-this",
        "range": {"startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 2},
    }

    result = json.loads(await cells.workiva_edit_table_structure("table", "mergeCells", params))
    assert result["state"] == "applied"
    assert result["operation_location"] == "/operations/merge"
    assert params["revision"] == "caller-keeps-this"
    assert "range" in params
    assert captured["data"]["mergeCells"]["ranges"] == [params["range"]]
    assert "revision" not in captured["data"]["mergeCells"]
