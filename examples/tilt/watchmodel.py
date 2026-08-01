"""A small live watch model in the corner, for visualisation.

The geometry, the pitch/roll derivation, the backface culling, the Z-sorting
and the shading are all lifted from ``examples/accelerometer_3d.py`` unchanged
— only the size and the screen position are parameterised. It is deliberately
*not* wired to the game's corrected gravity: it draws the raw vector exactly as
that example would, so it stays an honest reference for what the watch is
actually reporting rather than a second view of the same correction.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import pygame

from . import config as C


# ── 3D Math (from accelerometer_3d.py) ─────────────────────────────────────

def rotate_3d(point, pitch, roll):
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


def project_3d(point, center, fov, viewer_distance):
    x, y, z = point
    factor = fov / (viewer_distance + z) if (viewer_distance + z) != 0 else 1
    x_proj = x * factor + center[0]
    y_proj = -y * factor + center[1]      # Flip Y for screen coordinates
    return (int(x_proj), int(y_proj))


def normalize(v):
    mag = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / mag, v[1] / mag, v[2] / mag) if mag > 0 else (0, 0, 0)


def cross(v1, v2):
    return (
        v1[1] * v2[2] - v1[2] * v2[1],
        v1[2] * v2[0] - v1[0] * v2[2],
        v1[0] * v2[1] - v1[1] * v2[0],
    )


def sub(v1, v2):
    return (v1[0] - v2[0], v1[1] - v2[1], v1[2] - v2[2])


# ── The model ──────────────────────────────────────────────────────────────

class WatchModel:
    """A shaded cylinder that mirrors the watch's real orientation."""

    #: The example's own camera constants, at its own scale of radius 120.
    _REF_RADIUS = 120.0
    _REF_FOV = 400.0
    _REF_DISTANCE = 400.0

    def __init__(self, radius: float) -> None:
        self.scale = radius / self._REF_RADIUS
        self.fov = self._REF_FOV * self.scale
        self.distance = self._REF_DISTANCE * self.scale
        self._build_geometry()

    def _build_geometry(self) -> None:
        s = self.scale
        self.vertices = []
        self.faces = []  # (base_color, specular_power, [vertex indices])

        radius_outer = 120 * s
        radius_screen = 105 * s
        radius_ring = 85 * s
        radius_ring_inner = 75 * s
        thickness = 18 * s
        segments = 32

        color_body = (30, 32, 36)
        color_bezel = (160, 170, 185)   # Silver/Titanium
        color_screen = (5, 8, 12)       # Deep AMOLED black
        color_accent = (0, 255, 180)    # Cyan glow
        color_bottom = (20, 22, 25)

        def add_ring(r, z):
            start_idx = len(self.vertices)
            for i in range(segments):
                angle = i * (2 * math.pi / segments)
                self.vertices.append((math.cos(angle) * r, math.sin(angle) * r, z))
            return start_idx

        idx_top_out = add_ring(radius_outer, thickness)
        idx_bot_out = add_ring(radius_outer, -thickness)
        idx_screen = add_ring(radius_screen, thickness)
        idx_ring_out = add_ring(radius_ring, thickness - s)
        idx_ring_in = add_ring(radius_ring_inner, thickness - s)
        idx_center = len(self.vertices)
        self.vertices.append((0, 0, thickness - s))

        # Body (side cylinder)
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_body, 2, [
                idx_top_out + i, idx_bot_out + i, idx_bot_out + nxt, idx_top_out + nxt,
            ]))

        # Bottom face
        self.faces.append((color_bottom, 1,
                           [idx_bot_out + i for i in reversed(range(segments))]))

        # Metallic bezel
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_bezel, 12, [
                idx_top_out + i, idx_top_out + nxt, idx_screen + nxt, idx_screen + i,
            ]))

        # Screen outer
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_screen, 25, [
                idx_screen + i, idx_screen + nxt, idx_ring_out + nxt, idx_ring_out + i,
            ]))

        # Glowing ring
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_accent, 0, [
                idx_ring_out + i, idx_ring_out + nxt, idx_ring_in + nxt, idx_ring_in + i,
            ]))

        # Screen inner
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_screen, 25, [
                idx_ring_in + i, idx_ring_in + nxt, idx_center,
            ]))

        # 12 o'clock red marker on the bezel
        idx_12_tip = len(self.vertices)
        self.vertices.append((0, radius_outer - 5 * s, thickness + s))
        idx_12_l = len(self.vertices)
        self.vertices.append((-8 * s, radius_screen + 2 * s, thickness + s))
        idx_12_r = len(self.vertices)
        self.vertices.append((8 * s, radius_screen + 2 * s, thickness + s))
        self.faces.append(((255, 50, 50), 2, [idx_12_tip, idx_12_l, idx_12_r]))

    # ── Drawing ────────────────────────────────────────────────────────────

    def draw(self, surface: pygame.Surface, center: Tuple[float, float],
             gravity: Sequence[float]) -> Tuple[float, float]:
        """Render at ``center`` from a raw gravity vector. Returns pitch, roll."""
        gx, gy, gz = gravity
        pitch = math.atan2(-gy, math.sqrt(gx ** 2 + gz ** 2))
        roll = math.atan2(gx, gz)

        transformed = []
        for v in self.vertices:
            rotated = rotate_3d(v, pitch, roll)
            # Rotate the camera 90 degrees around X to view the watch edge-on.
            transformed.append((rotated[0], -rotated[2], rotated[1]))

        faces_to_draw = []
        light_dir = normalize((0.5, 0.7, -1.0))

        for base_color, spec_power, indices in self.faces:
            pts_3d = [transformed[i] for i in indices]

            v0, v1, v2 = pts_3d[0], pts_3d[1], pts_3d[2]
            normal = normalize(cross(sub(v1, v0), sub(v2, v1)))

            if normal[2] > 0:          # backface
                continue

            if spec_power == 0:
                shaded_color = base_color
            else:
                intensity = max(0.0, -(normal[0] * light_dir[0]
                                       + normal[1] * light_dir[1]
                                       + normal[2] * light_dir[2]))
                specular = (intensity ** spec_power) * 0.6 if intensity > 0 else 0
                light = min(1.0, 0.25 + intensity * 0.7 + specular)
                shaded_color = (int(base_color[0] * light),
                                int(base_color[1] * light),
                                int(base_color[2] * light))

            avg_z = sum(p[2] for p in pts_3d) / len(pts_3d)
            pts_2d = [project_3d(p, center, self.fov, self.distance) for p in pts_3d]
            faces_to_draw.append((avg_z, pts_2d, shaded_color))

        # Painter's algorithm: furthest first.
        faces_to_draw.sort(key=lambda item: item[0], reverse=True)
        for _, pts_2d, color in faces_to_draw:
            pygame.draw.polygon(surface, color, pts_2d)
            pygame.draw.polygon(surface, color, pts_2d, 1)

        return pitch, roll


def draw_corner(surface: pygame.Surface, model: "WatchModel", font,
                gravity: Sequence[float], connecting: bool = False, t: float = 0.0) -> None:
    """The model on its own rounded plate, tucked into the bottom right."""
    size = C.px(190)
    margin = C.px(26)
    rect = pygame.Rect(C.WIDTH - size - margin, C.HEIGHT - size - margin, size, size)

    plate = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(plate, (14, 20, 28, 150), plate.get_rect(), border_radius=C.px(18))
    
    if connecting:
        # Pulse a cyan border while pairing
        pulse = 0.5 + 0.5 * math.sin(t * 4.5)
        glow_alpha = int(90 + 90 * pulse)
        pygame.draw.rect(plate, (0, 255, 180, glow_alpha), plate.get_rect(),
                         width=C.px(2), border_radius=C.px(18))
    else:
        pygame.draw.rect(plate, (*C.PAPER, 70), plate.get_rect(),
                         width=C.px(2), border_radius=C.px(18))
    surface.blit(plate, rect.topleft)

    if connecting:
        # Rotate gravity in a circle so the watch model spins/wobbles dynamically while scanning
        angle = t * 3.2
        gravity = (math.sin(angle) * 0.6, math.cos(angle) * 0.6, -0.8)

    pitch, roll = model.draw(surface, rect.center, gravity)

    if connecting:
        dots = "." * (int(t * 2.5) % 4)
        label_text = f"PAIRING{dots:<3}"
        color = (0, 255, 180)
    else:
        label_text = f"{math.degrees(pitch):+.0f}° {math.degrees(roll):+.0f}°"
        color = (170, 214, 200)

    label = font.render(label_text, True, color)
    surface.blit(label, label.get_rect(midbottom=(rect.centerx, rect.bottom - C.px(8))))
