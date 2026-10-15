"""FastAPI app exposing a node index to the browser UI (`jbe serve`)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import Api, NotFound
from .store import Store

STATIC = Path(__file__).parent / "static"


def create_app(db_path: Path) -> FastAPI:
    api = Api(Store(db_path))
    app = FastAPI(title="JSON-BIG-EYE")

    def call(fn, *args):
        try:
            return fn(*args)
        except NotFound as e:
            raise HTTPException(404, str(e)) from None

    @app.get("/api/meta")
    def meta():
        return api.meta()

    @app.get("/api/node/{node_id}")
    def node(node_id: int):
        return call(api.node, node_id)

    @app.get("/api/node/{node_id}/children")
    def children(node_id: int, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=1000)):
        return call(api.children, node_id, offset, limit)

    @app.get("/api/node/{node_id}/ancestors")
    def ancestors(node_id: int):
        return call(api.ancestors, node_id)

    @app.get("/api/node/{node_id}/json")
    def subtree(node_id: int, max_nodes: int = Query(2000, ge=1, le=50_000)):
        return call(api.subtree, node_id, max_nodes)

    @app.get("/api/search")
    def search(q: str, limit: int = Query(50, ge=1, le=500)):
        return api.search(q, limit)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
