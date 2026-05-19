#!/usr/bin/env python3
"""
Garmin BLE Sensor Data Reverse-Engineering Analysis

Analyzes 7 JSON recordings taken under different conditions to identify
which bytes correspond to accelerometer, barometer, and status fields.

Packet structure (16 bytes total):
  - Bytes 0-1: 16-bit LE timestamp (confirmed, milliseconds)
  - Bytes 2-15: 14 unknown bytes (rest_bytes[0..13])
"""

import json
import os
import struct
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent

FILES = {
    "desk_up":    "desk_stationary_facing_up.json",
    "desk_down":  "desk_stationary_facing_down.json",
    "floor_up":   "floor_stationary_facing_up.json",
    "floor_down": "floor_stationary_facing_down.json",
    "hand_up":    "holding_hand_facing_up.json",
    "tilt_left":  "holding_in_hand_tilting_to_left_45_deg.json",
    "tilt_right": "holding_in_hand_tilting_to_right_45_deg.json",
    "wall_45":    "watch_against_wall_45_deg_right_desk.json",
    "shaking":    "shaking.json",
}

def load_data():
    """Load all JSON files and extract rest_bytes and raw_bytes arrays."""
    data = {}
    for key, fname in FILES.items():
        fpath = DATA_DIR / fname
        with open(fpath) as f:
            j = json.load(f)
        packets = j["packets"]
        rest = np.array([p["rest_bytes"] for p in packets], dtype=np.uint8)
        raw = np.array([p["raw_bytes"] for p in packets], dtype=np.uint8)
        timestamps = np.array([p["timestamp"] for p in packets])
        data[key] = {
            "rest": rest,
            "raw": raw,
            "timestamps": timestamps,
            "count": len(packets),
            "fname": fname,
        }
    return data

def print_separator(title=""):
    print("\n" + "=" * 100)
    if title:
        print(f"  {title}")
        print("=" * 100)

def analyze_individual_bytes(data):
    """Analyze each of the 14 rest_bytes as unsigned 8-bit values."""
    print_separator("1. INDIVIDUAL BYTE ANALYSIS (UNSIGNED 8-BIT)")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]
    print(f"\nrest_bytes has {n_bytes} elements (packet bytes 2-{1+n_bytes})")

    # Table header
    header = f"{'Byte':>6} |"
    for key in FILES:
        header += f" {key:>14} |"
    print(f"\n{header}")
    print("-" * len(header))

    means = {}
    stds = {}
    for key in FILES:
        means[key] = data[key]["rest"].astype(float).mean(axis=0)
        stds[key] = data[key]["rest"].astype(float).std(axis=0)

    for i in range(n_bytes):
        row = f"rb[{i:2d}] |"
        for key in FILES:
            row += f" {means[key][i]:6.1f}±{stds[key][i]:5.1f} |"
        print(row)

    # Also print as signed int8
    print_separator("1b. INDIVIDUAL BYTE ANALYSIS (SIGNED 8-BIT)")
    smeans = {}
    sstds = {}
    for key in FILES:
        signed = data[key]["rest"].view(np.int8).astype(float)
        smeans[key] = signed.mean(axis=0)
        sstds[key] = signed.std(axis=0)

    header = f"{'Byte':>6} |"
    for key in FILES:
        header += f" {key:>14} |"
    print(f"\n{header}")
    print("-" * len(header))

    for i in range(n_bytes):
        row = f"rb[{i:2d}] |"
        for key in FILES:
            row += f" {smeans[key][i]:6.1f}±{sstds[key][i]:5.1f} |"
        print(row)

    return means, stds


def analyze_16bit_pairs(data):
    """Analyze rest_bytes as 16-bit little-endian pairs."""
    print_separator("2. 16-BIT LITTLE-ENDIAN PAIR ANALYSIS (UNSIGNED)")

    # Pairs: (rb[0],rb[1]), (rb[2],rb[3]), ..., (rb[12],rb[13])
    # = packet bytes (2,3), (4,5), (6,7), (8,9), (10,11), (12,13), (14,15)
    pair_names = []
    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]
    n_pairs = n_bytes // 2
    leftover = n_bytes % 2

    for i in range(n_pairs):
        b1, b2 = i * 2, i * 2 + 1
        pair_names.append(f"rb[{b1},{b2}]")

    print(f"\n{n_pairs} pairs" + (f" + 1 leftover byte (rb[{n_bytes-1}])" if leftover else ""))

    # Unsigned 16-bit LE
    u16_data = {}
    for key in FILES:
        rest = data[key]["rest"]
        pairs = []
        for i in range(n_pairs):
            lo = rest[:, i * 2].astype(np.uint16)
            hi = rest[:, i * 2 + 1].astype(np.uint16)
            val = lo | (hi << 8)
            pairs.append(val)
        u16_data[key] = np.column_stack(pairs)

    header = f"{'Pair':>12} |"
    for key in FILES:
        header += f" {key:>16} |"
    print(f"\n{header}")
    print("-" * len(header))

    u16_means = {}
    u16_stds = {}
    for key in FILES:
        u16_means[key] = u16_data[key].astype(float).mean(axis=0)
        u16_stds[key] = u16_data[key].astype(float).std(axis=0)

    for i in range(n_pairs):
        row = f"{pair_names[i]:>12} |"
        for key in FILES:
            row += f" {u16_means[key][i]:8.1f}±{u16_stds[key][i]:6.1f} |"
        print(row)

    # Signed 16-bit LE
    print_separator("2b. 16-BIT LITTLE-ENDIAN PAIR ANALYSIS (SIGNED)")

    s16_data = {}
    for key in FILES:
        rest = data[key]["rest"]
        pairs = []
        for i in range(n_pairs):
            lo = rest[:, i * 2].astype(np.int32)
            hi = rest[:, i * 2 + 1].astype(np.int32)
            val = lo | (hi << 8)
            # Convert to signed 16-bit
            val = np.where(val >= 32768, val - 65536, val)
            pairs.append(val)
        s16_data[key] = np.column_stack(pairs)

    header = f"{'Pair':>12} |"
    for key in FILES:
        header += f" {key:>16} |"
    print(f"\n{header}")
    print("-" * len(header))

    s16_means = {}
    s16_stds = {}
    for key in FILES:
        s16_means[key] = s16_data[key].astype(float).mean(axis=0)
        s16_stds[key] = s16_data[key].astype(float).std(axis=0)

    for i in range(n_pairs):
        row = f"{pair_names[i]:>12} |"
        for key in FILES:
            row += f" {s16_means[key][i]:8.1f}±{s16_stds[key][i]:6.1f} |"
        print(row)

    return u16_means, u16_stds, s16_means, s16_stds, u16_data, s16_data


def analyze_12bit_values(data):
    """Try interpreting as 12-bit packed values (common in MEMS sensors)."""
    print_separator("3. 12-BIT PACKED VALUE ANALYSIS")
    print("\nSome MEMS sensors pack 3-axis data as 12-bit values (1.5 bytes each).")
    print("Trying: 3 values from 4.5 bytes, various offsets...\n")

    for offset in [0, 1, 2, 4, 6]:
        if offset + 6 > 14:
            continue
        print(f"  Offset rb[{offset}]: ", end="")
        for key in ["desk_up", "desk_down", "floor_up", "tilt_left"]:
            rest = data[key]["rest"]
            # Take first packet as example
            b = rest[0]
            if offset + 5 < len(b):
                # Try extracting 3x 12-bit values from 4.5 bytes
                raw_val = int.from_bytes(b[offset:offset+6], 'little')
                v1 = (raw_val >> 0) & 0xFFF
                v2 = (raw_val >> 12) & 0xFFF
                v3 = (raw_val >> 24) & 0xFFF
                # Convert to signed
                v1s = v1 - 4096 if v1 >= 2048 else v1
                v2s = v2 - 4096 if v2 >= 2048 else v2
                v3s = v3 - 4096 if v3 >= 2048 else v3
                print(f"  {key}: ({v1s:5d}, {v2s:5d}, {v3s:5d})", end="")
        print()


def analyze_nibbles(data):
    """Analyze each byte as two 4-bit nibbles."""
    print_separator("4. NIBBLE (4-BIT) ANALYSIS")
    print("\nSplitting each byte into high and low nibbles.\n")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]

    header = f"{'Nibble':>10} |"
    for key in ["desk_up", "desk_down", "floor_up", "floor_down", "tilt_left"]:
        header += f" {key:>12} |"
    print(header)
    print("-" * len(header))

    for i in range(n_bytes):
        for nibble_name, shift, mask in [("hi", 4, 0xF0), ("lo", 0, 0x0F)]:
            row = f"rb[{i:2d}].{nibble_name} |"
            for key in ["desk_up", "desk_down", "floor_up", "floor_down", "tilt_left"]:
                vals = (data[key]["rest"][:, i].astype(int) >> shift) & 0x0F
                mean_v = vals.mean()
                std_v = vals.std()
                row += f" {mean_v:5.2f}±{std_v:4.2f} |"
            print(row)


def height_orientation_analysis(data, s16_means, s16_stds):
    """Identify bytes that change with height vs orientation."""
    print_separator("5. HEIGHT vs ORIENTATION SENSITIVITY ANALYSIS")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]

    # For individual bytes (unsigned)
    print("\n--- Individual Bytes (unsigned) ---")
    byte_means = {}
    for key in FILES:
        byte_means[key] = data[key]["rest"].astype(float).mean(axis=0)

    print(f"\n{'Byte':>6} | {'Height Δ (desk-floor, same orient)':>40} | {'Orient Δ (up-down, same height)':>35} | {'Classification':>15}")
    print("-" * 110)

    for i in range(n_bytes):
        # Height change: compare desk vs floor with same orientation
        h_delta_up = abs(byte_means["desk_up"][i] - byte_means["floor_up"][i])
        h_delta_down = abs(byte_means["desk_down"][i] - byte_means["floor_down"][i])
        h_avg = (h_delta_up + h_delta_down) / 2

        # Orientation change: compare up vs down at same height
        o_delta_desk = abs(byte_means["desk_up"][i] - byte_means["desk_down"][i])
        o_delta_floor = abs(byte_means["floor_up"][i] - byte_means["floor_down"][i])
        o_avg = (o_delta_desk + o_delta_floor) / 2

        if h_avg > 5 and o_avg < 5:
            cls = "BAROMETER?"
        elif o_avg > 5 and h_avg < 5:
            cls = "ACCEL?"
        elif h_avg > 5 and o_avg > 5:
            cls = "BOTH?"
        else:
            cls = "CONSTANT"

        print(f"rb[{i:2d}] | up: {h_delta_up:6.1f}, down: {h_delta_down:6.1f}, avg: {h_avg:6.1f} | desk: {o_delta_desk:6.1f}, floor: {o_delta_floor:6.1f}, avg: {o_avg:6.1f} | {cls:>15}")

    # For 16-bit signed pairs
    print("\n--- 16-bit Signed Pairs ---")
    n_pairs = len(s16_means[list(s16_means.keys())[0]])

    print(f"\n{'Pair':>12} | {'Height Δ':>30} | {'Orient Δ':>30} | {'Tilt Δ (L vs R)':>20} | {'Class':>12}")
    print("-" * 120)

    for i in range(n_pairs):
        h_delta_up = abs(s16_means["desk_up"][i] - s16_means["floor_up"][i])
        h_delta_down = abs(s16_means["desk_down"][i] - s16_means["floor_down"][i])
        h_avg = (h_delta_up + h_delta_down) / 2

        o_delta_desk = abs(s16_means["desk_up"][i] - s16_means["desk_down"][i])
        o_delta_floor = abs(s16_means["floor_up"][i] - s16_means["floor_down"][i])
        o_avg = (o_delta_desk + o_delta_floor) / 2

        tilt_delta = abs(s16_means["tilt_left"][i] - s16_means["tilt_right"][i])

        if h_avg > 50 and o_avg < 50:
            cls = "BAROMETER?"
        elif o_avg > 50:
            cls = "ACCEL?"
        elif tilt_delta > 50:
            cls = "ACCEL?"
        else:
            cls = "CONSTANT"

        print(f"rb[{i*2},{i*2+1}] | up:{h_delta_up:7.1f} dn:{h_delta_down:7.1f} avg:{h_avg:7.1f} | dk:{o_delta_desk:7.1f} fl:{o_delta_floor:7.1f} avg:{o_avg:7.1f} | {tilt_delta:8.1f}           | {cls:>12}")


def check_accel_magnitude(s16_data):
    """Check if any 3 consecutive 16-bit pairs give ~1g magnitude."""
    print_separator("6. ACCELEROMETER 1g MAGNITUDE CHECK (signed 16-bit pairs)")
    print("\nChecking if any 3 consecutive pairs give magnitude ≈ 1024 (±200)...")
    print("Also checking for magnitude ≈ 4096, ≈ 8192, ≈ 16384 (other common scales)\n")

    n_pairs = s16_data[list(s16_data.keys())[0]].shape[1]

    for start_pair in range(n_pairs - 2):
        print(f"\n  Triplet: pairs [{start_pair}, {start_pair+1}, {start_pair+2}] = rb[{start_pair*2}:{start_pair*2+5+1}]")
        for key in FILES:
            x = s16_data[key][:, start_pair].astype(float)
            y = s16_data[key][:, start_pair + 1].astype(float)
            z = s16_data[key][:, start_pair + 2].astype(float)
            mag = np.sqrt(x**2 + y**2 + z**2)
            print(f"    {key:>12}: X={x.mean():8.1f}±{x.std():6.1f}  Y={y.mean():8.1f}±{y.std():6.1f}  Z={z.mean():8.1f}±{z.std():6.1f}  |mag|={mag.mean():8.1f}±{mag.std():6.1f}")


def analyze_alternate_16bit_offsets(data):
    """Try 16-bit pairs at odd byte offsets (e.g., rb[1,2], rb[3,4], ...)."""
    print_separator("7. 16-BIT SIGNED PAIRS AT ODD OFFSETS (offset by 1 byte)")
    print("\nTrying pairs starting at rb[1]: (rb[1],rb[2]), (rb[3],rb[4]), (rb[5],rb[6]), ...\n")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]

    # Pairs at offset 1
    pairs_odd = []
    for i in range(1, n_bytes - 1, 2):
        pairs_odd.append((i, i + 1))

    header = f"{'Pair':>12} |"
    for key in FILES:
        header += f" {key:>16} |"
    print(header)
    print("-" * len(header))

    odd_s16_data = {}
    for key in FILES:
        rest = data[key]["rest"]
        cols = []
        for (a, b) in pairs_odd:
            lo = rest[:, a].astype(np.int32)
            hi = rest[:, b].astype(np.int32)
            val = lo | (hi << 8)
            val = np.where(val >= 32768, val - 65536, val)
            cols.append(val)
        odd_s16_data[key] = np.column_stack(cols)

    for idx, (a, b) in enumerate(pairs_odd):
        row = f"rb[{a},{b}] |"
        for key in FILES:
            m = odd_s16_data[key][:, idx].astype(float).mean()
            s = odd_s16_data[key][:, idx].astype(float).std()
            row += f" {m:8.1f}±{s:6.1f} |"
        print(row)

    # Check magnitudes for odd-offset triplets
    print("\n  Magnitude check for odd-offset triplets:")
    n_odd_pairs = len(pairs_odd)
    for start in range(n_odd_pairs - 2):
        print(f"\n  Triplet: rb[{pairs_odd[start]}, {pairs_odd[start+1]}, {pairs_odd[start+2]}]")
        for key in ["desk_up", "desk_down", "floor_up", "floor_down", "hand_up", "tilt_left", "tilt_right"]:
            x = odd_s16_data[key][:, start].astype(float)
            y = odd_s16_data[key][:, start + 1].astype(float)
            z = odd_s16_data[key][:, start + 2].astype(float)
            mag = np.sqrt(x**2 + y**2 + z**2)
            print(f"    {key:>12}: X={x.mean():8.1f}  Y={y.mean():8.1f}  Z={z.mean():8.1f}  |mag|={mag.mean():8.1f}")


def analyze_raw_hex_dump(data):
    """Print first few packets from each file in hex for visual inspection."""
    print_separator("8. RAW HEX DUMP (first 5 packets per file)")

    for key in FILES:
        print(f"\n  {key} ({FILES[key]}):")
        raw = data[key]["raw"]
        for i in range(min(5, len(raw))):
            hex_str = " ".join(f"{b:02x}" for b in raw[i])
            # Also show with grouping
            ts = raw[i][0] | (raw[i][1] << 8)
            grouped = f"[{raw[i][0]:02x} {raw[i][1]:02x}]"
            for j in range(2, len(raw[i]), 2):
                if j + 1 < len(raw[i]):
                    grouped += f" [{raw[i][j]:02x} {raw[i][j+1]:02x}]"
                else:
                    grouped += f" [{raw[i][j]:02x}]"
            print(f"    pkt {i}: {hex_str}")
            print(f"           {grouped}  (ts={ts})")


def analyze_bit_patterns(data):
    """Look at bit-level patterns, especially for bytes that might be flags."""
    print_separator("9. BIT PATTERN ANALYSIS FOR POTENTIALLY CONSTANT BYTES")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]

    for i in range(n_bytes):
        unique_vals = set()
        for key in FILES:
            unique_vals.update(data[key]["rest"][:, i].tolist())
        if len(unique_vals) <= 16:
            print(f"\n  rb[{i:2d}] (pkt byte {i+2:2d}): {len(unique_vals)} unique values")
            sorted_vals = sorted(unique_vals)
            for v in sorted_vals:
                print(f"    {v:3d} = 0x{v:02x} = {v:08b}")


def analyze_potential_scaling(s16_data, s16_means):
    """Check various scaling factors to see if any give ~1g or ~9.81 m/s²."""
    print_separator("10. ACCELEROMETER SCALE FACTOR ANALYSIS")

    common_scales = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]
    n_pairs = s16_data[list(s16_data.keys())[0]].shape[1]

    print("\nFor each triplet of signed 16-bit pairs, checking the mean magnitude")
    print("against common LSB/g scales. Looking for 1g ≈ known scale factor.\n")

    for start in range(n_pairs - 2):
        for key in ["desk_up", "floor_up"]:
            x_mean = s16_means[key][start]
            y_mean = s16_means[key][start + 1]
            z_mean = s16_means[key][start + 2]
            mag = np.sqrt(x_mean**2 + y_mean**2 + z_mean**2)
            if mag > 10:  # Skip near-zero magnitudes
                for scale in common_scales:
                    g_val = mag / scale
                    if 0.8 < g_val < 1.2:
                        print(f"  *** MATCH: pairs [{start},{start+1},{start+2}] {key}: mag={mag:.1f}, scale={scale} → {g_val:.3f}g")


def analyze_high_low_bytes(data):
    """Check if the 14 bytes might encode values in non-standard widths."""
    print_separator("11. BYTE VALUE DISTRIBUTION SUMMARY")

    n_bytes = data[list(data.keys())[0]]["rest"].shape[1]

    print(f"\n{'Byte':>6} | {'Min':>4} | {'Max':>4} | {'Range':>5} | {'Unique#':>7} | {'Mode':>4} | Notes")
    print("-" * 80)

    for i in range(n_bytes):
        all_vals = np.concatenate([data[key]["rest"][:, i] for key in FILES])
        min_v = all_vals.min()
        max_v = all_vals.max()
        rng = max_v - min_v
        unique = len(np.unique(all_vals))
        mode_val = np.bincount(all_vals).argmax()

        notes = ""
        if unique <= 4:
            notes = f"LIKELY FLAG/STATUS (vals: {sorted(np.unique(all_vals).tolist())})"
        elif rng < 10:
            notes = "LOW VARIANCE"
        elif min_v >= 240:
            notes = "HIGH BYTE REGION"
        elif max_v <= 15:
            notes = "NIBBLE RANGE"

        print(f"rb[{i:2d}] | {min_v:4d} | {max_v:4d} | {rng:5d} |  {unique:5d} | {mode_val:4d} | {notes}")


def try_mixed_width_decoding(data):
    """Try decoding with non-uniform field widths."""
    print_separator("12. MIXED-WIDTH FIELD DECODING ATTEMPTS")

    print("\nTrying various struct unpack formats on rest_bytes[0:14]...\n")

    # Various hypotheses for the 14 bytes:
    formats = [
        ("B H H H H H B",    "1+2+2+2+2+2+1=12... NO"),  # only 12
        ("<B hhh hhh B",      "1+6+6+1=14: flag + 2x vec3 + flag"),
        ("<H hhh hh B",       "2+6+4+1=13... NO"),
        ("<hhh hhh BB",       "6+6+2=14: two vec3s + 2 flags"),
        ("<hhh hh BB B",      "6+4+2+1=13... NO"),
        ("<B hhh BB BBB",     "1+6+2+3=12... NO"),
        ("<hhh I BB",         "6+4+2=12... NO"),
        ("<HH hhh BB B",     "4+6+2+1=13... NO"),
        ("<hhh HHHB",         "6+6+1=13... NO"),
        ("<Bhhh BBB BBB",     "1+6+3+3=13... NO"),
        ("<hhhh hhh",         "8+6=14: 7 signed shorts"),
        ("<hhh hhh h",        "14 = 7 signed shorts"),
    ]

    # Actually just try the clean ones that sum to 14
    clean_formats = [
        ("<hhhhhhh",     "7 × int16: all shorts"),
        ("<BBBBhhh BB",   "can't = 4+6+2=12 NO"),
    ]

    # Let's just decode as 7 signed shorts
    print("  Format: 7 × signed int16 LE (14 bytes exactly)")
    print()
    header = f"{'File':>12} |"
    for i in range(7):
        header += f" {'s16_'+str(i):>10} |"
    print(header)
    print("-" * len(header))

    for key in FILES:
        rest = data[key]["rest"]
        vals = np.zeros((len(rest), 7), dtype=np.int16)
        for pkt_idx in range(len(rest)):
            b = bytes(rest[pkt_idx])
            vals[pkt_idx] = struct.unpack("<hhhhhhh", b)
        means = vals.astype(float).mean(axis=0)
        row = f"{key:>12} |"
        for i in range(7):
            row += f" {means[i]:10.1f} |"
        print(row)

    # Try: 1 byte + 3 signed shorts + 1 byte + 3 signed shorts (1+6+1+6=14)
    print("\n  Format: B + 3×int16 + B + 3×int16 (1+6+1+6=14)")
    print("  (flag + accel_xyz + flag + gyro_xyz ?)")
    print()
    header = f"{'File':>12} | {'f1':>4} |"
    for ax in ['X1', 'Y1', 'Z1']:
        header += f" {ax:>8} |"
    header += f" {'f2':>4} |"
    for ax in ['X2', 'Y2', 'Z2']:
        header += f" {ax:>8} |"
    print(header)
    print("-" * len(header))

    for key in FILES:
        rest = data[key]["rest"]
        f1_arr = rest[:, 0].astype(float)
        v1 = np.zeros((len(rest), 3))
        f2_arr = rest[:, 7].astype(float)
        v2 = np.zeros((len(rest), 3))
        for pkt_idx in range(len(rest)):
            b = bytes(rest[pkt_idx])
            v1[pkt_idx] = struct.unpack("<hhh", b[1:7])
            v2[pkt_idx] = struct.unpack("<hhh", b[8:14])

        row = f"{key:>12} | {f1_arr.mean():4.1f} |"
        for i in range(3):
            row += f" {v1[:, i].mean():8.1f} |"
        row += f" {f2_arr.mean():4.1f} |"
        for i in range(3):
            row += f" {v2[:, i].mean():8.1f} |"
        print(row)

        # Check magnitudes
        mag1 = np.sqrt((v1**2).sum(axis=1)).mean()
        mag2 = np.sqrt((v2**2).sum(axis=1)).mean()

    print()
    print("  Magnitudes for B+3h+B+3h format:")
    for key in FILES:
        rest = data[key]["rest"]
        v1 = np.zeros((len(rest), 3))
        v2 = np.zeros((len(rest), 3))
        for pkt_idx in range(len(rest)):
            b = bytes(rest[pkt_idx])
            v1[pkt_idx] = struct.unpack("<hhh", b[1:7])
            v2[pkt_idx] = struct.unpack("<hhh", b[8:14])
        mag1 = np.sqrt((v1**2).sum(axis=1)).mean()
        mag2 = np.sqrt((v2**2).sum(axis=1)).mean()
        print(f"    {key:>12}: |vec1|={mag1:8.1f}  |vec2|={mag2:8.1f}")

    # Try: 2 bytes + 3 signed shorts + 3 signed shorts (2+6+6=14)
    print("\n  Format: H + 3×int16 + 3×int16 (2+6+6=14)")
    print("  (sub-timestamp/counter + accel_xyz + other_xyz ?)")
    print()
    for key in FILES:
        rest = data[key]["rest"]
        counter = np.zeros(len(rest))
        v1 = np.zeros((len(rest), 3))
        v2 = np.zeros((len(rest), 3))
        for pkt_idx in range(len(rest)):
            b = bytes(rest[pkt_idx])
            counter[pkt_idx] = struct.unpack("<H", b[0:2])[0]
            v1[pkt_idx] = struct.unpack("<hhh", b[2:8])
            v2[pkt_idx] = struct.unpack("<hhh", b[8:14])
        mag1 = np.sqrt((v1**2).sum(axis=1)).mean()
        mag2 = np.sqrt((v2**2).sum(axis=1)).mean()
        print(f"    {key:>12}: counter_mean={counter.mean():8.1f}  |vec1|={mag1:8.1f}  |vec2|={mag2:8.1f}")


def analyze_timestamp_gaps(data):
    """Analyze timestamp behavior."""
    print_separator("13. TIMESTAMP & PACKET TIMING ANALYSIS")

    for key in FILES:
        ts = data[key]["timestamps"]
        # Handle wrapping
        diffs = np.diff(ts.astype(np.int32))
        diffs = np.where(diffs < 0, diffs + 65536, diffs)
        print(f"  {key:>12}: {len(ts)} pkts, ts range [{ts.min()}-{ts.max()}], "
              f"Δts mean={diffs.mean():.1f} std={diffs.std():.1f} min={diffs.min()} max={diffs.max()}")


def deep_nibble_analysis(data):
    """Check if data might use 4-bit packing or odd bit boundaries."""
    print_separator("14. CHECKING FOR 4-BIT BOUNDARY PATTERNS")

    # Print a few raw bytes in binary to look for patterns
    for key in ["desk_up", "desk_down"]:
        rest = data[key]["rest"]
        print(f"\n  {key} - first 3 packets (binary):")
        for i in range(min(3, len(rest))):
            bits = " ".join(f"{b:08b}" for b in rest[i])
            print(f"    {bits}")


def main():
    print("=" * 100)
    print("  GARMIN BLE SENSOR DATA REVERSE-ENGINEERING ANALYSIS")
    print("=" * 100)

    data = load_data()

    # Print file info
    print("\n  Files loaded:")
    for key in FILES:
        d = data[key]
        print(f"    {key:>12}: {d['count']:3d} packets, rest_bytes shape: {d['rest'].shape}")

    # Run all analyses
    means, stds = analyze_individual_bytes(data)
    u16_means, u16_stds, s16_means, s16_stds, u16_data, s16_data = analyze_16bit_pairs(data)
    analyze_12bit_values(data)
    # analyze_nibbles(data)  # Verbose, skip unless needed
    height_orientation_analysis(data, s16_means, s16_stds)
    check_accel_magnitude(s16_data)
    analyze_alternate_16bit_offsets(data)
    analyze_raw_hex_dump(data)
    analyze_bit_patterns(data)
    analyze_potential_scaling(s16_data, s16_means)
    analyze_high_low_bytes(data)
    try_mixed_width_decoding(data)
    analyze_timestamp_gaps(data)
    deep_nibble_analysis(data)

    print_separator("ANALYSIS COMPLETE")


if __name__ == "__main__":
    main()
