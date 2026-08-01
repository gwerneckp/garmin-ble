"""The transport contract.

A transport is the narrowest possible waist between :class:`~garmin_ble.watch.Watch`
and the outside world: open a link, write bytes, deliver bytes back, close.
Everything above it — handshake, handles, GFDI, metrics — is transport-agnostic,
which is what lets the same ``Watch`` code drive a real radio, an in-process
simulator, or a recorded capture.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

#: Called by the transport with each inbound packet.
NotifyCallback = Callable[[bytes], Awaitable[None]]

#: Called by the transport when the link drops for any reason.
DisconnectCallback = Callable[[str], None]


@dataclass(frozen=True)
class LinkInfo:
    """What the transport knows about the peer once the link is open."""

    address: str
    name: Optional[str] = None
    mtu: int = 23

    @property
    def max_write(self) -> int:
        """Largest payload one write can carry (MTU minus ATT overhead)."""
        return max(self.mtu - 3, 20)


class Transport(abc.ABC):
    """Byte pipe to a watch.

    Implementations must call ``on_notify`` for every inbound packet and
    ``on_disconnect`` exactly once if the link drops unexpectedly.
    """

    def __init__(self) -> None:
        self.on_notify: Optional[NotifyCallback] = None
        self.on_disconnect: Optional[DisconnectCallback] = None

    @abc.abstractmethod
    async def open(self) -> LinkInfo:
        """Establish the link and return what is known about the peer.

        Raises :class:`~garmin_ble.errors.WatchNotFound` if no matching device
        exists, or :class:`~garmin_ble.errors.ConnectionFailed` if one exists
        but the link could not be brought up.
        """

    async def start(self) -> None:
        """Begin delivering inbound packets.

        Called once the layer above is fully wired. Transports that generate
        traffic on their own — replay, above all — must hold it until here, or
        the first frames arrive before anything is listening for them.
        """
        return None

    @abc.abstractmethod
    async def close(self) -> None:
        """Tear the link down. Must be safe to call when already closed."""

    @abc.abstractmethod
    async def write(self, data: bytes) -> None:
        """Send one packet. Must not exceed :attr:`LinkInfo.max_write`."""

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        """Whether the link is currently usable."""

    @property
    def is_passive(self) -> bool:
        """Whether this link only plays traffic back and ignores what we send.

        A passive transport skips the handshake (the capture already contains
        its result) and treats subscribe/unsubscribe as no-ops, since the set of
        metrics in a recording is fixed at the time it was made.
        """
        return False

    @property
    def can_reconnect(self) -> bool:
        """Whether reopening this transport after a drop is meaningful.

        False for replay, where "reconnecting" would just replay the file again.
        """
        return True

    async def _deliver(self, data: bytes) -> None:
        """Hand an inbound packet upward. For implementations to call."""
        if self.on_notify is not None:
            await self.on_notify(data)

    def _dropped(self, reason: str) -> None:
        """Report an unexpected link loss upward. For implementations to call."""
        if self.on_disconnect is not None:
            self.on_disconnect(reason)


__all__ = ["Transport", "LinkInfo", "NotifyCallback", "DisconnectCallback"]
