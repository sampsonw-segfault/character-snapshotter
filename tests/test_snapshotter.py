"""Automated unit and Qt integration tests for Character Snapshotter."""

from __future__ import annotations

import ctypes
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import mss
import mss.exception
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.capture import (
    capture_region_to_file,
    generate_timestamp_path,
    process_captured_image,
)
from src.config import AppConfig, CaptureMode, load_config, save_config
from src.geometry import (
    PhysicalRect,
    ScreenMapping,
    compute_proportional_output_size,
    compute_virtual_bounds,
    ensure_dpi_awareness,
    enumerate_win32_monitors,
    resize_box,
    snap_rect_to_square,
)
from src.hotkey import (
    WM_HOTKEY,
    GlobalHotkeyManager,
    format_hotkey,
    parse_hotkey,
)
from src.overlay import HANDLE_IDS, OverlayController
from src.settings_dialog import SettingsDialog
from src.tray import SnapshotterApp


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    ensure_dpi_awareness()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_config_defaults_roundtrip_and_fallback(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.json"

    default_cfg = load_config(cfg_path)
    assert default_cfg.hotkey == "Win+Alt+S"
    assert default_cfg.default_width == 500
    assert default_cfg.default_height == 500
    assert default_cfg.box_width == 500
    assert default_cfg.box_height == 500
    assert default_cfg.square_lock is True
    assert default_cfg.scale_enabled is False
    assert default_cfg.capture_mode == CaptureMode.BOX.value

    custom_cfg = AppConfig(
        hotkey="Ctrl+Shift+F9",
        output_dir=str(tmp_path / "out"),
        filename_prefix="portrait_",
        default_width=600,
        default_height=600,
        box_x=-1200,
        box_y=250,
        box_width=700,
        box_height=700,
        square_lock=False,
        scale_enabled=True,
        scale_anchor_axis="height",
        scale_target_px=500,
        overlay_opacity=0.6,
        play_chime=False,
    )
    save_config(custom_cfg, cfg_path)
    reloaded = load_config(cfg_path)
    assert reloaded == custom_cfg

    # Corrupt JSON falls back cleanly to defaults
    cfg_path.write_text("{broken json!!!", encoding="utf-8")
    fallback = load_config(cfg_path)
    assert fallback.hotkey == "Win+Alt+S"
    assert fallback.box_width == 500

    # Unknown fields and out-of-range numeric values are handled safely
    cfg_path.write_text(
        '{"unknown_key": 123, "box_width": 2, "overlay_opacity": 0.99, "scale_anchor_axis": "diagonal"}',
        encoding="utf-8",
    )
    sanitized = load_config(cfg_path)
    assert sanitized.box_width == 16
    assert sanitized.overlay_opacity == pytest.approx(0.85)
    assert sanitized.scale_anchor_axis == "width"


@pytest.mark.parametrize(
    ("cap_w", "cap_h", "scale_enabled", "axis", "target", "expected"),
    [
        (700, 700, True, "width", 500, (500, 500)),
        (350, 350, True, "width", 500, (500, 500)),
        (800, 400, True, "width", 500, (500, 250)),
        (800, 400, True, "height", 300, (600, 300)),
        (720, 480, False, "width", 500, (720, 480)),
    ],
)
def test_proportional_scaling_no_warping(
    cap_w: int,
    cap_h: int,
    scale_enabled: bool,
    axis: str,
    target: int,
    expected: tuple[int, int],
) -> None:
    out_w, out_h = compute_proportional_output_size(cap_w, cap_h, scale_enabled, axis, target)
    assert (out_w, out_h) == expected

    # Verify aspect ratio is preserved within 1px rounding tolerance
    src_ratio = cap_w / cap_h
    out_ratio = out_w / out_h
    assert abs(out_ratio - src_ratio) <= (1.0 / min(out_w, out_h)) + 1e-6


def test_negative_origin_hidpi_screen_mapping_roundtrip() -> None:
    mapping = ScreenMapping(
        device_name=r"\\.\DISPLAY1",
        physical=PhysicalRect(-2560, 0, 2560, 1440),
        logical_x=-2560,
        logical_y=0,
        logical_w=2048,
        logical_h=1152,
        is_primary=False,
    )

    assert mapping.physical_to_local(-2560, 0) == pytest.approx((0.0, 0.0))
    assert mapping.physical_to_local(-1280, 720) == pytest.approx((1024.0, 576.0))
    assert mapping.local_to_physical(1024.0, 576.0) == pytest.approx((-1280.0, 720.0))

    lx, ly, lw, lh = mapping.physical_rect_to_local_rect(PhysicalRect(-1530, 470, 500, 500))
    assert (lx, ly, lw, lh) == pytest.approx((824.0, 376.0, 400.0, 400.0))
    assert mapping.physical_to_qt_global(-1280, 720) == (-1536, 576)


def test_resize_box_all_handles_square_and_rect_with_negative_bounds() -> None:
    bounds = compute_virtual_bounds(
        [
            PhysicalRect(-2560, 0, 2560, 1440),
            PhysicalRect(0, 0, 2560, 1440),
        ]
    )
    assert bounds == PhysicalRect(-2560, 0, 5120, 1440)

    start = PhysicalRect(-500, 300, 500, 500)

    for handle in HANDLE_IDS:
        # Drag outward by +120px along the active handle vector
        dx = -120 if "w" in handle else (120 if "e" in handle else 0)
        dy = -80 if "n" in handle else (80 if "s" in handle else 0)

        sq = resize_box(
            start_rect=start,
            handle=handle,
            cur_px=100 + dx,
            cur_py=100 + dy,
            start_px=100,
            start_py=100,
            square_lock=True,
            bounds=bounds,
        )
        assert sq.width == sq.height
        assert sq.width > 500
        assert sq.x >= bounds.x and sq.right <= bounds.right
        assert sq.y >= bounds.y and sq.bottom <= bounds.bottom

        # Verify anchored corner or edge stayed pinned
        if handle == "se":
            assert (sq.x, sq.y) == (start.x, start.y)
        elif handle == "nw":
            assert (sq.right, sq.bottom) == (start.right, start.bottom)
        elif handle == "ne":
            assert (sq.x, sq.bottom) == (start.x, start.bottom)
        elif handle == "sw":
            assert (sq.right, sq.y) == (start.right, start.y)
        elif handle == "e":
            assert sq.x == start.x
        elif handle == "w":
            assert sq.right == start.right
        elif handle == "s":
            assert sq.y == start.y
        elif handle == "n":
            assert sq.bottom == start.bottom

    # Free rectangle resize changes only pulled dimensions
    free = resize_box(
        start_rect=start,
        handle="se",
        cur_px=300,
        cur_py=150,
        start_px=100,
        start_py=100,
        square_lock=False,
        bounds=bounds,
    )
    assert free == PhysicalRect(start.x, start.y, 700, 550)

    # Clamping against extreme drag past negative virtual left boundary
    clamped = resize_box(
        start_rect=PhysicalRect(-2500, 300, 500, 500),
        handle="nw",
        cur_px=-5000,
        cur_py=-5000,
        start_px=0,
        start_py=0,
        square_lock=True,
        bounds=bounds,
    )
    assert clamped.width == clamped.height
    assert clamped.x >= bounds.x and clamped.y >= bounds.y

    # Snap rect back to centered square
    snapped = snap_rect_to_square(PhysicalRect(100, 200, 640, 360), bounds)
    assert snapped.width == snapped.height == 640


def test_timestamp_path_and_image_processing(tmp_path: Path) -> None:
    fixed_time = datetime(2026, 11, 19, 1, 15, 30)
    p1 = generate_timestamp_path(tmp_path, prefix="capture_", now=fixed_time)
    assert p1.name == "capture_20261119_011530.png"
    p1.write_bytes(b"stub")

    p2 = generate_timestamp_path(tmp_path, prefix="capture_", now=fixed_time)
    assert p2.name == "capture_20261119_011530_1.png"
    p2.write_bytes(b"stub")

    p3 = generate_timestamp_path(tmp_path, prefix="capture_", now=fixed_time)
    assert p3.name == "capture_20261119_011530_2.png"

    # RGB downscale
    src_rgb = Image.new("RGB", (700, 700), (120, 80, 40))
    scaled_rgb = process_captured_image(src_rgb, scale_enabled=True, anchor_axis="width", target_px=500)
    assert scaled_rgb.mode == "RGB"
    assert scaled_rgb.size == (500, 500)

    # RGBA with alpha mask (future lasso hook)
    mask = Image.new("L", (800, 400), 0)
    mask.paste(255, (100, 50, 700, 350))
    src_rect = Image.new("RGB", (800, 400), (200, 150, 100))
    scaled_rgba = process_captured_image(
        src_rect,
        scale_enabled=True,
        anchor_axis="width",
        target_px=500,
        alpha_mask=mask,
    )
    assert scaled_rgba.mode == "RGBA"
    assert scaled_rgba.size == (500, 250)
    assert scaled_rgba.getpixel((0, 0))[3] == 0
    assert scaled_rgba.getpixel((250, 125))[3] == 255


class _FakeMSS:
    """Deterministic fake mss.MSS context manager recording grabbed physical regions."""

    grabbed_monitors: list[dict[str, int]] = []

    def __enter__(self) -> _FakeMSS:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def grab(self, monitor: dict[str, int]) -> SimpleNamespace:
        type(self).grabbed_monitors.append(dict(monitor))
        w = monitor["width"]
        h = monitor["height"]
        return SimpleNamespace(size=(w, h), bgra=bytes([10, 120, 230, 255]) * (w * h))


def test_capture_region_to_file_coords_bgra_and_scaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _FakeMSS.grabbed_monitors = []
    monkeypatch.setattr("src.capture.mss.MSS", _FakeMSS)

    p_primary = capture_region_to_file(
        rect=PhysicalRect(0, 0, 100, 100),
        output_dir=tmp_path,
        prefix="primary_",
        scale_enabled=False,
    )
    assert _FakeMSS.grabbed_monitors[-1] == {"left": 0, "top": 0, "width": 100, "height": 100}
    assert p_primary.exists()
    with Image.open(p_primary) as img:
        assert img.mode == "RGB"
        assert img.size == (100, 100)
        assert img.getpixel((0, 0)) == (230, 120, 10)

    p_neg = capture_region_to_file(
        rect=PhysicalRect(-500, 100, 200, 200),
        output_dir=tmp_path,
        prefix="neg_",
        scale_enabled=True,
        anchor_axis="width",
        target_px=500,
    )
    assert _FakeMSS.grabbed_monitors[-1] == {"left": -500, "top": 100, "width": 200, "height": 200}
    assert p_neg.exists()
    with Image.open(p_neg) as img:
        assert img.size == (500, 500)


def test_live_desktop_capture_if_unlocked(tmp_path: Path) -> None:
    try:
        with mss.MSS() as sct:
            sct.grab({"left": 0, "top": 0, "width": 16, "height": 16})
    except mss.exception.ScreenShotError:
        pytest.skip("Windows desktop is locked; live BitBlt unavailable")

    p_primary = capture_region_to_file(
        rect=PhysicalRect(0, 0, 100, 100),
        output_dir=tmp_path,
        prefix="live_primary_",
        scale_enabled=False,
    )
    assert p_primary.exists()
    with Image.open(p_primary) as img:
        assert img.size == (100, 100)

    win32_mons = [r for _, r, _ in enumerate_win32_monitors()]
    neg_rect = PhysicalRect(-500, 100, 200, 200)
    if any(m.intersects(neg_rect) for m in win32_mons):
        p_neg = capture_region_to_file(
            rect=neg_rect,
            output_dir=tmp_path,
            prefix="live_neg_",
            scale_enabled=True,
            anchor_axis="width",
            target_px=500,
        )
        assert p_neg.exists()
        with Image.open(p_neg) as img:
            assert img.size == (500, 500)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("Win+Alt+S", "Win+Alt+S"),
        ("alt+win+s", "Win+Alt+S"),
        ("Ctrl+Shift+F9", "Ctrl+Shift+F9"),
        ("F9", "F9"),
        ("Alt+PrintScreen", "Alt+PrintScreen"),
    ],
)
def test_hotkey_parse_and_format_roundtrip(raw: str, canonical: str) -> None:
    mods, vk = parse_hotkey(raw)
    assert format_hotkey(mods, vk) == canonical


@pytest.mark.parametrize("bad_hotkey", ["", "   ", "Ctrl+", "Ctrl+Alt", "Ctrl+Bogus", "Ctrl+A+B"])
def test_hotkey_invalid_rejection(bad_hotkey: str) -> None:
    with pytest.raises(ValueError):
        parse_hotkey(bad_hotkey)


def test_live_win32_global_hotkey_dispatch(qapp: QApplication) -> None:
    manager = GlobalHotkeyManager()
    hits: list[int] = []
    manager.triggered.connect(lambda: hits.append(1))

    try:
        ok, err = manager.register("Win+Alt+S")
        assert ok, f"Unexpected hotkey failure: {err}"
        assert manager.current_hotkey == "Win+Alt+S"

        ctypes.windll.user32.PostMessageW(manager.host_hwnd, WM_HOTKEY, 0x4353, 0)
        qapp.processEvents()
        assert len(hits) == 1
    finally:
        manager.dispose()


def test_qt_overlay_hud_and_settings_smoke(qapp: QApplication, tmp_path: Path) -> None:
    cfg = AppConfig(
        output_dir=str(tmp_path),
        box_x=100,
        box_y=100,
        box_width=500,
        box_height=500,
        square_lock=True,
        scale_enabled=False,
        scale_target_px=500,
    )
    controller = OverlayController()
    try:
        controller.show_overlay(cfg)
        qapp.processEvents()

        assert controller.is_active is True
        assert controller.box_rect.width == 500
        assert controller.box_rect.height == 500
        assert len(controller.overlay_widgets) >= 1

        hud = controller.hud_widget
        assert hud.isVisible()

        # Simulate changing capture width to 700 in 1:1 locked mode
        hud.cap_w_spin.setValue(700)
        qapp.processEvents()
        assert controller.box_rect.width == 700
        assert controller.box_rect.height == 700

        # Unlock square, set rect 800x400, enable proportional scaling to width 500 -> 500x250
        hud.square_btn.setChecked(False)
        hud.cap_w_spin.setValue(800)
        hud.cap_h_spin.setValue(400)
        hud.scale_checkbox.setChecked(True)
        hud.out_w_spin.setValue(500)
        qapp.processEvents()

        assert controller.box_rect.width == 800
        assert controller.box_rect.height == 400
        assert hud.out_w_spin.value() == 500
        assert hud.out_h_spin.value() == 250

        # Reset back to 500x500 via R key simulation / reset button
        hud.reset_btn.click()
        qapp.processEvents()
        assert controller.box_rect.width == 500
        assert controller.box_rect.height == 500

        # Verify hide_for_capture hides all widgets cleanly before callback
        called: list[bool] = []
        controller.hide_for_capture(lambda: called.append(True), delay_ms=0)
        qapp.processEvents()
        assert called == [True]
        assert controller.is_active is False
        assert not hud.isVisible()

        # Settings dialog smoke check
        dlg = SettingsDialog(cfg)
        assert dlg.hotkey_recorder.current_hotkey() == "Win+Alt+S"
        assert dlg.default_w_spin.value() == 500
        assert dlg.scale_target_spin.value() == 500
        dlg.close()
    finally:
        controller.close_overlay()
        qapp.processEvents()


def test_two_tap_snapshotter_app_flow(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_file = tmp_path / "app_config.json"
    out_dir = tmp_path / "snapshots"
    _FakeMSS.grabbed_monitors = []
    monkeypatch.setattr("src.capture.mss.MSS", _FakeMSS)
    monkeypatch.setattr("src.tray.save_config", lambda cfg: save_config(cfg, cfg_file))

    cfg = AppConfig(
        hotkey="Win+Alt+S",
        output_dir=str(out_dir),
        box_x=50,
        box_y=50,
        box_width=350,
        box_height=350,
        scale_enabled=True,
        scale_target_px=500,
        play_chime=False,
    )
    app_ctrl = SnapshotterApp(config=cfg)
    try:
        # 1st press opens overlay
        app_ctrl.on_hotkey_triggered()
        qapp.processEvents()
        assert app_ctrl.overlay_controller.is_active is True

        # 2nd press captures & closes overlay
        app_ctrl.on_hotkey_triggered()
        deadline_iters = 0
        while (app_ctrl.overlay_controller.is_active or app_ctrl.overlay_controller.is_capturing) and deadline_iters < 30:
            qapp.processEvents()
            ctypes.windll.kernel32.Sleep(20)
            deadline_iters += 1

        # Wait briefly for QTimer 45ms capture callback
        for _ in range(10):
            qapp.processEvents()
            if list(out_dir.glob("capture_*.png")):
                break
            ctypes.windll.kernel32.Sleep(25)

        saved_files = list(out_dir.glob("capture_*.png"))
        assert len(saved_files) == 1
        with Image.open(saved_files[0]) as img:
            assert img.size == (500, 500)
        assert not app_ctrl.overlay_controller.is_active
        assert cfg_file.exists()
    finally:
        app_ctrl.shutdown()
        qapp.processEvents()
