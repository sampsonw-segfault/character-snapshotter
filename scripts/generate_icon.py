"""Generate the multi-resolution Windows .ico file from the shared tray icon renderer."""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image
from PySide6.QtGui import QGuiApplication, QImage

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.tray import render_app_icon_pixmap  # noqa: E402


ICON_SIZES = [256, 128, 64, 48, 32, 24, 16]


def main() -> int:
    _app = QGuiApplication(sys.argv)
    images: list[Image.Image] = []
    for sz in ICON_SIZES:
        pixmap = render_app_icon_pixmap(sz)
        qimg = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
        raw = bytes(qimg.constBits())
        images.append(Image.frombytes("RGBA", (sz, sz), raw))

    out_path = _PROJECT_ROOT / "assets" / "app.ico"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_path,
        format="ICO",
        sizes=[(sz, sz) for sz in ICON_SIZES],
        append_images=images[1:],
    )
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
