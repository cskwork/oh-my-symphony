"""SPEC §5.3.1, §6.1 — value coercion, $VAR / ~ expansion, state-key normalization.

These helpers transform raw YAML scalars into the typed shapes the
config builder feeds into the frozen `*Config` dataclasses. They are
deliberately permissive: malformed YAML values fall back to their
documented defaults rather than raising, because the strict-validation
layer (`builder._validated_*`, `preflight.validate_for_dispatch`) is
where loud errors belong.

Every dotted-path name exposed at module top-level is also re-exported
from `symphony.workflow` so test stubbing via `monkeypatch.setattr` keeps
working across the split.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..errors import ConfigValidationError
from .constants import _VAR_PATTERN


def resolve_var_indirection(value: Any) -> Any:
    """§5.3.1, §6.1 — only resolve `$VAR_NAME` form. Empty env -> empty string."""
    if isinstance(value, str):
        m = _VAR_PATTERN.match(value)
        if m:
            return os.environ.get(m.group(1), "")
    return value


def expand_path_value(value: str) -> str:
    """§6.1 — apply ~ then $VAR for path-like fields."""
    expanded = os.path.expanduser(value)
    expanded = os.path.expandvars(expanded)
    return expanded


def _as_int(value: Any, default: int, *, allow_zero: bool = True) -> int:
    if isinstance(value, bool) or value is None:
        return default
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    if not allow_zero and ivalue <= 0:
        return default
    return ivalue


def _as_str_list(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(value, list):
        return default
    out = tuple(item for item in value if isinstance(item, str) and item)
    return out or default


def _as_str(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    return default


def _normalize_state_key(value: str) -> str:
    return value.strip().lower()


def _normalize_state_map(value: Any) -> dict[str, int]:
    """§5.3.5 — keys lowercased, invalid entries dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if isinstance(raw, bool):
            continue
        try:
            ivalue = int(raw)
        except (TypeError, ValueError):
            continue
        if ivalue <= 0:
            continue
        out[key.lower()] = ivalue
    return out


def _normalize_state_description_map(value: Any) -> dict[str, str]:
    """tracker.state_descriptions — keys lowercased, non-string entries dropped."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw in value.items():
        if not isinstance(key, str):
            continue
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        out[key.lower()] = text
    return out


def _resolve_config_path(base_dir: Path, value: str) -> Path:
    resolved = resolve_var_indirection(value) if value.startswith("$") else value
    if not isinstance(resolved, str) or not resolved:
        return base_dir
    path = Path(expand_path_value(resolved))
    if not path.is_absolute():
        return (base_dir / path).resolve()
    return path.resolve()


def _read_prompt_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise ConfigValidationError("prompt file not found", path=str(path)) from exc
    except OSError as exc:
        raise ConfigValidationError(
            "prompt file unreadable", path=str(path), error=str(exc)
        ) from exc
