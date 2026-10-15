"""Stream a JSON file into a SQLite node index without loading it into memory."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from typing import Callable

import ijson

from .store import readonly_uri

SCHEMA_VERSION = 1
PREVIEW_LEN = 1000
BATCH = 50_000

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE nodes(
    id INTEGER PRIMARY KEY,
    parent_id INTEGER,
    key TEXT,
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    preview TEXT,
    child_count INTEGER NOT NULL DEFAULT 0,
    depth INTEGER NOT NULL
);
"""

POST_SCHEMA = "CREATE INDEX nodes_parent ON nodes(parent_id, idx);"

FTS_SCHEMA = """
CREATE VIRTUAL TABLE nodes_fts USING fts5(key, preview, content='nodes', content_rowid='id');
INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild');
"""

SCALAR_TYPES = {"string": "string", "number": "number", "boolean": "bool", "null": "null"}


def _cache_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "jbe"


def default_db_path(json_path: Path) -> Path:
    """<file>.jbe.db next to the JSON, or a per-user cache file if that folder is read-only."""
    json_path = Path(json_path).resolve()
    beside = json_path.with_name(json_path.name + ".jbe.db")
    if beside.exists() or os.access(json_path.parent, os.W_OK):
        return beside
    digest = hashlib.sha1(str(json_path).encode()).hexdigest()[:12]
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    return cache / f"{json_path.stem}-{digest}.jbe.db"


def _fingerprint(json_path: Path) -> dict[str, str]:
    st = json_path.stat()
    return {
        "schema_version": str(SCHEMA_VERSION),
        "source_size": str(st.st_size),
        "source_mtime": str(st.st_mtime_ns),
    }


def is_fresh(json_path: Path, db_path: Path) -> bool:
    """True if db_path is a complete index of the current json_path."""
    if not db_path.exists():
        return False
    try:
        con = sqlite3.connect(readonly_uri(db_path), uri=True)
        try:
            meta = dict(con.execute("SELECT key, value FROM meta"))
        finally:
            con.close()
    except sqlite3.Error:
        return False
    want = _fingerprint(json_path)
    return meta.get("complete") == "1" and all(meta.get(k) == v for k, v in want.items())


def _scalar_preview(event: str, value) -> str:
    if event == "string":
        return value[:PREVIEW_LEN]
    if event == "boolean":
        return "true" if value else "false"
    if event == "null":
        return "null"
    return str(value)


def build_index(
    json_path: Path,
    db_path: Path,
    *,
    fts: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """Index json_path into db_path. Returns the number of nodes.

    progress(bytes_read, total_bytes) is called periodically if given.
    The index is written to a temp file and only moved into place on success.
    """
    json_path = Path(json_path)
    db_path = Path(db_path)
    tmp_path = db_path.with_name(db_path.name + ".tmp")
    tmp_path.unlink(missing_ok=True)

    con = sqlite3.connect(tmp_path)
    try:
        con.executescript(
            "PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;"
            "PRAGMA cache_size=-64000;"
        )
        con.executescript(SCHEMA)
        node_count = _load_nodes(con, json_path, progress)
        con.executescript(POST_SCHEMA)
        if fts:
            con.executescript(FTS_SCHEMA)
        meta = _fingerprint(json_path) | {
            "source_path": str(json_path.resolve()),
            "node_count": str(node_count),
            "fts": "1" if fts else "0",
            "complete": "1",
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        con.commit()
    except BaseException:
        con.close()
        tmp_path.unlink(missing_ok=True)
        raise
    con.close()
    os.replace(tmp_path, db_path)
    return node_count


def _load_nodes(con: sqlite3.Connection, json_path: Path, progress) -> int:
    """Stream parse events into the nodes table; returns the node count."""
    total = json_path.stat().st_size
    rows: list[tuple] = []
    counts: list[tuple[int, int]] = []
    # Stack entries: [node_id, is_map, child_count, pending_key]
    stack: list[list] = []
    next_id = 0
    insert = "INSERT INTO nodes VALUES (?,?,?,?,?,?,?,?)"

    def flush() -> None:
        if rows:
            con.executemany(insert, rows)
            rows.clear()
        if counts:
            con.executemany("UPDATE nodes SET child_count=? WHERE id=?", counts)
            counts.clear()

    with open(json_path, "rb") as f:
        for event, value in ijson.basic_parse(f):
            if event == "map_key":
                stack[-1][3] = value
                continue
            if event in ("end_map", "end_array"):
                node_id, _, n, _ = stack.pop()
                if n:
                    counts.append((n, node_id))
                continue

            # A value: scalar or start of a container.
            if stack:
                parent = stack[-1]
                parent_id = parent[0]
                key = parent[3] if parent[1] else None
                idx = parent[2]
                parent[2] += 1
            else:
                parent_id, key, idx = None, None, 0
            depth = len(stack)
            node_id = next_id
            next_id += 1

            if event == "start_map":
                rows.append((node_id, parent_id, key, idx, "object", None, 0, depth))
                stack.append([node_id, True, 0, None])
            elif event == "start_array":
                rows.append((node_id, parent_id, key, idx, "array", None, 0, depth))
                stack.append([node_id, False, 0, None])
            else:
                rows.append(
                    (node_id, parent_id, key, idx, SCALAR_TYPES[event],
                     _scalar_preview(event, value), 0, depth)
                )

            if len(rows) >= BATCH:
                flush()
                if progress:
                    progress(f.tell(), total)

    flush()
    if progress:
        progress(total, total)
    return next_id
