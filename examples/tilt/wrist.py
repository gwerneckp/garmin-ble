"""The watch as a controller.

Two signals come out of the accelerometer: a smoothed gravity vector (which
axis is down, and how far over you are holding your arm) and a flick, which is
a brief departure from 1g. No calibration step exists because none is needed —
gravity is an absolute reference, so "watch face level" is exactly zero tilt
and it cannot drift.
"""

from __future__ import annotations

import math
import time
from typing import Tuple

from garmin_ble import metrics

from . import config as C


class Wrist:
    def __init__(self) -> None:
        # Face-up on the wrist reads about -1g on Z, which is level.
        self.gx = 0.0
        self.gy = 0.0
        self.gz = -1.0
        # Where the last packet said gravity was. `update` eases towards it.
        self.tx = 0.0
        self.ty = 0.0
        self.tz = -1.0
        self.magnitude = 1.0
        self._flick_ready_at = 0.0
        self._flick_pending = False

        # Which axis points out of the watch face. Config wins if it is set;
        # otherwise this is locked in the first time the watch is near level.
        self.face_axis = C.FACE_AXIS if C.FACE_AXIS is not None else 2
        self.face_sign = C.FACE_SIGN if C.FACE_SIGN is not None else -1
        self.face_locked = C.FACE_AXIS is not None and C.FACE_SIGN is not None
        self.packets = 0
        self.last_packet_at = 0.0

    # ── Ingest ─────────────────────────────────────────────────────────────

    def feed(self, packet: metrics.AccelPacket) -> None:
        """Average one burst into a gravity estimate and look for a flick."""
        if not packet.samples:
            return

        count = len(packet.samples)
        ax = sum(s.g[0] for s in packet.samples) / count
        ay = sum(s.g[1] for s in packet.samples) / count
        az = sum(s.g[2] for s in packet.samples) / count

        self.tx, self.ty, self.tz = ax, ay, az

        # Flicks are judged on the *raw* burst: smoothing would erase them.
        peak = max(s.magnitude_g for s in packet.samples)
        self.magnitude = peak
        now = time.monotonic()
        if abs(peak - 1.0) > C.FLICK_THRESHOLD and now >= self._flick_ready_at:
            self._flick_ready_at = now + C.FLICK_COOLDOWN
            self._flick_pending = True

        self._detect_face_axis()

        self.packets += 1
        self.last_packet_at = now

    def update(self, dt: float) -> None:
        """Ease the smoothed vector towards the newest packet, every frame.

        Packets arrive around 25 Hz while the game draws at 60 or more, so
        moving the board only when a packet lands makes it visibly step. An
        exponential ease with a time constant runs at the frame rate instead,
        and being time-based rather than per-packet it behaves the same however
        fast either side happens to be going.
        """
        if C.TILT_RESPONSE <= 0.0:
            self.gx, self.gy, self.gz = self.tx, self.ty, self.tz
            return
        a = 1.0 - math.exp(-dt / C.TILT_RESPONSE)
        self.gx += (self.tx - self.gx) * a
        self.gy += (self.ty - self.gy) * a
        self.gz += (self.tz - self.gz) * a

    def _detect_face_axis(self) -> None:
        """Learn which axis leaves the watch face, the first time it is level.

        This is not a calibration pose — there is nothing to hold and nothing
        to get wrong. Gravity is absolute, so the axis carrying ~1g while the
        rest read ~0 *is* the face normal, and it is a fixed property of the
        hardware. Once found it never changes, so this locks and stops looking.
        """
        if self.face_locked:
            return

        raw = (self.tx, self.ty, self.tz)
        axis = max(range(3), key=lambda i: abs(raw[i]))
        value = raw[axis]
        others = max(abs(raw[i]) for i in range(3) if i != axis)

        if abs(value) < C.FACE_DETECT_MIN or others > 0.45:
            return      # too far from level to be sure yet

        self.face_axis = axis
        self.face_sign = 1 if value > 0 else -1
        self.face_locked = True

    # ── Query ──────────────────────────────────────────────────────────────

    def tilt(self) -> Tuple[float, float]:
        """In-plane gravity, in g — directly the acceleration on the board."""
        return (self.gx, self.gy)

    def gravity(self) -> Tuple[float, float, float]:
        """Smoothed gravity, rotated so Z is the watch's face normal.

        Every axis correction lives here and nowhere else, so a change in
        `config` moves the board you see and the way the ball rolls together —
        they can never end up disagreeing.

        All of them are *rotations*, never single-axis negations. Negating one
        axis mirrors the frame, and a mirrored frame renders rotations
        backwards: tilt forward, watch the island lean back.
        """
        raw = (self.gx, self.gy, self.gz)
        axis, sign = self.face_axis, self.face_sign

        # Cyclic permutation, so whichever axis is the face normal lands on Z
        # without flipping handedness.
        if axis == 2:
            gx, gy, gz = raw
        elif axis == 1:
            gx, gy, gz = raw[2], raw[0], raw[1]
        else:
            gx, gy, gz = raw[1], raw[2], raw[0]

        # A face axis reading +1 when up needs a half-turn, which is two
        # negations rather than one.
        if sign > 0:
            gx, gz = -gx, -gz

        if C.INVERT_X:
            gx, gy = -gx, -gy      # half-turn about the face normal
        if C.INVERT_Y:
            gy, gz = -gy, -gz      # half-turn about X

        return (gx, gy, gz)

    def raw_gravity(self) -> Tuple[float, float, float]:
        """The vector exactly as the watch sent it, for the `d` readout."""
        return (self.gx, self.gy, self.gz)

    def tilt_magnitude(self) -> float:
        return math.hypot(self.gx, self.gy)

    def take_flick(self) -> bool:
        """Consume a pending flick, if there is one."""
        if self._flick_pending:
            self._flick_pending = False
            return True
        return False

    def is_live(self, stale_after: float = 3.0) -> bool:
        if not self.packets:
            return False
        return (time.monotonic() - self.last_packet_at) < stale_after
