"""Prepare the repaired realistic-kitten 0.1.1 PNG assets.

The 0.1.0 pipeline flood-filled a pale background and leaked into the pale
kitten fur.  The 0.1.1 source images already contain an alpha matte.  This
script only removes the one-pixel colour-contaminated fringe, crops, and
normalises each sprite to a 512 x 512 transparent canvas.

Authoring dependencies: Pillow, NumPy, SciPy, and OpenCV.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from scipy import ndimage


ROOT = Path(__file__).resolve().parent.parent
MASTERS = ROOT / "character-work" / "realistic-kitten-0.1.1" / "generated-masters"
OUTPUT = ROOT / "assets" / "petpack" / "realistic-kitten-0.1.1" / "assets"
TARGET_SIZE = 512


def clean_cutout(source: Path, destination: Path) -> None:
    rgba = np.array(Image.open(source).convert("RGBA"))
    original_alpha = rgba[:, :, 3]
    inside = original_alpha >= 16
    if not inside.any() or inside.all():
        raise ValueError(f"{source.name}: expected both opaque and transparent pixels")

    kernel = np.ones((3, 3), np.uint8)
    solid = cv2.erode((original_alpha >= 224).astype(np.uint8), kernel, iterations=1).astype(bool)
    if not solid.any():
        raise ValueError(f"{source.name}: no stable foreground remains")

    # Replace only fringe RGB with the nearest stable foreground colour.  The
    # alpha channel is rebuilt below, so generated red/green matte pixels do
    # not bleed into Qt's smooth scaling.
    _, nearest = ndimage.distance_transform_edt(~solid, return_indices=True)
    fringe = inside & ~solid
    rgba[fringe, :3] = rgba[nearest[0][fringe], nearest[1][fringe], :3]

    core = cv2.erode(inside.astype(np.uint8), kernel, iterations=1) * 255
    alpha = cv2.GaussianBlur(core, (0, 0), 0.75)
    rgba[:, :, 3] = alpha

    ys, xs = np.where(alpha > 2)
    pad = 8
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(rgba.shape[1], int(xs.max()) + pad + 1)
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(rgba.shape[0], int(ys.max()) + pad + 1)

    crop = Image.fromarray(rgba[y0:y1, x0:x1])
    crop.thumbnail((TARGET_SIZE, TARGET_SIZE), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (TARGET_SIZE, TARGET_SIZE), (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((TARGET_SIZE - crop.width) // 2,
                                  (TARGET_SIZE - crop.height) // 2))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, "PNG", optimize=True)


def main() -> None:
    expected = {
        "eat.png",
        "exercise.png",
        "idle-closed.png",
        "idle-meow.png",
        "meeting.png",
        "music.png",
        "rest-yawn.png",
        "work.png",
    }
    found = {path.name for path in MASTERS.glob("*.png")}
    if found != expected:
        raise SystemExit(f"master set mismatch: missing={sorted(expected - found)}, extra={sorted(found - expected)}")

    for name in sorted(expected):
        clean_cutout(MASTERS / name, OUTPUT / name)
        print(f"prepared {name}")


if __name__ == "__main__":
    main()
