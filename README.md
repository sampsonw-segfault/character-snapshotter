# Character Snapshotter

A lightweight Windows screen-region snapshot utility built for quick, repeatable portrait and UI captures.

## Features

- **2-Tap Global Hotkey Workflow** (defaults to `Win+Alt+S`, customizable in Settings):
  1. **First press:** Opens a translucent multi-monitor overlay with a crisp dashed capture box (defaults to `500×500` px and remembers your last position and size).
  2. **Second press:** Hides the overlay cleanly, captures the framed region, applies the float **Scale** multiplier (default `1`), saves a timestamped `.png` (`capture_YYYYMMDD_HHMMSS.png`), plays an optional audio chime, and stays closed until the next hotkey press.
- **Multi-Monitor & Per-Monitor DPI Support:** Drag the capture box smoothly across displays—even with negative monitor coordinates or mixed Windows DPI scaling—so `500×500` is always exact physical pixels.
- **Square Lock & Free Rectangle:** Hold `Shift` while dragging a resize handle to temporarily invert `1:1` square lock (or press `S`), and type exact capture dimensions directly into the pill.
- **Uniform Float Scaling:** Set the **Scale** multiplier (e.g. `1`, `0.5`, `2`, `0.7143`) on the HUD pill or in Settings to uniformly scale both dimensions using Pillow Lanczos resampling without stretching or warping.
- **System Tray + Floating HUD Pill:** Lives quietly in the Windows system tray; quick dimension and scale controls stay right outside the capture box without entering the screenshot.
- **Character Collager Companion Bridge:** Runs an optional loopback HTTP/SSE server (`127.0.0.1:47821`, toggleable in Settings) so [Character Collager](https://sampsonw-segfault.github.io/character-collager/) automatically detects the helper, lets you adjust capture/scaling settings from the web toolbar, and live-imports new snapshots directly into the Portrait Browser.

## Shortcuts While Overlay Is Open

| Input | Action |
| --- | --- |
| Global Hotkey (default `Win+Alt+S`) | Capture region and close overlay |
| Drag inside box | Move capture box across displays |
| Drag corners or edges | Resize capture box |
| `Shift` + Drag handle | Temporarily invert `1:1` square lock |
| `Arrow` keys (hold two for diagonal) | Nudge capture box by `1px` (opposing keys cancel) |
| `Shift + Arrow` keys | Nudge capture box by `10px` (including diagonals) |
| `R` | Reset capture box to configured default size (`500×500`) |
| `S` | Toggle `1:1` drag square lock |
| `Esc` or Right-click | Cancel overlay without capturing |

## Setup & Running

1. Ensure **Python 3.10+** (tested on Python 3.14) is installed on Windows.
2. Double-click **`start_snapshotter.bat`** (first run automatically creates `.venv` and installs `PySide6`, `mss`, and `Pillow`).
3. Look for the Character Snapshotter icon in your Windows system tray (right-click for **Trigger Overlay**, **Settings…**, **Open Output Folder**, or **Quit**).
4. For troubleshooting with console output visible, run **`start_snapshotter_console.bat`**.

> **Game Capture Tip:** When capturing from Crusader Kings III or other full-screen games, set the game's display mode to **Borderless Fullscreen** (or **Windowed**) so the Windows overlay can draw over the game window.
