"""Everything you hear, synthesised at startup into raw sample buffers.

No asset files and no numpy — ``array('h')`` plus ``pygame.mixer.Sound`` is
enough. The music is a written score played by a step sequencer, not random
notes: an original folk-minor tune in the idiom Tetris drew on, whose tempo
and arrangement thicken as you descend.

If the mixer cannot start (no audio device, CI), every call here degrades to
silence rather than raising.
"""

from __future__ import annotations

import array
import math
import random
from typing import Dict, List, Optional, Sequence

import pygame

from . import config as C

RATE = C.AUDIO_RATE

#: "Korobeiniki" — the Russian folk song from 1861 that everyone knows as the
#: Tetris theme. The melody is public domain; what is owned is Nintendo's
#: particular Game Boy *arrangement*, so this plays the traditional tune with
#: its own accompaniment and timbres rather than reproducing that.
#:
#: Written as (midi note or None for a rest, length in eighth notes).
_A4, _B4, _C5, _D5, _E5, _F5, _G5, _A5 = 69, 71, 72, 74, 76, 77, 79, 81

MELODY = (
    (_E5, 2), (_B4, 1), (_C5, 1), (_D5, 2), (_C5, 1), (_B4, 1),
    (_A4, 2), (_A4, 1), (_C5, 1), (_E5, 2), (_D5, 1), (_C5, 1),
    (_B4, 3), (_C5, 1), (_D5, 2), (_E5, 2),
    (_C5, 2), (_A4, 2), (_A4, 2), (None, 2),

    (_D5, 3), (_F5, 1), (_A5, 2), (_G5, 1), (_F5, 1),
    (_E5, 3), (_C5, 1), (_E5, 2), (_D5, 1), (_C5, 1),
    (_B4, 2), (_B4, 1), (_C5, 1), (_D5, 2), (_E5, 2),
    (_C5, 2), (_A4, 2), (_A4, 2), (None, 2),
)

#: My own accompaniment: one root per bar over Am-Am-E-Am-Dm-Am-E-Am, struck
#: on beats one and three.
_A2, _E2, _D3 = 45, 40, 50
BASS_ROOTS = (_A2, _A2, _E2, _A2, _D3, _A2, _E2, _A2)

STEPS_PER_BAR = 8


def _expand(melody):
    """(note, length) pairs -> one entry per eighth, None where a note holds."""
    steps = []
    for note, length in melody:
        steps.append(note)
        steps.extend([None] * (length - 1))
    return tuple(steps)


MELODY_STEPS = _expand(MELODY)

#: Square-ish: odd harmonics only, which is what gives it the chip timbre.
LEAD_HARMONICS = (1.0, 0.0, 0.34, 0.0, 0.20, 0.0, 0.14)
BASS_HARMONICS = (1.0, 0.42, 0.18, 0.08)


def _midi_hz(note: int) -> float:
    return 440.0 * (2.0 ** ((note - 69) / 12.0))


def _envelope(i: int, n: int, attack: float, release: float) -> float:
    a = max(1, int(n * attack))
    r = max(1, int(n * release))
    if i < a:
        return i / a
    if i > n - r:
        return max(0.0, (n - i) / r)
    return 1.0


def _to_sound(samples: Sequence[float], volume: float = 1.0) -> Optional[pygame.mixer.Sound]:
    if not pygame.mixer.get_init():
        return None
    buf = array.array("h")
    peak = max((abs(s) for s in samples), default=1.0) or 1.0
    scale = 32767.0 * volume / peak
    for s in samples:
        v = int(s * scale)
        buf.append(-32768 if v < -32768 else 32767 if v > 32767 else v)
    return pygame.mixer.Sound(buffer=buf.tobytes())


_TABLE_SIZE = 2048
_tables: Dict[Sequence[float], List[float]] = {}


def _wavetable(harmonics: Sequence[float]) -> List[float]:
    """One cycle with the harmonics baked in, cached per timbre.

    Calling ``math.sin`` per sample per harmonic costs seconds of startup for
    a bank this size. Building one cycle and reading it with a phase
    accumulator turns synthesis into an index and an add.
    """
    key = tuple(harmonics)
    table = _tables.get(key)
    if table is not None:
        return table

    table = [0.0] * _TABLE_SIZE
    for h, amp in enumerate(harmonics, start=1):
        if amp <= 0.0:
            continue
        step = 2.0 * math.pi * h / _TABLE_SIZE
        for i in range(_TABLE_SIZE):
            table[i] += amp * math.sin(step * i)
    _tables[key] = table
    return table


def _tone(freq: float, duration: float, *, harmonics: Sequence[float] = (1.0, 0.35, 0.14),
          attack: float = 0.06, release: float = 0.55, detune: float = 0.0) -> List[float]:
    table = _wavetable(harmonics)
    n = int(RATE * duration)
    out = [0.0] * n

    inc = freq * _TABLE_SIZE / RATE
    inc2 = inc * (1.0 + detune)
    phase = 0.0
    phase2 = 0.0
    size = _TABLE_SIZE

    for i in range(n):
        v = table[int(phase) & (size - 1)]
        if detune:
            v += table[int(phase2) & (size - 1)]
        out[i] = v * _envelope(i, n, attack, release)
        phase += inc
        phase2 += inc2
    return out


def _noise(duration: float, rng: random.Random, *, cutoff: float = 0.25,
           attack: float = 0.02, release: float = 0.3) -> List[float]:
    """Low-passed noise via a one-pole filter — cheap and warm enough."""
    n = int(RATE * duration)
    out = [0.0] * n
    prev = 0.0
    for i in range(n):
        prev += (rng.uniform(-1.0, 1.0) - prev) * cutoff
        out[i] = prev
    for i in range(n):
        out[i] *= _envelope(i, n, attack, release)
    return out


class Audio:
    """Owns the mixer, the sound bank, and the generative music scheduler."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = False
        self.sounds: Dict[str, pygame.mixer.Sound] = {}
        self.rng = random.Random(7)
        self._beat = 0.0
        self._step = 0
        self._tempo = C.MUSIC_TEMPO
        self._layers = 1
        self._roll_channel: Optional[pygame.mixer.Channel] = None
        self._music_gain = 1.0
        self._duck = 0.0

        if not enabled:
            return
        try:
            pygame.mixer.pre_init(RATE, -16, 1, 512)
            pygame.mixer.init(RATE, -16, 1, 512)
            pygame.mixer.set_num_channels(24)
        except pygame.error:
            return

        self.enabled = True
        self._build()

    # ── Bank ───────────────────────────────────────────────────────────────

    def _build(self) -> None:
        rng = self.rng

        # Only the pitches the score actually uses, so startup stays under a
        # second rather than synthesising a full chromatic bank.
        for note in sorted({n for n, _ in MELODY if n is not None}):
            snd = _to_sound(_tone(_midi_hz(note), 0.44, harmonics=LEAD_HARMONICS,
                                  attack=0.006, release=0.40), 0.22)
            if snd:
                self.sounds[f"lead{note}"] = snd

        for note in sorted(set(BASS_ROOTS)):
            snd = _to_sound(_tone(_midi_hz(note), 0.46, harmonics=BASS_HARMONICS,
                                  attack=0.008, release=0.55), 0.30)
            if snd:
                self.sounds[f"bass{note}"] = snd

        # A soft tick on the offbeat, which is what makes it feel like a dance
        # rather than a sequence of notes.
        snd = _to_sound(_noise(0.06, rng, cutoff=0.7, attack=0.02, release=0.75), 0.14)
        if snd:
            self.sounds["tick"] = snd

        # A bright bell for reaching the goal.
        for i, step in enumerate((0, 4, 7, 12)):
            samples = _tone(_midi_hz(72 + step), 0.7,
                            harmonics=(1.0, 0.5, 0.28, 0.12),
                            attack=0.005, release=0.8)
            snd = _to_sound(samples, 0.34)
            if snd:
                self.sounds[f"chime{i}"] = snd

        # Rolling rumble, three brightnesses to crossfade between by speed.
        for i, cutoff in enumerate((0.07, 0.16, 0.33)):
            snd = _to_sound(_noise(1.0, rng, cutoff=cutoff, attack=0.05, release=0.05), 0.5)
            if snd:
                self.sounds[f"roll{i}"] = snd

        whoosh = _noise(0.85, rng, cutoff=0.5, attack=0.12, release=0.7)
        for i in range(len(whoosh)):          # sweep it downward as it falls
            whoosh[i] *= 1.0 - 0.55 * (i / len(whoosh))
        snd = _to_sound(whoosh, 0.42)
        if snd:
            self.sounds["whoosh"] = snd

        snd = _to_sound(_tone(_midi_hz(57), 1.5, harmonics=(1.0, 0.3, 0.1),
                              attack=0.01, release=0.75), 0.36)
        if snd:
            self.sounds["death"] = snd

        snd = _to_sound(_tone(_midi_hz(79), 0.16, harmonics=(1.0, 0.4),
                              attack=0.01, release=0.6), 0.22)
        if snd:
            self.sounds["blip"] = snd

        snd = _to_sound(_tone(_midi_hz(84), 0.22, harmonics=(1.0, 0.6, 0.3),
                              attack=0.005, release=0.7), 0.26)
        if snd:
            self.sounds["bounce"] = snd

        pop = _noise(0.2, rng, cutoff=0.65, attack=0.005, release=0.8)
        snd = _to_sound(pop, 0.3)
        if snd:
            self.sounds["pop"] = snd

    # ── Playback ───────────────────────────────────────────────────────────

    def play(self, name: str, volume: float = 1.0) -> None:
        if not self.enabled:
            return
        snd = self.sounds.get(name)
        if snd is None:
            return
        channel = pygame.mixer.find_channel(True)
        if channel:
            channel.set_volume(volume)
            channel.play(snd)

    def goal_arpeggio(self) -> None:
        """Fired as four scheduled chimes rather than one baked sound, so the
        rise is audible even if the drop transition is cut short."""
        if not self.enabled:
            return
        for i in range(4):
            snd = self.sounds.get(f"chime{i}")
            if snd:
                ch = pygame.mixer.find_channel(True)
                if ch:
                    ch.set_volume(0.9 - i * 0.12)
                    ch.play(snd, fade_ms=int(10 + i * 55))

    def rolling(self, speed_fraction: float) -> None:
        """Keep one looping channel whose volume and brightness track speed."""
        if not self.enabled:
            return
        idx = 0 if speed_fraction < 0.33 else 1 if speed_fraction < 0.7 else 2
        snd = self.sounds.get(f"roll{idx}")
        if snd is None:
            return
        vol = min(0.55, speed_fraction * 0.75)
        if self._roll_channel is None or not self._roll_channel.get_busy():
            self._roll_channel = pygame.mixer.find_channel(True)
            if self._roll_channel:
                self._roll_channel.play(snd, loops=-1)
        if self._roll_channel:
            self._roll_channel.set_volume(vol)

    def stop_rolling(self) -> None:
        if self._roll_channel:
            self._roll_channel.stop()
            self._roll_channel = None

    def duck(self, seconds: float = 1.4) -> None:
        """Pull the music down so a death chime can be heard over it."""
        self._duck = seconds

    def set_depth(self, depth: int) -> None:
        """Deeper runs get more of the arrangement, and a little more urgency."""
        self._layers = 1 + min(2, depth // C.MUSIC_LAYER_EVERY)
        self._tempo = min(C.MUSIC_TEMPO_MAX,
                          C.MUSIC_TEMPO + depth * C.MUSIC_TEMPO_PER_ISLAND)

    def update_music(self, dt: float) -> None:
        """Advance the step sequencer.

        A fixed score rather than random notes: a tune you can hum is the
        whole difference between "there is music" and "there is a soundtrack".
        """
        if not self.enabled:
            return
        if self._duck > 0.0:
            self._duck = max(0.0, self._duck - dt)
        self._music_gain = 0.3 if self._duck > 0.0 else 1.0

        self._beat -= dt
        if self._beat > 0.0:
            return

        step_seconds = 30.0 / max(1.0, self._tempo)   # one eighth note
        self._beat += step_seconds

        step = self._step
        self._step = (self._step + 1) % len(MELODY_STEPS)

        note = MELODY_STEPS[step]
        if note is not None:
            snd = self.sounds.get(f"lead{note}")
            if snd:
                ch = pygame.mixer.find_channel(True)
                if ch:
                    ch.set_volume(self._music_gain * 0.62)
                    ch.play(snd)

        if self._layers >= 2 and step % 4 == 0:
            root = BASS_ROOTS[(step // STEPS_PER_BAR) % len(BASS_ROOTS)]
            snd = self.sounds.get(f"bass{root}")
            if snd:
                ch = pygame.mixer.find_channel(True)
                if ch:
                    ch.set_volume(self._music_gain * 0.52)
                    ch.play(snd)

        if self._layers >= 3 and step % 2 == 1:
            snd = self.sounds.get("tick")
            if snd:
                ch = pygame.mixer.find_channel(True)
                if ch:
                    ch.set_volume(self._music_gain * 0.40)
                    ch.play(snd)
