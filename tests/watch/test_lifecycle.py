"""Session lifecycle: handshake, teardown, identity, reconnection."""

import asyncio

import pytest

from garmin_ble import Watch, events, metrics
from garmin_ble.constants import GarminService
from garmin_ble.errors import HandshakeError, NotConnected
from garmin_ble.transport.simulated import SimulatedTransport


class TestHandshake:
    async def test_opening_a_session_completes_the_handshake(self, watch):
        assert watch.is_connected

    async def test_gfdi_is_registered(self, watch):
        """Without the control channel nothing else can be negotiated."""
        assert int(GarminService.GFDI) in watch._handles

    async def test_handshake_is_awaited_not_assumed(self, watch):
        """A completed open() means the watch actually answered.

        Registration confirmations arrive asynchronously, so the guarantee is
        that they have landed by the time `open()` returns.
        """
        assert watch.diagnostics.handles.get("gfdi") is not None

    async def test_a_silent_watch_raises_rather_than_hanging(self, monkeypatch):
        """A watch that never answers CLOSE_ALL is the paired-to-a-phone case."""

        async def swallow(self, data):
            return None

        monkeypatch.setattr(SimulatedTransport, "write", swallow)
        monkeypatch.setattr(Watch, "HANDSHAKE_TIMEOUT", 0.2)

        with pytest.raises(HandshakeError) as exc:
            async with Watch.simulated():
                pass
        assert exc.value.stage == "close_all"

    async def test_failed_open_still_releases_the_transport(self, monkeypatch):
        async def swallow(self, data):
            return None

        monkeypatch.setattr(SimulatedTransport, "write", swallow)
        monkeypatch.setattr(Watch, "HANDSHAKE_TIMEOUT", 0.2)

        session = Watch.simulated()
        with pytest.raises(HandshakeError):
            await session.open()
        assert not session._watch


class TestIdentity:
    async def test_info_reports_the_device(self, watch):
        assert watch.info.name == "fenix 7"
        assert watch.info.address
        assert watch.info.mtu > 23

    async def test_info_before_connecting_raises(self):
        """`info` has no meaningful value until there is a link."""
        unconnected = Watch(SimulatedTransport())
        with pytest.raises(NotConnected):
            unconnected.info

    async def test_a_declined_metric_raises_with_a_reason(self, limited_watch):
        """Asking is the only capability test the protocol offers."""
        from garmin_ble.errors import ServiceUnavailable

        with pytest.raises(ServiceUnavailable) as exc:
            await limited_watch.subscribe(metrics.SPO2)
        assert exc.value.metric is metrics.SPO2
        assert exc.value.reason


class TestTeardown:
    async def test_leaving_the_block_disconnects(self):
        async with Watch.simulated() as watch:
            assert watch.is_connected
        assert not watch.is_connected

    async def test_an_exception_inside_the_block_still_disconnects(self):
        captured = {}
        with pytest.raises(RuntimeError):
            async with Watch.simulated() as watch:
                captured["watch"] = watch
                raise RuntimeError("boom")
        assert not captured["watch"].is_connected

    async def test_cancellation_still_disconnects(self):
        captured = {}

        async def session():
            async with Watch.simulated() as watch:
                captured["watch"] = watch
                await asyncio.Event().wait()

        task = asyncio.create_task(session())
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not captured["watch"].is_connected

    async def test_streams_end_when_the_session_closes(self):
        session = Watch.simulated()
        watch = await session.open()
        received = []

        async def consume():
            async for reading in watch.stream(metrics.HEART_RATE):
                received.append(reading)

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.15)
        await session.aclose()
        # The iterator terminates rather than hanging on a queue nobody feeds.
        await asyncio.wait_for(task, timeout=1.0)
        assert received


class TestReconnection:
    async def test_a_dropped_link_comes_back(self):
        session = Watch.simulated(reconnect="exponential")
        watch = await session.open()
        try:
            await watch.subscribe(metrics.HEART_RATE)
            watch._transport.simulate_drop()
            await asyncio.sleep(3.0)

            assert watch.is_connected
            assert watch.diagnostics.reconnects == 1
        finally:
            await session.aclose()

    async def test_subscriptions_are_restored(self):
        session = Watch.simulated(reconnect="exponential")
        watch = await session.open()
        try:
            await watch.subscribe(metrics.HEART_RATE, metrics.STEPS)
            watch._transport.simulate_drop()
            await asyncio.sleep(3.0)

            assert set(watch.subscriptions) == {metrics.HEART_RATE, metrics.STEPS}
            # And they are live again, not merely listed.
            assert await watch.read(metrics.HEART_RATE, timeout=3)
        finally:
            await session.aclose()

    async def test_a_drop_is_reported_before_the_recovery(self):
        session = Watch.simulated(reconnect="exponential")
        watch = await session.open()
        seen = []

        async def pump():
            async for event in watch.events():
                seen.append(type(event))

        task = asyncio.create_task(pump())
        await asyncio.sleep(0)  # let the consumer subscribe before anything fires
        try:
            watch._transport.simulate_drop()
            await asyncio.sleep(3.0)
            assert seen.index(events.Disconnected) < seen.index(events.Reconnected)
        finally:
            task.cancel()
            await session.aclose()

    async def test_reconnect_off_leaves_the_link_down(self):
        session = Watch.simulated(reconnect="off")
        watch = await session.open()
        try:
            watch._transport.simulate_drop()
            await asyncio.sleep(0.5)
            assert not watch.is_connected
            assert watch.diagnostics.reconnects == 0
        finally:
            await session.aclose()


class TestSessionForms:
    async def test_session_is_awaitable(self):
        """For callers who want to own the lifetime themselves."""
        session = Watch.simulated()
        watch = await session
        try:
            assert watch.is_connected
        finally:
            await session.aclose()

    async def test_aclose_is_idempotent(self):
        session = Watch.simulated()
        await session.open()
        await session.aclose()
        await session.aclose()

    async def test_unknown_simulator_profile_is_rejected_by_name(self):
        with pytest.raises(ValueError, match="unknown simulator profile"):
            Watch.simulated(profile="nonexistent")
