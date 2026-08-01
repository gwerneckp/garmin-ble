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

    def update(self, dt: float, ball, danger: float = 0.0, dying: bool = False) -> None:
        """Advance the face simulation, blinking and steering the gaze."""
        if self.next_blink is None:
            self.next_blink = self.rng.uniform(1.8, 4.5)

        self.next_blink -= dt
        if self.next_blink <= 0.0:
            self.blink = 0.14
            self.next_blink = self.rng.uniform(1.8, 4.5)
        self.blink = max(0.0, self.blink - dt)
        self.wince = max(0.0, self.wince - dt * 2.2)

        if dying:
            # Spin rapidly on an arbitrary axis
            axis = R.normalize((1.0, 0.4, 0.2))
            self.spin = R.mat_mul(R.axis_angle_matrix(axis, 12.0 * dt), self.spin)
        else:
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
              height: float, squash: Tuple[float, float], happy: bool = False, dying: bool = False) -> None:
        """Emit the body as geometry and the face as a screen-space overlay."""
        centre = xf(ball.x, ball.y, height)
        _, _, centre_depth = camera.project(centre)

        r = C.BALL_RADIUS
        wide, tall = squash

        # Toon silhouette: a dark disc queued immediately behind the front faces
        # of the ball. This ensures it draws on top of the board tiles (at depth + r)
        # but underneath the ball itself (at depth <= centre_depth).
        def paint_outline(surface: pygame.Surface) -> None:
            ox, oy, odepth = camera.project(centre)
            orad = r * C.CAM_FOV / odepth
            pygame.draw.circle(surface, C.OUTLINE, (int(ox), int(oy)),
                               int(orad * 1.10 * max(wide, tall)))

        painter.custom(centre_depth + 0.5, paint_outline)

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
                       lambda surf: self._draw_overlay(surf, camera, centre, r, happy, dying))

    def _draw_overlay(self, surface: pygame.Surface, camera: R.Camera,
                      centre, radius_world: float, happy: bool = False, dying: bool = False) -> None:
        cx, cy, depth = camera.project(centre)
        radius = radius_world * C.CAM_FOV / depth
        
        # 1. Draw a soft shading/shadow on the bottom-right of the ball itself
        # to give it a nicer volumetric feel.
        sh_radius = radius * 0.98
        sh_surf = pygame.Surface((sh_radius * 2, sh_radius * 2), pygame.SRCALPHA)
        pygame.draw.circle(sh_surf, (20, 10, 30, 48), (sh_radius, sh_radius), sh_radius)
        # Shift slightly bottom-right
        surface.blit(sh_surf, (cx - sh_radius + radius * 0.12, cy - sh_radius + radius * 0.12))

        # 2. Draw shadows of leaf and face elements on the ball
        self._draw_leaf_shadow(surface, (cx, cy - radius * 0.78), radius, cx, cy)
        self._draw_face_shadows(surface, (cx, cy), radius, happy, dying)

        # 3. Draw actual leaf and face elements
        self._draw_leaf(surface, (cx, cy - radius * 0.78), radius, cx, cy)
        self._draw_face(surface, (cx, cy), radius, happy, dying)

    def _draw_leaf_shadow(self, surface: pygame.Surface, anchor: Tuple[float, float],
                          radius: float, cx: float, cy: float) -> None:
        ax, ay = anchor
        # Shift the shadow anchor slightly in the direction opposite to the light
        ax_sh = ax + radius * 0.08
        ay_sh = ay + radius * 0.08
        cx_sh = cx + radius * 0.08
        cy_sh = cy + radius * 0.08

        sway = math.sin(pygame.time.get_ticks() * 0.004) * 0.12
        angle = -math.pi / 2 + sway + math.cos(self.leaf_angle) * 0.55

        length = radius * 1.15
        tip = (ax_sh + math.cos(angle) * length, ay_sh + math.sin(angle) * length)
        side = angle + math.pi / 2
        w = radius * 0.36

        # Draw rounded leaf shape using a sin-based width curve
        leaf_pts_right = []
        leaf_pts_left = []
        segments = 6
        for i in range(1, segments):
            t = i / segments
            px = R.lerp(ax_sh, tip[0], t)
            py = R.lerp(ay_sh, tip[1], t)
            cur_w = w * math.sin(t * math.pi)
            leaf_pts_right.append((px + math.cos(side) * cur_w, py + math.sin(side) * cur_w))
            leaf_pts_left.insert(0, (px - math.cos(side) * cur_w, py - math.sin(side) * cur_w))

        leaf_shadow = [(ax_sh, ay_sh)] + leaf_pts_right + [tip] + leaf_pts_left
        
        # Draw semi-transparent shadow
        shadow_surf = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
        local_points = [(p[0] - ax_sh + radius * 2, p[1] - ay_sh + radius * 2) for p in leaf_shadow]
        
        # Stem shadow
        pygame.draw.line(shadow_surf, (20, 25, 35, 75), 
                         (cx_sh - ax_sh + radius * 2, cy_sh - radius * 0.7 - ay_sh + radius * 2), 
                         (radius * 2, radius * 2), max(3, int(radius * 0.09)))
        pygame.draw.polygon(shadow_surf, (20, 25, 35, 75), local_points)
        surface.blit(shadow_surf, (ax_sh - radius * 2, ay_sh - radius * 2))

    def _draw_leaf(self, surface: pygame.Surface, anchor: Tuple[float, float],
                   radius: float, cx: float, cy: float) -> None:
        ax, ay = anchor
        sway = math.sin(pygame.time.get_ticks() * 0.004) * 0.12
        angle = -math.pi / 2 + sway + math.cos(self.leaf_angle) * 0.55

        length = radius * 1.15
        tip = (ax + math.cos(angle) * length, ay + math.sin(angle) * length)
        side = angle + math.pi / 2
        w = radius * 0.36

        # Draw a little connecting stem
        pygame.draw.line(surface, C.OUTLINE, (int(cx), int(cy - radius * 0.7)), (int(ax), int(ay)), max(3, int(radius * 0.09)))
        pygame.draw.line(surface, C.VINE, (int(cx), int(cy - radius * 0.7)), (int(ax), int(ay)), max(1, int(radius * 0.05)))

        # Draw rounded leaf shape using a sin-based width curve
        leaf_pts_right = []
        leaf_pts_left = []
        segments = 6
        for i in range(1, segments):
            t = i / segments
            px = R.lerp(ax, tip[0], t)
            py = R.lerp(ay, tip[1], t)
            cur_w = w * math.sin(t * math.pi)
            leaf_pts_right.append((px + math.cos(side) * cur_w, py + math.sin(side) * cur_w))
            leaf_pts_left.insert(0, (px - math.cos(side) * cur_w, py - math.sin(side) * cur_w))

        leaf = [(ax, ay)] + leaf_pts_right + [tip] + leaf_pts_left

        pygame.draw.polygon(surface, C.SPROUT_LEAF, leaf)
        pygame.draw.polygon(surface, C.OUTLINE, leaf, max(2, int(radius * 0.10)))
        pygame.draw.line(surface, C.SPROUT_LEAF_LIT,
                         (int(ax), int(ay)), (int(tip[0]), int(tip[1])),
                         max(1, int(radius * 0.08)))

    def _draw_face_shadows(self, surface: pygame.Surface, center: Tuple[float, float],
                           radius: float, happy: bool = False, dying: bool = False) -> None:
        cx, cy = center
        # Shift the shadow slightly down and right
        cx_sh = cx + radius * 0.04
        cy_sh = cy + radius * 0.04

        spacing = radius * 0.36
        eye_y = cy_sh - radius * 0.02 + self.look[1] * radius * 0.12
        offset = self.look[0] * radius * 0.14

        base_w = radius * 0.24 * (1.0 + self.alarm * 0.40)
        base_h = radius * 0.32 * (1.0 + self.alarm * 0.50)

        shadow_color = (20, 25, 35, 65) # Semi-transparent dark shadow

        if dying:
            for sign in (-1, 1):
                ex = cx_sh + sign * spacing + offset
                h_w = base_w * 0.7
                h_h = base_h * 0.7
                pygame.draw.line(surface, shadow_color, (int(ex - h_w), int(eye_y - h_h)), (int(ex + h_w), int(eye_y + h_h)), max(3, int(radius * 0.11)))
                pygame.draw.line(surface, shadow_color, (int(ex - h_w), int(eye_y + h_h)), (int(ex + h_w), int(eye_y - h_h)), max(3, int(radius * 0.11)))
            
            mouth_w = radius * 0.14
            mouth_h = radius * 0.22
            mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
            mouth.center = (int(cx_sh + offset * 0.6), int(eye_y + base_h * 1.5))
            pygame.draw.ellipse(surface, shadow_color, mouth)
            return

        if happy:
            for sign in (-1, 1):
                ex = cx_sh + sign * spacing + offset
                h_w = base_w * 0.8
                h_h = base_h * 0.7
                pts = [
                    (int(ex - h_w), int(eye_y + h_h * 0.3)),
                    (int(ex), int(eye_y - h_h * 0.7)),
                    (int(ex + h_w), int(eye_y + h_h * 0.3))
                ]
                pygame.draw.lines(surface, shadow_color, False, pts, max(3, int(radius * 0.11)))
            
            mouth_w = radius * 0.18
            mouth_h = radius * 0.18
            mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
            mouth.center = (int(cx_sh + offset * 0.6), int(eye_y + base_h * 1.45))
            pygame.draw.ellipse(surface, shadow_color, mouth)
            return

        if self.blink > 0.0 or self.wince > 0.55:
            for sign in (-1, 1):
                ex = cx_sh + sign * spacing + offset
                pygame.draw.line(
                    surface, shadow_color,
                    (int(ex - base_w * 0.9), int(eye_y)),
                    (int(ex + base_w * 0.9), int(eye_y)),
                    max(2, int(base_h * 0.42)),
                )
            return

        for sign in (-1, 1):
            ex = cx_sh + sign * spacing + offset
            rect = pygame.Rect(0, 0, max(2, int(base_w * 2)), max(2, int(base_h * 2)))
            rect.center = (int(ex), int(eye_y))
            pygame.draw.ellipse(surface, shadow_color, rect)

        # Mouth shadow
        mouth_w = radius * (0.10 + self.alarm * 0.13)
        mouth_h = radius * (0.07 + self.alarm * 0.16)
        mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
        mouth.center = (int(cx_sh + offset * 0.6), int(eye_y + base_h * 1.55))
        pygame.draw.ellipse(surface, shadow_color, mouth)

    def _draw_face(self, surface: pygame.Surface, center: Tuple[float, float],
                   radius: float, happy: bool = False, dying: bool = False) -> None:
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

        if dying:
            for sign in (-1, 1):
                ex = cx + sign * spacing + offset
                h_w = base_w * 0.7
                h_h = base_h * 0.7
                # Draw X eyes
                pygame.draw.line(surface, C.SPROUT_EYE, (int(ex - h_w), int(eye_y - h_h)), (int(ex + h_w), int(eye_y + h_h)), max(3, int(radius * 0.11)))
                pygame.draw.line(surface, C.SPROUT_EYE, (int(ex - h_w), int(eye_y + h_h)), (int(ex + h_w), int(eye_y - h_h)), max(3, int(radius * 0.11)))
            
            # Wailing mouth
            mouth_w = radius * 0.14
            mouth_h = radius * 0.22
            mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
            mouth.center = (int(cx + offset * 0.6), int(eye_y + base_h * 1.5))
            pygame.draw.ellipse(surface, C.SPROUT_EYE, mouth)
            return

        if happy:
            for sign in (-1, 1):
                ex = cx + sign * spacing + offset
                # Draw a wedge ^ using lines
                h_w = base_w * 0.8
                h_h = base_h * 0.7
                pts = [
                    (int(ex - h_w), int(eye_y + h_h * 0.3)),
                    (int(ex), int(eye_y - h_h * 0.7)),
                    (int(ex + h_w), int(eye_y + h_h * 0.3))
                ]
                pygame.draw.lines(surface, C.SPROUT_EYE, False, pts, max(3, int(radius * 0.11)))
            
            # Big happy open mouth
            mouth_w = radius * 0.18
            mouth_h = radius * 0.18
            mouth = pygame.Rect(0, 0, max(2, int(mouth_w * 2)), max(2, int(mouth_h * 2)))
            mouth.center = (int(cx + offset * 0.6), int(eye_y + base_h * 1.45))
            pygame.draw.ellipse(surface, C.SPROUT_EYE, mouth)
            return

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
