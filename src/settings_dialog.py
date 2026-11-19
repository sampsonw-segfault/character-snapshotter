"""Dark-themed settings dialog and Win-key-friendly global hotkey recorder."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.config import (
    DEFAULT_HOTKEY,
    MAX_BOX_SIZE,
    MAX_BRIDGE_PORT,
    MAX_SCALE,
    MIN_BOX_SIZE,
    MIN_BRIDGE_PORT,
    MIN_SCALE,
    AppConfig,
)
from src.hotkey import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    VK_TO_KEY_NAME,
    format_hotkey,
    parse_hotkey,
    qt_key_to_vk,
)


_MODIFIER_QT_KEYS = {
    int(Qt.Key_Control),
    int(Qt.Key_Shift),
    int(Qt.Key_Alt),
    int(Qt.Key_AltGr),
    int(Qt.Key_Meta),
    int(Qt.Key_Super_L),
    int(Qt.Key_Super_R),
}


class HotkeyRecorderWidget(QWidget):
    """Hotkey editor combining modifier toggle chips with a key-capture button."""

    hotkey_changed = Signal(str)

    def __init__(self, initial_hotkey: str = DEFAULT_HOTKEY, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._listening = False
        self._current_vk = ord("S")
        self._build_ui()
        self.set_hotkey(initial_hotkey)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(5)

        self.ctrl_btn = self._make_mod_chip("Ctrl")
        self.win_btn = self._make_mod_chip("Win")
        self.alt_btn = self._make_mod_chip("Alt")
        self.shift_btn = self._make_mod_chip("Shift")

        for btn in (self.ctrl_btn, self.win_btn, self.alt_btn, self.shift_btn):
            row.addWidget(btn)

        plus_lbl = QLabel("+", self)
        plus_lbl.setObjectName("SettingsMuted")
        row.addWidget(plus_lbl)

        self.key_btn = QPushButton("S", self)
        self.key_btn.setObjectName("KeyCaptureButton")
        self.key_btn.setMinimumWidth(110)
        self.key_btn.setCursor(Qt.PointingHandCursor)
        self.key_btn.setToolTip("Click, then press a key (or standard key combo)")
        self.key_btn.clicked.connect(self._toggle_listening)
        row.addWidget(self.key_btn, stretch=1)

        self.reset_btn = QPushButton("Default", self)
        self.reset_btn.setObjectName("SecondaryButton")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        self.reset_btn.setToolTip(f"Reset hotkey to {DEFAULT_HOTKEY}")
        self.reset_btn.clicked.connect(lambda: self.set_hotkey(DEFAULT_HOTKEY))
        row.addWidget(self.reset_btn)

        root.addLayout(row)

        self.preview_label = QLabel("", self)
        self.preview_label.setObjectName("SettingsHint")
        root.addWidget(self.preview_label)

    def _make_mod_chip(self, label: str) -> QPushButton:
        btn = QPushButton(label, self)
        btn.setObjectName("ModChipButton")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setMinimumWidth(46)
        btn.toggled.connect(self._emit_current)
        return btn

    def _toggle_listening(self) -> None:
        self._set_listening(not self._listening)

    def _set_listening(self, active: bool) -> None:
        self._listening = active
        if active:
            self.key_btn.setText("Press a key…")
            self.key_btn.setProperty("listening", True)
            self.key_btn.setFocus(Qt.OtherFocusReason)
        else:
            key_name = VK_TO_KEY_NAME.get(self._current_vk, "S")
            self.key_btn.setText(key_name)
            self.key_btn.setProperty("listening", False)
        self.key_btn.style().unpolish(self.key_btn)
        self.key_btn.style().polish(self.key_btn)

    def _modifiers_mask(self) -> int:
        mods = 0
        if self.ctrl_btn.isChecked():
            mods |= MOD_CONTROL
        if self.win_btn.isChecked():
            mods |= MOD_WIN
        if self.alt_btn.isChecked():
            mods |= MOD_ALT
        if self.shift_btn.isChecked():
            mods |= MOD_SHIFT
        return mods

    def current_hotkey(self) -> str:
        return format_hotkey(self._modifiers_mask(), self._current_vk)

    def set_hotkey(self, hotkey_str: str) -> None:
        try:
            mods, vk = parse_hotkey(hotkey_str)
        except ValueError:
            mods, vk = parse_hotkey(DEFAULT_HOTKEY)

        self._current_vk = vk
        self._listening = False

        chips = (
            (self.ctrl_btn, bool(mods & MOD_CONTROL)),
            (self.win_btn, bool(mods & MOD_WIN)),
            (self.alt_btn, bool(mods & MOD_ALT)),
            (self.shift_btn, bool(mods & MOD_SHIFT)),
        )
        for btn, checked in chips:
            btn.blockSignals(True)
            btn.setChecked(checked)
            btn.blockSignals(False)

        self._set_listening(False)
        self._update_preview()

    def _emit_current(self) -> None:
        self._update_preview()
        self.hotkey_changed.emit(self.current_hotkey())

    def _update_preview(self) -> None:
        self.preview_label.setText(f"Active binding: {self.current_hotkey()}")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._listening:
            super().keyPressEvent(event)
            return

        key = event.key()
        if key == int(Qt.Key_Escape):
            self._set_listening(False)
            event.accept()
            return

        if key in _MODIFIER_QT_KEYS:
            event.accept()
            return

        vk = qt_key_to_vk(key, event.nativeVirtualKey())
        if vk is None:
            event.accept()
            return

        # If the user pressed modifiers alongside the key, adopt those modifiers;
        # otherwise preserve whichever modifier toggle chips are checked (handy for Win+...).
        qt_mods = event.modifiers()
        pressed_mods = 0
        if qt_mods & Qt.ControlModifier:
            pressed_mods |= MOD_CONTROL
        if qt_mods & Qt.MetaModifier:
            pressed_mods |= MOD_WIN
        if qt_mods & Qt.AltModifier:
            pressed_mods |= MOD_ALT
        if qt_mods & Qt.ShiftModifier:
            pressed_mods |= MOD_SHIFT

        self._current_vk = vk
        if pressed_mods != 0:
            for btn, checked in (
                (self.ctrl_btn, bool(pressed_mods & MOD_CONTROL)),
                (self.win_btn, bool(pressed_mods & MOD_WIN)),
                (self.alt_btn, bool(pressed_mods & MOD_ALT)),
                (self.shift_btn, bool(pressed_mods & MOD_SHIFT)),
            ):
                btn.blockSignals(True)
                btn.setChecked(checked)
                btn.blockSignals(False)

        self._set_listening(False)
        self._emit_current()
        event.accept()


class SettingsDialog(QDialog):
    """Modal dark-themed configuration dialog for Character Snapshotter."""

    def __init__(
        self,
        config: AppConfig,
        hotkey_validator: Callable[[str], tuple[bool, str]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Character Snapshotter Settings")
        self.setMinimumWidth(520)
        self.setModal(True)

        self._config = deepcopy(config)
        self._hotkey_validator = hotkey_validator

        self._build_ui()
        self._populate_from_config()

    @property
    def result_config(self) -> AppConfig:
        return self._config

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 16)
        root.setSpacing(14)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)

        self.hotkey_recorder = HotkeyRecorderWidget(self._config.hotkey, self)
        self.hotkey_recorder.hotkey_changed.connect(lambda _s: self._clear_error())
        form.addRow("Global Hotkey:", self.hotkey_recorder)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(6)
        self.output_dir_edit = QLineEdit(self)
        dir_row.addWidget(self.output_dir_edit, stretch=1)

        browse_btn = QPushButton("Browse…", self)
        browse_btn.setObjectName("SecondaryButton")
        browse_btn.setCursor(Qt.PointingHandCursor)
        browse_btn.clicked.connect(self._browse_output_dir)
        dir_row.addWidget(browse_btn)

        open_btn = QPushButton("Open", self)
        open_btn.setObjectName("SecondaryButton")
        open_btn.setCursor(Qt.PointingHandCursor)
        open_btn.clicked.connect(self._open_output_dir)
        dir_row.addWidget(open_btn)
        form.addRow("Output Folder:", dir_row)

        prefix_col = QVBoxLayout()
        prefix_col.setSpacing(4)
        self.prefix_edit = QLineEdit(self)
        self.prefix_edit.textChanged.connect(self._update_prefix_preview)
        prefix_col.addWidget(self.prefix_edit)
        self.prefix_preview = QLabel("", self)
        self.prefix_preview.setObjectName("SettingsHint")
        prefix_col.addWidget(self.prefix_preview)
        form.addRow("Filename Prefix:", prefix_col)

        box_row = QHBoxLayout()
        box_row.setSpacing(6)
        self.default_w_spin = QSpinBox(self)
        self.default_w_spin.setRange(MIN_BOX_SIZE, MAX_BOX_SIZE)
        self.default_w_spin.setFixedWidth(88)
        box_row.addWidget(self.default_w_spin)
        box_row.addWidget(QLabel("×", self))
        self.default_h_spin = QSpinBox(self)
        self.default_h_spin.setRange(MIN_BOX_SIZE, MAX_BOX_SIZE)
        self.default_h_spin.setFixedWidth(88)
        box_row.addWidget(self.default_h_spin)
        box_row.addWidget(QLabel("px (used on Reset)", self))
        box_row.addStretch(1)
        form.addRow("Default Box Size:", box_row)

        self.square_lock_cb = QCheckBox(
            "Lock drag handles to 1:1 square by default (hold Shift while dragging to invert)",
            self,
        )
        form.addRow("Drag Aspect:", self.square_lock_cb)

        scale_row = QHBoxLayout()
        scale_row.setSpacing(8)
        self.scale_spin = QDoubleSpinBox(self)
        self.scale_spin.setRange(MIN_SCALE, MAX_SCALE)
        self.scale_spin.setDecimals(2)
        self.scale_spin.setSingleStep(0.1)
        self.scale_spin.setFixedWidth(74)
        self.scale_spin.editingFinished.connect(
            lambda: self.scale_spin.lineEdit().setText(f"{self.scale_spin.value():.2f}")
        )
        scale_row.addWidget(self.scale_spin)
        scale_row.addWidget(QLabel("× (scales both width & height)", self))
        scale_row.addStretch(1)
        form.addRow("Output Scale:", scale_row)

        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(10)
        self.opacity_slider = QSlider(Qt.Horizontal, self)
        self.opacity_slider.setRange(10, 85)
        self.opacity_slider.valueChanged.connect(self._update_opacity_label)
        opacity_row.addWidget(self.opacity_slider, stretch=1)
        self.opacity_value_lbl = QLabel("45%", self)
        self.opacity_value_lbl.setFixedWidth(38)
        opacity_row.addWidget(self.opacity_value_lbl)
        form.addRow("Overlay Dim:", opacity_row)

        self.chime_cb = QCheckBox("Play audio chime after saving snapshot", self)
        form.addRow("Feedback:", self.chime_cb)

        bridge_row = QHBoxLayout()
        bridge_row.setSpacing(8)
        self.bridge_enabled_cb = QCheckBox("Enable localhost bridge (Character Collager)", self)
        self.bridge_enabled_cb.toggled.connect(self._on_bridge_toggled)
        bridge_row.addWidget(self.bridge_enabled_cb)
        bridge_row.addWidget(QLabel("Port:", self))
        self.bridge_port_spin = QSpinBox(self)
        self.bridge_port_spin.setRange(MIN_BRIDGE_PORT, MAX_BRIDGE_PORT)
        self.bridge_port_spin.setFixedWidth(78)
        bridge_row.addWidget(self.bridge_port_spin)
        bridge_row.addStretch(1)
        form.addRow("Companion Bridge:", bridge_row)

        root.addLayout(form)

        self.error_label = QLabel("", self)
        self.error_label.setObjectName("SettingsError")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        cancel_btn = QPushButton("Cancel", self)
        cancel_btn.setObjectName("SecondaryButton")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save", self)
        save_btn.setObjectName("PrimaryButton")
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        root.addLayout(btn_row)

        self.setStyleSheet(
            """
            QDialog {
                background-color: #181410;
                color: #F4EFE6;
            }
            QLabel {
                color: #E6DFD3;
                font-size: 13px;
            }
            QLabel#SettingsMuted, QLabel#SettingsHint {
                color: #9E9487;
                font-size: 12px;
            }
            QLabel#SettingsError {
                background-color: rgba(165, 48, 42, 70);
                border: 1px solid #C95B53;
                border-radius: 6px;
                color: #FFD2CE;
                padding: 8px 10px;
                font-size: 12px;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox {
                background-color: #241F1A;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 13px;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border-color: #DAA520;
            }
            QSpinBox:disabled, QDoubleSpinBox:disabled {
                color: #7D756B;
                background-color: #1C1814;
            }
            QPushButton#SecondaryButton, QPushButton#ModChipButton, QPushButton#KeyCaptureButton {
                background-color: #28221C;
                color: #F4EFE6;
                border: 1px solid #4A4238;
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton#SecondaryButton:hover, QPushButton#ModChipButton:hover, QPushButton#KeyCaptureButton:hover {
                border-color: #DAA520;
                background-color: #332B23;
            }
            QPushButton#ModChipButton:checked {
                background-color: #463718;
                border-color: #DAA520;
                color: #FFD97D;
            }
            QPushButton#KeyCaptureButton[listening="true"] {
                background-color: #3E3016;
                border-color: #FFD97D;
                color: #FFD97D;
            }
            QPushButton#PrimaryButton {
                background-color: #B88618;
                color: #14100C;
                border: 1px solid #DAA520;
                border-radius: 6px;
                padding: 6px 18px;
                font-size: 13px;
                font-weight: 700;
            }
            QPushButton#PrimaryButton:hover {
                background-color: #D29C22;
            }
            QCheckBox {
                color: #E6DFD3;
                font-size: 13px;
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 3px;
                border: 1px solid #6B5F52;
                background-color: #241F1A;
            }
            QCheckBox::indicator:checked {
                background-color: #DAA520;
                border-color: #FFD97D;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #2C261F;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -4px 0;
                border-radius: 7px;
                background: #DAA520;
                border: 1px solid #FFD97D;
            }
            """
        )

    def _populate_from_config(self) -> None:
        self.hotkey_recorder.set_hotkey(self._config.hotkey)
        self.output_dir_edit.setText(self._config.output_dir)
        self.prefix_edit.setText(self._config.filename_prefix)
        self.default_w_spin.setValue(self._config.default_width)
        self.default_h_spin.setValue(self._config.default_height)
        self.square_lock_cb.setChecked(self._config.square_lock)
        self.scale_spin.setValue(self._config.scale)
        pct = round(self._config.overlay_opacity * 100)
        self.opacity_slider.setValue(max(10, min(85, pct)))
        self._update_opacity_label(self.opacity_slider.value())
        self.chime_cb.setChecked(self._config.play_chime)
        self.bridge_enabled_cb.setChecked(self._config.bridge_enabled)
        self.bridge_port_spin.setValue(self._config.bridge_port)
        self._on_bridge_toggled(self._config.bridge_enabled)
        self._update_prefix_preview(self.prefix_edit.text())

    def _on_bridge_toggled(self, checked: bool) -> None:
        self.bridge_port_spin.setEnabled(checked)

    def _update_opacity_label(self, value: int) -> None:
        self.opacity_value_lbl.setText(f"{value}%")

    def _update_prefix_preview(self, prefix: str) -> None:
        cleaned = "".join(ch for ch in prefix if ch not in '\\/:*?"<>|')
        sample = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.prefix_preview.setText(f"Preview: {cleaned}{sample}.png")

    def _browse_output_dir(self) -> None:
        current = self.output_dir_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Choose Output Directory", current)
        if chosen:
            self.output_dir_edit.setText(chosen)

    def _open_output_dir(self) -> None:
        raw_path = self.output_dir_edit.text().strip()
        if not raw_path:
            return
        target = Path(raw_path)
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def _clear_error(self) -> None:
        self.error_label.hide()
        self.error_label.setText("")

    def _on_save(self) -> None:
        self._clear_error()
        output_dir = self.output_dir_edit.text().strip()
        if not output_dir:
            self._show_error("Please specify a valid output folder path.")
            return

        new_hotkey = self.hotkey_recorder.current_hotkey()
        if self._hotkey_validator is not None:
            ok, err = self._hotkey_validator(new_hotkey)
            if not ok:
                self._show_error(f"Could not bind {new_hotkey}: {err}")
                return

        self._config.hotkey = new_hotkey
        self._config.output_dir = output_dir
        self._config.filename_prefix = self.prefix_edit.text()
        self._config.default_width = self.default_w_spin.value()
        self._config.default_height = self.default_h_spin.value()
        self._config.square_lock = self.square_lock_cb.isChecked()
        self._config.scale = round(float(self.scale_spin.value()), 2)
        self._config.overlay_opacity = round(self.opacity_slider.value() / 100.0, 2)
        self._config.play_chime = self.chime_cb.isChecked()
        self._config.bridge_enabled = self.bridge_enabled_cb.isChecked()
        self._config.bridge_port = self.bridge_port_spin.value()
        self.accept()
