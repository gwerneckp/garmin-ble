"""Replay a recorded session as if the watch were present.

A capture written by ``watch.record(path)`` holds every frame in both
directions with its timestamp. Replaying feeds the RX frames back in their
original order, so the whole stack above the transport runs exactly as it did
live. Host writes are accepted and discarded — the recording already contains
whatever the watch said in response.

This is how a protocol bug gets reproduced by someone who does not own the
watch that produced it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List, Optional, Union

from ..errors import ConnectionFailed
from ..frames import Direction, Frame, read_capture
from ..logging import get_logger
from .base import LinkInfo, Transport

log = get_logger(__name__)


class ReplayTransport(Transport):
    """Feeds recorded RX frames back to the host.

    ``speed`` scales the original inter-frame delays: 1.0 replays in real time,
    higher is faster, and ``None`` replays as fast as the event loop allows —
    which is what tests want.
    """

    def __init__(
        self,
        path: Union[str, Path],
        speed: Optional[float] = None,
        loop_forever: bool = False,
        address: str = "RE:PL:AY:00:00:01",
        name: str = "Replay",
    ):
        super().__init__()
        self.path = Path(path)
        self.speed = speed
        self.loop_forever = loop_forever
        self._address = address
        self._name = name
        self._frames: List[Frame] = []
        self._task: Optional[asyncio.Task] = None
        self._open = False

    async def open(self) -> LinkInfo:
        if not self.path.exists():
            raise ConnectionFailed(f"capture file not found: {self.path}")

        self._frames = [f for f in read_capture(self.path) if f.direction is Direction.RX]
        if not self._frames:
            raise ConnectionFailed(f"capture {self.path} contains no inbound frames")

        self._open = True
        return LinkInfo(address=self._address, name=self._name, mtu=247)

    async def start(self) -> None:
        self._task = asyncio.ensure_future(self._replay())

    async def close(self) -> None:
        self._open = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def write(self, data: bytes) -> None:
        # A recording already contains the watch's side of the conversation, so
        # there is nothing to answer; dropping the write keeps replay faithful
        # rather than inventing responses.
        return None

    @property
    def is_open(self) -> bool:
        return self._open

    @property
    def can_reconnect(self) -> bool:
        """Replaying a file again is not a reconnection, so never retry."""
        return False

    @property
    def is_passive(self) -> bool:
        """A capture already contains the handshake; re-running it would hang.

        The recorded REGISTER_ML_RESP frames repopulate the handle table as
        they replay, so routing works out on its own.
        """
        return True

    async def _replay(self) -> None:
        try:
            while self._open:
                previous: Optional[Frame] = None
                for frame in self._frames:
                    if not self._open:
                        return
                    if self.speed and previous is not None:
                        gap = (frame.at - previous.at).total_seconds() / self.speed
                        if gap > 0:
                            await asyncio.sleep(min(gap, 5.0))
                    else:
                        # Yield so the host can process each frame in turn.
                        await asyncio.sleep(0)
                    previous = frame
                    await self._deliver(frame.raw)

                if not self.loop_forever:
                    break

            log.debug("Replay of %s exhausted", self.path)
            self._open = False
            self._dropped("capture exhausted")
        except asyncio.CancelledError:
            pass


__all__ = ["ReplayTransport"]
