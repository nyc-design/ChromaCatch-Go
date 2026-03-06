"""Discord monitor runtime (self-client wrapper)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

MessageHandler = Callable[[object], Awaitable[None]]
GatewayEventHandler = Callable[[str, dict], Awaitable[None]]


class DiscordMonitor:
    """Minimal wrapper around discord.py-self style clients.

    This class intentionally imports the discord package lazily so tests and
    local API-only runs can work without the dependency installed.
    """

    def __init__(
        self,
        token: str,
        on_message: MessageHandler,
        on_gateway_event: GatewayEventHandler | None = None,
    ):
        self._token = token
        self._on_message = on_message
        self._on_gateway_event = on_gateway_event
        self._client = None
        self._task: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    @property
    def connected(self) -> bool:
        return bool(self._client and getattr(self._client, "is_ready", lambda: False)())

    async def start(self) -> None:
        if not self._token:
            logger.warning("CC_SNIPER_DISCORD_TOKEN is empty; Discord monitor disabled")
            return

        try:
            import discord  # type: ignore
        except Exception as exc:  # pragma: no cover - exercised only in runtime env
            logger.error("Failed importing discord client library: %s", exc)
            return

        client_factory = getattr(discord, "Client", None)
        if client_factory is None:
            logger.error("Discord library does not expose Client; monitor disabled")
            return

        client_kwargs: dict = {}
        intents_factory = getattr(discord, "Intents", None)
        if intents_factory is not None:
            intents = intents_factory.default()
            if hasattr(intents, "message_content"):
                intents.message_content = True
            if hasattr(intents, "messages"):
                intents.messages = True
            if hasattr(intents, "guilds"):
                intents.guilds = True
            client_kwargs["intents"] = intents
        else:
            logger.warning(
                "Discord library has no Intents type; starting client without explicit intents"
            )

        try:
            client = client_factory(**client_kwargs)
        except TypeError:
            # Compatibility fallback for discord client implementations
            # that reject "intents" or differ in constructor signature.
            client = client_factory()

        @client.event
        async def on_ready():  # type: ignore[no-redef]
            user = getattr(client, "user", None)
            logger.info("Discord monitor connected as %s", user)

        @client.event
        async def on_message(message):  # type: ignore[no-redef]
            try:
                await self._on_message(message)
            except Exception:
                logger.exception("Failed handling Discord message_create event")

        @client.event
        async def on_message_edit(_before, after):  # type: ignore[no-redef]
            try:
                await self._on_message(after)
            except Exception:
                logger.exception("Failed handling Discord message_edit event")

        @client.event
        async def on_raw_message_edit(payload):  # type: ignore[no-redef]
            updated = getattr(payload, "message", None)
            if updated is None:
                return
            try:
                await self._on_message(updated)
            except Exception:
                logger.exception("Failed handling Discord raw_message_edit event")

        @client.event
        async def on_socket_raw_receive(payload):  # type: ignore[no-redef]
            if self._on_gateway_event is None:
                return
            if isinstance(payload, bytes):
                payload = payload.decode("utf-8", errors="ignore")
            if not isinstance(payload, str):
                return
            try:
                event = json.loads(payload)
            except Exception:
                return

            event_type = event.get("t")
            event_data = event.get("d")
            if event_type not in {"MESSAGE_CREATE", "MESSAGE_UPDATE"}:
                return
            if not isinstance(event_data, dict):
                return
            try:
                await self._on_gateway_event(event_type, event_data)
            except Exception:
                logger.exception("Failed handling Discord %s gateway event", event_type)

        self._client = client

        try:
            self._task = asyncio.create_task(client.start(self._token, bot=False))
        except TypeError:
            # Fallback for client implementations that do not accept bot=False.
            self._task = asyncio.create_task(client.start(self._token))

        await asyncio.sleep(0)

    async def stop(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:  # pragma: no cover
                logger.warning("Error closing Discord client: %s", exc)

        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover
                logger.warning("Discord monitor task exited with error: %s", exc)

        self._client = None
        self._task = None

    async def backfill_recent_messages(
        self,
        channel_ids: list[str],
        *,
        limit: int = 25,
        delay_seconds: float = 0.2,
    ) -> int:
        """Fetch recent channel messages and feed them through the normal message handler."""
        if self._client is None:
            return 0

        is_ready = getattr(self._client, "is_ready", None)
        if callable(is_ready) and not is_ready():
            return 0

        processed = 0
        seen = set()
        for channel_id in channel_ids:
            if not channel_id or channel_id in seen:
                continue
            seen.add(channel_id)
            try:
                channel_id_int = int(channel_id)
            except Exception:
                continue

            channel = None
            try:
                get_channel = getattr(self._client, "get_channel", None)
                if callable(get_channel):
                    channel = get_channel(channel_id_int)
                if channel is None:
                    fetch_channel = getattr(self._client, "fetch_channel", None)
                    if callable(fetch_channel):
                        channel = await fetch_channel(channel_id_int)
            except Exception as exc:
                logger.warning("Backfill: failed fetching channel=%s: %s", channel_id, exc)
                continue

            history = getattr(channel, "history", None)
            if not callable(history):
                continue

            try:
                async for message in history(limit=limit, oldest_first=True):
                    await self._on_message(message)
                    processed += 1
                    if delay_seconds > 0:
                        await asyncio.sleep(delay_seconds)
            except Exception as exc:
                logger.warning("Backfill: failed scanning channel=%s: %s", channel_id, exc)

        return processed
