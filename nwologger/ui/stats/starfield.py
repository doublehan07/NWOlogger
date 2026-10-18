"""The sky behind the globe: deep space, a faint band of the Milky Way and stars.

Stars are placed per cell of :data:`CELL` logical pixels, counted from the view's centre, with a fixed seed per
cell, so resizing the view reveals more of the same sky instead of a new one.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QRadialGradient

SPACE = QColor("#05080f")  # the darkest sky, at the corners
CELL = 128
STARS_PER_CELL = 9  # about one star per 1,800 square pixels
BAND_STARS = 22  # extra faint stars per cell on the Milky Way's axis
BAND_ANGLE = math.radians(-28)  # the band's slope across the view
BAND_WIDTH = 0.16  # its half-width as a share of the view's diagonal
TINTS = ((255, 255, 255), (205, 220, 255), (255, 236, 212))


def _band(x: float, y: float, diagonal: float) -> float:
    """How close (1 on its axis, towards 0 away from it) a point relative to the centre is to the Milky Way."""
    distance = -x * math.sin(BAND_ANGLE) + y * math.cos(BAND_ANGLE)
    return math.exp(-(distance / (BAND_WIDTH * diagonal)) ** 2)


def sky(size: QSize, dpr: float) -> QPixmap:
    """A pixmap of ``size`` logical pixels at ``dpr`` device pixels each."""
    width, height = max(1, size.width()), max(1, size.height())
    pixmap = QPixmap(math.ceil(width * dpr), math.ceil(height * dpr))
    pixmap.setDevicePixelRatio(dpr)
    middle = QPointF(width / 2, height / 2)
    diagonal = math.hypot(width, height)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        base = QRadialGradient(middle, diagonal * 0.62)
        base.setColorAt(0, QColor("#0c1626"))
        base.setColorAt(1, SPACE)
        painter.fillRect(0, 0, width, height, base)
        # The Milky Way: a soft glow across the view, with two faint clouds of colour.
        direction = QPointF(-math.sin(BAND_ANGLE), math.cos(BAND_ANGLE)) * (BAND_WIDTH * diagonal * 2.2)
        glow = QLinearGradient(middle - direction, middle + direction)
        for at, alpha in ((0, 0), (.3, 7), (.5, 17), (.7, 7), (1, 0)):
            glow.setColorAt(at, QColor(150, 170, 215, alpha))
        painter.fillRect(0, 0, width, height, glow)
        for (fx, fy), color, share in (((-.3, -.12), QColor(120, 90, 170, 16), .3),
                                       ((.34, .2), QColor(70, 120, 190, 14), .26)):
            center = middle + QPointF(fx * width, fy * height)
            cloud = QRadialGradient(center, diagonal * share)
            cloud.setColorAt(0, color)
            cloud.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
            painter.fillRect(0, 0, width, height, cloud)
        painter.setPen(Qt.PenStyle.NoPen)
        columns = range(math.floor(-width / 2 / CELL), math.ceil(width / 2 / CELL))
        rows = range(math.floor(-height / 2 / CELL), math.ceil(height / 2 / CELL))
        for i in columns:
            for j in rows:
                rng = random.Random(i * 7919 + j * 104_729 + 17)
                band = _band((i + .5) * CELL, (j + .5) * CELL, diagonal)
                count = STARS_PER_CELL + rng.randint(-3, 3) + round(BAND_STARS * band)
                for n in range(count):
                    x = middle.x() + (i + rng.random()) * CELL
                    y = middle.y() + (j + rng.random()) * CELL
                    brightness = rng.random() ** (2.4 if n < STARS_PER_CELL else 4.5)
                    r, g, b = TINTS[0] if rng.random() < .6 else rng.choice(TINTS[1:])
                    radius = .35 + .85 * brightness
                    if brightness > .82:
                        halo = QRadialGradient(QPointF(x, y), radius * 4)
                        halo.setColorAt(0, QColor(r, g, b, 60))
                        halo.setColorAt(1, QColor(r, g, b, 0))
                        painter.setBrush(halo)
                        painter.drawEllipse(QPointF(x, y), radius * 4, radius * 4)
                    painter.setBrush(QColor(r, g, b, round(55 + 200 * brightness)))
                    painter.drawEllipse(QPointF(x, y), radius, radius)
    finally:
        painter.end()
    return pixmap
