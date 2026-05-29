"""CLI entry point: `catnat ...`.

Subcommands:
  catnat probe              Verify warehouse connectivity and ST_* / H3 functions.
  catnat setup              Create catalog/schemas/volume (idempotent).
  catnat fetch rga          Pull BRGM RGA polygons from WFS and upload to volume.
  catnat run NOTEBOOK_PATH  Execute a SQL notebook against the warehouse.
  catnat pipeline rga       End-to-end: fetch → bronze → silver → gold.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from catnat.config import CONFIG
from catnat.sql import Statement, WarehouseRunner

app = typer.Typer(no_args_is_help=True, add_completion=False)
fetch_app = typer.Typer(no_args_is_help=True, add_completion=False)
pipeline_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(fetch_app, name="fetch", help="Pull source data into the bronze raw volume.")
app.add_typer(pipeline_app, name="pipeline", help="End-to-end pipelines per peril.")

console = Console()

NOTEBOOKS_DIR = Path(__file__).resolve().parent.parent.parent / "notebooks"


def _log_stmt(stmt: Statement) -> None:
    line0 = stmt.text.lstrip().splitlines()[0][:80]
    console.log(f"[dim]{stmt.label}[/dim] {line0}")


def _params_default() -> dict[str, str]:
    return {"catalog": CONFIG.catalog}


@app.command()
def probe() -> None:
    """Smoke-test the warehouse: spatial primitives and H3 functions."""
    runner = WarehouseRunner()
    checks = [
        ("ST_Point + ST_AsText", "SELECT ST_AsText(ST_Point(2.3522, 48.8566))"),
        (
            "ST_Transform L93→WGS84",
            "SELECT ST_AsText(ST_Transform(ST_GeomFromText('POINT(652000 6862000)', 2154), 4326))",
        ),
        ("h3_longlatash3 r=9", "SELECT h3_longlatash3(2.3522, 48.8566, 9)"),
        (
            "h3_polyfillash3 (WKB)",
            "SELECT size(h3_polyfillash3(ST_AsBinary(ST_Buffer(ST_Point(2.35, 48.85), 0.01)), 9))",
        ),
    ]
    for label, sql in checks:
        runner.execute(sql)
        console.print(f"  [green]✓[/green] {label}")
    console.print(
        f"\n[bold]Probe OK[/bold] — profile={runner.profile} warehouse={runner.warehouse_id}"
    )


@app.command()
def setup() -> None:
    """Create catnat_{bronze,silver,gold} schemas + bronze.raw volume. Idempotent."""
    nb = NOTEBOOKS_DIR / "_setup" / "00_create_catalog.sql"
    runner = WarehouseRunner()
    n = runner.run_notebook(nb, parameters=_params_default(), on_statement=_log_stmt)
    console.print(f"[bold]Setup done[/bold] — {n} statements applied.")


@app.command()
def run(
    notebook: Path = typer.Argument(..., help="Path to a .sql notebook."),
    param: list[str] = typer.Option(
        None,
        "--param",
        "-p",
        help="Override a notebook widget, e.g. -p catalog=foo. Repeatable.",
    ),
) -> None:
    """Run a SQL notebook against the warehouse."""
    params = _params_default()
    for kv in param or []:
        k, _, v = kv.partition("=")
        if not k or not v:
            raise typer.BadParameter(f"--param must be name=value, got: {kv}")
        params[k] = v
    runner = WarehouseRunner()
    n = runner.run_notebook(notebook, parameters=params, on_statement=_log_stmt)
    console.print(f"[bold]✓[/bold] {notebook.name} — {n} statements ran.")


@fetch_app.command("rga")
def fetch_rga(
    limit: int = typer.Option(
        100, "--limit", help="Number of features to pull. Use --full for everything."
    ),
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
) -> None:
    """Pull BRGM RGA polygons from Géorisques WFS and upload to bronze.raw."""
    from catnat.fetch import rga

    n_request = None if full else limit
    remote, n = rga.fetch_and_upload(limit=n_request)
    console.print(f"[bold]✓[/bold] uploaded {n} features → {remote}")


@pipeline_app.command("rga")
def pipeline_rga(
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    skip_fetch: bool = typer.Option(
        False, "--skip-fetch", help="Skip the WFS pull; reuse what's in the volume."
    ),
) -> None:
    """End-to-end RGA pipeline: fetch → bronze → silver → gold."""
    from catnat.fetch import rga as rga_fetch

    runner = WarehouseRunner()
    params = _params_default()

    if not skip_fetch:
        n_request = None if full else 100
        remote, n = rga_fetch.fetch_and_upload(limit=n_request)
        console.print(f"[bold]Fetch[/bold] — uploaded {n} features → {remote}")
        params["input_path"] = remote
    else:
        params["input_path"] = f"{CONFIG.raw_volume_path}/rga/rga_sample.geojsonl"

    # Per-stage params. The pipeline_rga signature is fixed; gold defaults to
    # H3 r=9 (the demo's policy-point grain). Override via env if needed.
    stages = [
        ("Setup", NOTEBOOKS_DIR / "_setup" / "00_create_catalog.sql", {}),
        ("Bronze", NOTEBOOKS_DIR / "bronze" / "10_rga_susceptibility.sql", {}),
        ("Silver", NOTEBOOKS_DIR / "silver" / "10_rga_susceptibility.sql", {}),
        ("Gold", NOTEBOOKS_DIR / "gold" / "10_rga_h3.sql", {"resolution": "9"}),
    ]
    for label, nb, extra in stages:
        console.print(f"[bold cyan]→ {label}[/bold cyan] {nb.relative_to(NOTEBOOKS_DIR.parent)}")
        merged = {**params, **extra}
        n = runner.run_notebook(nb, parameters=merged, on_statement=_log_stmt)
        console.print(f"  {label} — {n} statements.\n")

    console.print("[bold green]Pipeline complete.[/bold green]")
