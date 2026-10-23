#!/usr/bin/env python3
"""Pulse Orb: a glowing orb that beats with your heart.

Usage:
    python examples/pulse_orb.py
    python examples/pulse_orb.py --simulate   # no hardware needed

The orb swells on every heartbeat at your live BPM, and its colour drifts from
calm teal to hot orange as the watch's stress score rises. Each beat throws off
a ring of sparks. Two subscriptions, one pygame loop.
"""

import argparse
import asyncio
import logging
import math
import random

import pygame

from garmin_ble import GarminBleError, Watch, metrics
from garmin_ble.logging import configure

CALM = (40, 220, 190)
TENSE = (255, 120, 60)
BG = (10, 12, 18)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class PulseOrb:
    def __init__(self):
        pygame.init()
        self.width, self.height = 1000, 700
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Pulse Orb")
        self.big = pygame.font.SysFont("helvetica", 96, bold=True)
        self.small = pygame.font.SysFont("helvetica", 26, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

        self.bpm = 70
        self.stress = 25
        self.phase = 0.0
        self.sparks = []  # [x, y, vx, vy, life]

    def on_heart_rate(self, reading: metrics.HeartRate) -> None:
        self.bpm = reading.bpm

    def on_stress(self, reading: metrics.Stress) -> None:
        self.stress = reading.level

    def beat(self, color) -> None:
        cx, cy = self.width / 2, self.height / 2 - 20
        for _ in range(40):
            a = random.uniform(0, 2 * math.pi)
            v = random.uniform(250, 600)
            self.sparks.append([cx + math.cos(a) * 120, cy + math.sin(a) * 120, math.cos(a) * v, math.sin(a) * v, 1.0])

    def draw(self, dt: float) -> None:
        self.phase += dt * self.bpm / 60
        if self.phase >= 1:
            self.phase -= 1
            self.beat(self.color)

        # lub-dub: a sharp swell at the beat, a smaller one just after
        pulse = math.exp(-self.phase * 9) + 0.5 * math.exp(-max(0.0, self.phase - 0.18) * 11) * (self.phase > 0.18)
        cx, cy = self.width / 2, self.height / 2 - 20
        radius = 120 + 45 * pulse

        self.screen.fill(BG)
        # soft glow: stacked translucent discs, each blitted so the alpha accumulates
        for i in range(10, 0, -1):
            r = int(radius + i * 16)
            disc = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
            pygame.draw.circle(disc, (*self.color, int(8 + 5 * pulse)), (r, r), r)
            self.screen.blit(disc, (cx - r, cy - r))
        # shockwave ring expanding from the last beat
        ring = 120 + self.phase * 380
        ring_alpha = max(0.0, 1 - self.phase * 1.6)
        if ring_alpha > 0:
            pygame.draw.circle(self.screen, mix(BG, self.color, ring_alpha), (cx, cy), ring, 4)
        pygame.draw.circle(self.screen, self.color, (cx, cy), radius)
        pygame.draw.circle(self.screen, mix(self.color, (255, 255, 255), 0.35), (cx, cy), radius * 0.82, 6)

        for s in self.sparks:
            s[0] += s[2] * dt
            s[1] += s[3] * dt
            s[2] *= 1 - dt * 1.5
            s[3] *= 1 - dt * 1.5
            s[4] -= dt * 1.1
            if s[4] > 0:
                pygame.draw.circle(self.screen, mix(BG, self.color, s[4]), (s[0], s[1]), 7 * s[4] + 1)
        self.sparks = [s for s in self.sparks if s[4] > 0]

        text = self.big.render(f"{self.bpm}", True, (255, 255, 255))
        self.screen.blit(text, text.get_rect(center=(cx, cy)))
        label = self.small.render(f"BPM   ·   stress {self.stress}", True, mix(self.color, (255, 255, 255), 0.4))
        self.screen.blit(label, label.get_rect(center=(cx, self.height - 70)))
        pygame.display.flip()

    @property
    def color(self):
        return mix(CALM, TENSE, min(1.0, self.stress / 80))

    async def run(self, watch: Watch) -> None:
        watch.on(metrics.HEART_RATE)(self.on_heart_rate)
        watch.on(metrics.STRESS)(self.on_stress)

        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                    self.running = False
            dt = self.clock.tick(60) / 1000
            self.draw(dt)
            await asyncio.sleep(0)


async def main(simulate: bool) -> None:
    configure(level=logging.WARNING)
    app = PulseOrb()

    session = Watch.simulated() if simulate else Watch.discover()
    try:
        async with session as watch:
            await app.run(watch)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except GarminBleError as exc:
        print(f"\n{exc}")
    finally:
        pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A glowing orb that beats with your heart.")
    parser.add_argument("--simulate", action="store_true", help="use an in-process watch")
    args = parser.parse_args()
    asyncio.run(main(args.simulate))
