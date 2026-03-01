"""Tests for RTSP frame consumer."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.rtsp_consumer import RTSPFrameConsumer
from backend.session_manager import SessionManager


class TestRTSPFrameConsumer:
    @pytest.fixture
    def session_manager(self):
        return SessionManager()

    @pytest.fixture
    def consumer(self, session_manager):
        return RTSPFrameConsumer(session_manager)

    @pytest.mark.asyncio
    @patch("backend.rtsp_consumer.settings")
    async def test_start_disabled(self, mock_settings, consumer, caplog):
        mock_settings.rtsp_consumer_enabled = False
        import logging
        with caplog.at_level(logging.INFO):
            await consumer.start()
        assert "disabled" in caplog.text.lower()

    @pytest.mark.asyncio
    @patch("backend.rtsp_consumer.settings")
    async def test_start_enabled(self, mock_settings, consumer):
        mock_settings.rtsp_consumer_enabled = True
        mock_settings.rtsp_base_url = "rtsp://127.0.0.1:8554"
        await consumer.start()
        assert consumer._running is True
        await consumer.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_consumers(self, consumer):
        consumer._running = True
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        consumer._consumers["test-client"] = mock_task
        await consumer.stop()
        assert not consumer._running
        assert len(consumer._consumers) == 0
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    @patch("backend.rtsp_consumer.settings")
    async def test_add_stream_creates_task(self, mock_settings, consumer):
        mock_settings.rtsp_base_url = "rtsp://127.0.0.1:8554"
        consumer._running = True
        # Mock _consume_loop to avoid actual RTSP connection
        with patch.object(consumer, "_consume_loop", new_callable=AsyncMock):
            await consumer.add_stream("client-1")
            assert "client-1" in consumer._consumers
            # Clean up
            task = consumer._consumers["client-1"]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_add_stream_duplicate_skips(self, consumer, caplog):
        consumer._running = True
        consumer._consumers["client-1"] = MagicMock()
        import logging
        with caplog.at_level(logging.WARNING):
            await consumer.add_stream("client-1")
        assert "already running" in caplog.text

    @pytest.mark.asyncio
    async def test_remove_stream(self, consumer):
        mock_task = MagicMock()
        mock_task.cancel = MagicMock()
        consumer._consumers["client-1"] = mock_task
        await consumer.remove_stream("client-1")
        assert "client-1" not in consumer._consumers
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_stream_nonexistent(self, consumer):
        await consumer.remove_stream("nonexistent")  # Should not raise

    @pytest.mark.asyncio
    async def test_add_stream_custom_path(self, consumer):
        consumer._running = True
        with patch.object(consumer, "_consume_loop", new_callable=AsyncMock) as mock_loop:
            await consumer.add_stream("client-2", stream_path="custom/stream")
            assert "client-2" in consumer._consumers
            # Verify custom path was used
            mock_loop.assert_called_once()
            call_args = mock_loop.call_args
            assert "custom/stream" in call_args[0][1]
            task = consumer._consumers["client-2"]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
