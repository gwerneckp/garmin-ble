"""Rolling the sprite around a tilted island.

The mapping from wrist to motion needs no trigonometry. If the board's normal
is the watch's own face normal, then the in-plane component of gravity is
exactly the ``(x, y)`` part of the gravity vector the watch reports — so the
accelerometer reading *is* the acceleration, scaled.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Tuple

from . import config as C
from . import world as W

#: Shove a vine head imparts, in cells/s^2 at point-blank range.
VINE_PUSH = 34.0


@dataclass
class Ball:
    """Position is in cell coordinates, so it indexes the grid directly."""

    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    hop: float = 0.0        # height above the board, in world units
    hop_v: float = 0.0
    squash: float = 0.0     # +1 squashed flat, -1 stretched tall

    @property
    def speed(self) -> float:
        return math.hypot(self.vx, self.vy)

    def speed_fraction(self) -> float:
        return min(1.0, self.speed * C.CELL / C.MAX_SPEED)


@dataclass
class StepResult:
    fell: bool = False
    reached_goal: bool = False
    bounced: bool = False
    hit_vine: bool = False
    crumbled: List[Tuple[int, int]] = field(default_factory=list)


def step(ball: Ball, island: W.Island, tilt: Tuple[float, float],
         t: float, dt: float, gravity: float = C.GRAVITY_FULL) -> StepResult:
    """Advance one frame. ``tilt`` is the watch's in-plane gravity, in g."""
    result = StepResult()

    gx, gy = tilt

    # Acceleration in cell-units/s^2.
    ax = gravity * gx / C.CELL
    ay = gravity * gy / C.CELL

    ball.vx += ax * dt
    ball.vy += ay * dt

    damping = max(0.0, 1.0 - C.FRICTION * dt)
    ball.vx *= damping
    ball.vy *= damping

    limit = C.MAX_SPEED / C.CELL
    speed = math.hypot(ball.vx, ball.vy)
    if speed > limit:
        ball.vx *= limit / speed
        ball.vy *= limit / speed

    ball.x += ball.vx * dt
    ball.y += ball.vy * dt

    # Vertical hop, only ever started by a bounce pad.
    if ball.hop > 0.0 or ball.hop_v > 0.0:
        ball.hop_v -= 2600.0 * dt
        ball.hop += ball.hop_v * dt
        if ball.hop <= 0.0:
            ball.hop = 0.0
            ball.hop_v = 0.0
            ball.squash = 0.85

    # Squash and stretch settle back to round.
    ball.squash *= max(0.0, 1.0 - 7.0 * dt)

    for vine in island.vines:
        hx, hy = vine.head(t)
        dx, dy = ball.x - hx, ball.y - hy
        dist = math.hypot(dx, dy)
        if dist < vine.span:
            # A shove away from the vine, strongest at the centre of the head.
            push = (vine.span - dist) / vine.span
            if dist > 1e-4:
                ball.vx += (dx / dist) * push * VINE_PUSH * dt
                ball.vy += (dy / dist) * push * VINE_PUSH * dt
            result.hit_vine = True

    cx, cy = int(math.floor(ball.x + 0.5)), int(math.floor(ball.y + 0.5))
    cell = island.at(cx, cy)

    if ball.hop <= 0.0:
        if cell is None or cell == W.HOLE:
            result.fell = True
        elif cell == W.GOAL:
            result.reached_goal = True
        elif cell == W.CRUMBLE:
            key = (cx, cy)
            if key not in island.fuses:
                island.fuses[key] = 1.5

        for pad in island.bounces:
            if pad.cell == (cx, cy) and ball.hop_v <= 0.0:
                ball.hop_v = 780.0
                ball.squash = -0.7
                result.bounced = True

    # Burn down any lit fuses; a cap that runs out becomes a hole.
    for key in list(island.fuses):
        island.fuses[key] -= dt
        if island.fuses[key] <= 0.0:
            fx, fy = key
            island.cells[fy][fx] = W.HOLE
            del island.fuses[key]
            result.crumbled.append(key)

    return result


def contact_squash(ball: Ball) -> Tuple[float, float]:
    """Squash factors ``(x, y)`` for drawing, derived from motion."""
    s = ball.squash
    lean = min(0.22, ball.speed * 0.04)
    return (1.0 + s * 0.28 + lean, 1.0 - s * 0.30 - lean * 0.6)
