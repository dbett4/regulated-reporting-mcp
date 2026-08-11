"""
HTTP client for the Workiva API.

Handles:
- Unified HTTP requests with automatic header injection
- Async operation polling (202 responses)
- Pagination via @nextLink
- 429 rate-limit retries with Retry-After
- 409 conflict detection

Transport is swappable: WORKIVA_MCP_MOCK=1 routes every request through the
in-memory FakeWorkiva transport (see mock_transport.py) so the full tool
surface runs end-to-end with zero credentials and zero network I/O.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from workiva_mcp.auth import clear_token, ensure_token, mock_mode
from workiva_mcp.config import (
    API_VERSION,
    POLL_INTERVAL,
    POLL_TIMEOUT,
    RATE_LIMIT_BACKOFF,
    get_urls,
)
from workiva_mcp.ssl_context import create_ssl_context
from workiva_mcp.state import app_state

SSL_CTX = create_ssl_context()


class WorkivaAPIError(Exception):
    def __init__(self, status: int, message: str, body: dict | str | None = None):
        self.status = status
        self.body = body
        super().__init__(f"Workiva API {status}: {message}")


def _error(status: int, body, cause: str = "", hint: str = "", retry_safe: bool = False) -> str:
    """Return a consistent error JSON string for tool callers.

    Shape: {"error": true, "status": <int>, "cause": <str>, "hint"?: <str>, "retry_safe"?: true}
    """
    if not cause:
        if isinstance(body, dict):
            err = body.get("error")
            if isinstance(err, dict):
                cause = err.get("message") or str(err)[:200]
            else:
                cause = str(err or body.get("message") or body)[:200]
        else:
            cause = str(body)[:200]
    out: dict = {"error": True, "status": status, "cause": cause}
    if hint:
        out["hint"] = hint
    if retry_safe:
        out["retry_safe"] = True
    return json.dumps(out)


def _looks_like_protected_export_body(raw: bytes | str) -> bool:
    if isinstance(raw, bytes):
        return (
            raw.startswith(b")]}'")
            or raw.startswith(b"PK")
            or raw.startswith(b"%PDF-")
        )
    return (
        raw.startswith(")]}'")
        or raw.startswith("PK")
        or raw.startswith("%PDF-")
    )


def _parse_raw_body(raw: bytes, raw_on_pk_prefix: bool = False) -> dict | str | bytes:
    # Workiva export resources can be binary ZIP/XLSX or PDF bodies. Never
    # decode them as UTF-8: replacement characters corrupt embedded streams
    # and invalidate PDF cross-reference offsets.
    if raw_on_pk_prefix and _looks_like_protected_export_body(raw):
        return raw
    try:
        return json.loads(raw) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw.decode("utf-8", errors="replace")


def _raw_http(url, method="GET", headers=None, data=None, timeout=60, raw_on_pk_prefix: bool = False):
    """Low-level HTTP request. Returns (status, headers_dict, parsed_body)."""
    # Encode spaces and other bare characters that urllib rejects
    url = urllib.parse.quote(url, safe="/:?&=$',;@!*+()[]#%")
    headers = headers or {}
    body_bytes = None
    if data is not None:
        if isinstance(data, (bytes, bytearray)):
            body_bytes = data
        elif isinstance(data, (dict, list)):
            body_bytes = json.dumps(data).encode("utf-8")
        else:
            body_bytes = str(data).encode("utf-8")

    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout)
        raw = resp.read()
        parsed = _parse_raw_body(raw, raw_on_pk_prefix=raw_on_pk_prefix)
        return resp.status, dict(resp.headers), parsed
    except urllib.error.HTTPError as e:
        raw = e.read()
        parsed = _parse_raw_body(raw, raw_on_pk_prefix=raw_on_pk_prefix)
        return e.code, dict(e.headers) if e.headers else {}, parsed


def _transport():
    """Return the active low-level transport.

    Resolved per call so tests can monkeypatch `_raw_http` and so
    WORKIVA_MCP_MOCK can be flipped without re-importing the module.
    """
    if mock_mode():
        from workiva_mcp.mock_transport import mock_raw_http

        return mock_raw_http
    return _raw_http


def _fix_url(url: str) -> str:
    """Replace ${WORKIVA_CLUSTER_DOMAIN} placeholder."""
    urls = get_urls()
    return url.replace("${WORKIVA_CLUSTER_DOMAIN}", urls["cluster_domain"])


def _split_absolute_http_url(url: str) -> tuple[str, str] | None:
    parsed = urllib.parse.urlsplit(_fix_url(url))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    root = f"{parsed.scheme}://{parsed.netloc}"
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return root, path


def next_link_request_route(url: str) -> tuple[str, str | None, bool]:
    """Return path, optional absolute root, and version-header suppression flag."""
    absolute = _split_absolute_http_url(url)
    if absolute:
        root, path = absolute
        return path, root, True
    return _fix_url(url), None, False


def _content_headers(token: str) -> dict:
    """Headers for Content API (versioned)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Version": API_VERSION,
    }


def _platform_headers(token: str) -> dict:
    """Headers for Platform v1 API (no version header)."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _wdata_headers(token: str) -> dict:
    """Headers for Wdata API."""
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _headers_for_api(token: str, api: str, suppress_version_header: bool = False) -> dict:
    if api == "content":
        headers = _content_headers(token)
        if suppress_version_header:
            headers.pop("X-Version", None)
        return headers
    if api == "wdata" or api == "chains":
        return _wdata_headers(token)
    return _platform_headers(token)


def api_request(
    path: str,
    method: str = "GET",
    data: dict | list | bytes | None = None,
    api: str = "content",
    content_type: str | None = None,
    timeout: int = 60,
    tool_name: str = "",
    base_url: str | None = None,
    suppress_version_header: bool = False,
    raw_on_pk_prefix: bool = False,
) -> tuple[int, dict, dict | str | bytes]:
    """
    Make an authenticated Workiva API request.

    Args:
        path: API path (e.g., "/spreadsheets" or full URL)
        method: HTTP method
        data: Request body
        api: API surface — "content", "platform", "wdata", "chains"
        content_type: Override Content-Type header
        timeout: Request timeout in seconds
        tool_name: Name of the calling tool (for logging)

    Returns:
        (status_code, response_headers, parsed_body)
    """
    token = ensure_token()
    urls = get_urls()

    # Build URL
    if base_url:
        url = base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")
    elif path.startswith(("http://", "https://")):
        url = path
    elif api == "wdata":
        url = urls["wdata_base"] + path
    elif api == "chains":
        url = urls["chains_base"] + path
    elif api == "platform":
        url = urls["platform_base"] + "/platform/v1" + path
    else:
        url = urls["platform_base"] + path

    # Build headers
    headers = _headers_for_api(token, api, suppress_version_header)

    if content_type:
        headers["Content-Type"] = content_type

    # Rate-limit retry loop
    start = time.time()
    auth_retried = False

    for attempt, backoff in enumerate([0] + RATE_LIMIT_BACKOFF):
        if backoff > 0:
            time.sleep(backoff)

        # Pass raw_on_pk_prefix only when set, so the default path is the
        # original positional call — keeps monkeypatched _raw_http stubs that
        # do not declare the kwarg (test_http_client fakes) working.
        _raw_extra = {"raw_on_pk_prefix": raw_on_pk_prefix} if raw_on_pk_prefix else {}
        s, resp_headers, body = _transport()(
            url,
            method,
            headers,
            data,
            timeout,
            **_raw_extra,
        )
        duration_ms = (time.time() - start) * 1000

        # 401 — token expired or invalidated server-side; clear cache, re-auth, retry once
        if s == 401 and not auth_retried:
            auth_retried = True
            clear_token()
            token = ensure_token()
            headers = _headers_for_api(token, api, suppress_version_header)
            if content_type:
                headers["Content-Type"] = content_type
            continue

        if s == 429:
            if attempt < len(RATE_LIMIT_BACKOFF):
                continue
            app_state.log_operation(tool_name, path, method, s, duration_ms, "Rate limited")
            raise WorkivaAPIError(s, "Rate limited — too many requests", body)

        # Log the operation
        error_msg = None
        if s >= 400:
            error_msg = str(body)[:200] if body else f"HTTP {s}"
        app_state.log_operation(tool_name, path, method, s, duration_ms, error_msg)

        return s, resp_headers, body

    raise WorkivaAPIError(429, "Rate limited after all retries")


def poll_operation(
    location: str,
    label: str = "operation",
    timeout: float = POLL_TIMEOUT,
    tool_name: str = "",
) -> dict | str:
    """
    Poll an async operation (202 response) until completion.

    Args:
        location: Operation-Location URL from the 202 response
        label: Human label for logging
        timeout: Max seconds to wait
        tool_name: Calling tool name for logging

    Returns:
        The resource at the completed operation's resourceUrl
    """
    location = _fix_url(location)
    start = time.time()
    poll_tool = f"poll:{tool_name or 'op'}"

    while time.time() - start < timeout:
        # Route through api_request so poll traffic appears in /logs and
        # 429/401 retries are handled uniformly with every other call.
        s, _, body = api_request(location, api="content", tool_name=poll_tool)

        if s != 200:
            time.sleep(POLL_INTERVAL)
            continue

        if isinstance(body, dict):
            status = body.get("status", "")
            if status in ("completed", "succeeded"):
                resource_url = body.get("resourceUrl", "")
                if resource_url:
                    resource_url = _fix_url(resource_url)
                    rs, _, result = api_request(
                        resource_url, tool_name=tool_name, raw_on_pk_prefix=True
                    )
                    if rs == 200:
                        return result
                return body
            elif status == "failed":
                error_detail = body.get("error", body)
                raise WorkivaAPIError(500, f"{label} failed: {error_detail}", body)

        time.sleep(POLL_INTERVAL)

    raise WorkivaAPIError(408, f"{label} timed out after {timeout}s")


def paginate(
    path: str,
    api: str = "content",
    max_pages: int = 10,
    tool_name: str = "",
) -> list:
    """
    Fetch all pages of a list endpoint.

    For `/content/tables/{id}/cells` paths, auto-parallelizes via row-range
    fan-out using the documented `startRow`/`stopRow`/`$maxcellsperpage`
    query params (see Workiva 2026-01-01 Get Table Cells spec — default
    and max `$maxcellsperpage` is 50000).

    For all other paths, falls back to sequential @nextLink / next_url
    pagination.

    Returns combined items from the `data` or `body` array across pages.
    """
    # Fast path for table-cell reads: parallel row-range fetches.
    if api == "content" and "/content/tables/" in path and path.rstrip("/").endswith("/cells") and "?" not in path:
        return _read_table_cells_parallel(path, tool_name=tool_name)

    all_items = []
    url = path
    pages = 0

    while url and pages < max_pages:
        request_path, request_base_url, suppress_version_header = next_link_request_route(url)
        s, _, body = api_request(
            request_path,
            api=api,
            tool_name=tool_name,
            base_url=request_base_url,
            suppress_version_header=suppress_version_header,
        )
        if s != 200 or not isinstance(body, dict):
            # First-page failure is a hard error — callers must not confuse
            # "request failed" with "empty result". Subsequent pages may
            # break with partial results.
            if pages == 0:
                raise WorkivaAPIError(s, "paginate first page failed", body)
            break

        items = body.get("data") or body.get("body") or []
        all_items.extend(items)

        # Content/Platform APIs use @nextLink
        next_link = body.get("@nextLink")
        if next_link:
            url = next_link
        else:
            # Wdata uses next_url
            next_url = body.get("next_url")
            url = next_url if next_url else None

        pages += 1

    return all_items


# Tunable via env if needed later; conservative defaults.
_CELLS_CHUNK_ROWS = 500
_CELLS_CONCURRENCY = 8
_CELLS_MAX_ROWS = 20000
_CELLS_EMPTY_STOP = 2  # stop after this many consecutive empty chunks


def _read_table_cells_parallel(
    path: str,
    tool_name: str = "",
    chunk_rows: int = _CELLS_CHUNK_ROWS,
    concurrency: int = _CELLS_CONCURRENCY,
    max_rows: int = _CELLS_MAX_ROWS,
) -> list:
    """Parallel row-range fetch for `/content/tables/{id}/cells`.

    Fires `concurrency` chunked GETs per batch, each scoped to a 500-row
    range with `$maxcellsperpage=50000`. Stops when consecutive chunks
    return zero rows (end of table reached).

    Returns rows (row-items with `cells` sub-arrays) sorted by starting row.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_chunk(start_row: int, stop_row: int):
        q = f"?startRow={start_row}&stopRow={stop_row}&$maxcellsperpage=50000"
        s, _, body = api_request(path + q, api="content", tool_name=tool_name)
        if s != 200 or not isinstance(body, dict):
            return start_row, []
        return start_row, body.get("data") or []

    all_rows_by_start: dict[int, list] = {}
    consecutive_empty = 0
    cursor = 0

    while cursor < max_rows and consecutive_empty < _CELLS_EMPTY_STOP:
        ranges = [
            (cursor + i * chunk_rows, cursor + (i + 1) * chunk_rows - 1)
            for i in range(concurrency)
        ]
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            futures = [ex.submit(_fetch_chunk, lo, hi) for lo, hi in ranges]
            for fut in as_completed(futures):
                start_row, rows = fut.result()
                all_rows_by_start[start_row] = rows
        # Evaluate this batch IN ORDER to detect trailing-empty run
        for lo, _hi in ranges:
            if all_rows_by_start.get(lo):
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= _CELLS_EMPTY_STOP:
                    break
        cursor += concurrency * chunk_rows

    # Flatten in row order
    out: list = []
    for start in sorted(all_rows_by_start):
        out.extend(all_rows_by_start[start])
    return out
