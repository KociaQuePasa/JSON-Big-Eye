import json

import orjson
import pytest

from jbe.indexer import build_index, is_fresh
from jbe.store import Store

DOC = {
    "meta": {"name": "zażółć", "version": 2.5, "ok": True, "none": None},
    "items": [{"id": i, "tags": ["a", "b"] if i % 2 else []} for i in range(250)],
    "weird key": {"a.b": 1, "": "empty", 'q"t': -3},
    "empty_obj": {},
}


@pytest.fixture
def store(tmp_path):
    src = tmp_path / "doc.json"
    src.write_bytes(orjson.dumps(DOC, option=orjson.OPT_INDENT_2))
    db = tmp_path / "doc.json.jbe.db"
    build_index(src, db)
    return Store(db)


def test_roundtrip_matches_source(store):
    value, truncated = store.subtree_json(0, max_nodes=10_000)
    assert not truncated
    assert value == DOC


def test_child_counts_and_types(store):
    root = store.root()
    assert root["type"] == "object" and root["child_count"] == 4
    items = store.children(0)[1]
    assert items["key"] == "items" and items["type"] == "array" and items["child_count"] == 250
    empty = store.children(0)[3]
    assert empty["type"] == "object" and empty["child_count"] == 0


def test_paging_uses_idx(store):
    items_id = store.children(0)[1]["id"]
    page = store.children(items_id, offset=200, limit=100)
    assert [c["idx"] for c in page] == list(range(200, 250))


def test_paths(store):
    items_id = store.children(0)[1]["id"]
    rec = store.children(items_id, offset=137, limit=1)[0]
    assert store.path(store.children(rec["id"])[0]["id"]) == "$.items[137].id"
    weird = store.children(0)[2]
    kids = {c["key"]: c["id"] for c in store.children(weird["id"])}
    assert store.path(weird["id"]) == '$["weird key"]'
    assert store.path(kids["a.b"]) == '$["weird key"]["a.b"]'
    assert store.path(kids['q"t']) == '$["weird key"]["q\\"t"]'


def test_search(store):
    hits = store.search("zażółć")
    assert [h["path"] for h in hits] == ["$.meta.name"]
    assert store.search("weird")[0]["path"] == '$["weird key"]'
    assert store.search('"); DROP') == []


def test_subtree_truncation(store):
    items_id = store.children(0)[1]["id"]
    value, truncated = store.subtree_json(items_id, max_nodes=20)
    assert truncated and 0 < len(value) < 250


@pytest.mark.parametrize("doc", [42, "just a string", [], [1, [2, [3]]], None])
def test_scalar_and_odd_roots(tmp_path, doc):
    src = tmp_path / "x.json"
    src.write_text(json.dumps(doc))
    db = tmp_path / "x.db"
    build_index(src, db, fts=False)
    s = Store(db)
    assert s.subtree_json(0)[0] == doc
    assert s.path(0) == "$"


def test_freshness(tmp_path):
    src = tmp_path / "f.json"
    src.write_text('{"a": 1}')
    db = tmp_path / "f.db"
    assert not is_fresh(src, db)
    build_index(src, db)
    assert is_fresh(src, db)
    src.write_text('{"a": 12}')
    assert not is_fresh(src, db)


def test_invalid_json_leaves_no_index(tmp_path):
    src = tmp_path / "bad.json"
    src.write_text('{"a": [1, 2')
    db = tmp_path / "bad.db"
    with pytest.raises(Exception):
        build_index(src, db)
    assert not db.exists()
