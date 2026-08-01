"""Wire-format tests for every telemetry parser.

Parsers are tested directly rather than through a connected watch, so a routing
bug cannot hide a parsing bug or vice versa.

Payloads here exclude the leading MLR routing byte, which is what `parse`
receives. The captured vectors below still include it, and are sliced at the
call site to make that explicit.
"""

import struct

import pytest

from garmin_ble import metrics as m


class TestHeartRate:
    def test_parses_bpm_and_resting(self):
        reading = m.HEART_RATE.parse(bytes([0, 72, 55]))
        assert reading == m.HeartRate(bpm=72, resting_bpm=55)

    def test_zero_resting_becomes_none(self):
        """The watch sends 0 before it has established a resting rate.

        Reporting that as `0 bpm resting` is a lie; `None` is the truth.
        """
        assert m.HEART_RATE.parse(bytes([0, 72, 0])).resting_bpm is None

    def test_short_packet_yields_nothing(self):
        assert m.HEART_RATE.parse(bytes([0, 72])) is None


class TestSteps:
    def test_parses_count_and_goal(self):
        reading = m.STEPS.parse(struct.pack("<II", 5432, 10000))
        assert (reading.count, reading.goal) == (5432, 10000)

    def test_fraction_of_goal(self):
        assert m.STEPS.parse(struct.pack("<II", 5000, 10000)).fraction_of_goal == 0.5

    def test_no_goal_does_not_divide_by_zero(self):
        assert m.STEPS.parse(struct.pack("<II", 5000, 0)).fraction_of_goal == 0.0


class TestHrv:
    def test_parses_rr_interval(self):
        assert m.HRV.parse(struct.pack("<H", 850)).rr_ms == 850

    def test_instantaneous_bpm(self):
        assert round(m.HRV.parse(struct.pack("<H", 1000)).instantaneous_bpm) == 60


class TestSpO2:
    def test_parses_percentage(self):
        assert m.SPO2.parse(bytes([98])).percent == 98

    def test_sensor_not_ready_sentinel_yields_nothing(self):
        """255 is Garmin's "no reading" marker, not a 255% saturation."""
        assert m.SPO2.parse(bytes([255])) is None


class TestRespiration:
    def test_parses_rate(self):
        assert m.RESPIRATION.parse(bytes([14])).breaths_per_min == 14

    @pytest.mark.parametrize("value", [0, 0xFF])  # 0xFF is -1 as int8
    def test_non_positive_yields_nothing(self, value):
        assert m.RESPIRATION.parse(bytes([value])) is None


class TestCalories:
    def test_parses_total_and_active(self):
        reading = m.CALORIES.parse(struct.pack("<II", 1800, 420))
        assert (reading.total, reading.active) == (1800, 420)

    def test_resting_is_the_difference(self):
        assert m.CALORIES.parse(struct.pack("<II", 1800, 420)).resting == 1380

    def test_resting_never_goes_negative(self):
        assert m.CALORIES.parse(struct.pack("<II", 100, 400)).resting == 0


class TestIntensity:
    def test_parses_both_bands(self):
        reading = m.INTENSITY.parse(struct.pack("<HH", 30, 12))
        assert (reading.moderate, reading.vigorous) == (30, 12)

    def test_total_counts_vigorous_double(self):
        """Garmin scores vigorous minutes double toward the weekly goal."""
        assert m.INTENSITY.parse(struct.pack("<HH", 30, 12)).total == 54


class TestStress:
    def test_parses_level(self):
        assert m.STRESS.parse(bytes([42])).level == 42

    def test_negative_level_survives_as_int8(self):
        """A negative score means the watch cannot compute one right now."""
        assert m.STRESS.parse(bytes([0xFF])).level == -1

    @pytest.mark.parametrize(
        "level,band", [(10, "rest"), (40, "low"), (60, "medium"), (90, "high")]
    )
    def test_bands(self, level, band):
        assert m.STRESS.parse(bytes([level])).band == band


class TestBodyBattery:
    def test_parses_level(self):
        assert m.BODY_BATTERY.parse(bytes([67])).level == 67


class TestAccelerometer:
    """Captured from a real fenix 7 in four known orientations.

    These vectors are the most valuable assertions in the suite: they pin the
    12-bit unpacking and the 256-counts-per-g scale against ground truth.
    Each includes its leading MLR routing byte, sliced off at the call site.
    """

    @staticmethod
    def parse(capture_hex: str):
        return m.ACCELEROMETER.parse(bytes.fromhex(capture_hex)[1:])

    def test_flat_on_desk_face_up(self):
        packet = self.parse("104f71000000010f00fd1ff002e0ff021f")
        assert len(packet.samples) == 3
        x, y, z = packet.samples[0].g
        assert x == 0.0
        assert round(z, 3) == -0.996
        assert 0.95 < packet.samples[0].magnitude_g < 1.05

    def test_flat_on_desk_face_down(self):
        packet = self.parse("10a16604d0ff015100fe0f1005f0fffe10")
        x, y, z = packet.samples[0].g
        assert abs(x) < 0.05
        assert abs(y) < 0.05
        assert round(z, 3) == 1.004
        assert 0.95 < packet.samples[0].magnitude_g < 1.05

    def test_tilted_45_degrees_splits_gravity(self):
        packet = self.parse("100669354f01694ff31470f6343f01671f")
        x, y, z = packet.samples[0].g
        assert round(x, 2) == -0.79
        assert round(z, 2) == -0.59
        # Split across two axes, but still one g in total.
        assert 0.95 < packet.samples[0].magnitude_g < 1.05

    def test_shaking_exceeds_one_g(self):
        packet = self.parse("100b712dc307a51056fc09fe7583af6b10")
        first, second = packet.samples[0], packet.samples[1]
        assert round(first.g[0], 2) == 3.18
        assert round(first.g[2], 2) == 0.64
        assert round(second.g[0], 2) == 5.38
        assert round(second.g[1], 2) == -6.02
        assert second.magnitude_g > 5.0

    def test_timestamp_is_read(self):
        assert self.parse("104f71000000010f00fd1ff002e0ff021f").timestamp_ms == 0x714F

    def test_packet_is_iterable(self):
        packet = self.parse("104f71000000010f00fd1ff002e0ff021f")
        assert len(list(packet)) == len(packet) == 3

    def test_short_packet_yields_nothing(self):
        assert m.ACCELEROMETER.parse(bytes(10)) is None


class TestRoundTrip:
    """Every parser has an encoder, and they must agree.

    This is what lets the simulator generate real wire packets instead of
    fixtures, and it catches an off-by-one in either direction.
    """

    SAMPLES = [
        m.HeartRate(bpm=72, resting_bpm=55),
        m.HeartRate(bpm=72, resting_bpm=None),
        m.Steps(count=5432, goal=10000),
        m.Hrv(rr_ms=850),
        m.SpO2(percent=98),
        m.Respiration(breaths_per_min=14),
        m.Calories(total=1800, active=420),
        m.Intensity(moderate=30, vigorous=12),
        m.Stress(level=42),
        m.Stress(level=-1),
        m.BodyBattery(level=67),
    ]

    @pytest.mark.parametrize("reading", SAMPLES, ids=lambda r: type(r).__name__)
    def test_encode_then_parse_is_identity(self, reading):
        metric = reading.metric
        assert metric.parse(metric.encode(reading)) == reading

    def test_accelerometer_round_trip(self):
        original = m.ACCELEROMETER.parse(
            bytes.fromhex("100b712dc307a51056fc09fe7583af6b10")[1:]
        )
        assert m.ACCELEROMETER.parse(m.ACCELEROMETER.encode(original)) == original

    def test_accelerometer_round_trip_preserves_sign(self):
        packet = m.AccelPacket(
            samples=(
                m.AccelSample(-2048, 2047, 0),
                m.AccelSample(1, -1, 256),
                m.AccelSample(0, 0, -256),
            ),
            timestamp_ms=12345,
        )
        assert m.ACCELEROMETER.parse(m.ACCELEROMETER.encode(packet)) == packet


class TestRegistry:
    """Service code, reading type, and parser stay bound to one another."""

    def test_every_metric_is_reachable_by_service_code(self):
        for metric in m.Metric.ALL_TELEMETRY:
            assert m.by_service(int(metric.service)) is metric

    def test_every_metric_is_reachable_by_name(self):
        for metric in m.Metric.ALL_TELEMETRY:
            assert m.by_name(metric.name) is metric

    def test_service_codes_are_unique(self):
        codes = [int(metric.service) for metric in m.Metric.ALL_TELEMETRY]
        assert len(codes) == len(set(codes))

    def test_reading_knows_its_own_metric(self):
        """`reading.metric` works without the caller threading it through."""
        assert m.HeartRate(bpm=60).metric is m.HEART_RATE

    def test_all_telemetry_covers_the_registry(self):
        assert set(m.Metric.ALL_TELEMETRY) == set(m.all_metrics())

    def test_readings_carry_an_arrival_time(self):
        assert m.HeartRate(bpm=60).at is not None

    def test_arrival_time_is_excluded_from_equality(self):
        """Two identical samples compare equal even a moment apart."""
        assert m.HeartRate(bpm=60) == m.HeartRate(bpm=60)

    def test_readings_render_for_humans(self):
        for metric in m.Metric.ALL_TELEMETRY:
            sample = metric.parse(metric.encode(_example_for(metric)))
            assert str(sample) and not str(sample).startswith("<")


def _example_for(metric):
    examples = {
        "heart_rate": m.HeartRate(bpm=60, resting_bpm=50),
        "steps": m.Steps(count=100, goal=1000),
        "hrv": m.Hrv(rr_ms=800),
        "spo2": m.SpO2(percent=97),
        "respiration": m.Respiration(breaths_per_min=15),
        "calories": m.Calories(total=100, active=10),
        "intensity": m.Intensity(moderate=1, vigorous=2),
        "stress": m.Stress(level=30),
        "body_battery": m.BodyBattery(level=70),
        "accelerometer": m.AccelPacket(
            samples=(m.AccelSample(0, 0, -256),) * 3, timestamp_ms=1
        ),
    }
    return examples[metric.name]
