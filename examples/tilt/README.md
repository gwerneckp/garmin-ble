# TILT

A cel-shaded marble game where the controller is a Garmin watch. Hold your
forearm level and the island is level; tilt, and a seed-sprite rolls. Reach the
glowing tile and it drops through onto the next island below, forever, until
you fall off.

```bash
python -m examples.tilt
```

Your watch must not be connected to your phone — Garmin watches allow one BLE
connection at a time.

| flag | effect |
|------|--------|
| `--seed N` | fix the island sequence, so you can practise the same run |
| `--mute` | skip audio synthesis entirely |
| `--windowed` | run in a 1280x720 window instead of fullscreen |

`esc` pauses (`q` quits from the pause screen), `f` toggles fullscreen, `d`
shows the live sensor readout, `v` hides the watch model in the corner.

That corner model is `examples/accelerometer_3d.py` running inside the game —
same cylinder, same `rotate_3d`, same backface culling, Z-sorting and specular
shading, only smaller. It is fed the **raw** vector rather than the game's
corrected one, so it stays an honest picture of what the watch is reporting
instead of a second view of the same correction. Lie the watch flat and it goes
edge-on at `-0° +180°`, exactly as that example does.

It opens fullscreen at the panel's **true pixel resolution** rather than at the
scaled desktop size — on a Retina display that is the difference between a
crisp image and one the OS upscales for you. The whole layout is authored
against a 720p design height and multiplied up, so it is sharp at any size.

---

## The control mapping

There is no calibration step, and that is not a shortcut — it is the point.

Gravity is an absolute reference. The accelerometer reports which way down is,
in g, so "watch face level" is exactly zero tilt and cannot drift no matter how
long you play or how you shift in your seat.

It also means the physics needs no trigonometry at all. If the board's normal
is the watch's own face normal, then the in-plane component of gravity — the
part that actually rolls a ball — is exactly the `(x, y)` part of the vector
the watch already sends:

```python
ax = gravity_at(depth) * gx
ay = gravity_at(depth) * gy
```

The board's orientation is not derived here at all — it is
`examples/accelerometer_3d.py`'s maths, copied unchanged:

```python
pitch = math.atan2(-gy, math.sqrt(gx ** 2 + gz ** 2))
roll  = math.atan2(gx, gz)
```

plus that example's own `rotate_3d` and its `(x, -z, y)` camera swap. The board
is modelled the way that example models the watch — built in the XY plane with
its face normal along +Z — so the island *is* the watch face. That example is
known good on real hardware, and one convention that works beats two that have
to agree.

The physics signs (`board_tilt`) are checked against the recordings in
[`data/`](../../data/) rather than assumed: with `roll = atan2(gx, gz)` a
face-up watch sits at roll = pi, a half-turn about the vertical, and those
signs are what cancel it. A flat watch gives a board that is level to within
floating-point zero.

### Which axis is "up" is detected, not assumed

The top of the board is the big flat part — the watch face. But *which*
accelerometer axis leaves that face differs between models: the captures in
`data/` read Z = -1 lying face-up, while a watch whose face normal is Y instead
would sit at a permanent 90-degree pitch and the island would stand on its
edge. (`accelerometer_3d.py` hides this, because its `(x, -z, y)` camera swap
is itself a 90-degree rotation that happens to cancel it.)

So the game works it out. Gravity is absolute, so the axis carrying ~1g while
the other two read ~0 *is* the face normal, and it is a fixed property of the
hardware. `Wrist` watches for the first moment the watch is within about 40
degrees of level, locks that axis, and never looks again.

This is not a calibration pose. There is nothing to hold still, nothing to get
wrong, and no neutral angle being stored — a run simply will not start until
the axis is known, which takes about as long as glancing at the screen.

Press **`d`** for a live readout of the raw vector, the corrected one, and what
was detected. `FACE_AXIS` / `FACE_SIGN` in `config.py` override the detection,
and `INVERT_X` / `INVERT_Y` flip an axis pair — all applied in one function,
`Wrist.gravity()`, so the board you see and the way the ball rolls can never
disagree.

Restarting after a fall is a wrist flick, detected as a brief departure from
1g in the raw samples — deliberately measured *before* the smoothing that
feeds the tilt, since a low-pass filter is exactly the thing that would erase
a flick.

## The descent

Islands are offset sideways as well as down, so the tower is a staircase
rather than a stack. Looking straight down, an island directly beneath the one
you are leaving is completely hidden by it — the upper one is nearer, so it
paints over the top, and you would arrive on a board you had never seen. The
sprite is also carried across to the exact spot it will land on, rather than
being snapped to the spawn on arrival.

## Progression

Islands are generated with a guaranteed route: a path is carved from spawn to
goal first, and holes are only ever punched into cells that path never touches.
You can lose, but never to an uncrossable board.

New ideas arrive one at a time, each taught by the island it appears on:

| from island | hazard |
|---|---|
| 1 | pits, and the edges |
| 4 | flowering vines that sweep across and shove you |
| 7 | crumbling caps — they turn into holes 1.5s after you touch them |
| 10 | bounce pads |

Hole density ramps from 6% to 24% over the first 22 islands, then holds.

Gravity ramps too: the first island pulls at less than half strength and
reaches full by island 9. Full gravity from a standing start just flings you
off before you have read the board.

## Layout

| file | role |
|------|------|
| `__main__.py` | entry point; owns the frame loop, the library owns the link |
| `game.py` | state machine — TITLE, PLAY, DESCEND, DYING, CARD |
| `wrist.py` | accelerometer → tilt vector and flick detection |
| `physics.py` | rolling, hops, hazard collision |
| `sprite.py` | the rolling icosphere and its reactions |
| `world.py` | island generation with a guaranteed route |
| `board.py` | island → polygons |
| `render.py` | cel-shading software renderer and painter's-algorithm sorting |
| `particles.py` | bubbles, pollen, sparkles, dust, clouds |
| `audio.py` | the score, the sequencer and the SFX, synthesised at startup |
| `hud.py` | title, HUD, pause, score card |
| `watchmodel.py` | the corner watch, copied from `accelerometer_3d.py` |
| `config.py` | every tunable and the palette |

No binary assets. Every mesh, colour and sound is generated in code.

The music is **Korobeiniki** — the 1861 Russian folk song everyone knows as the
Tetris theme — played by a step sequencer. The melody is public domain; what is
owned is Nintendo's particular Game Boy *arrangement*, so the accompaniment
here is its own: a plain root-per-bar bass over Am-Am-E-Am-Dm-Am-E-Am, struck
on beats one and three, with square-wave voices synthesised at startup. The
bass joins at island 4, an offbeat tick at island 8, and the tempo creeps from
132 to 196 BPM as you descend.

## Notes on the rendering

The camera looks **straight down**. A tilted camera makes a level board *look*
tilted, and that is the one thing this game cannot afford to be ambiguous
about: a flat wrist has to read as a flat island at a glance. Perspective still
splays the island's sides outward near the frame edges, so it keeps its
thickness without ever lying about which way is level.

Cel shading here means Lambert lighting quantised into three hard bands rather
than a smooth gradient — the banding *is* the look. Faces are culled by
screen-space winding order, sorted back-to-front, and drawn as flat polygons.

The sprite is a 320-face icosphere carried through the same transform as the
board, so it is lit and depth-sorted with everything else and — the point of
making it geometry — it visibly **spins**. The rotation integrates
`(n x v) / r`, the angular velocity of a ball rolling without slipping, so the
tinted surface patches track the ground instead of sliding over it. Its
vertices are transformed once and shared between faces, since each is used by
about six of them.

Seen from straight overhead a sphere is a flat disc, so the sprite is shaded
with five bands instead of the world's three: enough to sell the curvature,
still few enough to look drawn rather than rendered. The face is deliberately
large and always looking back up at the camera — from directly above it is the
only thing saying this is a character and not a marble.

Its silhouette is a dark disc queued behind the mesh rather than an outline on
each triangle: outlining the triangles would draw every interior edge too, and
the ball would read as a wireframe instead of a drawn character. The face and
leaf stay upright on top — a face tumbling with the mesh becomes unreadable
within half a revolution.

## Smoothness

Three things were costing frames, and all three were paying for pictures that
had already been decided:

- **Outlined text** stamped a filled disc of offsets — hundreds of blits per
  string, per frame. It is now a ring of twelve, and the composed surface is
  cached, since the HUD redraws the same handful of strings forever.
- **Particles and clouds** each allocated a full-screen `SRCALPHA` surface
  every frame. At 2560x1664 that is ~17 MB a piece. The layer is now reused,
  and each cloud bank is rendered once into its own small surface and only
  ever blitted.
- At 2560x1664 that took the median frame from **12.3 ms to 8.9 ms**, and the
  95th percentile from **72 ms to 9.7 ms** — the long frames were the stutter.
  The rounder sprite and the vignette put the median back to ~11.5 ms, still
  comfortably inside the 16.7 ms budget.

Two more, on the simulation side:

- **Physics runs at a fixed 240 Hz** with an accumulator. Under a variable step
  a long frame integrates further and the ball behaves differently, which is
  felt as the controls going vague exactly when the game is busiest.
- **Tilt is eased per frame, not per packet.** Packets arrive around 25 Hz
  while the game draws at 60 or more, so moving the board only on arrival made
  it visibly step. An exponential ease with a 75 ms time constant cuts
  frame-to-frame jerk by about 8x, and being time-based it behaves the same
  whatever either rate happens to be.
