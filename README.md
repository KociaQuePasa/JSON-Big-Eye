# JSON Big Eye

Explore huge JSON files (millions of lines) as an interactive graph.

![tests](../../actions/workflows/tests.yml/badge.svg)

- **Streams the file once** into a SQLite index, so memory stays flat no matter how big the file is.
- **Draws only what you expand**: 100 children at a time, with ◀ / ▶ pager nodes for huge arrays.
- **Full-text search** over keys and values, with jump-to-node (even deep inside a 200k-item array).
- **Two ways to run**: a native desktop window with no server and no port, or a local web server
  for your browser.
- **Works offline**: the graph libraries are bundled.

## Install

Requires Python 3.10+.

```sh
git clone https://github.com/<you>/JSON-BIG-EYE.git
cd JSON-BIG-EYE
python3 -m venv .venv
.venv/bin/pip install -e .          # Windows: .venv\Scripts\pip install -e .
```

**Linux only:** the desktop window needs a GUI backend. Install one of these:
- GTK: `sudo apt install python3-gi gir1.2-webkit2-4.1`
- Qt: `pip install "pywebview[qt]"`

macOS and Windows 10/11 need nothing extra.

## Use

```sh
jbe open big.json      # desktop window (no network port); omit the file to get a file picker
jbe serve big.json     # same UI in your browser at http://127.0.0.1:8765
jbe index big.json     # just build or refresh the index
jbe sample test.json --records 200000   # synthetic ~2.8M-line test file
```

`jbe` lives in `.venv/bin/`. Activate the venv, or call it by that path.

In the UI:
- Click an object or array to expand it. Click it again to collapse it.
- The side panel shows the selected node's JSON path (with a Copy button) and its value.
- Use the ◀ / ▶ nodes, or *Show children from index*, to page through big containers.
- Press `/` to search keys and values; the last word is prefix-matched. Pick a hit to jump to it.
- In `serve` mode, the URL (`#node=123`) links straight to a node.

### Where the index goes

The index is written to `<file>.jbe.db` next to the JSON. It is reused until the JSON's size or
modification time changes. If the JSON's folder is read-only, the index goes to your user cache
folder instead:
- macOS: `~/Library/Caches/jbe`
- Windows: `%LOCALAPPDATA%\jbe`
- Linux: `~/.cache/jbe`

The index is about 3× the size of the JSON.

## Performance

Measured on an Apple Silicon Mac with a 2.85M-line, 50 MB file:

| | |
|---|---|
| Index build | 11.5 s, 172 MB peak RAM |
| Re-open with an existing index | instant |
| Expand / page / search / jump | 4–70 ms |

## Limits

- Strings are stored up to 1,000 characters. The side panel marks longer ones as truncated.
- The side panel's value preview rebuilds at most 500 nodes of a subtree.
- If an object has duplicate keys, the graph shows all of them; the value preview keeps the last one.

## How it works

```
big.json ──ijson stream──► jbe/indexer.py ──► SQLite (nodes table + FTS5)
                                                   │
                               jbe/store.py, jbe/api.py  (queries)
                                   │                         │
                  jbe/desktop.py (pywebview JS bridge)   jbe/server.py (FastAPI)
                                   └──────────┬──────────────┘
                                 jbe/static/app.js  (Cytoscape.js + dagre)
```

## Development

```sh
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

CI runs the tests on Linux, macOS and Windows (`.github/workflows/tests.yml`).
