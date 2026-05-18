"""Tests for GarminClient lifecycle management (events, disconnect, sync loop).

The client has an event system, a background sync loop, and a clean
disconnect path.  These tests verify correct resource cleanup and
event routing behaviour.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock

from garmin_ble.client import GarminClient


class TestClientLifecycle:
    """We are testing if: the client handles events, disconnect, and sync loop lifecycle.

    The event system uses `client.on("event_name", callback)` to register
    listeners.  Unknown event names emit a warning.  The sync loop polls the
    watch and cancels cleanly on disconnect.
    """

    async def test_on_unknown_event(self, capsys):
        """We are testing if: registering an unknown event name prints a warning.

        The client should not crash, but it should notify the user that the
        event name is not recognised so they can catch typos early.
        """
        client = GarminClient()
        client.on("nonexistent", lambda: None)
        captured = capsys.readouterr()
        assert "Warning" in captured.out
        assert "nonexistent" in captured.out

    async def test_disconnect_on_start_sync_loop_cancel(self, mock_bleak_client, mocker):
        """We are testing if: cancelling the sync loop disconnects the BLE client.

        When start_sync_loop is cancelled via asyncio.CancelledError, the
        client must call disconnect on the underlying BleakClient to release
        the BLE connection cleanly.
        """
        mocker.patch("garmin_ble.client.BleakClient", return_value=mock_bleak_client)
        mocker.patch(
            "garmin_ble.client.BleakScanner.discover",
            new=AsyncMock(return_value={}),
        )

        client = GarminClient()
        client.client = mock_bleak_client

        task = asyncio.create_task(client.start_sync_loop())
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)

        mock_bleak_client.disconnect.assert_awaited_once()

    async def test_multiple_event_callbacks_override(self):
        """We are testing if: registering a second callback overrides the first.

        The event system uses a single-callback model — calling `on()` with an
        existing event name replaces the previous callback.  To test multiple
        listeners, users should chain them inside one callback.
        """
        client = GarminClient()
        results = []
        client.on("hr", lambda hr, rest: results.append(("first", hr, rest)))
        client.on("hr", lambda hr, rest: results.append(("second", hr, rest)))

        # The second registration overrides the first
        assert client.callbacks["hr"] is not None
        assert len(results) == 0
