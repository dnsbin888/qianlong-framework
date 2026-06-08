"""YAML configuration loader.

Reads a YAML file and returns a validated FrameworkConfig instance.
Supports environment variable interpolation for secrets (e.g. ${TUSHARE_TOKEN}).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from quant_framework.config.config import FrameworkConfig

# Pattern for ${VAR_NAME} or ${VAR_NAME:-default} in YAML values
_ENV_VAR_RE = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def _interpolate_env(value: Any) -> Any:
    """Recursively replace ${VAR} / ${VAR:-default} in string values."""
    if isinstance(value, str):
        def _replacer(m: re.Match[str]) -> str:
            var = m.group(1)
            default = m.group(2)
            return os.environ.get(var, default if default is not None else m.group(0))

        return _ENV_VAR_RE.sub(_replacer, value)
    if isinstance(value, dict):
        return {k: _interpolate_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_interpolate_env(item) for item in value]
    return value


def load_config(path: str | Path) -> FrameworkConfig:
    """Load a YAML configuration file with validation.

    Args:
        path: Path to the YAML file.

    Returns:
        A fully validated FrameworkConfig instance.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the YAML is malformed or validation fails.

    Example:
        cfg = load_config("config/default.yaml")
        if cfg.risk.enabled:
            print(f"Max drawdown limit: {cfg.risk.max_drawdown_pct:.0%}")
    """
    path = Path(path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    # Interpolate environment variables
    raw = _interpolate_env(raw)

    # Build validated config
    cfg = FrameworkConfig(**raw)

    # Store the config directory for later path resolution
    cfg._config_dir = path.parent

    return cfg
