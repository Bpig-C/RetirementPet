"""Generate the app icon (assets/icons/retirement_pet.ico + .png).

Run inside the project venv:
    .venv\\Scripts\\python.exe scripts\\make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))


def paint_face(painter, size: float) -> None:
    """A friendly cat face filling the square canvas."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QBrush, QColor, QPen, QPolygonF

    painter.setRenderHint(painter.RenderHint.Antialiasing)
    s = size / 64.0  # designed on a 64 grid

    painter.setPen(QPen(Qt.NoPen))
    painter.setBrush(QBrush(QColor(233, 223, 203)))
    # Ears
    painter.drawPolygon(QPolygonF([QPointF(10 * s, 20 * s), QPointF(16 * s, 3 * s), QPointF(29 * s, 15 * s)]))
    painter.drawPolygon(QPolygonF([QPointF(35 * s, 15 * s), QPointF(48 * s, 3 * s), QPointF(54 * s, 20 * s)]))
    painter.setBrush(QBrush(QColor(232, 184, 176)))
    painter.drawPolygon(QPolygonF([QPointF(14 * s, 16 * s), QPointF(17 * s, 7 * s), QPointF(24 * s, 14 * s)]))
    painter.drawPolygon(QPolygonF([QPointF(40 * s, 14 * s), QPointF(47 * s, 7 * s), QPointF(50 * s, 16 * s)]))
    # Head
    painter.setBrush(QBrush(QColor(233, 223, 203)))
    painter.drawEllipse(int(6 * s), int(12 * s), int(52 * s), int(48 * s))
    # Eyes
    painter.setBrush(QBrush(QColor(58, 58, 64)))
    painter.drawEllipse(int(20 * s), int(28 * s), int(8 * s), int(10 * s))
    painter.drawEllipse(int(36 * s), int(28 * s), int(8 * s), int(10 * s))
    painter.setBrush(QBrush(QColor(255, 255, 255, 210)))
    painter.drawEllipse(int(22 * s), int(30 * s), int(2 * s), int(3 * s))
    painter.drawEllipse(int(38 * s), int(30 * s), int(2 * s), int(3 * s))
    # Nose + mouth
    painter.setBrush(QBrush(QColor(185, 138, 133)))
    painter.drawPolygon(QPolygonF([QPointF(30 * s, 42 * s), QPointF(34 * s, 42 * s), QPointF(32 * s, 45 * s)]))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(138, 117, 112), max(1.0, 1.4 * s)))
    painter.drawArc(int(24 * s), int(44 * s), int(8 * s), int(6 * s), 200 * 16, 140 * 16)
    painter.drawArc(int(32 * s), int(44 * s), int(8 * s), int(6 * s), 200 * 16, 140 * 16)
    # Whiskers
    painter.setPen(QPen(QColor(120, 115, 110), max(1.0, 1.0 * s)))
    painter.drawLine(QPointF(8 * s, 34 * s), QPointF(2 * s, 32 * s))
    painter.drawLine(QPointF(8 * s, 40 * s), QPointF(2 * s, 41 * s))
    painter.drawLine(QPointF(56 * s, 34 * s), QPointF(62 * s, 32 * s))
    painter.drawLine(QPointF(56 * s, 40 * s), QPointF(62 * s, 41 * s))


def main() -> int:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    out_dir = PROJECT_ROOT / "assets" / "icons"
    out_dir.mkdir(parents=True, exist_ok=True)

    big = QImage(256, 256, QImage.Format_ARGB32)
    big.fill(0)
    painter = QPainter(big)
    paint_face(painter, 256)
    painter.end()
    png_path = out_dir / "retirement_pet.png"
    big.save(str(png_path), "PNG")

    ico_path = out_dir / "retirement_pet.ico"
    ok = big.save(str(ico_path), "ICO")
    if not ok or not ico_path.is_file() or ico_path.stat().st_size < 1000:
        print("ICO writer unavailable; keeping PNG only")
        ico_path.unlink(missing_ok=True)
    print(f"icon written: {png_path}" + (f", {ico_path}" if ico_path.exists() else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
