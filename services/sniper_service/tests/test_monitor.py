import asyncio
import sys
from types import SimpleNamespace

import pytest

from sniper_service.monitor import DiscordMonitor


@pytest.mark.asyncio
async def test_monitor_starts_without_intents_api(monkeypatch):
    class FakeClient:
        def __init__(self, *args, **kwargs):
            self._ready = False
            self.user = "fake-user"

        def is_ready(self):
            return self._ready

        def event(self, fn):
            return fn

        async def start(self, token, bot=False):
            self._ready = True
            await asyncio.sleep(0.1)

        async def close(self):
            self._ready = False

    fake_discord = SimpleNamespace(Client=FakeClient)
    monkeypatch.setitem(sys.modules, "discord", fake_discord)

    async def on_message(_msg):
        return None

    monitor = DiscordMonitor(token="token", on_message=on_message)
    await monitor.start()
    assert monitor.enabled is True
    await monitor.stop()

