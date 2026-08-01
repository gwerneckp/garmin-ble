"""Frame tracing, capture files, and replay."""

import pytest

from garmin_ble import Direction, FrameKind, Watch, metrics, read_capture


class TestFrameTracing:
    async def test_frames_are_reported_in_both_directions(self, watch):
        frames = []
        watch.on_frame(frames.append)

        await watch.read(metrics.HEART_RATE, timeout=3)

        directions = {f.direction for f in frames}
        assert Direction.TX in directions
        assert Direction.RX in directions

    async def test_telemetry_frames_carry_a_decoded_summary(self, watch):
        frames = []
        watch.on_frame(frames.append)

        await watch.read(metrics.HEART_RATE, timeout=3)

        telemetry = [f for f in frames if f.kind is FrameKind.TELEMETRY]
        assert telemetry
        assert "heart_rate" in telemetry[0].decoded

    async def test_frames_render_for_humans(self, watch):
        frames = []
        watch.on_frame(frames.append)
        await watch.read(metrics.HEART_RATE, timeout=3)
        assert all("handle=0x" in str(f) for f in frames)

    async def test_a_raising_frame_handler_does_not_break_the_link(self, watch):
        @watch.on_frame
        def _(frame):
            raise RuntimeError("frame handler blew up")

        await watch.read(metrics.HEART_RATE, timeout=3)
        assert watch.is_connected


class TestCaptureFile:
    async def test_records_frames_to_disk(self, tmp_path):
        path = tmp_path / "session.gble"
        async with Watch.simulated() as watch:
            watch.record(path)
            await watch.read(metrics.HEART_RATE, timeout=3)

        frames = list(read_capture(path))
        assert frames
        assert {f.direction for f in frames} == {Direction.TX, Direction.RX}

    async def test_captured_frames_survive_the_round_trip(self, tmp_path):
        path = tmp_path / "session.gble"
        async with Watch.simulated() as watch:
            written = []
            watch.on_frame(written.append)
            watch.record(path)
            await watch.read(metrics.HEART_RATE, timeout=3)

        loaded = list(read_capture(path))
        assert [f.raw for f in loaded] == [f.raw for f in written]
        assert [f.kind for f in loaded] == [f.kind for f in written]


class TestReplay:
    @pytest.fixture
    async def capture(self, tmp_path):
        path = tmp_path / "session.gble"
        async with Watch.simulated() as watch:
            watch.record(path)
            async for _ in watch.stream_all(
                metrics.HEART_RATE, metrics.STEPS, timeout=0.5
            ):
                pass
        return path

    async def test_replays_the_recorded_telemetry(self, capture):
        received = []
        async with Watch.replay(capture) as watch:
            async for reading in watch.stream_all(timeout=2):
                received.append(reading)

        assert received
        assert {type(r) for r in received} == {metrics.HeartRate, metrics.Steps}

    async def test_replay_needs_no_handshake(self, capture):
        """The capture already contains it; re-running it would hang."""
        async with Watch.replay(capture) as watch:
            assert watch.is_connected

    async def test_subscribing_on_a_replay_is_a_no_op(self, capture):
        """A recording contains what it contains; there is nothing to request."""
        async with Watch.replay(capture) as watch:
            await watch.subscribe(metrics.SPO2)  # never recorded, must not raise

    async def test_a_missing_capture_says_so(self, tmp_path):
        from garmin_ble.errors import ConnectionFailed

        with pytest.raises(ConnectionFailed, match="not found"):
            async with Watch.replay(tmp_path / "nope.gble"):
                pass

    async def test_a_capture_with_no_inbound_frames_is_rejected(self, tmp_path):
        from garmin_ble.errors import ConnectionFailed
        from garmin_ble.frames import Frame, Recorder

        path = tmp_path / "tx_only.gble"
        recorder = Recorder(path)
        recorder.write(
            Frame(
                direction=Direction.TX, kind=FrameKind.CONTROL,
                handle=0, raw=b"\x00\x05",
            )
        )
        recorder.close()

        with pytest.raises(ConnectionFailed, match="no inbound frames"):
            async with Watch.replay(path):
                pass


class TestDiagnostics:
    async def test_handles_are_named(self, watch):
        await watch.subscribe(metrics.HEART_RATE)
        assert "gfdi" in watch.diagnostics.handles
        assert "heart_rate" in watch.diagnostics.handles

    async def test_frame_counters_move(self, watch):
        await watch.read(metrics.HEART_RATE, timeout=3)
        assert watch.diagnostics.frames_tx > 0
        assert watch.diagnostics.frames_rx > 0
        assert watch.diagnostics.malformed == 0

    async def test_summary_reports_the_live_state(self, watch):
        await watch.subscribe(metrics.HEART_RATE)
        await watch.battery()
        summary = watch.diagnostics.summary()

        assert "heart_rate" in summary
        assert "frames:" in summary
        assert "requests:  1 sent, 1 answered" in summary

    async def test_malformed_packets_are_counted_not_fatal(self, watch):
        await watch._on_packet(b"")
        assert watch.diagnostics.malformed == 1
        assert watch.is_connected

    async def test_latency_percentiles_survive_an_empty_history(self, watch):
        assert "n/a" in watch.diagnostics.summary()
