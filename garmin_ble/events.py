"""Typed events emitted by a :class:`~garmin_ble.watch.Watch`.

Everything that happens to the link or arrives outside the telemetry streams
surfaces here as a dataclass, consumable with::

    async for event in watch.events():
        if isinstance(event, Disconnected):
            ...

Each event names what happened and carries the fields that describe it, so
handling one never means decoding a pair of bare integers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from .constants import SystemEventType

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .metrics.base import Metric


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


@dataclass(frozen=True)
class WatchEvent:
    """Base class for link and protocol events."""

    at: datetime = field(default_factory=_now, compare=False, repr=False)


@dataclass(frozen=True)
class Connected(WatchEvent):
    """The BLE link is up and the GFDI handshake has completed."""

    address: str = ""
    name: Optional[str] = None

    def __str__(self) -> str:
        return f"connected to {self.name or self.address}"


@dataclass(frozen=True)
class DeviceIdentified(WatchEvent):
    """The watch reported its real model and firmware.

    Arrives shortly after connecting, and upgrades ``watch.info`` in place —
    before it, ``info.model`` is only the advert name and ``info.firmware`` is
    unknown.
    """

    info: object = None

    def __str__(self) -> str:
        return f"identified as {self.info}"


@dataclass(frozen=True)
class Disconnected(WatchEvent):
    """The BLE link dropped.

    ``expected`` distinguishes a clean shutdown from a link the watch or the OS
    tore down underneath us — the latter is what triggers reconnection.
    """

    reason: str = "unknown"
    expected: bool = False

    def __str__(self) -> str:
        return f"disconnected ({self.reason})"


@dataclass(frozen=True)
class Reconnected(WatchEvent):
    """The link came back after a drop and subscriptions were restored."""

    attempts: int = 0
    restored: int = 0

    def __str__(self) -> str:
        return f"reconnected after {self.attempts} attempt(s), {self.restored} subscription(s) restored"


@dataclass(frozen=True)
class ServiceRegistered(WatchEvent):
    """The watch assigned an MLR handle to a service."""

    service: int = 0
    handle: int = 0
    metric: Optional["Metric"] = None

    def __str__(self) -> str:
        what = self.metric.name if self.metric else f"service {self.service}"
        return f"{what} registered on handle 0x{self.handle:02x}"


@dataclass(frozen=True)
class MetricUnavailable(WatchEvent):
    """A metric was asked for and the watch declined it.

    Emitted where a refusal would otherwise be swallowed — chiefly
    :meth:`~garmin_ble.watch.Watch.stream_all`, which skips what it cannot get
    and keeps going. Callers that need to know what they are not receiving
    should watch for this rather than scrape the log.
    """

    metric: Optional["Metric"] = None
    reason: str = ""

    def __str__(self) -> str:
        name = self.metric.name if self.metric else "metric"
        return f"{name} unavailable: {self.reason}"


@dataclass(frozen=True)
class TimeSyncRequested(WatchEvent):
    """The watch asked for the current time; the library has already answered."""

    def __str__(self) -> str:
        return "watch requested time sync (auto-answered)"


@dataclass(frozen=True)
class TimeSyncSent(WatchEvent):
    """We pushed the current time to the watch, either on heartbeat or on demand."""

    def __str__(self) -> str:
        return "time pushed to watch"


@dataclass(frozen=True)
class SystemEvent(WatchEvent):
    """A GFDI system event from the watch.

    ``kind`` is the decoded :class:`~garmin_ble.constants.SystemEventType` when
    the value is one we know, and the raw integer otherwise.
    """

    event_type: int = 0
    value: int = 0

    @property
    def kind(self) -> object:
        try:
            return SystemEventType(self.event_type)
        except ValueError:
            return self.event_type

    def __str__(self) -> str:
        return f"system event {self.kind} value={self.value}"


@dataclass(frozen=True)
class ProtobufReceived(WatchEvent):
    """A protobuf message arrived, after correlation and dispatch.

    ``message`` is the innermost service message, not the ``Smart`` envelope.
    ``handled`` says whether a ``@watch.responds_to`` handler or a pending
    :meth:`~garmin_ble.watch.Watch.request` claimed it.
    """

    request_id: int = 0
    message: object = None
    handled: bool = False

    def __str__(self) -> str:
        name = type(self.message).__name__ if self.message is not None else "?"
        return f"protobuf {name} (request {self.request_id}, handled={self.handled})"


@dataclass(frozen=True)
class ProtocolWarning(WatchEvent):
    """A frame could not be decoded. Surfaced rather than only logged."""

    detail: str = ""

    def __str__(self) -> str:
        return f"protocol warning: {self.detail}"


__all__ = [
    "WatchEvent",
    "Connected",
    "DeviceIdentified",
    "Disconnected",
    "Reconnected",
    "ServiceRegistered",
    "MetricUnavailable",
    "TimeSyncRequested",
    "TimeSyncSent",
    "SystemEvent",
    "ProtobufReceived",
    "ProtocolWarning",
]
