"""Character geometry parsing and layout math (V12-01 requirements 2-3).

Structural assertions allow up to 1 DIP of float rounding, per the work
order's acceptance tolerance.
"""

from __future__ import annotations

import pytest

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QPainter

from retirement_pet.character_layout import (
    CharacterGeometry,
    compute_body_layout,
    widget_rect_for_mask,
)

VIEWPORT = QRectF(0, 0, 232, 236)

#: the PETPACK_SPEC 10 example geometry (square canvas)
SPEC_GEOMETRY = {
    "logical_canvas": {"width": 512, "height": 512},
    "content_bounds": {"x": 70, "y": 28, "width": 372, "height": 452},
    "motion_bounds": {"x": 30, "y": 12, "width": 452, "height": 486},
    "base_anchor": {"x": 256, "y": 480},
    "bubble_anchor": {"x": 256, "y": 80},
    "reference_height": 452,
    "hit_regions": [
        {"shape": "rect", "x": 70, "y": 28, "width": 372, "height": 452},
    ],
}


def _geometry(**overrides) -> dict:
    geometry = {
        "logical_canvas": {"width": 512, "height": 512},
        "content_bounds": {"x": 70, "y": 28, "width": 372, "height": 452},
        "motion_bounds": {"x": 30, "y": 12, "width": 452, "height": 486},
        "base_anchor": {"x": 256, "y": 480},
        "bubble_anchor": {"x": 256, "y": 80},
        "reference_height": 452,
        "hit_regions": [],
    }
    geometry.update(overrides)
    return geometry


def _parse(geometry: dict) -> CharacterGeometry:
    parsed = CharacterGeometry.from_character({"geometry": geometry})
    assert parsed is not None
    return parsed


# -- parsing -------------------------------------------------------------------


def test_full_geometry_parses_with_hit_rect():
    parsed = _parse(SPEC_GEOMETRY)
    assert parsed.canvas_size == (512.0, 512.0)
    assert parsed.reference_height == 452.0
    assert parsed.base_anchor == QPointF(256, 480)
    assert len(parsed.hit_shapes) == 1
    assert parsed.hit_shapes[0].kind == "rect"


def test_missing_or_malformed_geometry_falls_back_to_legacy_fit():
    assert CharacterGeometry.from_character({}) is None
    assert CharacterGeometry.from_character({"geometry": "nope"}) is None
    incomplete = _geometry()
    del incomplete["base_anchor"]
    assert CharacterGeometry.from_character({"geometry": incomplete}) is None


@pytest.mark.parametrize("field", [
    "logical_canvas", "content_bounds", "motion_bounds",
    "base_anchor", "bubble_anchor", "reference_height",
])
def test_each_required_field_is_mandatory(field):
    geometry = _geometry()
    del geometry[field]
    assert CharacterGeometry.from_character({"geometry": geometry}) is None


def test_non_finite_or_out_of_canvas_values_rejected():
    assert CharacterGeometry.from_character(
        {"geometry": _geometry(reference_height=float("nan"))}) is None
    over = _geometry(motion_bounds={"x": 30, "y": 12,
                                    "width": 600, "height": 486})
    assert CharacterGeometry.from_character({"geometry": over}) is None
    anchor = _geometry(base_anchor={"x": -1, "y": 480})
    assert CharacterGeometry.from_character({"geometry": anchor}) is None


def test_hit_shapes_rect_circle_polygon_and_unknown():
    geometry = _geometry(hit_regions=[
        {"shape": "rect", "x": 0, "y": 0, "width": 10, "height": 10},
        {"shape": "circle", "center": {"x": 30, "y": 5}, "radius": 5},
        {"shape": "polygon", "points": [
            {"x": 0, "y": 0}, {"x": 20, "y": 0}, {"x": 10, "y": 20}]},
        {"shape": "blob", "points": [{"x": 1, "y": 1}]},
        "garbage",
    ])
    parsed = _parse(geometry)
    kinds = [shape.kind for shape in parsed.hit_shapes]
    assert kinds == ["rect", "circle", "polygon"]
    assert parsed.hit_shapes[1].contains(QPointF(30, 5))
    assert not parsed.hit_shapes[1].contains(QPointF(30, 20))


# -- layout math ---------------------------------------------------------------


def test_square_pack_anchors_foot_and_fits_motion_bounds():
    layout = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    # One uniform scale, driven by the motion bounds fit.
    assert layout.scale == pytest.approx(
        min((236 - 6) / 486, (232 - 8) / 452, (236 - 6) / 486), abs=1e-6)
    # The foot lands on the stable foot line (viewport bottom - margin).
    assert layout.foot_point.x() == pytest.approx(116, abs=1)
    # Motion bounds must fit the viewport (clamping shifts, never rescales).
    mapped = layout.motion_rect
    assert mapped.top() >= VIEWPORT.top() - 1
    assert mapped.bottom() <= VIEWPORT.bottom() + 1
    assert mapped.left() >= VIEWPORT.left() - 1
    assert mapped.right() <= VIEWPORT.right() + 1


def test_portrait_canvas_keeps_single_scale_and_visible_foot():
    geometry = _geometry(
        logical_canvas={"width": 256, "height": 1024},
        content_bounds={"x": 28, "y": 24, "width": 200, "height": 976},
        motion_bounds={"x": 18, "y": 12, "width": 220, "height": 1000},
        base_anchor={"x": 128, "y": 1000},
        bubble_anchor={"x": 128, "y": 120},
        reference_height=950,
    )
    layout = compute_body_layout(VIEWPORT, _parse(geometry))
    assert layout.scale == pytest.approx(min(230 / 950, 224 / 220, 230 / 1000),
                                         abs=1e-6)
    assert layout.foot_point.y() <= VIEWPORT.bottom() + 1
    assert layout.foot_point.y() >= VIEWPORT.bottom() - 4


def test_wide_canvas_centers_body_without_clamp():
    geometry = _geometry(
        logical_canvas={"width": 1024, "height": 256},
        content_bounds={"x": 62, "y": 60, "width": 900, "height": 180},
        motion_bounds={"x": 12, "y": 40, "width": 1000, "height": 200},
        base_anchor={"x": 512, "y": 240},
        bubble_anchor={"x": 512, "y": 80},
        reference_height=180,
    )
    layout = compute_body_layout(VIEWPORT, _parse(geometry))
    assert layout.clamped is False
    assert layout.foot_point.x() == pytest.approx(116, abs=1)
    mapped = layout.motion_rect
    assert mapped.left() >= -1 and mapped.right() <= VIEWPORT.right() + 1


def test_large_transparent_margins_do_not_block_the_motion_area():
    """Body canvas may overflow; the motion (visible) area must fit."""
    geometry = _geometry(
        content_bounds={"x": 200, "y": 200, "width": 112, "height": 260},
        motion_bounds={"x": 190, "y": 190, "width": 132, "height": 280},
        base_anchor={"x": 256, "y": 460},
        reference_height=240,
    )
    layout = compute_body_layout(VIEWPORT, _parse(geometry))
    assert layout.body_rect.width() > VIEWPORT.width()  # canvas overflows
    assert layout.motion_rect.width() <= VIEWPORT.width() + 1
    assert layout.motion_rect.height() <= VIEWPORT.height() + 1


def test_panel_grow_keeps_foot_stable_within_the_cat_area():
    """The cat area never moves inside the widget: expanding the countdown
    panel grows the window upwards, so the computed foot is identical."""
    layout_before = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    layout_after = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    assert layout_after.foot_point == layout_before.foot_point


def test_hit_regions_map_into_window_coordinates_and_gate_clicks():
    layout = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    inside = QPointF(layout.foot_point.x(),
                     layout.foot_point.y() - 10 * layout.scale)
    assert layout.hit_contains(inside)
    # A transparent canvas corner far from any hit region is not a click.
    assert not layout.hit_contains(QPointF(2, 2))
    mapped_rect = layout.hit_shapes[0].rect
    assert mapped_rect.width() == pytest.approx(372 * layout.scale, abs=1)


def test_no_hit_regions_makes_whole_body_clickable():
    layout = compute_body_layout(VIEWPORT, _parse(_geometry(hit_regions=[])))
    assert layout.hit_shapes == ()
    assert layout.hit_contains(layout.body_rect.center())


def test_bubble_origin_maps_from_bubble_anchor():
    layout = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    expected = QPointF(256 * layout.scale + layout.body_rect.left(),
                       80 * layout.scale + layout.body_rect.top())
    assert layout.bubble_origin == expected


# -- device-pixel rendering (DPR 1.0 / 1.5 / 2.0) -------------------------------


def _render_layout_image(layout, scale: float) -> QImage:
    device_width = int(VIEWPORT.width() * scale)
    device_height = int(VIEWPORT.height() * scale)
    image = QImage(device_width, device_height, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    image.setDevicePixelRatio(scale)
    painter = QPainter(image)
    try:
        painter.fillRect(layout.motion_rect, QColor(255, 90, 0, 255))
    finally:
        painter.end()
    return image


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_foot_lands_on_foot_line_in_device_pixels(dpr):
    layout = compute_body_layout(VIEWPORT, _parse(SPEC_GEOMETRY))
    image = _render_layout_image(layout, dpr)
    # Scan the device-pixel column under the foot for the lowest lit pixel.
    column = int(layout.foot_point.x() * dpr)
    lowest = None
    for y in range(image.height() - 1, -1, -1):
        if QColor(image.pixel(column, y)).alpha() > 0:
            lowest = y
            break
    assert lowest is not None
    # motion_bounds extends below base_anchor, so the foot line is the
    # bottom of the mapped body; both must agree within 1 DIP * dpr.
    expected_bottom = layout.motion_rect.bottom() * dpr
    assert lowest <= expected_bottom + dpr
    assert lowest >= expected_bottom - dpr * 2


def test_widget_rect_for_mask_never_clips_painted_content():
    rect = QRectF(10.4, 20.6, 55.3, 41.8)
    widget = widget_rect_for_mask(rect)
    assert widget.left() <= 10.4 and widget.top() <= 20.6
    assert widget.right() >= 65.7 - 1 and widget.bottom() >= 62.4 - 1
