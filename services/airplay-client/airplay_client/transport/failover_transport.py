"""Failover transport — tries SRT first, falls back to WebSocket on failure.

When SRT transport fails to connect or crashes repeatedly, this transport
automatically falls back to WebSocket mode. It periodically re-attempts
SRT to recover low-latency transport when possible.
"""

import asyncio
import logging

from airplay_client.transport.base import MediaTransport
from airplay_client.transport.srt_transport import SRTTransport
from airplay_client.transport.ws_transport import WebSocketTransport

logger = logging.getLogger(__name__)

# Fall back to WS after this many consecutive SRT restarts
SRT_MAX_RESTARTS_BEFORE_FALLBACK = 3

# Re-attempt SRT every N seconds when running in WS fallback
SRT_RETRY_INTERVAL_S = 60


class FailoverTransport(MediaTransport):
    """SRT transport with automatic WebSocket fallback.

    Starts with SRT. If SRT fails SRT_MAX_RESTARTS_BEFORE_FALLBACK times
    without staying connected for >30s, switches to WebSocket. Periodically
    re-attempts SRT.
    """

    def __init__(
        self,
        srt_transport: SRTTransport,
        ws_transport: WebSocketTransport,
    ):
        self._srt = srt_transport
        self._ws = ws_transport
        self._active: MediaTransport = self._srt
        self._running = False
        self._failover_task: asyncio.Task | None = None
        self._using_fallback = False

    async def start(self) -> None:
        """Start with SRT, monitor for failures."""
        self._running = True
        self._active = self._srt
        self._using_fallback = False
        await self._srt.start()
        self._failover_task = asyncio.create_task(self._failover_monitor())

    async def _failover_monitor(self) -> None:
        """Monitor SRT health, switch to WS on repeated failures."""
        last_restart_count = 0
        srt_connected_since: float | None = None

        while self._running:
            await asyncio.sleep(5)

            if self._using_fallback:
                # Periodically try to recover SRT
                await asyncio.sleep(SRT_RETRY_INTERVAL_S - 5)
                if not self._running:
                    break
                logger.info("Attempting SRT recovery...")
                try:
                    await self._ws.stop()
                    self._srt._restart_count = 0
                    await self._srt.start()
                    self._active = self._srt
                    self._using_fallback = False
                    last_restart_count = 0
                    srt_connected_since = None
                    logger.info("SRT recovery started, monitoring...")
                except Exception as e:
                    logger.warning("SRT recovery failed: %s, staying on WebSocket", e)
                    await self._ws.start()
                    self._active = self._ws
                continue

            # Monitor SRT health
            if self._srt.is_connected:
                import time
                if srt_connected_since is None:
                    srt_connected_since = time.time()
                # Reset restart tracking after sustained connection (>30s)
                if time.time() - srt_connected_since > 30:
                    last_restart_count = self._srt.restart_count
                    srt_connected_since = None
            else:
                srt_connected_since = None

            restarts_since = self._srt.restart_count - last_restart_count
            if restarts_since >= SRT_MAX_RESTARTS_BEFORE_FALLBACK:
                logger.warning(
                    "SRT failed %d times, falling back to WebSocket transport",
                    restarts_since,
                )
                await self._srt.stop()
                await self._ws.start()
                self._active = self._ws
                self._using_fallback = True

    async def stop(self) -> None:
        """Stop whichever transport is active."""
        self._running = False
        if self._failover_task:
            self._failover_task.cancel()
            try:
                await self._failover_task
            except asyncio.CancelledError:
                pass
            self._failover_task = None
        await self._srt.stop()
        if self._using_fallback:
            await self._ws.stop()

    @property
    def is_connected(self) -> bool:
        return self._active.is_connected

    @property
    def transport_name(self) -> str:
        if self._using_fallback:
            return "websocket (srt-fallback)"
        return self._active.transport_name

    @property
    def stats(self):
        """Delegate to SRT stats if available."""
        return getattr(self._srt, "stats", None)

    @property
    def frames_captured(self) -> int:
        return getattr(self._active, "frames_captured", 0)

    @property
    def frames_sent(self) -> int:
        return getattr(self._active, "frames_sent", 0)

    @property
    def audio_chunks_captured(self) -> int:
        return getattr(self._active, "audio_chunks_captured", 0)

    @property
    def audio_chunks_sent(self) -> int:
        return getattr(self._active, "audio_chunks_sent", 0)
