"""Typed telemetry metrics and readings.

This module is the single registry for real-time telemetry. Each metric bundles
its Garmin service code, its parser, and the dataclass its samples arrive as::

    from garmin_ble import metrics

    reading = await watch.read(metrics.HEART_RATE)   # -> metrics.HeartRate
    reading.bpm

Importing this package registers every metric, which is what
:data:`Metric.ALL_TELEMETRY` and the service-code lookup used by the wire
decoder depend on.
"""

from __future__ import annotations

from .base import Metric, Reading, all_metrics, by_name, by_service, service_label
from .accelerometer import ACCELEROMETER, COUNTS_PER_G, AccelPacket, AccelSample
from .body_battery import BODY_BATTERY, BodyBattery
from .calories import CALORIES, Calories
from .heart_rate import HEART_RATE, HeartRate
from .hrv import HRV, Hrv
from .intensity import INTENSITY, Intensity
from .respiration import RESPIRATION, Respiration
from .spo2 import SPO2, SpO2
from .steps import STEPS, Steps
from .stress import STRESS, Stress

# Declaration order here is display order — it is what `Metric.ALL_TELEMETRY`,
# the checklist renderer, and `stream_all()` iterate in.
Metric.ALL_TELEMETRY = (
    HEART_RATE,
    STEPS,
    HRV,
    SPO2,
    RESPIRATION,
    CALORIES,
    INTENSITY,
    STRESS,
    BODY_BATTERY,
    ACCELEROMETER,
)

__all__ = [
    # registry
    "Metric",
    "Reading",
    "all_metrics",
    "by_name",
    "by_service",
    "service_label",
    # metrics
    "HEART_RATE",
    "STEPS",
    "HRV",
    "SPO2",
    "RESPIRATION",
    "CALORIES",
    "INTENSITY",
    "STRESS",
    "BODY_BATTERY",
    "ACCELEROMETER",
    # readings
    "HeartRate",
    "Steps",
    "Hrv",
    "SpO2",
    "Respiration",
    "Calories",
    "Intensity",
    "Stress",
    "BodyBattery",
    "AccelPacket",
    "AccelSample",
    "COUNTS_PER_G",
]
