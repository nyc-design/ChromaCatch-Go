"""Discord monitor runtime (self-client wrapper)."""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

MessageHandler = Callable[[object], Awaitable[None]]


class DiscordMonitor:
    """Minimal wrapper around discord.py-self style clients.

    This class intentionally imports the discord package lazily so tests and
    local API-only runs can work without the dependency installed.
    """

    def __init__(self, token: str, on_message: MessageHandler):
        self._token = token
        self._on_message = on_message
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

        intents = discord.Intents.default()
        intents.message_content = True
        intents.messages = True
        intents.guilds = True

        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():  # type: ignore[no-redef]
            user = getattr(client, "user", None)
            logger.info("Discord monitor connected as %s", user)

        @client.event
        async def on_message(message):  # type: ignore[no-redef]
            await self._on_message(message)

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
