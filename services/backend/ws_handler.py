"""WebSocket handler for client connections."""

import logging
import time
import uuid

from fastapi import WebSocket, WebSocketDisconnect

from backend.config import backend_settings
from backend.session_manager import ClientSession, SessionManager
from shared.frame_codec import decode_frame
from shared.messages import ClientStatus, FrameMetadata, HeartbeatPing, HeartbeatPong, parse_message

logger = logging.getLogger(__name__)


class WebSocketHandler:
    """Handles the WebSocket protocol for client connections."""

    def __init__(self, session_manager: SessionManager) -> None:
        self._session_manager = session_manager

    async def handle_connection(self, websocket: WebSocket, api_key: str | None = None) -> None:
        """Main handler for a client WebSocket connection."""
        if backend_settings.api_key and api_key != backend_settings.api_key:
            await websocket.close(code=4001, reason="Invalid API key")
            return

        await websocket.accept()
        client_id = str(uuid.uuid4())[:8]
        session = await self._session_manager.register(client_id, websocket)
        logger.info("Client %s connected", client_id)

        try:
            await self._message_loop(client_id, session, websocket)
        except WebSocketDisconnect:
            logger.info("Client %s disconnected", client_id)
        except Exception as e:
            logger.error("Client %s error: %s", client_id, e)
        finally:
            await self._session_manager.unregister(client_id)

    async def _message_loop(self, client_id: str, session: ClientSession, websocket: WebSocket) -> None:
        """Process incoming messages from the client."""
        expecting_frame_data: FrameMetadata | None = None

        while True:
            message = await websocket.receive()

            if "text" in message:
                raw = message["text"]
                try:
                    msg = parse_message(raw)
                except Exception:
                    logger.error("Invalid message from %s: %s", client_id, raw[:200])
                    continue

                if isinstance(msg, FrameMetadata):
                    expecting_frame_data = msg

                elif isinstance(msg, ClientStatus):
                    session.last_status = msg
                    logger.debug(
                        "Client %s status: airplay=%s, esp32=%s",
                        client_id,
                        msg.airplay_running,
                        msg.esp32_reachable,
                    )

                elif isinstance(msg, HeartbeatPing):
                    await websocket.send_text(HeartbeatPong().model_dump_json())

                else:
                    logger.debug("Unhandled message type from %s: %s", client_id, msg.type)

            elif "bytes" in message:
                jpeg_bytes = message["bytes"]

                if expecting_frame_data is None:
                    logger.warning(
                        "Received binary data without frame metadata from %s", client_id
                    )
                    continue

                if len(jpeg_bytes) > backend_settings.max_frame_bytes:
                    logger.warning(
                        "Frame too large from %s: %d bytes", client_id, len(jpeg_bytes)
                    )
                    expecting_frame_data = None
                    continue

                try:
                    frame = decode_frame(jpeg_bytes)
                    session.latest_frame = frame
                    session.frames_received += 1
                    session.last_frame_at = time.time()

                    latency_ms = (
                        time.time() - expecting_frame_data.capture_timestamp
                    ) * 1000
                    logger.debug(
                        "Frame #%d from %s: %dx%d, latency=%.0fms",
                        expecting_frame_data.sequence,
                        client_id,
                        frame.shape[1],
                        frame.shape[0],
                        latency_ms,
                    )
                except Exception as e:
                    logger.error("Failed to decode frame from %s: %s", client_id, e)

                expecting_frame_data = None
