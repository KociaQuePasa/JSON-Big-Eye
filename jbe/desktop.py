"""Native desktop window (`jbe open`): the UI talks to Python through pywebview's JS bridge.

No HTTP server is started and no port is opened; the page is passed to the webview as a string
with all CSS/JS inlined, and data calls go through `window.pywebview.api`.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .api import Api
from .indexer import build_index, default_db_path, is_fresh
from .store import Store

STATIC = Path(__file__).parent / "static"

SPLASH = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  :root { --bg:#f7f7f5; --text:#1d1d1b; --muted:#6b6b66; --track:#e4e4df; --accent:#2f6fde; --err:#c23d3d; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#151514; --text:#ececea; --muted:#9a9a94; --track:#2c2c29; --accent:#6c9cf0; --err:#ec7a7a; }
  }
  html, body { height:100%; margin:0; }
  body { display:grid; place-items:center; background:var(--bg); color:var(--text);
         font:14px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  .box { width:min(460px, 80vw); }
  h1 { font-size:18px; font-weight:650; margin:0 0 4px; }
  #msg { color:var(--muted); margin:0 0 14px; word-break:break-all; }
  .track { height:6px; border-radius:3px; background:var(--track); overflow:hidden; }
  #bar { height:100%; width:0; background:var(--accent); transition:width .15s; }
  #err { color:var(--err); white-space:pre-wrap; margin-top:14px; font-family:ui-monospace, Menlo, monospace; font-size:12px; }
</style></head><body><div class="box">
  <h1>JSON Big Eye</h1><p id="msg">Starting…</p>
  <div class="track"><div id="bar"></div></div><div id="err"></div>
</div><script>
  function setStatus(msg, pct) {
    document.getElementById("msg").textContent = msg;
    if (pct !== null) document.getElementById("bar").style.width = pct + "%";
  }
  function setError(msg) { document.getElementById("err").textContent = msg; }
</script></body></html>
"""


def app_html() -> str:
    """index.html with every local stylesheet and script inlined."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    def css(m: re.Match) -> str:
        return "<style>\n" + (STATIC / m.group(1)).read_text(encoding="utf-8") + "\n</style>"

    def js(m: re.Match) -> str:
        return "<script>\n" + (STATIC / m.group(1)).read_text(encoding="utf-8") + "\n</script>"

    page = re.sub(r'<link rel="stylesheet" href="/static/([^"]+)">', css, page)
    page = re.sub(r'<script src="/static/([^"]+)"></script>', js, page)
    return page.replace("<head>", "<head>\n<script>window.JBE_DESKTOP = true;</script>", 1)


def run(json_file: Path | None, db: Path | None, *, fts: bool = True, debug: bool = False) -> None:
    import webview  # imported lazily so the rest of the package works without a GUI toolkit

    bridge = _Bridge()
    window = webview.create_window(
        "JSON Big Eye", html=SPLASH, js_api=bridge, width=1400, height=900,
        min_size=(640, 420), text_select=True,
    )
    webview.start(_boot, (window, bridge, json_file, db, fts), debug=debug)


class _Bridge:
    """Object exposed to JavaScript as window.pywebview.api.

    It is created before the file is chosen; the real Api is attached once indexing is done.
    Methods mirror jbe.api.Api; attributes starting with "_" are hidden from JavaScript.
    """

    def __init__(self):
        self._api: Api | None = None

    def meta(self):
        return self._api.meta()

    def node(self, node_id):
        return self._api.node(node_id)

    def children(self, node_id, offset=0, limit=100):
        return self._api.children(node_id, offset, limit)

    def ancestors(self, node_id):
        return self._api.ancestors(node_id)

    def subtree(self, node_id, max_nodes=2000):
        return self._api.subtree(node_id, max_nodes)

    def search(self, q, limit=50):
        return self._api.search(q, limit)


def _boot(window, bridge: _Bridge, json_file: Path | None, db: Path | None, fts: bool) -> None:
    import webview

    def status(msg: str, pct: float | None = None) -> None:
        window.run_js(f"setStatus({json.dumps(msg)}, {'null' if pct is None else round(pct, 1)})")

    try:
        if json_file is None:
            picked = window.create_file_dialog(
                webview.FileDialog.OPEN, file_types=("JSON files (*.json)", "All files (*.*)")
            )
            if not picked:
                window.destroy()
                return
            json_file = Path(picked[0])

        db = db or default_db_path(json_file)
        window.title = f"{json_file.name} — JSON Big Eye"
        if not is_fresh(json_file, db):
            last = [0.0]

            def progress(done: int, total: int) -> None:
                now = time.monotonic()
                if now - last[0] > 0.1 or done == total:
                    last[0] = now
                    pct = 100 * done / total if total else 100
                    label = "Building search index…" if done == total and fts else f"Indexing {json_file.name}…"
                    status(label, pct)

            status(f"Indexing {json_file.name}…", 0)
            build_index(json_file, db, fts=fts, progress=progress)

        status("Opening…", 100)
        bridge._api = Api(Store(db))
        window.load_html(app_html())
    except Exception as e:  # show the problem in the window rather than dying silently
        window.run_js(f"setError({json.dumps(f'{type(e).__name__}: {e}')})")
