# Roadmap

garmin-ble hasn't reached 1.0. The public API can still change between minor
versions, and the [changelog](./CHANGELOG.md) lists every breaking change.

Each release below is a
[GitHub milestone](https://github.com/gwerneckp/garmin-ble/milestones). The
details are in the issues. This page is the overview and explains the order.

_Last updated: 2026-10-23 (v0.3.2)._

## Current status

| Release | Goal | Status |
|---------|------|--------|
| v0.1.0 | BLE transport and handshake | Done |
| v0.2.0 | Live telemetry streaming | Done |
| v0.3.0 | `Watch` API, transports, simulator, and replay | Done |
| v0.4.0 | Protobuf settings and device state | In progress |
| v0.5.0 | Notifications and media control | Planned |
| v0.6.0 | File transfers (FIT and GPX) | Planned |
| v1.0.0 | Stable release | Planned |

Working now: connecting and the GFDI V2 handshake, 10 live telemetry streams
(heart rate, steps, HRV, SpO2, respiration, calories, intensity minutes,
stress, Body Battery, accelerometer), reconnection, the keep-alive heartbeat,
awaitable protobuf requests, device status and battery, Garmin binary JSON, the
simulator, and record and replay.

## v0.4.0: device state and protobuf messages (in progress)

The protobuf envelope, request correlation, and Garmin JSON are done (#9, #12).
The remaining work uses them for the watch's own state.

- [ ] Settings sync: read and write user settings ([#10](https://github.com/gwerneckp/garmin-ble/issues/10))
- [ ] App config: acknowledge watch face and data field config requests ([#11](https://github.com/gwerneckp/garmin-ble/issues/11))
- [ ] Weather sync: build and send the weather protobuf ([#13](https://github.com/gwerneckp/garmin-ble/issues/13))
- [ ] Find My Phone and Find My Watch ([#14](https://github.com/gwerneckp/garmin-ble/issues/14))

Following the [design philosophy](./README.md#design-philosophy), weather and
Find My Phone will be builders and callbacks. The library won't fetch forecasts
or play sounds.

## v0.5.0: notifications and media control (planned)

- [ ] Send notifications to the watch ([#15](https://github.com/gwerneckp/garmin-ble/issues/15))
- [ ] Call control ([#16](https://github.com/gwerneckp/garmin-ble/issues/16))
- [ ] Music control ([#17](https://github.com/gwerneckp/garmin-ble/issues/17))

## v0.6.0: file transfers (planned)

This is the largest piece of protocol work left. It is also what gives access
to stored data such as activities and sleep, in addition to live data.

- [ ] File transfer handshake ([#18](https://github.com/gwerneckp/garmin-ble/issues/18))
- [ ] Binary chunk reassembly and ACKs ([#19](https://github.com/gwerneckp/garmin-ble/issues/19))
- [ ] Parse FIT files into typed objects ([#20](https://github.com/gwerneckp/garmin-ble/issues/20))
- [ ] Upload routes and activities ([#21](https://github.com/gwerneckp/garmin-ble/issues/21))
- [ ] Install apps and watch faces (`.PRG`) ([#22](https://github.com/gwerneckp/garmin-ble/issues/22))

## v1.0.0: stable release (planned)

1.0 is about a stable API. Some features may still be missing. The criteria:

- [ ] At least one minor release of the `Watch` API with no breaking changes
- [ ] A device compatibility table built from user reports ([#23](https://github.com/gwerneckp/garmin-ble/issues/23))
- [ ] A documentation site and guides ([#29](https://github.com/gwerneckp/garmin-ble/issues/29), [#30](https://github.com/gwerneckp/garmin-ble/issues/30), [#31](https://github.com/gwerneckp/garmin-ble/issues/31))
- [ ] Type checking and linting in CI
- [ ] The PyPI classifier changed from Alpha to `Development Status :: 5 - Production/Stable`

## Not scheduled yet

These have issues but no milestone. Each is fairly self-contained.

- ECG data ([#24](https://github.com/gwerneckp/garmin-ble/issues/24))
- HTTP proxy for requests from the watch ([#25](https://github.com/gwerneckp/garmin-ble/issues/25))
- Calendar sync ([#26](https://github.com/gwerneckp/garmin-ble/issues/26))
- Handling differences between Instinct, Forerunner, Venu, and Vivo models ([#23](https://github.com/gwerneckp/garmin-ble/issues/23))

## Out of scope

- A replacement for the Garmin Connect app. Use [Gadgetbridge](https://codeberg.org/Freeyourgadget/Gadgetbridge).
- Cloud sync or integrations with external services. Build these on top of the
  library.
- Watch firmware updates.

## Contributing

You can pick up any open item above. Comment on its issue and start. Most of
them need reverse engineering before any library code can be written, so a PR
that explains why you made each choice, and lists what you couldn't work out,
is welcome. Proposals to reorder or change this roadmap are welcome too if they
come with reasons. See
[CONTRIBUTING.md](./CONTRIBUTING.md#picking-up-roadmap-work).

If you have a watch model that hasn't been tested yet, running the walkthrough
example on it is the most useful thing you can do.
