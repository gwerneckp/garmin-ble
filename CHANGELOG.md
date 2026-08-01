# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0: the public API may still change between minor
versions, and breaking changes are called out explicitly.

## [0.3.1] - 2026-08-01

### Removed

- **`Watch.capabilities`.** It looked like device introspection but returned
  every metric unconditionally over BLE, because the GFDI protocol has no
  capability query. Answering "everything" when the truth is "I cannot know"
  invited callers to trust it and then fail at `subscribe()` anyway.

  There is no reliable way to probe for support either — a watch that lacks a
  sensor may accept the service registration and simply never send data — so
  asking is the only honest test. Migrate by handling the refusal where it
  happens:

  ```python
  # before
  if metrics.SPO2 in watch.capabilities:
      await watch.subscribe(metrics.SPO2)

  # after
  try:
      await watch.subscribe(metrics.SPO2)
  except ServiceUnavailable as exc:
      print(f"not available: {exc.reason}")
  ```

- **Python 3.9 support.** 3.9 reached end of life in October 2025. The minimum
  is now 3.10.

### Added

- **`MetricUnavailable` event.** `stream_all()` skips metrics the watch declines
  so that one refusal cannot end the whole stream, but it used to swallow them
  into a log line, leaving callers unable to learn what they were not
  receiving. It now emits an event instead:

  ```python
  async for event in watch.events():
      if isinstance(event, events.MetricUnavailable):
          print(f"{event.metric.name} declined: {event.reason}")
  ```

A declined metric therefore surfaces three ways, each at the moment of refusal:
`ServiceUnavailable` from `subscribe()`, `MetricUnavailable` from
`stream_all()`, and `Missing(supported=False)` from `collect()`.

## [0.3.0] - 2026-08-01

Replaced `GarminClient` with the `Watch` API.

### Added

- **`Watch`**, an async context manager owning the whole session — connecting,
  the GFDI handshake, the keep-alive heartbeat, reconnection, and teardown.
- **Typed metrics.** Readings are frozen dataclasses with named fields.
  Subscribing to a metric registers and starts its service, so the service
  code, the event name, and the parser can no longer disagree.
- **An exception hierarchy.** `WatchNotFound` carries the devices the scan did
  see, `HandshakeError` names the stage it stopped at, and `ServiceUnavailable`
  is raised when the watch declines a registration.
- **Awaitable protobuf requests**, correlated by request id so several can be in
  flight. The `Smart` envelope is derived from the compiled descriptors, making
  53 service message types routable with no per-service code.
- **A transport layer** with three backends: real BLE, an in-process simulator
  that speaks the protocol, and replay of recorded captures. Examples and tests
  run with no hardware.
- **Frame tracing and diagnostics.** `watch.record(path)` writes a capture that
  `Watch.replay(path)` reads back.
- **`DEVICE_INFORMATION` parsing**, so model, firmware, and the watch's own max
  packet size are known rather than guessed.

### Fixed

- Service registration is confirmed against the watch's `REGISTER_ML_RESP`
  rather than assumed after a fixed sleep, so a refused registration no longer
  looks identical to a successful one.
- Concurrent subscribes to one metric issued two `REGISTER_ML_REQ`s; the second
  clobbered the first's waiter, so the first timed out on a service the watch
  had accepted.
- The subscription reference count was read-modify-written across an `await`,
  so two subscribers could leave it at one and the first release would stop a
  stream the second still wanted.
- MLR handles of `0x80` and above set the flag bit and decoded back as a
  different handle, silently misrouting packets.
- `async def` telemetry handlers were dropped as un-awaited coroutines and
  never ran.

### Removed

- `GarminClient`, `GarminClientBase`, and `ProtobufHandler`. `cobs`, `crc`, and
  `gfdi` moved under `garmin_ble.protocol`.

[0.3.1]: https://github.com/gwerneckp/garmin-ble/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/gwerneckp/garmin-ble/compare/v0.2.3...v0.3.0
