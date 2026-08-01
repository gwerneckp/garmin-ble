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


async def discover_and_connect_watch(game: Game) -> None:
    async with Watch.discover() as watch:
        watch.on(metrics.ACCELEROMETER)(game.on_accel)
        while game.running:
            await asyncio.sleep(0.5)


async def run_game(game: Game, watch_task: asyncio.Task) -> None:
    previous = time.perf_counter()
    while game.running:
        # If watch discovery failed, propagate the exception to main
        if watch_task.done() and watch_task.exception() is not None:
            raise watch_task.exception()

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

    # Start watch discovery concurrently in the background so the game window
    # opens immediately and displays animations instead of freezing.
    watch_task = asyncio.create_task(discover_and_connect_watch(game))

    try:
        await run_game(game, watch_task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    except GarminBleError as exc:
        print(f"\n{exc}")
        print("\nTip: your watch allows one BLE connection at a time — "
              "disconnect it from your phone first.")
        print("Run `python examples/scan.py` to see what is in range.")
    finally:
        watch_task.cancel()
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
