"""Turning an :class:`~.world.Island` into polygons.

The island is a rectangular slab whose *top* has cells missing. Holes are
drawn as a dark floor set below the surface rather than as true openings,
which reads correctly at this camera angle for a fraction of the geometry.
"""

from __future__ import annotations

import math
from typing import Tuple

import pygame

from . import config as C
from . import render as R
from . import world as W

PIT_DROP = 46.0
DECOR_HEIGHT = 20.0


def rotate_3d(point, pitch, roll):
    """Copied verbatim from ``examples/accelerometer_3d.py``."""
    x, y, z = point

    # Rotate around X-axis (Pitch)
    cos_p = math.cos(pitch)
    sin_p = math.sin(pitch)
    y1 = y * cos_p - z * sin_p
    z1 = y * sin_p + z * cos_p

    # Rotate around Y-axis (Roll)
    cos_r = math.cos(roll)
    sin_r = math.sin(roll)
    x2 = x * cos_r + z1 * sin_r
    z2 = -x * sin_r + z1 * cos_r

    return (x2, y1, z2)


def pitch_roll(gravity: Tuple[float, float, float]) -> Tuple[float, float]:
    """Copied verbatim from ``examples/accelerometer_3d.py``."""
    gx, gy, gz = gravity
    pitch = math.atan2(-gy, math.sqrt(gx ** 2 + gz ** 2))
    roll = math.atan2(gx, gz)
    return pitch, roll


class BoardTransform:
    """Board-local (cell x, cell y, height) -> world.

    The board is the watch: built in the XY plane with its face normal along
    +Z, exactly like the cylinder in ``accelerometer_3d.py``, then run through
    that example's own ``rotate_3d`` and its ``(x, -z, y)`` camera swap. That
    example is known good on real hardware, so this uses its maths unchanged
    rather than deriving a second convention that has to agree with it.
    """

    __slots__ = ("origin", "_pitch", "_roll", "_half")

    def __init__(self, gravity: Tuple[float, float, float],
                 origin: Tuple[float, float, float] = (0.0, 0.0, 0.0)) -> None:
        self.origin = origin
        self._half = (C.GRID - 1) * 0.5
        self._pitch, self._roll = pitch_roll(gravity)

    def __call__(self, cx: float, cy: float, height: float = 0.0):
        point = (
            (cx - self._half) * C.CELL,
            (cy - self._half) * C.CELL,
            height,
        )
        rx, ry, rz = rotate_3d(point, self._pitch, self._roll)
        # The example's camera rotation, which puts the watch face up.
        ox, oy, oz = self.origin
        return (rx + ox, -rz + oy, ry + oz)


def board_tilt(gravity: Tuple[float, float, float]) -> Tuple[float, float]:
    """In-plane gravity in board-cell axes, so the ball rolls downhill.

    Derived from the transform above rather than guessed: with the example's
    ``roll = atan2(gx, gz)``, a face-up watch sits at roll = pi, which turns
    the board a half-turn about the vertical. The signs here are what cancel
    that, and they are checked against the recordings in ``data/``.
    """
    return (-gravity[0], gravity[1])


def draw_island(painter: R.Painter, camera: R.Camera, island: W.Island,
                xf: BoardTransform, t: float) -> None:
    g = C.GRID
    edge = g - 0.5

    # ── Slab sides and underside ───────────────────────────────────────────
    corners_top = [
        xf(-0.5, -0.5), xf(edge, -0.5), xf(edge, edge), xf(-0.5, edge),
    ]
    corners_bot = [
        xf(-0.5, -0.5, -C.SLAB_DEPTH), xf(edge, -0.5, -C.SLAB_DEPTH),
        xf(edge, edge, -C.SLAB_DEPTH), xf(-0.5, edge, -C.SLAB_DEPTH),
    ]

    painter.face(camera, list(reversed(corners_bot)), C.DIRT_DARK,
                 outline=C.OUTLINE, width=C.px(3))
    for i in range(4):
        j = (i + 1) % 4
        # Reversed relative to the pit walls: these face *out* of the slab,
        # those face in.
        painter.face(
            camera,
            [corners_top[j], corners_top[i], corners_bot[i], corners_bot[j]],
            C.DIRT, outline=C.OUTLINE, width=C.px(3),
        )

    # ── Top surface ────────────────────────────────────────────────────────
    for cy in range(g):
        for cx in range(g):
            cell = island.cells[cy][cx]
            x0, y0 = cx - 0.5, cy - 0.5
            x1, y1 = cx + 0.5, cy + 0.5

            if cell == W.HOLE:
                # Floor, then inward-facing walls joining it to the rim.
                # Without the walls the sky shows through the gap between the
                # missing top cell and the floor set below it.
                rim = [xf(x0, y0), xf(x1, y0), xf(x1, y1), xf(x0, y1)]
                floor = [xf(x0, y0, -PIT_DROP), xf(x1, y0, -PIT_DROP),
                         xf(x1, y1, -PIT_DROP), xf(x0, y1, -PIT_DROP)]
                painter.face(camera, floor, C.PIT, unlit=True, cull=False)
                for i in range(4):
                    j = (i + 1) % 4
                    painter.face(camera, [rim[i], rim[j], floor[j], floor[i]],
                                 C.PIT_WALL)

                pts = [camera.project(p) for p in rim]
                depth = sum(p[2] for p in pts) / 4.0
                painter.polygon(depth - 1.0, [(p[0], p[1]) for p in pts],
                                None, C.OUTLINE, C.px(4))
                continue

            quad = [xf(x0, y0), xf(x1, y0), xf(x1, y1), xf(x0, y1)]

            if cell == W.GOAL:
                pulse = 0.5 + 0.5 * math.sin(t * 4.0)
                color = R.mix(C.GOAL, C.GOAL_GLOW, pulse)
                painter.face(camera, quad, color, outline=C.OUTLINE, width=C.px(3))
                _draw_goal_beacon(painter, camera, xf, cx, cy, t)
            elif cell == W.CRUMBLE:
                fuse = island.fuses.get((cx, cy))
                if fuse is None:
                    color = C.CRUMBLE
                else:
                    # Flash faster as the fuse burns down.
                    flash = 0.5 + 0.5 * math.sin(t * (14.0 + (1.5 - fuse) * 22.0))
                    color = R.mix(C.CRUMBLE, C.CRUMBLE_WARN, flash)
                painter.face(camera, quad, color, outline=C.OUTLINE, width=C.px(2))
            else:
                base = C.GRASS if (cx + cy) % 2 == 0 else C.GRASS_ALT
                painter.face(camera, quad, base)

    # Silhouette outline around the whole island top.
    pts = [camera.project(p) for p in corners_top]
    depth = sum(p[2] for p in pts) / 4.0
    painter.polygon(depth - 2.0, [(p[0], p[1]) for p in pts], None, C.OUTLINE, C.px(4))

    _draw_decor(painter, camera, island, xf)
    _draw_bounces(painter, camera, island, xf, t)
    _draw_vines(painter, camera, island, xf, t)


def _draw_goal_beacon(painter: R.Painter, camera: R.Camera, xf: BoardTransform,
                      cx: int, cy: int, t: float) -> None:
    """A soft column of light so the goal is findable from across the board."""
    top = xf(cx, cy, 150.0)
    sx, sy, depth = camera.project(top)
    scale = C.CAM_FOV / depth
    pulse = 0.5 + 0.5 * math.sin(t * 3.2)

    def paint(surface: pygame.Surface) -> None:
        R.blob(surface, (sx, sy), 46 * scale * (0.85 + 0.25 * pulse),
               C.GOAL_GLOW, int(60 + 40 * pulse))
        R.blob(surface, (sx, sy), 22 * scale, C.GOAL_GLOW, int(90 + 50 * pulse))

    painter.custom(depth - 30.0, paint)


def _draw_decor(painter: R.Painter, camera: R.Camera, island: W.Island,
                xf: BoardTransform) -> None:
    for fx, fy, scale, variant in island.decor:
        cx, cy = int(fx), int(fy)
        if not island.is_solid(cx, cy) or island.cells[cy][cx] != W.FLOOR:
            continue
        tip = xf(fx + 0.06 * scale, fy, DECOR_HEIGHT * scale)
        left = xf(fx - 0.11 * scale, fy, 0.0)
        right = xf(fx + 0.11 * scale, fy, 0.0)
        # Unlit: a tuft is a thin vertical sliver, so real shading drops it
        # into the darkest band and it reads as dirt rather than grass.
        color = R.shift(C.GRASS, 0.86 + variant * 0.09)
        painter.face(camera, [left, right, tip], color, unlit=True, cull=False)


def _draw_bounces(painter: R.Painter, camera: R.Camera, island: W.Island,
                  xf: BoardTransform, t: float) -> None:
    for pad in island.bounces:
        cx, cy = pad.cell
        squish = 0.5 + 0.5 * math.sin(t * 3.4 + pad.phase)
        height = 12.0 + squish * 10.0
        r = 0.42
        ring = []
        for i in range(10):
            a = i * math.tau / 10
            ring.append(xf(cx + math.cos(a) * r, cy + math.sin(a) * r, height))
        painter.face(camera, ring, R.mix(C.BOUNCE, (255, 255, 255), squish * 0.35),
                     outline=C.OUTLINE, width=C.px(3))


def _draw_vines(painter: R.Painter, camera: R.Camera, island: W.Island,
                xf: BoardTransform, t: float) -> None:
    for vine in island.vines:
        hx, hy = vine.head(t)
        segments = 12
        # Stem runs from the island edge to the head, sagging in the middle.
        if vine.axis == 0:
            start = (-0.5, vine.lane)
        else:
            start = (vine.lane, -0.5)

        points = []
        for i in range(segments + 1):
            f = i / segments
            px = R.lerp(start[0], hx, f)
            py = R.lerp(start[1], hy, f)
            lift = 26.0 + math.sin(f * math.pi) * 16.0 + math.sin(t * 2.4 + f * 5.0) * 5.0
            points.append(xf(px, py, lift))

        projected = [camera.project(p) for p in points]
        depth = sum(p[2] for p in projected) / len(projected)
        screen = [(p[0], p[1]) for p in projected]

        def paint(surface: pygame.Surface, screen=screen) -> None:
            pygame.draw.lines(surface, C.OUTLINE, False, screen, C.px(14))
            pygame.draw.lines(surface, C.VINE, False, screen, C.px(9))

        painter.custom(depth, paint)

        # Flower head — the part that actually shoves you.
        head_world = xf(hx, hy, 34.0)
        fx, fy, fdepth = camera.project(head_world)
        scale = C.CAM_FOV / fdepth
        spin = t * 1.6

        def paint_flower(surface: pygame.Surface, fx=fx, fy=fy, scale=scale, spin=spin) -> None:
            petal = 17 * scale
            for i in range(5):
                a = spin + i * math.tau / 5
                px, py = fx + math.cos(a) * petal, fy + math.sin(a) * petal
                pygame.draw.circle(surface, C.OUTLINE, (int(px), int(py)), int(petal * 0.78) + 2)
                pygame.draw.circle(surface, C.VINE_FLOWER, (int(px), int(py)), int(petal * 0.78))
            pygame.draw.circle(surface, C.OUTLINE, (int(fx), int(fy)), int(petal * 0.62) + 2)
            pygame.draw.circle(surface, C.GOAL, (int(fx), int(fy)), int(petal * 0.62))

        painter.custom(fdepth - 6.0, paint_flower)
