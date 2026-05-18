# garmin_ble — Versioned Roadmap

**Current version:** `0.1.0`
**Target version:** `1.0.0` (end of Phase 5)

```
0.1.0 ──► 0.2.0 ──► 0.3.0 ──► 0.4.0 ──► 0.5.0 ──► 1.0.0
Phase 1    Phase 2   Phase 3   Phase 4   Phase 5   Stable release
```

Each minor version bump marks the completion of one phase.  After `1.0.0`,
Phases 6+ become `1.x` feature releases.

---

## Phase 1: Core Transport & Connectivity [v0.1.0] ✅

The BLE transport layer. Devices are discovered, connected, and the MLR
multiplexing channel is established.

### Done in 0.1.0

| Component | What it does |
|-----------|-------------|
| **BLE Discovery** | Scans for Garmin/Fenix devices by name, connects via `bleak`, stores device reference. File: `client.py::connect()` |
| **UUID Layout** | Defines the Garmin base UUID `6A4Exxxx-667B-11E3-949A-0800200C9A66` and characteristic range (RX 0x2810-0x2814, TX 0x2820-0x2824). File: `constants.py` |
| **CLOSE_ALL Handshake** | Sends `CLOSE_ALL_REQ` (type 5) to reset prior MLR assignments on the watch. File: `client.py::connect()` |
| **REGISTER_ML Handshake** | Sends `REGISTER_ML_REQ` (type 0) for each service after `CLOSE_ALL_RESP`; stores the dynamically assigned handles on `REGISTER_ML_RESP` (type 1). File: `client.py::_notify_handler()` |
| **MLR Multiplexing** | Reads the handle nibble from the first byte of incoming data to route messages to the correct service handler. File: `client.py::_notify_handler()` |
| **COBS Framing** | Encodes/decodes GFDI messages with Consistent Overhead Byte Stuffing — leading/trailing `0x00` delimiters, chunked streaming, buffered reassembly. File: `cobs.py` |
| **CRC16 (Garmin table)** | Computes the GFDI frame integrity checksum using Garmin's custom polynomial lookup table. File: `crc.py` |
| **GFDI Message Router** | Routes incoming GFDI messages by type — currently handles Protobuf Request (type 5043) by issuing a Protobuf ACK (type 5000). File: `gfdi.py` + `client.py::_handle_gfdi()` |
| **Protobuf Stubs** | Compiled `_pb2.py` files for all Gadgetbridge `.proto` schemas. Directory: `protobuf/` |
| **MTU Negotiation** | Reads the OS-negotiated MTU after connection; sets `max_write_size = mtu - 3` for optimal chunking. File: `client.py::connect()` |

### Known gaps in 0.1.0

- No keep-alive tick (the watch may drop connection during idle periods)
- No reconnection logic (if BLE drops, the client doesn't retry)
- No heartbeat/time-sync request (the watch may expect periodic time updates)

---

## Phase 2: Real-time Telemetry [v0.2.0] ✅

The client can receive and decode live sensor data pushed by the watch over
the dynamically assigned MLR handles.

### Done in 0.2.0

| Component | What it does |
|-----------|-------------|
| **Heart Rate** | Parses handle-assigned HR data: byte[2]=BPM, byte[3]=rest flag. Calls `callback("hr", bpm, rest)`. File: `client.py::_notify_handler()` |
| **Steps & Goal** | Parses `uint32` steps at byte[1:5] and `uint32` goal at byte[5:9]. Calls `callback("steps", count, goal)`. File: `client.py::_notify_handler()` |
| **HRV** | Parses `uint16` RR interval at byte[1:3]. Calls `callback("hrv", rr_ms)`. File: `client.py::_notify_handler()` |
| **SpO2** | Parses single-byte SpO2% at byte[1]; filters out 255 (invalid). Calls `callback("spo2", pct)`. File: `client.py::_notify_handler()` |
| **Respiration** | Parses signed-byte breaths/min at byte[1]; filters out ≤0. Calls `callback("respiration", breaths)`. File: `client.py::_notify_handler()` |
| **Callback Registry** | Single-callback-per-event model. `client.on("hr", fn)` registers; calling again replaces. File: `client.py::on()` |

### Not yet implemented (stretch for 0.2.x)

| Component | What it needs |
|-----------|--------------|
| **Calories** | Parse real-time caloric burn from service 8. Need to reverse-engineer the byte layout from Gadgetbridge. |
| **Intensity** | Parse activity intensity minutes from service 10. |
| **Stress** | Parse stress level 0-100 from service 13. Byte layout unknown. |
| **Accelerometer** | Parse raw accelerometer XYZ from service 16. High data rate — may need its own buffering/rate-limiting. |
| **Body Battery** | Parse body battery 1-100 from service 20. Layout likely similar to SpO2. |

---

## Phase 3: Device State & Protobuf Communication [v0.3.0]

Implement bidirectional state sync — the client responds to watch queries
and writes setting changes back through Protobuf messages on the GFDI handle.

### Features & implementation specs

#### 3.1 Device Status Responses
The watch sends a `deviceStatusService` Protobuf request (type 5043) asking
for battery level and firmware version. The client must recognise the
request and build a `gdi_smart_proto.Smart` response.

- **Detect request:** Match the parsed `Smart` message's `device_status.service`
  field from `gdi_device_status.proto`
- **Build response:** Populate `Smart.device_status.battery_level` (0-100 int)
  and `Smart.device_status.firmware_version` (string)
- **Send response:** Serialise to bytes, wrap in a GFDI data message (type 5120),
  encode with COBS, append CRC16, write to TX handle
- **Test:** Mock `_notify_handler` with a `deviceStatusService` payload;
  verify that a GFDI data message with the correct Protobuf is written back
- **Files to create/modify:** `garmin_ble/device_status.py`, extend `client.py`

#### 3.2 Settings Sync
The watch may request or write user settings (units, time format, activity
profiles) via the settings service Protobuf (`gdi_settings_service.proto`).

- **Read requests:** Handle `SettingsService` messages where `action = GET`
- **Write requests:** Handle `SettingsService` messages where `action = SET`;
  store changes in an in-memory settings dict
- **Persistence:** Save to a JSON file at `~/.garmin_ble/settings.json`
- **Supported keys to start:** `units` (metric/imperial), `time_format`
  (12h/24h), `activity_tracking` (on/off)
- **Test:** Mock a settings GET Protobuf; respond with the current value.
  Mock a settings SET; verify the in-memory dict updates
- **Files to create:** `garmin_ble/settings.py`

#### 3.3 App Config Requests
Watchfaces and data fields push their configuration screens through
`gdi_app_config_service.proto`. The client needs to acknowledge and
optionally serve config data.

- **Detect:** Match `AppConfigService` messages (request/response types)
- **Handle `CONFIG_REQUEST`:** Build an empty config response with the same
  `app_id` so the watch doesn't hang waiting
- **Advanced:** Store known app configs so the user can pre-configure
  watchfaces from the PC
- **Test:** Send a `CONFIG_REQUEST` Protobuf; verify an ACK-like response is
  written to the GFDI handle
- **Files to modify:** `client.py::_handle_gfdi()`

#### 3.4 Garmin JSON Encoder/Decoder
Some settings (alarms, contacts, calendar events,体育活动 settings) use a
proprietary binary JSON format rather than Protobuf.

- **Port from Gadgetbridge:** `legacy_java/service_garmin/http/GarminJson.java`
- **Encoding rules (to reverse-engineer):**
  - Strings are length-prefixed (1-2 byte length + UTF-8 data)
  - Numbers can be varint-encoded or fixed-width depending on context
  - Objects are wrapped in length-delimited groups
  - Key names are single-byte IDs, not full strings
- **API:** `GarminJson.encode({"alarms": [...]}) -> bytes` and
  `GarminJson.decode(bytes) -> dict`
- **Test:** Round-trip known alarm structures from Gadgetbridge test data
- **Files to create:** `garmin_ble/garmin_json.py`

#### 3.5 Weather Sync
Push local weather conditions to the watch for display on watchfaces.

- **Data source:** Fetch from OpenWeatherMap API (user-provided API key) or
  accept a dict from the calling application
- **Protobuf:** Build a `Smart.weather` message with:
  - Condition (sunny/cloudy/rain/etc mapped to Garmin's enum)
  - Temperature (current, high, low)
  - Humidity, UV index, wind speed
- **Trigger:** Send asynchronously via the GFDI handle (not in response to a
  watch request — the watch doesn't ask for weather, it's pushed)
- **Test:** Build a `Smart` weather message, serialise, wrap in GFDI data
  frame, verify the bytes are written to TX
- **Files to create:** `garmin_ble/weather.py`

#### 3.6 Find My Phone / Find My Watch
Respond to the "find my watch" command with a visual/audio alert on the PC.

- **Detect:** Watch sends a `FindMyWatch` Protobuf request
- **Respond with alert:** Print to console + optional desktop notification
  (`plyer` or similar)
- **Find My Phone:** Build a `FindMyWatch` Protobuf with `action = RING`
  and write it to the GFDI handle — the watch will vibrate
- **Test:** Mock the incoming request; verify the callback fires. Mock the
  outgoing request; verify the correct Protobuf bytes are written
- **Files to create:** `garmin_ble/find_device.py`

---

## Phase 4: Notifications & Media Control [v0.4.0]

Push phone notifications to the watch and handle media control commands
from the watch.

### Features & implementation specs

#### 4.1 Notification Push (Calls, SMS, Apps)
Use the ANCS (Apple Notification Center Service) pattern — or for non-Apple
devices, Garmin's custom notification protobuf — to display incoming
notifications on the watch.

- **Protobuf:** `gdi_notifications_service.proto` and optionally
  `gdi_sms_notification.proto`
- **Fields to populate:**
  - App name / sender (e.g. "Messages", "John Doe")
  - Title (e.g. "Hey, are you coming?")
  - Body (full message text)
  - Icon ID (map common apps to Garmin icon enums)
- **API:** `client.push_notification(app_name, sender, title, body)`
  → builds Protobuf → COBS-encodes → writes to GFDI handle
- **Call handling:** Separate method for incoming calls
  (`client.incoming_call(contact_name, phone_number)`) with accept/reject
  response handling
- **Test:** Build a notification protobuf, encode, verify bytes on the wire.
  Mock the watch's notification ACK and verify the callback fires
- **Files to create:** `garmin_ble/notifications.py`

#### 4.2 Call Control
Handle incoming call events and relay user actions back to the phone.

- **State machine:** `IDLE → RINGING → ANSWERED | REJECTED | MISSED`
- **Watch commands:** The watch can send play/pause/answer/reject commands
  via the notification service protobuf
- **OS Integration (optional):**
  - macOS: Use `scripting bridge` or `pyobjc` to simulate call app events
  - Linux: Use `modemmanager` D-Bus API
  - Windows: Use `pywin32` with Telephony API
- **Minimal mode:** Print "CALL FROM X — press Enter to accept" and relay
  the user's terminal input back as the response
- **Test:** Mock a call control protobuf from the watch; verify the callback
  fires with the correct action
- **Files to modify:** `garmin_ble/notifications.py`

#### 4.3 Music Control
Display now-playing track info on the watch and relay play/pause/skip/volume
commands to the PC's media player.

- **Protobuf:** Music control messages use `gdi_notifications_service.proto`
  with `music_control` fields
- **Push state:** `client.update_music_state(track, artist, album, state)`
  sends now-playing info to the watch
- **Receive commands:** The watch sends play/pause/next/prev/volume_up/
  volume_down actions
- **OS Integration:**
  - macOS: `osascript -e 'tell app "Spotify" to playpause'`
  - Linux: `playerctl` CLI tool
  - Windows: `pycaw` for volume, `SendKeys` for media keys
- **Test:** Mock a music control protobuf; verify the correct OS command
  would be executed (assert on a mocked subprocess call)
- **Files to create:** `garmin_ble/media_player.py`

---

## Phase 5: File Transfers — .FIT & .GPX [v0.5.0 → v1.0.0]

The "holy grail" — download activity files from the watch and upload
routes/watchfaces to it. This is the most protocol-complex phase.

### Features & implementation specs

#### 5.1 File Transfer Handshake
Register for `FILE_TRANSFER` services (0x4018, 0x401A, etc.) and negotiate
a file transfer session.

- **Service codes to register:** `FILE_TRANSFER_4 = 0x4018`,
  `FILE_TRANSFER_6 = 0x401A`, etc. — each represents a different transfer
  direction or file type
- **Protobuf:** `gdi_data_transfer_service.proto` and
  `gdi_file_sync_service.proto`
- **Handshake flow:**
  1. Client sends `DownloadRequest` Protobuf with file type (e.g. activities,
     sleep, settings backup)
  2. Watch responds with `TransferInit` — total file size, number of chunks,
  3. Client ACKs the init
- **Test:** Mock the watch's `TransferInit` response; verify the client
  transitions to "receiving" state
- **Files to create:** `garmin_ble/file_transfer.py`, `garmin_ble/transfer_state.py`

#### 5.2 Binary Chunk Reassembly
Files arrive in chunks. Each chunk must be ACKed individually or the watch
stops sending.

- **Chunk format:** Each GFDI data message contains a chunk header with
  sequence number, offset, total size, and chunk payload bytes
- **ACK protocol:** After every N chunks (or every chunk, depending on
  watch firmware), send a `ChunkAck` Protobuf with the received byte count
- **Reassembly buffer:** Accumulate chunks in a `bytearray` keyed by
  transfer session ID
- **Completion:** When received bytes == total file size, run CRC over the
  full assembled file and compare with the watch's CRC
- **Timeouts:** If no chunk arrives for 5 seconds, send a `TransferStatus`
  request to check if the watch is still sending
- **Test:** Feed a sequence of chunk GFDI messages; assert the reassembled
  bytes match the original file. Test that every chunk triggers a `write`
  to the TX handle (the ACK)
- **Files to modify:** `garmin_ble/file_transfer.py`

#### 5.3 FIT File Parser
Parse the downloaded `.FIT` files into structured Python objects
(activities, sleep, stress, daily summaries).

- **Library:** Use `fitparse` as a base, but extend it with Garmin-specific
  message types not in the standard FIT SDK:
  - Sport/session messages
  - Lap records with HR zones
  - Sleep stages (light/deep/rem/awake)
  - Stress score over time
  - Body Battery over time
- **Parser API:** `FitFile(path).parse() → Activity | SleepSession | ...`
  returning typed dataclass objects
- **Exporters:**
  - `to_csv()` — flat table of all records
  - `to_json()` — structured dict with nested lap/session/record lists
  - `to_gpx()` — extract GPS tracks into GPX format
- **Test:** Download known `.FIT` files from Gadgetbridge test fixtures and
  verify parsed output matches expected values
- **Files to create:** `garmin_ble/fit_parser.py`, `garmin_ble/fit_types.py`,
  `garmin_ble/exporters.py`

#### 5.4 Route/Activity Upload (GPX → FIT)
Send a GPX route or `.FIT` activity file from the PC to the watch.

- **Upload flow:**
  1. Parse GPX → list of waypoints/tracks
  2. Convert to a FIT course/route message (reverse-engineer the byte layout)
  3. Open a file upload transfer session via the file transfer service
  4. Chunk the FIT data and send each chunk, waiting for ACKs
  5. Send a "transfer complete" message
- **Supported file types:**
  - `.GPX` → Course (navigation route)
  - `.FIT` → Workout (structured intervals)
  - `.JSON` → Training Plan (if the watch supports it)
- **Test:** Take a real GPX file, convert to FIT bytes, upload through a
  mocked transfer session, verify the right bytes are written
- **Files to modify:** `garmin_ble/file_transfer.py`, `garmin_ble/fit_parser.py`

#### 5.5 App/Watchface Installation (.PRG)
Upload Connect IQ apps and watchfaces (`.PRG` / `.IQ` bundles) to the watch.

- **Detection:** Parse the `.PRG` header to extract app ID, version, device
  target
- **Upload:** Use the same file transfer mechanism as route upload, but
  register for a different file type service code
- **Verification:** After upload, query the installed apps service
  (`gdi_installed_apps_service.proto`) to confirm the app is listed
- **Test:** Mock a .PRG file upload; verify the chunked transfer flow
  handles files of this size correctly
- **Files to modify:** `garmin_ble/file_transfer.py`

---

## Post-1.0: Protocol Expansion [v1.x]

This is a **protocol library**, not an application. It provides typed
Python APIs for every Garmin BLE message. External projects should handle
persistence, UI, and business logic.

### Phase 6: Advanced Protocol Support [v1.1.0]
- **Device profiles:** Support for Instinct, Forerunner, Venu, Vivo series
  quirks (each has slightly different service/code mappings)
- **ECG support:** Parse `gdi_ecg_service.proto` data (requires watch with
  ECG hardware)
- **HTTP proxy:** `gdi_http_service.proto` -- intercept watch HTTP requests
  and serve custom responses (useful for Garmin Connect API mocking)
- **Calendar sync:** Push calendar events from the PC to the watch using
  `gdi_calendar_service.proto` and `garmin_contacts.proto`
---

## Test coverage targets

| Phase | Target coverage | Key areas to test |
|-------|----------------|-------------------|
| 1 (0.1.0) | ≥90% | COBS round-trip, CRC known-values, handshake MLR flow, connect success/failure paths |
| 2 (0.2.0) | ≥85% | Each telemetry parser, callback invocation, invalid-data filtering |
| 3 (0.3.0) | ≥80% | Protobuf request/response matching, settings persistence, Garmin JSON round-trip |
| 4 (0.4.0) | ≥75% | Notification encoding, call state machine, music command routing |
| 5 (0.5.0) | ≥70% | Chunk reassembly (multi-chunk), FIT parsing against real files, transfer session state machine |
| 1.0.0 | ≥85% overall | Full integration test with mocked watch, all error paths |

