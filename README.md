# Character Snapshotter

A lightweight Windows screen-region snapshot utility built for quick, repeatable portrait and UI captures.

## Setup

1. Download **`CharacterSnapshotter.exe`** from the [latest GitHub Release](https://github.com/sampsonw-segfault/character-snapshotter/releases/latest).
2. Double-click `CharacterSnapshotter.exe` to run.
   - If Windows SmartScreen warns on first launch, click **More info** → **Run anyway**.
3. Look for the Character Snapshotter icon in your Windows system tray (right-click for **Trigger Overlay**, **Settings…**, **Open Output Folder**, or **Quit**).
4. Press **`Win+Alt+S`** (configurable in Settings) to open the capture overlay, then press it again (or click the camera button on the pill) to take a snapshot.
5. Settings are saved automatically to `%APPDATA%\CharacterSnapshotter\config.json` (plain JSON, safe to read or back up) and screenshots default to `%USERPROFILE%\Pictures\CharacterSnapshotter\`.

## Features

- **2-Tap Global Hotkey Workflow** (defaults to `Win+Alt+S`, customizable in Settings):
  - **First press:** Opens a translucent multi-monitor overlay with a crisp dashed capture box (defaults to `500×500` px and remembers your last position and size).
  - **Second press:** Hides the overlay cleanly, captures the framed region, applies the float **Scale** multiplier (default `1.00`), saves a timestamped `.png` (`capture_YYYYMMDD_HHMMSS.png`), plays an optional audio chime, and stays closed until the next hotkey press.
- **Multi-Monitor & Per-Monitor DPI Support:** Drag the capture box smoothly across displays, even with negative monitor coordinates or mixed Windows DPI scaling, so `500×500` is always exact physical pixels.
- **Square & Non-Square Capture Box:** Drag top/bottom edges to resize height only, left/right edges to resize width only, and corners to scale proportionally (or hold `Shift` while dragging a corner to resize freely); press `S` to toggle default corner proportionality, or type exact dimensions directly into the pill.
- **Uniform Float Scaling:** Set the **Scale** multiplier (default `1.00`, range `0.05` to `9.99`) on the HUD pill or in Settings to uniformly scale both dimensions using Pillow Lanczos resampling without stretching or warping.
- **System Tray + Floating HUD Pill:** Lives quietly in the Windows system tray; quick capture button, dimension boxes, and scale control stay right outside the capture box without entering the screenshot.

## Shortcuts While Overlay Is Open

| Input | Action |
| --- | --- |
| Global Hotkey (default `Win+Alt+S`) | Capture region and close overlay |
| Drag inside box | Move capture box across displays |
| Drag top/bottom or left/right edges | Resize height only (top/bottom) or width only (left/right) |
| Drag corners | Scale both dimensions proportionally |
| `Shift` + Drag corner | Invert proportional lock (resize corner freely) |
| `Arrow` keys (hold two for diagonal) | Nudge capture box by `1px` (opposing keys cancel) |
| `Shift + Arrow` keys | Nudge capture box by `10px` (including diagonals) |
| `R` | Reset capture box to configured default size (`500×500`) |
| `S` | Toggle proportional corner drag lock |
| `Esc` or Right-click | Cancel overlay without capturing |

> **Game Capture Tip:** When capturing from Crusader Kings III or other full-screen games, set the game's display mode to **Borderless Fullscreen** (or **Windowed**) so the Windows overlay can draw over the game window.
