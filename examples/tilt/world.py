"""Island generation.

Every island is guaranteed solvable: a path is carved from the spawn to the
goal *first*, and holes are only ever punched into cells that path never
touches. Difficulty comes from how much of the rest of the board disappears
and which hazards have unlocked — never from a board you cannot cross.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import config as C

FLOOR = 0
HOLE = 1
GOAL = 2
CRUMBLE = 3

Cell = int


@dataclass
class Vine:
    """A flowering vine that sweeps the board and shoves the sprite aside.

    It pushes rather than kills: being knocked *towards* a hole is a fairer
    and funnier death than being deleted on contact.
    """

    axis: int          # 0 = sweeps along x, 1 = sweeps along y
    lane: float        # cell coordinate of the lane it travels down
    phase: float
    speed: float
    span: float

    def position(self, t: float) -> float:
        """Cell coordinate of the vine head along its axis of travel."""
        travel = math.sin(t * self.speed + self.phase)
        return (C.GRID - 1) * 0.5 + travel * (C.GRID - 1) * 0.5

    def head(self, t: float) -> Tuple[float, float]:
        p = self.position(t)
        return (p, self.lane) if self.axis == 0 else (self.lane, p)


@dataclass
class Bounce:
    cell: Tuple[int, int]
    phase: float = 0.0


@dataclass
class Island:
    depth: int
    cells: List[List[Cell]]
    spawn: Tuple[float, float]
    goal: Tuple[int, int]
    vines: List[Vine] = field(default_factory=list)
    bounces: List[Bounce] = field(default_factory=list)
    #: Crumbling cells that have been touched -> seconds of life remaining.
    fuses: dict = field(default_factory=dict)
    decor: List[Tuple[float, float, float, int]] = field(default_factory=list)

    def at(self, cx: int, cy: int) -> Optional[Cell]:
        if 0 <= cx < C.GRID and 0 <= cy < C.GRID:
            return self.cells[cy][cx]
        return None

    def is_solid(self, cx: int, cy: int) -> bool:
        cell = self.at(cx, cy)
        return cell is not None and cell != HOLE


def _carve_path(rng: random.Random, start: Tuple[int, int],
                goal: Tuple[int, int]) -> List[Tuple[int, int]]:
    """A wandering but always-progressing route from start to goal."""
    path = [start]
    x, y = start
    gx, gy = goal
    for _ in range(C.GRID * 8):
        if (x, y) == goal:
            break
        options = []
        if x < gx:
            options.append((1, 0))
        elif x > gx:
            options.append((-1, 0))
        if y < gy:
            options.append((0, 1))
        elif y > gy:
            options.append((0, -1))
        if not options:
            break
        # A sideways wobble makes routes feel authored rather than diagonal.
        if rng.random() < 0.28:
            options.append(rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)]))
        dx, dy = rng.choice(options)
        x = max(0, min(C.GRID - 1, x + dx))
        y = max(0, min(C.GRID - 1, y + dy))
        path.append((x, y))

    # If the wobble stalled us, walk the rest of the way in a straight line.
    # Appending the goal directly would leave a gap in the protected route,
    # and holes punched into that gap could make the island uncrossable.
    while (x, y) != goal:
        if x != gx:
            x += 1 if gx > x else -1
        elif y != gy:
            y += 1 if gy > y else -1
        path.append((x, y))
    return path


def _hole_density(depth: int) -> float:
    t = min(1.0, max(0, depth - 1) / C.HOLES_RAMP)
    return C.HOLES_START + (C.HOLES_END - C.HOLES_START) * t


def generate(depth: int, rng: random.Random) -> Island:
    """Build island number ``depth`` (1-based)."""
    g = C.GRID
    cells = [[FLOOR] * g for _ in range(g)]

    edge = rng.randrange(g)
    spawn_cell = (edge, 0)
    goal_cell = (rng.randrange(g), g - 1)

    path = _carve_path(rng, spawn_cell, goal_cell)
    protected = set(path)
    # Keep the cells orthogonally adjacent to the spawn solid too, so a fast
    # entry cannot clip a hole before the player has seen the board.
    protected.update({(spawn_cell[0] + dx, spawn_cell[1] + dy)
                      for dx in (-1, 0, 1) for dy in (-1, 0, 1)})

    density = _hole_density(depth)
    for y in range(g):
        for x in range(g):
            if (x, y) in protected:
                continue
            if rng.random() < density:
                cells[y][x] = HOLE

    if depth >= C.HAZARD_CRUMBLE_FROM:
        # Crumbling caps sit *on* the route, so they must be crossed.
        candidates = [c for c in path[2:-2] if rng.random() < 0.22]
        for cx, cy in candidates[:3]:
            cells[cy][cx] = CRUMBLE

    cells[goal_cell[1]][goal_cell[0]] = GOAL

    island = Island(
        depth=depth,
        cells=cells,
        spawn=(spawn_cell[0], spawn_cell[1]),
        goal=goal_cell,
    )

    if depth >= C.HAZARD_VINE_FROM:
        count = 1 + (depth >= C.HAZARD_VINE_FROM + 6)
        for _ in range(count):
            axis = rng.randrange(2)
            lane = rng.uniform(2.0, g - 3.0)
            island.vines.append(Vine(
                axis=axis,
                lane=lane,
                phase=rng.uniform(0.0, math.tau),
                speed=rng.uniform(0.7, 1.15) + depth * 0.012,
                span=rng.uniform(1.4, 2.1),
            ))

    if depth >= C.HAZARD_BOUNCE_FROM:
        for _ in range(rng.randrange(1, 3)):
            bx, by = rng.randrange(g), rng.randrange(g)
            if cells[by][bx] == FLOOR:
                island.bounces.append(Bounce(cell=(bx, by), phase=rng.uniform(0, 6.0)))

    # Decorative grass tufts, biased towards the island's edges where they
    # silhouette against the sky.
    for _ in range(26):
        fx, fy = rng.uniform(0, g), rng.uniform(0, g)
        cx, cy = int(fx), int(fy)
        if 0 <= cx < g and 0 <= cy < g and cells[cy][cx] == FLOOR:
            island.decor.append((fx, fy, rng.uniform(0.55, 1.0), rng.randrange(3)))

    return island
