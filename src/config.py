"""Persistent configuration loading, validation, and saving."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path
from typing import Any


MIN_BOX_SIZE = 16
MAX_BOX_SIZE = 9999
MIN_OPACITY = 0.10
MAX_OPACITY = 0.85
MIN_SCALE = 0.05
MAX_SCALE = 10.0
DEFAULT_SCALE = 1.0
DEFAULT_HOTKEY = "Win+Alt+S"
DEFAULT_BRIDGE_PORT = 47821
MIN_BRIDGE_PORT = 1024
MAX_BRIDGE_PORT = 65535


class CaptureMode(str, Enum):
    """Supported capture geometry modes."""

    BOX = "box"
    # Future: LASSO = "lasso"


def default_output_dir() -> str:
    """Return the default directory for saved snapshots."""
    return str(Path.home() / "Pictures" / "CharacterSnapshotter")


@dataclass
class AppConfig:
    """Persistent configuration and session state for Character Snapshotter."""

    hotkey: str = DEFAULT_HOTKEY
    output_dir: str = field(default_factory=default_output_dir)
    filename_prefix: str = "capture_"
    default_width: int = 500
    default_height: int = 500
    box_x: int | None = None
    box_y: int | None = None
    box_width: int = 500
    box_height: int = 500
    square_lock: bool = True
    scale: float = DEFAULT_SCALE
    overlay_opacity: float = 0.45
    play_chime: bool = True
    capture_mode: str = CaptureMode.BOX.value
    bridge_enabled: bool = True
    bridge_port: int = DEFAULT_BRIDGE_PORT


def get_config_path() -> Path:
    """Return the path to config.json in the project root."""
    return Path(__file__).resolve().parent.parent / "config.json"


def _coerce_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:  # NaN guard
        return default
    return max(minimum, min(maximum, parsed))


def validate_config(cfg: AppConfig) -> AppConfig:
    """Return a sanitized AppConfig with clamped numeric bounds and valid enums."""
    defaults = AppConfig()

    hotkey = str(cfg.hotkey).strip() if isinstance(cfg.hotkey, str) else ""
    if not hotkey:
        hotkey = DEFAULT_HOTKEY

    output_dir = str(cfg.output_dir).strip() if isinstance(cfg.output_dir, str) else ""
    if not output_dir:
        output_dir = defaults.output_dir

    filename_prefix = (
        str(cfg.filename_prefix) if isinstance(cfg.filename_prefix, str) else defaults.filename_prefix
    )

    valid_modes = {mode.value for mode in CaptureMode}
    capture_mode = cfg.capture_mode if cfg.capture_mode in valid_modes else CaptureMode.BOX.value

    return AppConfig(
        hotkey=hotkey,
        output_dir=output_dir,
        filename_prefix=filename_prefix,
        default_width=_coerce_int(cfg.default_width, defaults.default_width, MIN_BOX_SIZE, MAX_BOX_SIZE),
        default_height=_coerce_int(cfg.default_height, defaults.default_height, MIN_BOX_SIZE, MAX_BOX_SIZE),
        box_x=_coerce_optional_int(cfg.box_x),
        box_y=_coerce_optional_int(cfg.box_y),
        box_width=_coerce_int(cfg.box_width, defaults.box_width, MIN_BOX_SIZE, MAX_BOX_SIZE),
        box_height=_coerce_int(cfg.box_height, defaults.box_height, MIN_BOX_SIZE, MAX_BOX_SIZE),
        square_lock=bool(cfg.square_lock),
        scale=_coerce_float(cfg.scale, defaults.scale, MIN_SCALE, MAX_SCALE),
        overlay_opacity=_coerce_float(cfg.overlay_opacity, defaults.overlay_opacity, MIN_OPACITY, MAX_OPACITY),
        play_chime=bool(cfg.play_chime),
        capture_mode=capture_mode,
        bridge_enabled=bool(cfg.bridge_enabled),
        bridge_port=_coerce_int(cfg.bridge_port, defaults.bridge_port, MIN_BRIDGE_PORT, MAX_BRIDGE_PORT),
    )


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from JSON, falling back cleanly on missing or corrupt files."""
    target = path if path is not None else get_config_path()
    if not target.exists():
        return AppConfig()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return AppConfig()

    if not isinstance(raw, dict):
        return AppConfig()

    known_fields = {f.name for f in fields(AppConfig)}
    filtered = {k: v for k, v in raw.items() if k in known_fields}
    cfg = AppConfig(**filtered)
    return validate_config(cfg)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Persist validated configuration atomically to disk."""
    target = path if path is not None else get_config_path()
    validated = validate_config(cfg)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(asdict(validated), indent=2), encoding="utf-8")
    tmp_path.replace(target)
