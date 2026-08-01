"""Real BLE transport, over bleak.

This is the only module in the library that imports bleak. Everything bleak
exposes — ``BleakClient``, ``mtu_size``, GATT characteristic objects — stops
here rather than leaking into the public API, which is why ``watch.info.mtu``
exists instead of callers reaching for ``client.client.mtu_size``.
"""

from __future__ import annotations

import fnmatch
from typing import List, Optional

from bleak import BleakClient, BleakScanner

from ..constants import GARMIN_BASE_UUID
from ..errors import (
    CHARACTERISTIC_DISCOVERY,
    ConnectionFailed,
    DiscoveredDevice,
    HandshakeError,
    WatchNotFound,
)
from ..logging import get_logger
from .base import LinkInfo, Transport

log = get_logger(__name__)

#: Advert-name patterns that identify a Garmin wearable.
GARMIN_NAME_HINTS = ("garmin", "fenix", "forerunner", "venu", "vivo", "instinct", "epix", "enduro", "tactix", "quatix", "marq", "descent", "approach", "lily")

#: The proprietary characteristic pair lives somewhere in this window; the TX
#: characteristic is always the RX one plus 0x10.
_RX_UUID_RANGE = range(0x2810, 0x2815)
_TX_OFFSET = 0x10


def looks_like_garmin(name: Optional[str]) -> bool:
    """Whether a BLE advert name suggests a Garmin wearable."""
    if not name:
        return False
    lowered = name.lower()
    return any(hint in lowered for hint in GARMIN_NAME_HINTS)


def _matches(name: Optional[str], pattern: str) -> bool:
    """Case-insensitive glob match against an advert name."""
    return bool(name) and fnmatch.fnmatch(name.lower(), pattern.lower())


class BleTransport(Transport):
    """Transport over a real Bluetooth Low Energy link.

    Exactly one of *address* or *name_pattern* narrows the search; with neither,
    the first device whose advert name looks like a Garmin wearable wins.
    """

    def __init__(
        self,
        address: Optional[str] = None,
        name_pattern: Optional[str] = None,
        scan_timeout: float = 30.0,
        connect_timeout: float = 30.0,
    ):
        super().__init__()
        self._address = address
        self._name_pattern = name_pattern
        self._scan_timeout = scan_timeout
        self._connect_timeout = connect_timeout

        self._client: Optional[BleakClient] = None
        self._rx_char: Optional[str] = None
        self._tx_char: Optional[str] = None
        self._name: Optional[str] = None
        self._closing = False

    # ── discovery ───────────────────────────────────────────────────────────

    async def _scan(self) -> "tuple[str, Optional[str]]":
        """Find one matching device, or raise :class:`WatchNotFound`."""
        log.info("Scanning for a Garmin watch (%.0fs)...", self._scan_timeout)
        found = await BleakScanner.discover(timeout=self._scan_timeout, return_adv=True)

        seen: List[DiscoveredDevice] = []
        match: Optional["tuple[str, Optional[str]]"] = None

        for address, (device, adv) in found.items():
            name = device.name or getattr(adv, "local_name", None)
            seen.append(DiscoveredDevice(name=name, address=address, rssi=getattr(adv, "rssi", None)))
            if match is not None:
                continue
            if self._name_pattern is not None:
                if _matches(name, self._name_pattern):
                    match = (address, name)
            elif looks_like_garmin(name):
                match = (address, name)

        if match is None:
            criterion = (
                f"name matching {self._name_pattern!r}"
                if self._name_pattern
                else "a Garmin-looking advert name"
            )
            raise WatchNotFound(f"no device with {criterion} found in range", seen)

        return match

    # ── transport contract ──────────────────────────────────────────────────

    async def open(self) -> LinkInfo:
        self._closing = False
        address, name = (self._address, None)
        if address is None:
            address, name = await self._scan()

        log.info("Connecting to %s ...", name or address)
        client = BleakClient(address, disconnected_callback=self._on_bleak_disconnect)
        try:
            await client.connect(timeout=self._connect_timeout)
        except Exception as exc:  # bleak raises a wide variety here
            raise ConnectionFailed(f"could not connect to {address}: {exc}") from exc

        self._client = client
        self._name = name or getattr(client, "name", None)

        rx, tx = self._find_characteristics(client)
        if rx is None or tx is None:
            await self.close()
            raise HandshakeError(
                f"{address} exposes no Garmin proprietary characteristic "
                f"(0x{_RX_UUID_RANGE.start:04x}-0x{_RX_UUID_RANGE.stop - 1:04x}); "
                f"it is probably not a Garmin wearable",
                CHARACTERISTIC_DISCOVERY,
            )
        self._rx_char, self._tx_char = rx, tx

        await client.start_notify(rx, self._on_bleak_notify)

        return LinkInfo(address=address, name=self._name, mtu=self._read_mtu(client))

    @staticmethod
    def _read_mtu(client: BleakClient) -> int:
        """Negotiated MTU, or the BLE default if the backend will not say."""
        try:
            mtu = client.mtu_size
        except (AttributeError, NotImplementedError):
            return 23
        return mtu if mtu and mtu >= 23 else 23

    @staticmethod
    def _find_characteristics(client: BleakClient) -> "tuple[Optional[str], Optional[str]]":
        """Locate the Garmin RX/TX characteristic pair by UUID probing."""
        available = {
            char.uuid.lower()
            for service in (client.services or [])
            for char in service.characteristics
        }
        for base in _RX_UUID_RANGE:
            rx = GARMIN_BASE_UUID.format(base).lower()
            if rx in available:
                return rx, GARMIN_BASE_UUID.format(base + _TX_OFFSET).lower()
        return None, None

    async def close(self) -> None:
        self._closing = True
        client, self._client = self._client, None
        if client is not None and client.is_connected:
            try:
                await client.disconnect()
            except Exception as exc:  # pragma: no cover - teardown best effort
                log.debug("Ignoring error during disconnect: %s", exc)

    async def write(self, data: bytes) -> None:
        if self._client is None or self._tx_char is None:
            raise ConnectionFailed("write attempted on a closed BLE link")
        await self._client.write_gatt_char(self._tx_char, data, response=False)

    @property
    def is_open(self) -> bool:
        return self._client is not None and self._client.is_connected

    # ── bleak callbacks ─────────────────────────────────────────────────────

    async def _on_bleak_notify(self, _sender: object, data: bytearray) -> None:
        await self._deliver(bytes(data))

    def _on_bleak_disconnect(self, _client: BleakClient) -> None:
        if not self._closing:
            self._dropped("BLE link lost")


__all__ = ["BleTransport", "looks_like_garmin", "GARMIN_NAME_HINTS"]
