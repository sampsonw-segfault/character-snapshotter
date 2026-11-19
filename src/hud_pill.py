"""Floating dark HUD pill attached outside the active capture box."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QWidget,
)

from src.config import MAX_BOX_SIZE, MIN_BOX_SIZE
from src.geometry import compute_proportional_output_size


class _HudSpinBox(QSpinBox):
    """Compact pixel spinbox that waits for Enter/commit while typing digits."""

    escape_pressed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(MIN_BOX_SIZE, MAX_BOX_SIZE)
        self.setButtonSymbols(QAbstractSpinBox.NoButtons)
        self.setKeyboardTracking(False)
        self.setAlignment(Qt.AlignCenter)
        self.setFixedWidth(52)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.escape_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FloatingHudWidget(QWidget):
    """Compact floating control bar positioned adjacent to the capture box."""

    capture_size_edited = Signal(int, int)
    reset_clicked = Signal()
    square_lock_toggled = Signal(bool)
    scale_toggled = Signal(bool)
    output_size_edited = Signal(str, int)
    settings_clicked = Signal()
    cancel_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        # Allow clicking spinboxes to focus when the user explicitly interacts with them,
        # while staying on top without taskbar clutter.
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self._cap_w = 500
        self._cap_h = 500
        self._square_lock = True
        self._scale_enabled = False
        self._anchor_axis = "width"
        self._target_px = 500

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

        cap_label = QLabel("Cap")
        cap_label.setObjectName("HudMutedLabel")
        layout.addWidget(cap_label)

        self.cap_w_spin = _HudSpinBox(self._pill)
        self.cap_w_spin.setToolTip("Capture width (physical px)")
        self.cap_w_spin.valueChanged.connect(self._on_cap_w_changed)
        self.cap_w_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.cap_w_spin)

        mul_cap = QLabel("×")
        mul_cap.setObjectName("HudMutedLabel")
        layout.addWidget(mul_cap)

        self.cap_h_spin = _HudSpinBox(self._pill)
        self.cap_h_spin.setToolTip("Capture height (physical px)")
        self.cap_h_spin.valueChanged.connect(self._on_cap_h_changed)
        self.cap_h_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.cap_h_spin)

        self.reset_btn = QPushButton("↺ 500", self._pill)
        self.reset_btn.setObjectName("HudButton")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.clicked.connect(self.reset_clicked)
        layout.addWidget(self.reset_btn)

        self.square_btn = QPushButton("🔒 1:1", self._pill)
        self.square_btn.setObjectName("HudToggleButton")
        self.square_btn.setCheckable(True)
        self.square_btn.setChecked(True)
        self.square_btn.setCursor(Qt.PointingHandCursor)
        self.square_btn.setToolTip("Lock capture box to 1:1 square (S)")
        self.square_btn.toggled.connect(self._on_square_toggled)
        layout.addWidget(self.square_btn)

        sep = QFrame(self._pill)
        sep.setObjectName("HudSeparator")
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        sep.setFixedHeight(20)
        layout.addWidget(sep)

        self.scale_checkbox = QCheckBox("Scale", self._pill)
        self.scale_checkbox.setObjectName("HudCheckBox")
        self.scale_checkbox.setCursor(Qt.PointingHandCursor)
        self.scale_checkbox.setToolTip("Enable strictly proportional output scaling")
        self.scale_checkbox.toggled.connect(self.scale_toggled)
        layout.addWidget(self.scale_checkbox)

        arrow_label = QLabel("→")
        arrow_label.setObjectName("HudMutedLabel")
        layout.addWidget(arrow_label)

        self.out_w_spin = _HudSpinBox(self._pill)
        self.out_w_spin.setToolTip("Proportional output width (px)")
        self.out_w_spin.valueChanged.connect(self._on_out_w_changed)
        self.out_w_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.out_w_spin)

        mul_out = QLabel("×")
        mul_out.setObjectName("HudMutedLabel")
        layout.addWidget(mul_out)

        self.out_h_spin = _HudSpinBox(self._pill)
        self.out_h_spin.setToolTip("Proportional output height (px)")
        self.out_h_spin.valueChanged.connect(self._on_out_h_changed)
        self.out_h_spin.escape_pressed.connect(self.cancel_clicked)
        layout.addWidget(self.out_h_spin)

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
            QSpinBox {
                background-color: #25201B;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 5px;
                padding: 2px 4px;
                font-size: 12px;
                font-weight: 600;
            }
            QSpinBox:focus {
                border: 1px solid #DAA520;
            }
            QSpinBox:disabled {
                color: #7D756B;
                background-color: #1E1A16;
                border-color: #322C25;
            }
            QPushButton#HudButton, QPushButton#HudToggleButton, QPushButton#HudIconButton {
                background-color: #2A241E;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 5px;
                padding: 3px 8px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#HudButton:hover, QPushButton#HudToggleButton:hover, QPushButton#HudIconButton:hover {
                background-color: #362E26;
                border-color: #DAA520;
            }
            QPushButton#HudToggleButton:checked {
                background-color: #463718;
                border-color: #DAA520;
                color: #FFD97D;
            }
            QCheckBox#HudCheckBox {
                color: #F4EFE6;
                font-size: 12px;
                font-weight: 600;
                spacing: 5px;
            }
            QCheckBox#HudCheckBox::indicator {
                width: 14px;
                height: 14px;
                border-radius: 3px;
                border: 1px solid #6B5F52;
                background-color: #25201B;
            }
            QCheckBox#HudCheckBox::indicator:checked {
                background-color: #DAA520;
                border-color: #FFD97D;
            }
            """
        )
        self.adjustSize()

    def _on_cap_w_changed(self, value: int) -> None:
        new_h = value if self._square_lock else self._cap_h
        self.capture_size_edited.emit(value, new_h)

    def _on_cap_h_changed(self, value: int) -> None:
        new_w = value if self._square_lock else self._cap_w
        self.capture_size_edited.emit(new_w, value)

    def _on_square_toggled(self, checked: bool) -> None:
        self._update_square_button_label(checked)
        self.square_lock_toggled.emit(checked)

    def _on_out_w_changed(self, value: int) -> None:
        self.output_size_edited.emit("width", value)

    def _on_out_h_changed(self, value: int) -> None:
        self.output_size_edited.emit("height", value)

    def _update_square_button_label(self, locked: bool) -> None:
        self.square_btn.setText("🔒 1:1" if locked else "⬜ Rect")

    def commit_pending_edits(self) -> None:
        """Commit any in-progress typed text in spinboxes before capturing."""
        for spin in (self.cap_w_spin, self.cap_h_spin, self.out_w_spin, self.out_h_spin):
            if spin.hasFocus() or spin.lineEdit().isModified():
                spin.interpretText()

    def sync_state(
        self,
        cap_w: int,
        cap_h: int,
        default_w: int,
        default_h: int,
        square_lock: bool,
        scale_enabled: bool,
        anchor_axis: str,
        target_px: int,
    ) -> None:
        """Synchronize HUD inputs and toggle states without triggering signal loops."""
        self._cap_w = cap_w
        self._cap_h = cap_h
        self._square_lock = square_lock
        self._scale_enabled = scale_enabled
        self._anchor_axis = anchor_axis
        self._target_px = target_px

        out_w, out_h = compute_proportional_output_size(
            cap_w,
            cap_h,
            scale_enabled=scale_enabled,
            anchor_axis=anchor_axis,
            target_px=target_px,
        )

        widgets = [
            self.cap_w_spin,
            self.cap_h_spin,
            self.square_btn,
            self.scale_checkbox,
            self.out_w_spin,
            self.out_h_spin,
        ]
        for w in widgets:
            w.blockSignals(True)

        self.cap_w_spin.setValue(cap_w)
        self.cap_h_spin.setValue(cap_h)
        self.square_btn.setChecked(square_lock)
        self._update_square_button_label(square_lock)

        if default_w == default_h:
            self.reset_btn.setText(f"↺ {default_w}")
        else:
            self.reset_btn.setText(f"↺ {default_w}×{default_h}")
        self.reset_btn.setToolTip(f"Reset capture box to {default_w}×{default_h} (R)")

        self.scale_checkbox.setChecked(scale_enabled)
        self.out_w_spin.setEnabled(scale_enabled)
        self.out_h_spin.setEnabled(scale_enabled)
        self.out_w_spin.setValue(out_w)
        self.out_h_spin.setValue(out_h)

        for w in widgets:
            w.blockSignals(False)

        self.adjustSize()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)
