"""Floating dark HUD pill attached outside the active capture box."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeyEvent, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from src.config import (
    DEFAULT_VIDEO_FORMAT,
    DEFAULT_VIDEO_FPS,
    MAX_BOX_SIZE,
    MAX_SCALE,
    MIN_BOX_SIZE,
    MIN_SCALE,
)
from src.geometry import compute_scaled_output_size


_CAMERA_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#FFD97D" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>
  <circle cx="12" cy="13" r="3"/>
</svg>
"""

_RECORD_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <circle cx="12" cy="12" r="6.5" fill="#E05A4F" stroke="#F6A49D" stroke-width="1.5"/>
</svg>
"""

_STOP_RECORD_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
  <rect x="6" y="6" width="12" height="12" rx="2.5" ry="2.5" fill="#F4EFE6" stroke="#FFFFFF" stroke-width="1.2"/>
</svg>
"""

_SETTINGS_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#EAE3D7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
  <circle cx="12" cy="12" r="3"/>
</svg>
"""

_CLOSE_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#EAE3D7" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M18 6 6 18"/>
  <path d="m6 6 12 12"/>
</svg>
"""


def _svg_to_icon(svg_markup: str, logical_size: int = 15) -> QIcon:
    """Rasterize an SVG into multi-DPI pixmaps for crisp rendering on Windows scaling."""
    renderer = QSvgRenderer(QByteArray(svg_markup.strip().encode("utf-8")))
    icon = QIcon()
    for dpr in (1, 2, 3):
        px_size = logical_size * dpr
        pixmap = QPixmap(px_size, px_size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        renderer.render(painter)
        painter.end()
        pixmap.setDevicePixelRatio(float(dpr))
        icon.addPixmap(pixmap)
    return icon


class _HudSpinBox(QSpinBox):
    """Compact integer pixel spinbox that waits for Enter/commit while typing digits."""

    escape_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(MIN_BOX_SIZE, MAX_BOX_SIZE)
        self.setKeyboardTracking(False)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(64)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class _HudScaleSpinBox(QDoubleSpinBox):
    """Compact 2-decimal float multiplier spinbox that pads trailing zeros."""

    escape_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(MIN_SCALE, MAX_SCALE)
        self.setDecimals(2)
        self.setSingleStep(0.1)
        self.setKeyboardTracking(False)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(48)
        self.editingFinished.connect(self.normalize_display)

    def textFromValue(self, value: float) -> str:
        return f"{value:.2f}"

    def normalize_display(self) -> None:
        formatted = self.textFromValue(self.value())
        if self.lineEdit().text() != formatted:
            self.lineEdit().setText(formatted)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FloatingHudWidget(QWidget):
    """Compact floating control bar positioned adjacent to the capture box."""

    capture_clicked = Signal()
    record_clicked = Signal()
    capture_size_edited = Signal(int, int)
    scale_edited = Signal(float)
    settings_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._cap_w = 500
        self._cap_h = 500
        self._scale = 1.0
        self._recording = False
        self._saving = False
        self._video_format = DEFAULT_VIDEO_FORMAT
        self._video_fps = DEFAULT_VIDEO_FPS

        self._record_idle_icon = _svg_to_icon(_RECORD_SVG, logical_size=15)
        self._record_stop_icon = _svg_to_icon(_STOP_RECORD_SVG, logical_size=15)

        self._build_ui()

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._pill = QFrame(self)
        self._pill.setObjectName("HudPillFrame")
        outer.addWidget(self._pill)

        layout = QHBoxLayout(self._pill)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

        self.capture_btn = QPushButton("", self._pill)
        self.capture_btn.setObjectName("HudCaptureButton")
        self.capture_btn.setIcon(_svg_to_icon(_CAMERA_SVG, logical_size=15))
        self.capture_btn.setIconSize(QSize(15, 15))
        self.capture_btn.setFixedSize(28, 24)
        self.capture_btn.setCursor(Qt.PointingHandCursor)
        self.capture_btn.setToolTip("Capture PNG snapshot")
        self.capture_btn.clicked.connect(self.capture_clicked)
        layout.addWidget(self.capture_btn)

        self.record_btn = QPushButton("", self._pill)
        self.record_btn.setObjectName("HudRecordButton")
        self.record_btn.setProperty("recording", False)
        self.record_btn.setIcon(self._record_idle_icon)
        self.record_btn.setIconSize(QSize(15, 15))
        self.record_btn.setFixedSize(28, 24)
        self.record_btn.setCursor(Qt.PointingHandCursor)
        self.record_btn.setToolTip("Record WebP clip (30 FPS)")
        self.record_btn.clicked.connect(self.record_clicked)
        layout.addWidget(self.record_btn)

        self.rec_timer_label = QLabel("REC 0:00", self._pill)
        self.rec_timer_label.setObjectName("HudRecTimerLabel")
        self.rec_timer_label.hide()
        layout.addWidget(self.rec_timer_label)

        sep_left = QFrame(self._pill)
        sep_left.setObjectName("HudSeparator")
        sep_left.setFrameShape(QFrame.VLine)
        sep_left.setFixedWidth(1)
        sep_left.setFixedHeight(20)
        layout.addWidget(sep_left)

        self.cap_w_spin = _HudSpinBox(self._pill)
        self.cap_w_spin.setToolTip("Capture area width (physical px)")
        self.cap_w_spin.valueChanged.connect(self._on_cap_w_changed)
        self.cap_w_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.cap_w_spin)

        mul_cap = QLabel("×")
        mul_cap.setObjectName("HudMutedLabel")
        layout.addWidget(mul_cap)

        self.cap_h_spin = _HudSpinBox(self._pill)
        self.cap_h_spin.setToolTip("Capture area height (physical px)")
        self.cap_h_spin.valueChanged.connect(self._on_cap_h_changed)
        self.cap_h_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.cap_h_spin)

        sep = QFrame(self._pill)
        sep.setObjectName("HudSeparator")
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        layout.addWidget(sep)

        scale_label = QLabel("Scale")
        scale_label.setObjectName("HudMutedLabel")
        layout.addWidget(scale_label)

        self.scale_spin = _HudScaleSpinBox(self._pill)
        self.scale_spin.setValue(1.0)
        self.scale_spin.valueChanged.connect(self._on_scale_changed)
        self.scale_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.scale_spin)

        sep2 = QFrame(self._pill)
        sep2.setObjectName("HudSeparator")
        sep2.setFrameShape(QFrame.VLine)
        sep2.setFixedWidth(1)
        sep2.setFixedHeight(20)
        layout.addWidget(sep2)

        self.settings_btn = QPushButton("", self._pill)
        self.settings_btn.setObjectName("HudIconButton")
        self.settings_btn.setIcon(_svg_to_icon(_SETTINGS_SVG, logical_size=14))
        self.settings_btn.setIconSize(QSize(14, 14))
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setToolTip("Open Settings")
        self.settings_btn.setFixedSize(26, 24)
        self.settings_btn.clicked.connect(self.settings_clicked)
        layout.addWidget(self.settings_btn)

        self.cancel_btn = QPushButton("", self._pill)
        self.cancel_btn.setObjectName("HudIconButton")
        self.cancel_btn.setIcon(_svg_to_icon(_CLOSE_SVG, logical_size=14))
        self.cancel_btn.setIconSize(QSize(14, 14))
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setToolTip("Cancel overlay (Esc)")
        self.cancel_btn.setFixedSize(26, 24)
        self.cancel_btn.clicked.connect(self.cancel_clicked)
        layout.addWidget(self.cancel_btn)

        self.setStyleSheet(
            """
            QFrame#HudPillFrame {
                background-color: rgba(24, 20, 16, 238);
                border: 1px solid #4A4238;
                border-radius: 10px;
            }
            QLabel#HudMutedLabel {
                color: #C7BEB2;
                font-size: 12px;
                font-weight: 600;
            }
            QLabel#HudRecTimerLabel {
                color: #F6A49D;
                font-size: 11px;
                font-weight: 700;
                padding: 0px 2px;
            }
            QFrame#HudSeparator {
                background-color: #3D362E;
            }
            QSpinBox, QDoubleSpinBox {
                background-color: #25201B;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 5px;
                padding: 2px 4px;
                font-size: 12px;
                font-weight: 600;
            }
            QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #DAA520;
            }
            QSpinBox:disabled, QDoubleSpinBox:disabled {
                color: #7D756B;
                background-color: #1C1814;
                border-color: #352F28;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                height: 0px;
                border: none;
            }
            QPushButton#HudCaptureButton {
                background-color: #362B16;
                border: 1px solid #DAA520;
                border-radius: 5px;
                padding: 0px;
            }
            QPushButton#HudCaptureButton:hover {
                background-color: #49391B;
                border-color: #FFD97D;
            }
            QPushButton#HudCaptureButton:disabled {
                background-color: #1C1814;
                border-color: #352F28;
            }
            QPushButton#HudRecordButton {
                background-color: #2D201E;
                border: 1px solid #6E3B36;
                border-radius: 5px;
                padding: 0px;
            }
            QPushButton#HudRecordButton:hover {
                background-color: #412724;
                border-color: #E05A4F;
            }
            QPushButton#HudRecordButton[recording="true"] {
                background-color: #7A231E;
                border: 1px solid #E05A4F;
            }
            QPushButton#HudRecordButton[recording="true"]:hover {
                background-color: #932B25;
                border-color: #F6A49D;
            }
            QPushButton#HudRecordButton:disabled {
                background-color: #261D1B;
                border-color: #4A312E;
            }
            QPushButton#HudIconButton {
                background-color: #2A241E;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 5px;
                padding: 0px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#HudIconButton:hover {
                background-color: #362E26;
                border-color: #DAA520;
            }
            QPushButton#HudIconButton:disabled {
                background-color: #1C1814;
                border-color: #352F28;
            }
            """
        )
        self.adjustSize()

    def _on_cap_w_changed(self, value: int) -> None:
        self.capture_size_edited.emit(value, self._cap_h)

    def _on_cap_h_changed(self, value: int) -> None:
        self.capture_size_edited.emit(self._cap_w, value)

    def _on_scale_changed(self, value: float) -> None:
        self.scale_edited.emit(float(value))

    def set_video_preferences(self, video_format: str, video_fps: int) -> None:
        self._video_format = video_format
        self._video_fps = int(video_fps)
        if not self._recording and not self._saving:
            self.record_btn.setToolTip(
                f"Record {self._video_format.upper()} clip ({self._video_fps} FPS)"
            )

    def set_recording_state(
        self,
        recording: bool,
        elapsed_seconds: float = 0.0,
        saving: bool = False,
    ) -> None:
        """Update record/stop icon, timer label, and lock viewport-editing controls while recording."""
        self._recording = bool(recording)
        self._saving = bool(saving)

        if self._saving:
            for w in (
                self.capture_btn,
                self.record_btn,
                self.cap_w_spin,
                self.cap_h_spin,
                self.scale_spin,
                self.settings_btn,
                self.cancel_btn,
            ):
                w.setEnabled(False)
            self.record_btn.setIcon(self._record_stop_icon)
            self.record_btn.setProperty("recording", True)
            self.record_btn.setToolTip("Saving recording...")
            self.rec_timer_label.setText("Saving...")
            self.rec_timer_label.show()
        elif self._recording:
            for w in (
                self.capture_btn,
                self.cap_w_spin,
                self.cap_h_spin,
                self.scale_spin,
                self.settings_btn,
            ):
                w.setEnabled(False)
            self.record_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.record_btn.setIcon(self._record_stop_icon)
            self.record_btn.setProperty("recording", True)
            self.record_btn.setToolTip("Stop recording and save (viewport locked)")
            self.cancel_btn.setToolTip("Cancel recording (discard)")
            self.update_recording_elapsed(elapsed_seconds)
            self.rec_timer_label.show()
        else:
            for w in (
                self.capture_btn,
                self.record_btn,
                self.cap_w_spin,
                self.cap_h_spin,
                self.scale_spin,
                self.settings_btn,
                self.cancel_btn,
            ):
                w.setEnabled(True)
            self.record_btn.setIcon(self._record_idle_icon)
            self.record_btn.setProperty("recording", False)
            self.record_btn.setToolTip(
                f"Record {self._video_format.upper()} clip ({self._video_fps} FPS)"
            )
            self.cancel_btn.setToolTip("Cancel overlay (Esc)")
            self.rec_timer_label.hide()

        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)
        self.adjustSize()

    def update_recording_elapsed(self, elapsed_seconds: float) -> None:
        if self._saving:
            return
        total = max(0, int(elapsed_seconds))
        mins, secs = divmod(total, 60)
        text = f"REC {mins}:{secs:02d}"
        if self.rec_timer_label.text() != text:
            self.rec_timer_label.setText(text)

    def commit_pending_edits(self) -> None:
        """Commit any in-progress typed text in spinboxes before capturing."""
        for spin in (self.cap_w_spin, self.cap_h_spin, self.scale_spin):
            if spin.hasFocus() or spin.lineEdit().isModified():
                spin.interpretText()
        self.scale_spin.normalize_display()

    def sync_state(self, cap_w: int, cap_h: int, scale: float) -> None:
        """Synchronize live capture dimensions and scale multiplier without signal loops."""
        self._cap_w = cap_w
        self._cap_h = cap_h
        self._scale = scale

        out_w, out_h = compute_scaled_output_size(cap_w, cap_h, scale)

        widgets = (self.cap_w_spin, self.cap_h_spin, self.scale_spin)
        for w in widgets:
            w.blockSignals(True)

        self.cap_w_spin.setValue(cap_w)
        self.cap_h_spin.setValue(cap_h)
        self.scale_spin.setValue(scale)
        self.scale_spin.setToolTip(
            f"Scale multiplier applied to both dimensions (output: {out_w}×{out_h} px)"
        )

        for w in widgets:
            w.blockSignals(False)

        self.adjustSize()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)
