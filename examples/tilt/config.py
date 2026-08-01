"""Tunables and palette for TILT.

Everything a designer would want to touch lives here, so the rest of the
package can read like game logic instead of magic numbers.
"""

from __future__ import annotations

# ── Window ─────────────────────────────────────────────────────────────────
#
# The game is laid out against a 720p design height and then scaled to
# whatever the display actually is. On a Retina panel that means rendering at
# the true pixel resolution (2560x1664, say) rather than at 720p and letting
# the OS upscale it — which is what makes it look soft.

DESIGN_HEIGHT = 720

WIDTH, HEIGHT = 1280, 720
UI = 1.0                   # display height / DESIGN_HEIGHT; set at startup
FPS = 60
TITLE = "TILT"


def apply_display(width: int, height: int) -> None:
    """Adopt the real surface size and rescale everything derived from it."""
    global WIDTH, HEIGHT, UI, CAM_FOV, CAM_HEIGHT
    WIDTH, HEIGHT = width, height
    UI = height / DESIGN_HEIGHT
    CAM_FOV = CAM_FOV_BASE * UI
    CAM_HEIGHT = CAM_HEIGHT_BASE * UI


def px(value: float) -> int:
    """Scale a design-resolution pixel measurement to the real display."""
    return max(1, int(round(value * UI)))

# ── Palette ────────────────────────────────────────────────────────────────
#
# Cel shading only ever multiplies these by a small set of fixed bands, so the
# base colours are chosen bright: the shading darkens, it never brightens.

SKY_TOP = (96, 196, 255)
SKY_MID = (162, 228, 255)
SKY_HORIZON = (255, 226, 173)

CLOUD = (255, 253, 246)

GRASS = (128, 216, 80)
GRASS_ALT = (146, 226, 96)  # checker variation
DIRT = (156, 110, 66)
DIRT_DARK = (118, 80, 48)
PIT = (38, 46, 52)
PIT_WALL = (86, 62, 44)

GOAL = (255, 208, 72)
GOAL_GLOW = (255, 240, 168)

SPROUT_BODY = (255, 236, 130)
SPROUT_SPOT = (246, 214, 96)    # tinted faces, so the roll is visible
SPROUT_LEAF = (126, 220, 92)
SPROUT_LEAF_LIT = (176, 240, 132)
SPROUT_EYE = (44, 40, 58)
SPROUT_BLUSH = (255, 148, 150)

VINE = (108, 196, 92)
VINE_FLOWER = (255, 138, 176)
CRUMBLE = (226, 170, 108)
CRUMBLE_WARN = (240, 122, 96)
BOUNCE = (128, 172, 255)

OUTLINE = (40, 40, 56)
INK = (52, 48, 68)
PAPER = (255, 253, 244)

#: Card colours. The score used to be gold on cream, which is barely any
#: contrast at all; it is now ink on cream with the gold kept as an accent.
CARD_SCORE = (62, 52, 84)
CARD_PLATE = (28, 32, 48)
VIGNETTE = 78             # corner darkening, 0 disables

# Cel shading bands: (intensity threshold, multiplier). Highest first match.
CEL_BANDS = ((0.72, 1.00), (0.34, 0.84), (0.00, 0.63))

#: More bands, for the sprite. Seen from straight above a sphere is a flat
#: disc, so three hard steps read as a 2D circle; five sell the curvature
#: while still looking drawn rather than rendered.
CEL_BANDS_SOFT = (
    (0.86, 1.10), (0.66, 1.00), (0.44, 0.90), (0.24, 0.80), (0.00, 0.70),
)

#: Direction the sun comes from, in world space. Normalised on use.
LIGHT_DIR = (-0.34, -0.86, -0.38)

#: Warm bounce added to surfaces facing the light, for that sunlit look.
RIM_WARMTH = (26, 18, 0)

# ── Camera ─────────────────────────────────────────────────────────────────

#: Straight down. A tilted camera makes a level board *look* tilted, which
#: is the one thing this game cannot afford to be ambiguous about: flat
#: wrist must read as flat island at a glance.
CAM_PITCH = 1.5708
CAM_DISTANCE = 1600.0

#: Design-resolution values; `apply_display` scales them to the real surface.
CAM_HEIGHT_BASE = -6.0
CAM_FOV_BASE = 1150.0
CAM_HEIGHT = CAM_HEIGHT_BASE
CAM_FOV = CAM_FOV_BASE

#: The camera leans this many radians *against* the board tilt, and eases in
#: as the sprite picks up speed. Subtle on purpose — it should read as life,
#: not as a second control axis.
CAM_COUNTER_LEAN = 0.075
CAM_SPEED_DOLLY = 70.0     # px of dolly-in at full speed
CAM_LERP = 0.12

# ── Board ──────────────────────────────────────────────────────────────────

GRID = 9                   # islands are GRID x GRID cells
CELL = 88.0                # world units per cell
SLAB_DEPTH = 52.0          # thickness of the island slab

# ── Physics ────────────────────────────────────────────────────────────────
#
# The watch reports gravity in g. On a plane whose normal is the watch's own
# face normal, the in-plane component of gravity is exactly the (x, y) part of
# that vector — so tilt maps to acceleration with no trigonometry at all.

#: World units/s^2 at 1g of in-plane tilt. The first islands are deliberately
#: gentler — you are still learning how far your wrist has to move, and full
#: gravity there just flings you off before you have read the board.
GRAVITY_START = 1100.0
GRAVITY_FULL = 2400.0
GRAVITY_RAMP = 8           # islands taken to reach full gravity

FRICTION = 1.9             # velocity damping per second
MAX_SPEED = 1250.0
BALL_RADIUS = 38.0


def gravity_at(depth: int) -> float:
    t = min(1.0, max(0, depth - 1) / GRAVITY_RAMP)
    return GRAVITY_START + (GRAVITY_FULL - GRAVITY_START) * t

#: Which accelerometer axis points out of the watch face ("the big flat part"),
#: and what that axis reads when the face points at the sky.
#:
#: `None` auto-detects, which is what you want: mounting differs between
#: models. The captures in `data/` read Z = -1 lying face-up, but a watch whose
#: face normal is Y instead would sit at a 90-degree pitch forever and the
#: island would stand on its edge. Detection just watches for the first moment
#: the watch is roughly level and takes the axis carrying gravity — no pose to
#: hold, no prompt, and nothing to drift.
FACE_AXIS = None           # None = auto, else 0 = x, 1 = y, 2 = z
FACE_SIGN = None           # None = auto, else -1 or +1

#: How close to level the watch must be before auto-detection will commit.
#: 0.75 g on one axis is within about 41 degrees of flat.
FACE_DETECT_MIN = 0.75

#: Sign flips, in case a given watch is worn with the other orientation.
INVERT_X = False
INVERT_Y = False

#: Time constant of the tilt ease, in seconds. Lower is snappier and jitterier,
#: higher is smoother and laggier. 0 follows the packets exactly.
TILT_RESPONSE = 0.075

#: Physics runs at this fixed rate regardless of frame rate, so a long frame
#: cannot change how the ball behaves — only how often you see it.
PHYSICS_HZ = 240.0

#: A wrist flick is a departure from 1g by this much, in g.
FLICK_THRESHOLD = 0.62
FLICK_COOLDOWN = 0.45

# ── Progression ────────────────────────────────────────────────────────────

HAZARD_VINE_FROM = 4
HAZARD_CRUMBLE_FROM = 7
HAZARD_BOUNCE_FROM = 10

#: Hole density ramps from the first value to the second across this many
#: islands, then holds.
HOLES_START = 0.06
HOLES_END = 0.24
HOLES_RAMP = 22

# ── Timing ─────────────────────────────────────────────────────────────────

DESCEND_TIME = 1.25        # seconds of the drop-through transition
DEATH_SLOWMO = 0.32        # time scale while falling to your doom
DEATH_FALL_TIME = 0.8      # before the score card appears
CARD_INPUT_DELAY = 0.35    # grace so the killing flick can't insta-restart

# ── Audio ──────────────────────────────────────────────────────────────────

AUDIO_RATE = 22050

#: Beats per minute of the melody, which creeps up as you descend.
MUSIC_TEMPO = 132.0
MUSIC_TEMPO_PER_ISLAND = 1.6
MUSIC_TEMPO_MAX = 196.0
MUSIC_LAYER_EVERY = 4      # islands per added arrangement layer
