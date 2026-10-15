import os
import sys

import pytest

from jbe import indexer
from jbe.api import Api, NotFound
from jbe.desktop import app_html
from jbe.indexer import build_index, default_db_path, is_fresh
from jbe.store import Store


def test_paths_with_odd_characters(tmp_path):
    folder = tmp_path / "odd dir #1 ?ż"
    folder.mkdir()
    src = folder / "my file%20.json"
    src.write_text('{"a": [1, 2, 3]}')
    db = default_db_path(src)
    assert db.parent == folder
    build_index(src, db)
    assert is_fresh(src, db)
    assert Store(db).subtree_json(0)[0] == {"a": [1, 2, 3]}


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions")
def test_read_only_folder_falls_back_to_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "_cache_dir", lambda: tmp_path / "cache")
    folder = tmp_path / "ro"
    folder.mkdir()
    src = folder / "data.json"
    src.write_text("[1]")
    folder.chmod(0o555)
    try:
        db = default_db_path(src)
        assert db.parent == tmp_path / "cache"
        build_index(src, db)
        assert is_fresh(src, db)
    finally:
        folder.chmod(0o755)


def test_api_shapes_and_not_found(tmp_path):
    src = tmp_path / "d.json"
    src.write_text('{"xs": [10, 20, 30]}')
    db = tmp_path / "d.db"
    build_index(src, db)
    api = Api(Store(db))
    xs = api.children(0)["children"][0]
    assert api.children(xs["id"], offset=1, limit=1)["children"][0]["preview"] == "20"
    assert api.ancestors(xs["id"] + 2)["path"] == "$.xs[1]"
    with pytest.raises(NotFound):
        api.node(999)


def test_desktop_page_is_self_contained():
    page = app_html()
    assert "/static/" not in page
    assert "window.JBE_DESKTOP = true" in page
    assert "cytoscape" in page and "dagre" in page
