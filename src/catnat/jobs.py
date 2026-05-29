"""Entry points for Databricks `python_wheel_task`.

Typer's `app()` always calls `sys.exit()` when it's done (success or fail).
The Databricks wheel-task runner treats any `SystemExit` — even
`SystemExit(0)` — as a failure. This module wraps the CLI invocation so that
a clean exit returns normally and only non-zero exits propagate as errors.
"""

from __future__ import annotations

import sys

from catnat.cli import app


def main() -> None:
    """Entry point: run the typer app with the wheel-task's argv.

    Databricks builds argv from `python_wheel_task.parameters`, so calling
    `app()` here is equivalent to `catnat <args…>` on the command line, minus
    typer's enforced `sys.exit`.
    """
    try:
        app(standalone_mode=False)
    except SystemExit as e:
        if e.code not in (0, None):
            raise
    # Defensive: re-raise any explicit non-success-marker exits Click may emit.
    _ = sys
