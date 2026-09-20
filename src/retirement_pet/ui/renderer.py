"""Pet renderer: layered programmatic cat with PNG sprite fallback.

Draw order (design 9.2): shadow -> back accessory -> tail -> body ->
head -> face/accessories -> front accessory -> effects -> caption.
All drawing happens in a 256x256 logical canvas mapped into the target rect.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QPolygonF

from retirement_pet.assets import AssetBundle, PartsVisual, SequenceVisual
from retirement_pet.models import ActionId, LifeStage, RenderSnapshot
from retirement_pet.ui.overlay_renderer import (
    ACTION_CAPTIONS,
    EngineOverlayRenderer,
)

CANVAS = 256.0

FUR = {
    LifeStage.YOUNG: QColor(233, 223, 203),
    LifeStage.MIDDLE: QColor(226, 214, 192),
    LifeStage.OLD: QColor(216, 210, 196),
    LifeStage.RETIRED: QColor(214, 210, 200),
}
FUR_SHADE = {
    LifeStage.YOUNG: QColor(214, 197, 168),
    LifeStage.MIDDLE: QColor(207, 189, 159),
    LifeStage.OLD: QColor(198, 190, 170),
    LifeStage.RETIRED: QColor(196, 190, 175),
}
INNER_EAR = QColor(232, 184, 176)
EYE = QColor(58, 58, 64)
NOSE = QColor(185, 138, 133)
MOUTH = QColor(138, 117, 112)
SHADOW = QColor(0, 0, 0, 38)
ACCENT = QColor(63, 70, 80)


class CatRenderer:
    def __init__(self, bundle: AssetBundle | None = None):
        self._bundle = bundle
        self._engine_overlay = EngineOverlayRenderer()

    # -- entry ------------------------------------------------------------

    def render(self, painter: QPainter, rect: QRectF, snap: RenderSnapshot) -> None:
        """Compatibility composite for direct renderer consumers.

        PetWindow calls :meth:`render_body` and owns the one engine-overlay
        pass.  Keeping this composite preserves the renderer's public v1 API
        for previews and tests without ever baking overlays into PetPack v1.0.1.
        """
        self.render_body(painter, rect, snap)
        self._engine_overlay.render(painter, rect, snap)

    def render_body(self, painter: QPainter, rect: QRectF,
                    snap: RenderSnapshot, layout=None) -> None:
        """Paint character-owned pixels only.

        ``layout`` is accepted for PetWindow protocol compatibility; the
        programmatic cat owns its own canvas mapping and ignores it.
        """
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        scale = min(rect.width(), rect.height()) / CANVAS
        painter.translate(rect.center())
        painter.scale(scale, scale)
        painter.translate(-CANVAS / 2, -CANVAS / 2)

        visual = self._sprite_visual(snap)
        if visual is not None:
            self._draw_sprite(painter, visual, snap)
        else:
            self._draw_programmatic(painter, snap)

        painter.restore()

    # -- sprite path --------------------------------------------------------

    def _sprite_visual(self, snap: RenderSnapshot):
        if self._bundle is None or not self._bundle.available:
            return None
        return self._bundle.action_visual(snap.action.value)

    def _draw_sprite(self, painter: QPainter, visual, snap: RenderSnapshot) -> None:
        t = snap.time_ms / 1000.0
        if isinstance(visual, SequenceVisual):
            frames = visual.frames
            if not frames:
                return
            index = snap.frame % len(frames)
            if not visual.loop and frames and snap.frame >= len(frames):
                index = len(frames) - 1
            self._stamp(painter, frames[index])
            return
        assert isinstance(visual, PartsVisual)
        parts = visual.parts
        breath = 1.0 + 0.025 * math.sin(2 * math.pi * t / 3.2)
        sway = math.sin(2 * math.pi * t / 3.5)

        if "shadow" in parts:
            self._stamp(painter, parts["shadow"])
        if "tail" in parts:
            painter.save()
            painter.translate(CANVAS / 2 + 40, 216)
            painter.rotate(10 * sway)
            painter.drawPixmap(-parts["tail"].width() // 2, -parts["tail"].height(), parts["tail"])
            painter.restore()
        if "body" in parts:
            painter.save()
            painter.translate(CANVAS / 2, 224)
            painter.scale(1.0, breath)
            self._stamp_bottom(painter, parts["body"], CANVAS / 2, 224)
            painter.restore()
        head_dy = 2.0 * math.sin(2 * math.pi * t / 3.2)
        if "head" in parts:
            painter.save()
            painter.translate(snap.overlay.gaze_x * 3, head_dy)
            self._stamp(painter, parts["head"])
            painter.restore()
        face = visual.face_override or ("face_closed" if snap.overlay.blink else "face_open")
        if face in parts:
            painter.save()
            painter.translate(snap.overlay.gaze_x * 3, head_dy)
            self._stamp(painter, parts[face])
            painter.restore()
        for attachment in visual.attachments:
            part = parts.get(attachment)
            if part is not None:
                self._stamp(painter, part)

    def _stamp(self, painter: QPainter, pix: QPixmap) -> None:
        painter.drawPixmap(
            int((CANVAS - pix.width()) / 2), int(CANVAS - 16 - pix.height()), pix
        )

    def _stamp_bottom(self, painter: QPainter, pix: QPixmap, cx: float, bottom: float) -> None:
        painter.drawPixmap(int(cx - pix.width() / 2), int(bottom - pix.height()), pix)

    # -- programmatic path ----------------------------------------------------

    def _draw_programmatic(self, painter: QPainter, snap: RenderSnapshot) -> None:
        action = snap.action
        if action is ActionId.REST:
            self._draw_rest_pose(painter, snap)
            return
        if action is ActionId.STRETCH:
            self._draw_stretch_pose(painter, snap)
            return

        t = snap.time_ms / 1000.0
        elapsed = snap.elapsed_ms / 1000.0
        # breath and tail sway share ONE 3.2s period so the official pack
        # builder can sample a seamless idle loop (V13-07): 16 frames at
        # 200ms cover exactly one period and the seam lands on frame 0.
        breath = math.sin(2 * math.pi * t / 3.2)
        sway = math.sin(2 * math.pi * t / 3.2)
        bounce = 0.0
        if action is ActionId.INTERACT:
            decay = max(0.0, 1.0 - elapsed / 1.5)
            bounce = -10.0 * abs(math.sin(math.pi * elapsed / 0.45)) * decay

        fur = FUR[snap.stage]
        shade = FUR_SHADE[snap.stage]

        # Shadow
        painter.setPen(Qt.NoPen)
        painter.setBrush(SHADOW)
        painter.drawEllipse(QPointF(128, 232), 58, 11)

        # Tail
        self._tail(painter, fur, sway, lying=False)

        # Body (breathing)
        body_ry = 46 + 2.2 * breath
        painter.setBrush(shade)
        painter.drawEllipse(QPointF(128, 186), 52, body_ry)

        # Front legs
        leg_bob = 0.0
        if action is ActionId.WORK:
            leg_bob = 2.0 * math.sin(2 * math.pi * t * 4.0)
        painter.setBrush(fur)
        painter.drawEllipse(QPointF(108, 206), 10, 24)
        painter.drawEllipse(QPointF(148, 206 - abs(leg_bob)), 10, 24)

        # Head position modifiers
        head_x, head_y = 128.0, 106.0
        if action is ActionId.LOOK_AROUND:
            head_x += 12 * math.sin(2 * math.pi * t / 2.4)
        if action is ActionId.WORK:
            head_y += 6
        if action is ActionId.EAT:
            head_y += 8 + 4 * math.sin(2 * math.pi * t * 1.2)
        if action is ActionId.LICK_PAW:
            head_y += 10
        if action is ActionId.YAWN:
            head_y += 3
        head_y += 1.5 * breath + bounce
        head_x += snap.overlay.gaze_x * 4

        self._head(painter, snap, fur, head_x, head_y, t)
        self._accessories(painter, snap, fur, head_x, head_y, t)

    # -- poses -----------------------------------------------------------------

    def _draw_rest_pose(self, painter: QPainter, snap: RenderSnapshot) -> None:
        t = snap.time_ms / 1000.0
        fur = FUR[snap.stage]
        shade = FUR_SHADE[snap.stage]
        breath = math.sin(2 * math.pi * t / 4.0)

        painter.setPen(Qt.NoPen)
        painter.setBrush(SHADOW)
        painter.drawEllipse(QPointF(128, 232), 66, 10)

        self._tail(painter, fur, math.sin(2 * math.pi * t / 5.0), lying=True)

        painter.setBrush(shade)
        painter.drawEllipse(QPointF(128, 204), 64, 30 + 1.5 * breath)

        # Closed-eye head resting low
        painter.setBrush(fur)
        painter.drawEllipse(QPointF(100, 176), 34, 30)
        self._ears(painter, fur, 100, 152)
        painter.setPen(QPen(EYE, 2.4))
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(88, 178, 12, 8), 0, 180 * 16)
        painter.drawArc(QRectF(106, 178, 12, 8), 0, 180 * 16)
        self._stage_gear(painter, snap, 100, 176, 34)

    def _draw_stretch_pose(self, painter: QPainter, snap: RenderSnapshot) -> None:
        t = snap.elapsed_ms / 1000.0
        fur = FUR[snap.stage]
        shade = FUR_SHADE[snap.stage]
        arch = math.sin(math.pi * min(1.0, t / 2.0))  # rise then settle

        painter.setPen(Qt.NoPen)
        painter.setBrush(SHADOW)
        painter.drawEllipse(QPointF(128, 232), 70, 10)

        painter.setBrush(shade)
        # Elongated, front-lowered body
        painter.save()
        painter.translate(128, 210)
        painter.scale(1.0 + 0.18 * arch, 1.0 - 0.12 * arch)
        painter.drawEllipse(QPointF(0, 0), 56, 36)
        painter.restore()

        painter.setBrush(fur)
        # Front paws stretched forward
        painter.drawEllipse(QPointF(76, 218), 9, 14)
        painter.drawEllipse(QPointF(96, 222), 9, 12)
        # Rear raised
        painter.drawEllipse(QPointF(176, 196 - 8 * arch), 16, 20)

        head_y = 130 - 6 * arch
        self._head(painter, snap, fur, 62, head_y, t, eye="happy")
        self._stage_gear(painter, snap, 62, head_y, 40)

    # -- parts -------------------------------------------------------------------

    def _tail(self, painter: QPainter, fur: QColor, sway: float, lying: bool) -> None:
        painter.save()
        painter.setBrush(fur)
        painter.setPen(Qt.NoPen)
        base_x, base_y = (170, 214) if not lying else (182, 220)
        tip_x = base_x + 34 + 14 * sway
        tip_y = (base_y - 52) if not lying else (base_y - 18)
        mid_x = base_x + 30 + 8 * sway
        mid_y = base_y - (26 if not lying else 6)
        path_polygon = QPolygonF(
            [
                QPointF(base_x - 7, base_y + 6),
                QPointF(mid_x, mid_y),
                QPointF(tip_x, tip_y),
                QPointF(tip_x - 6, tip_y + 8),
                QPointF(mid_x - 4, mid_y + 8),
                QPointF(base_x - 9, base_y + 12),
            ]
        )
        painter.drawPolygon(path_polygon)
        painter.restore()

    def _ears(self, painter: QPainter, fur: QColor, cx: float, top_y: float) -> None:
        painter.setPen(Qt.NoPen)
        painter.setBrush(fur)
        left = QPolygonF(
            [QPointF(cx - 34, top_y + 16), QPointF(cx - 22, top_y - 14), QPointF(cx - 6, top_y + 8)]
        )
        right = QPolygonF(
            [QPointF(cx + 6, top_y + 8), QPointF(cx + 22, top_y - 14), QPointF(cx + 34, top_y + 16)]
        )
        painter.drawPolygon(left)
        painter.drawPolygon(right)
        painter.setBrush(INNER_EAR)
        painter.drawPolygon(
            QPolygonF(
                [QPointF(cx - 27, top_y + 10), QPointF(cx - 21, top_y - 6), QPointF(cx - 13, top_y + 8)]
            )
        )
        painter.drawPolygon(
            QPolygonF(
                [QPointF(cx + 13, top_y + 8), QPointF(cx + 21, top_y - 6), QPointF(cx + 27, top_y + 10)]
            )
        )

    def _head(
        self,
        painter: QPainter,
        snap: RenderSnapshot,
        fur: QColor,
        cx: float,
        cy: float,
        t: float,
        eye: str | None = None,
    ) -> None:
        tilt = -12.0 if snap.action is ActionId.LICK_PAW else 0.0
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(tilt)

        painter.setPen(Qt.NoPen)
        painter.setBrush(fur)
        self._ears(painter, fur, 0, -44)
        painter.drawEllipse(QPointF(0, 0), 44, 42)

        # Eyes
        look_x = snap.overlay.gaze_x * 3.5
        look_y = snap.overlay.gaze_y * 2.5
        blink = snap.overlay.blink
        mode = eye or ("closed" if blink else "open")
        if snap.action is ActionId.YAWN:
            mode = "closed"
        if snap.action is ActionId.LOOK_AROUND:
            mode = "wide"

        painter.setPen(Qt.NoPen)
        for ex in (-16, 16):
            if mode == "closed":
                painter.setPen(QPen(EYE, 2.2))
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(ex - 6, -4, 12, 8), 0, 180 * 16)
                painter.setPen(Qt.NoPen)
            elif mode == "happy":
                painter.setPen(QPen(EYE, 2.4))
                painter.setBrush(Qt.NoBrush)
                painter.drawArc(QRectF(ex - 7, -8, 14, 12), 20 * 16, 140 * 16)
                painter.setPen(Qt.NoPen)
            else:
                radius = 7.5 if mode == "wide" else 6.0
                painter.setBrush(QColor(255, 255, 255))
                painter.drawEllipse(QPointF(ex, 0), radius + 1.6, radius + 2.2)
                painter.setBrush(EYE)
                painter.drawEllipse(QPointF(ex + look_x, look_y), radius * 0.62, radius * 0.92)
                painter.setBrush(QColor(255, 255, 255, 220))
                painter.drawEllipse(QPointF(ex + look_x - 1.8, look_y - 2.4), 1.6, 2.0)

        # Nose + mouth
        painter.setPen(QPen(NOSE, 1.6))
        painter.setBrush(NOSE)
        painter.drawPolygon(
            QPolygonF([QPointF(-2.6, 12), QPointF(2.6, 12), QPointF(0, 15)])
        )
        painter.setBrush(Qt.NoBrush)
        if snap.action is ActionId.YAWN:
            openness = 6.0 + 2.5 * abs(math.sin(snap.time_ms / 260.0))
            painter.setBrush(QColor(150, 96, 92))
            painter.setPen(QPen(MOUTH, 1.4))
            painter.drawEllipse(QPointF(0, 22), 5.5, openness)
            painter.setBrush(Qt.NoBrush)
        elif snap.action is ActionId.LICK_PAW:
            painter.setPen(QPen(MOUTH, 1.4))
            painter.drawArc(QRectF(-6, 14, 12, 9), 200 * 16, 140 * 16)
            painter.setBrush(QColor(232, 148, 150))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(6, 18), 3.4, 2.6)
            painter.setBrush(Qt.NoBrush)
        else:
            painter.setPen(QPen(MOUTH, 1.4))
            painter.drawArc(QRectF(-7, 14, 7, 6), 200 * 16, 140 * 16)
            painter.drawArc(QRectF(0, 14, 7, 6), 200 * 16, 140 * 16)

        # Whiskers
        whisker = QColor(120, 115, 110) if snap.stage in (LifeStage.YOUNG, LifeStage.MIDDLE) else QColor(230, 228, 222)
        painter.setPen(QPen(whisker, 1.1))
        for side in (-1, 1):
            for dy, dx in ((8, 26), (13, 28), (18, 26)):
                painter.drawLine(
                    side * 20, dy - 4,
                    side * (dx + 8), dy + (2 if dy < 16 else -2),
                )
        painter.restore()
        self._stage_gear(painter, snap, cx, cy, 42)

    def _stage_gear(self, painter: QPainter, snap: RenderSnapshot, cx: float, cy: float, r: float) -> None:
        """Age accessories: student cap (young), glasses (middle+), beard (old+)."""
        stage = snap.stage
        if stage is LifeStage.YOUNG:
            painter.save()
            painter.translate(cx, cy)
            painter.setPen(Qt.NoPen)
            painter.setBrush(ACCENT)
            painter.drawPolygon(
                QPolygonF(
                    [QPointF(-30, -30), QPointF(0, -44), QPointF(30, -30), QPointF(0, -20)]
                )
            )
            painter.drawRect(QRectF(-12, -31, 24, 5))
            painter.setPen(QPen(QColor(90, 92, 100), 2))
            painter.drawLine(QPointF(26, -30), QPointF(32, -14))
            painter.restore()
        elif stage in (LifeStage.MIDDLE, LifeStage.OLD, LifeStage.RETIRED):
            pen_w = 2.6 if stage is not LifeStage.MIDDLE else 2.0
            painter.save()
            painter.translate(cx, cy)
            painter.setPen(QPen(QColor(70, 72, 78), pen_w))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QRectF(-24, -9, 17, 15))
            painter.drawEllipse(QRectF(7, -9, 17, 15))
            painter.drawLine(QPointF(-7, -2), QPointF(7, -2))
            painter.restore()
        if stage in (LifeStage.OLD, LifeStage.RETIRED):
            painter.save()
            painter.translate(cx, cy)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(226, 226, 226, 235))
            painter.drawEllipse(QPointF(0, 26), 12, 9)
            painter.restore()

    # -- action accessories ---------------------------------------------------

    def _accessories(self, painter: QPainter, snap: RenderSnapshot, fur: QColor, head_x: float, head_y: float, t: float) -> None:
        action = snap.action
        painter.setPen(Qt.NoPen)
        if action is ActionId.WORK:
            # Laptop: base + tilted glowing screen between the paws.
            painter.setBrush(QColor(58, 64, 72))
            painter.drawRoundedRect(QRectF(92, 196, 72, 10), 3, 3)
            screen = QRectF(96, 164, 64, 34)
            painter.setBrush(QColor(48, 54, 62))
            painter.drawRoundedRect(screen, 4, 4)
            painter.setBrush(QColor(159, 216, 255, 160))
            painter.drawRoundedRect(QRectF(100, 168, 56, 26), 3, 3)
        elif action is ActionId.EAT:
            painter.setBrush(QColor(201, 111, 74))
            painter.drawEllipse(QPointF(128, 218), 27, 9)
            painter.setBrush(QColor(224, 138, 95))
            painter.drawEllipse(QPointF(128, 214), 27, 9)
            painter.setBrush(QColor(240, 226, 200))
            painter.drawEllipse(QPointF(128, 212), 20, 6)
        elif action is ActionId.MEETING:
            self._headset(painter, head_x, head_y, mic=True)
            # Language-neutral DND icon.  Text belongs to the live engine
            # overlay and must never be baked into generated character media.
            badge = QRectF(head_x + 38, head_y - 58, 18, 18)
            painter.setBrush(QColor(196, 74, 71, 235))
            painter.drawEllipse(badge)
            painter.setPen(QPen(QColor(255, 255, 255), 2.2))
            painter.drawLine(badge.topLeft() + QPointF(4, 4),
                             badge.bottomRight() - QPointF(4, 4))
            painter.setPen(Qt.NoPen)
        elif action is ActionId.MUSIC:
            self._headset(painter, head_x, head_y, mic=False)
        elif action is ActionId.EXERCISE:
            lift = math.sin(2 * math.pi * t / 2.0)
            bar_y = head_y - 78 + 10 * lift
            painter.setPen(QPen(QColor(90, 96, 104), 4))
            painter.drawLine(QPointF(head_x - 34, bar_y), QPointF(head_x + 34, bar_y))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(70, 76, 84))
            for bx in (head_x - 38, head_x + 38):
                painter.drawRoundedRect(QRectF(bx - 7, bar_y - 12, 14, 24), 4, 4)
            painter.setPen(QPen(QColor(178, 160, 128), 5))
            painter.drawLine(QPointF(head_x - 22, head_y + 20), QPointF(head_x - 32, bar_y + 8))
            painter.drawLine(QPointF(head_x + 22, head_y + 20), QPointF(head_x + 32, bar_y + 8))
            painter.setPen(Qt.NoPen)
        elif action is ActionId.LICK_PAW:
            painter.setBrush(fur)
            painter.drawEllipse(QPointF(head_x + 30, head_y + 26), 9, 15)

    def _headset(self, painter: QPainter, head_x: float, head_y: float, mic: bool) -> None:
        painter.save()
        painter.translate(head_x, head_y)
        band_color = QColor(68, 75, 85)
        painter.setPen(QPen(band_color, 5))
        painter.setBrush(Qt.NoBrush)
        painter.drawArc(QRectF(-46, -46, 92, 92), 0, 180 * 16)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(46, 51, 59))
        painter.drawRoundedRect(QRectF(-50, -8, 12, 24), 5, 5)
        painter.drawRoundedRect(QRectF(38, -8, 12, 24), 5, 5)
        if mic:
            painter.setPen(QPen(band_color, 3))
            painter.drawArc(QRectF(20, 4, 30, 30), 90 * 16, 90 * 16)
            painter.setBrush(QColor(226, 113, 110))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(50, 20), 3.6, 3.6)
        painter.restore()
