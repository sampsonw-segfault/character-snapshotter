"""Multi-monitor region capture, proportional Lanczos scaling, and animated WebP/GIF output."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import mss
from PIL import Image

from src.geometry import PhysicalRect, compute_scaled_output_size


DEFAULT_MAX_RECORDING_BUFFER_BYTES = 1_500_000_000
_CURSOR_SHOWING = 0x00000001
_DI_NORMAL = 0x0003


if sys.platform == "win32":
    import ctypes.wintypes as wintypes

    class _CURSORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hCursor", wintypes.HCURSOR),
            ("ptScreenPos", wintypes.POINT),
        ]

    class _ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL),
            ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD),
            ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    class _BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", wintypes.LONG),
            ("bmWidth", wintypes.LONG),
            ("bmHeight", wintypes.LONG),
            ("bmWidthBytes", wintypes.LONG),
            ("bmPlanes", wintypes.WORD),
            ("bmBitsPixel", wintypes.WORD),
            ("bmBits", wintypes.LPVOID),
        ]

    class _BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    _USER32 = ctypes.windll.user32
    _GDI32 = ctypes.windll.gdi32

    _USER32.GetCursorInfo.argtypes = [ctypes.POINTER(_CURSORINFO)]
    _USER32.GetCursorInfo.restype = wintypes.BOOL
    _USER32.GetIconInfo.argtypes = [wintypes.HCURSOR, ctypes.POINTER(_ICONINFO)]
    _USER32.GetIconInfo.restype = wintypes.BOOL
    _USER32.DrawIconEx.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HCURSOR,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.HBRUSH,
        wintypes.UINT,
    ]
    _USER32.DrawIconEx.restype = wintypes.BOOL

    _GDI32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    _GDI32.CreateCompatibleDC.restype = wintypes.HDC
    _GDI32.DeleteDC.argtypes = [wintypes.HDC]
    _GDI32.DeleteDC.restype = wintypes.BOOL
    _GDI32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.POINTER(_BITMAPINFOHEADER),
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    _GDI32.CreateDIBSection.restype = wintypes.HBITMAP
    _GDI32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    _GDI32.SelectObject.restype = wintypes.HGDIOBJ
    _GDI32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    _GDI32.DeleteObject.restype = wintypes.BOOL
    _GDI32.GetObjectW.argtypes = [wintypes.HGDIOBJ, ctypes.c_int, ctypes.c_void_p]
    _GDI32.GetObjectW.restype = ctypes.c_int


def _query_win32_cursor_state() -> tuple[int, int, int] | None:
    """Return (screen_x, screen_y, hcursor) when a visible Windows cursor is active."""
    if sys.platform != "win32":
        return None
    ci = _CURSORINFO()
    ci.cbSize = ctypes.sizeof(_CURSORINFO)
    if not _USER32.GetCursorInfo(ctypes.byref(ci)):
        return None
    if not (ci.flags & _CURSOR_SHOWING) or not ci.hCursor:
        return None
    return (int(ci.ptScreenPos.x), int(ci.ptScreenPos.y), int(ci.hCursor))


def overlay_cursor_on_image(img: Image.Image, rect: PhysicalRect) -> Image.Image:
    """Composite the active Win32 mouse cursor onto img at native physical pixels if inside rect."""
    if sys.platform != "win32":
        return img
    state = _query_win32_cursor_state()
    if state is None:
        return img
    screen_x, screen_y, hcursor = state
    cursor_handle = wintypes.HCURSOR(hcursor)

    ii = _ICONINFO()
    if not _USER32.GetIconInfo(cursor_handle, ctypes.byref(ii)):
        return img

    try:
        target_bmp = ii.hbmColor if ii.hbmColor else ii.hbmMask
        if not target_bmp:
            return img
        bm = _BITMAP()
        if not _GDI32.GetObjectW(target_bmp, ctypes.sizeof(bm), ctypes.byref(bm)):
            return img
        icon_w = int(bm.bmWidth)
        icon_h = int(bm.bmHeight if ii.hbmColor else bm.bmHeight // 2)
        if icon_w <= 0 or icon_h <= 0:
            return img
        local_x = screen_x - int(ii.xHotspot) - int(rect.x)
        local_y = screen_y - int(ii.yHotspot) - int(rect.y)
    finally:
        if ii.hbmMask:
            _GDI32.DeleteObject(ii.hbmMask)
        if ii.hbmColor:
            _GDI32.DeleteObject(ii.hbmColor)

    src_x1 = max(0, local_x)
    src_y1 = max(0, local_y)
    src_x2 = min(img.width, local_x + icon_w)
    src_y2 = min(img.height, local_y + icon_h)
    patch_w = src_x2 - src_x1
    patch_h = src_y2 - src_y1
    if patch_w <= 0 or patch_h <= 0:
        return img

    patch = img.crop((src_x1, src_y1, src_x2, src_y2)).convert("RGB")
    raw_in = patch.tobytes("raw", "BGRX")

    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = patch_w
    bmi.biHeight = -patch_h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    hdc = _GDI32.CreateCompatibleDC(None)
    if not hdc:
        return img

    hbmp = None
    old_obj = None
    try:
        bits = ctypes.c_void_p()
        hbmp = _GDI32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not hbmp or not bits.value:
            return img
        old_obj = _GDI32.SelectObject(hdc, hbmp)
        ctypes.memmove(bits.value, raw_in, len(raw_in))
        _USER32.DrawIconEx(
            hdc,
            local_x - src_x1,
            local_y - src_y1,
            cursor_handle,
            icon_w,
            icon_h,
            0,
            None,
            _DI_NORMAL,
        )
        raw_out = ctypes.string_at(bits.value, len(raw_in))
        updated_patch = Image.frombytes("RGB", (patch_w, patch_h), raw_out, "raw", "BGRX")
        img.paste(updated_patch, (src_x1, src_y1))
    finally:
        if old_obj:
            _GDI32.SelectObject(hdc, old_obj)
        if hbmp:
            _GDI32.DeleteObject(hbmp)
        _GDI32.DeleteDC(hdc)

    return img


def _sanitize_prefix(prefix: str) -> str:
    cleaned = "".join(ch for ch in prefix if ch not in '\\/:*?"<>|')
    return cleaned


def generate_timestamp_path(
    output_dir: Path,
    prefix: str = "capture_",
    now: datetime | None = None,
    extension: str = "png",
) -> Path:
    """Create output_dir if needed and return a collision-safe timestamped output path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_prefix = _sanitize_prefix(prefix)
    ext = extension.lstrip(".").lower() or "png"
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"{safe_prefix}{stamp}"

    candidate = output_dir / f"{stem}.{ext}"
    counter = 1
    while candidate.exists():
        candidate = output_dir / f"{stem}_{counter}.{ext}"
        counter += 1
    return candidate


def process_captured_image(
    img: Image.Image,
    scale: float = 1.0,
    alpha_mask: Image.Image | None = None,
) -> Image.Image:
    """Apply optional alpha mask and uniform float Lanczos scaling."""
    if alpha_mask is not None:
        prepared = img.convert("RGBA")
        mask = alpha_mask.convert("L")
        if mask.size != prepared.size:
            mask = mask.resize(prepared.size, Image.Resampling.LANCZOS)
        prepared.putalpha(mask)
    else:
        prepared = img.convert("RGB")

    out_w, out_h = compute_scaled_output_size(
        prepared.width,
        prepared.height,
        scale=scale,
    )
    if (out_w, out_h) != prepared.size:
        prepared = prepared.resize((out_w, out_h), Image.Resampling.LANCZOS)
    return prepared


def capture_region_to_file(
    rect: PhysicalRect,
    output_dir: Path,
    prefix: str = "capture_",
    scale: float = 1.0,
    alpha_mask: Image.Image | None = None,
    include_cursor: bool = False,
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
    if include_cursor:
        raw_img = overlay_cursor_on_image(raw_img, rect)
    final_img = process_captured_image(
        raw_img,
        scale=scale,
        alpha_mask=alpha_mask,
    )
    out_path = generate_timestamp_path(output_dir, prefix=prefix, extension="png")
    final_img.save(out_path, format="PNG")
    return out_path


def compute_frame_durations_ms(
    timestamps: list[float],
    end_time: float,
    video_format: str = "webp",
    fps: int = 30,
) -> list[int]:
    """Compute per-frame durations in ms using cumulative quantization to prevent timing drift."""
    count = len(timestamps)
    if count == 0:
        return []

    fmt = video_format.strip().lower()
    is_gif = fmt == "gif"
    step = 10 if is_gif else 1
    min_dur = 20 if is_gif else 1
    fallback_dur = max(20 if is_gif else 16, round(1000.0 / max(1, int(fps))))

    if count == 1:
        total_ms = max(0.0, (end_time - timestamps[0]) * 1000.0)
        if total_ms < fallback_dur:
            return [fallback_dur]
        quant = round(total_ms / step) * step
        return [max(min_dur, quant)]

    t0 = timestamps[0]
    prev_ms = 0
    durations: list[int] = []
    for idx in range(count):
        next_t = timestamps[idx + 1] if idx + 1 < count else end_time
        elapsed_ms = max(0.0, (next_t - t0) * 1000.0)
        if idx == count - 1 and elapsed_ms <= prev_ms:
            dur = fallback_dur
        else:
            quant_end = round(elapsed_ms / step) * step
            dur = max(min_dur, quant_end - prev_ms)
        prev_ms += dur
        durations.append(int(dur))
    return durations


def save_recorded_frames(
    frames: list[Image.Image],
    timestamps: list[float],
    end_time: float,
    output_dir: Path,
    prefix: str = "capture_",
    video_format: str = "webp",
    fps: int = 30,
) -> Path:
    """Encode captured frames into an animated WebP or GIF file with timestamped filename."""
    if not frames:
        raise ValueError("Cannot save recording: no frames were captured.")

    fmt = video_format.strip().lower()
    if fmt not in ("webp", "gif"):
        fmt = "webp"

    out_path = generate_timestamp_path(output_dir, prefix=prefix, extension=fmt)
    durations = compute_frame_durations_ms(
        timestamps=timestamps,
        end_time=end_time,
        video_format=fmt,
        fps=fps,
    )

    if fmt == "webp":
        frames[0].save(
            out_path,
            format="WEBP",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            quality=85,
            method=0,
        )
    else:
        frames[0].save(
            out_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=False,
        )
    return out_path


class RegionRecorder:
    """Captures physical screen frames in a locked region on a background thread."""

    def __init__(
        self,
        rect: PhysicalRect,
        scale: float = 1.0,
        fps: int = 30,
        include_cursor: bool = True,
        max_buffer_bytes: int = DEFAULT_MAX_RECORDING_BUFFER_BYTES,
        on_limit_reached: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._rect = rect
        self._scale = float(scale)
        self._fps = max(1, int(fps))
        self._include_cursor = bool(include_cursor)
        self._max_buffer_bytes = max(1_000_000, int(max_buffer_bytes))
        self._on_limit_reached = on_limit_reached
        self._on_error = on_error

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames: list[Image.Image] = []
        self._timestamps: list[float] = []
        self._buffered_bytes = 0
        self._start_time = 0.0
        self._end_time = 0.0
        self._error: str | None = None
        self._canceled = False

    @property
    def is_running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive() and not self._stop_event.is_set()

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time <= 0.0:
            return 0.0
        end = self._end_time if self._end_time > 0.0 else time.perf_counter()
        return max(0.0, end - self._start_time)

    @property
    def frame_count(self) -> int:
        with self._lock:
            return len(self._frames)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._error

    def start(self) -> None:
        """Start the background screen-grab loop immediately."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._canceled = False
        self._start_time = time.perf_counter()
        self._end_time = 0.0
        self._thread = threading.Thread(
            target=self._capture_loop,
            name="SnapshotterRegionRecorder",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> tuple[list[Image.Image], list[float], float]:
        """Stop capturing, join the worker thread, and return (frames, timestamps, end_time)."""
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        end_time = self._end_time if self._end_time > 0.0 else time.perf_counter()
        with self._lock:
            frames = list(self._frames)
            timestamps = list(self._timestamps)
            self._frames.clear()
            self._timestamps.clear()
            self._buffered_bytes = 0
        return (frames, timestamps, end_time)

    def cancel(self) -> None:
        """Stop capturing and discard all buffered frames."""
        self._canceled = True
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._thread = None
        with self._lock:
            self._frames.clear()
            self._timestamps.clear()
            self._buffered_bytes = 0

    def _capture_loop(self) -> None:
        monitor = {
            "left": int(self._rect.x),
            "top": int(self._rect.y),
            "width": max(1, int(self._rect.width)),
            "height": max(1, int(self._rect.height)),
        }
        interval = 1.0 / self._fps
        frame_idx = 0
        start_t = self._start_time

        try:
            with mss.MSS() as sct:
                while not self._stop_event.is_set():
                    captured_at = time.perf_counter()
                    sct_img = sct.grab(monitor)
                    raw_img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                    if self._include_cursor:
                        raw_img = overlay_cursor_on_image(raw_img, self._rect)
                    processed = process_captured_image(raw_img, scale=self._scale)
                    est_bytes = processed.width * processed.height * len(processed.getbands())

                    limit_hit = False
                    with self._lock:
                        if self._canceled:
                            break
                        if self._frames and (self._buffered_bytes + est_bytes > self._max_buffer_bytes):
                            limit_hit = True
                        else:
                            self._frames.append(processed)
                            self._timestamps.append(captured_at)
                            self._buffered_bytes += est_bytes

                    if limit_hit:
                        self._end_time = time.perf_counter()
                        self._stop_event.set()
                        if self._on_limit_reached is not None and not self._canceled:
                            self._on_limit_reached()
                        break

                    frame_idx += 1
                    next_deadline = start_t + frame_idx * interval
                    now = time.perf_counter()
                    wait_s = next_deadline - now
                    if wait_s < -interval:
                        # Reset pacing after a stall instead of bursting backlog frames.
                        start_t = now
                        frame_idx = 0
                    elif wait_s > 0.0:
                        self._stop_event.wait(timeout=wait_s)
        except Exception as exc:  # noqa: BLE001
            self._end_time = time.perf_counter()
            with self._lock:
                self._error = str(exc)
            if self._on_error is not None and not self._canceled:
                self._on_error(str(exc))
        finally:
            if self._end_time <= 0.0:
                self._end_time = time.perf_counter()


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
