"""Shared TLS context helpers for urllib-based Workiva integrations."""

from __future__ import annotations

import os
import platform
import ssl
import subprocess


def _load_macos_keychains(context: ssl.SSLContext) -> None:
    if platform.system() != "Darwin":
        return

    keychains = [
        "/System/Library/Keychains/SystemRootCertificates.keychain",
        "/Library/Keychains/System.keychain",
        os.path.expanduser("~/Library/Keychains/login.keychain-db"),
    ]
    existing = [path for path in keychains if os.path.exists(path)]
    if not existing:
        return

    result = subprocess.run(
        ["security", "find-certificate", "-a", "-p", *existing],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if result.stdout:
        context.load_verify_locations(cadata=result.stdout)


def create_ssl_context() -> ssl.SSLContext:
    """Create a verifying SSL context with explicit CA-bundle support."""
    ca_file = (
        os.environ.get("WORKIVA_CA_BUNDLE")
        or os.environ.get("SSL_CERT_FILE")
        or os.environ.get("REQUESTS_CA_BUNDLE")
    )
    ca_path = os.environ.get("SSL_CERT_DIR")
    if ca_file:
        context = ssl.create_default_context(cafile=os.path.expanduser(ca_file))
        _load_macos_keychains(context)
        return context
    if ca_path:
        context = ssl.create_default_context(capath=os.path.expanduser(ca_path))
        _load_macos_keychains(context)
        return context

    try:
        import certifi

        context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        context = ssl.create_default_context()

    _load_macos_keychains(context)
    return context
