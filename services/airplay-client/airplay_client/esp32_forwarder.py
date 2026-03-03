"""Receives commands from the backend WebSocket and forwards via Commander."""

import logging
import time
from uuid import uuid4

from airplay_client.commander.base import Commander
from shared.messages import CommandAck, GameCommandMessage, HIDCommandMessage

logger = logging.getLogger(__name__)


class CommandForwarder:
    """Routes backend commands through the pluggable Commander interface.

    Handles both HIDCommandMessage (legacy ESP32 path) and GameCommandMessage
    (universal commander path). Both produce a CommandAck for the backend.
    """

    def __init__(self, commander: Commander) -> None:
        self._commander = commander
        self._commands_sent = 0
        self._commands_acked = 0
        self._last_command_rtt_ms: float | None = None

    @property
    def commander(self) -> Commander:
        return self._commander

    @property
    def commands_sent(self) -> int:
        return self._commands_sent

    @property
    def commands_acked(self) -> int:
        return self._commands_acked

    @property
    def last_command_rtt_ms(self) -> float | None:
        return self._last_command_rtt_ms

    async def handle_command(self, msg: HIDCommandMessage | GameCommandMessage) -> CommandAck:
        """Forward a command to the target via Commander."""
        received_at = time.time()

        # Extract action/params from either message type
        if isinstance(msg, GameCommandMessage):
            action = msg.action
            params = dict(msg.params)
            command_id = msg.command_id or str(uuid4())
        else:
            action = msg.action
            params = dict(msg.params)
            command_id = msg.command_id or str(uuid4())

        self._commands_sent += 1

        try:
            result = await self._commander.send_command(action, params)
            completed_at = time.time()
            self._commands_acked += 1

            dispatched_at = getattr(msg, "dispatched_at_backend", None)
            if dispatched_at is not None:
                self._last_command_rtt_ms = max(0.0, (completed_at - dispatched_at) * 1000)

            logger.debug(
                "%s executed %s: success=%s",
                self._commander.commander_name,
                action,
                result.success,
            )
            return CommandAck(
                command_id=command_id,
                command_sequence=msg.command_sequence,
                received_at_client=received_at,
                forwarded_at_client=result.forwarded_at,
                completed_at_client=completed_at,
                success=result.success,
                error=result.error,
            )
        except Exception as e:
            completed_at = time.time()
            self._commands_acked += 1
            logger.error("Failed to forward command via %s: %s", self._commander.commander_name, e)
            return CommandAck(
                command_id=command_id,
                command_sequence=msg.command_sequence,
                received_at_client=received_at,
                forwarded_at_client=None,
                completed_at_client=completed_at,
                success=False,
                error=str(e),
            )


# Backward-compatible alias
ESP32Forwarder = CommandForwarder
