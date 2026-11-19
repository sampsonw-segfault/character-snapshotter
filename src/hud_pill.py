"""Floating dark HUD pill attached outside the active capture box."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from src.config import MAX_BOX_SIZE, MAX_SCALE, MIN_BOX_SIZE, MIN_SCALE
from src.geometry import compute_scaled_output_size


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

        self._build_ui()

    def _build_ui(self) -> None:
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self._pill = QFrame(self)
        self._pill.setObjectName("HudPillFrame")
        outer.addWidget(self._pill)

        layout = QHBoxLayout(self._pill)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)

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

        self.settings_btn = QPushButton("⚙", self._pill)
        self.settings_btn.setObjectName("HudIconButton")
        self.settings_btn.setCursor(Qt.PointingHandCursor)
        self.settings_btn.setToolTip("Open Settings")
        self.settings_btn.setFixedWidth(28)
        self.settings_btn.clicked.connect(self.settings_clicked)
        layout.addWidget(self.settings_btn)

        self.cancel_btn = QPushButton("✕", self._pill)
        self.cancel_btn.setObjectName("HudIconButton")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setToolTip("Cancel overlay (Esc)")
        self.cancel_btn.setFixedWidth(28)
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
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                height: 0px;
                border: none;
            }
            QPushButton#HudIconButton {
                background-color: #2A241E;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 5px;
                padding: 3px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#HudIconButton:hover {
                background-color: #362E26;
                border-color: #DAA520;
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
