"""BLE client implementations for Garmin watches."""

from .base import GarminClientBase
from .auto import GarminClient

__all__ = ["GarminClientBase", "GarminClient"]
