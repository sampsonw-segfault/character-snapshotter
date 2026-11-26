"""Multi-monitor region capture, proportional Lanczos scaling, and animated WebP/GIF output."""

from __future__ import annotations

import ctypes
import shutil
import struct
import sys
import tempfile
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable

import mss
from PIL import Image, _webp

from src.geometry import PhysicalRect, compute_scaled_output_size


DEFAULT_MAX_RAM_BUFFER_BYTES = 96_000_000
DEFAULT_MAX_RECORDING_BUFFER_BYTES = DEFAULT_MAX_RAM_BUFFER_BYTES
DEFAULT_MAX_SPOOL_DISK_BYTES = 6_000_000_000
DEFAULT_MIN_FREE_DISK_BYTES = 1_000_000_000
_SPOOL_HEADER = struct.Struct("<dIII")
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


def _read_exact(fp: object, nbytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = nbytes
    while remaining > 0:
        chunk = fp.read(remaining)  # type: ignore[attr-defined]
        if not chunk:
            raise EOFError("Unexpected end of temporary recording spool file.")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class _FrameSpool:
    """Thread-safe FIFO keeping frames in RAM up to a budget, spilling overflow to a temp file."""

    def __init__(
        self,
        max_ram_bytes: int = DEFAULT_MAX_RAM_BUFFER_BYTES,
        max_disk_bytes: int = DEFAULT_MAX_SPOOL_DISK_BYTES,
        min_free_disk_bytes: int = DEFAULT_MIN_FREE_DISK_BYTES,
    ) -> None:
        self._max_ram_bytes = max(0, int(max_ram_bytes))
        self._max_disk_bytes = max(1_000_000, int(max_disk_bytes))
        self._min_free_disk_bytes = max(0, int(min_free_disk_bytes))

        self._cond = threading.Condition()
        self._ram_queue: deque[tuple[Image.Image, float]] = deque()
        self._ram_bytes = 0
        self._total_pushed = 0
        self._disk_pending = 0
        self._disk_written_total = 0
        self._disk_spill_events = 0

        self._spool_path: Path | None = None
        self._write_fp: object | None = None
        self._read_fp: object | None = None
        self._write_pos = 0
        self._read_pos = 0
        self._disk_bytes = 0
        self._next_disk_check_bytes = 0

        self._eof = False
        self._closed = False

    @property
    def total_pushed(self) -> int:
        with self._cond:
            return self._total_pushed

    @property
    def disk_spill_events(self) -> int:
        with self._cond:
            return self._disk_spill_events

    @property
    def spool_path(self) -> Path | None:
        with self._cond:
            return self._spool_path

    def _ensure_spool_open_locked(self) -> None:
        if self._write_fp is not None:
            return
        tmp = tempfile.NamedTemporaryFile(prefix="csnap_spool_", suffix=".bin", delete=False)
        path = Path(tmp.name)
        tmp.close()
        self._spool_path = path
        self._write_fp = path.open("wb")
        self._read_fp = path.open("rb")
        self._write_pos = 0
        self._read_pos = 0
        self._disk_bytes = 0
        self._next_disk_check_bytes = 0

    def _reset_spool_locked(self) -> None:
        if self._write_fp is not None and self._read_fp is not None:
            self._write_fp.seek(0)  # type: ignore[attr-defined]
            self._write_fp.truncate(0)  # type: ignore[attr-defined]
            self._write_fp.flush()  # type: ignore[attr-defined]
            self._read_fp.seek(0)  # type: ignore[attr-defined]
        self._write_pos = 0
        self._read_pos = 0
        self._disk_bytes = 0
        self._next_disk_check_bytes = 0

    def _has_disk_headroom_locked(self, needed: int) -> bool:
        if self._disk_bytes + needed > self._max_disk_bytes:
            return False
        if self._min_free_disk_bytes > 0 and self._disk_bytes >= self._next_disk_check_bytes:
            assert self._spool_path is not None
            free = shutil.disk_usage(self._spool_path.parent).free
            if free - needed < self._min_free_disk_bytes:
                return False
            self._next_disk_check_bytes = self._disk_bytes + 100_000_000
        return True

    def push(self, img: Image.Image, timestamp: float) -> bool:
        """Enqueue a frame in RAM or spill to disk if RAM queue is full and not caught up."""
        rgb_img = img if img.mode == "RGB" else img.convert("RGB")
        est_bytes = rgb_img.width * rgb_img.height * 3

        with self._cond:
            if self._closed:
                return False
            if self._disk_pending == 0 and (
                self._ram_bytes + est_bytes <= self._max_ram_bytes
                or (self._total_pushed == 0 and self._max_ram_bytes >= est_bytes)
            ):
                self._ram_queue.append((rgb_img, float(timestamp)))
                self._ram_bytes += est_bytes
                self._total_pushed += 1
                self._cond.notify_all()
                return True

            self._ensure_spool_open_locked()
            payload = rgb_img.tobytes()
            record_bytes = _SPOOL_HEADER.size + len(payload)
            if not self._has_disk_headroom_locked(record_bytes):
                return False

            header = _SPOOL_HEADER.pack(
                float(timestamp),
                rgb_img.width,
                rgb_img.height,
                len(payload),
            )
            assert self._write_fp is not None
            self._write_fp.seek(self._write_pos)  # type: ignore[attr-defined]
            self._write_fp.write(header)  # type: ignore[attr-defined]
            self._write_fp.write(payload)  # type: ignore[attr-defined]
            self._write_fp.flush()  # type: ignore[attr-defined]
            self._write_pos += record_bytes
            self._disk_bytes += record_bytes
            self._disk_pending += 1
            self._disk_written_total += 1
            if self._disk_pending == 1:
                self._disk_spill_events += 1
            self._total_pushed += 1
            self._cond.notify_all()
            return True

    def pop(self, wait: bool = True) -> tuple[Image.Image, float] | None:
        """Pop the oldest frame in strict FIFO order across RAM queue and disk spool."""
        with self._cond:
            while not self._ram_queue and self._disk_pending == 0:
                if not wait or self._closed or self._eof:
                    return None
                self._cond.wait(timeout=0.15)

            if self._ram_queue:
                img, ts = self._ram_queue.popleft()
                self._ram_bytes = max(0, self._ram_bytes - img.width * img.height * 3)
                return (img, ts)

            assert self._read_fp is not None
            self._read_fp.seek(self._read_pos)  # type: ignore[attr-defined]
            hdr_bytes = _read_exact(self._read_fp, _SPOOL_HEADER.size)
            ts, width, height, payload_len = _SPOOL_HEADER.unpack(hdr_bytes)
            payload = _read_exact(self._read_fp, payload_len)
            self._read_pos += _SPOOL_HEADER.size + payload_len
            self._disk_pending -= 1
            if self._disk_pending == 0:
                self._reset_spool_locked()
            img = Image.frombytes("RGB", (width, height), payload)
            return (img, ts)

    def mark_eof(self) -> None:
        with self._cond:
            self._eof = True
            self._cond.notify_all()

    def cleanup(self) -> None:
        with self._cond:
            self._closed = True
            self._ram_queue.clear()
            self._ram_bytes = 0
            self._disk_pending = 0
            write_fp = self._write_fp
            read_fp = self._read_fp
            spool_path = self._spool_path
            self._write_fp = None
            self._read_fp = None
            self._spool_path = None
            self._cond.notify_all()

        for fp in (read_fp, write_fp):
            if fp is not None:
                try:
                    fp.close()  # type: ignore[attr-defined]
                except OSError:
                    pass
        if spool_path is not None:
            try:
                spool_path.unlink(missing_ok=True)
            except OSError:
                pass


class RegionRecorder:
    """Captures physical screen frames with bounded RAM queue, disk overflow, and live WebP encoding."""

    def __init__(
        self,
        rect: PhysicalRect,
        scale: float = 1.0,
        fps: int = 30,
        include_cursor: bool = True,
        video_format: str = "webp",
        live_encode: bool = False,
        max_ram_bytes: int = DEFAULT_MAX_RAM_BUFFER_BYTES,
        max_disk_bytes: int = DEFAULT_MAX_SPOOL_DISK_BYTES,
        on_limit_reached: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._rect = rect
        self._scale = float(scale)
        self._fps = max(1, int(fps))
        self._include_cursor = bool(include_cursor)
        self._video_format = video_format.strip().lower()
        if self._video_format not in ("webp", "gif"):
            self._video_format = "webp"
        self._live_webp = bool(live_encode) and self._video_format == "webp"
        self._on_limit_reached = on_limit_reached
        self._on_error = on_error

        self._spool = _FrameSpool(
            max_ram_bytes=max_ram_bytes,
            max_disk_bytes=max_disk_bytes,
        )
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._capture_thread: threading.Thread | None = None
        self._encode_thread: threading.Thread | None = None

        self._start_time = 0.0
        self._end_time = 0.0
        self._error: str | None = None
        self._canceled = False

        self._webp_encoder: object | None = None
        self._webp_start_ts = 0.0
        self._webp_last_ts_ms = 0
        self._encoded_count = 0
        self._progress_cb: Callable[[int, int], None] | None = None

    @property
    def is_running(self) -> bool:
        t = self._capture_thread
        return t is not None and t.is_alive() and not self._stop_event.is_set()

    @property
    def elapsed_seconds(self) -> float:
        if self._start_time <= 0.0:
            return 0.0
        end = self._end_time if self._end_time > 0.0 else time.perf_counter()
        return max(0.0, end - self._start_time)

    @property
    def frame_count(self) -> int:
        return self._spool.total_pushed

    @property
    def disk_spill_events(self) -> int:
        return self._spool.disk_spill_events

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._error

    def start(self) -> None:
        """Start background screen capture (and live WebP encoder thread when enabled)."""
        if self._capture_thread is not None:
            return
        self._stop_event.clear()
        self._canceled = False
        self._start_time = time.perf_counter()
        self._end_time = 0.0

        if self._live_webp:
            self._encode_thread = threading.Thread(
                target=self._webp_encode_loop,
                name="SnapshotterWebPEncoder",
                daemon=True,
            )
            self._encode_thread.start()

        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="SnapshotterRegionRecorder",
            daemon=True,
        )
        self._capture_thread.start()

    def _stop_capture_thread(self) -> float:
        self._stop_event.set()
        cap_t = self._capture_thread
        if cap_t is not None and cap_t.is_alive():
            cap_t.join(timeout=5.0)
        self._capture_thread = None
        self._spool.mark_eof()
        return self._end_time if self._end_time > 0.0 else time.perf_counter()

    def stop(self) -> tuple[list[Image.Image], list[float], float]:
        """Stop capturing, drain buffered frames into memory, and return (frames, timestamps, end_time)."""
        end_time = self._stop_capture_thread()
        enc_t = self._encode_thread
        if enc_t is not None and enc_t.is_alive():
            self._spool.cleanup()
            enc_t.join(timeout=5.0)
        self._encode_thread = None
        self._webp_encoder = None

        frames: list[Image.Image] = []
        timestamps: list[float] = []
        try:
            while True:
                item = self._spool.pop(wait=False)
                if item is None:
                    break
                img, ts = item
                frames.append(img)
                timestamps.append(ts)
        finally:
            self._spool.cleanup()
        return (frames, timestamps, end_time)

    def finish_and_save(
        self,
        output_dir: Path,
        prefix: str = "capture_",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Stop capture, flush any spilled/queued frames, and write the final WebP or GIF file."""
        with self._lock:
            self._progress_cb = on_progress
        try:
            end_time = self._stop_capture_thread()

            if self._live_webp:
                enc_t = self._encode_thread
                if enc_t is not None:
                    while enc_t.is_alive():
                        enc_t.join(timeout=0.1)
                self._encode_thread = None

                err = self.last_error
                if err is not None:
                    raise RuntimeError(err)

                enc = self._webp_encoder
                if enc is None or self._encoded_count == 0:
                    raise RuntimeError("No video frames were captured.")

                if on_progress is not None:
                    on_progress(self._encoded_count, max(self._encoded_count, self._spool.total_pushed))

                raw_end_ms = round((end_time - self._webp_start_ts) * 1000.0)
                min_last = max(1, round(1000.0 / self._fps))
                final_ts_ms = max(self._webp_last_ts_ms + min_last, raw_end_ms)
                enc.add(None, final_ts_ms, False, 85, 100, 0)  # type: ignore[attr-defined]
                data = enc.assemble(b"", b"", b"")  # type: ignore[attr-defined]
                if data is None:
                    raise RuntimeError("WebP animation encoder returned no data.")

                out_path = generate_timestamp_path(output_dir, prefix=prefix, extension="webp")
                out_path.write_bytes(data)
                return out_path

            # Fallback / GIF path: drain spool cleanly while compacting to palette frames
            err = self.last_error
            if err is not None:
                raise RuntimeError(err)

            total = max(1, self._spool.total_pushed)
            frames: list[Image.Image] = []
            timestamps: list[float] = []
            idx = 0
            while True:
                item = self._spool.pop(wait=False)
                if item is None:
                    break
                img, ts = item
                if self._video_format == "gif":
                    img = img.convert("P", palette=Image.Palette.ADAPTIVE)
                frames.append(img)
                timestamps.append(ts)
                idx += 1
                if on_progress is not None:
                    on_progress(idx, total)

            if not frames:
                raise RuntimeError("No video frames were captured.")

            return save_recorded_frames(
                frames=frames,
                timestamps=timestamps,
                end_time=end_time,
                output_dir=output_dir,
                prefix=prefix,
                video_format=self._video_format,
                fps=self._fps,
            )
        finally:
            with self._lock:
                self._progress_cb = None
            self._webp_encoder = None
            self._spool.cleanup()

    def cancel(self) -> None:
        """Stop capture + encoding and discard all buffered or spilled frames."""
        self._canceled = True
        self._stop_event.set()
        self._spool.cleanup()
        for t in (self._capture_thread, self._encode_thread):
            if t is not None and t.is_alive():
                t.join(timeout=5.0)
        self._capture_thread = None
        self._encode_thread = None
        self._webp_encoder = None

    def _webp_encode_loop(self) -> None:
        try:
            while True:
                if self._canceled:
                    break
                item = self._spool.pop(wait=True)
                if item is None:
                    break
                img, captured_at = item
                if self._canceled:
                    break

                if self._webp_encoder is None:
                    self._webp_start_ts = captured_at
                    ts_ms = 0
                    self._webp_encoder = _webp.WebPAnimEncoder(
                        img.size,
                        0,
                        0,
                        False,
                        3,
                        5,
                        False,
                        False,
                    )
                else:
                    raw_ms = round((captured_at - self._webp_start_ts) * 1000.0)
                    ts_ms = max(self._webp_last_ts_ms + 1, raw_ms)

                self._webp_encoder.add(img.getim(), ts_ms, False, 85, 100, 0)  # type: ignore[attr-defined]
                self._webp_last_ts_ms = ts_ms
                self._encoded_count += 1

                cb = self._progress_cb
                if cb is not None:
                    cb(self._encoded_count, max(self._encoded_count, self._spool.total_pushed))
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                if self._error is None:
                    self._error = str(exc)
            self._stop_event.set()
            self._spool.cleanup()
            if self._on_error is not None and not self._canceled:
                self._on_error(str(exc))

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

                    if self._canceled:
                        break
                    pushed = self._spool.push(processed, captured_at)
                    if not pushed:
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
                if self._error is None:
                    self._error = str(exc)
            if self._on_error is not None and not self._canceled:
                self._on_error(str(exc))
        finally:
            if self._end_time <= 0.0:
                self._end_time = time.perf_counter()
            self._spool.mark_eof()


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
