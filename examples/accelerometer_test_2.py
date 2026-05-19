#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accelerometer demo — tilt-controlled system volume.

Calibrates to your watch's specific orientation baselines, then
lets you raise/lower system volume by tilting left or right.

Usage:
  python3 examples/accelerometer.py              # load saved .calibration (or run calibration)
  python3 examples/accelerometer.py --calibrate   # force recalibration
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure

# ── main ─────────────────────────────────────────────────────────────────


def on_accel(packet):
    # take the average of the 3 samples in this packet
    x_avg = sum(sample[0] for sample in packet["samples"]) / len(packet
        ["samples"])
    y_avg = sum(sample[1] for sample in packet["samples"]) / len(packet
        ["samples"])
    z_avg = sum(sample[2] for sample in packet["samples"]) / len(packet
        ["samples"])

    # check delta timestamp and just print if mod 2000 is < 100 (i.e. every ~2000ms)
    # only print when any axis exceeds a threshold (0.2g)
    if packet["timestamp_ms"] % 2000 < 100:
        # convert to g
        x_g = x_avg / 1024
        y_g = y_avg / 1024
        z_g = z_avg / 1024
        thresh = 0.2

        # only show when any axis magnitude exceeds threshold
        if not (abs(x_g) > thresh or abs(y_g) > thresh or abs(z_g) > thresh):
            return

        def color_for(v):
            if v > thresh:
                return "\033[92m"
            if v < -thresh:
                return "\033[91m"
            return "\033[90m"  # dim/grey for small values

        x_color = color_for(x_g)
        y_color = color_for(y_g)
        z_color = color_for(z_g)
        print(f"⌚ Accel @ {packet['timestamp_ms']}ms: "
              f"({x_color}{x_g:+.3f}g\033[0m, {y_color}{y_g:+.3f}g\033[0m, {z_color}{z_g:+.3f}g\033[0m)")
    

async def main():
    configure(level=logging.INFO)

    client = GarminClient()
    await client.connect()
    await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
    await asyncio.sleep(0.5)
    client.on("accel", on_accel)
    await client.start_sync_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
