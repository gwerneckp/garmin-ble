#!/usr/bin/env python3
"""3D Watch Orientation Demo (Pygame) - Shaded Edition

Parses the true 14-byte accelerometer payload and uses it to
rotate a 3D solid cylinder representing the watch face in realtime,
complete with backface culling, Z-sorting, and directional lighting.
"""

import asyncio
import logging
import math
import struct
import pygame

from garmin_ble import GarminClient, GarminService
from garmin_ble.logging import configure


# ── 3D Math ────────────────────────────────────────────────────────────────

def rotate_3d(point, pitch, roll):
    x, y, z = point

    # Rotate around X-axis (Pitch)
    cos_p = math.cos(pitch)
    sin_p = math.sin(pitch)
    y1 = y * cos_p - z * sin_p
    z1 = y * sin_p + z * cos_p

    # Rotate around Y-axis (Roll)
    cos_r = math.cos(roll)
    sin_r = math.sin(roll)
    x2 = x * cos_r + z1 * sin_r
    z2 = -x * sin_r + z1 * cos_r

    return (x2, y1, z2)

def project_3d(point, width, height, fov=400, viewer_distance=400):
    x, y, z = point
    factor = fov / (viewer_distance + z) if (viewer_distance + z) != 0 else 1
    x_proj = x * factor + width // 2
    y_proj = -y * factor + height // 2  # Flip Y for screen coordinates
    return (int(x_proj), int(y_proj))

def normalize(v):
    mag = math.sqrt(v[0]**2 + v[1]**2 + v[2]**2)
    return (v[0]/mag, v[1]/mag, v[2]/mag) if mag > 0 else (0,0,0)

def cross(v1, v2):
    return (
        v1[1]*v2[2] - v1[2]*v2[1],
        v1[2]*v2[0] - v1[0]*v2[2],
        v1[0]*v2[1] - v1[1]*v2[0]
    )

def sub(v1, v2):
    return (v1[0]-v2[0], v1[1]-v2[1], v1[2]-v2[2])

# ── Pygame App ─────────────────────────────────────────────────────────────

class Watch3DApp:
    def __init__(self):
        pygame.init()
        self.width, self.height = 800, 600
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Orientação 3D Garmin (Sombreado)")
        self.font = pygame.font.SysFont("monospace", 20, bold=True)
        self.clock = pygame.time.Clock()
        self.running = True

        # Smoothing variables
        self.smooth_x = 0.0
        self.smooth_y = 0.0
        self.smooth_z = -1.0
        self.alpha = 0.15

        self._build_geometry()

    def _build_geometry(self):
        self.vertices = []
        self.faces = []  # List of (base_color, specular_power, [vertex_indices])
        
        radius_outer = 120
        radius_screen = 105
        radius_ring = 85
        radius_ring_inner = 75
        thickness = 18
        segments = 36
        self.segments = segments

        color_body = (30, 32, 36)
        color_bezel = (160, 170, 185)  # Silver/Titanium
        color_screen = (5, 8, 12)      # Deep AMOLED black
        color_accent = (0, 255, 180)   # Cyan glow
        color_bottom = (20, 22, 25)

        # Helper to add a ring of vertices
        def add_ring(r, z):
            start_idx = len(self.vertices)
            for i in range(segments):
                angle = i * (2 * math.pi / segments)
                self.vertices.append((math.cos(angle) * r, math.sin(angle) * r, z))
            return start_idx

        # 0: Outer Top
        idx_top_out = add_ring(radius_outer, thickness)
        # 1: Outer Bottom
        idx_bot_out = add_ring(radius_outer, -thickness)
        # 2: Screen Edge (Bezel inner)
        idx_screen = add_ring(radius_screen, thickness)
        # 3: Glowing Ring Outer
        idx_ring_out = add_ring(radius_ring, thickness - 1)
        # 4: Glowing Ring Inner
        idx_ring_in = add_ring(radius_ring_inner, thickness - 1)
        # 5: Screen Center
        idx_center = len(self.vertices)
        self.vertices.append((0, 0, thickness - 1))

        # --- Faces ---
        # Body (Side cylinder)
        for i in range(segments):
            nxt = (i + 1) % segments
            # Material: Body color, low specular
            self.faces.append((color_body, 2, [
                idx_top_out + i, 
                idx_bot_out + i, 
                idx_bot_out + nxt, 
                idx_top_out + nxt
            ]))

        # Bottom Face
        self.faces.append((color_bottom, 1, [idx_bot_out + i for i in reversed(range(segments))]))

        # Metallic Bezel (Outer Top to Screen Edge)
        for i in range(segments):
            nxt = (i + 1) % segments
            # Material: Silver, high specular (shiny metal)
            self.faces.append((color_bezel, 12, [
                idx_top_out + i,
                idx_top_out + nxt,
                idx_screen + nxt,
                idx_screen + i
            ]))

        # Screen Outer (Screen Edge to Glowing Ring Outer)
        for i in range(segments):
            nxt = (i + 1) % segments
            # Material: Glass black, high specular
            self.faces.append((color_screen, 25, [
                idx_screen + i,
                idx_screen + nxt,
                idx_ring_out + nxt,
                idx_ring_out + i
            ]))

        # Glowing Ring
        for i in range(segments):
            nxt = (i + 1) % segments
            # Material: Cyan glow, no specular (unlit)
            self.faces.append((color_accent, 0, [
                idx_ring_out + i,
                idx_ring_out + nxt,
                idx_ring_in + nxt,
                idx_ring_in + i
            ]))

        # Screen Inner (Glowing Ring Inner to Center)
        for i in range(segments):
            nxt = (i + 1) % segments
            self.faces.append((color_screen, 25, [
                idx_ring_in + i,
                idx_ring_in + nxt,
                idx_center
            ]))

        # 12 o'clock Red Triangle on Bezel
        idx_12_tip = len(self.vertices)
        self.vertices.append((0, radius_outer - 5, thickness + 1))
        idx_12_l = len(self.vertices)
        self.vertices.append((-8, radius_screen + 2, thickness + 1))
        idx_12_r = len(self.vertices)
        self.vertices.append((8, radius_screen + 2, thickness + 1))
        
        self.faces.append(((255, 50, 50), 2, [idx_12_tip, idx_12_l, idx_12_r]))

    def on_accel(self, samples):
        if not samples:
            return
        
        avg_x = sum(s[0] for s in samples) / 3.0
        avg_y = sum(s[1] for s in samples) / 3.0
        avg_z = sum(s[2] for s in samples) / 3.0

        self.smooth_x = self.smooth_x * (1 - self.alpha) + avg_x * self.alpha
        self.smooth_y = self.smooth_y * (1 - self.alpha) + avg_y * self.alpha
        self.smooth_z = self.smooth_z * (1 - self.alpha) + avg_z * self.alpha

    def draw(self):
        self.screen.fill((25, 25, 30))

        # Calculate pitch and roll
        pitch = math.atan2(-self.smooth_y, math.sqrt(self.smooth_x**2 + self.smooth_z**2))
        roll = math.atan2(self.smooth_x, self.smooth_z)

        # Transform vertices
        transformed = []
        for v in self.vertices:
            # 1. Rotate watch itself based on accelerometer
            rotated = rotate_3d(v, pitch, roll)
            # 2. Rotate camera 90 degrees around X to view edge-on
            cam_rotated = (rotated[0], -rotated[2], rotated[1])
            transformed.append(cam_rotated)

        faces_to_draw = []
        light_dir = normalize((0.5, 0.7, -1.0)) # Light coming from top-left, pointing into screen

        for base_color, spec_power, indices in self.faces:
            pts_3d = [transformed[i] for i in indices]
            
            # Normal calculation (assuming planar face, using first 3 vertices)
            v0, v1, v2 = pts_3d[0], pts_3d[1], pts_3d[2]
            normal = normalize(cross(sub(v1, v0), sub(v2, v1)))
            
            # Backface culling: if normal points away from camera (+Z), skip it
            if normal[2] > 0:
                continue

            # Directional Lighting & Materials
            if spec_power == 0:
                # Unlit / Glowing material (e.g. Cyan ring)
                shaded_color = base_color
            else:
                intensity = max(0.0, - (normal[0]*light_dir[0] + normal[1]*light_dir[1] + normal[2]*light_dir[2]))
                ambient = 0.25
                diffuse = intensity * 0.7
                
                # Fake specular highlight
                specular = 0
                if intensity > 0:
                    specular = (intensity ** spec_power) * 0.6
                    
                light = min(1.0, ambient + diffuse + specular)
                shaded_color = (int(base_color[0]*light), int(base_color[1]*light), int(base_color[2]*light))

            # Z-sort value (average Z of the face)
            avg_z = sum(p[2] for p in pts_3d) / len(pts_3d)
            
            # Project to 2D
            pts_2d = [project_3d(p, self.width, self.height) for p in pts_3d]
            faces_to_draw.append((avg_z, pts_2d, shaded_color))
        
        # Painter's Algorithm: Sort by depth (Z descending, furthest first)
        faces_to_draw.sort(key=lambda x: x[0], reverse=True)

        for z, pts_2d, color in faces_to_draw:
            pygame.draw.polygon(self.screen, color, pts_2d)
            # Draw subtle anti-aliased wireframe to prevent gaps between polygons
            pygame.draw.polygon(self.screen, color, pts_2d, 1)

        # HUD Text
        txt_pitch = self.font.render(f"Inclinação: {math.degrees(pitch):.1f}°", True, (200, 200, 200))
        txt_roll = self.font.render(f"Rolagem:    {math.degrees(roll):.1f}°", True, (200, 200, 200))
        txt_vec = self.font.render(f"Gravidade:  [{self.smooth_x:+.2f}, {self.smooth_y:+.2f}, {self.smooth_z:+.2f}]", True, (150, 150, 150))
        
        self.screen.blit(txt_pitch, (20, 20))
        self.screen.blit(txt_roll, (20, 50))
        self.screen.blit(txt_vec, (20, 80))

        pygame.display.flip()

    async def run(self, client):
        client.on("accel", self.on_accel)
        while self.running:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
            
            self.draw()
            await asyncio.sleep(0.01)
            self.clock.tick(60)

async def main():
    configure(level=logging.WARNING)
    app = Watch3DApp()
    client = GarminClient()

    try:
        if not await client.connect():
            return
        
        await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
        sync_task = asyncio.create_task(client.start_sync_loop())
        await app.run(client)
        sync_task.cancel()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        pygame.quit()
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
