"""Multi-monitor translucent overlay with dashed capture box, resize handles, and HUD sync."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from src.config import DEFAULT_SCALE, MAX_SCALE, MIN_SCALE, AppConfig
from src.geometry import (
    DEFAULT_MIN_SIZE,
    PhysicalRect,
    ScreenMapping,
    centered_rect_in,
    clamp_box_to_monitors,
    compute_arrow_direction,
    compute_virtual_bounds,
    enumerate_win32_monitors,
    find_monitor_for_point,
    get_win32_cursor_pos,
    resize_box,
    snap_rect_to_square,
)
from src.hud_pill import FloatingHudWidget


HANDLE_IDS = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
_HANDLE_CURSORS: dict[str, Qt.CursorShape] = {
    "nw": Qt.SizeFDiagCursor,
    "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor,
    "sw": Qt.SizeBDiagCursor,
    "e": Qt.SizeHorCursor,
    "w": Qt.SizeHorCursor,
    "n": Qt.SizeVerCursor,
    "s": Qt.SizeVerCursor,
    "move": Qt.SizeAllCursor,
}
_HANDLE_HIT_RADIUS = 8.0
_EDGE_HIT_TOLERANCE = 6.0
_HANDLE_DRAW_HALF = 4.0

_ARROW_KEY_TO_DIR: dict[int, str] = {
    int(Qt.Key_Left): "left",
    int(Qt.Key_Right): "right",
    int(Qt.Key_Up): "up",
    int(Qt.Key_Down): "down",
}


def build_screen_mappings() -> list[tuple[QScreen, ScreenMapping]]:
    """Pair every Qt QScreen with its exact Win32 physical monitor rectangle."""
    screens = QApplication.screens()
    win32_monitors = enumerate_win32_monitors()
    used_indices: set[int] = set()
    result: list[tuple[QScreen, ScreenMapping]] = []

    for screen in screens:
        geom = screen.geometry()
        dpr = max(0.5, float(screen.devicePixelRatio()))
        expected_w = round(geom.width() * dpr)
        expected_h = round(geom.height() * dpr)

        matched_idx: int | None = None
        for idx, (_dev, phys, _primary) in enumerate(win32_monitors):
            if idx in used_indices:
                continue
            if phys.x == geom.x() and phys.y == geom.y():
                matched_idx = idx
                break

        if matched_idx is None:
            best_score: int | None = None
            for idx, (_dev, phys, _primary) in enumerate(win32_monitors):
                if idx in used_indices:
                    continue
                score = (
                    abs(phys.x - geom.x())
                    + abs(phys.y - geom.y())
                    + abs(phys.width - expected_w)
                    + abs(phys.height - expected_h)
                )
                if best_score is None or score < best_score:
                    best_score = score
                    matched_idx = idx

        if matched_idx is not None:
            used_indices.add(matched_idx)
            dev_name, phys_rect, is_primary = win32_monitors[matched_idx]
        else:
            dev_name = screen.name()
            phys_rect = PhysicalRect(geom.x(), geom.y(), expected_w, expected_h)
            is_primary = screen == QApplication.primaryScreen()

        mapping = ScreenMapping(
            device_name=dev_name,
            physical=phys_rect,
            logical_x=geom.x(),
            logical_y=geom.y(),
            logical_w=geom.width(),
            logical_h=geom.height(),
            is_primary=is_primary,
        )
        result.append((screen, mapping))

    return result


class ScreenOverlayWidget(QWidget):
    """Per-display translucent overlay rendering the dimmed region, dashed box, and handles."""

    def __init__(self, controller: OverlayController, mapping: ScreenMapping) -> None:
        super().__init__(None)
        self._controller = controller
        self._mapping = mapping
        self._drag_mode: str | None = None
        self._drag_start_rect: PhysicalRect | None = None
        self._drag_start_cursor: tuple[int, int] = (0, 0)
        self._hovered_handle: str | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setGeometry(
            mapping.logical_x,
            mapping.logical_y,
            mapping.logical_w,
            mapping.logical_h,
        )

    def _local_box_rect(self) -> QRectF:
        lx, ly, lw, lh = self._mapping.physical_rect_to_local_rect(self._controller.box_rect)
        return QRectF(lx, ly, lw, lh)

    def _handle_centers(self, box: QRectF) -> dict[str, QPointF]:
        cx = box.center().x()
        cy = box.center().y()
        return {
            "nw": QPointF(box.left(), box.top()),
            "n": QPointF(cx, box.top()),
            "ne": QPointF(box.right(), box.top()),
            "e": QPointF(box.right(), cy),
            "se": QPointF(box.right(), box.bottom()),
            "s": QPointF(cx, box.bottom()),
            "sw": QPointF(box.left(), box.bottom()),
            "w": QPointF(box.left(), cy),
        }

    def _hit_test(self, pos: QPointF) -> str | None:
        box = self._local_box_rect()
        centers = self._handle_centers(box)
        for handle_id in ("nw", "ne", "se", "sw", "n", "s", "e", "w"):
            center = centers[handle_id]
            if (
                abs(pos.x() - center.x()) <= _HANDLE_HIT_RADIUS
                and abs(pos.y() - center.y()) <= _HANDLE_HIT_RADIUS
            ):
                return handle_id

        if box.left() <= pos.x() <= box.right():
            if abs(pos.y() - box.top()) <= _EDGE_HIT_TOLERANCE:
                return "n"
            if abs(pos.y() - box.bottom()) <= _EDGE_HIT_TOLERANCE:
                return "s"
        if box.top() <= pos.y() <= box.bottom():
            if abs(pos.x() - box.left()) <= _EDGE_HIT_TOLERANCE:
                return "w"
            if abs(pos.x() - box.right()) <= _EDGE_HIT_TOLERANCE:
                return "e"

        if box.contains(pos):
            return "move"
        return None

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        box = self._local_box_rect()
        screen_rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))

        # Dim outside the capture box using an EvenOdd path cutout.
        dim_path = QPainterPath()
        dim_path.setFillRule(Qt.OddEvenFill)
        dim_path.addRect(screen_rect)
        dim_path.addRect(box)

        alpha = int(round(self._controller.opacity * 255))
        painter.fillPath(dim_path, QColor(12, 12, 15, alpha))

        # 1/255 alpha inside the cutout ensures Windows layered hit-testing catches drags anywhere in the box.
        painter.fillRect(box, QColor(0, 0, 0, 1))

        # Draw border slightly outside the exact capture pixels so edges stay crystal clear.
        border_rect = box.adjusted(-1.5, -1.5, 1.5, 1.5)
        if border_rect.intersects(screen_rect.adjusted(-20, -20, 20, 20)):
            under_pen = QPen(QColor(15, 12, 10, 225), 2.5, Qt.SolidLine)
            painter.setPen(under_pen)
            painter.drawRect(border_rect)

            dash_pen = QPen(QColor(255, 220, 125, 245), 1.5, Qt.CustomDashLine)
            dash_pen.setDashPattern([6.0, 4.0])
            painter.setPen(dash_pen)
            painter.drawRect(border_rect)

            for handle_id, center in self._handle_centers(box).items():
                if not screen_rect.adjusted(-12, -12, 12, 12).contains(center):
                    continue
                is_hot = handle_id == self._hovered_handle or handle_id == self._drag_mode
                fill = QColor("#FFD97D") if is_hot else QColor("#F5EFE6")
                handle_rect = QRectF(
                    center.x() - _HANDLE_DRAW_HALF,
                    center.y() - _HANDLE_DRAW_HALF,
                    _HANDLE_DRAW_HALF * 2.0,
                    _HANDLE_DRAW_HALF * 2.0,
                )
                painter.fillRect(handle_rect, fill)
                painter.setPen(QPen(QColor("#181410"), 1.25, Qt.SolidLine))
                painter.drawRect(handle_rect)

        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.RightButton:
            self._controller.cancel()
            event.accept()
            return

        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        hit = self._hit_test(event.position())
        if hit is not None:
            self._drag_mode = hit
            self._drag_start_rect = self._controller.box_rect
            self._drag_start_cursor = get_win32_cursor_pos()
            self.grabMouse()
            self.setCursor(_HANDLE_CURSORS.get(hit, Qt.ArrowCursor))
            self._controller.raise_hud()
            event.accept()
            return

        self._controller.raise_hud()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_mode is not None and self._drag_start_rect is not None:
            cur_px, cur_py = get_win32_cursor_pos()
            start_px, start_py = self._drag_start_cursor
            if self._drag_mode == "move":
                dx = cur_px - start_px
                dy = cur_py - start_py
                candidate = self._drag_start_rect.translated(dx, dy)
                new_rect = clamp_box_to_monitors(candidate, self._controller.monitor_rects)
            else:
                shift_held = bool(event.modifiers() & Qt.ShiftModifier)
                effective_square = self._controller.square_lock ^ shift_held
                new_rect = resize_box(
                    start_rect=self._drag_start_rect,
                    handle=self._drag_mode,
                    cur_px=cur_px,
                    cur_py=cur_py,
                    start_px=start_px,
                    start_py=start_py,
                    square_lock=effective_square,
                    bounds=self._controller.virtual_bounds,
                    min_size=DEFAULT_MIN_SIZE,
                )
                if not any(new_rect.intersects(m) for m in self._controller.monitor_rects):
                    new_rect = clamp_box_to_monitors(new_rect, self._controller.monitor_rects)

            self._controller.set_box_rect(new_rect)
            event.accept()
            return

        hit = self._hit_test(event.position())
        if hit != self._hovered_handle:
            self._hovered_handle = hit
            self.setCursor(_HANDLE_CURSORS.get(hit, Qt.ArrowCursor) if hit else Qt.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton and self._drag_mode is not None:
            self._drag_mode = None
            self._drag_start_rect = None
            self.releaseMouse()
            hit = self._hit_test(event.position())
            self._hovered_handle = hit
            self.setCursor(_HANDLE_CURSORS.get(hit, Qt.ArrowCursor) if hit else Qt.ArrowCursor)
            self._controller.update_all_screens()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = int(event.key())
        if key == int(Qt.Key_Escape):
            self._controller.cancel()
            event.accept()
            return
        if key == int(Qt.Key_R):
            self._controller.reset_box_to_default()
            event.accept()
            return
        if key == int(Qt.Key_S):
            self._controller.toggle_square_lock()
            event.accept()
            return

        if key in _ARROW_KEY_TO_DIR:
            if event.isAutoRepeat():
                event.accept()
                return
            self._controller.on_arrow_pressed(
                _ARROW_KEY_TO_DIR[key],
                bool(event.modifiers() & Qt.ShiftModifier),
            )
            event.accept()
            return

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        key = int(event.key())
        if key in _ARROW_KEY_TO_DIR:
            if event.isAutoRepeat():
                event.accept()
                return
            self._controller.on_arrow_released(_ARROW_KEY_TO_DIR[key])
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event: object) -> None:
        self._controller.clear_held_arrows()
        super().focusOutEvent(event)  # type: ignore[arg-type]

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.WindowActivate:
            QTimer.singleShot(0, self._controller.raise_hud)
        super().changeEvent(event)


class _RecordingBorderWidget(QWidget):
    """Click-through locked border drawn strictly outside the recorded pixel region."""

    def __init__(self, controller: OverlayController, mapping: ScreenMapping) -> None:
        super().__init__(None)
        self._controller = controller
        self._mapping = mapping

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setGeometry(
            mapping.logical_x,
            mapping.logical_y,
            mapping.logical_w,
            mapping.logical_h,
        )

    def _local_box_rect(self) -> QRectF:
        lx, ly, lw, lh = self._mapping.physical_rect_to_local_rect(self._controller.box_rect)
        return QRectF(lx, ly, lw, lh)

    def paintEvent(self, event: object) -> None:
        box = self._local_box_rect()
        screen_rect = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        border_rect = box.adjusted(-2.5, -2.5, 2.5, 2.5)
        if not border_rect.intersects(screen_rect.adjusted(-12, -12, 12, 12)):
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        under_pen = QPen(QColor(15, 12, 10, 225), 3.0, Qt.SolidLine)
        painter.setPen(under_pen)
        painter.drawRect(border_rect)

        rec_pen = QPen(QColor(224, 90, 79, 245), 2.0, Qt.CustomDashLine)
        rec_pen.setDashPattern([6.0, 4.0])
        painter.setPen(rec_pen)
        painter.drawRect(border_rect)

        painter.end()


class OverlayController(QObject):
    """Coordinates multi-monitor overlay windows, the capture box, and the floating HUD pill."""

    capture_requested = Signal()
    record_requested = Signal()
    canceled = Signal()
    settings_requested = Signal()
    state_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._capturing = False
        self._recording = False
        self._saving_recording = False
        self._screen_pairs: list[tuple[QScreen, ScreenMapping]] = []
        self._overlays: list[ScreenOverlayWidget] = []
        self._recording_borders: list[_RecordingBorderWidget] = []
        self._monitor_rects: list[PhysicalRect] = []
        self._virtual_bounds = PhysicalRect(0, 0, 1920, 1080)

        self._box_rect = PhysicalRect(0, 0, 500, 500)
        self._default_w = 500
        self._default_h = 500
        self._square_lock = False
        self._scale = DEFAULT_SCALE
        self._opacity = 0.45

        self._held_directions: set[str] = set()
        self._nudge_delay_timer = QTimer(self)
        self._nudge_delay_timer.setSingleShot(True)
        self._nudge_delay_timer.setInterval(180)
        self._nudge_delay_timer.timeout.connect(self._start_nudge_repeat)

        self._nudge_repeat_timer = QTimer(self)
        self._nudge_repeat_timer.setInterval(25)
        self._nudge_repeat_timer.timeout.connect(self._apply_held_nudge)

        self._hud = FloatingHudWidget()
        self._hud.capture_clicked.connect(self.capture_requested)
        self._hud.record_clicked.connect(self.record_requested)
        self._hud.capture_size_edited.connect(self._on_hud_capture_size_edited)
        self._hud.scale_edited.connect(self._on_hud_scale_edited)
        self._hud.settings_clicked.connect(self._on_hud_settings_clicked)
        self._hud.cancel_clicked.connect(self.cancel)

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_capturing(self) -> bool:
        return self._capturing

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_saving_recording(self) -> bool:
        return self._saving_recording

    @property
    def recording_border_widgets(self) -> list[_RecordingBorderWidget]:
        return list(self._recording_borders)

    @property
    def box_rect(self) -> PhysicalRect:
        return self._box_rect

    @property
    def monitor_rects(self) -> list[PhysicalRect]:
        return self._monitor_rects

    @property
    def virtual_bounds(self) -> PhysicalRect:
        return self._virtual_bounds

    @property
    def square_lock(self) -> bool:
        return self._square_lock

    @property
    def scale(self) -> float:
        return self._scale

    @property
    def opacity(self) -> float:
        return self._opacity

    @property
    def hud_widget(self) -> FloatingHudWidget:
        return self._hud

    @property
    def overlay_widgets(self) -> list[ScreenOverlayWidget]:
        return list(self._overlays)

    def show_overlay(self, config: AppConfig) -> None:
        """Build fresh monitor mappings, restore box & settings from config, and show overlay."""
        self.clear_held_arrows()
        self._teardown_overlays()
        self._teardown_recording_borders()
        self._capturing = False
        self._recording = False
        self._saving_recording = False

        self._default_w = config.default_width
        self._default_h = config.default_height
        self._square_lock = config.square_lock
        self._scale = config.scale
        self._opacity = config.overlay_opacity
        self._hud.set_video_preferences(config.video_format, config.video_fps)
        self._hud.set_recording_state(False, saving=False)

        self._screen_pairs = build_screen_mappings()
        self._monitor_rects = [mapping.physical for _, mapping in self._screen_pairs]
        self._virtual_bounds = compute_virtual_bounds(self._monitor_rects)

        if config.box_x is not None and config.box_y is not None:
            saved = PhysicalRect(config.box_x, config.box_y, config.box_width, config.box_height)
            if any(saved.intersects(m) for m in self._monitor_rects):
                self._box_rect = clamp_box_to_monitors(saved, self._monitor_rects)
            else:
                self._box_rect = self._center_on_cursor_monitor(config.box_width, config.box_height)
        else:
            self._box_rect = self._center_on_cursor_monitor(config.box_width, config.box_height)

        cursor_px, cursor_py = get_win32_cursor_pos()
        focus_overlay: ScreenOverlayWidget | None = None

        for _qscreen, mapping in self._screen_pairs:
            widget = ScreenOverlayWidget(self, mapping)
            widget.show()
            self._overlays.append(widget)
            if mapping.physical.contains_point(cursor_px, cursor_py):
                focus_overlay = widget

        if focus_overlay is None and self._overlays:
            focus_overlay = self._overlays[0]
        if focus_overlay is not None:
            focus_overlay.activateWindow()
            focus_overlay.setFocus()

        self._active = True
        self._sync_hud()
        self._hud.show()
        self._position_hud()
        self._hud.raise_()
        QTimer.singleShot(0, self.raise_hud)

    def _center_on_cursor_monitor(self, width: int, height: int) -> PhysicalRect:
        cx, cy = get_win32_cursor_pos()
        target_mon = find_monitor_for_point(cx, cy, self._monitor_rects)
        if target_mon is None:
            primary_mappings = [m.physical for _, m in self._screen_pairs if m.is_primary]
            target_mon = (
                primary_mappings[0]
                if primary_mappings
                else (self._monitor_rects[0] if self._monitor_rects else self._virtual_bounds)
            )
        return centered_rect_in(target_mon, width, height)

    def set_box_rect(self, rect: PhysicalRect) -> None:
        if self._recording or self._saving_recording or rect == self._box_rect:
            return
        self._box_rect = rect
        self.update_all_screens()
        self._sync_hud()
        self._position_hud()
        self.state_changed.emit()

    def nudge_box(self, dx: int, dy: int) -> None:
        if self._recording or self._saving_recording:
            return
        moved = clamp_box_to_monitors(self._box_rect.translated(dx, dy), self._monitor_rects)
        self.set_box_rect(moved)

    def on_arrow_pressed(self, direction: str, shift_held: bool) -> None:
        """Track a pressed arrow key and nudge immediately (supporting diagonal chords)."""
        self._held_directions.add(direction)
        self._apply_held_nudge(shift_override=shift_held)
        if not self._nudge_repeat_timer.isActive():
            self._nudge_delay_timer.start()

    def on_arrow_released(self, direction: str) -> None:
        """Stop tracking a released arrow key and halt repeat timers if none remain held."""
        self._held_directions.discard(direction)
        if not self._held_directions:
            self._nudge_delay_timer.stop()
            self._nudge_repeat_timer.stop()

    def clear_held_arrows(self) -> None:
        """Clear all tracked directional keys and stop repeat timers."""
        self._held_directions.clear()
        self._nudge_delay_timer.stop()
        self._nudge_repeat_timer.stop()

    def _start_nudge_repeat(self) -> None:
        if self._held_directions and self._active:
            self._apply_held_nudge()
            self._nudge_repeat_timer.start()

    def _apply_held_nudge(self, shift_override: bool | None = None) -> None:
        dx, dy = compute_arrow_direction(self._held_directions)
        if dx == 0 and dy == 0:
            return
        shift = (
            shift_override
            if shift_override is not None
            else bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        )
        step = 10 if shift else 1
        self.nudge_box(dx * step, dy * step)

    def reset_box_to_default(self) -> None:
        if self._recording or self._saving_recording:
            return
        w = self._default_w
        h = self._default_h
        x = self._box_rect.center_x - w // 2
        y = self._box_rect.center_y - h // 2
        reset_rect = clamp_box_to_monitors(PhysicalRect(x, y, w, h), self._monitor_rects)
        self.set_box_rect(reset_rect)

    def toggle_square_lock(self) -> None:
        if self._recording or self._saving_recording:
            return
        self.set_square_lock(not self._square_lock)

    def set_square_lock(self, locked: bool) -> None:
        if self._recording or self._saving_recording:
            return
        self._square_lock = bool(locked)
        self.state_changed.emit()

    def _on_hud_capture_size_edited(self, width: int, height: int) -> None:
        if self._recording or self._saving_recording:
            return
        target_w = max(DEFAULT_MIN_SIZE, int(width))
        target_h = max(DEFAULT_MIN_SIZE, int(height))
        x = self._box_rect.center_x - target_w // 2
        y = self._box_rect.center_y - target_h // 2
        resized = clamp_box_to_monitors(
            PhysicalRect(x, y, target_w, target_h),
            self._monitor_rects,
        )
        self.set_box_rect(resized)

    def _on_hud_scale_edited(self, scale: float) -> None:
        if self._recording or self._saving_recording:
            return
        self._scale = round(max(MIN_SCALE, min(MAX_SCALE, float(scale))), 2)
        self._sync_hud()
        self.state_changed.emit()

    def _on_hud_settings_clicked(self) -> None:
        if self._recording or self._saving_recording:
            return
        self.close_overlay()
        self.settings_requested.emit()

    def cancel(self) -> None:
        if self._saving_recording:
            return
        if not self._active and not self._recording:
            return
        self.close_overlay()
        self.canceled.emit()

    def write_state_to_config(self, config: AppConfig) -> None:
        """Persist current box rect and HUD toggle values back onto config."""
        config.box_x = self._box_rect.x
        config.box_y = self._box_rect.y
        config.box_width = self._box_rect.width
        config.box_height = self._box_rect.height
        config.square_lock = self._square_lock
        config.scale = self._scale

    def apply_config(self, config: AppConfig) -> None:
        """Push updated config settings into the currently visible overlay and HUD."""
        if self._recording or self._saving_recording:
            return
        self._default_w = config.default_width
        self._default_h = config.default_height
        self._square_lock = config.square_lock
        self._scale = config.scale
        self._opacity = config.overlay_opacity
        self._hud.set_video_preferences(config.video_format, config.video_fps)

        if self._active:
            target_w = max(DEFAULT_MIN_SIZE, config.box_width)
            target_h = max(DEFAULT_MIN_SIZE, config.box_height)
            x = self._box_rect.center_x - target_w // 2
            y = self._box_rect.center_y - target_h // 2
            resized = clamp_box_to_monitors(
                PhysicalRect(x, y, target_w, target_h),
                self._monitor_rects,
            )
            self._box_rect = resized
            self.update_all_screens()
            self._sync_hud()
            self._position_hud()
            self._hud.raise_()

    def hide_for_capture(self, callback: Callable[[], None], delay_ms: int = 45) -> PhysicalRect:
        """Hide overlay + HUD immediately, flush events, and run callback after DWM settles."""
        self._capturing = True
        self.clear_held_arrows()
        self._hud.commit_pending_edits()
        captured_rect = self._box_rect

        self._hud.hide()
        for overlay in self._overlays:
            overlay.hide()
        QApplication.processEvents()

        def _finish() -> None:
            self._teardown_overlays()
            self._active = False
            self._capturing = False
            callback()

        if delay_ms <= 0:
            _finish()
        else:
            QTimer.singleShot(delay_ms, _finish)
        return captured_rect

    def enter_recording_mode(self, on_ready: Callable[[], None], delay_ms: int = 45) -> PhysicalRect:
        """Lock viewport geometry, show click-through recording border, and start recording after DWM settles."""
        self.clear_held_arrows()
        self._hud.commit_pending_edits()
        self._recording = True
        self._saving_recording = False
        self._capturing = False
        locked_rect = self._box_rect

        self._teardown_recording_borders()
        for _qscreen, mapping in self._screen_pairs:
            border = _RecordingBorderWidget(self, mapping)
            border.show()
            self._recording_borders.append(border)

        for overlay in self._overlays:
            overlay.hide()
        self._teardown_overlays()

        self._hud.set_recording_state(recording=True, elapsed_seconds=0.0, saving=False)
        self._position_hud()
        self._hud.raise_()
        QApplication.processEvents()

        def _finish() -> None:
            if self._recording:
                on_ready()

        if delay_ms <= 0:
            _finish()
        else:
            QTimer.singleShot(delay_ms, _finish)
        return locked_rect

    def update_recording_elapsed(self, elapsed_seconds: float) -> None:
        if self._recording:
            self._hud.update_recording_elapsed(elapsed_seconds)

    def enter_recording_saving(self) -> None:
        """Remove recording borders and lock the HUD briefly while encoding finishes."""
        self._recording = False
        self._saving_recording = True
        self._teardown_recording_borders()
        self._hud.set_recording_state(recording=False, saving=True)
        self._position_hud()
        self._hud.raise_()

    def update_saving_progress(self, encoded: int, total: int) -> None:
        if self._saving_recording:
            self._hud.update_saving_progress(encoded, total)
            self._position_hud()

    def close_overlay(self) -> None:
        """Hide and tear down all overlay windows, recording borders, and the HUD pill."""
        self.clear_held_arrows()
        self._hud.commit_pending_edits()
        self._hud.set_recording_state(recording=False, saving=False)
        self._hud.hide()
        self._teardown_overlays()
        self._teardown_recording_borders()
        self._active = False
        self._capturing = False
        self._recording = False
        self._saving_recording = False

    def _teardown_overlays(self) -> None:
        for overlay in self._overlays:
            overlay.hide()
            overlay.deleteLater()
        self._overlays.clear()

    def _teardown_recording_borders(self) -> None:
        for border in self._recording_borders:
            border.hide()
            border.deleteLater()
        self._recording_borders.clear()

    def update_all_screens(self) -> None:
        for overlay in self._overlays:
            overlay.update()

    def _sync_hud(self) -> None:
        self._hud.sync_state(
            cap_w=self._box_rect.width,
            cap_h=self._box_rect.height,
            scale=self._scale,
        )

    def _position_hud(self) -> None:
        if not self._screen_pairs:
            return
        target_pair = self._find_pair_for_box()
        qscreen, mapping = target_pair
        avail = qscreen.availableGeometry()

        qt_left, qt_top = mapping.physical_to_qt_global(self._box_rect.x, self._box_rect.y)
        qt_right, qt_bottom = mapping.physical_to_qt_global(self._box_rect.right, self._box_rect.bottom)
        qt_center_x = (qt_left + qt_right) // 2

        self._hud.adjustSize()
        hud_w = self._hud.width()
        hud_h = self._hud.height()

        margin = 10
        min_x = avail.left() + 8
        max_x = max(min_x, avail.right() - hud_w - 8)
        hud_x = max(min_x, min(qt_center_x - hud_w // 2, max_x))

        below_y = qt_bottom + margin
        above_y = qt_top - hud_h - margin
        if below_y + hud_h <= avail.bottom() - 8:
            hud_y = below_y
        elif above_y >= avail.top() + 8:
            hud_y = above_y
        else:
            hud_y = max(avail.top() + 8, avail.bottom() - hud_h - 12)

        self._hud.move(hud_x, hud_y)
        self._hud.raise_()

    def raise_hud(self) -> None:
        if (self._active or self._recording or self._saving_recording) and self._hud.isVisible():
            self._hud.raise_()

    def _find_pair_for_box(self) -> tuple[QScreen, ScreenMapping]:
        cx = self._box_rect.center_x
        cy = self._box_rect.center_y
        for qscreen, mapping in self._screen_pairs:
            if mapping.physical.contains_point(cx, cy):
                return (qscreen, mapping)
        for qscreen, mapping in self._screen_pairs:
            if mapping.physical.intersects(self._box_rect):
                return (qscreen, mapping)
        return self._screen_pairs[0]
