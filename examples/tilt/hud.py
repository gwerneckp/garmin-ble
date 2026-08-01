"""Title card, in-run HUD, pause and the death card.

Text is drawn with a chunky dark outline and a drop shadow, which is what
stops flat UI from dissolving into a bright, busy background.
"""

from __future__ import annotations

import math
from typing import Sequence, Tuple

import pygame

from . import config as C
from . import render as R

_FONT_STACK = "futura,avenirnext,helveticaneue,verdana,arial"


class Fonts:
    def __init__(self) -> None:
        self.huge = pygame.font.SysFont(_FONT_STACK, C.px(128), bold=True)
        self.big = pygame.font.SysFont(_FONT_STACK, C.px(62), bold=True)
        self.mid = pygame.font.SysFont(_FONT_STACK, C.px(34), bold=True)
        self.small = pygame.font.SysFont(_FONT_STACK, C.px(24), bold=True)
        self.tiny = pygame.font.SysFont(_FONT_STACK, C.px(18), bold=True)
        self.mono = pygame.font.SysFont("menlo,monaco,couriernew", C.px(16), bold=True)


#: Composed text surfaces, keyed by everything that affects the pixels.
#: Outlining by brute force costs hundreds of blits per string per frame; the
#: HUD redraws the same handful of strings forever, so compose once and keep.
_TEXT_CACHE: dict = {}
_TEXT_CACHE_MAX = 256

#: Directions the outline is stamped in. A filled disc of offsets is O(t^2)
#: blits for no visible gain over a ring of twelve.
_RING = tuple((math.cos(math.tau * i / 12), math.sin(math.tau * i / 12))
              for i in range(12))


def _compose(font: pygame.font.Font, text: str, color, outline, thickness, shadow):
    key = (id(font), text, tuple(color), tuple(outline), thickness, shadow)
    surf = _TEXT_CACHE.get(key)
    if surf is not None:
        return surf

    body = font.render(text, True, color)
    drop = thickness + C.px(5)
    pad = thickness + 2
    size = (body.get_width() + pad * 2, body.get_height() + pad * 2 + (drop if shadow else 0))
    surf = pygame.Surface(size, pygame.SRCALPHA)

    if shadow:
        dark = font.render(text, True, (0, 0, 0))
        dark.set_alpha(46)
        surf.blit(dark, (pad, pad + drop))

    if thickness > 0:
        edge = font.render(text, True, outline)
        for dx, dy in _RING:
            surf.blit(edge, (pad + dx * thickness, pad + dy * thickness))

    surf.blit(body, (pad, pad))

    if len(_TEXT_CACHE) >= _TEXT_CACHE_MAX:
        _TEXT_CACHE.clear()
    _TEXT_CACHE[key] = surf
    return surf


def outlined(surface: pygame.Surface, font: pygame.font.Font, text: str,
             center: Tuple[float, float], color: Sequence[int],
             outline: Sequence[int] = C.OUTLINE, thickness: int = 3,
             shadow: bool = True) -> pygame.Rect:
    composed = _compose(font, text, color, outline, C.px(thickness), shadow)
    rect = composed.get_rect(center=(int(center[0]), int(center[1])))
    surface.blit(composed, rect)
    return rect


def panel(surface: pygame.Surface, rect: pygame.Rect, *, alpha: int = 244,
          radius: int = 26) -> None:
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    local = layer.get_rect()
    radius = C.px(radius)
    pygame.draw.rect(layer, (*C.PAPER, alpha), local, border_radius=radius)
    pygame.draw.rect(layer, (*C.OUTLINE, 255), local, width=C.px(5), border_radius=radius)
    surface.blit(layer, rect.topleft)


# ── In-run HUD ─────────────────────────────────────────────────────────────

def draw_hud(surface: pygame.Surface, fonts: Fonts, depth: int, best: int) -> None:
    outlined(surface, fonts.tiny, "ISLAND", (C.px(96), C.px(44)), (255, 255, 255), thickness=2)
    outlined(surface, fonts.big, str(depth), (C.px(96), C.px(100)), C.PAPER, thickness=4)

    if best:
        outlined(surface, fonts.tiny, "BEST", (C.WIDTH - C.px(96), C.px(44)),
                 (255, 255, 255), thickness=2)
        outlined(surface, fonts.mid, str(best), (C.WIDTH - C.px(96), C.px(92)),
                 C.GOAL_GLOW, thickness=3)


def draw_orientation(surface: pygame.Surface, fonts: Fonts, wrist, depth: int) -> None:
    """Live sensor readout, toggled with `d`.

    Face-up on a desk should read (+0.00, +0.00, -1.00) and level 0.0 deg. If
    your watch reads something else, that is the number to report.
    """
    raw = wrist.raw_gravity()
    gx, gy, gz = wrist.gravity()
    lean = math.degrees(math.asin(min(1.0, max(-1.0, math.hypot(gx, gy)))))
    biggest = max(range(3), key=lambda i: abs(raw[i]))
    want_sign = 1 if raw[biggest] > 0 else -1
    lines = [
        f"raw    x {raw[0]:+.3f}   y {raw[1]:+.3f}   z {raw[2]:+.3f}",
        f"used   x {gx:+.3f}   y {gy:+.3f}   z {gz:+.3f}",
        f"lean {lean:5.1f} deg    packets {wrist.packets}    island {depth}",
        "",
        "LAY THE WATCH FLAT, FACE UP:",
        f"  used should read   x +0.000  y +0.000  z -1.000",
        f"  biggest raw axis is {'xyz'[biggest]} ({raw[biggest]:+.2f})",
        f"  face axis detected: {'xyz'[wrist.face_axis]} sign {wrist.face_sign:+d}"
        f"   locked {wrist.face_locked}",
    ]
    pad = C.px(14)
    height = fonts.mono.get_height() * len(lines) + pad * 2
    rect = pygame.Rect(C.px(24), C.HEIGHT - height - C.px(24), C.px(620), height)
    layer = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(layer, (12, 16, 22, 200), layer.get_rect(), border_radius=C.px(10))
    surface.blit(layer, rect.topleft)
    for i, line in enumerate(lines):
        surface.blit(fonts.mono.render(line, True, (150, 255, 190)),
                     (rect.x + pad, rect.y + pad + i * fonts.mono.get_height()))


def draw_connection_lost(surface: pygame.Surface, fonts: Fonts, t: float) -> None:
    """Shown while the library is reconnecting — the run keeps its state."""
    pulse = 0.5 + 0.5 * math.sin(t * 4.0)
    rect = pygame.Rect(0, 0, C.px(520), C.px(92))
    rect.center = (C.WIDTH // 2, C.HEIGHT - C.px(84))
    panel(surface, rect, alpha=int(200 + 40 * pulse))
    outlined(surface, fonts.small, "reconnecting to your watch...",
             rect.center, C.INK, thickness=2, shadow=False)


# ── Title ──────────────────────────────────────────────────────────────────

def draw_title(surface: pygame.Surface, fonts: Fonts, t: float, best: int,
               waiting: bool, ready: bool = True) -> None:
    bob = math.sin(t * 1.5) * C.px(9)

    outlined(surface, fonts.huge, "TILT", (C.WIDTH * 0.5, C.HEIGHT * 0.26 + bob),
             C.GOAL_GLOW, thickness=6)

    if waiting:
        sub = "connecting to your watch..."
    elif not ready:
        sub = "hold your watch level for a moment"
    else:
        sub = "tilt your wrist to begin"
    alpha_pulse = 0.55 + 0.45 * math.sin(t * 3.0)
    color = R.mix(C.PAPER, C.GOAL, 1.0 - alpha_pulse)
    outlined(surface, fonts.mid, sub, (C.WIDTH * 0.5, C.HEIGHT * 0.80), color, thickness=3)

    if best:
        outlined(surface, fonts.small, f"BEST  {best}",
                 (C.WIDTH * 0.5, C.HEIGHT * 0.88), C.PAPER, thickness=2)

    outlined(surface, fonts.tiny, "d sensor  ·  v watch  ·  f fullscreen  ·  esc to quit",
             (C.WIDTH * 0.5, C.HEIGHT - C.px(34)), (255, 255, 255), thickness=2)


# ── Death card ─────────────────────────────────────────────────────────────

def draw_card(surface: pygame.Surface, fonts: Fonts, depth: int, best: int,
              is_record: bool, t: float, ready: bool) -> None:
    ease = min(1.0, t / 0.28)
    ease = 1.0 - (1.0 - ease) ** 3          # ease-out cubic

    rect = pygame.Rect(0, 0, C.px(560), C.px(340))
    rect.center = (C.WIDTH // 2, int(C.HEIGHT * 0.5 + (1.0 - ease) * C.px(90)))

    # A firmer veil: the card used to sit on a bright sky, and every line on
    # and around it was fighting the background for contrast.
    faded = pygame.Surface((C.WIDTH, C.HEIGHT), pygame.SRCALPHA)
    faded.fill((14, 20, 30, int(165 * ease)))
    surface.blit(faded, (0, 0))

    panel(surface, rect)

    cx = rect.centerx
    top = rect.top

    if is_record:
        wobble = math.sin(t * 7.0) * C.px(3)
        outlined(surface, fonts.small, "NEW BEST!", (cx, top + C.px(54) + wobble),
                 C.GOAL, thickness=3)
    else:
        outlined(surface, fonts.tiny, "YOU FELL", (cx, top + C.px(54)), C.INK,
                 thickness=0, shadow=False)

    outlined(surface, fonts.tiny, "ISLAND", (cx, top + C.px(116)), C.INK,
             thickness=0, shadow=False)
    # Ink on cream, not gold on cream — the score is the one number that has
    # to be legible from across the room.
    outlined(surface, fonts.huge, str(depth), (cx, top + C.px(186)),
             C.CARD_SCORE, thickness=0, shadow=False)

    outlined(surface, fonts.small, f"best  {best}", (cx, top + C.px(266)), C.INK,
             thickness=0, shadow=False)

    if ready:
        pulse = 0.5 + 0.5 * math.sin(t * 5.0)
        label = "flick your wrist to try again"
        text = fonts.small.render(label, True, C.PAPER)
        pill = pygame.Rect(0, 0, text.get_width() + C.px(52),
                           text.get_height() + C.px(24))
        pill.center = (cx, rect.bottom + C.px(52))

        plate = pygame.Surface(pill.size, pygame.SRCALPHA)
        pygame.draw.rect(plate, (*C.CARD_PLATE, 230), plate.get_rect(),
                         border_radius=pill.height // 2)
        pygame.draw.rect(plate, (*C.GOAL, int(90 + 120 * pulse)), plate.get_rect(),
                         width=C.px(3), border_radius=pill.height // 2)
        surface.blit(plate, pill.topleft)
        surface.blit(text, text.get_rect(center=pill.center))


# ── Pause ──────────────────────────────────────────────────────────────────

def draw_pause(surface: pygame.Surface, fonts: Fonts) -> None:
    veil = pygame.Surface((C.WIDTH, C.HEIGHT), pygame.SRCALPHA)
    veil.fill((10, 18, 26, 132))
    surface.blit(veil, (0, 0))
    outlined(surface, fonts.big, "PAUSED", (C.WIDTH * 0.5, C.HEIGHT * 0.44),
             C.PAPER, thickness=4)
    outlined(surface, fonts.small, "esc to resume  ·  q to quit",
             (C.WIDTH * 0.5, C.HEIGHT * 0.56), C.PAPER, thickness=2)


def soft_blur(surface: pygame.Surface, amount: int = 6) -> None:
    """A cheap blur: downscale, upscale. Good enough behind a veil."""
    if amount <= 1:
        return
    w, h = surface.get_size()
    small = pygame.transform.smoothscale(surface, (max(1, w // amount), max(1, h // amount)))
    surface.blit(pygame.transform.smoothscale(small, (w, h)), (0, 0))
