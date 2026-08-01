#!/usr/bin/env python3
"""Full Walkthrough — the API tour, annotated with what it replaced.

This file started life as a design sketch written against an API that did not
exist. It now runs: the sketch became the library. Every ``# WAS:`` block quotes
the call this version replaced, so the delta stays legible.

Usage:
    python examples/full_walkthrough_idealistic.py            # simulated watch
    python examples/full_walkthrough_idealistic.py --real     # your actual watch

The default is a simulated watch, so this runs anywhere with no hardware. That
is itself one of the changes: the library had no way to be exercised without a
physical device in range and disconnected from your phone.

The design goals, in priority order:

  1. **The type system should know what you're doing.** Every event used to be a
     string (``client.on("hr", ...)``) and every payload untyped positional args
     of unguessable arity. ``examples/telemetry_advanced.py`` called
     ``GarminService.REALTIME_HEART_RATE``, which did not exist — the member was
     ``REALTIME_HR``. An AttributeError at runtime that a typed API catches in
     the editor.

  2. **One concept, one registry.** A metric used to live in three disconnected
     places: the ``GarminService`` enum, the ``callbacks`` dict key, and
     ``_PARSE_TABLE``. Subscribing meant knowing all three lined up. Now a
     metric is one object, and subscribing to it registers the service.

  3. **Failures raise.** ``connect()`` returning ``False`` meant every caller
     wrote the same "if not success" block and nobody ever learned *why*.

  4. **Request/response is awaitable.** Reading the battery took twenty lines,
     three protobuf imports, a global processor, and a polling loop.

  5. **You can develop without a watch on your wrist.**
"""

import argparse
import asyncio
from datetime import timedelta

# Everything comes from one namespace. No reaching into `garmin_ble.protobuf.*`
# for common operations, no `client.client.mtu_size` to read the MTU.
from garmin_ble import (
    Checklist,
    Metric,
    RequestTimeout,
    ServiceUnavailable,
    Watch,
    WatchNotFound,
    events,
    metrics,
)
from garmin_ble.errors import HandshakeError


# ─────────────────────────────────────────────────────────────────────────────
#  1. Connecting
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS:
#     client = GarminClient()
#     success = await client.connect(timeout=30.0)
#     if not success:
#         print("Could not find or connect to a Garmin watch.")
#         return
#     ...
#     client.enable_heartbeat(60.0)
#     sync_task = asyncio.create_task(client.start_sync_loop())
#     ...
#     sync_task.cancel()
#     await client.disconnect()
#
# Five lifecycle calls the caller had to sequence correctly, plus a task they
# had to remember to cancel, plus a bool they had to check.
#
# NOW: one context manager. Heartbeat and reconnection are policy, not
# procedure — tuned by argument rather than by calling an extra method at the
# right moment. Exiting the block always disconnects, including on exception
# and on Ctrl+C.


def open_watch(real: bool):
    """Return the session to run the tour against."""
    if real:
        return Watch.discover(
            timeout=timedelta(seconds=30),
            heartbeat=timedelta(seconds=60),  # None disables it
            reconnect="exponential",          # or "off"
        )
    return Watch.simulated(profile="fenix7")

    # Other factories, all returning the same kind of session:
    #
    #   Watch.at("C7:1B:...")             # known address, no scan
    #   Watch.named("fenix*")             # glob on the advert name
    #   Watch.simulated(profile="venu3")  # a model that lacks some metrics
    #   Watch.replay("capture.gble")      # a recorded session


async def main(real: bool) -> None:
    async with open_watch(real) as watch:
        await walkthrough(watch)


# ─────────────────────────────────────────────────────────────────────────────
#  2. What did we actually connect to?
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS:
#     client.device.name if client.device else 'Unknown'
#     client.address
#     client.client.mtu_size if client.client else '?'
#
# Three attributes, two possibly None, one reaching through to the underlying
# bleak object. `watch.info` is a frozen dataclass that only exists once
# connected, so none of its fields are Optional.


async def walkthrough(watch: Watch) -> None:
    print(f"\nConnected to {watch.info.name} ({watch.info.address})")

    # Real watches push a DEVICE_INFORMATION message just after the channel
    # opens; until it lands, `model` is only the advert name and `firmware` is
    # unknown. Waiting a beat lets `watch.info` be upgraded in place.
    await asyncio.sleep(0.5)
    print(f"  model={watch.info.model} firmware={watch.info.firmware} mtu={watch.info.mtu}")

    # Ask before you subscribe, instead of waiting 60s for data this model will
    # never send. Over real BLE there is no capability query, so every metric is
    # reported as possible and subscribe() is what discovers the truth.
    print(f"  supports: {', '.join(m.name for m in watch.capabilities)}")

    await stream_telemetry(watch)
    await device_queries(watch)
    await protobuf_and_events(watch)
    await debugging(watch)


# ─────────────────────────────────────────────────────────────────────────────
#  3. Telemetry — subscription implies registration
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS:
#     client.on("hr", lambda hr, r: on_heart_rate(tracker, hr, r))
#     client.on("accel", lambda p: on_accel(tracker, p))
#     ...twelve more lambdas whose arity you had to memorise...
#     await client.register_and_start_service(GarminService.REALTIME_HR)
#     await client.register_and_start_service(GarminService.REALTIME_ACCELEROMETER)
#     ...ten more...
#
# Two parallel lists kept in sync by hand, joined only by a convention
# ("hr" ↔ REALTIME_HR) the type checker could not see.
#
# NOW: `Metric` is the single handle. Subscribing registers the service and
# starts the stream; the last unsubscribe stops it. Reference-counted, so two
# parts of an app can both want heart rate without fighting.


async def stream_telemetry(watch: Watch) -> None:
    print("\n── telemetry ───────────────────────────────────────────")

    # ---- 3a. Decorator form: typed, discoverable, one dataclass argument ----
    #
    # `reading` is a `HeartRate`, not a bare tuple. `reading.bpm` autocompletes;
    # `resting_bpm` is Optional because the watch genuinely omits it sometimes —
    # that used to arrive as a silent 0.

    shown = {"hr": 0, "accel": 0}  # this is a tour; don't flood the terminal

    @watch.on(metrics.HEART_RATE)
    async def _(reading: metrics.HeartRate) -> None:
        # Async handlers are awaited properly. The old dispatcher called
        # `cb(*result)` and dropped the coroutine on the floor, so an
        # `async def` handler silently never ran.
        shown["hr"] += 1
        if shown["hr"] <= 3:
            print(f"  ❤️  {reading.bpm} bpm (resting {reading.resting_bpm}) @{reading.at:%H:%M:%S}")

    @watch.on(metrics.ACCELEROMETER)
    def _(packet: metrics.AccelPacket) -> None:
        # Raw counts and g-units both available, instead of the caller dividing
        # by a magic constant — the old examples disagreed on whether it was
        # 256 or 1024.
        shown["accel"] += 1
        if shown["accel"] <= 3:
            x, y, z = packet.samples[0].g
            print(f"  ⌚ {len(packet.samples)} samples, first = {x:+.3f}g {y:+.3f}g {z:+.3f}g")

    await asyncio.sleep(0.3)

    # ---- 3b. Async-iterator form, when a callback is the wrong shape ----
    #
    # Callbacks cannot await, be cancelled, compose, or apply backpressure. An
    # async iterator does all four and needs no tracker object to smuggle state
    # out of a closure.

    async for reading in watch.stream(metrics.STRESS):
        print(f"  🧘 stress {reading.level}/100 ({reading.band})")
        break  # leaving the loop releases the subscription

    # ---- 3c. Merged stream, for "give me everything" ----
    #
    # WAS: twelve separate callbacks all writing into a shared FeatureTracker,
    # plus a `while time.monotonic() < deadline: await asyncio.sleep(0.5)` poll
    # to find out when they had all arrived.

    seen = set()
    async for sample in watch.stream_all(timeout=timedelta(seconds=2)):
        if sample.metric not in seen:
            seen.add(sample.metric)
            print(f"  {sample.metric.name:<14} {sample}")  # every reading has a good __str__
        if len(seen) >= 4:
            break

    # ---- 3d. Collect-until-complete: the thing this walkthrough exists for ---
    #
    # WAS: ~40 lines of FeatureTracker, a manual deadline poll, and a set
    # difference to work out what was missing.
    #
    # NOW: one await. Returns when every requested metric has produced at least
    # one sample, or when the timeout expires — whichever comes first.

    print("\n  collecting one sample of every metric…")
    result = await watch.collect(
        Metric.ALL_TELEMETRY,
        until="first_sample_each",
        timeout=timedelta(seconds=20),
    )

    print(Checklist.from_collection(result))  # renders the ✅/⏳ table for you
    if result.missing:
        # Typed and actionable: each entry knows *why* it is missing, and
        # `supported` distinguishes "needs movement" from "never coming".
        for metric, reason in result.missing.items():
            print(f"    ⏳ {metric.name}: {reason}")

    # ---- 3e. One-shot read, no subscription bookkeeping ----
    hr = await watch.read(metrics.HEART_RATE, timeout=timedelta(seconds=10))
    print(f"\n  single reading: {hr.bpm} bpm")


# ─────────────────────────────────────────────────────────────────────────────
#  4. Device queries — protobuf, but you don't have to know that
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS, to find out the battery level:
#
#     def handle_device_status(tracker, msg):
#         if msg.HasField("remote_device_battery_status_response"):
#             tracker.samples["battery_level"] = \
#                 msg.remote_device_battery_status_response.current_battery_level
#             tracker.checks["battery_rx"] = True
#         ...
#     client.protobuf_handler.register_processor("device_status_service", ...)
#     request = gdi_smart_proto_pb2.Smart(
#         device_status_service=gdi_device_status_pb2.DeviceStatusService(
#             remote_device_battery_status_request=
#                 gdi_device_status_pb2.DeviceStatusService
#                     .RemoteDeviceBatteryStatusRequest()))
#     await client.send_protobuf(request)
#     for _ in range(20):
#         if tracker.checks.get("battery_rx"):
#             break
#         await asyncio.sleep(0.5)
#
# Twenty lines, three protobuf imports, a global processor, and a polling loop —
# to read one integer. The request id was generated internally and thrown away,
# so there was no way to correlate a response with its request.


async def device_queries(watch: Watch) -> None:
    print("\n── device ──────────────────────────────────────────────")

    # Battery is answered by every model.
    battery = await watch.battery()
    print(f"  🔋 {battery.percent}% ({battery.status})")

    # The rest are optional: which protobuf services a watch actually answers
    # varies by model and firmware, and some need a paired-app authentication
    # this library does not perform. A request nobody answers raises
    # RequestTimeout rather than hanging forever or returning an empty result
    # that looks like "no apps installed".
    try:
        for app in await watch.installed_apps(timeout=timedelta(seconds=5)):
            print(f"  📱 {app}")
    except RequestTimeout:
        print("  · installed_apps: not answered by this watch")

    try:
        await watch.find_my_watch(duration=timedelta(seconds=3), timeout=timedelta(seconds=5))
        print("  🔔 find-my-watch triggered")
    except RequestTimeout:
        print("  · find_my_watch: not answered by this watch")


# ─────────────────────────────────────────────────────────────────────────────
#  5. Raw protobuf — still available, now correlated and awaitable
# ─────────────────────────────────────────────────────────────────────────────


async def protobuf_and_events(watch: Watch) -> None:
    from garmin_ble.protobuf import gdi_device_status_pb2

    device_status = gdi_device_status_pb2.DeviceStatusService

    print("\n── protobuf ────────────────────────────────────────────")

    # `request` matches the response by request id and returns it. The old
    # `send_protobuf` was fire-and-forget and the reply surfaced on a global
    # "protobuf" callback with no link back to what you asked for — so two
    # in-flight requests were indistinguishable. The Smart envelope is derived
    # from the protobuf descriptors, so you pass the innermost message.
    response = await watch.request(
        device_status.RemoteDeviceBatteryStatusRequest(),
        timeout=timedelta(seconds=10),
    )
    print(f"  raw response: {response.current_battery_level}%")

    # Responding to the *watch's* requests. WAS: one processor per top-level
    # service field, with a chain of `if msg.HasField(...)` inside it, returning
    # a fully-nested `Smart` you built by hand.
    #
    # NOW: dispatch on the concrete message type; return the bare response
    # message and let the library wrap, frame, and correlate it.

    @watch.responds_to(device_status.RemoteDeviceBatteryStatusRequest)
    async def _(request):
        print("  📱 watch asked for phone battery — replying 99%")
        return device_status.RemoteDeviceBatteryStatusResponse(current_battery_level=99)

    # System events as typed objects rather than `(int, int)` positional args
    # whose meaning you looked up in the library source.
    print("\n── events ──────────────────────────────────────────────")

    async def show_events(limit: int) -> None:
        count = 0
        async for event in watch.events():
            if isinstance(event, events.TimeSyncRequested):
                print("  ⏱  watch asked for the time (auto-answered)")
            elif isinstance(event, events.Disconnected):
                print(f"  ⚠️  link dropped: {event.reason}")
            elif isinstance(event, events.ServiceRegistered):
                print(f"  🔗 {event}")
            else:
                print(f"  · {event}")
            count += 1
            if count >= limit:
                return

    # Nudge the watch so there is something to show, then bound our patience:
    # the event stream never ends on its own, which is the point of it.
    watcher = asyncio.ensure_future(show_events(3))
    await asyncio.sleep(0.1)
    await watch.sync_time()
    try:
        await asyncio.wait_for(watcher, timeout=3.0)
    except asyncio.TimeoutError:
        print("  (quiet — no further events in 3s)")


# ─────────────────────────────────────────────────────────────────────────────
#  6. Errors that say what went wrong
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS: `connect()` returned False. `register_and_start_service()` returned None
# after `await asyncio.sleep(0.5)` — it never checked whether the watch had
# actually assigned a handle, so a failed registration looked exactly like a
# successful one until the data silently never arrived. Parse errors were
# logged and swallowed. There was not a single exception type in the library.


async def error_handling() -> None:
    try:
        async with Watch.discover(timeout=timedelta(seconds=10)) as watch:
            await watch.subscribe(metrics.SPO2)

    except WatchNotFound as exc:
        # Carries every device the scan *did* see, so the message can be useful
        # instead of "returned False".
        print(f"  no watch: {exc}")
        print(f"  candidates: {[d.name for d in exc.candidates]}")

    except HandshakeError as exc:
        # exc.stage is characteristic_discovery | close_all | register_ml
        print(f"  handshake failed at {exc.stage}: {exc}")

    except ServiceUnavailable as exc:
        # Raised by subscribe() when the watch declines registration, instead of
        # the old silent 0.5s sleep and a stream that never fired.
        print(f"  {exc.metric.name} unavailable: {exc.reason}")


# ─────────────────────────────────────────────────────────────────────────────
#  7. Debugging affordances
# ─────────────────────────────────────────────────────────────────────────────
#
# WAS: `configure(level=logging.DEBUG)`, which turned on firehose logging for
# the whole process and gave you hex dumps to decode by eye.


async def debugging(watch: Watch) -> None:
    print("\n── diagnostics ─────────────────────────────────────────")

    # Record every frame in both directions to a replayable capture. Attach one
    # to a bug report and a maintainer reproduces it with `Watch.replay(path)` —
    # no hardware, no wrist.
    watch.record("session.gble")

    # Per-watch structured tracing, not global logging config.
    frames = []

    @watch.on_frame
    def _(frame) -> None:
        frames.append(frame)

    await watch.read(metrics.HEART_RATE, timeout=timedelta(seconds=10))
    for frame in frames[:3]:
        print(f"  {frame}")

    # Live view of protocol state that used to exist only as a private dict
    # (`client.service_handles`) with no public accessor.
    print()
    for line in watch.diagnostics.summary().splitlines():
        print(f"  {line}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--real",
        action="store_true",
        help="use a real watch over BLE instead of the simulator",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.real))
    except KeyboardInterrupt:
        print("\nInterrupted.")
