"""Command line entry point: `jbe open`, `jbe serve`, `jbe index`, `jbe sample`."""

from __future__ import annotations

import json
import random
import threading
import time
import webbrowser
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn

from .indexer import build_index, default_db_path, is_fresh

app = typer.Typer(add_completion=False, help="Explore huge JSON files as an interactive graph.")
console = Console()


def _index(json_file: Path, db: Path, force: bool, fts: bool) -> None:
    if not force and is_fresh(json_file, db):
        console.print(f"[green]Index up to date:[/] {db}")
        return
    console.print(f"Indexing [bold]{json_file}[/] → {db}")
    start = time.perf_counter()
    with Progress(
        "[progress.description]{task.description}", BarColumn(), DownloadColumn(),
        TimeRemainingColumn(), console=console,
    ) as bar:
        task = bar.add_task("parsing", total=json_file.stat().st_size)
        n = build_index(json_file, db, fts=fts, progress=lambda done, _t: bar.update(task, completed=done))
    console.print(f"[green]Indexed {n:,} nodes in {time.perf_counter() - start:.1f}s[/]")


@app.command()
def index(
    json_file: Path = typer.Argument(..., exists=True, dir_okay=False, help="JSON file to index"),
    db: Path = typer.Option(None, help="Index location (default: <file>.jbe.db next to the JSON)"),
    force: bool = typer.Option(False, help="Rebuild even if the index is up to date"),
    fts: bool = typer.Option(True, help="Build the full-text search index"),
):
    """Build (or refresh) the SQLite index for a JSON file."""
    _index(json_file, db or default_db_path(json_file), force, fts)


@app.command("open")
def open_(
    json_file: Path = typer.Argument(
        None, exists=True, dir_okay=False, help="JSON file to explore (omit to pick one in a dialog)"
    ),
    db: Path = typer.Option(None, help="Index location (default: <file>.jbe.db next to the JSON)"),
    fts: bool = typer.Option(True, help="Build the full-text search index if indexing"),
    debug: bool = typer.Option(False, help="Enable the webview's developer tools"),
):
    """Explore a JSON file in a desktop window (no server, no port)."""
    try:
        import webview  # noqa: F401
    except ImportError as e:
        console.print(f"[red]Desktop mode needs pywebview ({e}).[/] Try `jbe serve` instead.")
        raise typer.Exit(1)
    from .desktop import run

    run(json_file, db, fts=fts, debug=debug)


@app.command()
def serve(
    json_file: Path = typer.Argument(..., exists=True, dir_okay=False, help="JSON file to explore"),
    db: Path = typer.Option(None, help="Index location (default: <file>.jbe.db next to the JSON)"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    browser: bool = typer.Option(True, help="Open the UI in a browser"),
    fts: bool = typer.Option(True, help="Build the full-text search index if indexing"),
):
    """Index if needed, then serve the UI in your browser over a local port."""
    import uvicorn

    from .server import create_app

    db = db or default_db_path(json_file)
    _index(json_file, db, force=False, fts=fts)
    url = f"http://{host}:{port}/"
    console.print(f"Serving at [bold]{url}[/]  (Ctrl+C to stop)")
    if browser:
        threading.Timer(1.0, webbrowser.open, (url,)).start()
    uvicorn.run(create_app(db), host=host, port=port, log_level="warning")


@app.command()
def sample(
    out: Path = typer.Argument(..., help="Where to write the synthetic JSON"),
    records: int = typer.Option(100_000, help="Number of user records"),
    seed: int = typer.Option(42),
):
    """Write a synthetic test file (100k records ≈ 2.5M pretty-printed lines)."""
    rnd = random.Random(seed)
    tags = ["alpha", "beta", "gamma", "delta", "omega", "zeta"]
    cities = ["Warsaw", "Kraków", "Berlin", "Lisbon", "Osaka", "Toronto"]
    with open(out, "w", encoding="utf-8") as f:
        f.write('{\n  "meta": {"generated": "jbe sample", "records": %d},\n  "users": [\n' % records)
        for i in range(records):
            rec = {
                "id": i,
                "name": f"user_{i}",
                "active": rnd.random() > 0.3,
                "score": round(rnd.uniform(0, 100), 3),
                "address": {"city": rnd.choice(cities), "zip": f"{rnd.randint(10000, 99999)}"},
                "tags": rnd.sample(tags, rnd.randint(0, 3)),
                "manager": None if rnd.random() > 0.2 else rnd.randrange(records),
            }
            text = json.dumps(rec, indent=2, ensure_ascii=False)
            f.write("    " + text.replace("\n", "\n    "))
            f.write(",\n" if i < records - 1 else "\n")
        f.write("  ]\n}\n")
    console.print(f"Wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    app()
