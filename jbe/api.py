"""Transport-independent API used by both the HTTP server and the desktop window."""

from __future__ import annotations

from .store import Store


class NotFound(LookupError):
    pass


class Api:
    def __init__(self, store: Store):
        self._store = store

    def _node(self, node_id: int) -> dict:
        n = self._store.node(int(node_id))
        if n is None:
            raise NotFound(f"node {node_id} not found")
        return n

    def meta(self) -> dict:
        s = self._store
        return {
            "source": s.meta.get("source_path"),
            "node_count": int(s.meta.get("node_count", 0)),
            "fts": s.has_fts,
            "root": s.root(),
        }

    def node(self, node_id: int) -> dict:
        n = self._node(node_id)
        n["path"] = self._store.path(n["id"])
        return n

    def children(self, node_id: int, offset: int = 0, limit: int = 100) -> dict:
        n = self._node(node_id)
        offset, limit = max(0, int(offset)), min(max(1, int(limit)), 1000)
        return {"node": n, "offset": offset, "children": self._store.children(n["id"], offset, limit)}

    def ancestors(self, node_id: int) -> dict:
        n = self._node(node_id)
        chain = self._store.ancestors(n["id"])
        return {"path": self._store.path_of(chain), "ancestors": chain}

    def subtree(self, node_id: int, max_nodes: int = 2000) -> dict:
        n = self._node(node_id)
        value, truncated = self._store.subtree_json(n["id"], min(max(1, int(max_nodes)), 50_000))
        return {"value": value, "truncated": truncated}

    def search(self, q: str, limit: int = 50) -> dict:
        return {"hits": self._store.search(str(q), min(max(1, int(limit)), 500))}
