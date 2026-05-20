"""Telemetry parsers for Garmin real-time sensor data.

Each module in this package handles one telemetry type (HR, steps, HRV, etc.),
extracting typed values from the raw binary payloads sent by the watch.

The :class:`TelemetryDispatcher` aggregates all parsers and provides a single
entry point that replaces the old ``_parse_*`` methods on ``GarminClientBase``.
"""

from typing import Callable, Dict, Optional

from ...constants import GarminService
from ...logging import get_logger

from .hr import parse_hr
from .steps import parse_steps
from .hrv import parse_hrv
from .spo2 import parse_spo2
from .respiration import parse_respiration
from .calories import parse_calories
from .intensity import parse_intensity
from .stress import parse_stress
from .accel import parse_accel
from .body_battery import parse_body_battery

log = get_logger(__name__)

# Maps GarminService codes to (parser_fn, event_name)
_PARSE_TABLE = {
    GarminService.REALTIME_HR: (parse_hr, "hr"),
    GarminService.REALTIME_STEPS: (parse_steps, "steps"),
    GarminService.REALTIME_HRV: (parse_hrv, "hrv"),
    GarminService.REALTIME_SPO2: (parse_spo2, "spo2"),
    GarminService.REALTIME_RESPIRATION: (parse_respiration, "respiration"),
    GarminService.REALTIME_CALORIES: (parse_calories, "calories"),
    GarminService.REALTIME_INTENSITY: (parse_intensity, "intensity"),
    GarminService.REALTIME_STRESS: (parse_stress, "stress"),
    GarminService.REALTIME_ACCELEROMETER: (parse_accel, "accel"),
    GarminService.REALTIME_BODY_BATTERY: (parse_body_battery, "body_battery"),
}


class TelemetryDispatcher:
    """Routes raw telemetry bytes to the correct parser and user callback.

    The client registers its high-level ``callbacks`` dict here; when
    ``dispatch()`` is called with a service code and raw bytes, the
    dispatcher looks up the correct parser, runs it, and fires the
    registered callback with the parsed values.
    """

    def __init__(self, callbacks: Dict[str, Callable]):
        self.callbacks = callbacks

    def dispatch(self, service_code: int, data: bytes) -> None:
        """Parse *data* for *service_code* and fire the matching callback."""
        entry = _PARSE_TABLE.get(service_code)
        if entry is None:
            return
        parser_fn, event_name = entry
        result = parser_fn(data)
        if result is not None:
            cb = self.callbacks.get(event_name)
            if cb:
                if isinstance(result, tuple):
                    cb(*result)
                else:
                    cb(result)
