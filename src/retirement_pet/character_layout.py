"""Geometry-driven character layout (V12-01).

Turns the manifest ``geometry`` block into the one transform shared by the
body painter, the bubble layer, the desktop-input mask and click testing.
Everything is computed in device-independent window coordinates (DIP);
Qt 6 maps floats through QPainter to device pixels in a single resample.

Layout rule (V12-01 requirements 2-3):

- one uniform scale per composition, derived from ``reference_height``
  against the available viewport height, then reduced if the *action
  motion bounds* would not fit - never per-frame alpha bounds, so the
  character cannot "breathe" between frames;
- ``base_anchor`` lands on a stable foot line (viewport bottom minus a
  small margin), horizontally centered; layout/panel changes keep the
  foot line fixed because the cat area itself never moves inside the
  widget;
- if clamping shifts the mapped motion bounds, the whole character moves
  by one evidence-backed offset (foot moves with it) instead of being
  rescaled;
- any malformed geometry returns ``None`` and the renderer falls back to
  the legacy whole-PNG fit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRect, QRectF

FOOT_MARGIN_DIP = 2.0
SIDE_MARGIN_DIP = 4.0
TOP_MARGIN_DIP = 4.0


def _number(value) -> float | None:
    """Finite number, not bool (manifest ints and explicit floats only)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _size(value) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    width = _number(value.get("width"))
    height = _number(value.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return width, height


def _point(value) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    if x is None or y is None:
        return None
    return x, y


def _rect(value) -> QRectF | None:
    if not isinstance(value, dict):
        return None
    x = _number(value.get("x"))
    y = _number(value.get("y"))
    size = _size(value)
    if x is None or y is None or size is None:
        return None
    width, height = size
    if x < 0 or y < 0:
        return None
    return QRectF(x, y, width, height)


@dataclass(frozen=True)
class HitShape:
    """One hit region in logical canvas coordinates."""

    kind: str                    # "rect" | "circle" | "polygon"
    rect: QRectF | None = None   # rect shape, or circle/polygon bounds
    center: QPointF | None = None
    radius: float = 0.0
    polygon: tuple[QPointF, ...] = field(default=())

    def contains(self, point: QPointF) -> bool:
        if self.kind == "rect" and self.rect is not None:
            return self.rect.contains(point)
        if self.kind == "circle" and self.center is not None:
            dx = point.x() - self.center.x()
            dy = point.y() - self.center.y()
            return dx * dx + dy * dy <= self.radius * self.radius
        # QPolygonF setup belongs to the GUI tier; tests exercise rect and
        # circle paths, polygons fall back to their bounding rect here.
        return self.rect is not None and self.rect.contains(point)


@dataclass(frozen=True)
class CharacterGeometry:
    """Parsed, validated manifest geometry in logical canvas coordinates."""

    canvas_size: tuple[float, float]
    content_bounds: QRectF
    motion_bounds: QRectF
    base_anchor: QPointF
    bubble_anchor: QPointF
    reference_height: float
    hit_shapes: tuple[HitShape, ...] = ()

    @classmethod
    def from_character(cls, character: dict) -> "CharacterGeometry | None":
        """Parse one manifest character; None means "use legacy fit"."""
        geometry = character.get("geometry")
        if not isinstance(geometry, dict):
            return None
        canvas = _size(geometry.get("logical_canvas"))
        content = _rect(geometry.get("content_bounds"))
        motion = _rect(geometry.get("motion_bounds"))
        base = _point(geometry.get("base_anchor"))
        bubble = _point(geometry.get("bubble_anchor"))
        reference_height = _number(geometry.get("reference_height"))
        if (canvas is None or content is None or motion is None
                or base is None or bubble is None or reference_height is None
                or reference_height <= 0):
            return None
        canvas_w, canvas_h = canvas
        if content.right() > canvas_w + 1e-6 or content.bottom() > canvas_h + 1e-6:
            return None
        if (motion.right() > canvas_w + 1e-6
                or motion.bottom() > canvas_h + 1e-6):
            return None
        if not (0 <= base[0] <= canvas_w and 0 <= base[1] <= canvas_h):
            return None
        if not (0 <= bubble[0] <= canvas_w and 0 <= bubble[1] <= canvas_h):
            return None
        return cls(
            canvas_size=canvas,
            content_bounds=content,
            motion_bounds=motion,
            base_anchor=QPointF(base[0], base[1]),
            bubble_anchor=QPointF(bubble[0], bubble[1]),
            reference_height=reference_height,
            hit_shapes=cls._parse_hit_shapes(geometry.get("hit_regions")),
        )

    @staticmethod
    def _parse_hit_shapes(regions) -> tuple[HitShape, ...]:
        if not isinstance(regions, list):
            return ()
        shapes: list[HitShape] = []
        for region in regions[:16]:
            if not isinstance(region, dict):
                continue
            shape = region.get("shape")
            if shape == "rect":
                rect = _rect(region)
                if rect is not None:
                    shapes.append(HitShape("rect", rect=rect))
            elif shape == "circle":
                center = _point(region.get("center"))
                radius = _number(region.get("radius"))
                if center is not None and radius is not None and radius > 0:
                    bounds = QRectF(
                        center[0] - radius, center[1] - radius,
                        radius * 2, radius * 2)
                    shapes.append(HitShape(
                        "circle", rect=bounds,
                        center=QPointF(center[0], center[1]),
                        radius=radius))
            elif shape == "polygon":
                points = region.get("points")
                if not isinstance(points, list) or len(points) < 3:
                    continue
                parsed: list[QPointF] = []
                for entry in points[:32]:
                    point = _point(entry)
                    if point is None:
                        parsed.clear()
                        break
                    parsed.append(QPointF(point[0], point[1]))
                if parsed:
                    xs = [p.x() for p in parsed]
                    ys = [p.y() for p in parsed]
                    bounds = QRectF(
                        QPointF(min(xs), min(ys)),
                        QPointF(max(xs), max(ys)))
                    shapes.append(HitShape(
                        "polygon", rect=bounds, polygon=tuple(parsed)))
        return tuple(shapes)


@dataclass(frozen=True)
class BodyLayout:
    """The one character transform for one composition (window DIP)."""

    body_rect: QRectF            # full logical canvas mapped into the window
    foot_point: QPointF          # where base_anchor lands
    bubble_origin: QPointF       # where bubble_anchor lands
    scale: float
    clamped: bool                # an evidence-backed whole-body shift applied
    hit_shapes: tuple[HitShape, ...] = ()   # mapped into window coordinates
    motion_rect: QRectF | None = None       # mapped motion bounds (mask use)

    def hit_contains(self, point: QPointF) -> bool:
        """True when ``point`` is inside any mapped hit region.

        Without declared regions every point inside the body rect counts
        (the whole character is the click target)."""
        if not self.hit_shapes:
            return self.body_rect.contains(point)
        return any(shape.contains(point) for shape in self.hit_shapes)


def compute_body_layout(viewport: QRectF,
                        geometry: CharacterGeometry) -> BodyLayout:
    """Place one character inside ``viewport`` per the layout rule."""
    avail_w = max(1.0, viewport.width() - SIDE_MARGIN_DIP * 2.0)
    avail_h = max(1.0, viewport.height() - TOP_MARGIN_DIP - FOOT_MARGIN_DIP)
    scale = avail_h / geometry.reference_height
    motion = geometry.motion_bounds
    scale = min(scale,
                avail_w / motion.width(),
                avail_h / motion.height())
    scale = max(scale, 1e-6)

    foot = QPointF(viewport.center().x(),
                   viewport.bottom() - FOOT_MARGIN_DIP)
    top_left = QPointF(
        foot.x() - geometry.base_anchor.x() * scale,
        foot.y() - geometry.base_anchor.y() * scale,
    )

    # Defensive clamp: the scale step already fits the motion bounds, so
    # at most one side can violate; the whole body shifts, never rescales.
    mapped_left = top_left.x() + motion.x() * scale
    mapped_right = mapped_left + motion.width() * scale
    mapped_top = top_left.y() + motion.y() * scale
    mapped_bottom = mapped_top + motion.height() * scale
    dx = dy = 0.0
    if mapped_left < viewport.left():
        dx = viewport.left() - mapped_left
    elif mapped_right > viewport.right():
        dx = viewport.right() - mapped_right
    if mapped_top < viewport.top():
        dy = viewport.top() - mapped_top
    elif mapped_bottom > viewport.bottom():
        dy = viewport.bottom() - mapped_bottom
    if dx or dy:
        top_left += QPointF(dx, dy)

    body_rect = QRectF(
        top_left.x(), top_left.y(),
        geometry.canvas_size[0] * scale,
        geometry.canvas_size[1] * scale,
    )
    motion_rect = QRectF(
        top_left.x() + motion.x() * scale,
        top_left.y() + motion.y() * scale,
        motion.width() * scale,
        motion.height() * scale,
    )
    hit_shapes = tuple(
        HitShape(
            shape.kind,
            rect=(QRectF(shape.rect.topLeft() * scale + top_left,
                         shape.rect.bottomRight() * scale + top_left)
                  if shape.rect is not None else None),
            center=(QPointF(shape.center.x() * scale + top_left.x(),
                            shape.center.y() * scale + top_left.y())
                    if shape.center is not None else None),
            radius=shape.radius * scale,
            polygon=tuple(QPointF(p.x() * scale + top_left.x(),
                                  p.y() * scale + top_left.y())
                          for p in shape.polygon),
        )
        for shape in geometry.hit_shapes
    )
    return BodyLayout(
        body_rect=body_rect,
        foot_point=QPointF(
            geometry.base_anchor.x() * scale + top_left.x(),
            geometry.base_anchor.y() * scale + top_left.y(),
        ),
        bubble_origin=QPointF(
            geometry.bubble_anchor.x() * scale + top_left.x(),
            geometry.bubble_anchor.y() * scale + top_left.y(),
        ),
        scale=scale,
        clamped=bool(dx or dy),
        hit_shapes=hit_shapes,
        motion_rect=motion_rect,
    )


def widget_rect_for_mask(rect: QRectF) -> QRect:
    """Round a DIP rect outward so a mask never clips painted content."""
    return QRect(int(math.floor(rect.left())),
                 int(math.floor(rect.top())),
                 int(math.ceil(rect.right())) - int(math.floor(rect.left())),
                 int(math.ceil(rect.bottom())) - int(math.floor(rect.top())))


__all__ = [
    "CharacterGeometry",
    "BodyLayout",
    "HitShape",
    "compute_body_layout",
    "widget_rect_for_mask",
    "FOOT_MARGIN_DIP",
    "SIDE_MARGIN_DIP",
    "TOP_MARGIN_DIP",
]
