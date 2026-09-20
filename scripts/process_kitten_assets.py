"""Process realistic kitten master images into transparent PetPack assets.

Reads 1254×1254 RGB masters, removes the light background via flood-fill
from edges, crops to content, resizes to 512×512, and saves RGBA PNGs
into a staging directory ready for PetPack packaging.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage

#: authoring masters live OUTSIDE the public tree; point
#: KITTEN_MASTERS_DIR at the "masters" folder when rebuilding.
MASTERS = Path(os.environ.get(
    "KITTEN_MASTERS_DIR",
    str(Path(__file__).resolve().parent.parent / "character-work"
        / "realistic-kitten-assets-v1" / "masters")))
OUTPUT = Path(__file__).resolve().parent.parent / "assets" / "petpack" / \
    "realistic-kitten-0.1.0" / "assets"

TARGET_SIZE = 512
BG_TOLERANCE = 30  # per-channel tolerance for background detection


def is_background(pixel: QColor, bg_r: int, bg_g: int, bg_b: int) -> bool:
    """Check if a pixel is close to the background colour."""
    return (abs(pixel.red() - bg_r) < BG_TOLERANCE
            and abs(pixel.green() - bg_g) < BG_TOLERANCE
            and abs(pixel.blue() - bg_b) < BG_TOLERANCE)


def remove_background(img: QImage) -> QImage:
    """Convert RGB32 to ARGB32, flood-fill background from edges to
    transparent.  Returns a new image with alpha channel."""
    w, h = img.width(), img.height()
    result = img.convertToFormat(QImage.Format.Format_ARGB32)

    # average corner colour as background reference
    corners = [result.pixelColor(0, 0), result.pixelColor(w - 1, 0),
               result.pixelColor(0, h - 1), result.pixelColor(w - 1, h - 1)]
    bg_r = sum(c.red() for c in corners) // 4
    bg_g = sum(c.green() for c in corners) // 4
    bg_b = sum(c.blue() for c in corners) // 4

    # BFS flood fill from all edge pixels
    visited = [[False] * w for _ in range(h)]
    queue = []
    for x in range(w):
        for y in (0, h - 1):
            queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            queue.append((x, y))

    # mark background pixels
    bg_mask = [[False] * w for _ in range(h)]
    head = 0
    while head < len(queue):
        x, y = queue[head]
        head += 1
        if x < 0 or x >= w or y < 0 or y >= h or visited[y][x]:
            continue
        visited[y][x] = True
        c = result.pixelColor(x, y)
        if is_background(c, bg_r, bg_g, bg_b):
            bg_mask[y][x] = True
            queue.extend([(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)])

    # make background transparent
    for y in range(h):
        for x in range(w):
            if bg_mask[y][x]:
                c = result.pixelColor(x, y)
                result.setPixelColor(x, y, QColor(c.red(), c.green(),
                                                  c.blue(), 0))
    return result


def crop_to_content(img: QImage) -> QImage:
    """Crop to the bounding box of non-transparent pixels."""
    w, h = img.width(), img.height()
    min_x, min_y, max_x, max_y = w, h, 0, 0
    for y in range(h):
        for x in range(w):
            if img.pixelColor(x, y).alpha() > 0:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x <= min_x or max_y <= min_y:
        return img
    return img.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def process_image(src_path: Path, dst_path: Path) -> None:
    img = QImage(str(src_path))
    if img.isNull():
        print(f"  SKIP (unreadable): {src_path.name}")
        return

    # remove background
    img = remove_background(img)

    # crop to content
    img = crop_to_content(img)

    # resize to fit 512×512 preserving aspect ratio
    img = img.scaled(TARGET_SIZE, TARGET_SIZE,
                     Qt.AspectRatioMode.KeepAspectRatio,
                     Qt.TransformationMode.SmoothTransformation)

    # centre on 512×512 canvas
    canvas = QImage(TARGET_SIZE, TARGET_SIZE,
                    QImage.Format.Format_ARGB32)
    canvas.fill(Qt.GlobalColor.transparent)
    x = (TARGET_SIZE - img.width()) // 2
    y = (TARGET_SIZE - img.height()) // 2
    painter = __import__("PySide6.QtGui", fromlist=["QPainter"]).QPainter(canvas)
    painter.drawImage(x, y, img)
    painter.end()

    canvas.save(str(dst_path), "PNG")
    print(f"  OK: {dst_path.name} ({img.width()}x{img.height()} "
          f"-> {TARGET_SIZE}x{TARGET_SIZE})")


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for f in sorted(MASTERS.glob("*.png")):
        dst = OUTPUT / f.name
        print(f"Processing {f.name}...")
        process_image(f, dst)


if __name__ == "__main__":
    main()
