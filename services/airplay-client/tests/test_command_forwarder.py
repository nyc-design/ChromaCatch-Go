"""Tests for CommandForwarder (generalized from ESP32Forwarder)."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from airplay_client.commander.base import Commander, CommandResult
from airplay_client.esp32_forwarder import CommandForwarder, ESP32Forwarder
from shared.messages import GameCommandMessage, HIDCommandMessage


class MockCommander(Commander):
    """Test commander that records calls."""

    def __init__(self, success: bool = True, error: str | None = None):
        self._success = success
        self._error = error
        self._connected = False
        self._calls: list[tuple[str, dict]] = []

    async def send_command(self, action: str, params: dict) -> CommandResult:
        self._calls.append((action, params))
        return CommandResult(
            success=self._success,
            forwarded_at=1.0,
            completed_at=2.0,
            error=self._error,
        )

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "mock"

    @property
    def supported_command_types(self) -> list[str]:
        return ["test"]


class TestCommandForwarder:
    @pytest.fixture
    def commander(self):
        return MockCommander()

    @pytest.fixture
    def forwarder(self, commander):
        return CommandForwarder(commander)

    @pytest.mark.asyncio
    async def test_handle_hid_command(self, forwarder, commander):
        msg = HIDCommandMessage(action="click", params={"x": 100, "y": 200})
        ack = await forwarder.handle_command(msg)
        assert ack.success is True
        assert len(commander._calls) == 1
        assert commander._calls[0] == ("click", {"x": 100, "y": 200})

    @pytest.mark.asyncio
    async def test_handle_game_command(self, forwarder, commander):
        msg = GameCommandMessage(
            command_type="gamepad",
            action="button_press",
            params={"button": "A"},
        )
        ack = await forwarder.handle_command(msg)
        assert ack.success is True
        assert len(commander._calls) == 1
        assert commander._calls[0] == ("button_press", {"button": "A"})

    @pytest.mark.asyncio
    async def test_handle_stick_command(self, forwarder, commander):
        msg = GameCommandMessage(
            command_type="gamepad",
            action="stick",
            params={"stick_id": "left", "x": 32767, "y": 0},
        )
        ack = await forwarder.handle_command(msg)
        assert ack.success is True
        assert commander._calls[0][1] == {"stick_id": "left", "x": 32767, "y": 0}

    @pytest.mark.asyncio
    async def test_failure_returns_error_ack(self):
        commander = MockCommander(success=False, error="device offline")
        forwarder = CommandForwarder(commander)
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        ack = await forwarder.handle_command(msg)
        assert ack.success is False
        assert ack.error == "device offline"

    @pytest.mark.asyncio
    async def test_exception_returns_error_ack(self):
        commander = MockCommander()
        # Force an exception
        commander.send_command = AsyncMock(side_effect=ConnectionError("refused"))
        forwarder = CommandForwarder(commander)
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        ack = await forwarder.handle_command(msg)
        assert ack.success is False
        assert "refused" in ack.error

    @pytest.mark.asyncio
    async def test_commands_sent_counter(self, forwarder, commander):
        assert forwarder.commands_sent == 0
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        await forwarder.handle_command(msg)
        assert forwarder.commands_sent == 1
        await forwarder.handle_command(msg)
        assert forwarder.commands_sent == 2

    @pytest.mark.asyncio
    async def test_commands_acked_counter(self, forwarder, commander):
        assert forwarder.commands_acked == 0
        msg = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        await forwarder.handle_command(msg)
        assert forwarder.commands_acked == 1

    @pytest.mark.asyncio
    async def test_rtt_tracking(self, forwarder, commander):
        assert forwarder.last_command_rtt_ms is None
        msg = HIDCommandMessage(
            action="click",
            params={"x": 0, "y": 0},
            dispatched_at_backend=1.0,
        )
        await forwarder.handle_command(msg)
        assert forwarder.last_command_rtt_ms is not None
        assert forwarder.last_command_rtt_ms >= 0

    @pytest.mark.asyncio
    async def test_command_id_propagated(self, forwarder, commander):
        msg = GameCommandMessage(
            command_type="gamepad",
            action="button_press",
            params={"button": "B"},
            command_id="test-id-123",
            command_sequence=42,
        )
        ack = await forwarder.handle_command(msg)
        assert ack.command_id == "test-id-123"
        assert ack.command_sequence == 42

    @pytest.mark.asyncio
    async def test_commander_property(self, forwarder, commander):
        assert forwarder.commander is commander


class TestBackwardCompatibility:
    def test_esp32_forwarder_alias(self):
        """ESP32Forwarder is a backward-compatible alias for CommandForwarder."""
        assert ESP32Forwarder is CommandForwarder
