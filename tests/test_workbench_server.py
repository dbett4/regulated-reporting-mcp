"""HTTP integration and browser-origin boundaries on a disposable loopback server."""

import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from workiva_mcp.workbench_server import create_server


@pytest.fixture(name="http_request")
def request_fixture(tmp_path):
    server = create_server(tmp_path / "http-workspace", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def call(path, body=None, headers=None, method=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
        try:
            data = (
                json.dumps(body).encode()
                if body is not None and not isinstance(body, bytes)
                else body
            )
            connection.request(
                method or ("POST" if body is not None else "GET"),
                path,
                data,
                {"Content-Type": "application/json", "X-Workbench-Request": "1", **(headers or {})},
            )
            response = connection.getresponse()
            content = response.read()
            return (
                response.status,
                dict(response.getheaders()),
                json.loads(content)
                if response.headers.get_content_type() == "application/json"
                else content,
            )
        finally:
            connection.close()

    yield call
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_http_demo_to_reopened_review_and_export(http_request):
    request = http_request
    status, headers, html = request("/")
    assert status == 200 and b"Review workbench" in html
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    status, _, state = request("/api/demo", {"version": 1, "expected_revision": 0})
    assert status == 200 and state["demo_version"] == 1
    _, _, state = request(
        "/api/confirm", {"reviewer": "Local reviewer", "expected_revision": state["revision"]}
    )
    _, _, state = request("/api/run", {"expected_revision": state["revision"]})
    finding = next(f for f in state["findings"] if f["rule_id"] == "cash")
    status, _, state = request(
        "/api/review",
        {
            "rule_id": "cash",
            "disposition": "noted",
            "note": "Evidence checked",
            "reviewer": "Local reviewer",
            "evidence_digest": finding["evidence_digest"],
            "expected_revision": state["revision"],
        },
    )
    assert status == 200
    _, _, state = request("/api/demo", {"version": 2, "expected_revision": state["revision"]})
    finding = next(f for f in state["findings"] if f["rule_id"] == "cash")
    assert finding["difference"] == "5000.00" and finding["review_status"] == "reopened"
    _, headers, packet = request("/api/export")
    assert packet["status"] == "unreviewed" and len(packet["versions"]) == 2
    assert headers["Content-Disposition"] == 'attachment; filename="review-packet.json"'


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Origin": "https://evil.example"},
        {"X-Workbench-Request": ""},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_foreign_requests_cannot_modify_workspace(http_request, headers):
    request = http_request
    assert request("/api/demo", {"version": 1, "expected_revision": 0}, headers)[0] == 403
    assert request("/api/state")[2]["revision"] == 0
    assert request("/api/state", headers={"Host": "evil.example"})[0] == 403


def test_bad_encoding_bad_input_and_oversize_are_controlled(http_request):
    request = http_request
    for body in (b"\xff", b"[]", b'{"sensitive123":true}'):
        status, _, error = request("/api/run", body)
        assert status == 400 and "sensitive123" not in json.dumps(error)
    assert request("/api/run", b"", {"Content-Length": "10000001"})[0] == 413
    assert request("/../workbench.sqlite3")[0] == 404
    assert request("/api/state", method="OPTIONS")[0] == 501


def test_concurrent_requests_use_one_revision_once(http_request):
    request = http_request
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: request("/api/demo", {"version": 1, "expected_revision": 0})[0], range(2)
            )
        )
    assert sorted(results) == [200, 409]
    assert len(request("/api/state")[2]["workbooks"]) == 1


def test_raw_upload_endpoint_and_no_implicit_mapping_confirmation(http_request):
    request = http_request
    _, _, content = request("/api/demo-workbook?version=1")
    status, _, state = request(
        "/api/import?filename=uploaded.xlsx&label=Uploaded&expected_revision=0",
        content,
        {"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    )
    assert status == 200 and state["workbooks"][0]["label"] == "Uploaded"
    assert state["demo_version"] is None and not state["mappings_confirmed"]


def test_http_preview_requires_current_revision_and_valid_bounds(http_request):
    request = http_request
    assert request("/api/preview")[0] == 400
    _, _, state = request("/api/demo", {"version": 1, "expected_revision": 0})
    revision = state["revision"]

    status, _, preview = request(
        f"/api/preview?expected_revision={revision}&sheet=Support&start_row=2&start_column=2"
    )
    assert status == 200
    assert preview["sheet"] == "Support"
    assert preview["rows"][0][0] == {
        "cell": "B2",
        "raw_value": 600000,
        "value": 600000,
        "error": None,
    }
    assert request("/api/preview?expected_revision=0")[0] == 409
    for query in (
        f"expected_revision={revision}&sheet=Missing",
        f"expected_revision={revision}&start_row=0",
        f"expected_revision={revision}&start_column=16385",
        f"expected_revision={revision}.0",
        f"expected_revision={revision}&start_row=true",
    ):
        assert request(f"/api/preview?{query}")[0] == 400
