"""Loopback-only, single-user review workbench. Not a production multi-user server."""

from __future__ import annotations

import argparse
import json
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from workiva_mcp.workbench import LIMITS, Workbench, demo_workbook

STATIC = Path(__file__).with_name("workbench_static")


def _query_integer(args: dict[str, list[str]], name: str, *, default: int | None = None) -> int:
    values = args.get(name)
    if values is None:
        if default is None:
            raise ValueError(f"Missing {name}")
        return default
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        raise ValueError(f"Invalid {name}")
    return int(values[0])


def create_server(root: Path, port: int = 8765, allowed_hosts: tuple[str, ...] = ()):
    workbench = Workbench(root)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log filenames, review notes, or request parameters.

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def send(self, status, body, content_type="application/json", filename=None):
            if content_type == "application/json":
                body = json.dumps(body, ensure_ascii=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            )
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def trusted_host(self):
            try:
                hostname = urlsplit("http://" + self.headers.get("Host", "")).hostname
            except ValueError:
                hostname = None
            if hostname not in {"localhost", "127.0.0.1", *allowed_hosts}:
                self.send(403, {"error": "Host not allowed"})
                return False
            return True

        def do_GET(self):
            if not self.trusted_host():
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/api/state":
                    self.send(200, workbench.state())
                elif url.path == "/api/preview":
                    args = parse_qs(url.query)
                    sheets = args.get("sheet")
                    if sheets is not None and len(sheets) != 1:
                        raise ValueError("Invalid sheet")
                    self.send(
                        200,
                        workbench.preview(
                            _query_integer(args, "expected_revision"),
                            sheet=sheets[0] if sheets else None,
                            start_row=_query_integer(args, "start_row", default=1),
                            start_column=_query_integer(args, "start_column", default=1),
                        ),
                    )
                elif url.path == "/api/export":
                    self.send(200, workbench.export_packet(), filename="review-packet.json")
                elif url.path == "/api/demo-workbook":
                    version = int(parse_qs(url.query).get("version", ["1"])[0])
                    self.send(
                        200,
                        demo_workbook(version),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        f"riverton-v{version}.xlsx",
                    )
                elif url.path in ("/", "/index.html", "/app.js", "/app.css"):
                    name = "index.html" if url.path == "/" else url.path[1:]
                    mime = {
                        "index.html": "text/html; charset=utf-8",
                        "app.js": "text/javascript; charset=utf-8",
                        "app.css": "text/css; charset=utf-8",
                    }[name]
                    self.send(200, (STATIC / name).read_bytes(), mime)
                else:
                    self.send(404, {"error": "Not found"})
            except ValueError as exc:
                stale = str(exc).startswith("Stale")
                self.send(409 if stale else 400, {"error": "Invalid request"})
            except (OSError, sqlite3.Error):
                self.send(500, {"error": "Local storage unavailable"})

        def do_POST(self):
            if not self.trusted_host():
                return
            # A custom header requires a preflight from foreign origins; no CORS
            # permission is served. Also reject a mismatched browser Origin.
            origin = self.headers.get("Origin")
            try:
                origin_url = urlsplit(origin) if origin else None
                same_origin = not origin_url or (
                    origin_url.scheme in ("http", "https")
                    and origin_url.netloc == self.headers.get("Host")
                )
            except ValueError:
                same_origin = False
            if (
                self.headers.get("X-Workbench-Request") != "1"
                or not same_origin
                or self.headers.get("Sec-Fetch-Site") == "cross-site"
            ):
                self.send(403, {"error": "Same-origin workbench request required"})
                return
            try:
                size = int(self.headers.get("Content-Length", "-1"))
                if (
                    self.headers.get("Transfer-Encoding")
                    or not 0 <= size <= LIMITS["compressed_bytes"]
                ):
                    self.send(413, {"error": "Request body exceeds limit or has no valid length"})
                    return
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError("Incomplete request")
                url = urlsplit(self.path)
                if url.path == "/api/import":
                    args = parse_qs(url.query)
                    result = workbench.import_workbook(
                        raw,
                        args["filename"][0],
                        args["label"][0],
                        int(args["expected_revision"][0]),
                    )
                else:
                    if self.headers.get_content_type() != "application/json":
                        raise ValueError("JSON request required")
                    args = json.loads(raw)
                    if not isinstance(args, dict):
                        raise ValueError("Request must be an object")
                    routes = {
                        "/api/demo": workbench.load_demo,
                        "/api/rules": workbench.save_rules,
                        "/api/confirm": workbench.confirm_mappings,
                        "/api/run": workbench.run_checks,
                        "/api/review": workbench.review_finding,
                    }
                    method = routes.get(url.path)
                    if method is None:
                        self.send(404, {"error": "Not found"})
                        return
                    result = method(**args)
                self.send(200, result)
            except (ValueError, TypeError, KeyError) as exc:
                stale = isinstance(exc, ValueError) and str(exc).startswith("Stale")
                # Backend ValueErrors are static messages; parse errors may echo
                # financial data. Never include arbitrary exception text here.
                message = (
                    "Stale workspace or finding; reload and review before resubmitting"
                    if stale
                    else "Request rejected. Check mappings, confirmation, input limits, and workbook format."
                )
                self.send(409 if stale else 400, {"error": message})
            except (OSError, sqlite3.Error):
                self.send(500, {"error": "Local storage unavailable"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / ".workiva_mcp" / "workbench")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--allowed-host",
        action="append",
        default=[],
        help="Explicit trusted reverse-proxy hostname; grants access to this single-user workspace",
    )
    args = parser.parse_args()
    server = create_server(args.data_dir, args.port, tuple(args.allowed_host))
    print(f"Local single-user workbench on loopback port {server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
