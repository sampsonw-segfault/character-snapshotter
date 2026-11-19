"""System tray coordinator, 2-tap global hotkey state machine, and localhost bridge."""

from __future__ import annotations

import os
import sys
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFileDialog, QMenu, QSystemTrayIcon

from src.bridge import BridgeCallbacks, LocalBridgeServer, config_to_bridge_dict
from src.capture import capture_region_to_file, play_capture_chime
from src.config import AppConfig, load_config, save_config, validate_config
from src.geometry import compute_scaled_output_size
from src.hotkey import GlobalHotkeyManager
from src.overlay import OverlayController
from src.settings_dialog import SettingsDialog


_REMOTE_PATCHABLE_FIELDS = {
    "hotkey",
    "output_dir",
    "filename_prefix",
    "default_width",
    "default_height",
    "box_width",
    "box_height",
    "square_lock",
    "scale",
    "overlay_opacity",
    "play_chime",
}


def render_app_icon_pixmap(size: int) -> QPixmap:
    """Render the dark-and-gold viewfinder icon at the requested pixel size."""
    s = size / 64.0
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setBrush(QColor("#181410"))
    painter.setPen(QPen(QColor("#4A4238"), max(1.0, 3.0 * s)))
    painter.drawRoundedRect(
        QRectF(3.0 * s, 3.0 * s, size - 6.0 * s, size - 6.0 * s),
        12.0 * s,
        12.0 * s,
    )

    dash_pen = QPen(QColor("#FFD97D"), max(1.2, 3.5 * s), Qt.DashLine)
    dash_pen.setDashPattern([3.0, 2.2])
    painter.setBrush(Qt.NoBrush)
    painter.setPen(dash_pen)
    painter.drawRect(QRectF(14.0 * s, 14.0 * s, size - 28.0 * s, size - 28.0 * s))

    painter.setBrush(QColor("#DAA520"))
    painter.setPen(Qt.NoPen)
    half = 3.5 * s
    corner = 7.0 * s
    for cx, cy in (
        (14.0 * s, 14.0 * s),
        (size - 14.0 * s, 14.0 * s),
        (14.0 * s, size - 14.0 * s),
        (size - 14.0 * s, size - 14.0 * s),
    ):
        painter.drawRect(QRectF(cx - half, cy - half, corner, corner))

    painter.end()
    return pixmap


def create_tray_icon() -> QIcon:
    """Build a crisp multi-size dark-and-gold viewfinder icon."""
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128):
        icon.addPixmap(render_app_icon_pixmap(sz))
    return icon


class SnapshotterApp(QObject):
    """Coordinates system tray icon, global hotkey, overlay controller, settings, and bridge."""

    def __init__(
        self,
        config: AppConfig | None = None,
        parent: QObject | None = None,
        start_bridge: bool = True,
    ) -> None:
        super().__init__(parent)
        self._config = config if config is not None else load_config()
        self._start_bridge = start_bridge
        self._hotkey_manager = GlobalHotkeyManager(self)
        self._overlay = OverlayController(self)
        self._settings_dialog: SettingsDialog | None = None

        self._bridge = LocalBridgeServer(
            BridgeCallbacks(
                get_status=self._get_bridge_status_base,
                apply_config_patch=self.apply_remote_config_patch,
                trigger_action=self.trigger_remote_action,
                browse_folder=self.browse_output_folder_remote,
                open_folder=self.open_output_folder,
                open_settings=self.open_settings,
            ),
            parent=self,
        )

        self._state_broadcast_timer = QTimer(self)
        self._state_broadcast_timer.setSingleShot(True)
        self._state_broadcast_timer.setInterval(120)
        self._state_broadcast_timer.timeout.connect(self._on_debounced_overlay_state_changed)

        self._hotkey_manager.triggered.connect(self.on_hotkey_triggered)
        self._overlay.capture_requested.connect(self._on_overlay_capture_requested)
        self._overlay.canceled.connect(self._on_overlay_canceled)
        self._overlay.settings_requested.connect(self.open_settings)
        self._overlay.state_changed.connect(self._state_broadcast_timer.start)

        self._tray = QSystemTrayIcon(create_tray_icon(), self)
        self._tray.activated.connect(self._on_tray_activated)

        self._menu = QMenu()
        self._trigger_action = QAction("", self._menu)
        self._trigger_action.triggered.connect(self._on_menu_trigger)
        self._menu.addAction(self._trigger_action)

        self._settings_action = QAction("Settings…", self._menu)
        self._settings_action.triggered.connect(self.open_settings)
        self._menu.addAction(self._settings_action)

        self._open_folder_action = QAction("Open Output Folder", self._menu)
        self._open_folder_action.triggered.connect(self.open_output_folder)
        self._menu.addAction(self._open_folder_action)

        self._bridge_sep = self._menu.addSeparator()

        self._bridge_status_action = QAction("Bridge: Off", self._menu)
        self._bridge_status_action.setEnabled(False)
        self._menu.addAction(self._bridge_status_action)

        self._menu.addSeparator()

        self._quit_action = QAction("Quit", self._menu)
        self._quit_action.triggered.connect(self.quit_app)
        self._menu.addAction(self._quit_action)

        self._tray.setContextMenu(self._menu)
        self._refresh_tray_labels()

    @property
    def config(self) -> AppConfig:
        return self._config

    @property
    def hotkey_manager(self) -> GlobalHotkeyManager:
        return self._hotkey_manager

    @property
    def overlay_controller(self) -> OverlayController:
        return self._overlay

    @property
    def bridge(self) -> LocalBridgeServer:
        return self._bridge

    def start(self) -> None:
        """Show tray icon, register configured global hotkey, and start localhost bridge."""
        self._tray.show()
        self._start_bridge_if_enabled()

        ok, err = self._hotkey_manager.register(self._config.hotkey)
        self._refresh_tray_labels()
        if ok:
            self._tray.showMessage(
                "Character Snapshotter",
                f"Running in the system tray. Press {self._config.hotkey} to open the capture box.",
                QSystemTrayIcon.Information,
                3500,
            )
        else:
            self._tray.showMessage(
                "Character Snapshotter - Hotkey Unavailable",
                f"Could not register {self._config.hotkey}: {err}",
                QSystemTrayIcon.Warning,
                5000,
            )
            self.open_settings()

    def _start_bridge_if_enabled(self) -> None:
        if self._start_bridge and self._config.bridge_enabled:
            ok, err = self._bridge.start(self._config.bridge_port)
            if not ok:
                self._tray.showMessage(
                    "Character Snapshotter - Bridge Unavailable",
                    f"Could not listen on 127.0.0.1:{self._config.bridge_port}: {err}",
                    QSystemTrayIcon.Warning,
                    4500,
                )
        self._refresh_tray_labels()

    def _refresh_tray_labels(self) -> None:
        hk = self._config.hotkey
        self._trigger_action.setText(f"Trigger Overlay ({hk})")
        self._tray.setToolTip(f"Character Snapshotter ({hk})")
        show_bridge = self._bridge.is_running or self._config.bridge_enabled
        self._bridge_sep.setVisible(show_bridge)
        self._bridge_status_action.setVisible(show_bridge)
        if self._bridge.is_running:
            self._bridge_status_action.setText(f"Bridge: 127.0.0.1:{self._bridge.actual_port}")
        elif self._config.bridge_enabled:
            self._bridge_status_action.setText(f"Bridge: Stopped ({self._config.bridge_port})")
        else:
            self._bridge_status_action.setText("Bridge: Off")

    def _get_bridge_status_base(self) -> dict[str, Any]:
        cfg = deepcopy(self._config)
        if self._overlay.is_active:
            self._overlay.write_state_to_config(cfg)
        return {
            "overlay_active": self._overlay.is_active and not self._overlay.is_capturing,
            "config": config_to_bridge_dict(cfg),
        }

    def _broadcast_status_update(self) -> None:
        if self._bridge.is_running:
            self._bridge.broadcast_status(self._bridge.build_status_payload())

    def _on_debounced_overlay_state_changed(self) -> None:
        if not self._overlay.is_active or self._overlay.is_capturing:
            return
        self._overlay.write_state_to_config(self._config)
        save_config(self._config)
        self._broadcast_status_update()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_settings()

    def _on_menu_trigger(self) -> None:
        if self._settings_dialog is not None:
            return
        if not self._overlay.is_active:
            self._overlay.show_overlay(self._config)
            if self._bridge.is_running:
                self._bridge.broadcast_overlay_state(True)

    def on_hotkey_triggered(self) -> None:
        """Toggle between opening the framing overlay and snapping the framed region."""
        if self._settings_dialog is not None or self._overlay.is_capturing:
            return

        if not self._overlay.is_active:
            self._overlay.show_overlay(self._config)
            if self._bridge.is_running:
                self._bridge.broadcast_overlay_state(True)
            return

        self._capture_active_overlay()

    def _on_overlay_capture_requested(self) -> None:
        if self._settings_dialog is None and self._overlay.is_active and not self._overlay.is_capturing:
            self._capture_active_overlay()

    def _capture_active_overlay(self) -> None:
        self._state_broadcast_timer.stop()
        self._overlay.hud_widget.commit_pending_edits()
        self._overlay.write_state_to_config(self._config)
        save_config(self._config)

        rect = self._overlay.box_rect
        scale = self._overlay.scale
        output_dir = Path(self._config.output_dir)
        prefix = self._config.filename_prefix
        play_chime = self._config.play_chime

        def _do_grab() -> None:
            try:
                out_path = capture_region_to_file(
                    rect=rect,
                    output_dir=output_dir,
                    prefix=prefix,
                    scale=scale,
                )
                if play_chime:
                    play_capture_chime()
                self._tray.setToolTip(
                    f"Character Snapshotter ({self._config.hotkey}) - Last: {out_path.name}"
                )
                if self._bridge.is_running:
                    out_w, out_h = compute_scaled_output_size(
                        rect.width,
                        rect.height,
                        scale=scale,
                    )
                    self._bridge.record_capture(out_path, out_w, out_h)
                    self._bridge.broadcast_overlay_state(False)
            except Exception as exc:
                if self._bridge.is_running:
                    self._bridge.broadcast_overlay_state(False)
                self._tray.showMessage(
                    "Character Snapshotter",
                    f"Snapshot failed: {exc}",
                    QSystemTrayIcon.Critical,
                    4000,
                )

        if self._bridge.is_running:
            self._bridge.broadcast_overlay_state(False)
        self._overlay.hide_for_capture(_do_grab)

    def _on_overlay_canceled(self) -> None:
        self._state_broadcast_timer.stop()
        self._overlay.write_state_to_config(self._config)
        save_config(self._config)
        if self._bridge.is_running:
            self._bridge.broadcast_overlay_state(False)
            self._broadcast_status_update()

    def apply_remote_config_patch(self, patch: dict[str, Any]) -> tuple[bool, str]:
        """Validate and apply a partial config update received over the localhost bridge."""
        if not isinstance(patch, dict):
            return (False, "Config patch must be a JSON object.")

        raw_data = asdict(self._config)
        if self._overlay.is_active:
            temp_cfg = AppConfig(**raw_data)
            self._overlay.write_state_to_config(temp_cfg)
            raw_data = asdict(temp_cfg)

        if "hotkey" in patch:
            new_hotkey = str(patch["hotkey"]).strip()
            if not new_hotkey:
                return (False, "Hotkey cannot be empty.")
            if new_hotkey != self._config.hotkey:
                if self._settings_dialog is not None:
                    return (False, "Close the Settings dialog before changing the hotkey.")
                ok, err = self._hotkey_manager.register(new_hotkey)
                if not ok:
                    return (False, err)
                raw_data["hotkey"] = self._hotkey_manager.current_hotkey or new_hotkey

        if "output_dir" in patch:
            new_dir = str(patch["output_dir"]).strip()
            if not new_dir:
                return (False, "Output directory cannot be empty.")
            raw_data["output_dir"] = new_dir

        for key, value in patch.items():
            if key not in _REMOTE_PATCHABLE_FIELDS or key in ("hotkey", "output_dir"):
                continue
            raw_data[key] = value

        candidate = validate_config(AppConfig(**raw_data))

        self._config = candidate
        save_config(self._config)
        self._refresh_tray_labels()

        if self._overlay.is_active:
            self._overlay.apply_config(self._config)

        self._broadcast_status_update()
        return (True, "")

    def trigger_remote_action(self, action: str) -> tuple[bool, str, bool]:
        """Execute an overlay/capture trigger action sent from the localhost bridge."""
        if self._settings_dialog is not None or self._overlay.is_capturing:
            return (False, "Overlay is busy or Settings dialog is open.", self._overlay.is_active)

        normalized = action.strip().lower()
        if normalized == "toggle":
            was_active = self._overlay.is_active
            self.on_hotkey_triggered()
            return (True, "", not was_active)

        if normalized == "show":
            if not self._overlay.is_active:
                self._overlay.show_overlay(self._config)
                if self._bridge.is_running:
                    self._bridge.broadcast_overlay_state(True)
            return (True, "", True)

        if normalized == "capture":
            if not self._overlay.is_active:
                return (False, "Overlay is not currently open.", False)
            self._capture_active_overlay()
            return (True, "", False)

        if normalized == "cancel":
            if self._overlay.is_active:
                self._overlay.cancel()
            return (True, "", False)

        if normalized == "reset_size":
            self._config.box_width = self._config.default_width
            self._config.box_height = self._config.default_height
            save_config(self._config)
            if self._overlay.is_active:
                self._overlay.reset_box_to_default()
            self._broadcast_status_update()
            return (True, "", self._overlay.is_active)

        return (False, f"Unsupported trigger action: {action}", self._overlay.is_active)

    def browse_output_folder_remote(self) -> None:
        """Open a native folder picker on the desktop and update output_dir if selected."""
        chosen = QFileDialog.getExistingDirectory(
            None,
            "Choose Snapshot Output Folder",
            self._config.output_dir,
        )
        if chosen:
            self._config.output_dir = chosen
            save_config(self._config)
            self._broadcast_status_update()

    def open_settings(self) -> None:
        """Open the modal Settings dialog while pausing the active global hotkey."""
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        if self._overlay.is_active:
            self._state_broadcast_timer.stop()
            self._overlay.hud_widget.commit_pending_edits()
            self._overlay.write_state_to_config(self._config)
            self._overlay.close_overlay()
            save_config(self._config)
            if self._bridge.is_running:
                self._bridge.broadcast_overlay_state(False)

        prev_bridge = (self._config.bridge_enabled, self._config.bridge_port)
        self._hotkey_manager.unregister()
        dialog = SettingsDialog(
            config=self._config,
            hotkey_validator=self._hotkey_manager.register,
        )
        self._settings_dialog = dialog
        try:
            accepted = dialog.exec()
            if accepted:
                self._config = dialog.result_config
                save_config(self._config)
                if (self._config.bridge_enabled, self._config.bridge_port) != prev_bridge:
                    self._bridge.stop()
                    self._start_bridge_if_enabled()
                self._refresh_tray_labels()
                self._broadcast_status_update()
            else:
                ok, err = self._hotkey_manager.register(self._config.hotkey)
                if not ok:
                    self._tray.showMessage(
                        "Character Snapshotter - Hotkey Unavailable",
                        f"Could not restore {self._config.hotkey}: {err}",
                        QSystemTrayIcon.Warning,
                        4500,
                    )
        finally:
            self._settings_dialog = None

    def open_output_folder(self) -> None:
        """Open the configured snapshot output directory in File Explorer."""
        target = Path(self._config.output_dir)
        target.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606

    def shutdown(self) -> None:
        """Cleanly stop bridge, close overlays, release hotkey, and hide tray icon."""
        self._state_broadcast_timer.stop()
        self._bridge.stop()
        if self._overlay.is_active:
            self._overlay.close_overlay()
        self._hotkey_manager.dispose()
        self._tray.hide()

    def quit_app(self) -> None:
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()
