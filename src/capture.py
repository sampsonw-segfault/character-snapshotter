"""Multi-monitor region capture, proportional Lanczos scaling, and timestamped PNG output."""

from __future__ import annotations

import sys
import threading
from datetime import datetime
from pathlib import Path

import mss
from PIL import Image

from src.geometry import PhysicalRect, compute_proportional_output_size


def _sanitize_prefix(prefix: str) -> str:
    cleaned = "".join(ch for ch in prefix if ch not in '\\/:*?"<>|')
    return cleaned


def generate_timestamp_path(
    output_dir: Path,
    prefix: str = "capture_",
    now: datetime | None = None,
) -> Path:
    """Create output_dir if needed and return a collision-safe timestamped PNG path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = _sanitize_prefix(prefix)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"{safe_prefix}{stamp}"

    candidate = output_dir / f"{stem}.png"
    counter = 1
    while candidate.exists():
        candidate = output_dir / f"{stem}_{counter}.png"
        counter += 1
    return candidate


def process_captured_image(
    img: Image.Image,
    scale_enabled: bool,
    anchor_axis: str,
    target_px: int,
    alpha_mask: Image.Image | None = None,
) -> Image.Image:
    """Apply optional alpha mask and strictly proportional Lanczos scaling."""
    if alpha_mask is not None:
        prepared = img.convert("RGBA")
        mask = alpha_mask.convert("L")
        if mask.size != prepared.size:
            mask = mask.resize(prepared.size, Image.Resampling.LANCZOS)
        prepared.putalpha(mask)
    else:
        prepared = img.convert("RGB")

    out_w, out_h = compute_proportional_output_size(
        prepared.width,
        prepared.height,
        scale_enabled=scale_enabled,
        anchor_axis=anchor_axis,
        target_px=target_px,
    )
    if (out_w, out_h) != prepared.size:
        prepared = prepared.resize((out_w, out_h), Image.Resampling.LANCZOS)
    return prepared


def capture_region_to_file(
    rect: PhysicalRect,
    output_dir: Path,
    prefix: str = "capture_",
    scale_enabled: bool = False,
    anchor_axis: str = "width",
    target_px: int = 500,
    alpha_mask: Image.Image | None = None,
) -> Path:
    """Grab physical screen pixels in rect, process them, and save a timestamped PNG."""
    monitor = {
        "left": int(rect.x),
        "top": int(rect.y),
        "width": max(1, int(rect.width)),
        "height": max(1, int(rect.height)),
    }
    with mss.MSS() as sct:
        sct_img = sct.grab(monitor)

    raw_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
    final_img = process_captured_image(
        raw_img,
        scale_enabled=scale_enabled,
        anchor_axis=anchor_axis,
        target_px=target_px,
        alpha_mask=alpha_mask,
    )
    out_path = generate_timestamp_path(output_dir, prefix=prefix)
    final_img.save(out_path, format="PNG")
    return out_path


def play_capture_chime() -> None:
    """Play a brief non-blocking audio chime after saving a snapshot."""
    if sys.platform != "win32":
        return

    def _beep() -> None:
        try:
            import winsound

            winsound.Beep(1150, 110)
        except Exception:
            pass

    threading.Thread(target=_beep, daemon=True).start()
