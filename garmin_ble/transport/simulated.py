"""An in-process watch that speaks the real protocol.

The simulator is a full peer, not a stub: it negotiates MLR handles, refuses
services its profile does not support, COBS-frames its GFDI traffic, answers
protobuf requests, and asks the host for the time. Because it sits at the
transport seam, everything above it — handshake, subscriptions, correlation,
reconnection — is the same code that runs against a real watch.

That buys three things the library had no way to offer before:

* examples that run with no hardware, so the walkthrough works in CI;
* tests that exercise the protocol rather than a mocked ``BleakClient``;
* a way to reproduce someone's bug from a profile rather than a wrist.
"""

from __future__ import annotations

import asyncio
import random
import struct
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple

from ..constants import GarminMessage, GarminService, RequestType, SystemEventType
from ..logging import get_logger
from ..metrics import Metric
from ..protobuf import gdi_device_status_pb2, gdi_find_my_watch_pb2, gdi_installed_apps_service_pb2
from ..protocol import cobs as cobs_mod
from ..protocol import gfdi as gfdi_mod
from ..protocol import mlr
from ..protocol.smart import SmartRouter
from .base import LinkInfo, Transport

log = get_logger(__name__)


@dataclass(frozen=True)
class WatchProfile:
    """What a simulated model can do.

    ``supports`` is the whole point: a profile that omits a metric makes
    ``subscribe`` raise :class:`~garmin_ble.errors.ServiceUnavailable`, exactly
    as a real watch that declines the registration does.
    """

    name: str
    model: str
    firmware: str = "1.0.0"
    mtu: int = 247
    battery_percent: int = 78
    supports: Tuple[Metric, ...] = ()
    apps: Tuple[str, ...] = ()
    #: Seconds between telemetry samples, per metric name; the fallback applies
    #: to anything not listed.
    intervals: Dict[str, float] = field(default_factory=dict)
    default_interval: float = 1.0


def _profiles() -> Dict[str, WatchProfile]:
    from .. import metrics as m

    everything = m.Metric.ALL_TELEMETRY
    fast = {metric.name: 0.05 for metric in everything}
    return {
        # A flagship that reports everything, quickly enough for tests.
        "fenix7": WatchProfile(
            name="fenix 7",
            model="Fenix 7",
            firmware="19.20",
            battery_percent=78,
            supports=everything,
            apps=("Connect IQ Store", "Trail Run", "Sleep Coach"),
            intervals=fast,
            default_interval=0.05,
        ),
        # A mid-range model with no SpO2 or accelerometer stream, for exercising
        # capability checks and the "unsupported" path.
        "venu3": WatchProfile(
            name="Venu 3",
            model="Venu 3",
            firmware="8.10",
            battery_percent=52,
            supports=tuple(
                metric for metric in everything
                if metric not in (m.SPO2, m.ACCELEROMETER)
            ),
            apps=("Music", "Body Battery"),
            intervals=fast,
            default_interval=0.05,
        ),
        # Heart rate only: the smallest thing that still completes a handshake.
        "minimal": WatchProfile(
            name="Garmin Minimal",
            model="Simulator",
            supports=(m.HEART_RATE,),
            intervals={m.HEART_RATE.name: 0.05},
            default_interval=0.05,
        ),
    }


def available_profiles() -> Tuple[str, ...]:
    """Names accepted by ``Watch.simulated(profile=...)``."""
    return tuple(_profiles())


def get_profile(name: str) -> WatchProfile:
    profiles = _profiles()
    try:
        return profiles[name]
    except KeyError:
        raise ValueError(
            f"unknown simulator profile {name!r}; try one of {', '.join(profiles)}"
        ) from None


class _Vitals:
    """Plausible, smoothly-varying telemetry.

    Seeded, so a simulated session is reproducible — which matters when a test
    asserts on values rather than merely on arrival.
    """

    def __init__(self, rng: random.Random, battery_percent: int):
        self.rng = rng
        self.bpm = rng.randint(58, 72)
        self.resting = rng.randint(45, 55)
        self.steps = rng.randint(2000, 9000)
        self.goal = 10000
        self.calories_total = rng.randint(1400, 2200)
        self.calories_active = rng.randint(200, 700)
        self.moderate = rng.randint(0, 60)
        self.vigorous = rng.randint(0, 30)
        self.stress = rng.randint(15, 60)
        self.body_battery = battery_percent
        self.spo2 = rng.randint(94, 99)
        self.respiration = rng.randint(12, 18)
        self.accel_t = 0

    def _walk(self, value: int, step: int, low: int, high: int) -> int:
        return max(low, min(high, value + self.rng.randint(-step, step)))

    def next_reading(self, metric: Metric):
        from .. import metrics as m

        if metric is m.HEART_RATE:
            self.bpm = self._walk(self.bpm, 3, 45, 190)
            return m.HeartRate(bpm=self.bpm, resting_bpm=self.resting)
        if metric is m.STEPS:
            self.steps += self.rng.randint(0, 12)
            return m.Steps(count=self.steps, goal=self.goal)
        if metric is m.HRV:
            return m.Hrv(rr_ms=int(60_000 / max(self.bpm, 1)) + self.rng.randint(-25, 25))
        if metric is m.SPO2:
            self.spo2 = self._walk(self.spo2, 1, 90, 100)
            return m.SpO2(percent=self.spo2)
        if metric is m.RESPIRATION:
            self.respiration = self._walk(self.respiration, 1, 8, 24)
            return m.Respiration(breaths_per_min=self.respiration)
        if metric is m.CALORIES:
            self.calories_total += self.rng.randint(0, 3)
            self.calories_active += self.rng.randint(0, 2)
            return m.Calories(total=self.calories_total, active=self.calories_active)
        if metric is m.INTENSITY:
            return m.Intensity(moderate=self.moderate, vigorous=self.vigorous)
        if metric is m.STRESS:
            self.stress = self._walk(self.stress, 4, 0, 100)
            return m.Stress(level=self.stress)
        if metric is m.BODY_BATTERY:
            self.body_battery = self._walk(self.body_battery, 1, 5, 100)
            return m.BodyBattery(level=self.body_battery)
        if metric is m.ACCELEROMETER:
            self.accel_t = (self.accel_t + 40) % 65536
            samples = tuple(
                m.AccelSample(
                    x=self.rng.randint(-40, 40),
                    y=self.rng.randint(-40, 40),
                    z=-256 + self.rng.randint(-20, 20),
                )
                for _ in range(3)
            )
            return m.AccelPacket(samples=samples, timestamp_ms=self.accel_t)
        raise ValueError(f"simulator has no generator for {metric.name}")


class SimulatedTransport(Transport):
    """A watch that exists only inside this process."""

    def __init__(
        self,
        profile: str = "fenix7",
        seed: Optional[int] = 0,
        address: str = "SI:MU:LA:TE:D0:01",
        time_sync_after: Optional[float] = 0.2,
        system_event_after: Optional[float] = 0.3,
    ):
        super().__init__()
        self.profile = get_profile(profile) if isinstance(profile, str) else profile
        self._rng = random.Random(seed)
        self._vitals = _Vitals(self._rng, self.profile.battery_percent)
        self._address = address
        self._time_sync_after = time_sync_after
        self._system_event_after = system_event_after

        self._open = False
        self._silent = False
        self._handles: Dict[int, int] = {}      # service code -> handle
        self._next_handle = 1
        self._streaming: Set[int] = set()       # service codes currently started
        self._cobs = cobs_mod.CobsCoDec()
        self._outbox: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._tasks: "list[asyncio.Task]" = []

    # ── transport contract ──────────────────────────────────────────────────

    async def open(self) -> LinkInfo:
        self._open = True
        self._cobs.reset()
        self._spawn(self._pump())
        if self._time_sync_after is not None:
            self._spawn(self._ask_for_time_later())
        if self._system_event_after is not None:
            self._spawn(self._send_system_event_later())
        return LinkInfo(address=self._address, name=self.profile.name, mtu=self.profile.mtu)

    async def close(self) -> None:
        self._open = False
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014 - teardown
                pass
        self._handles.clear()
        self._streaming.clear()

    @property
    def is_open(self) -> bool:
        return self._open

    async def write(self, data: bytes) -> None:
        if not self._open:
            from ..errors import ConnectionFailed

            raise ConnectionFailed("write attempted on a closed simulated link")
        self._handle_host_packet(data)

    # ── plumbing ────────────────────────────────────────────────────────────

    def _spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.append(task)

    def _send(self, handle: int, payload: bytes) -> None:
        """Queue one packet for delivery to the host."""
        self._outbox.put_nowait(mlr.encode_rx(handle, payload))

    async def _pump(self) -> None:
        """Deliver queued packets.

        Responses go through a queue rather than straight out of ``write`` so
        that the host is never re-entered from inside its own send call.
        """
        try:
            while self._open:
                packet = await self._outbox.get()
                await self._deliver(packet)
        except asyncio.CancelledError:
            pass

    # ── host -> watch ───────────────────────────────────────────────────────

    def _handle_host_packet(self, data: bytes) -> None:
        if not data:
            return

        if data[0] == mlr.CONTROL_HANDLE and len(data) >= 2:
            self._handle_control(data)
            return

        handle = data[0]
        service = self._service_for_handle(handle)
        if service is None:
            log.debug("Simulator ignoring packet on unassigned handle 0x%02x", handle)
            return

        if service == GarminService.GFDI:
            self._handle_gfdi_bytes(data[1:])
        elif len(data) >= 2:
            self._handle_service_command(service, data[1])

    def _handle_control(self, data: bytes) -> None:
        msg_type = data[1]

        if msg_type == RequestType.CLOSE_ALL_REQ:
            self._handles.clear()
            self._streaming.clear()
            self._next_handle = 1
            # Control-channel frames are never MLR-flagged, so they carry a
            # bare routing byte rather than going through _send().
            self._outbox.put_nowait(
                mlr.encode_tx(mlr.CONTROL_HANDLE, mlr.build_close_all_response())
            )
            return

        if msg_type == RequestType.REGISTER_ML_REQ and len(data) >= 13:
            service = struct.unpack("<h", data[10:12])[0]
            self._register_service(service)

    def _register_service(self, service: int) -> None:
        from ..metrics import by_service

        supported_codes = {int(metric.service) for metric in self.profile.supports}
        if service != GarminService.GFDI and service not in supported_codes:
            metric = by_service(service)
            log.debug("Simulator refusing service %s", metric.name if metric else service)
            self._outbox.put_nowait(
                mlr.encode_tx(
                    mlr.CONTROL_HANDLE, mlr.build_register_ml_response(service, 0, status=1)
                )
            )
            return

        handle = self._handles.get(service)
        if handle is None:
            handle = self._next_handle
            self._next_handle += 1
            self._handles[service] = handle
        self._outbox.put_nowait(
            mlr.encode_tx(
                mlr.CONTROL_HANDLE, mlr.build_register_ml_response(service, handle, status=0)
            )
        )

    def _handle_service_command(self, service: int, command: int) -> None:
        if command == 0x01:
            if service not in self._streaming:
                self._streaming.add(service)
                self._spawn(self._stream_metric(service))
        elif command == 0x00:
            self._streaming.discard(service)

    def _service_for_handle(self, handle: int) -> Optional[int]:
        for service, assigned in self._handles.items():
            if assigned == handle:
                return service
        return None

    # ── telemetry generation ────────────────────────────────────────────────

    async def _stream_metric(self, service: int) -> None:
        from ..metrics import by_service

        metric = by_service(service)
        if metric is None:
            return
        interval = self.profile.intervals.get(metric.name, self.profile.default_interval)
        try:
            while self._open and service in self._streaming:
                handle = self._handles.get(service)
                if handle is not None and not self._silent:
                    reading = self._vitals.next_reading(metric)
                    self._send(handle, metric.encode(reading))
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    # ── GFDI ────────────────────────────────────────────────────────────────

    def _send_gfdi(self, frame: bytes) -> None:
        handle = self._handles.get(int(GarminService.GFDI))
        if handle is None:
            return
        encoded = cobs_mod.CobsCoDec.encode(frame)
        for chunk in mlr.fragment(encoded, max(self.profile.mtu - 3, 23)):
            self._send(handle, chunk)

    def _handle_gfdi_bytes(self, payload: bytes) -> None:
        self._cobs.received_bytes(payload)
        while True:
            raw = self._cobs.retrieve_message()
            if not raw:
                break
            message = gfdi_mod.parse_message(raw)
            if message is not None:
                self._handle_gfdi_message(message)

    def _handle_gfdi_message(self, message: gfdi_mod.GfdiMessage) -> None:
        if message.type == GarminMessage.PROTOBUF_REQUEST:
            frame = gfdi_mod.parse_protobuf_frame(message)
            if frame is None or not frame.is_complete:
                return
            self._send_gfdi(
                gfdi_mod.GfdiMessageBuilder.build_protobuf_ack(
                    message.type, frame.request_id, frame.data_offset
                )
            )
            self._answer_protobuf(frame)
            return

        if message.type == GarminMessage.SYSTEM_EVENT:
            # The host's heartbeat. Acknowledge it the way a watch does.
            self._send_gfdi(gfdi_mod.GfdiMessageBuilder.build_status_ack(message.type))
            return

        if message.type != GarminMessage.RESPONSE:
            self._send_gfdi(gfdi_mod.GfdiMessageBuilder.build_status_ack(message.type))

    def _answer_protobuf(self, frame: gfdi_mod.ProtobufFrame) -> None:
        from ..protobuf import gdi_smart_proto_pb2

        smart = gdi_smart_proto_pb2.Smart()
        try:
            smart.ParseFromString(frame.proto_bytes)
        except Exception as exc:  # pragma: no cover - malformed input
            log.debug("Simulator could not parse protobuf: %s", exc)
            return

        request = SmartRouter.unwrap(smart)
        response = self._respond_to(request)
        if response is None:
            return

        body = SmartRouter.wrap(response).SerializeToString()
        self._send_gfdi(
            gfdi_mod.GfdiMessageBuilder.build_protobuf_response(
                request_id=frame.request_id,
                data_offset=0,
                total_length=len(body),
                proto_bytes=body,
            )
        )

    def _respond_to(self, request):
        """Map a request message to the response a watch would send."""
        ds = gdi_device_status_pb2.DeviceStatusService
        fmw = gdi_find_my_watch_pb2.FindMyWatchService
        apps = gdi_installed_apps_service_pb2.InstalledAppsService

        if isinstance(request, ds.RemoteDeviceBatteryStatusRequest):
            return ds.RemoteDeviceBatteryStatusResponse(
                status=ds.ResponseStatus.OK,
                current_battery_level=self.profile.battery_percent,
            )
        if isinstance(request, (fmw.FindMyWatchRequest,)):
            return fmw.FindMyWatchResponse(status=fmw.ResponseStatus.OK)
        if isinstance(request, (fmw.FindMyWatchCancelRequest,)):
            return fmw.FindMyWatchCancelResponse(status=fmw.ResponseStatus.OK)
        if isinstance(request, apps.GetInstalledAppsRequest):
            return apps.GetInstalledAppsResponse(
                availableSpace=32 * 1024 * 1024,
                availableSlots=12,
                installedApps=[
                    apps.InstalledApp(
                        storeAppId=bytes([i + 1]) * 4,
                        type=apps.AppType.WATCH_APP,
                        name=name,
                        disabled=False,
                        version=1,
                    )
                    for i, name in enumerate(self.profile.apps)
                ],
            )
        return None

    # ── watch-initiated traffic ─────────────────────────────────────────────

    async def _ask_for_time_later(self) -> None:
        """Real watches ask the host for the time shortly after connecting."""
        try:
            await asyncio.sleep(self._time_sync_after or 0)
            while self._open and int(GarminService.GFDI) not in self._handles:
                await asyncio.sleep(0.02)
            if self._open:
                self._send_gfdi(
                    gfdi_mod.GfdiMessageBuilder.build_message(
                        int(GarminMessage.CURRENT_TIME_REQUEST), struct.pack("<I", 0)
                    )
                )
        except asyncio.CancelledError:
            pass

    async def _send_system_event_later(self) -> None:
        try:
            await asyncio.sleep(self._system_event_after or 0)
            while self._open and int(GarminService.GFDI) not in self._handles:
                await asyncio.sleep(0.02)
            if self._open:
                self._send_gfdi(
                    gfdi_mod.GfdiMessageBuilder.build_system_event(
                        int(SystemEventType.SYNC_COMPLETE), 0
                    )
                )
        except asyncio.CancelledError:
            pass

    # ── test affordances ────────────────────────────────────────────────────

    def silence(self) -> None:
        """Keep the link up but stop emitting telemetry.

        Models the very common real case of a metric whose service is
        registered and started but which has nothing new to report — a resting
        wrist produces no steps, no intensity minutes, and no body battery
        change for minutes at a time.
        """
        self._silent = True

    def resume(self) -> None:
        """Start emitting telemetry again."""
        self._silent = False

    def simulate_drop(self, reason: str = "simulated link loss") -> None:
        """Pull the link out from under the host, as a real watch can."""
        self._open = False
        self._streaming.clear()
        self._dropped(reason)


__all__ = [
    "SimulatedTransport",
    "WatchProfile",
    "available_profiles",
    "get_profile",
]
