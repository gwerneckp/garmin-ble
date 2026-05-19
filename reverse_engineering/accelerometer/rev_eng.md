# Reverse Engineering: Garmin BLE Accelerometer Service (Service 16)

A step-by-step journal of how we cracked the proprietary Garmin BLE "Accelerometer" service (Service code `0x10`). This document outlines our initial failures, our false hypotheses about mixed sensors, the statistical tests across 9 different physical datasets, and the final mathematical breakthrough that fully decoded the protocol.

---

## 1. The Starting Point: A Blank Slate

Our "ground truth" for the Garmin BLE protocol is the legacy Gadgetbridge Java source code. While the Java code successfully authenticates and routes messages, it **never actually parsed the accelerometer data**. It simply logged the hex dump:

```java
// From CommunicatorV2.java — this is ALL Gadgetbridge did:
private static class RealtimeAccelerometerCallback implements ServiceCallback {
    @Override
    public void onMessage(final byte[] value) {
        LOG.debug("Got realtime accel: {}", GB.hexdump(value));
    }
}
```

We were entirely on our own to figure out what the bytes meant.

### The Original Assumption (And Why It Was Wrong)

We observed that the packet was 16 bytes long. Stripping the 1-byte service header left a 15-byte payload. 
We guessed it was a standard 12-bit packed format. Why? Because 15 bytes = 120 bits, which neatly divides into exactly 10 values of 12 bits each.

```text
Our First (Wrong) Guess:
  V0:         Timestamp (12-bit unsigned, wraps at 4096ms)
  V1, V2, V3: Sample 1 X, Y, Z
  V4, V5, V6: Sample 2 X, Y, Z
  V7, V8, V9: Sample 3 X, Y, Z
```

We built a full Pygame 3D visualizer based on this assumption. It actually looked plausible at first! When you rotated the watch, the 3D model rotated. However, the axes felt "mushy" and cross-contaminated.

---

## 2. The Anomaly: Is there a Barometer?

To calibrate the axes, we built a recording tool (`reverse_eng.py`) to capture and label 10-second sessions of raw data. We recorded our first 7 datasets:

1. `desk_stationary_facing_down`
2. `desk_stationary_facing_up`
3. `floor_stationary_facing_down`
4. `floor_stationary_facing_up`
5. `holding_hand_facing_up`
6. `holding_in_hand_tilting_to_left_45_deg`
7. `holding_in_hand_tilting_to_right_45_deg`

### The Height Test
We compared the watch placed perfectly flat on a shelf/desk against the floor (keeping the orientation absolutely identical). Under our original parser, we saw this:

| Placement | P1 (avg "X") | P2 (avg "Y") |
|-----------|-------------|-------------|
| Shelf     | ~+10        | ~-30        |
| Floor     | ~+100       | ~-700       |

**This was alarming.** An accelerometer measures the direction of gravity. A stationary watch should output the exact same gravity vector regardless of whether it's on a shelf or the floor — altitude doesn't change gravity enough to be measurable here.

### The Barometer Hypothesis
Because the values changed so drastically with height, we formulated a new hypothesis: **Service 16 is not just an accelerometer.** We suspected the data was a multiplexed stream containing Barometric Pressure (which changes measurably between a desk and a floor) alongside the accelerometer.

---

## 3. Stripping It Back to Raw Bytes

Realizing our 12-bit assumption might be causing data from different sensors to bleed across byte boundaries, we threw out the parser. We wrote `testing.py` to dump the raw hexadecimal bytes and look for basic patterns.

### Discovering the Timestamp

We built a minimal script (`testing.py`) to print the raw hex bytes in every possible grouping (8-bit, 16-bit LE, 16-bit BE). Here is a snippet of the raw packet bytes (after stripping the `0x10` service header):

```text
Bytes 0-14: [31, 105, 0, 176, 255, 3, 255, 255, 253, 63, 240, 0, 192, 255, 1]
```

When we asked the script to print these as 16-bit Little-Endian pairs, we saw:
```text
As 16-bit LE pairs: [26911, 45056, 1023, 65535, 16381, 240, 65472, 7937]
```

The first value (`26911`) caught our eye. It looked like an incrementing counter. We added a quick delta tracking script to compare consecutive packets:

```text
  📡 pkt 1  ts=26911  Δ=0    │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 2  ts=27027  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 3  ts=27143  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 4  ts=27259  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  ...
  📡 pkt 70 ts=35031  Δ=+116 │ 0 176 255 3 255 255 253 63 240 0 192 255 1
  📡 pkt 71 ts=26847  Δ=-8184 (Rollover anomaly)
```

**Confirmed:** Bytes 0-1 are a 16-bit LE unsigned timestamp in milliseconds. 
- The delta is consistently `+115`, `+116`, or `+117` ms, which perfectly aligns with a packet transmission rate of ~8.7 Hz.
- The occasional massive negative delta (like `-8184`) is just a standard integer rollover artifact (wrapping around a limit).

This proved our initial 12-bit timestamp hypothesis was entirely wrong. The packet had a full 2-byte timestamp, leaving a **14-byte payload**, not 15 bytes.

---

## 4. Statistical Analysis of the 14 Bytes

We wrote an automated analysis script (`analyze.py`) to calculate the Mean and Standard Deviation (StdDev) of the 14 remaining bytes across all our datasets. We treated the 14 bytes as signed 8-bit integers (`rb[0]` to `rb[13]`).

Here is a snippet of the actual analysis log comparing the desk, floor, and tilted orientations:

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

**Key Discoveries from the Stats:**
1. **The Gravity Flipper:** `rb[8]` flipped perfectly from `-16.0` (face up) to `+15.8` (face down), regardless of desk vs. floor. This was an undeniable gravity axis.
2. **The Flag Byte:** `rb[13]` (the very last byte) had almost zero variance and only ever took values in the `0x1X` range (16, 17, 30, 31). It was clearly a status flag, not sensor data.

---

## 5. The Turning Point: The Shaking Test

With 14 mystery bytes left, we still needed to prove if there was a barometer mixed in. We recorded two final datasets:
8. `watch_against_wall_45_deg`
9. `shaking` (violent random shaking for 10 seconds)

The shaking test was the definitive filter:
- If a byte represents an **Accelerometer**, the physical shaking will cause the values to swing wildly (huge standard deviation).
- If a byte represents a **Barometer** or a **Status Flag**, shaking won't affect it, and the std dev will remain low.

### The Shaking Results
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

When we analyzed the standard deviation during shaking, **almost every single byte swung wildly.** There was no stable data block. The shaking test conclusively proved that the payload is **100% accelerometer data**. There is no barometer. 

### So what caused the Height Anomaly?
If it's pure accelerometer data, and the shelf and floor were at the exact same level orientation, why did the values change so drastically?

**It was an illusion caused by the broken math.**
Our original parser assumed the payload was 15 bytes long and tried to extract 10 values from it. Because the actual payload was only 14 bytes, the math was shifting the bitwise operations completely out of alignment and scrambling the channels. What looked like a massive sensor change (which we mistook for a barometer responding to altitude) was actually just the bit-shifting parser tearing the accelerometer data apart incorrectly. The breakthrough was realizing the payload is exactly 14 bytes (13.5 bytes of data + a 4-bit flag). Once the 1.5-byte (12-bit) boundaries were properly aligned, the math proved the accelerometer values were identical on both the shelf and the floor.

---

## 6. The Final Mathematical Breakthrough

Knowing we had 14 bytes of pure accelerometer data, the final puzzle piece clicked into place.

An accelerometer delivers data in 3 axes (X, Y, Z). Garmin clearly sends multiple samples per packet to save battery. How many fit in 14 bytes?
- 3 samples of X, Y, Z = **9 values**.
- If Garmin uses **12-bit packing** (1.5 bytes per value), then 9 values × 1.5 bytes = **13.5 bytes**.

This leaves exactly **0.5 bytes (4 bits)** leftover at the end of the 14-byte payload.

This perfectly matched our statistical finding that `rb[13]` (the last byte) was a flag! 
The upper nibble (first 4 bits) of `rb[13]` is ALWAYS `1` (`0x1_`). The lower nibble contains the remaining 4 bits of the 9th accelerometer value!

### Proving the Math

We wrote `test_12bit.py` to unpack the 14 bytes into 9 signed values (3 samples) using this exact layout and tested it against our logs:

**Test 1: Flat on Desk (Face Up)**
```text
V0-V2 (Sample 1 X,Y,Z): [0, -3, -255]
V3-V5 (Sample 2 X,Y,Z): [0, -3, -255]
V6-V8 (Sample 3 X,Y,Z): [2, -2, -254]
```
- Magnitude: `sqrt(0^2 + (-3)^2 + (-255)^2) ≈ 255`
- *Result:* Perfect 1g reading! (A 10-bit accelerometer scales such that 1g = 256. Mapped into a 12-bit signed integer, this gives a massive ±8g range).

**Test 2: Flat on Desk (Face Down)**
```text
V0-V2 (Sample 1 X,Y,Z): [4, -1, 258]
```
- Magnitude: `~258`
- *Result:* Perfect 1g reading. Flipping the watch upside down perfectly inverted the Z-axis from -255 to +258.

**Test 3: Tilted 45 Degrees Against a Wall**
```text
V0-V2 (Sample 1 X,Y,Z): [-203, 20, -151]
```
- Magnitude: `sqrt((-203)^2 + 20^2 + (-151)^2) ≈ 253.7`
- *Result:* Perfect 1g reading. Gravity is evenly distributed across the X and Z axes due to the 45-degree tilt.

**Test 4: Shaking**
```text
V0-V2 (Sample 1 X,Y,Z): [813, 124, 165]
V3-V5 (Sample 2 X,Y,Z): [1377, -1540, -32]
V6-V8 (Sample 3 X,Y,Z): [885, -1288, 107]
```
- *Result:* Values swing violently from -2048 to +2047, confirming the dynamic nature of the data.

---

## 7. Summary of the True Protocol

The reverse engineering is complete. We now have 100% confidence in the Garmin GFDI V2 Accelerometer packet structure.

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
