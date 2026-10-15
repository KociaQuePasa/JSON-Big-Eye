import json

import pytest
from fastapi.testclient import TestClient

from jbe.indexer import build_index
from jbe.server import create_app


@pytest.fixture
def client(tmp_path):
    src = tmp_path / "doc.json"
    src.write_text(json.dumps({"users": [{"name": f"u{i}", "n": i} for i in range(300)]}))
    db = tmp_path / "doc.db"
    build_index(src, db)
    return TestClient(create_app(db))


def test_meta_and_root(client):
    m = client.get("/api/meta").json()
    assert m["node_count"] == 1 + 1 + 300 * 3
    assert m["root"]["child_count"] == 1


def test_children_paging(client):
    users = client.get("/api/node/0/children").json()["children"][0]
    page = client.get(f"/api/node/{users['id']}/children?offset=250&limit=100").json()
    assert len(page["children"]) == 50
    assert page["children"][0]["idx"] == 250


def test_search_and_ancestors(client):
    hit = client.get("/api/search?q=u123").json()["hits"][0]
    assert hit["path"] == "$.users[123].name"
    anc = client.get(f"/api/node/{hit['id']}/ancestors").json()
    assert anc["path"] == "$.users[123].name"
    assert [a["depth"] for a in anc["ancestors"]] == [0, 1, 2, 3]


def test_json_and_404(client):
    assert client.get("/api/node/0/json").json()["value"]["users"][5] == {"name": "u5", "n": 5}
    assert client.get("/api/node/999999").status_code == 404


def test_ui_served(client):
    assert "cytoscape" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/vendor/cytoscape.min.js").status_code == 200
