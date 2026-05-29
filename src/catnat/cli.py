"""CLI entry point: `catnat ...`.

Subcommands:
  catnat probe              Verify warehouse connectivity and ST_* / H3 functions.
  catnat setup              Create catalog/schemas/volume (idempotent).
  catnat fetch rga          Pull BRGM RGA polygons from WFS and upload to volume.
  catnat fetch ppri         Pull Géorisques PPRI commune footprints (approuv + prescrit).
  catnat fetch tri          Pull Géorisques TRI flood-hazard footprints (11 layers).
  catnat run NOTEBOOK_PATH  Execute a SQL notebook against the warehouse.
  catnat pipeline rga       End-to-end: fetch → bronze → silver → gold (RGA).
  catnat pipeline ppri      End-to-end: fetch → bronze → silver → gold (PPRI).
  catnat pipeline tri       End-to-end: fetch → bronze → silver → gold (TRI).
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
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """Pull BRGM RGA polygons from Géorisques WFS and upload to bronze.raw."""
    from catnat.fetch import rga

    n_request = None if full else limit
    remote, n, cached = rga.fetch_and_upload(limit=n_request, force=force)
    if cached:
        console.print(f"[bold yellow]cache hit[/bold yellow] (use --force to refresh) — {remote}")
    else:
        console.print(f"[bold]✓[/bold] uploaded {n} features → {remote}")


@fetch_app.command("ppri")
def fetch_ppri(
    limit: int = typer.Option(
        200, "--limit", help="Per-status feature cap. Use --full for everything."
    ),
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """Pull PPRI commune footprints (approuv + prescrit) and upload to bronze.raw."""
    from catnat.fetch import ppri

    n_request = None if full else limit
    results = ppri.fetch_and_upload(limit=n_request, force=force)
    for status, (remote, n, cached) in results.items():
        if cached:
            console.print(f"[bold yellow]cache hit[/bold yellow] [{status}] — {remote}")
        else:
            console.print(f"[bold]✓[/bold] [{status}] uploaded {n} features → {remote}")


@fetch_app.command("tri")
def fetch_tri(
    limit: int = typer.Option(
        30, "--limit", help="Per-layer feature cap (11 layers). Use --full for everything."
    ),
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """Pull TRI flood-hazard footprints (11 ALEA_SYNT layers, one file each)."""
    from catnat.fetch import tri

    n_request = None if full else limit
    results, glob = tri.fetch_and_upload(limit=n_request, force=force)
    for r in results:
        tag = f"{r.scenario}_{r.intensity}"
        if r.skipped:
            console.print(f"[bold red]skip[/bold red] [{tag}] — {r.error}")
        elif r.cached:
            console.print(f"[bold yellow]cache hit[/bold yellow] [{tag}] — {r.remote_path}")
        else:
            console.print(f"[bold]✓[/bold] [{tag}] uploaded {r.count} features → {r.remote_path}")
    n_ok = sum(1 for r in results if not r.skipped)
    console.print(f"\n[bold]{n_ok}/{len(results)}[/bold] layers ready under {glob}")


def _run_stages(
    runner: WarehouseRunner,
    params: dict[str, str],
    stages: list[tuple[str, Path, dict[str, str]]],
) -> None:
    for label, nb, extra in stages:
        console.print(f"[bold cyan]→ {label}[/bold cyan] {nb.relative_to(NOTEBOOKS_DIR.parent)}")
        merged = {**params, **extra}
        n = runner.run_notebook(nb, parameters=merged, on_statement=_log_stmt)
        console.print(f"  {label} — {n} statements.\n")
    console.print("[bold green]Pipeline complete.[/bold green]")


@pipeline_app.command("rga")
def pipeline_rga(
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    skip_fetch: bool = typer.Option(
        False, "--skip-fetch", help="Skip the WFS pull entirely; reuse what's in the volume."
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """End-to-end RGA pipeline: fetch → bronze → silver → gold."""
    from catnat.fetch import rga as rga_fetch

    runner = WarehouseRunner()
    params = _params_default()

    if not skip_fetch:
        n_request = None if full else 100
        remote, n, cached = rga_fetch.fetch_and_upload(limit=n_request, force=force)
        if cached:
            console.print(f"[bold yellow]Fetch[/bold yellow] — cache hit at {remote}")
        else:
            console.print(f"[bold]Fetch[/bold] — uploaded {n} features → {remote}")
        params["input_path"] = remote
    else:
        suffix = "full" if full else "sample"
        params["input_path"] = f"{CONFIG.raw_volume_path}/rga/rga_{suffix}.geojsonl"

    stages = [
        ("Setup", NOTEBOOKS_DIR / "_setup" / "00_create_catalog.sql", {}),
        ("Bronze", NOTEBOOKS_DIR / "bronze" / "10_rga_susceptibility.sql", {}),
        ("Silver", NOTEBOOKS_DIR / "silver" / "10_rga_susceptibility.sql", {}),
        ("Gold", NOTEBOOKS_DIR / "gold" / "10_rga_h3.sql", {"resolution": "9"}),
    ]
    _run_stages(runner, params, stages)


@pipeline_app.command("ppri")
def pipeline_ppri(
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    skip_fetch: bool = typer.Option(
        False, "--skip-fetch", help="Skip the WFS pull entirely; reuse what's in the volume."
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """End-to-end PPRI pipeline: fetch (approuv + prescrit) → bronze → silver → gold."""
    from catnat.fetch import ppri as ppri_fetch

    runner = WarehouseRunner()
    params = _params_default()

    if not skip_fetch:
        n_request = None if full else 200
        results = ppri_fetch.fetch_and_upload(limit=n_request, force=force)
        for status, (remote, n, cached) in results.items():
            if cached:
                console.print(
                    f"[bold yellow]Fetch[/bold yellow] [{status}] — cache hit at {remote}"
                )
            else:
                console.print(f"[bold]Fetch[/bold] [{status}] — {n} features → {remote}")
            params[f"input_{status}"] = remote
    else:
        suffix = "full" if full else "sample"
        for status in ppri_fetch.LAYERS:
            params[f"input_{status}"] = (
                f"{CONFIG.raw_volume_path}/ppri/ppri_{status}_{suffix}.geojsonl"
            )

    stages = [
        ("Setup", NOTEBOOKS_DIR / "_setup" / "00_create_catalog.sql", {}),
        ("Bronze", NOTEBOOKS_DIR / "bronze" / "20_ppri_communes.sql", {}),
        ("Silver", NOTEBOOKS_DIR / "silver" / "20_ppri_communes.sql", {}),
        ("Gold", NOTEBOOKS_DIR / "gold" / "20_ppri_communes_h3.sql", {"resolution": "9"}),
    ]
    _run_stages(runner, params, stages)


@pipeline_app.command("tri")
def pipeline_tri(
    full: bool = typer.Option(False, "--full", help="Pull the full national dataset."),
    skip_fetch: bool = typer.Option(
        False, "--skip-fetch", help="Skip the WFS pull entirely; reuse what's in the volume."
    ),
    force: bool = typer.Option(False, "--force", help="Re-download even if cached in the volume."),
) -> None:
    """End-to-end TRI pipeline: fetch (11 ALEA_SYNT layers) → bronze → silver → gold.

    Each layer caches in its own file under bronze.raw/tri/; partial fetches
    (some layers permanently 502'ing) still proceed — bronze reads whatever
    landed via a glob.
    """
    from catnat.fetch import tri as tri_fetch

    runner = WarehouseRunner()
    params = _params_default()

    if not skip_fetch:
        n_request = None if full else 30
        results, glob = tri_fetch.fetch_and_upload(limit=n_request, force=force)
        for r in results:
            tag = f"{r.scenario}_{r.intensity}"
            if r.skipped:
                console.print(f"[bold red]Fetch[/bold red] [{tag}] — skipped ({r.error})")
            elif r.cached:
                console.print(f"[bold yellow]Fetch[/bold yellow] [{tag}] — cache hit")
            else:
                console.print(f"[bold]Fetch[/bold] [{tag}] — {r.count} features")
        n_ok = sum(1 for r in results if not r.skipped)
        console.print(f"  {n_ok}/{len(results)} layers ready under {glob}\n")
        params["input_path"] = glob
    else:
        params["input_path"] = tri_fetch.bronze_glob("full" if full else "sample")

    stages = [
        ("Setup", NOTEBOOKS_DIR / "_setup" / "00_create_catalog.sql", {}),
        ("Bronze", NOTEBOOKS_DIR / "bronze" / "30_tri_flood.sql", {}),
        ("Silver", NOTEBOOKS_DIR / "silver" / "30_tri_flood.sql", {}),
        ("Gold", NOTEBOOKS_DIR / "gold" / "30_tri_flood_h3.sql", {"resolution": "9"}),
    ]
    _run_stages(runner, params, stages)
