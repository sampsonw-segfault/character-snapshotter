"""System tray coordinator and 2-tap global hotkey state machine."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from src.capture import capture_region_to_file, play_capture_chime
from src.config import AppConfig, load_config, save_config
from src.hotkey import GlobalHotkeyManager
from src.overlay import OverlayController
from src.settings_dialog import SettingsDialog


def create_tray_icon() -> QIcon:
    """Render a crisp dark-and-gold viewfinder icon programmatically."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)

    painter.setBrush(QColor("#181410"))
    painter.setPen(QPen(QColor("#4A4238"), 3.0))
    painter.drawRoundedRect(QRectF(3.0, 3.0, size - 6.0, size - 6.0), 12.0, 12.0)

    dash_pen = QPen(QColor("#FFD97D"), 3.5, Qt.DashLine)
    dash_pen.setDashPattern([3.0, 2.2])
    painter.setBrush(Qt.NoBrush)
    painter.setPen(dash_pen)
    painter.drawRect(QRectF(14.0, 14.0, size - 28.0, size - 28.0))

    painter.setBrush(QColor("#DAA520"))
    painter.setPen(Qt.NoPen)
    for cx, cy in ((14.0, 14.0), (50.0, 14.0), (14.0, 50.0), (50.0, 50.0)):
        painter.drawRect(QRectF(cx - 3.5, cy - 3.5, 7.0, 7.0))

    painter.end()
    return QIcon(pixmap)


class SnapshotterApp(QObject):
    """Coordinates system tray icon, global hotkey, overlay controller, and settings."""

    def __init__(self, config: AppConfig | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._config = config if config is not None else load_config()
        self._hotkey_manager = GlobalHotkeyManager(self)
        self._overlay = OverlayController(self)
        self._settings_dialog: SettingsDialog | None = None

        self._hotkey_manager.triggered.connect(self.on_hotkey_triggered)
        self._overlay.cancelled.connect(self._on_overlay_cancelled)
        self._overlay.settings_requested.connect(self.open_settings)

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

    def start(self) -> None:
        """Show tray icon and register configured global hotkey."""
        self._tray.show()
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
                "Character Snapshotter — Hotkey Unavailable",
                f"Could not register {self._config.hotkey}: {err}",
                QSystemTrayIcon.Warning,
                5000,
            )
            self.open_settings()

    def _refresh_tray_labels(self) -> None:
        hk = self._config.hotkey
        self._trigger_action.setText(f"Trigger Overlay ({hk})")
        self._tray.setToolTip(f"Character Snapshotter ({hk})")

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_settings()

    def _on_menu_trigger(self) -> None:
        if self._settings_dialog is not None:
            return
        if not self._overlay.is_active:
            self._overlay.show_overlay(self._config)

    def on_hotkey_triggered(self) -> None:
        """Toggle between opening the framing overlay and snapping the framed region."""
        if self._settings_dialog is not None or self._overlay.is_capturing:
            return

        if not self._overlay.is_active:
            self._overlay.show_overlay(self._config)
            return

        self._capture_active_overlay()

    def _capture_active_overlay(self) -> None:
        self._overlay.hud_widget.commit_pending_edits()
        self._overlay.write_state_to_config(self._config)
        save_config(self._config)

        rect = self._overlay.box_rect
        scale_enabled = self._overlay.scale_enabled
        anchor_axis = self._overlay.scale_anchor_axis
        target_px = self._overlay.scale_target_px
        output_dir = Path(self._config.output_dir)
        prefix = self._config.filename_prefix
        play_chime = self._config.play_chime

        def _do_grab() -> None:
            try:
                out_path = capture_region_to_file(
                    rect=rect,
                    output_dir=output_dir,
                    prefix=prefix,
                    scale_enabled=scale_enabled,
                    anchor_axis=anchor_axis,
                    target_px=target_px,
                )
                if play_chime:
                    play_capture_chime()
                self._tray.setToolTip(
                    f"Character Snapshotter ({self._config.hotkey}) — Last: {out_path.name}"
                )
            except Exception as exc:
                self._tray.showMessage(
                    "Character Snapshotter",
                    f"Snapshot failed: {exc}",
                    QSystemTrayIcon.Critical,
                    4000,
                )

        self._overlay.hide_for_capture(_do_grab)

    def _on_overlay_cancelled(self) -> None:
        self._overlay.write_state_to_config(self._config)
        save_config(self._config)

    def open_settings(self) -> None:
        """Open the modal Settings dialog while pausing the active global hotkey."""
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return

        if self._overlay.is_active:
            self._overlay.hud_widget.commit_pending_edits()
            self._overlay.write_state_to_config(self._config)
            self._overlay.close_overlay()
            save_config(self._config)

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
                self._refresh_tray_labels()
            else:
                ok, err = self._hotkey_manager.register(self._config.hotkey)
                if not ok:
                    self._tray.showMessage(
                        "Character Snapshotter — Hotkey Unavailable",
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
        """Cleanly close overlays, release hotkey, and hide tray icon."""
        if self._overlay.is_active:
            self._overlay.close_overlay()
        self._hotkey_manager.dispose()
        self._tray.hide()

    def quit_app(self) -> None:
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()
