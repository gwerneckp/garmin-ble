"""A small cel-shading software renderer.

There is no mesh format here on purpose. Each system (board, hazards, sprite)
knows its own geometry and emits polygons into a :class:`Painter`, which sorts
everything back-to-front and draws it. That keeps the renderer to one idea —
*transform, shade, sort, draw* — and lets the board punch holes in itself
without fighting a scene graph.
"""

from __future__ import annotations

import math
from typing import List, Sequence, Tuple

import pygame

from . import config as C

Vec3 = Tuple[float, float, float]

#: Nothing projects closer than this. See :meth:`Camera.project`.
NEAR_PLANE = 140.0

#: Screen-space art is clamped to this radius before any surface is allocated.
MAX_SCREEN_RADIUS = 1600.0


# ── Vector helpers ─────────────────────────────────────────────────────────

def normalize(v: Vec3) -> Vec3:
    m = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / m, v[1] / m, v[2] / m) if m else (0.0, 0.0, 0.0)


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


_LIGHT = normalize(C.LIGHT_DIR)


def rotate_light(pitch: float, roll: float) -> None:
    """Rotate the light vector by the board's pitch and roll so light is board-local.

    This prevents sudden changes/pops in board shading when the board tilts.
    """
    global _LIGHT
    lx, ly, lz = C.LIGHT_DIR
    # Convert world-space LIGHT_DIR to board-local coordinates
    x, y, z = lx, lz, -ly

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

    # Apply the same coordinate swap as BoardTransform: (rx, -rz, ry)
    _LIGHT = normalize((x2, -z2, y1))


def cel_shade(base: Sequence[int], normal: Vec3, bands=None) -> Tuple[int, int, int]:
    """Quantise Lambert lighting into a few hard bands.

    The banding *is* the look: a smooth gradient here would read as generic
    3D, while a few flat steps read as hand-drawn.
    """
    bands = bands or C.CEL_BANDS
    intensity = max(0.0, -dot(normal, _LIGHT))
    mult = bands[-1][1]
    for threshold, value in bands:
        if intensity >= threshold:
            mult = value
            break

    warm = C.RIM_WARMTH if mult >= 1.0 else (0, 0, 0)
    return (
        min(255, int(base[0] * mult) + warm[0]),
        min(255, int(base[1] * mult) + warm[1]),
        min(255, int(base[2] * mult) + warm[2]),
    )


def shift(color: Sequence[int], amount: float) -> Tuple[int, int, int]:
    """Brighten (amount > 1) or darken (amount < 1) a colour."""
    return (
        int(clamp(color[0] * amount, 0, 255)),
        int(clamp(color[1] * amount, 0, 255)),
        int(clamp(color[2] * amount, 0, 255)),
    )


def mix(a: Sequence[int], b: Sequence[int], t: float) -> Tuple[int, int, int]:
    return (
        int(lerp(a[0], b[0], t)),
        int(lerp(a[1], b[1], t)),
        int(lerp(a[2], b[2], t)),
    )


# ── Camera ─────────────────────────────────────────────────────────────────

class Camera:
    """A fixed 3/4 view that leans gently against the board.

    World space is Y-up. The camera orbits by pitch only — there is no yaw
    control, because the player already has two axes and a third would make
    the board unreadable.
    """

    def __init__(self) -> None:
        self.pitch = C.CAM_PITCH
        self.roll = 0.0
        self.distance = C.CAM_DISTANCE
        self.target_roll = 0.0
        self.target_distance = C.CAM_DISTANCE
        self.origin = (0.0, 0.0, 0.0)
        self.shake = 0.0
        self._shake_offset = (0.0, 0.0)

    def follow(self, board_roll: float, speed_fraction: float) -> None:
        """Lean against the board and push in as the sprite speeds up."""
        self.target_roll = -board_roll * C.CAM_COUNTER_LEAN
        self.target_distance = C.CAM_DISTANCE - C.CAM_SPEED_DOLLY * speed_fraction

    def update(self, dt: float, rng) -> None:
        self.roll = lerp(self.roll, self.target_roll, C.CAM_LERP)
        self.distance = lerp(self.distance, self.target_distance, C.CAM_LERP)

        if self.shake > 0.0:
            self.shake = max(0.0, self.shake - dt * 3.2)
            amp = self.shake * 14.0
            self._shake_offset = (rng.uniform(-amp, amp), rng.uniform(-amp, amp))
        else:
            self._shake_offset = (0.0, 0.0)

    def kick(self, amount: float) -> None:
        self.shake = min(1.0, self.shake + amount)

    def project(self, p: Vec3) -> Tuple[float, float, float]:
        """World point -> (screen x, screen y, view depth).

        Depth is distance from the camera, so painter's-algorithm sorting is a
        plain descending sort.
        """
        x = p[0] - self.origin[0]
        y = p[1] - self.origin[1]
        z = p[2] - self.origin[2]

        # Camera roll about the view axis.
        cr, sr = math.cos(self.roll), math.sin(self.roll)
        x, y = x * cr - y * sr, x * sr + y * cr

        # Camera pitch: tip the world forward so we look down on it. The sign
        # matters — with it reversed, raising a point *increases* its depth,
        # and anything standing on the board sorts behind the board.
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        y, z = y * cp + z * sp, -y * sp + z * cp

        # Clamp at a near plane rather than at zero. Without this, geometry
        # passing behind the camera projects to a scale of thousands, and the
        # screen-space art derived from it asks SDL for surfaces big enough to
        # abort the process.
        depth = max(NEAR_PLANE, z + self.distance)
        f = C.CAM_FOV / depth

        sx = x * f + C.WIDTH * 0.5 + self._shake_offset[0]
        sy = -y * f + C.HEIGHT * 0.5 + C.CAM_HEIGHT + self._shake_offset[1]
        return (sx, sy, depth)

    def scale_at(self, p: Vec3) -> float:
        """Perspective scale factor at a world point, for screen-space art."""
        _, _, depth = self.project(p)
        return C.CAM_FOV / depth


# ── Painter ────────────────────────────────────────────────────────────────

class Painter:
    """Collects draw calls, then flushes them back-to-front.

    Both polygons and arbitrary callables can be queued, which is what lets
    the sprite draw itself as fast screen-space circles while still sorting
    correctly against the board it is rolling on.
    """

    __slots__ = ("_items",)

    def __init__(self) -> None:
        self._items: List[Tuple[float, int, object, object, object, int]] = []

    def polygon(
        self,
        depth: float,
        points: Sequence[Tuple[float, float]],
        color: Sequence[int] | None,
        outline: Sequence[int] | None = None,
        width: int = 0,
    ) -> None:
        """Queue a polygon. ``color=None`` draws the outline only.

        The display surface is opaque, so a "transparent" fill colour would
        paint solid black rather than nothing.
        """
        self._items.append((depth, 0, points, color, outline, width))

    def custom(self, depth: float, fn) -> None:
        """Queue a ``fn(surface)`` to run at this depth."""
        self._items.append((depth, 1, fn, None, None, 0))

    def face(
        self,
        camera: Camera,
        points3: Sequence[Vec3],
        base_color: Sequence[int],
        *,
        outline: Sequence[int] | None = None,
        width: int = 0,
        unlit: bool = False,
        cull: bool = True,
        bands=None,
    ) -> bool:
        """Transform, shade, cull and queue one convex polygon.

        Returns whether the face survived culling, so callers can skip work
        that only matters for visible faces.
        """
        projected = [camera.project(p) for p in points3]

        # Wind-order cull in screen space. The board frame is a proper
        # rotation of the watch frame (no mirroring), which makes front-facing
        # polygons wind positive here.
        area = 0.0
        for i in range(len(projected)):
            x1, y1, _ = projected[i]
            x2, y2, _ = projected[(i + 1) % len(projected)]
            area += x1 * y2 - x2 * y1
        if cull and area <= 0.0:
            return False

        if unlit:
            color = tuple(base_color)
        else:
            n = normalize(cross(sub(points3[1], points3[0]), sub(points3[2], points3[1])))
            color = cel_shade(base_color, n, bands)

        depth = sum(p[2] for p in projected) / len(projected)
        self.polygon(depth, [(p[0], p[1]) for p in projected], color, outline, width)
        return True

    def flush(self, surface: pygame.Surface) -> None:
        self._items.sort(key=lambda it: it[0], reverse=True)
        for _, kind, a, color, outline, width in self._items:
            if kind == 1:
                a(surface)
                continue
            if len(a) < 3:
                continue
            if any(not (-1e5 < x < 1e5 and -1e5 < y < 1e5) for x, y in a):
                continue     # NaN or a projection blowup; SDL would abort
            if color is not None:
                pygame.draw.polygon(surface, color, a)
                # Redraw the same colour as a hairline: pygame's polygon fill
                # leaves seams between adjacent faces otherwise.
                pygame.draw.polygon(surface, color, a, 1)
            if outline is not None:
                pygame.draw.polygon(surface, outline, a, max(1, width))
        self._items.clear()


# ── Sky ────────────────────────────────────────────────────────────────────

def icosphere(subdivisions: int = 1):
    """A unit sphere as (vertices, faces), built by subdividing an icosahedron.

    Icosahedral faces are near-equilateral and evenly sized, so cel shading
    bands across them cleanly — a lat/long sphere would bunch triangles at the
    poles and make the band edges visibly wobble as the ball rolls.
    """
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    verts = [normalize(v) for v in verts]

    for _ in range(subdivisions):
        cache: dict = {}
        new_faces = []

        def midpoint(a: int, b: int) -> int:
            key = (min(a, b), max(a, b))
            if key not in cache:
                va, vb = verts[a], verts[b]
                verts.append(normalize(((va[0] + vb[0]) / 2,
                                        (va[1] + vb[1]) / 2,
                                        (va[2] + vb[2]) / 2)))
                cache[key] = len(verts) - 1
            return cache[key]

        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
        faces = new_faces

    return verts, faces


def axis_angle_matrix(axis: Vec3, angle: float):
    """Row-major 3x3 rotation about a unit axis."""
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = 1.0 - c
    return (
        c + x * x * k, x * y * k - z * s, x * z * k + y * s,
        y * x * k + z * s, c + y * y * k, y * z * k - x * s,
        z * x * k - y * s, z * y * k + x * s, c + z * z * k,
    )


def mat_mul(a, b):
    """Row-major 3x3 product ``a @ b``."""
    return tuple(
        a[row * 3 + 0] * b[0 + col] + a[row * 3 + 1] * b[3 + col] + a[row * 3 + 2] * b[6 + col]
        for row in range(3) for col in range(3)
    )


def mat_apply(m, v: Vec3) -> Vec3:
    return (
        m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
        m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
        m[6] * v[0] + m[7] * v[1] + m[8] * v[2],
    )


IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def orthonormalize(m):
    """Re-orthogonalise a rotation matrix that has drifted from repeated
    incremental multiplication."""
    r0 = normalize((m[0], m[1], m[2]))
    r1 = (m[3], m[4], m[5])
    d = dot(r0, r1)
    r1 = normalize((r1[0] - r0[0] * d, r1[1] - r0[1] * d, r1[2] - r0[2] * d))
    r2 = cross(r0, r1)
    return (*r0, *r1, *r2)


def make_sky(width: int, height: int) -> pygame.Surface:
    """Vertical gradient plus a soft sun, baked once."""
    """A vertical gradient, baked once — cyan overhead, warm at the horizon."""
    sky = pygame.Surface((1, height))
    for y in range(height):
        t = y / max(1, height - 1)
        if t < 0.55:
            color = mix(C.SKY_TOP, C.SKY_MID, t / 0.55)
        else:
            color = mix(C.SKY_MID, C.SKY_HORIZON, (t - 0.55) / 0.45)
        sky.set_at((0, y), color)
    out = pygame.transform.smoothscale(sky, (width, height))

    # A soft sun where the light is coming from, so the shading has a source.
    sun = (width * 0.20, height * 0.16)
    for radius, alpha in ((0.30, 26), (0.19, 30), (0.10, 44), (0.045, 90)):
        blob(out, sun, height * radius, (255, 250, 214), alpha)
    return out


def blob(surface: pygame.Surface, center: Tuple[float, float], radius: float,
         color: Sequence[int], alpha: int) -> None:
    """A soft round splat, used for clouds and glows."""
    r = max(1, int(min(radius, MAX_SCREEN_RADIUS)))
    layer = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
    pygame.draw.circle(layer, (*color, alpha), (r, r), r)
    surface.blit(layer, (center[0] - r, center[1] - r))


def cel_sphere(
    surface: pygame.Surface,
    center: Tuple[float, float],
    radius: float,
    base: Sequence[int],
    *,
    squash: Tuple[float, float] = (1.0, 1.0),
    outline: Sequence[int] = C.OUTLINE,
) -> None:
    """Draw a cel-shaded sphere as nested offset ellipses.

    A real shaded sphere mesh would cost dozens of polygons and still band
    imperfectly. Three offset ellipses give a *cleaner* toon sphere than
    geometry does, for a fraction of the cost.
    """
    radius = min(radius, MAX_SCREEN_RADIUS)
    rx = max(1.0, radius * squash[0])
    ry = max(1.0, radius * squash[1])
    cx, cy = center

    def ellipse(color, ox, oy, sx, sy):
        rect = pygame.Rect(0, 0, max(2, int(rx * 2 * sx)), max(2, int(ry * 2 * sy)))
        rect.center = (int(cx + ox), int(cy + oy))
        pygame.draw.ellipse(surface, color, rect)

    shade = C.CEL_BANDS
    ellipse(shift(base, shade[2][1]), 0, 0, 1.0, 1.0)          # core shadow
    ellipse(shift(base, shade[1][1]), -rx * 0.10, -ry * 0.12, 0.92, 0.92)
    ellipse(shift(base, shade[0][1]), -rx * 0.20, -ry * 0.24, 0.70, 0.70)

    # Specular pip — the single brightest thing on the sprite.
    ellipse(shift(base, 1.28), -rx * 0.34, -ry * 0.42, 0.24, 0.20)

    rect = pygame.Rect(0, 0, int(rx * 2), int(ry * 2))
    rect.center = (int(cx), int(cy))
    pygame.draw.ellipse(surface, outline, rect, max(2, int(radius * 0.09)))


def shadow(surface: pygame.Surface, center: Tuple[float, float],
           rx: float, ry: float, alpha: int) -> None:
    rx = clamp(rx, 2.0, MAX_SCREEN_RADIUS)
    ry = clamp(ry, 1.0, MAX_SCREEN_RADIUS)
    # Draw multiple nested layers to create a soft, feathered shadow
    for i in range(3):
        factor = 1.0 + i * 0.18
        cur_rx = rx * factor
        cur_ry = ry * factor
        cur_alpha = int(alpha * (0.5 - i * 0.15))
        if cur_alpha <= 0:
            continue
        layer = pygame.Surface((int(cur_rx * 2), int(cur_ry * 2)), pygame.SRCALPHA)
        pygame.draw.ellipse(layer, (20, 35, 25, cur_alpha), layer.get_rect())
        surface.blit(layer, (center[0] - cur_rx, center[1] - cur_ry))


_vignette: "pygame.Surface | None" = None


def vignette(surface: pygame.Surface) -> None:
    """Darken the corners a little. Cached — it never changes."""
    global _vignette
    if C.VIGNETTE <= 0:
        return
    if _vignette is None or _vignette.get_size() != (C.WIDTH, C.HEIGHT):
        small = 96
        layer = pygame.Surface((small, small), pygame.SRCALPHA)
        cx = cy = (small - 1) / 2.0
        limit = math.hypot(cx, cy)
        for y in range(small):
            for x in range(small):
                t = math.hypot(x - cx, y - cy) / limit
                a = int(C.VIGNETTE * max(0.0, t - 0.45) / 0.55)
                if a:
                    layer.set_at((x, y), (12, 18, 34, min(255, a)))
        _vignette = pygame.transform.smoothscale(layer, (C.WIDTH, C.HEIGHT))
    surface.blit(_vignette, (0, 0))
