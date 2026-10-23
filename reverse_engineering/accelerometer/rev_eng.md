# Reverse engineering the Garmin BLE accelerometer service (service 16)

A journal of how we decoded the proprietary Garmin BLE accelerometer service (service code `0x10`). It covers the parsers that failed, the wrong guess that a second sensor was mixed in, statistical tests across 9 physical datasets, and the bit layout that finally decoded the payload.

---

## 1. Starting point

Our reference for the Garmin BLE protocol is the legacy Gadgetbridge Java source. It authenticates and routes messages, but it never parsed the accelerometer data. It only logged a hex dump:

```java
// From CommunicatorV2.java. This is all Gadgetbridge did:
private static class RealtimeAccelerometerCallback implements ServiceCallback {
    @Override
    public void onMessage(final byte[] value) {
        LOG.debug("Got realtime accel: {}", GB.hexdump(value));
    }
}
```

So we had to work out what the bytes meant ourselves.

### The original assumption, and why it was wrong

The packet was 16 bytes long. Stripping the 1-byte service header left a 15-byte payload.
We guessed it was a standard 12-bit packed format, because 15 bytes = 120 bits, which divides into exactly 10 values of 12 bits each.

```text
Our First (Wrong) Guess:
  V0:         Timestamp (12-bit unsigned, wraps at 4096ms)
  V1, V2, V3: Sample 1 X, Y, Z
  V4, V5, V6: Sample 2 X, Y, Z
  V7, V8, V9: Sample 3 X, Y, Z
```

We built a Pygame 3D visualizer on this assumption. At first it looked plausible: rotating the watch rotated the 3D model. But the axes felt mushy and seemed to bleed into each other.

---

## 2. Is there a barometer?

To calibrate the axes, we wrote a recording tool (`reverse_eng.py`) that captures and labels 10-second sessions of raw data. We recorded the first 7 datasets:

1. `desk_stationary_facing_down`
2. `desk_stationary_facing_up`
3. `floor_stationary_facing_down`
4. `floor_stationary_facing_up`
5. `holding_hand_facing_up`
6. `holding_in_hand_tilting_to_left_45_deg`
7. `holding_in_hand_tilting_to_right_45_deg`

### Height test
We compared the watch lying flat on a shelf/desk with the same watch on the floor, in the same orientation. The original parser gave:

| Placement | P1 (avg "X") | P2 (avg "Y") |
|-----------|-------------|-------------|
| Shelf     | ~+10        | ~-30        |
| Floor     | ~+100       | ~-700       |

This should not happen. An accelerometer measures the direction of gravity, so a stationary watch should output the same gravity vector on a shelf or on the floor. The altitude difference doesn't change gravity enough to measure here.

### Barometer hypothesis
Because the values changed so much with height, we suspected service 16 carried more than accelerometer data: a multiplexed stream with barometric pressure (which does change measurably between a desk and the floor) alongside the accelerometer.

---

## 3. Back to raw bytes

The 12-bit assumption might have been smearing data from different sensors across byte boundaries, so we threw out the parser. We wrote `testing.py` to dump the raw bytes and look for basic patterns.

### Finding the timestamp

`testing.py` prints the raw bytes in every grouping (8-bit, 16-bit LE, 16-bit BE). Here are the raw packet bytes from one packet, after stripping the `0x10` service header:

```text
Bytes 0-14: [31, 105, 0, 176, 255, 3, 255, 255, 253, 63, 240, 0, 192, 255, 1]
```

As 16-bit little-endian pairs:
```text
As 16-bit LE pairs: [26911, 45056, 1023, 65535, 16381, 240, 65472, 7937]
```

The first value (`26911`) looked like an incrementing counter. We added delta tracking to compare consecutive packets:

```text
  📡 pkt 1  ts=26911  Δ=0    │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 2  ts=27027  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 3  ts=27143  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 4  ts=27259  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  ...
  📡 pkt 70 ts=35031  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 71 ts=26847  Δ=-8184 (Rollover anomaly)
```

Confirmed: bytes 0-1 are a 16-bit LE unsigned timestamp in milliseconds.
- The delta is always `+115`, `+116`, or `+117` ms, which matches a packet rate of ~8.7 Hz.
- The occasional large negative delta (like `-8184`) is an integer rollover (the counter wrapping around a limit).

So the 12-bit timestamp guess was wrong. The packet has a full 2-byte timestamp, which leaves a 14-byte payload, not 15.

---

## 4. Statistics on the 14 bytes

`analyze.py` calculates the mean and standard deviation (StdDev) of each of the 14 remaining bytes across all datasets, treating them as signed 8-bit integers (`rb[0]` to `rb[13]`).

An excerpt of the analysis log for the desk, floor and tilted recordings:

```text
=============================================================================
  INDIVIDUAL BYTE ANALYSIS (SIGNED 8-BIT)
=============================================================================
  Byte |   desk_up | desk_down |  floor_up | floor_down | tilt_left | tilt_right
-----------------------------------------------------------------------------
rb[ 0] | -0.6± 0.9 |  3.3± 1.0 | -0.8± 0.9 |  1.6± 1.1  | 84.0±41.3 | -76.4± 6.5
rb[ 1] |-34.4±13.7 |-29.8±14.5 |-26.1±15.5 |-11.1±16.8  | 12.0±77.5 |  -2.4±80.1
rb[ 2] | -1.0± 0.1 | -0.9± 0.3 | -1.0± 0.0 | -0.5± 0.5  | -1.7± 0.8 |  -3.2± 0.5
rb[ 3] |  1.1± 0.9 |  0.4± 0.9 |  1.3± 0.9 |  0.3± 0.9  | 63.4±13.0 |  76.1± 6.3
rb[ 4] |  7.1±13.3 | 54.6±16.1 |  1.0±15.4 | 21.7±16.7  | 20.2±75.5 |   7.2±78.4
rb[ 5] | -0.5± 0.5 |  0.0± 0.0 | -0.6± 0.5 | -0.0± 0.2  |-10.7± 0.9 |  10.7± 0.5
...
rb[ 8] |-16.0± 0.1 | 15.8± 0.4 |-16.0± 0.0 | 15.9± 0.3  |-12.7± 1.0 | -11.7± 0.5
...
rb[13] | 31.0± 0.1 | 16.8± 0.4 | 31.0± 0.0 | 16.9± 0.3  | 31.0± 0.0 |  31.0± 0.0
```

What the stats showed:
1. `rb[8]` flipped from `-16.0` (face up) to `+15.8` (face down), on both the desk and the floor. That is a gravity axis.
2. `rb[13]` (the last byte) had almost zero variance and only took values in the `0x1X` range (16, 17, 30, 31). It looked like a status flag rather than sensor data.

---

## 5. Shaking test

We still needed to find out whether a barometer was mixed into the 14 bytes. We recorded two more datasets:
8. `watch_against_wall_45_deg`
9. `shaking` (violent random shaking for 10 seconds)

Shaking separates the two cases:
- If a byte is accelerometer data, shaking makes it swing widely (large standard deviation).
- If a byte is a barometer reading or a status flag, shaking doesn't affect it and the standard deviation stays low.

### Shaking results
```text
  Byte |    shaking (StdDev)
-----------------------------
rb[ 0] |   -8.4 ± 75.0
rb[ 1] |  -13.1 ± 71.3
rb[ 2] |    1.0 ± 56.4
rb[ 3] |  -11.3 ± 73.2
rb[ 4] |   -0.5 ± 79.1
rb[ 5] |   12.0 ± 26.3 
rb[ 6] |   10.7 ± 74.7
rb[ 7] |   -5.2 ± 73.3
rb[ 8] |    3.5 ± 25.2 
rb[ 9] |    5.2 ± 79.4
rb[10] |    0.1 ± 74.0
rb[11] |   -5.5 ± 73.7
rb[12] |    5.6 ± 71.3
rb[13] |   22.3 ±  6.4  <-- Low variance (Flag byte)
```

During shaking, almost every byte swung widely. There was no stable block of data, so the payload is all accelerometer data. There is no barometer.

### What caused the height difference?
If it is all accelerometer data, and the watch had the same orientation on the shelf and the floor, why did the values change so much?

The parser was wrong.
It assumed a 15-byte payload and extracted 10 values from it. The real payload was 14 bytes, so the bit shifts were out of alignment and the channels were scrambled. The large change we had taken for a barometer responding to altitude was the parser misreading the accelerometer data. The payload is exactly 14 bytes (13.5 bytes of data plus a 4-bit flag). With the 1.5-byte (12-bit) boundaries aligned correctly, the accelerometer values on the shelf and the floor were identical.

---

## 6. The bit layout

With 14 bytes of accelerometer data, the layout follows.

An accelerometer reports 3 axes (X, Y, Z). Garmin presumably sends several samples per packet to save battery. How many fit in 14 bytes?
- 3 samples of X, Y, Z = 9 values.
- With 12-bit packing (1.5 bytes per value), 9 values × 1.5 bytes = 13.5 bytes.

That leaves 0.5 bytes (4 bits) at the end of the 14-byte payload.

This matches the finding that `rb[13]` (the last byte) behaves like a flag.
The upper nibble (first 4 bits) of `rb[13]` is always `1` (`0x1_`). The lower nibble holds the remaining 4 bits of the 9th accelerometer value.

### Checking the layout

`test_12bit.py` unpacks the 14 bytes into 9 signed values (3 samples) with this layout. We ran it against the recordings:

Test 1: flat on desk, face up
```text
V0-V2 (Sample 1 X,Y,Z): [0, -3, -255]
V3-V5 (Sample 2 X,Y,Z): [0, -3, -255]
V6-V8 (Sample 3 X,Y,Z): [2, -2, -254]
```
- Magnitude: `sqrt(0^2 + (-3)^2 + (-255)^2) ≈ 255`
- Result: 1g. (A 10-bit accelerometer scales so that 1g = 256. In a 12-bit signed integer that gives a ±8g range.)

Test 2: flat on desk, face down
```text
V0-V2 (Sample 1 X,Y,Z): [4, -1, 258]
```
- Magnitude: `~258`
- Result: 1g. Turning the watch upside down inverted the Z axis from -255 to +258.

Test 3: tilted 45 degrees against a wall
```text
V0-V2 (Sample 1 X,Y,Z): [-203, 20, -151]
```
- Magnitude: `sqrt((-203)^2 + 20^2 + (-151)^2) ≈ 253.7`
- Result: 1g. The 45-degree tilt splits gravity across the X and Z axes.

Test 4: shaking
```text
V0-V2 (Sample 1 X,Y,Z): [813, 124, 165]
V3-V5 (Sample 2 X,Y,Z): [1377, -1540, -32]
V6-V8 (Sample 3 X,Y,Z): [885, -1288, 107]
```
- Result: values swing across the full -2048 to +2047 range, as expected for motion.

---

## 7. Protocol summary

The Garmin GFDI V2 accelerometer packet structure:

```text
Packet size: 17 bytes total

Byte 0:    Service Header (0x10 for REALTIME_ACCELEROMETER)
Byte 1-2:  16-bit LE Unsigned Timestamp (ms)
Byte 3-16: 14 bytes of payload containing:
           - 9 × 12-bit signed values (3 samples of X, Y, Z)
           - 1 × 4-bit status flag (upper nibble of byte 16)

Scale: 256 LSB = 1g (±8g total range)
Rate:  Packets arrive every ~115ms (8.7 Hz). 
       With 3 samples per packet, the true sampling rate is ~26 Hz.
```
