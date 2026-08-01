#!/usr/bin/env python3
"""Full Walkthrough — a guided tour of the garmin-ble API.

Usage:
    python examples/full_walkthrough_idealistic.py            # simulated watch
    python examples/full_walkthrough_idealistic.py --real     # your actual watch

Defaults to a simulated watch, so it runs anywhere with no hardware. Covers
connecting, every way of consuming telemetry, device queries, raw protobuf in
both directions, error handling, and the debugging affordances.

For a pass/fail check that every feature works against your own watch, use
``full_walkthrough.py`` instead.
"""

import argparse
import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path

# Everything comes from one namespace. No reaching into `garmin_ble.protobuf.*`
# for common operations, no `client.client.mtu_size` to read the MTU.
from garmin_ble import (
    Checklist,
    Metric,
    RequestTimeout,
    ServiceUnavailable,
    Watch,
    events,
    metrics,
)


# ─────────────────────────────────────────────────────────────────────────────
#  1. Connecting
# ─────────────────────────────────────────────────────────────────────────────

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

async def walkthrough(watch: Watch) -> None:
    print(f"\nConnected to {watch.info.name} ({watch.info.address})")

    # Real watches push a DEVICE_INFORMATION message just after the channel
    # opens; until it lands, `model` is only the advert name and `firmware` is
    # unknown. Waiting a beat lets `watch.info` be upgraded in place.
    await asyncio.sleep(0.5)
    print(f"  model={watch.info.model} firmware={watch.info.firmware} mtu={watch.info.mtu}")

    # There is deliberately nothing here listing what the watch supports. The
    # protocol has no capability query, so any such list would be a guess.
    # Asking is the only honest test: subscribe() raises ServiceUnavailable
    # when the watch declines, stream_all() emits a MetricUnavailable event for
    # what it skips, and collect() marks it missing with supported=False.

    await stream_telemetry(watch)
    await device_queries(watch)
    await protobuf_and_events(watch)
    await error_handling()
    await debugging(watch)


# ─────────────────────────────────────────────────────────────────────────────
#  3. Telemetry — subscription implies registration
# ─────────────────────────────────────────────────────────────────────────────

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
    seen = set()
    async for sample in watch.stream_all(timeout=timedelta(seconds=2)):
        if sample.metric not in seen:
            seen.add(sample.metric)
            print(f"  {sample.metric.name:<14} {sample}")  # every reading has a good __str__
        if len(seen) >= 4:
            break

    # ---- 3d. Collect-until-complete: the thing this walkthrough exists for ---
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

    # Responding to the watch's own requests: dispatch on the concrete message
    # type and return the bare response. The library wraps it in the Smart
    # envelope, frames it, and matches it to the incoming request id.

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

async def error_handling() -> None:
    """Show a real failure, and describe the ones we will not provoke.

    This deliberately does not scan for hardware: the tour runs simulated by
    default, and a section that quietly used the radio would be both slow and
    dependent on what happens to be in the room.
    """
    print("\n── errors ──────────────────────────────────────────────")

    # A genuine refusal, provoked on a model that lacks the sensor. The Venu 3
    # profile has no SpO2, and asking is the only way to find that out — the
    # protocol has no capability query.
    async with Watch.simulated(profile="venu3") as watch:
        try:
            await watch.subscribe(metrics.SPO2)
        except ServiceUnavailable as exc:
            print(f"  ServiceUnavailable: {exc.metric.name} — {exc.reason}")

    # The two connection failures, described rather than provoked:
    #
    #   WatchNotFound   — no match in range. `exc.candidates` lists every device
    #                     the scan did see, which is usually what you need: the
    #                     watch is normally there under a name the filter missed,
    #                     or absent because it is paired to a phone.
    #
    #   HandshakeError  — the link came up but the protocol did not.
    #                     `exc.stage` is characteristic_discovery (not a Garmin),
    #                     close_all (busy with a phone), or register_ml.
    print("  WatchNotFound / HandshakeError: see the source of this function")


# ─────────────────────────────────────────────────────────────────────────────
#  7. Debugging affordances
# ─────────────────────────────────────────────────────────────────────────────

async def debugging(watch: Watch) -> None:
    print("\n── diagnostics ─────────────────────────────────────────")

    # Record every frame in both directions to a replayable capture. Attach one
    # to a bug report and a maintainer reproduces it with `Watch.replay(path)` —
    # no hardware, no wrist.
    capture = Path(tempfile.gettempdir()) / "garmin-ble-session.gble"
    watch.record(capture)

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
    print(f"\n  capture written to {capture}")


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
