"""Shared IO helpers for workiva_mcp.

Atomic writes so a crash mid-write cannot leave config/state files truncated
or corrupt. Write to a unique sibling temp file, fsync/flush, then os.replace
onto the real path, which is atomic on POSIX and Windows as long as source and
destination are on the same filesystem.
"""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text to `path` atomically.

    Creates parent directories if missing. Writes to a unique sibling temp file,
    flushes + fsyncs, then os.replace()s onto the destination so a partial write
    never leaves a half-written file at the real path. The temp file must be
    unique because multiple threads may write the same cache/config path.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def atomic_create_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Create a complete file atomically and refuse to replace an existing path.

    The completed temporary file is hard-linked into place, so readers never
    observe a partial destination and a collision raises ``FileExistsError``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
