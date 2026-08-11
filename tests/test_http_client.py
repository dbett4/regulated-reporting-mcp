"""Unit tests for workiva_mcp.http_client (pagination, auth refresh, rate limits)."""

from __future__ import annotations

import json

import pytest

from workiva_mcp import http_client
from workiva_mcp.http_client import WorkivaAPIError, _error, _parse_raw_body


def test_error_json_shape():
    payload = json.loads(_error(503, {}, cause="deferred feature"))
    assert payload["error"] is True
    assert payload["status"] == 503
    assert payload["cause"] == "deferred feature"


def test_workiva_api_error_includes_status():
    err = WorkivaAPIError(429, "too many requests", {"error": "slow down"})
    assert err.status == 429
    assert err.body == {"error": "slow down"}
    assert "429" in str(err)


@pytest.mark.parametrize(
    "payload",
    [
        b"PK\x03\x04binary-xlsx",
        b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nbinary-pdf",
        b")]}'binary-export",
    ],
)
def test_parse_raw_body_preserves_binary_export_bytes(payload):
    assert _parse_raw_body(payload, raw_on_pk_prefix=True) is payload


def test_parse_raw_body_keeps_json_behavior_for_operation_envelopes():
    assert _parse_raw_body(b'{"status":"completed"}', raw_on_pk_prefix=True) == {
        "status": "completed"
    }


def test_pagination_follows_next_link(monkeypatch):
    calls: list[str] = []

    def fake_api_request(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            return 200, {}, {
                "data": [{"id": "a"}],
                "@nextLink": "https://api.workiva.com/content/page2",
            }
        return 200, {}, {"data": [{"id": "b"}]}

    monkeypatch.setattr(http_client, "api_request", fake_api_request)
    items = http_client.paginate("/spreadsheets", tool_name="test")
    assert items == [{"id": "a"}, {"id": "b"}]
    assert len(calls) == 2


def test_pagination_first_page_failure_raises(monkeypatch):
    def fake_api_request(path, **kwargs):
        return 500, {}, {"error": "boom"}

    monkeypatch.setattr(http_client, "api_request", fake_api_request)
    with pytest.raises(WorkivaAPIError) as exc:
        http_client.paginate("/spreadsheets")
    assert exc.value.status == 500


def test_429_retries_with_backoff(monkeypatch):
    calls = {"n": 0}

    def fake_raw(url, method="GET", headers=None, data=None, timeout=60):
        calls["n"] += 1
        if calls["n"] == 1:
            return 429, {}, {"error": "rate limited"}
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http_client, "_raw_http", fake_raw)
    monkeypatch.setattr(http_client, "ensure_token", lambda: "token")
    monkeypatch.setattr(http_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(http_client.app_state, "log_operation", lambda *a, **k: None)

    status, _, body = http_client.api_request("/spreadsheets", api="content", tool_name="t")
    assert status == 200
    assert body == {"ok": True}
    assert calls["n"] == 2


def test_401_clears_token_and_retries_once(monkeypatch):
    cleared = {"v": False}
    token_calls = {"n": 0}
    raw_calls = {"n": 0}

    def fake_clear():
        cleared["v"] = True

    def fake_ensure():
        token_calls["n"] += 1
        return f"token-{token_calls['n']}"

    def fake_raw(url, method="GET", headers=None, data=None, timeout=60):
        raw_calls["n"] += 1
        if raw_calls["n"] == 1:
            return 401, {}, {"error": "expired"}
        return 200, {}, {"ok": True}

    monkeypatch.setattr(http_client, "clear_token", fake_clear)
    monkeypatch.setattr(http_client, "ensure_token", fake_ensure)
    monkeypatch.setattr(http_client, "_raw_http", fake_raw)
    monkeypatch.setattr(http_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(http_client.app_state, "log_operation", lambda *a, **k: None)

    status, _, body = http_client.api_request("/spreadsheets", api="content")
    assert cleared["v"] is True
    assert status == 200
    assert body == {"ok": True}
    assert raw_calls["n"] == 2
