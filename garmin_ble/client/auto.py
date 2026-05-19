"""Garmin client with automatic discovery, reconnection, and keep-alive."""

import asyncio
from typing import Optional
from bleak import BleakScanner

from .base import GarminClientBase
from ..constants import GarminService
from ..logging import get_logger

log = get_logger(__name__)


class GarminClient(GarminClientBase):
    """Extends :class:`GarminClientBase` with BLE scanning and automatic
    reconnection + keep-alive logic.

    Use this when you want to discover a watch on the network and maintain
    the connection without manual intervention.
    """

    def __init__(self):
        super().__init__()
        self._running = False
        self._reconnect_interval = 5.0
        self._disconnect_event = asyncio.Event()
        self.on("disconnected", lambda: self._disconnect_event.set())

    async def connect(self, address: Optional[str] = None, timeout: float = 30.0) -> bool:
        """Scan for a Garmin watch and connect, or connect directly to *address*."""
        self._disconnect_event.clear()
        if address is None:
            log.info("Scanning for Garmin watches...")
            devices = await BleakScanner.discover(timeout=timeout, return_adv=True)

            garmin_device = None
            all_found = []

            for addr, (device, adv_data) in devices.items():
                name = device.name or adv_data.local_name or "Unknown"
                all_found.append(f" - {name} [{addr}]")

                name_lower = name.lower()
                if "garmin" in name_lower or "fenix" in name_lower:
                    garmin_device = device
                    break

            if not garmin_device:
                log.warning("Could not find a Garmin/Fenix device nearby. Devices found:")
                for d in all_found:
                    log.warning(d)
                return False

            self.device = garmin_device
            address = garmin_device.address

        return await super().connect(address, timeout=timeout)

    async def start_sync_loop(self):
        """Keep the client alive and process events with automatic reconnection."""
        self._running = True
        log.info("Starting automatic sync loop.")

        try:
            while self._running:
                if not self.is_connected:
                    success = await self._reconnect_with_backoff()
                    if not success:
                        continue

                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            log.debug("Sync loop cancelled.")
            raise
        finally:
            self._running = False
            await self.disconnect()

    async def _reconnect_with_backoff(self) -> bool:
        """Attempt reconnection with exponential backoff."""
        delay = self._reconnect_interval
        max_delay = 60.0
        attempts = 0
        max_attempts = 10

        while self._running and attempts < max_attempts:
            attempts += 1
            log.info("Reconnection attempt %d/%d...", attempts, max_attempts)
            success = await self.connect(self.address)
            if success:
                return True
            log.debug("Reconnection failed. Retrying in %.1fs...", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

        log.warning("Max reconnection attempts reached.")
        return False

    def stop_sync_loop(self):
        """Stop the sync loop and disconnect."""
        self._running = False

    def _on_service_registered(self, service_code: int, handle: int):
        """Override: no automatic triggers. User controls start manually."""
        pass

    def _on_handshake_reset(self):
        """Register GFDI after CLOSE_ALL.

        Only the GFDI service (service code 1) is registered automatically.
        All other services — HR, steps, HRV, SpO2, respiration, stress,
        body battery, accelerometer, calories, intensity — must be
        registered explicitly by the user via
        :meth:`~garmin_ble.client.base.GarminClientBase.request_service_registration`.
        """
        asyncio.create_task(self.request_service_registration(GarminService.GFDI))

