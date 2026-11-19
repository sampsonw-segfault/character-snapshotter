"""Application entry point with Per-Monitor DPI awareness and single-instance mutex."""

from __future__ import annotations

import ctypes
import json
import sys
from pathlib import Path

# Ensure project root is importable when running `python src/main.py`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src import __version__  # noqa: E402
from src.geometry import ensure_dpi_awareness  # noqa: E402

ensure_dpi_awareness()

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.tray import SnapshotterApp, create_tray_icon  # noqa: E402


_MUTEX_NAME = "Local\\CharacterSnapshotterSingleInstance"
_ERROR_ALREADY_EXISTS = 183


if sys.platform == "win32":
    import ctypes.wintypes as wintypes


def _acquire_single_instance_mutex() -> int | None:
    """Create a named Win32 mutex; return handle if first instance, else None."""
    if sys.platform != "win32":
        return 1
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        return 1
    if ctypes.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(handle)
        return None
    return int(handle)


def _release_single_instance_mutex(handle: int | None) -> None:
    if sys.platform == "win32" and handle:
        kernel32 = ctypes.windll.kernel32
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(wintypes.HANDLE(handle))


def _parse_self_check_arg(argv: list[str]) -> tuple[bool, Path | None]:
    """Parse optional --self-check or --self-check=<path> smoke-test CLI flag."""
    for arg in argv:
        if arg == "--self-check":
            return (True, None)
        if arg.startswith("--self-check="):
            raw = arg.split("=", 1)[1].strip()
            return (True, Path(raw) if raw else None)
    return (False, None)


def _run_self_check(app: QApplication, output_path: Path | None) -> int:
    """Verify frozen runtime dependencies, Qt platform, SVG icons, and settings dialog."""
    try:
        import mss
        import PIL

        from src.config import AppConfig, get_config_path
        from src.hud_pill import FloatingHudWidget
        from src.settings_dialog import SettingsDialog

        hud = FloatingHudWidget()
        svg_icons_ok = all(
            not btn.icon().isNull() for btn in (hud.capture_btn, hud.settings_btn, hud.cancel_btn)
        )
        hud.close()
        hud.deleteLater()

        dlg = SettingsDialog(AppConfig())
        settings_dialog_ok = dlg.hotkey_recorder.current_hotkey() == "Win+Alt+S"
        dlg.close()
        dlg.deleteLater()

        report = {
            "ok": bool(svg_icons_ok and settings_dialog_ok),
            "version": __version__,
            "frozen": bool(getattr(sys, "frozen", False)),
            "config_path": str(get_config_path()),
            "qt_platform": app.platformName(),
            "svg_icons_ok": bool(svg_icons_ok),
            "settings_dialog_ok": bool(settings_dialog_ok),
            "mss_version": getattr(mss, "__version__", ""),
            "pillow_version": getattr(PIL, "__version__", ""),
        }
        exit_code = 0 if report["ok"] else 1
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "error": repr(exc)}
        exit_code = 1

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return exit_code


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Character Snapshotter")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(create_tray_icon())

    self_check, self_check_out = _parse_self_check_arg(sys.argv[1:])
    if self_check:
        return _run_self_check(app, self_check_out)

    mutex_handle = _acquire_single_instance_mutex()
    if mutex_handle is None:
        QMessageBox.information(
            None,
            "Character Snapshotter",
            "Character Snapshotter is already running in your system tray.",
        )
        return 0

    controller = SnapshotterApp()
    app.aboutToQuit.connect(controller.shutdown)
    controller.start()

    try:
        return app.exec()
    finally:
        _release_single_instance_mutex(mutex_handle)


if __name__ == "__main__":
    raise SystemExit(main())
