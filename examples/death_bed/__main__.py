#!/usr/bin/env python3
"""TILT/Garmin BLE — Death Bed Telemetry Monitor.

A clinical-style patient monitor displaying real-time waveforms and metrics 
directly streamed from a Garmin BLE watch. Shows live ECG and Pleth waves synced 
to your actual heart rate, a respiration wave synced to your breathing rate, 
and raw 3-axis accelerometer traces.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple, Optional

import pygame

# Try importing numpy for dynamic tone generation, fallback if not available
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from garmin_ble import GarminBleError, Watch, metrics, events
from garmin_ble.logging import configure

# ── Colors & Aesthetics (Clinical Palette) ────────────────────────────────

BG_COLOR = (0, 0, 0)             # Pure black CRT background
GRID_COLOR = (12, 20, 16)        # Clinical dark green faint grid lines

# Standard Hospital Color Codes for Vitals
ECG_COLOR = (0, 255, 0)          # Green for ECG / Heart Rate
SPO2_COLOR = (0, 240, 255)       # Cyan for SpO2 / Pleth
RESP_COLOR = (255, 240, 0)       # Yellow for Respiration
STRESS_COLOR = (230, 126, 34)    # Orange for Stress
BAT_COLOR = (200, 150, 255)      # Lilac/Purple for Body Battery / Energy
ACC_COLOR = (200, 150, 255)      # Purple/Lilac for ACC/EEG text

ACC_X_COLOR = (231, 76, 60)      # Red
ACC_Y_COLOR = (155, 89, 182)     # Purple
ACC_Z_COLOR = (230, 126, 34)     # Orange

TEXT_MUTED = (120, 140, 150)
ALERT_BG_YELLOW = (200, 160, 0)

# ── Dynamic Beep Tone Generator ──────────────────────────────────────────

def create_beep(frequency: float = 620.0, duration: float = 0.12, sample_rate: int = 22050) -> Optional[pygame.mixer.Sound]:
    if not HAS_NUMPY:
        return None
    try:
        n_samples = int(duration * sample_rate)
        t = np.linspace(0, duration, n_samples, False)
        sine_wave = np.sin(2 * np.pi * frequency * t)
        
        # Smooth fade out to prevent clicking speaker pop
        fade_out = min(150, n_samples // 4)
        fade = np.ones(n_samples)
        fade[-fade_out:] = np.linspace(1.0, 0.0, fade_out)
        sine_wave *= fade

        # Convert to 16-bit signed integers
        audio = (sine_wave * 32767).astype(np.int16)
        stereo = np.column_stack((audio, audio))
        return pygame.sndarray.make_sound(stereo)
    except Exception:
        return None

# ── Waveform Simulators ───────────────────────────────────────────────────

class ECGWave:
    """Generates standard ECG wave (P-QRS-T complex) dynamically."""
    def __init__(self, size: int = 400):
        self.size = size
        self.buffer = [0.0] * size
        self.phase = 0.0

    def step(self, dt: float, bpm: float) -> float:
        self.phase += (bpm / 60.0) * dt
        p = self.phase % 1.0

        val = 0.0
        # P Wave
        if 0.05 <= p <= 0.15:
            val = 0.12 * math.sin((p - 0.05) / 0.10 * math.pi)
        # QRS Complex
        elif 0.20 <= p <= 0.22:
            val = -0.15 * math.sin((p - 0.20) / 0.02 * math.pi)
        elif 0.22 <= p <= 0.25:
            # R spike
            val = 1.1 * math.sin((p - 0.22) / 0.03 * math.pi)
        elif 0.25 <= p <= 0.28:
            # S drop
            val = -0.32 * math.sin((p - 0.25) / 0.03 * math.pi)
        # T Wave
        elif 0.35 <= p <= 0.52:
            val = 0.26 * math.sin((p - 0.35) / 0.17 * math.pi)
        
        # Faint line noise
        val += random.uniform(-0.008, 0.008)
        return val

class PlethWave:
    """Pulse wave (photoplethysmogram) matching the pulse flow."""
    def __init__(self):
        self.phase = 0.0

    def step(self, dt: float, bpm: float) -> float:
        self.phase += (bpm / 60.0) * dt
        p = self.phase % 1.0
        
        val = 0.0
        if p <= 0.30:
            val = math.sin(p / 0.30 * (math.pi / 2))
        elif p <= 0.45:
            # Dicrotic notch
            val = 0.70 + 0.10 * math.sin((p - 0.30) / 0.15 * math.pi)
        else:
            val = 0.80 * math.cos((p - 0.45) / 0.55 * (math.pi / 2))
            
        val += random.uniform(-0.005, 0.005)
        return val

class RespWave:
    """Slow sine wave for breathing cycles."""
    def __init__(self):
        self.phase = 0.0

    def step(self, dt: float, rpm: float) -> float:
        self.phase += (rpm / 60.0) * dt
        val = math.sin(self.phase * math.tau)
        val += random.uniform(-0.02, 0.02)
        return val

# ── App Classes ───────────────────────────────────────────────────────────

class TelemetryState:
    """Consolidated metrics from the watch or simulation."""
    def __init__(self):
        # Current Vitals (None means disconnected/flatline)
        self.bpm: Optional[float] = None
        self.resting_bpm: Optional[int] = None
        self.spo2: Optional[int] = None
        self.resp_rate: Optional[int] = None
        self.body_battery: Optional[int] = None
        self.stress_level: Optional[int] = None
        self.hrv_rr_ms: Optional[int] = None
        self.steps: Optional[int] = None

        # Accel
        self.acc_x = 0.0
        self.acc_y = 0.0
        self.acc_z = -1.0

        # Connection status
        self.device_name = "SEARCHING"
        self.connected = False
        self.connecting = True
        self.packets = 0
        self.error_msg = ""

        # Timers
        self.last_beat_time = 0.0

    def initialize_simulation(self) -> None:
        if self.bpm is None:
            self.bpm = 74.0
            self.resting_bpm = 60
            self.spo2 = 98
            self.resp_rate = 14
            self.body_battery = 85
            self.stress_level = 18
            self.hrv_rr_ms = 820
            self.steps = 4280
            self.device_name = "SIMULATOR"

    def simulate(self, dt: float) -> None:
        """Slightly drift vitals to look organic in demo/simulation mode."""
        self.initialize_simulation()
        self.bpm += random.uniform(-0.4, 0.4) * dt
        self.bpm = max(55.0, min(130.0, self.bpm))

        if random.random() < 0.02:
            self.spo2 = max(95, min(100, self.spo2 + random.choice([-1, 0, 1])))

        if random.random() < 0.02:
            self.resp_rate = max(11, min(22, self.resp_rate + random.choice([-1, 0, 1])))

        if random.random() < 0.01:
            self.stress_level = max(5, min(95, self.stress_level + random.choice([-2, -1, 0, 1, 2])))
            
        if random.random() < 0.03 and self.steps is not None:
            self.steps += random.choice([0, 1, 2])

        if random.random() < 0.05 and self.hrv_rr_ms is not None:
            self.hrv_rr_ms = max(600, min(1200, self.hrv_rr_ms + random.choice([-10, -5, 0, 5, 10])))


class DeathBedApp:
    def __init__(self, simulate: bool):
        self.simulate_mode = simulate
        self.state = TelemetryState()
        
        # Audio
        self.sound_enabled = HAS_NUMPY
        self.last_beep_pitch = 0.0
        self.beep_sound = None
        self.warning_chime_time = 0.0

        # Grid and UI
        self.width = 1024
        self.height = 768
        self.running = True
        
        pygame.init()
        pygame.mixer.init()
        
        self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
        pygame.display.set_caption("Garmin BLE ICU Bedside Monitor - BED 08")
        self.clock = pygame.time.Clock()

        # Load fonts
        self.fonts = {}
        self._init_fonts()

        # Waveforms
        self.ecg_wave = ECGWave()
        self.pleth_wave = PlethWave()
        self.resp_wave = RespWave()

        # Wave buffers for scrolling CRT trace line
        self.trace_width = 750
        self.ecg_buffer = [0.0] * self.trace_width
        self.pleth_buffer = [0.0] * self.trace_width
        self.resp_buffer = [0.0] * self.trace_width
        self.acc_x_buffer = [0.0] * self.trace_width
        self.acc_y_buffer = [0.0] * self.trace_width
        self.acc_z_buffer = [0.0] * self.trace_width

        # Scanning index for sweeping line
        self.scan_idx = 0
        self.last_time = time.perf_counter()

    def _init_fonts(self) -> None:
        sys_mono = pygame.font.match_font("courier", "monaco", "consolas")
        self.fonts["large_digit"] = pygame.font.Font(sys_mono, 90)
        self.fonts["mid_digit"] = pygame.font.Font(sys_mono, 48)
        self.fonts["label"] = pygame.font.Font(sys_mono, 14)
        self.fonts["label_bold"] = pygame.font.Font(sys_mono, 15)
        self.fonts["mono"] = pygame.font.Font(sys_mono, 18)
        self.fonts["mono_bold"] = pygame.font.Font(sys_mono, 22)

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self.width, self.height = event.size
                self.screen = pygame.display.set_mode((self.width, self.height), pygame.RESIZABLE)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                elif event.key == pygame.K_s:
                    self.sound_enabled = not self.sound_enabled

    def update(self, dt: float) -> None:
        is_live = self.state.connected and not self.simulate_mode
        if self.simulate_mode:
            self.state.simulate(dt)
        
        # 1. Beep / Chime trigger
        if self.simulate_mode or is_live:
            bpm = self.state.bpm if self.state.bpm is not None else 72.0
            time_since_beat = time.perf_counter() - self.state.last_beat_time
            beat_interval = 60.0 / bpm
            
            if time_since_beat >= beat_interval:
                self.state.last_beat_time = time.perf_counter()
                if self.sound_enabled and HAS_NUMPY:
                    spo2 = self.state.spo2 if self.state.spo2 is not None else 98
                    pitch = 500.0 + (spo2 - 90) * 18.0
                    pitch = max(200.0, min(1000.0, pitch))
                    
                    if abs(pitch - self.last_beep_pitch) > 1.0 or self.beep_sound is None:
                        self.beep_sound = create_beep(pitch)
                        self.last_beep_pitch = pitch
                    
                    if self.beep_sound:
                        self.beep_sound.play()
        else:
            self.warning_chime_time += dt
            if self.warning_chime_time >= 3.0:
                self.warning_chime_time = 0.0
                if self.sound_enabled and HAS_NUMPY:
                    # Alert tone sequence: beep beep
                    c1 = create_beep(420.0, 0.12)
                    c2 = create_beep(340.0, 0.12)
                    if c1 and c2:
                        c1.play()
                        pygame.time.delay(150)
                        c2.play()

        # 2. Step waveforms
        if self.simulate_mode or is_live:
            bpm = self.state.bpm if self.state.bpm is not None else 72.0
            resp_rate = self.state.resp_rate if self.state.resp_rate is not None else 14.0
            ecg_val = self.ecg_wave.step(dt, bpm)
            pleth_val = self.pleth_wave.step(dt, bpm)
            resp_val = self.resp_wave.step(dt, resp_rate)
            acc_x, acc_y, acc_z = self.state.acc_x, self.state.acc_y, self.state.acc_z
        else:
            # Leads off flatline noise
            ecg_val = random.uniform(-0.012, 0.012)
            pleth_val = random.uniform(-0.006, 0.006)
            resp_val = random.uniform(-0.01, 0.01)
            acc_x, acc_y, acc_z = 0.0, 0.0, -1.0

        # 3. Fill trace buffers
        step_size = max(1, int(150 * dt))
        for _ in range(step_size):
            self.scan_idx = (self.scan_idx + 1) % self.trace_width
            self.ecg_buffer[self.scan_idx] = ecg_val
            self.pleth_buffer[self.scan_idx] = pleth_val
            self.resp_buffer[self.scan_idx] = resp_val
            
            self.acc_x_buffer[self.scan_idx] = acc_x
            self.acc_y_buffer[self.scan_idx] = acc_y
            self.acc_z_buffer[self.scan_idx] = acc_z + 1.0

    def draw(self) -> None:
        self.screen.fill(BG_COLOR)
        
        # Faint green grid lines
        for x in range(0, self.width, 35):
            pygame.draw.line(self.screen, GRID_COLOR, (x, 0), (x, self.height), 1)
        for y in range(0, self.height, 35):
            pygame.draw.line(self.screen, GRID_COLOR, (0, y), (self.width, y), 1)

        # UI Dividers
        vitals_start_x = int(self.width * 0.74)
        pygame.draw.line(self.screen, (50, 60, 70), (vitals_start_x, 50), (vitals_start_x, self.height), 2)
        pygame.draw.line(self.screen, (50, 60, 70), (0, 50), (self.width, 50), 2)

        # 1. Header (Patient Info Bar)
        self._draw_header(vitals_start_x)

        # 2. Waveforms (Left Panel)
        self._draw_waveforms(vitals_start_x)

        # 3. Vitals columns (Right Panel)
        self._draw_vitals(vitals_start_x)

        pygame.display.flip()

    def _draw_header(self, vitals_start_x: int) -> None:
        is_live = self.state.connected and not self.simulate_mode
        active = self.simulate_mode or is_live

        # Left Info block (shortened if disconnected to prevent layout cutoff)
        if not active:
            patient_text = "BED 08  |  DISCONNECTED"
        else:
            patient_text = "BED 08  |  SPROUT, GUEST  |  ID: 802-991"

        lbl = self.fonts["mono_bold"].render(patient_text, True, (255, 255, 255))
        self.screen.blit(lbl, (15, 12))

        # Center Alert Banner (Real clinical style)
        alert_flash = (int(time.perf_counter() * 2.0) % 2) == 0

        if not active:
            # Flashing yellow warning bar at top
            alert_w = vitals_start_x - lbl.get_width() - 80
            alert_x = lbl.get_width() + 40
            alert_rect = pygame.Rect(alert_x, 8, alert_w, 34)
            
            if alert_flash:
                pygame.draw.rect(self.screen, ALERT_BG_YELLOW, alert_rect, border_radius=4)
                alert_text = "** LEADS OFF **"
                alert_lbl = self.fonts["mono_bold"].render(alert_text, True, (0, 0, 0))
                self.screen.blit(alert_lbl, (alert_x + alert_w // 2 - alert_lbl.get_width() // 2, 13))
        else:
            status_text = "PACEMAKER: OFF  |  ALARM LEVEL: AUTO"
            status_lbl = self.fonts["label"].render(status_text, True, TEXT_MUTED)
            self.screen.blit(status_lbl, (lbl.get_width() + 40, 18))

        # Time & Audio status
        t_str = time.strftime("%d-%b-%Y  %H:%M:%S")
        time_lbl = self.fonts["mono"].render(t_str, True, (150, 255, 190))
        self.screen.blit(time_lbl, (self.width - time_lbl.get_width() - 15, 14))

    def _draw_waveforms(self, vitals_start_x: int) -> None:
        w_width = vitals_start_x - 70
        n_channels = 4
        channel_height = int((self.height - 75) / n_channels)
        
        is_live = self.state.connected and not self.simulate_mode
        active = self.simulate_mode or is_live

        channels = [
            ("ECG (II) x1.0", ECG_COLOR, self.ecg_buffer, 0.45, "1mV"),
            ("Pleth", SPO2_COLOR, self.pleth_buffer, 0.35, "Scale Auto"),
            ("RESP (x1.0)", RESP_COLOR, self.resp_buffer, 0.35, "Scale Auto"),
            ("ACCELEROMETER (EEG)", None, None, 0.25, "ACC")
        ]

        alert_flash = (int(time.perf_counter() * 2.0) % 2) == 0

        for i, (name, color, buf, scale, scale_lbl) in enumerate(channels):
            y_center = 60 + i * channel_height + channel_height // 2
            
            # Left Wave label block
            lbl_color = color if active else (100, 110, 120)
            if name == "ACCELEROMETER (EEG)":
                lbl_color = ACC_COLOR if active else (100, 110, 120)
                
            name_lbl = self.fonts["label_bold"].render(name, True, lbl_color)
            self.screen.blit(name_lbl, (15, y_center - channel_height // 3))
            
            scale_surf = self.fonts["label"].render(scale_lbl, True, TEXT_MUTED)
            self.screen.blit(scale_surf, (15, y_center - channel_height // 3 + 20))

            # Draw calibration reference bar for ECG
            if name == "ECG (II) x1.0":
                bar_y = y_center - 25
                pygame.draw.line(self.screen, ECG_COLOR if active else (100, 110, 120), (55, bar_y), (55, bar_y + 50), 2)
                pygame.draw.line(self.screen, ECG_COLOR if active else (100, 110, 120), (50, bar_y), (60, bar_y), 2)
                pygame.draw.line(self.screen, ECG_COLOR if active else (100, 110, 120), (50, bar_y + 50), (60, bar_y + 50), 2)

            # Draw "NO SENSOR / LEADS OFF" indicator text
            if not active:
                if alert_flash:
                    leads_lbl = self.fonts["mono_bold"].render("NO SENSOR / CHECK LINK", True, (150, 150, 160))
                    self.screen.blit(leads_lbl, (vitals_start_x // 2 - leads_lbl.get_width() // 2, y_center))

            # Channel boundary line
            pygame.draw.line(self.screen, (22, 32, 45), (10, y_center + channel_height // 2), (vitals_start_x - 10, y_center + channel_height // 2), 1)

            # Draw waveforms
            start_draw_x = 65
            if name != "ACCELEROMETER (EEG)":
                assert buf is not None and color is not None
                points = []
                for x in range(w_width):
                    buf_idx = int(x / w_width * (self.trace_width - 1))
                    dist_to_scan = (buf_idx - self.scan_idx) % self.trace_width
                    if dist_to_scan < 18:
                        continue
                        
                    val = buf[buf_idx]
                    y = y_center - int(val * (channel_height * scale))
                    points.append((start_draw_x + x, y))
                
                if len(points) > 1:
                    line_color = color if active else (70, 75, 80)
                    pygame.draw.lines(self.screen, line_color, False, points, 2)
            else:
                # Accelerator multi-waves (ACC)
                accs = [
                    (self.acc_x_buffer, ACC_X_COLOR),
                    (self.acc_y_buffer, ACC_Y_COLOR),
                    (self.acc_z_buffer, ACC_Z_COLOR)
                ]
                for acc_buf, acc_color in accs:
                    points = []
                    for x in range(w_width):
                        buf_idx = int(x / w_width * (self.trace_width - 1))
                        dist_to_scan = (buf_idx - self.scan_idx) % self.trace_width
                        if dist_to_scan < 18:
                            continue
                            
                        val = acc_buf[buf_idx]
                        y = y_center - int(val * (channel_height * scale))
                        points.append((start_draw_x + x, y))
                    
                    if len(points) > 1:
                        line_color = acc_color if active else (50, 55, 60)
                        pygame.draw.lines(self.screen, line_color, False, points, 1)

    def _draw_vitals(self, vitals_start_x: int) -> None:
        panel_w = self.width - vitals_start_x
        y_offset = 60
        box_h = int((self.height - 75) / 5)

        is_live = self.state.connected and not self.simulate_mode
        active = self.simulate_mode or is_live

        # Heart Rate Pulse flashing check
        time_since_beat = time.perf_counter() - self.state.last_beat_time
        hr_flash = active and (time_since_beat < 0.15)

        # Formatting values
        hr_val = f"{int(self.state.bpm)}" if (active and self.state.bpm is not None) else "--"
        spo2_val = f"{self.state.spo2}" if (active and self.state.spo2 is not None) else "--"
        resp_val = f"{self.state.resp_rate}" if (active and self.state.resp_rate is not None) else "--"
        stress_val = f"{self.state.stress_level}" if (active and self.state.stress_level is not None) else "--"
        bat_val = f"{self.state.body_battery}" if (active and self.state.body_battery is not None) else "--"

        # Extra metrics
        resting_bpm = f"REST  {self.state.resting_bpm}" if (active and self.state.resting_bpm is not None) else "REST  --"
        hrv_lbl = f"HRV {self.state.hrv_rr_ms} ms" if (active and self.state.hrv_rr_ms is not None) else "HRV -- ms"
        steps_lbl = f"STEPS {self.state.steps}" if (active and self.state.steps is not None) else "STEPS --"

        vitals = [
            {
                "label": "ECG (HR)",
                "value": hr_val,
                "color": (255, 255, 255) if hr_flash else ECG_COLOR,
                "extra": resting_bpm,
                "limits": "120\n 50",
            },
            {
                "label": "SPO2 (%)",
                "value": spo2_val,
                "color": SPO2_COLOR,
                "extra": "PULSE PR",
                "limits": "100\n 90",
            },
            {
                "label": "RESP (RPM)",
                "value": resp_val,
                "color": RESP_COLOR,
                "extra": "BREATHS",
                "limits": " 30\n  8",
            },
            {
                "label": "STRESS",
                "value": stress_val,
                "color": STRESS_COLOR,
                "extra": hrv_lbl,
                "limits": " 75\n  0",
            },
            {
                "label": "ENERGY (BAT)",
                "value": bat_val,
                "color": BAT_COLOR,
                "extra": steps_lbl,
                "limits": "100\n  0",
            }
        ]

        for i, vit in enumerate(vitals):
            by = y_offset + i * box_h
            
            # Label
            lbl = self.fonts["label_bold"].render(vit["label"], True, vit["color"] if active else TEXT_MUTED)
            self.screen.blit(lbl, (vitals_start_x + 15, by + 8))

            # Extra info
            ext = self.fonts["label"].render(vit["extra"], True, TEXT_MUTED)
            self.screen.blit(ext, (vitals_start_x + 15, by + box_h - 22))

            # Limit numbers (stacked)
            lim_lines = vit["limits"].split("\n")
            for j, lim_line in enumerate(lim_lines):
                lim_surf = self.fonts["label"].render(lim_line, True, vit["color"] if active else TEXT_MUTED)
                self.screen.blit(lim_surf, (vitals_start_x + panel_w - 75, by + 12 + j * 16))

            # Flashing Heart Icon
            if vit["label"] == "ECG (HR)":
                heart_color = (255, 30, 70) if hr_flash else ((120, 15, 25) if active else (50, 55, 60))
                hx, hy = vitals_start_x + panel_w - 110, by + 12
                pygame.draw.circle(self.screen, heart_color, (hx - 6, hy), 6)
                pygame.draw.circle(self.screen, heart_color, (hx + 6, hy), 6)
                pygame.draw.polygon(self.screen, heart_color, [
                    (hx - 12, hy + 2), (hx + 12, hy + 2), (hx, hy + 14)
                ])

            # Value digits
            val_color = vit["color"] if active else (90, 100, 110)
            val_surf = self.fonts["large_digit"].render(vit["value"], True, val_color)
            self.screen.blit(val_surf, (vitals_start_x + 25, by + 22))

            # Divider line
            pygame.draw.line(self.screen, (22, 35, 50), (vitals_start_x + 10, by + box_h), (self.width - 10, by + box_h), 1)

    def on_accel(self, packet) -> None:
        """Receive live accelerometer packets from watch."""
        self.state.packets += 1
        for sample in packet:
            gx, gy, gz = sample.g
            self.state.acc_x = gx
            self.state.acc_y = gy
            self.state.acc_z = gz
            break

    def on_heart_rate(self, reading) -> None:
        self.state.bpm = float(reading.bpm)
        if reading.resting_bpm is not None:
            self.state.resting_bpm = reading.resting_bpm

    def on_spo2(self, reading) -> None:
        self.state.spo2 = reading.percent

    def on_respiration(self, reading) -> None:
        self.state.resp_rate = reading.breaths_per_min

    def on_body_battery(self, reading) -> None:
        self.state.body_battery = reading.level

    def on_stress(self, reading) -> None:
        self.state.stress_level = reading.level


# ── Watch Connection Task ──────────────────────────────────────────────────

async def watch_connection_loop(app: DeathBedApp) -> None:
    while app.running:
        if app.simulate_mode:
            await asyncio.sleep(1.0)
            continue
            
        app.state.connecting = True
        app.state.connected = False
        app.state.device_name = "SEARCHING"
        
        try:
            async with Watch.discover() as watch:
                app.state.connected = True
                app.state.connecting = False
                app.state.device_name = watch.info.name
                
                # Subscribe to telemetry metrics streams
                watch.on(metrics.ACCELEROMETER)(app.on_accel)
                watch.on(metrics.HEART_RATE)(app.on_heart_rate)
                watch.on(metrics.SPO2)(app.on_spo2)
                watch.on(metrics.RESPIRATION)(app.on_respiration)
                watch.on(metrics.BODY_BATTERY)(app.on_body_battery)
                watch.on(metrics.STRESS)(app.on_stress)
                
                # We can subscribe to additional helpful stats like steps/HRV!
                @watch.on(metrics.HRV)
                def _(reading: metrics.Hrv) -> None:
                    app.state.hrv_rr_ms = reading.rr_ms

                @watch.on(metrics.STEPS)
                def _(reading: metrics.Steps) -> None:
                    app.state.steps = reading.count
                
                # Keep active while connection remains
                async for event in watch.events():
                    if isinstance(event, events.Disconnected):
                        break
                    
        except GarminBleError as exc:
            app.state.error_msg = str(exc)
            app.state.connected = False
            app.state.connecting = False
            await asyncio.sleep(5.0)
        except Exception as e:
            app.state.connected = False
            app.state.connecting = False
            await asyncio.sleep(5.0)


# ── Entry Point ───────────────────────────────────────────────────────────

async def run_gui(app: DeathBedApp) -> None:
    while app.running:
        now = time.perf_counter()
        dt = min(0.05, now - app.last_time)
        app.last_time = now

        app.handle_events()
        app.update(dt)
        app.draw()

        await asyncio.sleep(0)
        app.clock.tick(60)


async def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m examples.death_bed",
        description="Death Bed — hospital telemetry patient monitor visualizer."
    )
    parser.add_argument("--simulate", action="store_true", help="run in simulated demo mode")
    args = parser.parse_args()

    configure(level=logging.WARNING)

    app = DeathBedApp(simulate=args.simulate)
    
    # Start BLE tasks
    watch_task = asyncio.create_task(watch_connection_loop(app))
    
    try:
        await run_gui(app)
    finally:
        app.running = False
        watch_task.cancel()
        pygame.quit()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
