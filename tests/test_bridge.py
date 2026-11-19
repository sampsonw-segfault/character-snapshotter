"""Unit and integration tests for the localhost HTTP + SSE bridge server."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, TypeVar

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.bridge import is_allowed_origin
from src.config import AppConfig, save_config
from src.geometry import ensure_dpi_awareness
from src.tray import SnapshotterApp

T = TypeVar("T")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    ensure_dpi_awareness()
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _run_with_qt(qapp: QApplication, fn: Callable[[], T], timeout: float = 5.0) -> T:
    """Run blocking HTTP client call in a worker thread while pumping Qt main-thread events."""
    fut: Future[T] = Future()

    def _worker() -> None:
        try:
            fut.set_result(fn())
        except BaseException as exc:  # noqa: BLE001
            fut.set_exception(exc)

    threading.Thread(target=_worker, daemon=True).start()
    iters = 0
    max_iters = int(timeout / 0.005)
    while not fut.done() and iters < max_iters:
        qapp.processEvents()
        threading.Event().wait(0.005)
        iters += 1
    qapp.processEvents()
    return fut.result(timeout=1.0)


@pytest.fixture()
def bridge_app(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[SnapshotterApp, int]:
    cfg_path = tmp_path / "config.json"
    monkeypatch.setattr("src.tray.save_config", lambda cfg: save_config(cfg, cfg_path))

    cfg = AppConfig(
        hotkey="Win+Alt+S",
        output_dir=str(tmp_path / "out"),
        box_x=100,
        box_y=100,
        box_width=500,
        box_height=500,
        square_lock=True,
        scale_enabled=False,
        scale_target_px=500,
        play_chime=False,
        bridge_enabled=True,
        bridge_port=0,
    )
    app_ctrl = SnapshotterApp(config=cfg, start_bridge=False)
    ok, err = app_ctrl.bridge.start(0)
    assert ok, f"Failed to start bridge on ephemeral port: {err}"
    port = app_ctrl.bridge.actual_port
    try:
        yield (app_ctrl, port)
    finally:
        app_ctrl.shutdown()
        qapp.processEvents()


def test_allowed_origin_checks() -> None:
    assert is_allowed_origin(None)
    assert is_allowed_origin("")
    assert is_allowed_origin("https://sampsonw-segfault.github.io")
    assert is_allowed_origin("http://localhost:5173")
    assert is_allowed_origin("http://127.0.0.1:4173")
    assert is_allowed_origin("https://localhost:8443")

    assert not is_allowed_origin("https://evil.example.com")
    assert not is_allowed_origin("https://sampsonw-segfault.github.io.evil.com")
    assert not is_allowed_origin("http://localhost.evil.com")


def test_bridge_status_and_cors_pna_headers(
    qapp: QApplication, bridge_app: tuple[SnapshotterApp, int]
) -> None:
    _app_ctrl, port = bridge_app

    for origin in ("https://sampsonw-segfault.github.io", "http://localhost:5173"):
        def _req(orig: str = origin) -> tuple[int, dict[str, str], dict[str, object]]:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/status",
                headers={"Origin": orig},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                headers = {k.lower(): v for k, v in resp.headers.items()}
                return (resp.status, headers, json.loads(resp.read().decode("utf-8")))

        status, headers, body = _run_with_qt(qapp, _req)
        assert status == 200
        assert body["app"] == "character-snapshotter"
        assert body["port"] == port
        assert body["overlay_active"] is False
        assert headers["access-control-allow-origin"] == origin
        assert headers["access-control-allow-private-network"] == "true"


def test_bridge_options_preflight_and_rejects_untrusted_origin(
    qapp: QApplication, bridge_app: tuple[SnapshotterApp, int]
) -> None:
    _app_ctrl, port = bridge_app

    def _preflight(origin: str) -> tuple[int, dict[str, str]]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3.0)
        try:
            conn.request(
                "OPTIONS",
                "/api/config",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Private-Network": "true",
                },
            )
            resp = conn.getresponse()
            resp.read()
            return (resp.status, {k.lower(): v for k, v in resp.getheaders()})
        finally:
            conn.close()

    ok_status, ok_headers = _run_with_qt(
        qapp, lambda: _preflight("https://sampsonw-segfault.github.io")
    )
    assert ok_status == 204
    assert ok_headers["access-control-allow-origin"] == "https://sampsonw-segfault.github.io"
    assert ok_headers["access-control-allow-private-network"] == "true"

    bad_status, _ = _run_with_qt(qapp, lambda: _preflight("https://evil.example.com"))
    assert bad_status == 403

    def _evil_get() -> int:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/status",
            headers={"Origin": "https://evil.example.com"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    def _evil_post() -> int:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config",
            data=b'{"scale_enabled": true}',
            headers={
                "Origin": "https://evil.example.com",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status
        except urllib.error.HTTPError as err:
            return err.code

    assert _run_with_qt(qapp, _evil_get) == 403
    assert _run_with_qt(qapp, _evil_post) == 403


def test_bridge_post_config_mutates_and_persists(
    qapp: QApplication, bridge_app: tuple[SnapshotterApp, int]
) -> None:
    app_ctrl, port = bridge_app

    def _patch() -> dict[str, object]:
        payload = json.dumps(
            {
                "scale_enabled": True,
                "scale_target_px": 500,
                "box_width": 700,
                "box_height": 700,
                "square_lock": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/config",
            data=payload,
            headers={
                "Origin": "https://sampsonw-segfault.github.io",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            assert resp.status == 200
            return json.loads(resp.read().decode("utf-8"))

    res = _run_with_qt(qapp, _patch)
    assert res["ok"] is True
    assert app_ctrl.config.scale_enabled is True
    assert app_ctrl.config.scale_target_px == 500
    assert app_ctrl.config.box_width == 700
    assert app_ctrl.config.box_height == 700
    assert app_ctrl.config.square_lock is False


def test_bridge_capture_download_and_traversal_rejection(
    qapp: QApplication, bridge_app: tuple[SnapshotterApp, int], tmp_path: Path
) -> None:
    app_ctrl, port = bridge_app
    png_path = tmp_path / "sample_500.png"
    img = Image.new("RGB", (500, 500), (218, 165, 32))
    img.save(png_path, format="PNG")
    expected_bytes = png_path.read_bytes()

    meta = app_ctrl.bridge.record_capture(png_path, 500, 500)

    def _fetch(path: str) -> tuple[int, str, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3.0)
        try:
            conn.request(
                "GET",
                path,
                headers={"Origin": "https://sampsonw-segfault.github.io"},
            )
            resp = conn.getresponse()
            data = resp.read()
            return (resp.status, resp.getheader("Content-Type", ""), data)
        finally:
            conn.close()

    status, ctype, data = _run_with_qt(qapp, lambda: _fetch(f"/api/captures/{meta['id']}"))
    assert status == 200
    assert ctype.startswith("image/png")
    assert data == expected_bytes

    bad_status, _, _ = _run_with_qt(qapp, lambda: _fetch("/api/captures/nonexistent"))
    assert bad_status == 404

    trav_status, _, _ = _run_with_qt(qapp, lambda: _fetch("/api/captures/../config.json"))
    assert trav_status == 404


def test_bridge_sse_events_and_clean_stop(
    qapp: QApplication, bridge_app: tuple[SnapshotterApp, int], tmp_path: Path
) -> None:
    app_ctrl, port = bridge_app
    png_path = tmp_path / "live_sse.png"
    Image.new("RGB", (500, 500), (24, 20, 16)).save(png_path, format="PNG")

    received: list[tuple[str, dict[str, object]]] = []
    ready = threading.Event()
    finished = threading.Event()

    def _sse_reader() -> None:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5.0)
        try:
            conn.request(
                "GET",
                "/api/events",
                headers={"Origin": "https://sampsonw-segfault.github.io"},
            )
            resp = conn.getresponse()
            assert resp.status == 200
            current_event = ""
            while True:
                raw_line = resp.fp.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8").rstrip("\r\n")
                if line.startswith("event: "):
                    current_event = line[len("event: ") :]
                elif line.startswith("data: "):
                    payload = json.loads(line[len("data: ") :])
                    received.append((current_event, payload))
                    if current_event == "status":
                        ready.set()
        finally:
            conn.close()
            finished.set()

    reader_thread = threading.Thread(target=_sse_reader, daemon=True)
    reader_thread.start()

    iters = 0
    while not ready.is_set() and iters < 400:
        qapp.processEvents()
        threading.Event().wait(0.005)
        iters += 1
    assert ready.is_set(), "Did not receive initial SSE status event"

    recorded = app_ctrl.bridge.record_capture(png_path, 500, 500)

    iters = 0
    while len(received) < 2 and iters < 400:
        qapp.processEvents()
        threading.Event().wait(0.005)
        iters += 1

    assert received[0][0] == "status"
    assert received[1][0] == "snapshot_captured"
    assert received[1][1]["id"] == recorded["id"]
    assert received[1][1]["file_name"] == "live_sse.png"

    # Stopping the bridge wakes SSE handlers immediately and exits cleanly
    app_ctrl.bridge.stop()
    assert finished.wait(timeout=2.0)
