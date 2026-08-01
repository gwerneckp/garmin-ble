"""The state machine, the run loop, and the descent between islands.

States are deliberately few — TITLE, PLAY, DESCEND, DYING, CARD — because the
whole design goal is that a run never stops to show you a menu. Death spends
0.8 seconds on drama and then hands control straight back.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Optional

import pygame

from . import audio as A
from . import board as B
from . import config as C
from . import hud as H
from . import particles as P
from . import physics as PH
from . import render as R
from . import sprite as S
from . import watchmodel as WM
from . import world as W
from .wrist import Wrist

TITLE, PLAY, DESCEND, DYING, CARD = range(5)

#: How far below the current island the next one sits.
DROP_HEIGHT = 900.0

#: ...and how far to the side. Looking straight down, an island stacked
#: directly beneath the one you are leaving is completely hidden by it — the
#: island above is nearer, so it paints over the top. Offsetting the tower into
#: a staircase means the board you are falling towards is in clear view the
#: whole way down, which is the difference between a transition and a blindfold.
DROP_SPREAD = 1.45 * C.GRID * C.CELL

_SCORE_FILE = Path.home() / ".garmin_ble_tilt_best"


def _load_best() -> int:
    try:
        return int(json.loads(_SCORE_FILE.read_text())["best"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _save_best(best: int) -> None:
    try:
        _SCORE_FILE.write_text(json.dumps({"best": best}))
    except OSError:
        pass  # A read-only home should never cost you a run.


def _open_display(fullscreen: bool) -> pygame.Surface:
    """Open at 720p resizable by default, or borderless fullscreen."""
    if not fullscreen:
        return pygame.display.set_mode((1280, 720), pygame.RESIZABLE)

    modes = pygame.display.list_modes()
    size = modes[0] if modes else (0, 0)
    try:
        return pygame.display.set_mode(size, pygame.FULLSCREEN, vsync=1)
    except pygame.error:
        return pygame.display.set_mode(size, pygame.FULLSCREEN)


class Game:
    def __init__(self, seed: Optional[int] = None, audio: bool = True,
                 fullscreen: bool = False) -> None:
        self.rng = random.Random(seed)
        self.screen = _open_display(fullscreen)
        C.apply_display(*self.screen.get_size())
        pygame.display.set_caption(C.TITLE)

        self.fonts = H.Fonts()
        self.sky = R.make_sky(C.WIDTH, C.HEIGHT)
        self.camera = R.Camera()
        self.painter = R.Painter()
        self.particles = P.Particles(self.rng)
        self.clouds = P.Clouds(self.rng)
        self.sprite = S.Sprite(self.rng)
        self.audio = A.Audio(enabled=audio)
        self.watch_model = WM.WatchModel(radius=C.px(66))
        self.wrist = Wrist()

        self.best = _load_best()
        self.state = TITLE
        self.state_time = 0.0
        self.clock = pygame.time.Clock()
        self.running = True
        self.paused = False
        self.paused_background = None
        self.depth_bounce = 0.0
        self.show_sensor = False
        self.show_watch = True
        self.t = 0.0

        self.depth = 0
        self.island: Optional[W.Island] = None
        self.next_island: Optional[W.Island] = None
        self.ball = PH.Ball(0.0, 0.0)
        self.is_record = False
        self.death_fall = 0.0
        self._physics_accum = 0.0
        self._drop_offset = (0.0, 0.0)      # where the next island sits
        self._drop_from = (0.0, 0.0)        # ball position when the drop began

        self._title_island = W.generate(1, random.Random(4))

    # ── Run lifecycle ──────────────────────────────────────────────────────

    def start_run(self) -> None:
        self.depth = 1
        self.depth_bounce = 1.0
        self.island = W.generate(self.depth, self.rng)
        self.next_island = None
        self.ball = PH.Ball(float(self.island.spawn[0]), float(self.island.spawn[1]))
        self._physics_accum = 0.0
        self.is_record = False
        self.camera.origin = (0.0, 0.0, 0.0)
        self.camera.pitch = C.CAM_PITCH
        self.camera.target_distance = C.CAM_DISTANCE
        self.audio.set_depth(self.depth)
        self.audio.play("blip")
        self._set_state(PLAY)

    def _set_state(self, state: int) -> None:
        self.state = state
        self.state_time = 0.0

    def _die(self) -> None:
        self.death_fall = 0.0
        self.audio.stop_rolling()
        self.audio.play("whoosh", 0.8)
        self.audio.play("death")
        self.audio.duck(1.6)
        self.camera.kick(0.55)
        self._set_state(DYING)

    def _reach_goal(self) -> None:
        self.audio.goal_arpeggio()
        self.next_island = W.generate(self.depth + 1, self.rng)

        angle = self.rng.uniform(0.0, math.tau)
        self._drop_offset = (math.cos(angle) * DROP_SPREAD,
                             math.sin(angle) * DROP_SPREAD)
        self._drop_from = (self.ball.x, self.ball.y)

        centre = self._sprite_screen_pos()
        self.particles.burst(centre, 26, P.SPARKLE, C.GOAL_GLOW, 300.0)
        # Add a gorgeous, colorful confetti explosion on success!
        self.particles.burst(centre, 35, P.CONFETTI, speed=260.0)
        self._set_state(DESCEND)

    # ── Frame ──────────────────────────────────────────────────────────────

    def update(self, dt: float) -> None:
        self.t += dt
        self.state_time += dt
        self.wrist.update(dt)
        self.audio.update_music(dt)
        self.clouds.update(dt, scroll=self._cloud_scroll())
        self.particles.update(dt)
        self.particles.ambient(dt, 1.0 if self.state == TITLE else 0.7)
        if self.depth_bounce > 0.0:
            self.depth_bounce = max(0.0, self.depth_bounce - dt * 4.0)

        if self.state == TITLE:
            self._update_title(dt)
        elif self.state == PLAY:
            self._update_play(dt)
        elif self.state == DESCEND:
            self._update_descend(dt)
        elif self.state == DYING:
            self._update_dying(dt)
        elif self.state == CARD:
            self._update_card(dt)

        self.camera.update(dt, self.rng)

    def _cloud_scroll(self) -> float:
        if self.state == DESCEND:
            return -260.0
        if self.state == DYING:
            return -90.0
        return 0.0

    def _update_title(self, dt: float) -> None:
        # Sit further back and lower, so the logo has clear sky behind it
        # rather than competing with the checkered board.
        self.camera.target_roll = 0.0
        self.camera.target_distance = C.CAM_DISTANCE * 1.30
        self.camera.origin = (0.0, 165.0, 0.0)

        self.sprite.update(dt, PH.Ball(0.0, 0.0))
        # Wait for the face axis to be known before handing over control —
        # starting a run on an unidentified mounting would put the island on
        # its edge for the first second.
        if not self.wrist.face_locked:
            return
        # Any decisive movement or a flick begins the run.
        if self.wrist.is_live() and (self.wrist.tilt_magnitude() > 0.22
                                     or self.wrist.take_flick()):
            self.start_run()

    def _update_play(self, dt: float) -> None:
        assert self.island is not None
        tilt = B.board_tilt(self.wrist.gravity())
        gravity = C.gravity_at(self.depth)

        # Fixed-timestep integration. With a variable step, a frame that runs
        # long integrates further and the ball behaves differently — which is
        # felt as the controls going vague exactly when the game is busiest.
        fixed = 1.0 / C.PHYSICS_HZ
        self._physics_accum = min(self._physics_accum + dt, 0.25)
        result = PH.StepResult()
        while self._physics_accum >= fixed:
            self._physics_accum -= fixed
            tick = PH.step(self.ball, self.island, tilt, self.t, fixed,
                           gravity=gravity)
            result.fell |= tick.fell
            result.reached_goal |= tick.reached_goal
            result.bounced |= tick.bounced
            result.hit_vine |= tick.hit_vine
            result.crumbled.extend(tick.crumbled)
            if tick.fell or tick.reached_goal:
                break

        danger = S.danger_level(self.ball, self.island)
        self.sprite.update(dt, self.ball, danger)

        speed_fraction = self.ball.speed_fraction()
        # Lean against how far the board is banked left/right.
        self.camera.follow(-math.asin(R.clamp(tilt[0], -1.0, 1.0)), speed_fraction)

        self.audio.rolling(speed_fraction if self.ball.hop <= 0.0 else 0.0)
        if self.ball.hop <= 0.0 and speed_fraction > 0.12:
            self.particles.trail(self._sprite_screen_pos(), speed_fraction * 0.75)

        if result.bounced:
            self.audio.play("bounce", 0.8)
            self.particles.burst(self._sprite_screen_pos(), 10, P.SPARKLE,
                                 C.BOUNCE, 180.0)
        if result.hit_vine:
            self.sprite.bump()
        for _ in result.crumbled:
            self.camera.kick(0.18)
            self.audio.play("pop", 0.5)

        if result.reached_goal:
            self._reach_goal()
        elif result.fell:
            self._die()

    def _update_descend(self, dt: float) -> None:
        f = min(1.0, self.state_time / C.DESCEND_TIME)
        ease = f * f * (3.0 - 2.0 * f)          # smoothstep

        # Ride down *and across* to the next island, so it is centred and
        # readable well before the sprite arrives on it.
        ox, oz = self._drop_offset
        self.camera.origin = (ox * ease, -DROP_HEIGHT * ease, oz * ease)

        # Swing side-on and back over the drop, so the fall itself is visible.
        self.camera.pitch = C.CAM_PITCH - 0.40 * math.sin(math.pi * f)

        # Carry the sprite to where it will actually land. Snapping it to the
        # spawn on arrival would put it somewhere you had not been watching.
        if self.next_island is not None:
            self.ball.vx = self.ball.vy = 0.0
            self.ball.x = R.lerp(self._drop_from[0], self.next_island.spawn[0], ease)
            self.ball.y = R.lerp(self._drop_from[1], self.next_island.spawn[1], ease)
        self.ball.hop = 0.0
        self.sprite.update(dt, self.ball)

        if f >= 1.0:
            assert self.next_island is not None
            self.depth += 1
            self.depth_bounce = 1.0
            self.island = self.next_island
            self.next_island = None
            self.ball = PH.Ball(float(self.island.spawn[0]), float(self.island.spawn[1]))
            self._physics_accum = 0.0
            self._drop_offset = (0.0, 0.0)
            self.camera.origin = (0.0, 0.0, 0.0)
            self.camera.pitch = C.CAM_PITCH
            self.audio.set_depth(self.depth)
            self.particles.burst(self._sprite_screen_pos(), 18, P.DUST, (226, 244, 200), 150.0)
            self._set_state(PLAY)

    def _update_dying(self, dt: float) -> None:
        self.death_fall += dt
        self.sprite.update(dt, self.ball, 1.0, dying=True)
        # Keep drifting on whatever velocity killed you, and accelerate down.
        self.ball.x += self.ball.vx * dt * 0.4
        self.ball.y += self.ball.vy * dt * 0.4

        if self.state_time >= C.DEATH_FALL_TIME:
            self.particles.pop(self._sprite_screen_pos())
            self.audio.play("pop", 0.7)
            self.is_record = self.depth > self.best
            if self.is_record:
                self.best = self.depth
                _save_best(self.best)
            self._set_state(CARD)

    def _update_card(self, dt: float) -> None:
        if self.state_time < C.CARD_INPUT_DELAY:
            self.wrist.take_flick()     # swallow the flick that killed you
            return
        if self.wrist.take_flick():
            self.start_run()

    # ── Drawing ────────────────────────────────────────────────────────────

    def _transform(self, origin=(0.0, 0.0, 0.0)) -> B.BoardTransform:
        if self.state in (PLAY, DESCEND, DYING):
            gravity = self.wrist.gravity()
        else:
            # The title island sways on its own, so the scene is never static.
            gravity = (math.cos(self.t * 0.53) * 0.13,
                       math.sin(self.t * 0.70) * 0.10,
                       -1.0)
        pitch, roll = B.pitch_roll(gravity)
        R.rotate_light(pitch, roll)
        return B.BoardTransform(gravity, origin)

    def _sprite_screen_pos(self):
        xf = self._transform()
        height = C.BALL_RADIUS + self.ball.hop
        if self.state == DYING:
            height -= (self.death_fall ** 2) * 900.0
        sx, sy, _ = self.camera.project(xf(self.ball.x, self.ball.y, height))
        return (sx, sy)

    def draw(self) -> None:
        self.screen.blit(self.sky, (0, 0))
        self.clouds.draw(self.screen)

        if self.state == TITLE:
            self._draw_title_scene()
        else:
            self._draw_run_scene()

        self.particles.draw(self.screen)
        R.vignette(self.screen)

        if self.state == TITLE:
            H.draw_title(self.screen, self.fonts, self.t, self.best,
                         waiting=not self.wrist.is_live(),
                         ready=self.wrist.face_locked)
        elif self.state == CARD:
            H.draw_hud(self.screen, self.fonts, self.depth, self.best, self.depth_bounce)
            H.draw_card(self.screen, self.fonts, self.depth, self.best,
                        self.is_record, self.state_time,
                        ready=self.state_time >= C.CARD_INPUT_DELAY)
        else:
            H.draw_hud(self.screen, self.fonts, self.depth, self.best, self.depth_bounce)
            if not self.wrist.is_live():
                H.draw_connection_lost(self.screen, self.fonts, self.t)

        if self.show_watch:
            # Raw, not corrected: this is a faithful mirror of what the watch
            # reports, so it stays useful as a reference rather than becoming
            # a second view of the game's own correction.
            WM.draw_corner(self.screen, self.watch_model, self.fonts.tiny,
                           self.wrist.raw_gravity(),
                           connecting=not self.wrist.is_live(),
                           t=self.t)

        if self.show_sensor:
            H.draw_orientation(self.screen, self.fonts, self.wrist, self.depth, self.t)

        if self.paused:
            if not hasattr(self, 'paused_background') or self.paused_background is None or self.paused_background.get_size() != self.screen.get_size():
                self.paused_background = self.screen.copy()
                H.soft_blur(self.paused_background, 5)
            self.screen.blit(self.paused_background, (0, 0))
            H.draw_pause(self.screen, self.fonts)

        pygame.display.flip()

    def _draw_title_scene(self) -> None:
        xf = self._transform()
        B.draw_island(self.painter, self.camera, self._title_island, xf, self.t)
        self._queue_sprite(xf, PH.Ball(4.0, 4.0), height=C.BALL_RADIUS)
        self.painter.flush(self.screen)

    def _draw_run_scene(self) -> None:
        assert self.island is not None
        xf = self._transform()
        B.draw_island(self.painter, self.camera, self.island, xf, self.t)

        if self.state == DESCEND and self.next_island is not None:
            ox, oz = self._drop_offset
            below = self._transform((ox, -DROP_HEIGHT, oz))
            B.draw_island(self.painter, self.camera, self.next_island, below, self.t)

        if self.state == DYING:
            fall = (self.death_fall ** 2) * 900.0
            self._queue_sprite(xf, self.ball, height=C.BALL_RADIUS - fall,
                               fading=min(1.0, self.death_fall / C.DEATH_FALL_TIME),
                               dying=True)
        elif self.state == DESCEND:
            f = min(1.0, self.state_time / C.DESCEND_TIME)
            ease = f * f * (3.0 - 2.0 * f)
            ox, oz = self._drop_offset
            falling = self._transform((ox * ease, -DROP_HEIGHT * ease + C.BALL_RADIUS,
                                       oz * ease))
            self._queue_sprite(falling, self.ball, height=0.0, happy=True)
        elif self.state != CARD:
            self._queue_sprite(xf, self.ball, height=C.BALL_RADIUS + self.ball.hop)

        self.painter.flush(self.screen)

    def _queue_sprite(self, xf: B.BoardTransform, ball: PH.Ball,
                      height: float, fading: float = 0.0, happy: bool = False, dying: bool = False) -> None:
        world = xf(ball.x, ball.y, height)
        sx, sy, depth = self.camera.project(world)
        scale = C.CAM_FOV / depth
        radius = C.BALL_RADIUS * scale
        squash = PH.contact_squash(ball)

        # Contact shadow, projected onto the board rather than the sprite.
        ground = xf(ball.x, ball.y, 0.0)
        gx, gy, gdepth = self.camera.project(ground)
        lift = max(0.0, height - C.BALL_RADIUS)
        fade = R.clamp(1.0 - lift / 340.0, 0.0, 1.0) * (1.0 - fading)

        def paint_shadow(surface: pygame.Surface) -> None:
            if fade <= 0.02:
                return
            R.shadow(surface, (gx, gy), radius * 0.95 * (0.7 + 0.3 * fade),
                     radius * 0.44 * (0.7 + 0.3 * fade), int(96 * fade))

        # Clamp shadow depth so it always sorts behind the ball outline (depth + 0.5)
        # and on top of the ground tile (gdepth)
        self.painter.custom(max(gdepth - 0.1, depth + 0.6), paint_shadow)

        self.sprite.queue(self.painter, self.camera, xf, ball, height, squash, happy, dying)

    # ── Events ─────────────────────────────────────────────────────────────

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                # Re-create the surface to handle the resize/maximize
                self.screen = pygame.display.set_mode(event.size, pygame.RESIZABLE)
                C.apply_display(*self.screen.get_size())
                self.sky = R.make_sky(C.WIDTH, C.HEIGHT)
                self.watch_model = WM.WatchModel(radius=C.px(66))
                self.fonts = H.Fonts()
                H._TEXT_CACHE.clear()
                self.paused_background = None
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.state == TITLE:
                        self.running = False
                    else:
                        self.paused = not self.paused
                        if self.paused:
                            self.audio.stop_rolling()
                            self.paused_background = None
                elif event.key == pygame.K_q and self.paused:
                    self.running = False
                elif event.key == pygame.K_d:
                    self.show_sensor = not self.show_sensor
                elif event.key == pygame.K_v:
                    self.show_watch = not self.show_watch

    def on_accel(self, packet) -> None:
        self.wrist.feed(packet)
