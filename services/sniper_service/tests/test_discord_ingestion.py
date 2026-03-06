from datetime import UTC, datetime, timedelta

import pytest

from sniper_service.main import service
from sniper_service.models import WatchBlock


class FakeGuild:
    def __init__(self, gid: str):
        self.id = gid


class FakeChannel:
    def __init__(self, cid: str):
        self.id = cid


class FakeAuthor:
    def __init__(self, uid: str):
        self.id = uid


class FakeEmbed:
    def __init__(self, description: str):
        self.description = description

    def to_dict(self):
        return {"description": self.description}


class FakeButton:
    def __init__(self, label: str):
        self.label = label
        self.custom_id = "reveal_btn"
        self.disabled = False
        self.clicked = False

    async def click(self):
        self.clicked = True


class FakeRow:
    def __init__(self, children):
        self.children = children


class FakeMessage:
    def __init__(self, *, message_id: str, content: str, components=None, embeds=None):
        self.id = message_id
        self.guild = FakeGuild("111")
        self.channel = FakeChannel("222")
        self.author = FakeAuthor("333")
        self.content = content
        self.components = components or []
        self.embeds = embeds or []
        self.created_at = datetime.now(UTC)


def setup_function() -> None:
    service.replace_watch_blocks(
        [WatchBlock(id="wb-1", server_id="111", channel_id="222", user_ids=["333"])]
    )
    service.clear_queue()
    service._reveal_attempted_message_ids.clear()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_message_without_coords_clicks_reveal_button():
    button = FakeButton("Reveal")
    message = FakeMessage(
        message_id="msg-reveal",
        content="Spawn alert incoming",
        components=[FakeRow([button])],
    )

    await service.handle_discord_message(message)

    assert button.clicked is True
    assert service.queue_size == 0


@pytest.mark.asyncio
async def test_message_edit_payload_with_coords_is_queued():
    future_epoch = int((datetime.now(UTC) + timedelta(minutes=5)).timestamp())
    message = FakeMessage(
        message_id="msg-coords",
        content=f"Despawn <t:{future_epoch}:R>",
        embeds=[FakeEmbed("37.774900, -122.419400")],
    )

    await service.handle_discord_message(message)

    state = service.queue_state()
    assert state.size == 1
    assert state.items[0].latitude == 37.7749
    assert state.items[0].longitude == -122.4194
