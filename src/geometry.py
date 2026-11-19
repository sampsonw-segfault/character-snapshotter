"""Multi-monitor physical geometry, coordinate mapping, scaling, and box resize math."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

if sys.platform == "win32":
    import ctypes.wintypes as wintypes


DEFAULT_MIN_SIZE = 16


@dataclass(frozen=True)
class PhysicalRect:
    """Axis-aligned rectangle in Win32 physical virtual-desktop pixel coordinates."""

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2

    def contains_point(self, px: int, py: int) -> bool:
        return self.x <= px < self.right and self.y <= py < self.bottom

    def intersects(self, other: PhysicalRect) -> bool:
        return (
            self.x < other.right
            and self.right > other.x
            and self.y < other.bottom
            and self.bottom > other.y
        )

    def translated(self, dx: int, dy: int) -> PhysicalRect:
        return PhysicalRect(self.x + dx, self.y + dy, self.width, self.height)

    def clamped_to(self, bounds: PhysicalRect, min_size: int = DEFAULT_MIN_SIZE) -> PhysicalRect:
        """Clamp dimensions and origin so this rect lies completely inside bounds."""
        w = max(min_size, min(self.width, bounds.width))
        h = max(min_size, min(self.height, bounds.height))
        max_x = bounds.right - w
        max_y = bounds.bottom - h
        x = max(bounds.x, min(self.x, max_x))
        y = max(bounds.y, min(self.y, max_y))
        return PhysicalRect(x, y, w, h)


@dataclass(frozen=True)
class ScreenMapping:
    """Affine bridge between Win32 physical screen pixels and Qt logical widget pixels."""

    device_name: str
    physical: PhysicalRect
    logical_x: int
    logical_y: int
    logical_w: int
    logical_h: int
    is_primary: bool = False

    def physical_to_local(self, px: float, py: float) -> tuple[float, float]:
        """Convert physical virtual-desktop coords to widget-local logical coords."""
        lx = (px - self.physical.x) * (self.logical_w / max(1, self.physical.width))
        ly = (py - self.physical.y) * (self.logical_h / max(1, self.physical.height))
        return (lx, ly)

    def local_to_physical(self, lx: float, ly: float) -> tuple[float, float]:
        """Convert widget-local logical coords to physical virtual-desktop coords."""
        px = self.physical.x + lx * (self.physical.width / max(1, self.logical_w))
        py = self.physical.y + ly * (self.physical.height / max(1, self.logical_h))
        return (px, py)

    def physical_rect_to_local_rect(self, rect: PhysicalRect) -> tuple[float, float, float, float]:
        """Convert a physical rect to (x, y, width, height) in widget-local logical coords."""
        lx1, ly1 = self.physical_to_local(rect.x, rect.y)
        lx2, ly2 = self.physical_to_local(rect.right, rect.bottom)
        return (lx1, ly1, lx2 - lx1, ly2 - ly1)

    def physical_to_qt_global(self, px: float, py: float) -> tuple[int, int]:
        """Convert physical virtual-desktop coords to Qt global logical desktop coords."""
        lx, ly = self.physical_to_local(px, py)
        return (round(self.logical_x + lx), round(self.logical_y + ly))


def ensure_dpi_awareness() -> None:
    """Enable Per-Monitor V2 DPI awareness on Windows before Qt initializes."""
    if sys.platform != "win32":
        return
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except (AttributeError, OSError):
        pass
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass


def enumerate_win32_monitors() -> list[tuple[str, PhysicalRect, bool]]:
    """Return [(device_name, PhysicalRect, is_primary), ...] in physical screen pixels."""
    if sys.platform != "win32":
        return []

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitors: list[tuple[str, PhysicalRect, bool]] = []
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    def _callback(hmon: int, hdc: int, lprect: object, lparam: int) -> int:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(wintypes.HMONITOR(hmon), ctypes.byref(info)):
            r = info.rcMonitor
            rect = PhysicalRect(r.left, r.top, r.right - r.left, r.bottom - r.top)
            is_primary = bool(info.dwFlags & 1)
            monitors.append((info.szDevice, rect, is_primary))
        return 1

    enum_proc_type = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )
    user32.EnumDisplayMonitors(None, None, enum_proc_type(_callback), 0)
    return monitors


def get_win32_cursor_pos() -> tuple[int, int]:
    """Return cursor (x, y) in physical virtual-desktop pixel coordinates."""
    if sys.platform != "win32":
        return (0, 0)
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (int(pt.x), int(pt.y))


def compute_virtual_bounds(rects: list[PhysicalRect]) -> PhysicalRect:
    """Return the union bounding rectangle across all provided monitor rectangles."""
    if not rects:
        return PhysicalRect(0, 0, 1920, 1080)
    min_x = min(r.x for r in rects)
    min_y = min(r.y for r in rects)
    max_r = max(r.right for r in rects)
    max_b = max(r.bottom for r in rects)
    return PhysicalRect(min_x, min_y, max_r - min_x, max_b - min_y)


def find_monitor_for_point(px: int, py: int, monitors: list[PhysicalRect]) -> PhysicalRect | None:
    """Return the monitor rect containing (px, py), or None if outside all monitors."""
    for mon in monitors:
        if mon.contains_point(px, py):
            return mon
    return None


def centered_rect_in(bounds: PhysicalRect, width: int, height: int, min_size: int = DEFAULT_MIN_SIZE) -> PhysicalRect:
    """Return a rect of size (width, height) centered inside bounds and clamped to it."""
    w = max(min_size, min(width, bounds.width))
    h = max(min_size, min(height, bounds.height))
    x = bounds.x + (bounds.width - w) // 2
    y = bounds.y + (bounds.height - h) // 2
    return PhysicalRect(x, y, w, h)


def clamp_box_to_monitors(
    rect: PhysicalRect,
    monitors: list[PhysicalRect],
    min_size: int = DEFAULT_MIN_SIZE,
) -> PhysicalRect:
    """Clamp rect to the multi-monitor desktop and guarantee it overlaps a real monitor."""
    if not monitors:
        return rect
    bounds = compute_virtual_bounds(monitors)
    clamped = rect.clamped_to(bounds, min_size=min_size)
    if any(clamped.intersects(m) for m in monitors):
        return clamped
    nearest = min(
        monitors,
        key=lambda m: abs(m.center_x - clamped.center_x) + abs(m.center_y - clamped.center_y),
    )
    return clamped.clamped_to(nearest, min_size=min_size)


def compute_scaled_output_size(cap_w: int, cap_h: int, scale: float) -> tuple[int, int]:
    """Scale both capture dimensions uniformly by a float multiplier without distortion."""
    safe_w = max(1, int(cap_w))
    safe_h = max(1, int(cap_h))
    safe_scale = max(0.05, min(9.99, float(scale)))
    if abs(safe_scale - 1.0) < 1e-6:
        return (safe_w, safe_h)
    return (max(1, round(safe_w * safe_scale)), max(1, round(safe_h * safe_scale)))


def compute_arrow_direction(held_directions: set[str]) -> tuple[int, int]:
    """Resolve simultaneous directional keys into a net (dx, dy) step in {-1, 0, 1}."""
    dx = (1 if "right" in held_directions else 0) - (1 if "left" in held_directions else 0)
    dy = (1 if "down" in held_directions else 0) - (1 if "up" in held_directions else 0)
    return (dx, dy)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def resize_box(
    start_rect: PhysicalRect,
    handle: str,
    cur_px: int,
    cur_py: int,
    start_px: int,
    start_py: int,
    square_lock: bool,
    bounds: PhysicalRect,
    min_size: int = DEFAULT_MIN_SIZE,
) -> PhysicalRect:
    """Resize start_rect from any of 8 handles; edges always resize 1 axis, corners scale proportionally when locked."""
    dx = cur_px - start_px
    dy = cur_py - start_py

    # Edge handles ('n', 's', 'e', 'w') always resize one dimension independently;
    # corner handles resize freely when proportional lock is off.
    if handle in ("n", "s", "e", "w") or not square_lock:
        left = start_rect.x
        top = start_rect.y
        right = start_rect.right
        bottom = start_rect.bottom
        if "w" in handle:
            left = _clamp(start_rect.x + dx, bounds.x, start_rect.right - min_size)
        if "e" in handle:
            right = _clamp(start_rect.right + dx, start_rect.x + min_size, bounds.right)
        if "n" in handle:
            top = _clamp(start_rect.y + dy, bounds.y, start_rect.bottom - min_size)
        if "s" in handle:
            bottom = _clamp(start_rect.bottom + dy, start_rect.y + min_size, bounds.bottom)
        return PhysicalRect(left, top, right - left, bottom - top)

    if handle not in ("nw", "ne", "se", "sw"):
        return start_rect

    # Proportional corner scaling: pin the opposite corner and preserve start_rect's aspect ratio.
    ax = start_rect.right if "w" in handle else start_rect.x
    ay = start_rect.bottom if "n" in handle else start_rect.y
    raw_w = start_rect.width + (-dx if "w" in handle else dx)
    raw_h = start_rect.height + (-dy if "n" in handle else dy)
    max_w = ax - bounds.x if "w" in handle else bounds.right - ax
    max_h = ay - bounds.y if "n" in handle else bounds.bottom - ay

    base_w = max(1, start_rect.width)
    base_h = max(1, start_rect.height)
    k_raw = max(raw_w / base_w, raw_h / base_h)
    k_min = max(min_size / base_w, min_size / base_h)
    k_max = min(max_w / base_w, max_h / base_h)
    k = k_max if k_max < k_min else max(k_min, min(k_raw, k_max))

    if base_w == base_h:
        side = _clamp(round(base_w * k), min_size, max(min_size, min(max_w, max_h)))
        new_w = side
        new_h = side
    else:
        new_w = _clamp(round(base_w * k), min_size, max(min_size, max_w))
        new_h = _clamp(round(base_h * k), min_size, max(min_size, max_h))

    new_x = ax - new_w if "w" in handle else ax
    new_y = ay - new_h if "n" in handle else ay
    return PhysicalRect(new_x, new_y, new_w, new_h)


def snap_rect_to_square(
    rect: PhysicalRect,
    bounds: PhysicalRect,
    min_size: int = DEFAULT_MIN_SIZE,
) -> PhysicalRect:
    """Convert rect to a centered square clamped within bounds."""
    max_side = min(bounds.width, bounds.height)
    side = _clamp(rect.width, min_size, max(min_size, max_side))
    x = rect.center_x - side // 2
    y = rect.center_y - side // 2
    return PhysicalRect(x, y, side, side).clamped_to(bounds, min_size=min_size)
