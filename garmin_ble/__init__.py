"""garmin_ble — talk to a Garmin watch over its proprietary BLE protocol.

Stream live telemetry, read device state, and drive Garmin's GFDI protobufs
directly over Bluetooth Low Energy. No cloud, no phone, no Garmin Connect.

    import asyncio
    from garmin_ble import Watch, metrics

    async def main():
        async with Watch.discover() as watch:
            async for reading in watch.stream(metrics.HEART_RATE):
                print(reading.bpm)

    asyncio.run(main())

Develop without hardware by swapping the factory — ``Watch.simulated()`` runs an
in-process watch that speaks the same protocol, and ``Watch.replay(path)``
replays a recorded session.
"""

from __future__ import annotations

from . import events, metrics
from .constants import GarminMessage, GarminService, RequestType, SystemEventType
from .errors import (
    ConnectionFailed,
    GarminBleError,
    HandshakeError,
    NotConnected,
    ProtocolError,
    RequestTimeout,
    ServiceUnavailable,
    UnroutableMessage,
    WatchNotFound,
)
from .frames import Direction, Frame, FrameKind, read_capture
from .metrics import Metric, Reading
from .report import Checklist, CollectionResult, Missing
from .transport import WatchProfile, available_profiles
from .watch import Battery, DeviceInfo, Diagnostics, InstalledApp, Watch, WatchSession

__version__ = "0.3.0"

__all__ = [
    # the API
    "Watch",
    "WatchSession",
    "metrics",
    "Metric",
    "Reading",
    "events",
    # value types
    "DeviceInfo",
    "Battery",
    "InstalledApp",
    "Diagnostics",
    "CollectionResult",
    "Missing",
    "Checklist",
    # errors
    "GarminBleError",
    "WatchNotFound",
    "ConnectionFailed",
    "NotConnected",
    "HandshakeError",
    "ServiceUnavailable",
    "RequestTimeout",
    "ProtocolError",
    "UnroutableMessage",
    # tracing
    "Frame",
    "FrameKind",
    "Direction",
    "read_capture",
    # simulation
    "WatchProfile",
    "available_profiles",
    # protocol constants
    "GarminService",
    "GarminMessage",
    "RequestType",
    "SystemEventType",
    "__version__",
]
