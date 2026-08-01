"""Exception hierarchy for garmin-ble.

Every failure mode that a caller might reasonably want to branch on gets its
own type, and every type carries the context needed to write a useful error
message. Nothing in the public API signals failure by returning ``False`` or
``None``.

All exceptions derive from :class:`GarminBleError`, so ``except GarminBleError``
is a valid catch-all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .metrics.base import Metric


class GarminBleError(Exception):
    """Base class for every error raised by this library."""


# ── Discovery / connection ───────────────────────────────────────────────────


class DiscoveredDevice:
    """A BLE device seen during a scan that we chose not to connect to."""

    __slots__ = ("name", "address", "rssi")

    def __init__(self, name: Optional[str], address: str, rssi: Optional[int] = None):
        self.name = name
        self.address = address
        self.rssi = rssi

    def __repr__(self) -> str:
        return f"DiscoveredDevice(name={self.name!r}, address={self.address!r})"


class WatchNotFound(GarminBleError):
    """No device matching the discovery criteria was found in range.

    ``candidates`` holds every device the scan *did* see, which is almost always
    what you need to debug this — the watch is usually there under a name the
    filter did not match, or missing because it is paired to a phone.
    """

    def __init__(self, message: str, candidates: Sequence[DiscoveredDevice] = ()):
        super().__init__(message)
        self.candidates = tuple(candidates)

    def __str__(self) -> str:
        base = super().__str__()
        if not self.candidates:
            return f"{base} (no BLE devices seen at all)"
        seen = ", ".join(f"{d.name or '<unnamed>'} [{d.address}]" for d in self.candidates[:8])
        more = "" if len(self.candidates) <= 8 else f", +{len(self.candidates) - 8} more"
        return f"{base} (saw {len(self.candidates)}: {seen}{more})"


class ConnectionFailed(GarminBleError):
    """The BLE link itself could not be established or was lost unexpectedly."""


class NotConnected(GarminBleError):
    """An operation needing a live link was attempted without one."""


# ── Protocol ────────────────────────────────────────────────────────────────


class HandshakeStage(str):
    """Named stage of the GFDI handshake, for :attr:`HandshakeError.stage`."""


CHARACTERISTIC_DISCOVERY = HandshakeStage("characteristic_discovery")
CLOSE_ALL = HandshakeStage("close_all")
REGISTER_ML = HandshakeStage("register_ml")


class HandshakeError(GarminBleError):
    """The GFDI handshake did not complete.

    ``stage`` is one of :data:`CHARACTERISTIC_DISCOVERY`, :data:`CLOSE_ALL`, or
    :data:`REGISTER_ML`, so a caller can tell "this is not a Garmin device" from
    "the watch is busy talking to a phone".
    """

    def __init__(self, message: str, stage: HandshakeStage):
        super().__init__(message)
        self.stage = stage

    def __str__(self) -> str:
        return f"[{self.stage}] {super().__str__()}"


class ServiceUnavailable(GarminBleError):
    """The watch declined, or never answered, a service registration.

    Raised by :meth:`~garmin_ble.watch.Watch.subscribe` instead of silently
    producing a stream that never yields.
    """

    def __init__(self, metric: "Metric", reason: str):
        super().__init__(f"{metric.name} unavailable: {reason}")
        self.metric = metric
        self.reason = reason


class RequestTimeout(GarminBleError):
    """A protobuf request was sent but no matching response arrived in time."""

    def __init__(self, message: str, request_id: Optional[int] = None):
        super().__init__(message)
        self.request_id = request_id


class ProtocolError(GarminBleError):
    """A frame from the watch could not be decoded or made sense of."""


class UnroutableMessage(ProtocolError):
    """A protobuf message has no unambiguous place in the ``Smart`` envelope."""


__all__ = [
    "GarminBleError",
    "DiscoveredDevice",
    "WatchNotFound",
    "ConnectionFailed",
    "NotConnected",
    "HandshakeStage",
    "HandshakeError",
    "CHARACTERISTIC_DISCOVERY",
    "CLOSE_ALL",
    "REGISTER_ML",
    "ServiceUnavailable",
    "RequestTimeout",
    "ProtocolError",
    "UnroutableMessage",
]
