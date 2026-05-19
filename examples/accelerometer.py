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

_CALIB_FILE = os.path.join(os.path.dirname(__file__), ".calibration")
_CALIBRATION_DURATION_SECONDS = 5
_CALIBRATION_PHASES = [
    ("level", "Place your watch flat on a level surface (screen up)."),
    ("left", "Tilt your watch to the LEFT (volume down position)."),
    ("right", "Tilt your watch to the RIGHT (volume up position)."),
]


# ── helpers ──────────────────────────────────────────────────────────────

def convert_raw_to_g_force(raw_value: int) -> float:
    """Convert raw 12-bit signed accelerometer count to G-force."""
    return raw_value / 1024


# ── calibration data ───────────────────────────────────────────────────

class AccelerometerCalibration:
    """Holds calibrated X-axis baselines for left/right tilt."""

    def __init__(self, level_x_baseline: float, right_x_limit: float, left_x_limit: float):
        self.level_x = level_x_baseline
        self.right_x = right_x_limit
        self.left_x = left_x_limit

    @property
    def tilt_threshold(self) -> float:
        """Minimum X deviation (in G) to register as intentional tilt."""
        diff_left = abs(self.left_x - self.level_x)
        diff_right = abs(self.right_x - self.level_x)
        return max(0.1, min(diff_left, diff_right) * 0.6)

    @classmethod
    def calculate_from_samples(cls, samples: dict) -> "AccelerometerCalibration":
        """Factory: compute means from recorded samples."""
        def _calculate_mean(values):
            return sum(values) / len(values) if values else 0.0

        return cls(
            level_x_baseline=_calculate_mean(samples["level"][0]),
            right_x_limit=_calculate_mean(samples["right"][0]),
            left_x_limit=_calculate_mean(samples["left"][0]),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "AccelerometerCalibration":
        return cls(data["level_x"], data["right_x"], data["left_x"])

    def to_dict(self) -> dict:
        return {"level_x": self.level_x, "right_x": self.right_x, "left_x": self.left_x}


# ── terminal helpers ─────────────────────────────────────────────────────

TERM_CLEAR = "\033[H\033[J"
TERM_HIDE_CURSOR = "\033[?25l"
TERM_SHOW_CURSOR = "\033[?25h"
TERM_RESET = "\033[0m"
TERM_BOLD = "\033[1m"
TERM_DIM = "\033[2m"
COLOR_GREEN = "\033[38;5;46m"
COLOR_YELLOW = "\033[38;5;226m"
COLOR_RED = "\033[31m"
COLOR_BLUE = "\033[34m"
COLOR_CYAN = "\033[36m"
COLOR_BRIGHT_GREEN = "\033[92m"
COLOR_BRIGHT_RED = "\033[91m"
COLOR_VOLUME_BAR = "\033[38;5;86m"  # Cyan-ish

def get_current_system_volume() -> int:
    """Return current system output volume (0–100)."""
    try:
        process_output = subprocess.run(
            ["osascript", "-e", "get volume settings"],
            capture_output=True, text=True
        ).stdout
        match = re.search(r'output volume:(\d+)', process_output)
        return int(match.group(1)) if match else 50
    except Exception:
        return 50


def generate_live_status_display(x_tilt_g: float, volume_percent: int, calibration: AccelerometerCalibration):
    """Generate the UI seen in the screenshot."""
    # Logic for boundaries
    left_range = calibration.level_x - calibration.left_x
    right_range = calibration.right_x - calibration.level_x
    left_dead_zone = calibration.level_x - left_range * 0.5
    right_dead_zone = calibration.level_x + right_range * 0.5
    threshold = (left_range + right_range) / 4 # Rough approx of 'thresh'

    # Determine direction string
    direction_str = "CENTER"
    dir_color = TERM_DIM
    if x_tilt_g < left_dead_zone:
        direction_str = "LEFT"
        dir_color = COLOR_BRIGHT_RED
    elif x_tilt_g > right_dead_zone:
        direction_str = "RIGHT"
        dir_color = COLOR_BRIGHT_GREEN

    # Build buffer
    buffer = TERM_CLEAR
    buffer += TERM_BOLD + "  TILT" + TERM_RESET + "\n"

    # Tilt bar construction
    # We want a bar like: L  [red wedge] | [green wedge]  R
    bar_width = 40
    center_idx = bar_width // 2
    
    # Scale x_tilt_g to fit bar. Let's assume range is -1 to 1 for the visual
    cursor_offset = int(x_tilt_g * (bar_width / 2))
    cursor_idx = center_idx + cursor_offset
    cursor_idx = max(0, min(bar_width - 1, cursor_idx))

    tilt_bar_cells = []
    for i in range(bar_width):
        if i == cursor_idx:
            tilt_bar_cells.append(TERM_BOLD + " \u25c6 " + TERM_RESET)
        elif i == center_idx:
            tilt_bar_cells.append(TERM_DIM + "|" + TERM_RESET)
        elif i < center_idx:
            # Red wedge: taller/thicker towards the left
            if i >= cursor_idx:
                 tilt_bar_cells.append(COLOR_RED + "\u2584" + TERM_RESET) # lower block
            else:
                 tilt_bar_cells.append(" ")
        else:
            # Green wedge: taller/thicker towards the right
            if i <= cursor_idx:
                tilt_bar_cells.append(COLOR_GREEN + "\u2580" + TERM_RESET) # upper block
            else:
                tilt_bar_cells.append(" ")

    # The screenshot shows a very specific "wedge" look. 
    # Let's try to approximate the bar from the image.
    # L [marker] [red bar] | [green bar]
    
    buf_tilt = [" "] * bar_width
    # Left side (red)
    for i in range(center_idx):
        if x_tilt_g < 0 and i >= cursor_idx:
            buf_tilt[i] = COLOR_RED + "\u2588" + TERM_RESET
        else:
            buf_tilt[i] = " "
    
    # Right side (green)
    for i in range(center_idx + 1, bar_width):
        if x_tilt_g > 0 and i <= cursor_idx:
            buf_tilt[i] = COLOR_GREEN + "\u2588" + TERM_RESET
        else:
            buf_tilt[i] = " "
    
    buf_tilt[center_idx] = TERM_DIM + "|" + TERM_RESET
    if 0 <= cursor_idx < bar_width:
        buf_tilt[cursor_idx] = TERM_BOLD + "\u25c6" + TERM_RESET

    buffer += "  L " + "".join(buf_tilt) + "\n"
    
    # Info line
    buffer += TERM_DIM + f"  tilt={x_tilt_g:+.2f}  dir=" + TERM_RESET + dir_color + f"{direction_str}" + TERM_RESET + TERM_DIM + f"  thresh={threshold:.2f}" + TERM_RESET + "\n\n"

    # X-AXIS
    buffer += TERM_BOLD + f"  X-AXIS  {x_tilt_g:+.4f}g" + TERM_RESET + "\n\n"

    # VOLUME
    buffer += TERM_BOLD + f"  VOLUME  {volume_percent}%" + TERM_RESET + "\n"
    vol_bar_width = 25
    filled_vol = int(volume_percent / 100 * vol_bar_width)
    vol_bar = COLOR_VOLUME_BAR + "\u2588" * filled_vol + TERM_RESET + TERM_DIM + "\u2591" * (vol_bar_width - filled_vol) + TERM_RESET
    buffer += "  " + vol_bar + "\n\n"

    # Footer
    buffer += "     \u25c0\u2500 VOL \u2500\u25b6\n"
    buffer += "  " + "\u2500" * 45 + "\n"
    buffer += "  " + TERM_BOLD + "Ctrl+C to stop" + TERM_RESET + "\n"

    return buffer

def generate_calibration_bubble(x_tilt_g: float, y_tilt_g: float, terminal_columns: int) -> str:
    """Compact one-line bubble showing current tilt."""
    bubble_width = min(terminal_columns - 4, 24)
    center_x = bubble_width // 2
    offset_x = max(-center_x, min(center_x, int(x_tilt_g * 40)))
    dot_position = center_x + offset_x
    cells = []
    for i in range(bubble_width):
        if i == dot_position:
            cells.append(TERM_BOLD + "\u25cf" + TERM_RESET)
        elif i == center_x:
            cells.append(TERM_DIM + "\u2502" + TERM_RESET)
        else:
            cells.append(" ")
    return "".join(cells)


def generate_calibration_display(phase_name: str, instruction: str, seconds_remaining: int,
                                 x_tilt_g: float, y_tilt_g: float, terminal_columns: int) -> str:
    """Full-screen calibration frame with countdown and live tilt bubble."""
    buffer = TERM_CLEAR
    buffer += COLOR_CYAN + TERM_BOLD + f"  \u25b6 {phase_name.upper()}" + TERM_RESET + "\n"
    buffer += f"    {TERM_DIM}{instruction}{TERM_RESET}\n\n"

    progress_bar_width = min(terminal_columns - 2, 40)
    completion_ratio = seconds_remaining / _CALIBRATION_DURATION_SECONDS
    filled_length = int(progress_bar_width * (1 - completion_ratio))
    progress_bar = COLOR_GREEN + "\u2588" * filled_length + TERM_DIM + "\u2591" * (progress_bar_width - filled_length) + TERM_RESET

    buffer += f"    {progress_bar}  {TERM_BOLD}{seconds_remaining}s{TERM_RESET}\n\n"
    buffer += TERM_BOLD + "    LIVE TILT" + TERM_RESET + f"  x={x_tilt_g:+.2f}g  y={y_tilt_g:+.2f}g\n"
    buffer += f"    {generate_calibration_bubble(x_tilt_g, y_tilt_g, terminal_columns)}\n\n"

    # Direction-specific hints
    if phase_name == "left":
        buffer += COLOR_YELLOW + f"    \u2190  tilt LEFT   (x < {x_tilt_g:.2f})" + TERM_RESET + "\n"
    elif phase_name == "right":
        buffer += COLOR_YELLOW + f"    \u2192  tilt RIGHT  (x > {x_tilt_g:.2f})" + TERM_RESET + "\n"

    return buffer


async def execute_calibration_sequence(client: GarminClient) -> dict:
    """Guided 3-pose calibration sequence with live countdown."""
    calibration_samples: dict = {}
    current_phase: str | None = None
    last_g_force_x = 0.0
    last_g_force_y = 0.0

    def accel_data_receiver(packet):
        nonlocal current_phase, last_g_force_x, last_g_force_y
        if current_phase is None:
            return

        # Samples are [x, y, z] triples. We take the second sample for stability.
        raw_x, raw_y, _ = packet["samples"][1]
        g_force_x, g_force_y = convert_raw_to_g_force(raw_x), convert_raw_to_g_force(raw_y)

        last_g_force_x, last_g_force_y = g_force_x, g_force_y
        x_samples_list, _ = calibration_samples[current_phase]
        x_samples_list.append(g_force_x)

    client.on("accel", accel_data_receiver)
    await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
    await asyncio.sleep(0.5)

    for phase_name, instruction in _CALIBRATION_PHASES:
        calibration_samples[phase_name] = ([], [])

        # Display prompt and wait for user confirmation
        buffer = TERM_CLEAR
        buffer += COLOR_CYAN + TERM_BOLD + f"  \u25b6 {phase_name.upper()}" + TERM_RESET + "\n"
        buffer += f"    {TERM_DIM}{instruction}{TERM_RESET}\n\n"
        buffer += f"    {TERM_BOLD}Press Enter{_RESET} to start recording  (\u00d7{_CALIBRATION_DURATION_SECONDS}s)"
        print(buffer, end="", flush=True)
        await wait_for_user_input()

        # Record samples while showing a live countdown
        current_phase = phase_name
        for seconds_left in range(_CALIBRATION_DURATION_SECONDS, 0, -1):
            cols, _ = shutil.get_terminal_size((80, 24))
            print(generate_calibration_display(phase_name, instruction, seconds_left, last_g_force_x, last_g_force_y, cols),
                  end="", flush=True)
            await asyncio.sleep(1)

        current_phase = None
        print(COLOR_GREEN + f"  \u2713 {phase_name} done" + TERM_RESET + "\n")

    client.on("accel", None)
    return calibration_samples


async def wait_for_user_input():
    """Read a line from stdin without blocking the event loop."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, input)


# ── main ─────────────────────────────────────────────────────────────────

async def main(force_recalibration: bool = False):
    configure(level=logging.INFO)

    calibration = None
    is_calibration_required = force_recalibration

    # ── load or run calibration ──
    if not force_recalibration and os.path.exists(_CALIB_FILE):
        try:
            with open(_CALIB_FILE) as calibration_file:
                calibration_data = json.load(calibration_file)
            calibration = AccelerometerCalibration.from_dict(calibration_data)
            print(f"  \u2192 Loaded saved calibration ({_CALIB_FILE})")
            is_calibration_required = False
        except (KeyError, TypeError, ValueError):
            is_calibration_required = True

    print("  \u2192 Initialising BLE client \u2026", flush=True)
    client = GarminClient()
    print(TERM_HIDE_CURSOR, end="", flush=True)

    try:
        print("  \u2192 Scanning for Garmin watch \u2026", flush=True)
        if not await client.connect():
            print(TERM_SHOW_CURSOR + "Failed to connect.")
            return

        print("  \u2192 Connected.", flush=True)

        if is_calibration_required:
            print("  \u2192 Running calibration \u2026\n")
            raw_samples = await execute_calibration_sequence(client)
            calibration = AccelerometerCalibration.calculate_from_samples(raw_samples)
            with open(_CALIB_FILE, "w") as calibration_file:
                json.dump(calibration.to_dict(), calibration_file, indent=2)
            print(f"  \u2713 Calibration saved to {_CALIB_FILE}\n")
        else:
            await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
            await asyncio.sleep(0.5)

        print(TERM_CLEAR)
        header = TERM_BOLD + "  \u2713 Calibrated" + TERM_RESET + "\n\n"
        header += f"    level X   = {calibration.level_x:+.3f}g\n"
        header += f"    right X   = {calibration.right_x:+.3f}g\n"
        header += f"    left X    = {calibration.left_x:+.3f}g\n"
        print(header)

        print("  " + TERM_DIM + "\u2500" * 50 + TERM_RESET)
        print(f"  {TERM_BOLD}Entering live view{TERM_RESET} \u2026\n")

        # ── live loop state ──
        last_render_time = 0.0
        MIN_RENDER_INTERVAL = 1 / 15

        last_volume_adjustment_time = 0.0
        smoothed_x_tilt = 0.0        # Exponential Moving Average filter state
        SMOOTHING_ALPHA = 0.35       # lower = smoother but laggier

        def adjust_volume_based_on_tilt(x_tilt_value: float):
            """Step system volume by fixed amount when past the dead zone."""
            nonlocal last_volume_adjustment_time
            now = time.monotonic()
            if now - last_volume_adjustment_time < 0.08:
                return
            last_volume_adjustment_time = now

            # Define the 'dead zone' as the inner 50% of each tilt direction
            left_tilt_range = calibration.level_x - calibration.left_x
            right_tilt_range = calibration.right_x - calibration.level_x
            left_dead_zone_boundary = calibration.level_x - left_tilt_range * 0.5
            right_dead_zone_boundary = calibration.level_x + right_tilt_range * 0.5

            if left_dead_zone_boundary < x_tilt_value < right_dead_zone_boundary:
                return  # We are in the dead zone, do nothing

            tilt_direction = 1 if x_tilt_value > calibration.level_x else -1
            try:
                # Use AppleScript to get and set system volume on macOS
                settings_output = subprocess.run(
                    ["osascript", "-e", "get volume settings"],
                    capture_output=True, text=True, timeout=1
                ).stdout
                match = re.search(r'output volume:(\d+)', settings_output)
                current_volume = int(match.group(1)) if match else 50
                new_volume = max(0, min(100, current_volume + tilt_direction * 1))
                subprocess.run(
                    ["osascript", "-e", f"set volume output volume {new_volume}"],
                    capture_output=True, timeout=1)
            except Exception:
                pass

        def on_accelerometer_update(packet):
            nonlocal last_render_time, last_volume_adjustment_time, smoothed_x_tilt
            now = time.monotonic()
            if now - last_render_time < MIN_RENDER_INTERVAL:
                return

            # Take a sample from the packet (using index 1 for stability)
            raw_x, _, _ = packet["samples"][1]
            current_g_force_x = convert_raw_to_g_force(raw_x)

            # Apply EMA smoothing to the X-axis g-force
            smoothed_x_tilt = smoothed_x_tilt + SMOOTHING_ALPHA * (current_g_force_x - smoothed_x_tilt)

            # Check if we should adjust volume based on smoothed tilt
            adjust_volume_based_on_tilt(smoothed_x_tilt)

            last_render_time = now
            screen_content = generate_live_status_display(
                smoothed_x_tilt,
                get_current_system_volume(),
                calibration
            )
            print(screen_content, end="", flush=True)

        client.on("accel", on_accelerometer_update)
        await client.start_sync_loop()

    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print(TERM_SHOW_CURSOR)
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Garmin accelerometer demo")
    parser.add_argument("--calibrate", action="store_true",
                        help="Force recalibration (ignore saved .calibration)")
    args = parser.parse_args()
    try:
        asyncio.run(main(force_recalibration=args.calibrate))
    except KeyboardInterrupt:
        pass
