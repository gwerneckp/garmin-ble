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

    async def connect(self, address: Optional[str] = None, timeout: float = 5.0) -> bool:
        """Scan for a Garmin watch and connect, or connect directly to *address*."""
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
                    log.info("Client disconnected. Attempting reconnection...")
                    # self.address is set in GarminClientBase.connect()
                    success = await self.connect(self.address)
                    if not success:
                        log.debug("Reconnection failed. Retrying in %.1fs...", self._reconnect_interval)
                        await asyncio.sleep(self._reconnect_interval)
                        continue
                
                # Periodically check connection status (keep-alive)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            log.debug("Sync loop cancelled.")
            raise
        finally:
            self._running = False
            await self.disconnect()

    def stop_sync_loop(self):
        """Stop the sync loop and disconnect."""
        self._running = False

    def _on_handshake_reset(self):
        """Request default telemetry services after CLOSE_ALL."""
        telemetry_services = [
            GarminService.GFDI,
            GarminService.REALTIME_HR,
            GarminService.REALTIME_STEPS,
            GarminService.REALTIME_HRV,
            GarminService.REALTIME_SPO2,
            GarminService.REALTIME_RESPIRATION
        ]
        for svc in telemetry_services:
            asyncio.create_task(self.request_service_registration(svc))
