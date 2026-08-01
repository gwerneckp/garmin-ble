"""Subscribing to telemetry and consuming it."""

import asyncio
from contextlib import aclosing

import pytest

from garmin_ble import metrics
from garmin_ble.errors import ServiceUnavailable
from garmin_ble.metrics import Metric


class TestSubscription:
    async def test_subscribing_registers_the_service(self, watch):
        """One call, not a callback plus a separate service registration."""
        await watch.subscribe(metrics.HEART_RATE)
        assert int(metrics.HEART_RATE.service) in watch._handles

    async def test_subscribing_lists_the_metric(self, watch):
        await watch.subscribe(metrics.HEART_RATE)
        assert metrics.HEART_RATE in watch.subscriptions

    async def test_several_metrics_at_once(self, watch):
        await watch.subscribe(metrics.HEART_RATE, metrics.STEPS, metrics.STRESS)
        assert len(watch.subscriptions) == 3

    async def test_unsubscribing_releases_the_metric(self, watch):
        await watch.subscribe(metrics.HEART_RATE)
        await watch.unsubscribe(metrics.HEART_RATE)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_subscriptions_are_reference_counted(self, watch):
        """Two consumers of one metric must not cancel each other."""
        await watch.subscribe(metrics.HEART_RATE)
        await watch.subscribe(metrics.HEART_RATE)
        await watch.unsubscribe(metrics.HEART_RATE)
        assert metrics.HEART_RATE in watch.subscriptions

        await watch.unsubscribe(metrics.HEART_RATE)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_unsubscribing_what_was_never_held_is_harmless(self, watch):
        await watch.unsubscribe(metrics.HEART_RATE)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_a_refused_service_raises(self, limited_watch):
        """The watch declining registration must not look like success."""
        with pytest.raises(ServiceUnavailable) as exc:
            await limited_watch.subscribe(metrics.SPO2)
        assert exc.value.metric is metrics.SPO2
        assert "Venu 3" in exc.value.reason

    async def test_a_refused_service_leaves_no_subscription(self, limited_watch):
        with pytest.raises(ServiceUnavailable):
            await limited_watch.subscribe(metrics.SPO2)
        assert metrics.SPO2 not in limited_watch.subscriptions


class TestHandlers:
    async def test_handler_receives_typed_readings(self, watch):
        received = []

        @watch.on(metrics.HEART_RATE)
        def _(reading):
            received.append(reading)

        await asyncio.sleep(0.3)
        assert received
        assert all(isinstance(r, metrics.HeartRate) for r in received)
        assert all(r.bpm > 0 for r in received)

    async def test_async_handlers_are_awaited(self, watch):
        """A coroutine handler must actually run, not be dropped un-awaited."""
        received = []

        @watch.on(metrics.HEART_RATE)
        async def _(reading):
            await asyncio.sleep(0)
            received.append(reading)

        await asyncio.sleep(0.3)
        assert received

    async def test_several_handlers_for_one_metric_all_fire(self, watch):
        first, second = [], []
        watch.on(metrics.HEART_RATE)(first.append)
        watch.on(metrics.HEART_RATE)(second.append)

        await asyncio.sleep(0.3)
        assert first and second

    async def test_a_raising_handler_does_not_stop_the_others(self, watch):
        survived = []

        @watch.on(metrics.HEART_RATE)
        def _(reading):
            raise RuntimeError("handler blew up")

        watch.on(metrics.HEART_RATE)(survived.append)

        await asyncio.sleep(0.3)
        assert survived

    async def test_registering_a_handler_subscribes(self, watch):
        watch.on(metrics.STRESS)(lambda r: None)
        await asyncio.sleep(0.2)
        assert metrics.STRESS in watch.subscriptions


class TestStream:
    async def test_yields_typed_readings(self, watch):
        received = []
        async for reading in watch.stream(metrics.HEART_RATE):
            received.append(reading)
            if len(received) == 3:
                break
        assert all(isinstance(r, metrics.HeartRate) for r in received)

    async def test_leaving_the_loop_releases_the_subscription(self, watch):
        async for _ in watch.stream(metrics.HEART_RATE):
            break
        # Closing the iterator is scheduled, not immediate; the release lands a
        # few loop iterations later. `aclosing` is the deterministic form.
        await asyncio.sleep(0.05)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_an_exception_inside_the_loop_releases_it_too(self, watch):
        with pytest.raises(RuntimeError):
            async for _ in watch.stream(metrics.HEART_RATE):
                raise RuntimeError("boom")
        await asyncio.sleep(0.05)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_aclosing_releases_it_synchronously(self, watch):
        """For callers who need the release to have landed on the next line."""
        stream = watch.stream(metrics.HEART_RATE)
        async with aclosing(stream):
            async for _ in stream:
                break
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_two_streams_of_one_metric_are_independent(self, watch):
        """Both consumers see data, and the first to finish does not cut the other off."""
        first, second = [], []

        async def consume(sink, count):
            async for reading in watch.stream(metrics.HEART_RATE):
                sink.append(reading)
                if len(sink) == count:
                    return

        await asyncio.gather(consume(first, 2), consume(second, 5))
        assert len(first) == 2 and len(second) == 5


class TestStreamAll:
    async def test_interleaves_several_metrics(self, watch):
        seen = set()
        async for reading in watch.stream_all(
            metrics.HEART_RATE, metrics.STEPS, timeout=3
        ):
            seen.add(reading.metric)
            if len(seen) == 2:
                break
        assert seen == {metrics.HEART_RATE, metrics.STEPS}

    async def test_no_arguments_means_everything_supported(self, watch):
        seen = set()
        async for reading in watch.stream_all(timeout=3):
            seen.add(reading.metric)
            if len(seen) >= 5:
                break
        assert len(seen) >= 5

    async def test_unsupported_metrics_are_skipped_not_fatal(self, limited_watch):
        """A model that lacks SpO2 should still stream everything else."""
        seen = set()
        async for reading in limited_watch.stream_all(timeout=3):
            seen.add(reading.metric)
            if len(seen) >= 3:
                break
        assert metrics.SPO2 not in seen
        assert seen

    async def test_timeout_ends_the_iteration(self, watch):
        received = [r async for r in watch.stream_all(metrics.HEART_RATE, timeout=0.3)]
        assert received

    async def test_subscriptions_are_released_afterwards(self, watch):
        async for _ in watch.stream_all(metrics.HEART_RATE, timeout=0.3):
            pass
        assert not watch.subscriptions


class TestRead:
    async def test_returns_one_typed_reading(self, watch):
        reading = await watch.read(metrics.HEART_RATE, timeout=3)
        assert isinstance(reading, metrics.HeartRate)

    async def test_releases_the_subscription(self, watch):
        await watch.read(metrics.HEART_RATE, timeout=3)
        assert metrics.HEART_RATE not in watch.subscriptions

    async def test_times_out_rather_than_hanging(self, watch):
        """A registered service with nothing to report must not block forever."""
        watch._transport.silence()
        with pytest.raises(asyncio.TimeoutError):
            await watch.read(metrics.HEART_RATE, timeout=0.2)


class TestLatest:
    async def test_nothing_cached_before_the_first_sample(self, watch):
        assert watch.latest(metrics.HEART_RATE) is None

    async def test_the_most_recent_reading_is_kept(self, watch):
        reading = await watch.read(metrics.HEART_RATE, timeout=3)
        cached = watch.latest(metrics.HEART_RATE)
        assert isinstance(cached, metrics.HeartRate)
        assert cached.at >= reading.at


class TestCollect:
    async def test_gathers_one_sample_of_each(self, watch):
        result = await watch.collect(
            [metrics.HEART_RATE, metrics.STEPS, metrics.STRESS], timeout=5
        )
        assert result.complete
        assert set(result.samples) == {metrics.HEART_RATE, metrics.STEPS, metrics.STRESS}

    async def test_result_is_truthy_when_complete(self, watch):
        assert await watch.collect([metrics.HEART_RATE], timeout=5)

    async def test_returns_as_soon_as_the_condition_is_met(self, watch):
        """It must not sit out the whole timeout once it has what it needs."""
        result = await watch.collect([metrics.HEART_RATE], timeout=30)
        assert result.elapsed < 5

    async def test_unsupported_metrics_are_reported_as_such(self, limited_watch):
        result = await limited_watch.collect(
            [metrics.HEART_RATE, metrics.SPO2], timeout=3
        )
        assert not result.complete
        assert metrics.SPO2 in result.missing
        assert not result.missing[metrics.SPO2].supported

    async def test_missing_metrics_explain_themselves(self, watch):
        watch._transport.silence()
        result = await watch.collect([metrics.HEART_RATE], timeout=0.3)
        assert result.timed_out
        assert "timeout" in result.missing[metrics.HEART_RATE].reason

    async def test_cached_readings_count_by_default(self, watch):
        """Low-rate metrics are pushed once and then only on change.

        Requiring a fresh sample would report a known value as missing.
        """
        await watch.read(metrics.HEART_RATE, timeout=3)
        watch._transport.silence()

        result = await watch.collect([metrics.HEART_RATE], timeout=0.3)
        assert result.complete
        assert result.counts[metrics.HEART_RATE] == 0  # seen earlier, not during

    async def test_cached_readings_can_be_refused(self, watch):
        await watch.read(metrics.HEART_RATE, timeout=3)
        watch._transport.silence()

        result = await watch.collect(
            [metrics.HEART_RATE], timeout=0.3, include_cached=False
        )
        assert not result.complete

    async def test_defaults_to_every_metric(self, watch):
        result = await watch.collect(timeout=5)
        assert set(result.requested) == set(Metric.ALL_TELEMETRY)

    async def test_rejects_an_unknown_condition(self, watch):
        with pytest.raises(ValueError, match="unknown collect condition"):
            await watch.collect([metrics.HEART_RATE], until="whenever")

    async def test_renders_as_a_checklist(self, watch):
        result = await watch.collect([metrics.HEART_RATE], timeout=5)
        assert "heart_rate" in str(result)
        assert "✅" in str(result)

    async def test_subscriptions_are_released_afterwards(self, watch):
        await watch.collect([metrics.HEART_RATE, metrics.STEPS], timeout=5)
        assert not watch.subscriptions
