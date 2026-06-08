"""CLI entry points for quant-framework.

These are the console_scripts entry points referenced in pyproject.toml.
They delegate to the full implementations in scripts/.
"""

from __future__ import annotations

import sys
from pathlib import Path


def run_backtest() -> None:
    """Entry point for `quant-backtest` command."""
    # Ensure the scripts directory is importable
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from run_backtest import main as _main
    _main()


def run_live() -> None:
    """Entry point for `quant-run` command."""
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from run_live import main as _main
    _main()
