# Character Snapshotter

A lightweight Windows utility built for fine-tuned, pixel-perfect screen snapshots.

## Setup

1. Download **`CharacterSnapshotter.exe`** from the [latest GitHub Release](https://github.com/sampsonw-segfault/character-snapshotter/releases/latest).
2. Double-click `CharacterSnapshotter.exe` to run.
   - If Windows SmartScreen warns on first launch, click **More info** → **Run anyway**.
3. Look for the Character Snapshotter icon in your Windows system tray (right-click for **Trigger Overlay**, **Settings…**, **Open Output Folder**, or **Quit**).
4. Press **`Win+Alt+S`** (configurable in Settings) to open the capture overlay, then press it again (or click the camera button for a PNG, or the record button for a WebP/GIF clip).
5. Settings are saved automatically to `%APPDATA%\CharacterSnapshotter\config.json` (plain JSON, safe to read or back up) and screenshots default to `%USERPROFILE%\Pictures\CharacterSnapshotter\`.

## Features

- **Fine-Tuned, Pixel-Perfect Framing** (2-tap global hotkey, defaults to `Win+Alt+S`, customizable in Settings):
  - **First press:** Opens a translucent multi-monitor overlay with a crisp dashed capture box at your exact last size and position (defaults to `500×500` physical pixels).
  - **Second press:** Hides the overlay cleanly, captures the exact framed pixels, applies your optional **Scale** multiplier (default `1.00`), saves a timestamped `.png` (`capture_YYYYMMDD_HHMMSS.png`), plays an optional audio chime, and stays closed until the next hotkey press.
- **Precision Resizing & Pixel Nudging:** Drag corners freely to wherever you point (hold `Shift` to lock aspect ratio), drag top/bottom or left/right edges to adjust a single axis, type exact pixel dimensions into the floating pill, or nudge the frame `1px` or `10px` at a time with arrow keys (including diagonals).
- **Exact Physical Pixels Across Multi-Monitor & Mixed-DPI Setups:** Drag the capture frame smoothly across displays, even with negative monitor coordinates or mixed Windows DPI scaling, so a `500×500` capture is always an exact `500×500` physical pixels.
- **High-Quality Uniform Output Scaling:** Set the **Scale** multiplier (default `1.00`, range `0.05` to `9.99`) on the HUD pill or in Settings to uniformly resize both dimensions with Lanczos resampling without stretching or warping.
- **Animated WebP / GIF Video Capture:** Click the circle record button on the floating pill to record the framed region (video-only, defaulting to `WebP` at `30 FPS`, with `GIF`, `5` to `60 FPS`, and optional mouse pointer capture configurable in Settings). The viewport and controls lock while recording, and clicking the rounded-square stop button (or pressing the hotkey again) saves your scaled clip.
- **Unobtrusive System Tray + Floating HUD Pill:** Lives quietly in the Windows system tray; the quick capture button, record button, exact dimension inputs, and scale control sit right outside the capture frame so they never enter your screenshot.

## Shortcuts While Overlay Is Open

| Input | Action |
| --- | --- |
| Global Hotkey (default `Win+Alt+S`) | Capture PNG / stop active recording and close overlay |
| Drag inside box | Move capture box across displays |
| Drag top/bottom or left/right edges | Resize height only (top/bottom) or width only (left/right) |
| Drag corners | Resize width and height freely to the mouse pointer |
| `Shift` + Drag corner | Scale corners proportionally (locks aspect ratio) |
| `Arrow` keys (hold two for diagonal) | Nudge capture box by `1px` (opposing keys cancel) |
| `Shift + Arrow` keys | Nudge capture box by `10px` (including diagonals) |
| `R` | Reset capture box to configured default size (`500×500`) |
| `S` | Toggle proportional corner lock on or off |
| `Esc` or Right-click | Cancel overlay without capturing |

> **Game Capture Tip:** When capturing from full-screen games, set the game's display mode to **Borderless Fullscreen** (or **Windowed**) so the Windows overlay can draw over the game window.
