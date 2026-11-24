"""Localhost HTTP + Server-Sent Events companion bridge for Character Collager."""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, Qt, QThread, Signal

from src import __version__
from src.config import AppConfig


ALLOWED_REMOTE_ORIGIN = "https://sampsonw-segfault.github.io"
MAX_JSON_BODY_BYTES = 65_536
MAX_RECENT_CAPTURES = 50


def is_allowed_origin(origin: str | None) -> bool:
    """Return True for non-browser callers, GitHub Pages host, and local loopback origins."""
    if origin is None or origin == "":
        return True

    parsed = urlsplit(origin)
    if parsed.username or parsed.password:
        return False
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return False

    if origin == ALLOWED_REMOTE_ORIGIN:
        return True

    if parsed.scheme in ("http", "https") and parsed.hostname in ("localhost", "127.0.0.1"):
        return True

    return False


def config_to_bridge_dict(cfg: AppConfig) -> dict[str, Any]:
    """Serialize safe bridge-visible configuration fields."""
    return {
        "hotkey": cfg.hotkey,
        "output_dir": cfg.output_dir,
        "filename_prefix": cfg.filename_prefix,
        "default_width": cfg.default_width,
        "default_height": cfg.default_height,
        "box_width": cfg.box_width,
        "box_height": cfg.box_height,
        "square_lock": cfg.square_lock,
        "scale": cfg.scale,
        "overlay_opacity": cfg.overlay_opacity,
        "play_chime": cfg.play_chime,
        "capture_mode": cfg.capture_mode,
        "video_format": cfg.video_format,
        "video_fps": cfg.video_fps,
        "record_cursor": cfg.record_cursor,
        "bridge_enabled": cfg.bridge_enabled,
        "bridge_port": cfg.bridge_port,
    }


@dataclass(frozen=True)
class CaptureRecord:
    """In-memory descriptor for a captured snapshot streamed via opaque ID."""

    id: str
    file_name: str
    folder_name: str
    full_path: Path
    width: int
    height: int
    captured_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "file_name": self.file_name,
            "folder_name": self.folder_name,
            "width": self.width,
            "height": self.height,
            "captured_at": self.captured_at,
        }


class _MainThreadInvoker(QObject):
    """Marshals HTTP worker callbacks safely onto the Qt main thread."""

    _invoke_signal = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._invoke_signal.connect(self._execute, Qt.QueuedConnection)

    def _execute(self, fn: Callable[[], Any]) -> None:
        fn()

    def call(self, fn: Callable[[], Any], timeout: float = 5.0) -> Any:
        if QThread.currentThread() == self.thread():
            return fn()

        done = threading.Event()
        result_box: dict[str, Any] = {}

        def _runner() -> None:
            try:
                result_box["value"] = fn()
            except Exception as exc:  # noqa: BLE001
                result_box["error"] = exc
            finally:
                done.set()

        self._invoke_signal.emit(_runner)
        if not done.wait(timeout):
            raise TimeoutError("Timed out waiting for Qt main thread.")
        if "error" in result_box:
            raise result_box["error"]
        return result_box.get("value")

    def call_async(self, fn: Callable[[], Any]) -> None:
        if QThread.currentThread() == self.thread():
            fn()
        else:
            self._invoke_signal.emit(fn)


class BridgeCallbacks:
    """Callbacks supplied by SnapshotterApp to inspect and mutate Qt app state."""

    def __init__(
        self,
        get_status: Callable[[], dict[str, Any]],
        apply_config_patch: Callable[[dict[str, Any]], tuple[bool, str]],
        trigger_action: Callable[[str], tuple[bool, str, bool]],
        browse_folder: Callable[[], None],
        open_folder: Callable[[], None],
        open_settings: Callable[[], None],
    ) -> None:
        self.get_status = get_status
        self.apply_config_patch = apply_config_patch
        self.trigger_action = trigger_action
        self.browse_folder = browse_folder
        self.open_folder = open_folder
        self.open_settings = open_settings


class _BridgeHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        bridge: LocalBridgeServer,
    ) -> None:
        self.bridge = bridge
        super().__init__(server_address, handler_class)


class _BridgeRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        # Silence standard stderr logging so pythonw / tests stay clean.
        return

    @property
    def _bridge(self) -> LocalBridgeServer:
        return self.server.bridge  # type: ignore[attr-defined]

    def _request_origin(self) -> str | None:
        return self.headers.get("Origin")

    def _send_cors_headers(self, origin: str | None) -> None:
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        self.send_header("Cache-Control", "no-store")

    def _send_json(self, status: int, payload: dict[str, Any], origin: str | None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers(origin)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_origin_or_reject(self) -> tuple[bool, str | None]:
        origin = self._request_origin()
        if not is_allowed_origin(origin):
            self._send_json(
                HTTPStatus.FORBIDDEN,
                {"ok": False, "error": "Origin not allowed"},
                origin=None,
            )
            return (False, None)
        return (True, origin)

    def _read_json_body(self, allow_empty: bool = False) -> tuple[bool, dict[str, Any], str]:
        raw_len = self.headers.get("Content-Length")
        if raw_len is None or raw_len == "0":
            return (True, {}, "") if allow_empty else (False, {}, "Request body must be JSON.")
        try:
            length = int(raw_len)
        except ValueError:
            return (False, {}, "Invalid Content-Length header.")
        if length < 0 or length > MAX_JSON_BODY_BYTES:
            return (False, {}, "Request body is too large.")
        if length == 0:
            return (True, {}, "") if allow_empty else (False, {}, "Request body must be JSON.")

        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return (False, {}, "Invalid JSON body.")
        if not isinstance(parsed, dict):
            return (False, {}, "JSON body must be an object.")
        return (True, parsed, "")

    def do_OPTIONS(self) -> None:  # noqa: N802
        allowed, origin = self._check_origin_or_reject()
        if not allowed:
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers(origin)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        allowed, origin = self._check_origin_or_reject()
        if not allowed:
            return

        path = urlsplit(self.path).path
        if path == "/api/status":
            try:
                status = self._bridge.build_status_payload()
                self._send_json(HTTPStatus.OK, status, origin)
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                    origin,
                )
            return

        if path == "/api/events":
            self._handle_sse(origin)
            return

        if path.startswith("/api/captures/"):
            capture_id = path[len("/api/captures/") :]
            self._handle_capture_download(capture_id, origin)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}, origin)

    def do_POST(self) -> None:  # noqa: N802
        allowed, origin = self._check_origin_or_reject()
        if not allowed:
            return

        path = urlsplit(self.path).path
        if path == "/api/config":
            ok, payload, err = self._read_json_body(allow_empty=False)
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": err}, origin)
                return
            try:
                applied, msg = self._bridge.invoke_apply_config(payload)
                if not applied:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": msg}, origin)
                    return
                status = self._bridge.build_status_payload()
                self._send_json(HTTPStatus.OK, {"ok": True, "status": status}, origin)
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                    origin,
                )
            return

        if path == "/api/trigger":
            ok, payload, err = self._read_json_body(allow_empty=True)
            if not ok:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": err}, origin)
                return
            action = str(payload.get("action", "toggle"))
            try:
                ran, msg, overlay_active = self._bridge.invoke_trigger(action)
                if not ran:
                    self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": msg}, origin)
                    return
                status = self._bridge.build_status_payload()
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "overlay_active": overlay_active, "status": status},
                    origin,
                )
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                    origin,
                )
            return

        if path == "/api/browse-folder":
            self._bridge.invoke_browse_folder_async()
            self._send_json(HTTPStatus.OK, {"ok": True, "opened": True}, origin)
            return

        if path == "/api/open-folder":
            try:
                self._bridge.invoke_open_folder()
                self._send_json(HTTPStatus.OK, {"ok": True}, origin)
            except Exception as exc:  # noqa: BLE001
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": str(exc)},
                    origin,
                )
            return

        if path == "/api/open-settings":
            self._bridge.invoke_open_settings_async()
            self._send_json(HTTPStatus.OK, {"ok": True}, origin)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"}, origin)

    def _handle_capture_download(self, capture_id: str, origin: str | None) -> None:
        if not capture_id or "/" in capture_id or "\\" in capture_id or ".." in capture_id:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Capture not found"}, origin)
            return

        record = self._bridge.get_capture(capture_id)
        if record is None or not record.full_path.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Capture not found"}, origin)
            return

        try:
            data = record.full_path.read_bytes()
        except OSError:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Capture unavailable"}, origin)
            return

        safe_name = record.file_name.replace('"', "")
        self.send_response(HTTPStatus.OK)
        self._send_cors_headers(origin)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Disposition", f'inline; filename="{safe_name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_sse(self, origin: str | None) -> None:
        sub_queue = self._bridge.subscribe_sse()
        try:
            self.send_response(HTTPStatus.OK)
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Private-Network", "true")
                self.send_header("Vary", "Origin")
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.close_connection = True

            initial_payload = json.dumps(self._bridge.build_status_payload())
            self.wfile.write(f"event: status\ndata: {initial_payload}\n\n".encode("utf-8"))
            self.wfile.flush()

            while not self._bridge.is_stopping:
                try:
                    item = sub_queue.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue

                if item is None:
                    break

                event_name, payload_json = item
                frame = f"event: {event_name}\ndata: {payload_json}\n\n".encode("utf-8")
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            return
        finally:
            self._bridge.unsubscribe_sse(sub_queue)


class LocalBridgeServer:
    """Loopback-only HTTP + SSE server bridging Character Snapshotter with Character Collager."""

    def __init__(self, callbacks: BridgeCallbacks, parent: QObject | None = None) -> None:
        self._callbacks = callbacks
        self._invoker = _MainThreadInvoker(parent)
        self._server: _BridgeHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._stopping = False
        self._actual_port = 0

        self._lock = threading.Lock()
        self._capture_seq = 0
        self._capture_order: deque[str] = deque()
        self._captures: dict[str, CaptureRecord] = {}
        self._subscribers: set[queue.Queue[tuple[str, str] | None]] = set()

    @property
    def is_running(self) -> bool:
        return self._server is not None

    @property
    def is_stopping(self) -> bool:
        return self._stopping

    @property
    def actual_port(self) -> int:
        return self._actual_port

    def start(self, port: int) -> tuple[bool, str]:
        """Start listening on 127.0.0.1:<port> (port=0 picks an ephemeral port)."""
        if self._server is not None:
            return (True, "")

        self._stopping = False
        try:
            server = _BridgeHTTPServer(("127.0.0.1", int(port)), _BridgeRequestHandler, self)
        except OSError as exc:
            return (False, str(exc))

        self._server = server
        self._actual_port = int(server.server_address[1])
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="SnapshotterLocalBridge",
            daemon=True,
        )
        self._thread.start()
        return (True, "")

    def stop(self) -> None:
        """Stop listening and close active SSE streams cleanly."""
        self._stopping = True
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(None)
            except queue.Full:
                pass

        server = self._server
        self._server = None
        self._actual_port = 0
        if server is not None:
            server.shutdown()
            server.server_close()

        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)

    def subscribe_sse(self) -> queue.Queue[tuple[str, str] | None]:
        q: queue.Queue[tuple[str, str] | None] = queue.Queue(maxsize=128)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe_sse(self, q: queue.Queue[tuple[str, str] | None]) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def broadcast_event(self, event_name: str, payload: dict[str, Any]) -> None:
        data = json.dumps(payload)
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait((event_name, data))
            except queue.Full:
                # Drop oldest entry if a client fell behind so active captures still push through.
                try:
                    q.get_nowait()
                    q.put_nowait((event_name, data))
                except (queue.Empty, queue.Full):
                    pass

    def broadcast_status(self, status_payload: dict[str, Any]) -> None:
        self.broadcast_event("config_updated", status_payload)

    def broadcast_overlay_state(self, overlay_active: bool) -> None:
        self.broadcast_event("overlay_state", {"overlay_active": overlay_active})

    def record_capture(self, file_path: Path, width: int, height: int) -> dict[str, Any]:
        """Register a newly captured PNG in memory and notify all SSE listeners."""
        with self._lock:
            self._capture_seq += 1
            cap_id = f"cap_{self._capture_seq}"
            record = CaptureRecord(
                id=cap_id,
                file_name=file_path.name,
                folder_name=file_path.parent.name,
                full_path=file_path,
                width=int(width),
                height=int(height),
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
            self._captures[cap_id] = record
            self._capture_order.append(cap_id)
            while len(self._capture_order) > MAX_RECENT_CAPTURES:
                evicted = self._capture_order.popleft()
                self._captures.pop(evicted, None)
            meta = record.to_public_dict()

        self.broadcast_event("snapshot_captured", meta)
        return meta

    def get_capture(self, capture_id: str) -> CaptureRecord | None:
        with self._lock:
            return self._captures.get(capture_id)

    def list_recent_captures(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                self._captures[cid].to_public_dict()
                for cid in reversed(self._capture_order)
                if cid in self._captures
            ]

    def build_status_payload(self) -> dict[str, Any]:
        base = self._invoker.call(self._callbacks.get_status)
        return {
            "app": "character-snapshotter",
            "version": __version__,
            "port": self._actual_port,
            "overlay_active": bool(base["overlay_active"]),
            "config": base["config"],
            "recent_captures": self.list_recent_captures(),
        }

    def invoke_apply_config(self, patch: dict[str, Any]) -> tuple[bool, str]:
        return self._invoker.call(lambda: self._callbacks.apply_config_patch(patch))

    def invoke_trigger(self, action: str) -> tuple[bool, str, bool]:
        return self._invoker.call(lambda: self._callbacks.trigger_action(action))

    def invoke_browse_folder_async(self) -> None:
        self._invoker.call_async(self._callbacks.browse_folder)

    def invoke_open_folder(self) -> None:
        self._invoker.call(self._callbacks.open_folder)

    def invoke_open_settings_async(self) -> None:
        self._invoker.call_async(self._callbacks.open_settings)
