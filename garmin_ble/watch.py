"""The public API: a connected Garmin watch.

Everything a caller needs hangs off one object with one lifecycle::

    async with Watch.discover() as watch:
        async for reading in watch.stream(metrics.HEART_RATE):
            print(reading.bpm)

The context manager owns connecting, the handshake, the keep-alive heartbeat,
reconnection, and teardown, so none of those are steps a caller can forget or
sequence wrongly. Failures raise; nothing signals an error by returning
``False``.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Type,
    TypeVar,
    Union,
)

from google.protobuf.message import Message

from . import events as ev
from .constants import GarminMessage, GarminService, SystemEventType
from .errors import (
    CLOSE_ALL,
    REGISTER_ML,
    HandshakeError,
    NotConnected,
    RequestTimeout,
    ServiceUnavailable,
)
from .frames import Direction, Frame, FrameKind, Recorder
from .logging import get_logger
from .metrics import Metric, by_service, service_label
from .metrics.base import Reading
from .protobuf import gdi_device_status_pb2, gdi_find_my_watch_pb2
from .protobuf import gdi_installed_apps_service_pb2 as apps_pb2
from .protobuf import gdi_smart_proto_pb2
from .protocol import cobs, gfdi, mlr
from .protocol.smart import SmartRouter
from .report import CollectionResult, Missing
from .transport import ReplayTransport, SimulatedTransport, Transport

log = get_logger(__name__)


# ── types and helpers ────────────────────────────────────────────────────────

R = TypeVar("R", bound=Reading)

#: Any timeout or interval may be given as a timedelta or as plain seconds.
Duration = Union[timedelta, float, int]

#: A user callback taking one reading or message; sync or async, both awaited.
Handler = Callable[[Any], Union[None, Awaitable[None]]]

#: Pushed into subscriber queues to end an async iterator on teardown.
_SENTINEL = object()


def _seconds(value: Optional[Duration], default: Optional[float] = None) -> Optional[float]:
    """Normalise a :data:`Duration` to seconds, or to *default* if unset."""
    if value is None:
        return default
    if isinstance(value, timedelta):
        return value.total_seconds()
    return float(value)


# ─────────────────────────────────────────────────────────────────────────────
#  Value types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DeviceInfo:
    """What we know about the connected watch.

    Only exists once connected, so no field is ``Optional`` for the sake of a
    pre-connection state that no caller can observe.
    """

    name: str
    address: str
    mtu: int
    model: str = "unknown"
    firmware: str = "unknown"

    def __str__(self) -> str:
        return f"{self.name} [{self.address}] mtu={self.mtu}"


@dataclass(frozen=True)
class Battery:
    """The watch's own battery state."""

    percent: int
    status: str = "ok"

    def __str__(self) -> str:
        return f"{self.percent}%"


@dataclass(frozen=True)
class InstalledApp:
    """One app, widget, or watch face installed on the device."""

    name: str
    kind: str
    version: int
    disabled: bool

    def __str__(self) -> str:
        state = " (disabled)" if self.disabled else ""
        return f"{self.name} [{self.kind}] v{self.version}{state}"


@dataclass
class Diagnostics:
    """Live protocol counters, for ``watch.diagnostics.summary()``.

    Answers "which handles did I get, is the link healthy, how slow are
    requests" without attaching a debugger.
    """

    handles: Dict[str, int] = field(default_factory=dict)
    frames_tx: int = 0
    frames_rx: int = 0
    malformed: int = 0
    heartbeats_sent: int = 0
    last_heartbeat: Optional[float] = None
    requests_sent: int = 0
    requests_answered: int = 0
    reconnects: int = 0
    latencies_ms: List[float] = field(default_factory=list)

    def record_latency(self, ms: float) -> None:
        self.latencies_ms.append(ms)
        if len(self.latencies_ms) > 256:
            del self.latencies_ms[:-256]

    def _percentile(self, pct: float) -> Optional[float]:
        if not self.latencies_ms:
            return None
        ordered = sorted(self.latencies_ms)
        index = min(int(pct / 100 * len(ordered)), len(ordered) - 1)
        return ordered[index]

    def summary(self) -> str:
        handles = "  ".join(f"{name}→0x{h:02x}" for name, h in sorted(self.handles.items()))
        ago = "never" if self.last_heartbeat is None else f"{time.monotonic() - self.last_heartbeat:.1f}s ago"
        p50, p99 = self._percentile(50), self._percentile(99)
        latency = "n/a" if p50 is None else f"p50={p50:.0f}ms p99={p99:.0f}ms"
        return (
            f"handles:   {handles or '(none)'}\n"
            f"frames:    tx={self.frames_tx:,} rx={self.frames_rx:,}  malformed={self.malformed}\n"
            f"heartbeat: last {ago}, {self.heartbeats_sent} sent\n"
            f"requests:  {self.requests_sent} sent, {self.requests_answered} answered  {latency}\n"
            f"reconnects: {self.reconnects}"
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Fan-out plumbing
# ─────────────────────────────────────────────────────────────────────────────


class _Fanout:
    """Delivers each item to every open subscriber queue.

    Queues are bounded: a consumer that stops reading drops its own oldest
    items rather than growing without limit or stalling the BLE notify path.
    """

    def __init__(self, maxsize: int = 256):
        self._queues: Set["asyncio.Queue"] = set()
        self._maxsize = maxsize

    def subscribe(self) -> "asyncio.Queue":
        queue: "asyncio.Queue" = asyncio.Queue(maxsize=self._maxsize)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue") -> None:
        self._queues.discard(queue)

    def publish(self, item: Any) -> None:
        for queue in list(self._queues):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:  # pragma: no cover - race
                    pass
            queue.put_nowait(item)

    def close(self) -> None:
        for queue in list(self._queues):
            queue.put_nowait(_SENTINEL)
        self._queues.clear()


# ─────────────────────────────────────────────────────────────────────────────
#  Watch
# ─────────────────────────────────────────────────────────────────────────────


class Watch:
    """A connected Garmin watch.

    Do not construct directly — use :meth:`discover`, :meth:`at`, :meth:`named`,
    :meth:`simulated`, or :meth:`replay`, each of which returns a
    :class:`WatchSession` usable as an async context manager.
    """

    #: How long to wait for each handshake step before giving up.
    HANDSHAKE_TIMEOUT = 10.0
    #: How long to wait for the watch to answer a service registration.
    REGISTRATION_TIMEOUT = 5.0

    def __init__(
        self,
        transport: Transport,
        heartbeat: Optional[float] = 60.0,
        reconnect: str = "exponential",
        supported: Optional[Sequence[Metric]] = None,
    ):
        self._transport = transport
        self._heartbeat_interval = heartbeat
        self._reconnect_policy = reconnect

        self._info: Optional[DeviceInfo] = None
        self._capabilities: Optional[Sequence[Metric]] = supported

        # protocol state
        self._handles: Dict[int, int] = {}          # service code -> handle
        self._cobs = cobs.CobsCoDec()
        self._max_write = 20

        # subscription refcounts, so two consumers of heart rate do not fight
        self._subscriptions: Dict[Metric, int] = {}
        self._subscription_locks: Dict[Metric, "asyncio.Lock"] = {}
        self._metric_fanouts: Dict[Metric, _Fanout] = {}
        self._metric_handlers: Dict[Metric, List[Handler]] = {}
        self._latest: Dict[Metric, Reading] = {}
        self._all_telemetry = _Fanout()
        self._events = _Fanout()
        self._frame_handlers: List[Callable[[Frame], None]] = []

        # request/response correlation
        self._pending: Dict[int, "asyncio.Future"] = {}
        self._pending_sent_at: Dict[int, float] = {}
        self._next_request_id = 0
        self._responders: Dict[str, Handler] = {}

        # handshake signalling
        self._close_all_done: Optional["asyncio.Future"] = None
        self._registration_waiters: Dict[int, "asyncio.Future"] = {}

        # background work
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._dispatch_task: Optional[asyncio.Task] = None
        self._dispatch_queue: "asyncio.Queue" = asyncio.Queue(maxsize=1024)
        self._reconnect_task: Optional[asyncio.Task] = None
        self._closing = False
        self._connected = False

        self._recorder: Optional[Recorder] = None
        self.diagnostics = Diagnostics()

        transport.on_notify = self._on_packet
        transport.on_disconnect = self._on_transport_drop

    # ── session factories ───────────────────────────────────────────────────

    @classmethod
    def discover(
        cls,
        timeout: Optional[Duration] = 30.0,
        heartbeat: Optional[Duration] = 60.0,
        reconnect: str = "exponential",
        connect_timeout: Optional[Duration] = 30.0,
    ) -> "WatchSession":
        """Scan for any nearby Garmin watch and connect to the first match."""
        from .transport.ble import BleTransport

        return WatchSession(
            lambda: BleTransport(
                scan_timeout=_seconds(timeout, 30.0),
                connect_timeout=_seconds(connect_timeout, 30.0),
            ),
            heartbeat=_seconds(heartbeat),
            reconnect=reconnect,
        )

    @classmethod
    def at(
        cls,
        address: str,
        heartbeat: Optional[Duration] = 60.0,
        reconnect: str = "exponential",
        connect_timeout: Optional[Duration] = 30.0,
    ) -> "WatchSession":
        """Connect straight to a known BLE address, skipping the scan."""
        from .transport.ble import BleTransport

        return WatchSession(
            lambda: BleTransport(
                address=address, connect_timeout=_seconds(connect_timeout, 30.0)
            ),
            heartbeat=_seconds(heartbeat),
            reconnect=reconnect,
        )

    @classmethod
    def named(
        cls,
        pattern: str,
        timeout: Optional[Duration] = 30.0,
        heartbeat: Optional[Duration] = 60.0,
        reconnect: str = "exponential",
    ) -> "WatchSession":
        """Connect to the first device whose advert name matches a glob."""
        from .transport.ble import BleTransport

        return WatchSession(
            lambda: BleTransport(name_pattern=pattern, scan_timeout=_seconds(timeout, 30.0)),
            heartbeat=_seconds(heartbeat),
            reconnect=reconnect,
        )

    @classmethod
    def simulated(
        cls,
        profile: str = "fenix7",
        seed: Optional[int] = 0,
        heartbeat: Optional[Duration] = None,
        reconnect: str = "off",
    ) -> "WatchSession":
        """Connect to an in-process watch that speaks the real protocol.

        The same ``Watch`` code path runs, so examples and tests exercise the
        library rather than a mock. See
        :func:`~garmin_ble.transport.simulated.available_profiles`.
        """
        transport = SimulatedTransport(profile=profile, seed=seed)
        return WatchSession(
            lambda: transport,
            heartbeat=_seconds(heartbeat),
            reconnect=reconnect,
            supported=transport.profile.supports,
        )

    @classmethod
    def replay(
        cls,
        path: Union[str, Path],
        speed: Optional[float] = None,
        heartbeat: Optional[Duration] = None,
        reconnect: str = "off",
    ) -> "WatchSession":
        """Replay a capture written by :meth:`record`."""
        return WatchSession(
            lambda: ReplayTransport(path, speed=speed),
            heartbeat=_seconds(heartbeat),
            reconnect=reconnect,
        )

    # ── properties ──────────────────────────────────────────────────────────

    @property
    def info(self) -> DeviceInfo:
        """Identity and link parameters of the connected watch."""
        if self._info is None:
            raise NotConnected("watch info is only available once connected")
        return self._info

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport.is_open

    @property
    def capabilities(self) -> Sequence[Metric]:
        """Metrics this watch is known to support.

        Only a simulated or replayed watch can answer this up front; over BLE
        there is no capability query, so every metric is reported as possible
        and :meth:`subscribe` is what discovers the truth.
        """
        return self._capabilities if self._capabilities is not None else Metric.ALL_TELEMETRY

    @property
    def subscriptions(self) -> Sequence[Metric]:
        """Metrics currently registered and streaming."""
        return tuple(m for m, count in self._subscriptions.items() if count > 0)

    def latest(self, metric: "Metric[R]") -> Optional[R]:
        """The most recent reading seen for *metric* this session, if any.

        Several metrics — steps, intensity minutes, body battery — are sent once
        when their service starts and then only when the value changes, which on
        a resting wrist can be many minutes. Without a cache, anything that
        subscribes, reads, and unsubscribes sees them exactly once and then
        appears to lose them.
        """
        return self._latest.get(metric)  # type: ignore[return-value]

    # ── connection lifecycle ────────────────────────────────────────────────

    async def _connect(self) -> None:
        """Open the transport and complete the GFDI handshake."""
        self._closing = False
        link = await self._transport.open()
        self._max_write = link.max_write
        self._connected = True

        if self._dispatch_task is None or self._dispatch_task.done():
            self._dispatch_task = asyncio.ensure_future(self._dispatch_loop())

        if self._transport.is_passive:
            self._reset_protocol_state()
        else:
            await self._handshake()

        model, firmware = self._static_identity(link.name)
        self._info = DeviceInfo(
            name=link.name or "Garmin",
            address=link.address,
            mtu=link.mtu,
            model=model,
            firmware=firmware,
        )
        self._emit(ev.Connected(address=link.address, name=self._info.name))

        if self._heartbeat_interval:
            self._heartbeat_task = asyncio.ensure_future(self._heartbeat_loop())

        # Only now is everything wired, so a transport with traffic of its own
        # can safely start delivering it.
        await self._transport.start()

    def _static_identity(self, link_name: Optional[str]) -> "tuple[str, str]":
        """Best guess at model and firmware before the watch identifies itself.

        A simulated watch knows both up front. Over BLE the advert name is the
        only hint available at this point; the real values arrive shortly after
        in a DEVICE_INFORMATION message and replace these.
        """
        profile = getattr(self._transport, "profile", None)
        if profile is not None:
            return profile.model, profile.firmware
        return (link_name or "unknown"), "unknown"

    def _reset_protocol_state(self) -> None:
        self._handles.clear()
        self.diagnostics.handles.clear()
        self._cobs.reset()

    async def _handshake(self) -> None:
        """CLOSE_ALL, then register the GFDI control service.

        Both steps are awaited rather than fired and forgotten, so a watch that
        is busy talking to a phone produces a clear error instead of a
        connection that silently never carries data.
        """
        self._reset_protocol_state()

        loop = asyncio.get_event_loop()
        self._close_all_done = loop.create_future()
        await self._write(mlr.build_close_all_request(), FrameKind.CONTROL, handle=0)

        try:
            await asyncio.wait_for(self._close_all_done, self.HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise HandshakeError(
                "watch did not acknowledge CLOSE_ALL; it is probably connected "
                "to a phone (Garmin watches accept only one BLE client)",
                CLOSE_ALL,
            ) from None
        finally:
            self._close_all_done = None

        registered = await self._register_service(int(GarminService.GFDI))
        if not registered:
            raise HandshakeError(
                "watch refused to open the GFDI control channel", REGISTER_ML
            )

    async def _disconnect(self) -> None:
        self._closing = True
        self._connected = False

        for task in (self._heartbeat_task, self._reconnect_task, self._dispatch_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._heartbeat_task, self._reconnect_task, self._dispatch_task):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: B014 - teardown
                    pass
        self._heartbeat_task = self._reconnect_task = self._dispatch_task = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        await self._transport.close()

        self._all_telemetry.close()
        for fanout in self._metric_fanouts.values():
            fanout.close()
        self._events.close()

        if self._recorder is not None:
            self._recorder.close()
            self._recorder = None

    def _on_transport_drop(self, reason: str) -> None:
        """Transport-thread callback: the link went away."""
        was_connected = self._connected
        self._connected = False
        if not was_connected or self._closing:
            return

        self._emit(ev.Disconnected(reason=reason, expected=False))
        if self._reconnect_policy != "off" and self._transport.can_reconnect:
            self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnect with backoff and restore whatever was subscribed."""
        delay, max_delay, attempts = 2.0, 60.0, 0
        wanted = list(self.subscriptions)

        # Release the dead link first: reopening on top of it would leave the
        # old transport's tasks running and its stale frames still arriving.
        try:
            await self._transport.close()
        except Exception:  # pragma: no cover - best effort
            log.debug("Ignoring error closing the dropped link")

        while not self._closing and attempts < 10:
            attempts += 1
            try:
                await self._connect()
            except Exception as exc:
                log.info("Reconnect attempt %d failed: %s", attempts, exc)
                await asyncio.sleep(delay)
                if self._reconnect_policy == "exponential":
                    delay = min(delay * 2, max_delay)
                continue

            restored = 0
            for metric in wanted:
                try:
                    await self._start_metric(metric)
                    restored += 1
                except ServiceUnavailable as exc:
                    log.warning("Could not restore %s after reconnect: %s", metric.name, exc)
            self.diagnostics.reconnects += 1
            self._emit(ev.Reconnected(attempts=attempts, restored=restored))
            return

        log.warning("Giving up reconnecting after %d attempts", attempts)

    # ── sending ─────────────────────────────────────────────────────────────

    async def _write(self, payload: bytes, kind: FrameKind, handle: int) -> None:
        packet = mlr.encode_tx(handle, payload)
        self._trace(Direction.TX, kind, handle, packet)
        self.diagnostics.frames_tx += 1
        await self._transport.write(packet)

    async def _send_gfdi(self, frame: bytes) -> None:
        handle = self._handles.get(int(GarminService.GFDI))
        if handle is None:
            raise NotConnected("GFDI channel is not open")
        encoded = cobs.CobsCoDec.encode(frame)
        for chunk in mlr.fragment(encoded, self._max_write):
            await self._write(chunk, FrameKind.GFDI, handle)

    async def _reflex_gfdi(self, frame: bytes) -> None:
        """Send a reflex to inbound traffic — an ACK, or a time answer.

        Unlike a caller-initiated send, these are worthless once the link is
        down: a frame that arrived just before a drop would otherwise raise from
        a background task. Dropping it is the correct response.
        """
        if int(GarminService.GFDI) not in self._handles or not self.is_connected:
            return
        try:
            await self._send_gfdi(frame)
        except NotConnected:
            pass

    # ── receiving ───────────────────────────────────────────────────────────

    async def _on_packet(self, data: bytes) -> None:
        """Transport callback. Kept short: decode, route, hand off."""
        self.diagnostics.frames_rx += 1
        decoded = mlr.decode_packet(data)

        if decoded is None:
            self.diagnostics.malformed += 1
            self._trace(Direction.RX, FrameKind.UNKNOWN, 0, data)
            self._emit(ev.ProtocolWarning(detail=f"undecodable packet {data.hex()}"))
            return

        if isinstance(decoded, mlr.ControlMessage):
            self._trace(Direction.RX, FrameKind.CONTROL, 0, data, f"control type={decoded.type}")
            self._on_control(decoded)
            return

        if isinstance(decoded, mlr.RegisterMlResponse):
            self._trace(
                Direction.RX, FrameKind.CONTROL, decoded.handle, data,
                f"REGISTER_ML_RESP service={decoded.service} status={decoded.status}",
            )
            self._on_registration(decoded)
            return

        service = self._service_for_handle(decoded.handle)
        if service is None:
            self._trace(Direction.RX, FrameKind.UNKNOWN, decoded.handle, data)
            return

        if service == int(GarminService.GFDI):
            self._trace(Direction.RX, FrameKind.GFDI, decoded.handle, data)
            self._on_gfdi_bytes(decoded.payload[1:])
            return

        metric = by_service(service)
        if metric is None:
            self._trace(Direction.RX, FrameKind.UNKNOWN, decoded.handle, data)
            return

        # payload[0] is the MLR routing byte; the service payload starts after it.
        reading = metric.parse(decoded.payload[1:])
        self._trace(
            Direction.RX, FrameKind.TELEMETRY, decoded.handle, data,
            f"{metric.name}: {reading}" if reading else f"{metric.name}: (no sample)",
        )
        if reading is not None:
            self._publish(metric, reading)

    def _on_control(self, message: mlr.ControlMessage) -> None:
        from .constants import RequestType

        if message.type == RequestType.CLOSE_ALL_RESP:
            self._handles.clear()
            self.diagnostics.handles.clear()
            if self._close_all_done is not None and not self._close_all_done.done():
                self._close_all_done.set_result(True)

    def _on_registration(self, response: mlr.RegisterMlResponse) -> None:
        if response.accepted:
            self._handles[response.service] = response.handle
            metric = by_service(response.service)
            self.diagnostics.handles[service_label(response.service)] = response.handle
            self._emit(
                ev.ServiceRegistered(
                    service=response.service, handle=response.handle, metric=metric
                )
            )

        waiter = self._registration_waiters.pop(response.service, None)
        if waiter is not None and not waiter.done():
            waiter.set_result(response.accepted)

    def _service_for_handle(self, handle: int) -> Optional[int]:
        for service, assigned in self._handles.items():
            if assigned == handle:
                return service
        return None

    # ── GFDI ────────────────────────────────────────────────────────────────

    def _on_gfdi_bytes(self, payload: bytes) -> None:
        self._cobs.received_bytes(payload)
        while True:
            raw = self._cobs.retrieve_message()
            if not raw:
                break
            message = gfdi.parse_message(raw)
            if message is None:
                self.diagnostics.malformed += 1
                continue
            self._on_gfdi_message(message)

    def _on_gfdi_message(self, message: gfdi.GfdiMessage) -> None:
        # Every inbound GFDI message is acknowledged, as the watch expects.
        if message.type != GarminMessage.RESPONSE:
            self._schedule(self._reflex_gfdi(gfdi.GfdiMessageBuilder.build_status_ack(message.type)))

        if message.type in (GarminMessage.PROTOBUF_REQUEST, GarminMessage.PROTOBUF_RESPONSE):
            self._on_protobuf(message)
        elif message.type == GarminMessage.CURRENT_TIME_REQUEST:
            self._schedule(self._answer_time_request())
        elif message.type == GarminMessage.SYSTEM_EVENT:
            parsed = gfdi.parse_system_event(message)
            if parsed is not None:
                self._emit(ev.SystemEvent(event_type=parsed[0], value=parsed[1]))
        elif message.type == GarminMessage.DEVICE_INFORMATION:
            self._on_device_information(message)

    def _on_device_information(self, message: gfdi.GfdiMessage) -> None:
        """Adopt the model, firmware, and packet size the watch reports.

        Watches push this unprompted once the GFDI channel is open, so
        ``watch.info`` starts out with what the advert could tell us and is
        upgraded in place a moment later.
        """
        info = gfdi.parse_device_information(message)
        if info is None or self._info is None:
            return

        self._info = DeviceInfo(
            name=self._info.name,
            address=self._info.address,
            mtu=self._info.mtu,
            model=info.device_model or self._info.model,
            firmware=info.firmware,
        )
        # The watch's own limit can be stricter than the negotiated MTU.
        if info.max_packet_size:
            self._max_write = min(self._max_write, info.max_packet_size)
        self._emit(ev.DeviceIdentified(info=self._info))

    async def _answer_time_request(self) -> None:
        await self._reflex_gfdi(gfdi.GfdiMessageBuilder.build_time_response())
        self._emit(ev.TimeSyncRequested())

    def _on_protobuf(self, message: gfdi.GfdiMessage) -> None:
        frame = gfdi.parse_protobuf_frame(message)
        if frame is None:
            self.diagnostics.malformed += 1
            return

        self._schedule(
            self._reflex_gfdi(
                gfdi.GfdiMessageBuilder.build_protobuf_ack(
                    message.type, frame.request_id, frame.data_offset
                )
            )
        )

        if not frame.is_complete:
            # Chunked protobufs are rare in practice and reassembly is not
            # implemented; say so rather than decode a truncated message.
            self._emit(
                ev.ProtocolWarning(
                    detail=f"ignoring chunked protobuf (offset {frame.data_offset} "
                    f"of {frame.total_length} bytes)"
                )
            )
            return

        smart = gdi_smart_proto_pb2.Smart()
        try:
            smart.ParseFromString(frame.proto_bytes)
        except Exception as exc:
            self.diagnostics.malformed += 1
            self._emit(ev.ProtocolWarning(detail=f"undecodable protobuf: {exc}"))
            return

        payload = SmartRouter.unwrap(smart)
        handled = False

        # 1. Does this answer something we asked for?
        future = self._pending.pop(frame.request_id, None)
        if future is not None:
            sent_at = self._pending_sent_at.pop(frame.request_id, None)
            if sent_at is not None:
                self.diagnostics.record_latency((time.monotonic() - sent_at) * 1000)
            self.diagnostics.requests_answered += 1
            if not future.done():
                future.set_result(payload)
            handled = True

        # 2. Is it something the watch is asking us?
        elif payload is not None:
            responder = self._responders.get(type(payload).DESCRIPTOR.full_name)
            if responder is not None:
                self._schedule(self._run_responder(responder, payload, frame.request_id))
                handled = True

        self._emit(
            ev.ProtobufReceived(request_id=frame.request_id, message=payload, handled=handled)
        )

    async def _run_responder(self, responder: Handler, payload: Any, request_id: int) -> None:
        try:
            result = responder(payload)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            log.exception("Protobuf responder for %s raised", type(payload).__name__)
            return

        if result is None:
            return

        body = SmartRouter.wrap(result).SerializeToString()
        await self._send_gfdi(
            gfdi.GfdiMessageBuilder.build_protobuf_response(
                request_id=request_id,
                data_offset=0,
                total_length=len(body),
                proto_bytes=body,
            )
        )

    # ── dispatch ────────────────────────────────────────────────────────────

    def _publish(self, metric: Metric, reading: Reading) -> None:
        self._latest[metric] = reading
        fanout = self._metric_fanouts.get(metric)
        if fanout is not None:
            fanout.publish(reading)
        self._all_telemetry.publish(reading)
        if self._metric_handlers.get(metric):
            self._dispatch_queue.put_nowait((metric, reading))

    def _emit(self, event: ev.WatchEvent) -> None:
        self._events.publish(event)

    async def _dispatch_loop(self) -> None:
        """Run user handlers off the transport's callback path.

        Handlers run here rather than inline so a slow one delays only other
        handlers, never frame decoding — and so ``async def`` handlers are
        actually awaited instead of being dropped as un-awaited coroutines.
        """
        try:
            while True:
                metric, reading = await self._dispatch_queue.get()
                for handler in list(self._metric_handlers.get(metric, ())):
                    try:
                        result = handler(reading)
                        if inspect.isawaitable(result):
                            await result
                    except Exception:
                        log.exception("Handler for %s raised", metric.name)
        except asyncio.CancelledError:
            pass

    def _schedule(self, coro: Awaitable[None]) -> None:
        """Fire-and-forget a coroutine, logging rather than swallowing failures."""

        async def runner() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background protocol task failed")

        asyncio.ensure_future(runner())

    def _trace(
        self,
        direction: Direction,
        kind: FrameKind,
        handle: int,
        raw: bytes,
        decoded: Optional[str] = None,
    ) -> None:
        if not self._frame_handlers and self._recorder is None:
            return
        frame = Frame(direction=direction, kind=kind, handle=handle, raw=raw, decoded=decoded)
        if self._recorder is not None:
            self._recorder.write(frame)
        for handler in list(self._frame_handlers):
            try:
                handler(frame)
            except Exception:
                log.exception("Frame handler raised")

    # ── heartbeat ───────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        try:
            await self.sync_time()
            while self.is_connected:
                await asyncio.sleep(self._heartbeat_interval or 60.0)
                if not self.is_connected:
                    break
                await self.sync_time()
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("Heartbeat stopped")

    async def sync_time(self) -> None:
        """Push the current time to the watch. Also serves as the keep-alive."""
        await self._send_gfdi(
            gfdi.GfdiMessageBuilder.build_system_event(int(SystemEventType.TIME_UPDATED), 0)
        )
        self.diagnostics.heartbeats_sent += 1
        self.diagnostics.last_heartbeat = time.monotonic()
        self._emit(ev.TimeSyncSent())

    # ── subscriptions ───────────────────────────────────────────────────────

    async def _register_service(self, service: int) -> bool:
        """Ask for a handle and wait for the watch's actual answer.

        Concurrent callers for one service share a single request: the watch
        answers a given service code once, so issuing a second request would
        leave the first caller waiting on a reply that never comes.
        """
        waiter = self._registration_waiters.get(service)
        issuer = waiter is None

        if issuer:
            waiter = asyncio.get_event_loop().create_future()
            self._registration_waiters[service] = waiter
            await self._write(
                mlr.build_register_ml_request(service), FrameKind.CONTROL, handle=0
            )

        try:
            # Shielded so one caller's timeout cannot cancel the shared future.
            return await asyncio.wait_for(
                asyncio.shield(waiter), self.REGISTRATION_TIMEOUT
            )
        except asyncio.TimeoutError:
            return False
        finally:
            if issuer:
                self._registration_waiters.pop(service, None)

    async def _start_metric(self, metric: Metric) -> None:
        """Register a metric's service and tell the watch to start streaming."""
        if self._transport.is_passive:
            # A recording already contains whatever it contains.
            return
        if not self.is_connected:
            raise NotConnected(f"cannot subscribe to {metric.name} while disconnected")

        if self._capabilities is not None and metric not in self._capabilities:
            raise ServiceUnavailable(metric, f"not supported by {self.info.model}")

        service = int(metric.service)
        if service not in self._handles:
            accepted = await self._register_service(service)
            if not accepted:
                raise ServiceUnavailable(
                    metric,
                    "the watch refused or ignored the service registration",
                )

        await self._write(b"\x01", FrameKind.CONTROL, handle=self._handles[service])

    async def _stop_metric(self, metric: Metric) -> None:
        if self._transport.is_passive:
            return
        handle = self._handles.get(int(metric.service))
        if handle is None or not self.is_connected:
            return
        try:
            await self._write(b"\x00", FrameKind.CONTROL, handle=handle)
        except Exception:  # pragma: no cover - best effort on teardown
            log.debug("Could not stop %s cleanly", metric.name)

    def _subscription_lock(self, metric: Metric) -> "asyncio.Lock":
        """Serialise subscribe/unsubscribe for one metric.

        Both operations await mid-way through a read-modify-write of the
        reference count, so without this two concurrent subscribers can each
        see a count of zero and leave it at one.
        """
        lock = self._subscription_locks.get(metric)
        if lock is None:
            lock = asyncio.Lock()
            self._subscription_locks[metric] = lock
        return lock

    async def subscribe(self, *metrics: Metric) -> None:
        """Start streaming one or more metrics.

        Reference-counted: two callers can both want heart rate, and the stream
        stops only when the last one lets go. Raises
        :class:`~garmin_ble.errors.ServiceUnavailable` if the watch declines,
        rather than leaving a stream that never yields.
        """
        for metric in metrics:
            async with self._subscription_lock(metric):
                if self._subscriptions.get(metric, 0) == 0:
                    await self._start_metric(metric)
                    self._metric_fanouts.setdefault(metric, _Fanout())
                self._subscriptions[metric] = self._subscriptions.get(metric, 0) + 1

    async def unsubscribe(self, *metrics: Metric) -> None:
        """Release a subscription, stopping the stream when the last one goes."""
        for metric in metrics:
            async with self._subscription_lock(metric):
                count = self._subscriptions.get(metric, 0)
                if count <= 0:
                    continue
                if count == 1:
                    await self._stop_metric(metric)
                    self._subscriptions.pop(metric, None)
                else:
                    self._subscriptions[metric] = count - 1

    # ── consuming telemetry ─────────────────────────────────────────────────

    def on(self, metric: Metric) -> Callable[[Handler], Handler]:
        """Register a handler for a metric, subscribing on first use.

        The handler takes one typed reading. Sync and ``async def`` handlers are
        both supported and both run on the dispatch task, in arrival order::

            @watch.on(metrics.HEART_RATE)
            async def _(reading: metrics.HeartRate) -> None:
                print(reading.bpm)
        """

        def decorator(handler: Handler) -> Handler:
            self._metric_handlers.setdefault(metric, []).append(handler)
            self._schedule(self.subscribe(metric))
            return handler

        return decorator

    def on_frame(self, handler: Callable[[Frame], None]) -> Callable[[Frame], None]:
        """Register a raw-frame handler for protocol debugging."""
        self._frame_handlers.append(handler)
        return handler

    async def stream(self, metric: "Metric[R]") -> AsyncIterator[R]:
        """Yield readings for one metric until the caller stops iterating.

        Subscribes on entry and releases when the iterator is closed. Leaving
        the loop — by ``break``, ``return``, or an exception — schedules that
        close, so the release lands a few event-loop iterations later rather
        than on the next statement. Wrap the call in ``contextlib.aclosing`` if
        you need it to have happened by then::

            async with aclosing(watch.stream(metrics.HEART_RATE)) as readings:
                async for reading in readings:
                    ...
        """
        await self.subscribe(metric)
        fanout = self._metric_fanouts.setdefault(metric, _Fanout())
        queue = fanout.subscribe()
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            fanout.unsubscribe(queue)
            await self.unsubscribe(metric)

    async def stream_all(
        self,
        *metrics: Metric,
        timeout: Optional[Duration] = None,
    ) -> AsyncIterator[Reading]:
        """Yield readings from several metrics, interleaved in arrival order.

        With no arguments, subscribes to everything this watch supports.
        """
        wanted = list(metrics) or [m for m in Metric.ALL_TELEMETRY if m in self.capabilities]
        deadline = None
        seconds = _seconds(timeout)
        if seconds is not None:
            deadline = time.monotonic() + seconds

        subscribed: List[Metric] = []
        for metric in wanted:
            try:
                await self.subscribe(metric)
                subscribed.append(metric)
            except ServiceUnavailable as exc:
                log.info("Skipping %s: %s", metric.name, exc.reason)

        queue = self._all_telemetry.subscribe()
        try:
            while True:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return
                    try:
                        item = await asyncio.wait_for(queue.get(), remaining)
                    except asyncio.TimeoutError:
                        return
                else:
                    item = await queue.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            self._all_telemetry.unsubscribe(queue)
            for metric in subscribed:
                await self.unsubscribe(metric)

    async def read(self, metric: "Metric[R]", timeout: Optional[Duration] = 30.0) -> R:
        """Wait for exactly one reading, then release the subscription.

        Raises :class:`asyncio.TimeoutError` if nothing arrives in time.
        """
        seconds = _seconds(timeout, 30.0)
        agen = self.stream(metric)
        try:
            return await asyncio.wait_for(agen.__anext__(), seconds)
        finally:
            await agen.aclose()

    async def collect(
        self,
        metrics: Optional[Iterable[Metric]] = None,
        until: str = "first_sample_each",
        timeout: Optional[Duration] = 60.0,
        include_cached: bool = True,
    ) -> CollectionResult:
        """Gather samples until every requested metric has produced data.

        Returns as soon as the condition is met, or when the timeout expires —
        whichever comes first. The result says what arrived, how much of it, and
        why anything missing is missing.

        ``until`` accepts ``"first_sample_each"`` (default) or ``"timeout"``,
        which keeps collecting for the whole window regardless.

        ``include_cached`` seeds the result with the most recent reading already
        seen this session. Low-rate metrics are pushed once when their service
        starts and then only on change, so a strict "wait for a fresh sample"
        rule reports them missing on a resting wrist even though their value is
        known. Pass ``False`` to require every sample to arrive during the call.
        """
        if until not in ("first_sample_each", "timeout"):
            raise ValueError(f"unknown collect condition {until!r}")

        requested = list(metrics) if metrics is not None else list(Metric.ALL_TELEMETRY)
        result = CollectionResult(requested=requested)
        started = time.monotonic()
        seconds = _seconds(timeout, 60.0)

        subscribed: List[Metric] = []
        for metric in requested:
            try:
                await self.subscribe(metric)
                subscribed.append(metric)
            except ServiceUnavailable as exc:
                result.missing[metric] = Missing(metric, exc.reason, supported=False)

        if include_cached:
            for metric in requested:
                cached = self._latest.get(metric)
                if cached is not None:
                    result.samples[metric] = cached
                    result.counts[metric] = 0  # seen earlier, not during this call

        queue = self._all_telemetry.subscribe()
        try:
            while True:
                if until == "first_sample_each" and len(result.samples) + len(result.missing) >= len(requested):
                    break
                remaining = None if seconds is None else seconds - (time.monotonic() - started)
                if remaining is not None and remaining <= 0:
                    result.timed_out = True
                    break
                try:
                    item = await (
                        queue.get() if remaining is None else asyncio.wait_for(queue.get(), remaining)
                    )
                except asyncio.TimeoutError:
                    result.timed_out = True
                    break
                if item is _SENTINEL:
                    break
                metric = item.metric
                if metric in requested:
                    result.samples[metric] = item
                    result.counts[metric] = result.counts.get(metric, 0) + 1
                    result.missing.pop(metric, None)
        finally:
            self._all_telemetry.unsubscribe(queue)
            for metric in subscribed:
                await self.unsubscribe(metric)

        result.elapsed = time.monotonic() - started
        for metric in requested:
            if metric not in result.samples and metric not in result.missing:
                result.missing[metric] = Missing(
                    metric,
                    "no sample within the timeout — some metrics need movement "
                    "or a longer window",
                    supported=True,
                )
        return result

    # ── events ──────────────────────────────────────────────────────────────

    async def events(self) -> AsyncIterator[ev.WatchEvent]:
        """Yield link and protocol events until the session ends.

        Events are delivered live, not replayed: a consumer sees only what is
        published after it starts iterating. Start iterating before triggering
        whatever you mean to observe.
        """
        queue = self._events.subscribe()
        try:
            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    return
                yield item
        finally:
            self._events.unsubscribe(queue)

    # ── protobuf ────────────────────────────────────────────────────────────

    async def request(
        self,
        message: Message,
        timeout: Optional[Duration] = 10.0,
    ) -> Message:
        """Send a protobuf request and wait for its matching response.

        The message is the innermost service message; the ``Smart`` envelope is
        derived from the descriptors. Responses are matched by request id, so
        several requests can be in flight at once.
        """
        smart = SmartRouter.wrap(message)
        body = smart.SerializeToString()

        self._next_request_id = (self._next_request_id + 1) % 65536
        request_id = self._next_request_id

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[request_id] = future
        self._pending_sent_at[request_id] = time.monotonic()
        self.diagnostics.requests_sent += 1

        await self._send_gfdi(
            gfdi.GfdiMessageBuilder.build_protobuf_request(
                request_id=request_id,
                data_offset=0,
                total_length=len(body),
                proto_bytes=body,
            )
        )

        try:
            return await asyncio.wait_for(future, _seconds(timeout, 10.0))
        except asyncio.TimeoutError:
            raise RequestTimeout(
                f"no response to {type(message).__name__} within "
                f"{_seconds(timeout, 10.0):.0f}s",
                request_id=request_id,
            ) from None
        finally:
            self._pending.pop(request_id, None)
            self._pending_sent_at.pop(request_id, None)

    async def send(self, message: Message) -> None:
        """Send a protobuf without waiting for a reply."""
        body = SmartRouter.wrap(message).SerializeToString()
        self._next_request_id = (self._next_request_id + 1) % 65536
        await self._send_gfdi(
            gfdi.GfdiMessageBuilder.build_protobuf_request(
                request_id=self._next_request_id,
                data_offset=0,
                total_length=len(body),
                proto_bytes=body,
            )
        )

    def responds_to(self, message_type: Type[Message]) -> Callable[[Handler], Handler]:
        """Answer a message the watch sends us.

        Dispatch is on the concrete message type, and the handler returns the
        bare response message — the library wraps, frames, and correlates it::

            @watch.responds_to(DeviceStatusService.RemoteDeviceBatteryStatusRequest)
            def _(request):
                return DeviceStatusService.RemoteDeviceBatteryStatusResponse(...)
        """

        def decorator(handler: Handler) -> Handler:
            self._responders[message_type.DESCRIPTOR.full_name] = handler
            return handler

        return decorator

    # ── device queries ──────────────────────────────────────────────────────

    async def battery(self, timeout: Optional[Duration] = 10.0) -> Battery:
        """Read the watch's battery level."""
        ds = gdi_device_status_pb2.DeviceStatusService
        response = await self.request(ds.RemoteDeviceBatteryStatusRequest(), timeout=timeout)
        status = ds.ResponseStatus.Name(response.status) if response.HasField("status") else "ok"
        return Battery(percent=response.current_battery_level, status=status.lower())

    async def find_my_watch(
        self, duration: Duration = 10.0, timeout: Optional[Duration] = 10.0
    ) -> None:
        """Make the watch beep and vibrate so you can find it."""
        fmw = gdi_find_my_watch_pb2.FindMyWatchService
        await self.request(
            fmw.FindMyWatchRequest(timeout=int(_seconds(duration, 10.0))), timeout=timeout
        )

    async def stop_find_my_watch(self, timeout: Optional[Duration] = 10.0) -> None:
        """Cancel an in-progress find-my-watch alert."""
        fmw = gdi_find_my_watch_pb2.FindMyWatchService
        await self.request(fmw.FindMyWatchCancelRequest(), timeout=timeout)

    async def installed_apps(self, timeout: Optional[Duration] = 15.0) -> List[InstalledApp]:
        """List the apps, widgets, and watch faces installed on the device."""
        svc = apps_pb2.InstalledAppsService
        response = await self.request(
            svc.GetInstalledAppsRequest(appType=svc.AppType.ALL), timeout=timeout
        )
        return [
            InstalledApp(
                name=app.name,
                kind=svc.AppType.Name(app.type),
                version=app.version,
                disabled=app.disabled,
            )
            for app in response.installedApps
        ]

    # ── debugging ───────────────────────────────────────────────────────────

    def record(self, path: Union[str, Path]) -> None:
        """Write every frame, both directions, to a replayable capture file."""
        if self._recorder is not None:
            self._recorder.close()
        self._recorder = Recorder(path)


# ─────────────────────────────────────────────────────────────────────────────
#  Session
# ─────────────────────────────────────────────────────────────────────────────


class WatchSession:
    """A not-yet-open connection to a watch.

    Returned by every ``Watch`` factory. Usable as an async context manager,
    which is the intended form because it guarantees teardown::

        async with Watch.discover() as watch:
            ...

    It is also awaitable for callers who want to manage the lifetime
    themselves, in which case they own calling :meth:`aclose`.
    """

    def __init__(
        self,
        make_transport: Callable[[], Transport],
        heartbeat: Optional[float] = 60.0,
        reconnect: str = "exponential",
        supported: Optional[Sequence[Metric]] = None,
    ):
        self._make_transport = make_transport
        self._heartbeat = heartbeat
        self._reconnect = reconnect
        self._supported = supported
        self._watch: Optional[Watch] = None

    async def open(self) -> Watch:
        """Connect and complete the handshake, returning the live watch."""
        watch = Watch(
            self._make_transport(),
            heartbeat=self._heartbeat,
            reconnect=self._reconnect,
            supported=self._supported,
        )
        try:
            await watch._connect()
        except Exception:
            await watch._disconnect()
            raise
        self._watch = watch
        return watch

    async def aclose(self) -> None:
        if self._watch is not None:
            await self._watch._disconnect()
            self._watch = None

    async def __aenter__(self) -> Watch:
        return await self.open()

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.aclose()
        return False

    def __await__(self):
        return self.open().__await__()


__all__ = [
    "Watch",
    "WatchSession",
    "DeviceInfo",
    "Battery",
    "InstalledApp",
    "Diagnostics",
    "Duration",
]
