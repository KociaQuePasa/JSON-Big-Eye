"""Read-only queries over a node index built by jbe.indexer."""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
COLUMNS = "id, parent_id, key, idx, type, preview, child_count, depth"


def readonly_uri(db_path: Path) -> str:
    """SQLite URI opening db_path read-only; as_uri() handles Windows drives and odd characters."""
    return Path(db_path).resolve().as_uri() + "?mode=ro"


def _row(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in r.keys()}


def path_segment(parent_type: str | None, key: str | None, idx: int) -> str:
    if parent_type is None:
        return "$"
    if parent_type == "array":
        return f"[{idx}]"
    if key is not None and _IDENT.match(key):
        return f".{key}"
    escaped = (key or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'["{escaped}"]'


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._local = threading.local()
        self.meta = dict(self._con.execute("SELECT key, value FROM meta"))

    @property
    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, "con", None)
        if con is None:
            con = sqlite3.connect(readonly_uri(self.db_path), uri=True, check_same_thread=False)
            con.row_factory = sqlite3.Row
            self._local.con = con
        return con

    @property
    def has_fts(self) -> bool:
        return self.meta.get("fts") == "1"

    def node(self, node_id: int) -> dict | None:
        r = self._con.execute(f"SELECT {COLUMNS} FROM nodes WHERE id=?", (node_id,)).fetchone()
        return _row(r) if r else None

    def root(self) -> dict | None:
        return self.node(0)

    def children(self, node_id: int, offset: int = 0, limit: int = 100) -> list[dict]:
        cur = self._con.execute(
            f"SELECT {COLUMNS} FROM nodes WHERE parent_id=? AND idx>=? ORDER BY idx LIMIT ?",
            (node_id, offset, limit),
        )
        return [_row(r) for r in cur]

    def ancestors(self, node_id: int) -> list[dict]:
        """Nodes from the root down to node_id (inclusive)."""
        cur = self._con.execute(
            f"""
            WITH RECURSIVE chain(id) AS (
                SELECT ? UNION ALL
                SELECT n.parent_id FROM nodes n JOIN chain c ON n.id = c.id
                WHERE n.parent_id IS NOT NULL
            )
            SELECT {COLUMNS} FROM nodes WHERE id IN (SELECT id FROM chain) ORDER BY depth
            """,
            (node_id,),
        )
        return [_row(r) for r in cur]

    def path(self, node_id: int) -> str:
        return self.path_of(self.ancestors(node_id))

    @staticmethod
    def path_of(chain: list[dict]) -> str:
        parts, parent_type = [], None
        for n in chain:
            parts.append(path_segment(parent_type, n["key"], n["idx"]))
            parent_type = n["type"]
        return "".join(parts)

    def search(self, q: str, limit: int = 50) -> list[dict]:
        q = q.strip()
        if not q:
            return []
        if self.has_fts:
            # Quote each term so user input can't break FTS syntax; prefix-match the last one.
            terms = ['"' + t.replace('"', '""') + '"' for t in q.split()]
            terms[-1] += "*"
            sql = (
                f"SELECT {', '.join('n.' + c.strip() for c in COLUMNS.split(','))} "
                "FROM nodes_fts f JOIN nodes n ON n.id = f.rowid "
                "WHERE nodes_fts MATCH ? ORDER BY n.id LIMIT ?"
            )
            params = (" ".join(terms), limit)
        else:
            like = f"%{q}%"
            sql = f"SELECT {COLUMNS} FROM nodes WHERE key LIKE ? OR preview LIKE ? ORDER BY id LIMIT ?"
            params = (like, like, limit)
        hits = [_row(r) for r in self._con.execute(sql, params)]
        for h in hits:
            h["path"] = self.path(h["id"])
        return hits

    def subtree_json(self, node_id: int, max_nodes: int = 2000):
        """Rebuild the JSON value under node_id, truncating after max_nodes nodes.

        Returns (value, truncated).
        """
        budget = [max_nodes]
        truncated = [False]

        def build(n: dict):
            budget[0] -= 1
            t = n["type"]
            if t == "string":
                return n["preview"]
            if t == "number":
                p = n["preview"]
                try:
                    return int(p)
                except ValueError:
                    return float(p)
            if t == "bool":
                return n["preview"] == "true"
            if t == "null":
                return None
            kids = self.children(n["id"], 0, max(budget[0], 0))
            if len(kids) < n["child_count"]:
                truncated[0] = True
            if t == "array":
                out = []
                for k in kids:
                    if budget[0] <= 0:
                        truncated[0] = True
                        break
                    out.append(build(k))
                return out
            out = {}
            for k in kids:
                if budget[0] <= 0:
                    truncated[0] = True
                    break
                out[k["key"]] = build(k)
            return out

        n = self.node(node_id)
        if n is None:
            return None, False
        return build(n), truncated[0]
