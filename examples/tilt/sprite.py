"""The seed-sprite: a real 3D ball that rolls, wearing a face.

The body is an icosphere carried through the same transform as the board, so
it is lit and depth-sorted like everything else and — crucially — it *spins*.
The rotation is derived from the distance actually rolled, so the surface
patches track the ground the way a real ball does instead of sliding.

The face and the leaf stay upright and camera-facing on top of it. That is the
Kirby compromise: a face tumbling with the mesh becomes unreadable within half
a revolution, and unreadable is the opposite of charming.
"""

from __future__ import annotations

import math
import random
from typing import Tuple

import pygame

from . import config as C
from . import render as R
from . import world as W

#: Two subdivisions: 320 faces. From straight overhead the silhouette is
#: the whole read on the character, and 80 faces left it visibly faceted.
_VERTS, _FACES = R.icosphere(2)

#: A scattering of faces gets a darker tint so the spin reads on a plain ball.
_PATCH = frozenset(range(7, 320, 23))


class Sprite:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.blink = 0.0
        self.next_blink = rng.uniform(1.5, 4.0)
        self.look = (0.0, 0.0)
        self.alarm = 0.0        # 0 calm .. 1 wide-eyed
        self.leaf_angle = 0.0
        self.wince = 0.0
        self.spin = R.IDENTITY   # board-local orientation of the body
        self._reortho = 0

    # ── State ──────────────────────────────────────────────────────────────

    def update(self, dt: float, ball, danger: float = 0.0) -> None:
        self.next_blink -= dt
        if self.next_blink <= 0.0:
            self.blink = 0.14
            self.next_blink = self.rng.uniform(1.8, 4.5)
        self.blink = max(0.0, self.blink - dt)
        self.wince = max(0.0, self.wince - dt * 2.2)

        self._roll(dt, ball)

        # Eyes lead the motion, saturating so they never leave the face.
        target = (
            R.clamp(ball.vx / 6.0, -1.0, 1.0),
            R.clamp(ball.vy / 6.0, -1.0, 1.0),
        )
        self.look = (
            R.lerp(self.look[0], target[0], min(1.0, dt * 9.0)),
            R.lerp(self.look[1], target[1], min(1.0, dt * 9.0)),
        )

        self.alarm = R.lerp(self.alarm, danger, min(1.0, dt * 6.0))

        # The leaf lags behind the direction of travel, like hair.
        if ball.speed > 0.4:
            want = math.atan2(ball.vy, ball.vx)
            delta = (want - self.leaf_angle + math.pi) % math.tau - math.pi
            self.leaf_angle += delta * min(1.0, dt * 4.0)

    def _roll(self, dt: float, ball) -> None:
        """Spin the body by the arc it just rolled through.

        For a ball rolling without slipping on a plane with normal ``n``, the
        angular velocity is ``(n x v) / r``. Integrating that is what keeps the
        surface locked to the ground rather than drifting against it.
        """
        vx, vy = ball.vx * C.CELL, ball.vy * C.CELL   # cells/s -> world units/s
        speed = math.hypot(vx, vy)
        if speed < 1e-4:
            return

        # n = (0, 0, 1) in board-local space, so n x v = (-vy, vx, 0).
        axis = R.normalize((-vy, vx, 0.0))
        angle = (speed / C.BALL_RADIUS) * dt
        self.spin = R.mat_mul(R.axis_angle_matrix(axis, angle), self.spin)

        # Repeated incremental products drift out of orthonormality; this
        # costs nothing twice a second and keeps the ball from shearing.
        self._reortho += 1
        if self._reortho >= 120:
            self._reortho = 0
            self.spin = R.orthonormalize(self.spin)

    def bump(self) -> None:
        self.wince = 1.0

    # ── Drawing ────────────────────────────────────────────────────────────

    def queue(self, painter: R.Painter, camera: R.Camera, xf, ball,
              height: float, squash: Tuple[float, float]) -> None:
        """Emit the body as geometry and the face as a screen-space overlay."""
        centre = xf(ball.x, ball.y, height)
        _, _, centre_depth = camera.project(centre)

        r = C.BALL_RADIUS
        wide, tall = squash

        # Toon silhouette: a dark disc queued *behind* the mesh. Outlining the
        # individual triangles instead would draw every interior edge too, and
        # the ball would read as a wireframe rather than a drawn character.
        def paint_outline(surface: pygame.Surface) -> None:
            ox, oy, odepth = camera.project(centre)
            orad = r * C.CAM_FOV / odepth
            pygame.draw.circle(surface, C.OUTLINE, (int(ox), int(oy)),
                               int(orad * 1.10 * max(wide, tall)))

        painter.custom(centre_depth + r * 0.9, paint_outline)

        # Transform each vertex once. Every vertex is shared by about six
        # faces, so doing it per face-corner was six times the work for the
        # same 162 points.
        spin = self.spin
        cell = C.CELL
        world = []
        for v in _VERTS:
            wv = R.mat_apply(spin, v)
            # Squash in board-local space, so it flattens against the ground
            # rather than against the screen.
            world.append(xf(
                ball.x + (wv[0] * r * wide) / cell,
                ball.y + (wv[1] * r * wide) / cell,
                height + wv[2] * r * tall,
            ))

        for index, (ia, ib, ic) in enumerate(_FACES):
            base = C.SPROUT_SPOT if index in _PATCH else C.SPROUT_BODY
            painter.face(camera, (world[ia], world[ib], world[ic]), base,
                         bands=C.CEL_BANDS_SOFT)

        painter.custom(centre_depth - r * 1.6,
                       lambda surf: self._draw_overlay(surf, camera, centre, r))

    def _draw_overlay(self, surface: pygame.Surface, camera: R.Camera,
                      centre, radius_world: float) -> None:
        cx, cy, depth = camera.project(centre)
        radius = radius_world * C.CAM_FOV / depth
        self._draw_leaf(surface, (cx, cy - radius * 0.78), radius)
        self._draw_face(surface, (cx, cy), radius)

    def _draw_leaf(self, surface: pygame.Surface, anchor: Tuple[float, float],
                   radius: float) -> None:
        ax, ay = anchor
        sway = math.sin(pygame.time.get_ticks() * 0.004) * 0.12
        angle = -math.pi / 2 + sway + math.cos(self.leaf_angle) * 0.55

        length = radius * 1.08
        tip = (ax + math.cos(angle) * length, ay + math.sin(angle) * length)
        side = angle + math.pi / 2
        w = radius * 0.30
        mid = ((ax + tip[0]) * 0.5, (ay + tip[1]) * 0.5)

        leaf = [
            (ax, ay),
            (mid[0] + math.cos(side) * w, mid[1] + math.sin(side) * w),
            tip,
            (mid[0] - math.cos(side) * w, mid[1] - math.sin(side) * w),
        ]
        pygame.draw.polygon(surface, C.SPROUT_LEAF, leaf)
        pygame.draw.polygon(surface, C.OUTLINE, leaf, max(2, int(radius * 0.10)))
        pygame.draw.line(surface, C.SPROUT_LEAF_LIT,
                         (int(ax), int(ay)), (int(tip[0]), int(tip[1])),
                         max(1, int(radius * 0.08)))

    def _draw_face(self, surface: pygame.Surface, center: Tuple[float, float],
                   radius: float) -> None:
        """Big eyes, blush and a small mouth, facing the camera.

        Seen from directly above, the face is the only thing telling you this
        is a character rather than a marble, so it is drawn large and always
        looking back up at you.
        """
        cx, cy = center
        spacing = radius * 0.36
        eye_y = cy - radius * 0.02 + self.look[1] * radius * 0.12
        offset = self.look[0] * radius * 0.14

        base_w = radius * 0.24 * (1.0 + self.alarm * 0.40)
        base_h = radius * 0.32 * (1.0 + self.alarm * 0.50)

        # Blush sits behind the eyes and stays put.
        blush_r = max(1, int(radius * 0.15))
        for sign in (-1, 1):
            bx = int(cx + sign * radius * 0.60 + offset * 0.4)
            patch = pygame.Surface((blush_r * 2, blush_r * 2), pygame.SRCALPHA)
            pygame.draw.circle(patch, (*C.SPROUT_BLUSH, 130),
                               (blush_r, blush_r), blush_r)
            surface.blit(patch, (bx - blush_r, int(eye_y + radius * 0.16) - blush_r))

        if self.blink > 0.0 or self.wince > 0.55:
            for sign in (-1, 1):
                ex = cx + sign * spacing + offset
                pygame.draw.line(
                    surface, C.SPROUT_EYE,
                    (int(ex - base_w * 0.9), int(eye_y)),
                    (int(ex + base_w * 0.9), int(eye_y)),
                    max(2, int(base_h * 0.42)),
                )
            return

        for sign in (-1, 1):
            ex = cx + sign * spacing + offset
            rect = pygame.Rect(0, 0, max(2, int(base_w * 2)), max(2, int(base_h * 2)))
            rect.center = (int(ex), int(eye_y))
            pygame.draw.ellipse(surface, C.SPROUT_EYE, rect)

            # Two catchlights — one big towards the sun, one small opposite.
            big = pygame.Rect(0, 0, max(2, int(base_w * 0.82)), max(2, int(base_h * 0.72)))
            big.center = (int(ex - base_w * 0.30), int(eye_y - base_h * 0.36))
            pygame.draw.ellipse(surface, (255, 255, 255), big)

            small = pygame.Rect(0, 0, max(1, int(base_w * 0.34)), max(1, int(base_h * 0.30)))
            small.center = (int(ex + base_w * 0.40), int(eye_y + base_h * 0.34))
            pygame.draw.ellipse(surface, (255, 255, 255), small)

        # A small open mouth, wider when alarmed.
        mouth_w = radius * (0.10 + self.alarm * 0.13)
        mouth_h = radius * (0.07 + self.alarm * 0.16)
        mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
        mouth.center = (int(cx + offset * 0.6), int(eye_y + base_h * 1.55))
        pygame.draw.ellipse(surface, C.SPROUT_EYE, mouth)


def danger_level(ball, island: W.Island) -> float:
    """How close the sprite is to a hole or an edge, as 0..1.

    Sampling ahead along the velocity is what makes the wide-eyed look land
    *before* the fall rather than during it.
    """
    fx = ball.x + R.clamp(ball.vx, -6.0, 6.0) * 0.09
    fy = ball.y + R.clamp(ball.vy, -6.0, 6.0) * 0.09

    worst = 0.0
    for dx, dy in ((0.0, 0.0), (0.6, 0.0), (-0.6, 0.0), (0.0, 0.6), (0.0, -0.6)):
        cx = int(math.floor(fx + dx + 0.5))
        cy = int(math.floor(fy + dy + 0.5))
        if not island.is_solid(cx, cy):
            worst = max(worst, 1.0 - math.hypot(dx, dy) * 0.7)
    return R.clamp(worst, 0.0, 1.0)
