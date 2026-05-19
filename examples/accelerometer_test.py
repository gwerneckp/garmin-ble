#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Accelerometer Multiview Comparison (Pygame).

Displays 6 high-resolution 3D views of the watch model, each using a different
permutation of the (X, Y, Z) sensor axes mapped to the (b1, b2, b3) basis.
Includes interactive controls to flip axes and adjust bias.

Usage:
  python3 examples/accelerometer_test.py
"""

import asyncio
import logging
import math
import sys
import time
import pygame

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure

# ── 3D Engine Constants ──────────────────────────────────────────────────

DISK_SEGMENTS = 24
DISK_RADIUS = 100
DISK_THICKNESS = 40

def generate_disk_model():
    vertices = []
    edges = []
    for z in [DISK_THICKNESS / 2, -DISK_THICKNESS / 2]:
        for i in range(DISK_SEGMENTS):
            angle = (2 * math.pi * i) / DISK_SEGMENTS
            x = DISK_RADIUS * math.cos(angle)
            y = DISK_RADIUS * math.sin(angle)
            vertices.append([x, y, z])
    for j in [0, DISK_SEGMENTS]: 
        for i in range(DISK_SEGMENTS):
            next_i = (i + 1) % DISK_SEGMENTS
            edges.append((j + i, j + next_i))
    for i in range(DISK_SEGMENTS):
        edges.append((i, i + DISK_SEGMENTS))
    return vertices, edges

WATCH_VERTICES, WATCH_EDGES = generate_disk_model()

# ── UI Components ─────────────────────────────────────────────────────────

class Button:
    def __init__(self, x, y, w, h, text, color=(100, 100, 100)):
        self.rect = pygame.Rect(x, y, w, h)
        self.text = text
        self.color = color
        self.active_color = (0, 200, 0)
        self.is_active = False

    def draw(self, screen, font):
        color = self.active_color if self.is_active else self.color
        pygame.draw.rect(screen, color, self.rect)
        pygame.draw.rect(screen, (255, 255, 255), self.rect, 2)
        text_surf = font.render(self.text, True, (255, 255, 255))
        screen.blit(text_surf, (self.rect.centerx - text_surf.get_width()//2, 
                               self.rect.centery - text_surf.get_height()//2))

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.rect.collidepoint(event.pos):
                self.is_active = not self.is_active
                return True
        return False

class Slider:
    def __init__(self, x, y, w, label, min_val=-1.0, max_val=1.0, initial=0.0):
        self.rect = pygame.Rect(x, y, w, 20)
        self.label = label
        self.min_val = min_val
        self.max_val = max_val
        self.value = initial
        self.handle_rect = pygame.Rect(x, y, 10, 20)
        self.update_handle()
        self.dragging = False

    def update_handle(self):
        pos = (self.value - self.min_val) / (self.max_val - self.min_val)
        self.handle_rect.centerx = self.rect.x + pos * self.rect.width

    def draw(self, screen, font):
        pygame.draw.rect(screen, (100, 100, 100), self.rect)
        pygame.draw.rect(screen, (200, 200, 200), self.handle_rect)
        text = f"{self.label}: {self.value:+.3f}"
        text_surf = font.render(text, True, (255, 255, 255))
        screen.blit(text_surf, (self.rect.x, self.rect.y - 25))

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            if self.handle_rect.collidepoint(event.pos) or self.rect.collidepoint(event.pos):
                self.dragging = True
        elif event.type == pygame.MOUSEBUTTONUP:
            self.dragging = False
        elif event.type == pygame.MOUSEMOTION and self.dragging:
            pos = max(0, min(1, (event.pos[0] - self.rect.x) / self.rect.width))
            self.value = self.min_val + pos * (self.max_val - self.min_val)
            self.update_handle()
            return True
        return False

# ── Rendering Logic ──────────────────────────────────────────────────────

def rotate_3d(x, y, z, roll, pitch):
    ny = y * math.cos(pitch) - z * math.sin(pitch)
    nz = y * math.sin(pitch) + z * math.cos(pitch)
    y, z = ny, nz
    nx = x * math.cos(roll) + z * math.sin(roll)
    nz = -x * math.sin(roll) + z * math.cos(roll)
    x, z = nx, nz
    return x, y, z

def project_2d(x, y, z, center_x, center_y):
    dist = z + 400
    factor = 300 / dist
    px = int(center_x + x * factor)
    py = int(center_y - y * factor)
    return px, py

# ── Application State ─────────────────────────────────────────────────────

class MultiviewApp:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((1400, 900))
        pygame.display.set_caption("Garmin Accel Dashboard")
        self.font = pygame.font.SysFont("monospace", 16, bold=True)
        self.clock = pygame.time.Clock()
        self.raw_xyz = [0.0, 0.0, 0.0]
        self.timestamp = 0
        self.running = True

        # Controls
        self.btn_flip_x = Button(1220, 50, 150, 40, "Flip X")
        self.btn_flip_y = Button(1220, 100, 150, 40, "Flip Y")
        self.btn_flip_z = Button(1220, 150, 150, 40, "Flip Z")
        
        self.slider_bias_x = Slider(1220, 250, 150, "Bias X")
        self.slider_bias_y = Slider(1220, 320, 150, "Bias Y")
        self.slider_bias_z = Slider(1220, 390, 150, "Bias Z")
        
        self.alpha_slider = Slider(1220, 480, 150, "Smoothing", 0.01, 0.5, 0.15)
        
        self.smoothed = [0.0, 0.0, 0.0]

    def update_sensor_data(self, packet):
        samples = packet["samples"]
        self.raw_xyz = [
            (sum(s[0] for s in samples) / len(samples)) / 1024,
            (sum(s[1] for s in samples) / len(samples)) / 1024,
            (sum(s[2] for s in samples) / len(samples)) / 1024
        ]
        self.timestamp = packet["timestamp_ms"]

    def draw_view(self, b_vector, label, rect):
        cx, cy = rect.center
        b1, b2, b3 = b_vector
        roll = math.atan2(b1, b3)
        pitch = math.atan2(-b2, math.sqrt(b1**2 + b3**2))
        pts = []
        for x, y, z in WATCH_VERTICES:
            rx, ry, rz = rotate_3d(x, y, z, roll, pitch)
            pts.append(project_2d(rx, ry, rz, cx, cy))
        for start, end in WATCH_EDGES:
            pygame.draw.line(self.screen, (0, 255, 255), pts[start], pts[end], 2)
        text_surf = self.font.render(label, True, (255, 255, 0))
        self.screen.blit(text_surf, (rect.x + 10, rect.y + 10))
        pygame.draw.rect(self.screen, (60, 60, 60), rect, 1)

    def render(self):
        self.screen.fill((15, 15, 20))
        
        # Apply Logic
        alpha = self.alpha_slider.value
        for i in range(3):
            # 1. Flip
            val = self.raw_xyz[i]
            if (i==0 and self.btn_flip_x.is_active) or \
               (i==1 and self.btn_flip_y.is_active) or \
               (i==2 and self.btn_flip_z.is_active):
                val = -val
            
            # 2. Bias
            bias = [self.slider_bias_x.value, self.slider_bias_y.value, self.slider_bias_z.value][i]
            val -= bias
            
            # 3. Smooth
            self.smoothed[i] += alpha * (val - self.smoothed[i])

        rx, ry, rz = self.smoothed
        configs = [
            ( (rx, ry, rz), "b1=X b2=Y b3=Z" ),
            ( (rx, rz, ry), "b1=X b2=Z b3=Y" ),
            ( (ry, rx, rz), "b1=Y b2=X b3=Z" ),
            ( (ry, rz, rx), "b1=Y b2=Z b3=X" ),
            ( (rz, rx, ry), "b1=Z b2=X b3=Y" ),
            ( (rz, ry, rx), "b1=Z b2=Y b3=X" )
        ]

        w, h = 400, 400
        for i, (b_vec, label) in enumerate(configs):
            row, col = i // 3, i % 3
            self.draw_view(b_vec, label, pygame.Rect(col * w, row * h, w, h))

        # Controls Panel
        pygame.draw.line(self.screen, (100, 100, 100), (1200, 0), (1200, 900), 2)
        self.btn_flip_x.draw(self.screen, self.font)
        self.btn_flip_y.draw(self.screen, self.font)
        self.btn_flip_z.draw(self.screen, self.font)
        self.slider_bias_x.draw(self.screen, self.font)
        self.slider_bias_y.draw(self.screen, self.font)
        self.slider_bias_z.draw(self.screen, self.font)
        self.alpha_slider.draw(self.screen, self.font)

        # Info
        info = [
            f"X: {rx:+.3f}", f"Y: {ry:+.3f}", f"Z: {rz:+.3f}",
            f"MS: {self.timestamp}"
        ]
        for i, txt in enumerate(info):
            surf = self.font.render(txt, True, (200, 200, 200))
            self.screen.blit(surf, (1220, 600 + i*30))

        pygame.display.flip()

    async def run(self, client):
        client.on("accel", self.update_sensor_data)
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT: self.running = False
                self.btn_flip_x.handle_event(event)
                self.btn_flip_y.handle_event(event)
                self.btn_flip_z.handle_event(event)
                self.slider_bias_x.handle_event(event)
                self.slider_bias_y.handle_event(event)
                self.slider_bias_z.handle_event(event)
                self.alpha_slider.handle_event(event)
            self.render()
            await asyncio.sleep(0.01)
            self.clock.tick(60)

async def main():
    configure(level=logging.WARNING)
    app = MultiviewApp()
    client = GarminClient()
    try:
        if not await client.connect(): return
        await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
        sync_task = asyncio.create_task(client.start_sync_loop())
        await app.run(client)
        sync_task.cancel()
    except (asyncio.CancelledError, KeyboardInterrupt): pass
    finally:
        pygame.quit()
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
