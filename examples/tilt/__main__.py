"""TILT — entry point.

    python -m examples.tilt

The watch is the controller: hold your forearm level and the island is level,
tilt and the sprite rolls. The board matches your wrist one to one, so there
is nothing to calibrate.

The library owns connecting, the GFDI handshake, the heartbeat and
reconnection; this module owns the frame loop. Both run on the same event
loop, and the game keeps rendering while a dropped link reconnects rather
than freezing or tearing the run down.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time

import pygame

from garmin_ble import GarminBleError, Watch, metrics
from garmin_ble.logging import configure

from . import config as C
from .game import DYING, Game


async def run_game(game: Game, watch: Watch) -> None:
    # Registering the handler subscribes to the metric, which registers and
    # starts the accelerometer service on the watch. The last unsubscribe —
    # here, leaving the session — stops it again.
    watch.on(metrics.ACCELEROMETER)(game.on_accel)

    previous = time.perf_counter()
    while game.running:
        now = time.perf_counter()
        # Clamp the step: a stall (window drag, GC pause) must not teleport
        # the sprite through a wall of holes.
        dt = min(0.05, now - previous)
        previous = now

        game.handle_events()
        if not game.paused:
            scale = C.DEATH_SLOWMO if game.state == DYING else 1.0
            game.update(dt * scale)
        game.draw()

        # Yield to the event loop so BLE notifications are actually delivered.
        await asyncio.sleep(0)
        game.clock.tick(C.FPS)


async def main(seed: int | None, mute: bool, fullscreen: bool) -> None:
    configure(level=logging.WARNING)
    pygame.init()

    game = Game(seed=seed, audio=not mute, fullscreen=fullscreen)

    try:
        # The session owns connect, handshake, heartbeat and teardown —
        # including when pygame raises or you close the window.
        async with Watch.discover() as watch:
            await run_game(game, watch)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except GarminBleError as exc:
        print(f"\n{exc}")
        print("\nTip: your watch allows one BLE connection at a time — "
              "disconnect it from your phone first.")
        print("Run `python examples/scan.py` to see what is in range.")
    finally:
        pygame.quit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="python -m examples.tilt",
        description="TILT — roll a seed-sprite down an endless tower of "
                    "floating islands, steering with your wrist.",
    )
    parser.add_argument("--seed", type=int, default=None,
                        help="fix the island sequence, for practice or comparison")
    parser.add_argument("--mute", action="store_true", help="disable all audio")
    parser.add_argument("--fullscreen", action="store_true",
                        help="run in fullscreen mode instead of a resizable window")
    args = parser.parse_args()

    asyncio.run(main(args.seed, args.mute, args.fullscreen))
