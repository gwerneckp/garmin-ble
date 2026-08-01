"""Bubbles, sparkles, pollen and dust.

One flat list of particles with a kind tag, drawn in screen space. Nothing
here is simulated in 3D — the depth is faked with size and drift speed, which
is both cheaper and easier to art-direct than real perspective would be.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import pygame

from . import config as C
from . import render as R

#: Reusable local scratch surface for fast transparent particle drawing.
_local_scratch: pygame.Surface | None = None


def _get_local_scratch(w: int, h: int) -> pygame.Surface:
    global _local_scratch
    size = max(w, h)
    if _local_scratch is None or _local_scratch.get_width() < size:
        _local_scratch = pygame.Surface((size, size), pygame.SRCALPHA)
    else:
        _local_scratch.fill((0, 0, 0, 0))
    return _local_scratch

BUBBLE = 0
SPARKLE = 1
POLLEN = 2
DUST = 3
DROPLET = 4
CONFETTI = 5


@dataclass
class Particle:
    kind: int
    x: float
    y: float
    vx: float
    vy: float
    life: float
    max_life: float
    size: float
    color: Tuple[int, int, int]
    wobble: float = 0.0


class Particles:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.items: List[Particle] = []
        self._ambient_timer = 0.0

    # ── Emitters ───────────────────────────────────────────────────────────

    def ambient(self, dt: float, intensity: float = 1.0) -> None:
        """The constant drift of bubbles and pollen rising past the camera."""
        self._ambient_timer -= dt
        if self._ambient_timer > 0.0:
            return
        self._ambient_timer = self.rng.uniform(0.05, 0.16) / max(0.2, intensity)

        rng = self.rng
        if rng.random() < 0.55:
            self.items.append(Particle(
                kind=BUBBLE,
                x=rng.uniform(-40, C.WIDTH + 40),
                y=C.HEIGHT + rng.uniform(10, 90),
                vx=rng.uniform(-14, 14),
                vy=-rng.uniform(26, 74),
                life=rng.uniform(6.0, 11.0),
                max_life=11.0,
                size=rng.uniform(5, 17),
                color=(255, 255, 255),
                wobble=rng.uniform(0, math.tau),
            ))
        else:
            self.items.append(Particle(
                kind=POLLEN,
                x=rng.uniform(-40, C.WIDTH + 40),
                y=C.HEIGHT + rng.uniform(10, 120),
                vx=rng.uniform(-22, 22),
                vy=-rng.uniform(14, 44),
                life=rng.uniform(7.0, 13.0),
                max_life=13.0,
                size=rng.uniform(2, 5),
                color=(255, 248, 198),
                wobble=rng.uniform(0, math.tau),
            ))

    def burst(self, pos: Tuple[float, float], count: int, kind: int = SPARKLE,
              color: Sequence[int] = (255, 236, 150), speed: float = 220.0) -> None:
        for _ in range(count):
            a = self.rng.uniform(0, math.tau)
            s = self.rng.uniform(0.35, 1.0) * speed
            
            # Select random bright colors for confetti
            p_color = color
            p_size = self.rng.uniform(3, 8)
            p_life = self.rng.uniform(0.4, 0.95)
            if kind == CONFETTI:
                p_color = self.rng.choice([
                    (255, 90, 130),   # Pink
                    (90, 210, 255),   # Cyan
                    (255, 230, 90),   # Yellow
                    (180, 100, 255),  # Purple
                    (255, 140, 60),   # Orange
                ])
                p_size = self.rng.uniform(6, 12)
                p_life = self.rng.uniform(1.2, 2.5) # Confetti floats longer

            self.items.append(Particle(
                kind=kind,
                x=pos[0], y=pos[1],
                vx=math.cos(a) * s,
                vy=math.sin(a) * s - (speed * 0.4 if kind == CONFETTI else speed * 0.25),
                life=p_life,
                max_life=p_life,
                size=p_size,
                color=tuple(p_color),
                wobble=self.rng.uniform(0, math.tau),
            ))

    def trail(self, pos: Tuple[float, float], amount: float) -> None:
        """Dust kicked up under the sprite, scaled by how fast it is rolling."""
        if self.rng.random() > amount:
            return
        self.items.append(Particle(
            kind=DUST,
            x=pos[0] + self.rng.uniform(-8, 8),
            y=pos[1] + self.rng.uniform(-4, 4),
            vx=self.rng.uniform(-30, 30),
            vy=-self.rng.uniform(10, 45),
            life=self.rng.uniform(0.3, 0.6),
            max_life=0.6,
            size=self.rng.uniform(4, 9),
            color=(226, 244, 200),
            wobble=0.0,
        ))

    def pop(self, pos: Tuple[float, float]) -> None:
        self.burst(pos, 16, DROPLET, (198, 240, 255), 260.0)
        self.burst(pos, 10, SPARKLE, (255, 246, 190), 190.0)

    # ── Simulation ─────────────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        alive: List[Particle] = []
        for p in self.items:
            p.life -= dt
            if p.life <= 0.0:
                continue
            # Wobble is spin speed for confetti
            p.wobble += dt * (5.0 if p.kind == CONFETTI else 2.4)
            p.x += (p.vx + math.sin(p.wobble) * (20.0 if p.kind == CONFETTI else 14.0)) * dt
            p.y += p.vy * dt
            if p.kind in (SPARKLE, DUST, DROPLET, CONFETTI):
                gravity_acc = 160.0 if p.kind == CONFETTI else 420.0
                p.vy += gravity_acc * dt      # these fall back down
                decay_rate = 1.2 if p.kind == CONFETTI else 2.2
                p.vx *= max(0.0, 1.0 - decay_rate * dt)
            if p.y < -120 or p.x < -160 or p.x > C.WIDTH + 160:
                continue
            alive.append(p)
        self.items = alive

    def draw(self, surface: pygame.Surface) -> None:
        if not self.items:
            return
        ui = C.UI
        for p in self.items:
            fade = R.clamp(p.life / p.max_life, 0.0, 1.0)
            pos = (int(p.x), int(p.y))

            if p.kind == BUBBLE:
                alpha = int(120 * min(1.0, fade * 2.2))
                r = max(2, int(p.size * ui))
                temp = _get_local_scratch(r * 2, r * 2)
                pygame.draw.circle(temp, (*p.color, alpha), (r, r), r, max(1, r // 5))
                pygame.draw.circle(
                    temp, (255, 255, 255, min(255, alpha + 90)),
                    (r - r // 3, r - r // 3), max(1, r // 4),
                )
                surface.blit(temp, (pos[0] - r, pos[1] - r))
            elif p.kind == POLLEN:
                alpha = int(190 * min(1.0, fade * 2.0))
                r = max(1, int(p.size * ui))
                temp = _get_local_scratch(r * 2, r * 2)
                pygame.draw.circle(temp, (*p.color, alpha), (r, r), r)
                surface.blit(temp, (pos[0] - r, pos[1] - r))
            elif p.kind == SPARKLE:
                alpha = int(255 * fade)
                s = p.size * fade * ui
                w = int(s * 1.2) + 2
                h = int(s * 3.6) + 2
                temp = _get_local_scratch(w, h)
                pygame.draw.polygon(temp, (*p.color, alpha), [
                    (w // 2, 0), (w, h // 2),
                    (w // 2, h), (0, h // 2),
                ])
                surface.blit(temp, (pos[0] - w // 2, pos[1] - h // 2))
            elif p.kind == DROPLET:
                alpha = int(210 * fade)
                r = max(1, int(p.size * fade * ui))
                temp = _get_local_scratch(r * 2, r * 2)
                pygame.draw.circle(temp, (*p.color, alpha), (r, r), r)
                surface.blit(temp, (pos[0] - r, pos[1] - r))
            elif p.kind == DUST:
                alpha = int(150 * fade)
                r = max(1, int(p.size * fade * ui))
                temp = _get_local_scratch(r * 2, r * 2)
                pygame.draw.circle(temp, (*p.color, alpha), (r, r), r)
                surface.blit(temp, (pos[0] - r, pos[1] - r))
            else: # CONFETTI
                alpha = int(255 * fade)
                s = p.size * fade * ui
                w = int(s * 1.5)
                h = int(s * 0.7)
                temp = _get_local_scratch(w * 2, h * 2)
                # Draw local rectangle
                rect = pygame.Rect(w // 2, h // 2, w, h)
                pygame.draw.rect(temp, (*p.color, alpha), rect)
                # Rotate local rectangle
                rotated = pygame.transform.rotate(temp, math.degrees(p.wobble * 1.5))
                surface.blit(rotated, (pos[0] - rotated.get_width() // 2, pos[1] - rotated.get_height() // 2))


class Clouds:
    """Parallax cloud banks under the islands.

    Each bank is rendered once into its own small surface and then only ever
    blitted. Their shape never changes, so redrawing the puffs every frame —
    and compositing them through a full-screen alpha layer — was paying a
    megabyte of memory traffic for a picture that was already decided.
    """

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.banks = []
        for _ in range(14):
            self.banks.append({
                "x": rng.uniform(-200, C.WIDTH + 200),
                "y": rng.uniform(C.HEIGHT * 0.42, C.HEIGHT * 1.05),
                "scale": rng.uniform(0.5, 1.5),
                "speed": rng.uniform(4.0, 16.0),
                "puffs": [(rng.uniform(-1, 1), rng.uniform(-0.35, 0.35), rng.uniform(0.45, 1.0))
                          for _ in range(rng.randrange(4, 7))],
                "surface": None,
                "built_ui": None,
            })

    def _build(self, bank) -> pygame.Surface:
        base = 74.0 * bank["scale"] * C.UI
        alpha = min(235, int(150 + 60 * bank["scale"]))
        half_w = int(base * 2.6)
        half_h = int(base * 1.6)
        surf = pygame.Surface((half_w * 2, half_h * 2), pygame.SRCALPHA)
        for ox, oy, r in bank["puffs"]:
            pygame.draw.circle(
                surf, (*C.CLOUD, alpha),
                (int(half_w + ox * base * 1.25), int(half_h + oy * base)),
                max(4, int(r * base)),
            )
        bank["surface"] = surf
        bank["built_ui"] = C.UI
        return surf

    def update(self, dt: float, scroll: float = 0.0) -> None:
        for b in self.banks:
            b["x"] += b["speed"] * dt
            b["y"] += scroll * b["scale"] * dt
            if b["x"] > C.WIDTH + 260:
                b["x"] = -260
            if b["y"] > C.HEIGHT + 200:
                b["y"] = C.HEIGHT * 0.4
                b["x"] = self.rng.uniform(-200, C.WIDTH + 200)

    def draw(self, surface: pygame.Surface) -> None:
        for b in self.banks:
            surf = b["surface"]
            if surf is None or b["built_ui"] != C.UI:
                surf = self._build(b)
            surface.blit(surf, (int(b["x"]) - surf.get_width() // 2,
                                int(b["y"]) - surf.get_height() // 2))
