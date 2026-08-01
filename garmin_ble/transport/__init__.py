"""Transports: the byte pipe between :class:`~garmin_ble.watch.Watch` and a peer.

Three implementations share one contract, so the same ``Watch`` drives all of
them: :class:`~garmin_ble.transport.ble.BleTransport` for a real radio,
:class:`~garmin_ble.transport.simulated.SimulatedTransport` for an in-process
watch, and :class:`~garmin_ble.transport.replay.ReplayTransport` for a recorded
session.

``BleTransport`` is imported lazily so that importing this package — and
therefore importing :mod:`garmin_ble` — does not require bleak to be importable
on a machine with no Bluetooth stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import DisconnectCallback, LinkInfo, NotifyCallback, Transport
from .replay import ReplayTransport
from .simulated import SimulatedTransport, WatchProfile, available_profiles, get_profile

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .ble import BleTransport


def __getattr__(name: str) -> Any:
    if name in ("BleTransport", "looks_like_garmin", "GARMIN_NAME_HINTS"):
        from . import ble

        return getattr(ble, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "Transport",
    "LinkInfo",
    "NotifyCallback",
    "DisconnectCallback",
    "BleTransport",
    "SimulatedTransport",
    "ReplayTransport",
    "WatchProfile",
    "available_profiles",
    "get_profile",
]
