"""Application entry point with Per-Monitor DPI awareness and single-instance mutex."""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

# Ensure project root is importable when running `python src/main.py`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.geometry import ensure_dpi_awareness  # noqa: E402

ensure_dpi_awareness()

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from src.tray import SnapshotterApp  # noqa: E402


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


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Character Snapshotter")
    app.setQuitOnLastWindowClosed(False)

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
