"""Native Windows global hotkey registration via RegisterHotKey and Qt native event filtering."""

from __future__ import annotations

import ctypes
import sys
from typing import Callable

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QWidget

if sys.platform == "win32":
    import ctypes.wintypes as wintypes


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_DEFAULT_HOTKEY_ID = 0x4353  # 'CS'

_MODIFIER_ALIASES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "meta": MOD_WIN,
    "super": MOD_WIN,
    "alt": MOD_ALT,
    "menu": MOD_ALT,
    "shift": MOD_SHIFT,
}

_CANONICAL_MODIFIERS: list[tuple[int, str]] = [
    (MOD_CONTROL, "Ctrl"),
    (MOD_WIN, "Win"),
    (MOD_ALT, "Alt"),
    (MOD_SHIFT, "Shift"),
]


def _build_vk_tables() -> tuple[dict[str, int], dict[int, str]]:
    name_to_vk: dict[str, int] = {}
    vk_to_name: dict[int, str] = {}

    def _add(vk: int, canonical: str, *aliases: str) -> None:
        vk_to_name[vk] = canonical
        name_to_vk[canonical.lower()] = vk
        for alias in aliases:
            name_to_vk[alias.lower()] = vk

    for code in range(ord("A"), ord("Z") + 1):
        _add(code, chr(code))

    for code in range(ord("0"), ord("9") + 1):
        _add(code, chr(code))

    for idx in range(1, 25):
        _add(0x6F + idx, f"F{idx}")

    for idx in range(0, 10):
        _add(0x60 + idx, f"Num{idx}", f"Numpad{idx}")

    specials: list[tuple[int, str, tuple[str, ...]]] = [
        (0x2C, "PrintScreen", ("prtsc", "prtscn", "print", "snapshot")),
        (0x13, "Pause", ("break",)),
        (0x2D, "Insert", ("ins",)),
        (0x2E, "Delete", ("del",)),
        (0x24, "Home", ()),
        (0x23, "End", ()),
        (0x21, "PageUp", ("pgup",)),
        (0x22, "PageDown", ("pgdn", "pgdown")),
        (0x20, "Space", ("spacebar",)),
        (0x09, "Tab", ()),
        (0x0D, "Enter", ("return",)),
        (0x08, "Backspace", ("bksp",)),
        (0x26, "Up", ()),
        (0x28, "Down", ()),
        (0x25, "Left", ()),
        (0x27, "Right", ()),
        (0x14, "CapsLock", ()),
        (0x91, "ScrollLock", ()),
        (0x90, "NumLock", ()),
        (0xBD, "-", ("minus",)),
        (0xBB, "=", ("equals", "plus")),
        (0xDB, "[", ("lbracket",)),
        (0xDD, "]", ("rbracket",)),
        (0xDC, "\\", ("backslash",)),
        (0xBA, ";", ("semicolon",)),
        (0xDE, "'", ("apostrophe", "quote")),
        (0xBC, ",", ("comma",)),
        (0xBE, ".", ("period",)),
        (0xBF, "/", ("slash",)),
        (0xC0, "`", ("grave", "tilde")),
    ]
    for vk, canonical, aliases in specials:
        _add(vk, canonical, *aliases)

    return name_to_vk, vk_to_name


KEY_NAME_TO_VK, VK_TO_KEY_NAME = _build_vk_tables()


def parse_hotkey(hotkey_str: str) -> tuple[int, int]:
    """Parse a human-readable hotkey like 'Win+Alt+S' into (win32_modifiers, virtual_key)."""
    if not isinstance(hotkey_str, str) or not hotkey_str.strip():
        raise ValueError("Hotkey cannot be empty.")

    parts = [part.strip() for part in hotkey_str.split("+")]
    if any(not part for part in parts):
        raise ValueError(f"Invalid hotkey syntax: {hotkey_str!r}")

    modifiers = 0
    key_vk: int | None = None

    for part in parts:
        lowered = part.lower()
        if lowered in _MODIFIER_ALIASES:
            modifiers |= _MODIFIER_ALIASES[lowered]
        elif lowered in KEY_NAME_TO_VK:
            if key_vk is not None:
                raise ValueError(f"Hotkey must specify exactly one primary key: {hotkey_str!r}")
            key_vk = KEY_NAME_TO_VK[lowered]
        else:
            raise ValueError(f"Unsupported key in hotkey: {part!r}")

    if key_vk is None:
        raise ValueError(f"Hotkey must include a non-modifier key: {hotkey_str!r}")

    return (modifiers, key_vk)


def format_hotkey(modifiers: int, vk: int) -> str:
    """Format (win32_modifiers, virtual_key) into canonical hotkey string."""
    key_name = VK_TO_KEY_NAME.get(vk)
    if key_name is None:
        raise ValueError(f"Unsupported virtual key code: {vk:#x}")

    tokens = [label for bit, label in _CANONICAL_MODIFIERS if modifiers & bit]
    tokens.append(key_name)
    return "+".join(tokens)


def qt_key_to_vk(qt_key: int, native_vk: int = 0) -> int | None:
    """Convert a Qt key enum (and optional Win32 nativeVirtualKey) to a supported VK code."""
    if Qt.Key_A <= qt_key <= Qt.Key_Z:
        return ord("A") + (qt_key - Qt.Key_A)
    if Qt.Key_0 <= qt_key <= Qt.Key_9:
        return ord("0") + (qt_key - Qt.Key_0)
    if Qt.Key_F1 <= qt_key <= Qt.Key_F24:
        return 0x70 + (qt_key - Qt.Key_F1)

    qt_map: dict[int, int] = {
        int(Qt.Key_Print): 0x2C,
        int(Qt.Key_Pause): 0x13,
        int(Qt.Key_Insert): 0x2D,
        int(Qt.Key_Delete): 0x2E,
        int(Qt.Key_Home): 0x24,
        int(Qt.Key_End): 0x23,
        int(Qt.Key_PageUp): 0x21,
        int(Qt.Key_PageDown): 0x22,
        int(Qt.Key_Space): 0x20,
        int(Qt.Key_Tab): 0x09,
        int(Qt.Key_Return): 0x0D,
        int(Qt.Key_Enter): 0x0D,
        int(Qt.Key_Backspace): 0x08,
        int(Qt.Key_Up): 0x26,
        int(Qt.Key_Down): 0x28,
        int(Qt.Key_Left): 0x25,
        int(Qt.Key_Right): 0x27,
        int(Qt.Key_CapsLock): 0x14,
        int(Qt.Key_ScrollLock): 0x91,
        int(Qt.Key_NumLock): 0x90,
        int(Qt.Key_Minus): 0xBD,
        int(Qt.Key_Equal): 0xBB,
        int(Qt.Key_BracketLeft): 0xDB,
        int(Qt.Key_BracketRight): 0xDD,
        int(Qt.Key_Backslash): 0xDC,
        int(Qt.Key_Semicolon): 0xBA,
        int(Qt.Key_Apostrophe): 0xDE,
        int(Qt.Key_Comma): 0xBC,
        int(Qt.Key_Period): 0xBE,
        int(Qt.Key_Slash): 0xBF,
        int(Qt.Key_QuoteLeft): 0xC0,
    }
    if int(qt_key) in qt_map:
        return qt_map[int(qt_key)]
    if native_vk in VK_TO_KEY_NAME:
        return native_vk
    return None


class _Win32HotkeyEventFilter(QAbstractNativeEventFilter):
    """Qt native event filter listening for WM_HOTKEY on a registered HWND."""

    def __init__(self, hotkey_id: int, hwnd: int, callback: Callable[[], None]) -> None:
        super().__init__()
        self._hotkey_id = hotkey_id
        self._hwnd = hwnd
        self._callback = callback

    def nativeEventFilter(self, eventType: object, message: object) -> tuple[bool, int]:
        if sys.platform == "win32" and eventType == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id and (
                msg.hWnd == 0 or msg.hWnd == self._hwnd
            ):
                self._callback()
                return (True, 0)
        return (False, 0)


class GlobalHotkeyManager(QObject):
    """Manages a single global Win32 hotkey tied to a persistent hidden HWND."""

    triggered = Signal()

    def __init__(self, parent: QObject | None = None, hotkey_id: int = _DEFAULT_HOTKEY_ID) -> None:
        super().__init__(parent)
        self._hotkey_id = hotkey_id
        self._host_widget = QWidget()
        self._host_widget.setObjectName("HotkeyHostWidget")
        self._hwnd = int(self._host_widget.winId())

        self._registered = False
        self._enabled = True
        self._current_hotkey: str | None = None
        self._current_spec: tuple[int, int] | None = None

        self._event_filter = _Win32HotkeyEventFilter(
            hotkey_id=self._hotkey_id,
            hwnd=self._hwnd,
            callback=self._on_hotkey_message,
        )
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._event_filter)

    @property
    def host_hwnd(self) -> int:
        return self._hwnd

    @property
    def current_hotkey(self) -> str | None:
        return self._current_hotkey

    def _on_hotkey_message(self) -> None:
        if self._enabled:
            self.triggered.emit()

    def _win32_register(self, mods: int, vk: int) -> tuple[bool, str]:
        if sys.platform != "win32":
            return (False, "Global hotkeys are only supported on Windows.")
        user32 = ctypes.windll.user32
        user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user32.RegisterHotKey.restype = wintypes.BOOL
        ctypes.set_last_error(0)
        ok = bool(user32.RegisterHotKey(wintypes.HWND(self._hwnd), self._hotkey_id, mods | MOD_NOREPEAT, vk))
        if ok:
            return (True, "")
        err_code = ctypes.GetLastError()
        if err_code == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
            return (
                False,
                "That key combination is already reserved by Windows or another application.",
            )
        return (False, f"Windows refused the hotkey registration (error {err_code}).")

    def _win32_unregister(self) -> None:
        if sys.platform == "win32" and self._registered:
            user32 = ctypes.windll.user32
            user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.UnregisterHotKey.restype = wintypes.BOOL
            user32.UnregisterHotKey(wintypes.HWND(self._hwnd), self._hotkey_id)
        self._registered = False

    def register(self, hotkey_str: str) -> tuple[bool, str]:
        """Validate and register hotkey_str globally, restoring the prior hotkey on failure."""
        try:
            mods, vk = parse_hotkey(hotkey_str)
            canonical = format_hotkey(mods, vk)
        except ValueError as exc:
            return (False, str(exc))

        if self._registered and self._current_spec == (mods, vk):
            self._current_hotkey = canonical
            return (True, "")

        prev_hotkey = self._current_hotkey
        prev_spec = self._current_spec
        was_registered = self._registered

        self._win32_unregister()
        ok, err = self._win32_register(mods, vk)
        if ok:
            self._registered = True
            self._current_hotkey = canonical
            self._current_spec = (mods, vk)
            return (True, "")

        if was_registered and prev_spec is not None:
            restored, _ = self._win32_register(*prev_spec)
            self._registered = restored
            if restored:
                self._current_hotkey = prev_hotkey
                self._current_spec = prev_spec
        return (False, err)

    def unregister(self) -> None:
        """Unregister the active global hotkey if registered."""
        self._win32_unregister()
        self._current_hotkey = None
        self._current_spec = None

    def set_enabled(self, enabled: bool) -> None:
        """Enable or temporarily pause emitting triggered events (and Win32 registration)."""
        self._enabled = enabled

    def dispose(self) -> None:
        """Remove native event filter and release Win32 registration."""
        self.unregister()
        app = QApplication.instance()
        if app is not None:
            app.removeNativeEventFilter(self._event_filter)
        self._host_widget.deleteLater()
