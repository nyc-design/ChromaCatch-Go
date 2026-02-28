"""FastAPI application for ChromaCatch-Go remote backend."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.config import backend_settings
from backend.session_manager import SessionManager
from backend.ws_handler import WebSocketHandler
from shared.frame_codec import encode_frame
from shared.constants import setup_logging
from shared.messages import HIDCommandMessage

setup_logging()
logger = logging.getLogger(__name__)

session_manager = SessionManager()
ws_handler = WebSocketHandler(session_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ChromaCatch-Go backend starting")
    yield
    logger.info("ChromaCatch-Go backend shutting down")


app = FastAPI(
    title="ChromaCatch-Go Backend",
    description="Remote backend for shiny hunting automation",
    version="0.2.0",
    lifespan=lifespan,
)


# --- WebSocket Endpoint --- $TODO: REORG: move to separate router file


@app.websocket("/ws/client")
async def websocket_endpoint(websocket: WebSocket, api_key: str = Query(default=None)):
    """Client WebSocket connection for frame upload and command dispatch."""
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    await ws_handler.handle_connection(websocket, api_key=token)


# --- REST Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok", "role": "backend"}


class BackendStatus(BaseModel):
    connected_clients: list[str]
    total_clients: int


@app.get("/status", response_model=BackendStatus)
async def get_status():
    clients = session_manager.connected_clients
    return BackendStatus(connected_clients=clients, total_clients=len(clients))


class SendCommandRequest(BaseModel):
    client_id: str | None = None
    action: str
    params: dict[str, int | float] = {}


@app.post("/command")
async def send_command(req: SendCommandRequest):
    """Send a HID command to a connected client (for manual/debug use)."""
    cmd = HIDCommandMessage(action=req.action, params=req.params)
    try:
        if req.client_id:
            await session_manager.send_command(req.client_id, cmd)
        else:
            await session_manager.broadcast_command(cmd)
        return {"status": "sent", "action": req.action}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/clients/{client_id}/status")
async def get_client_status(client_id: str):
    """Get the latest status from a connected client."""
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if session.last_status:
        return session.last_status.model_dump()
    return {"detail": "No status received yet"}


@app.get("/clients/{client_id}/frame")
async def get_latest_frame(client_id: str):
    """Get the latest frame as JPEG (for debug viewing)."""
    frame = session_manager.get_latest_frame(client_id)
    if frame is None:
        raise HTTPException(status_code=404, detail="No frame available")
    jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


# --- MJPEG Stream + Dashboard --- #TODO: REORG: move to separate file


async def _mjpeg_generator(client_id: str):
    """Yield JPEG frames as a multipart MJPEG stream."""
    while True:
        session = session_manager.get_session(client_id)
        if session is None:
            return
        frame = session.latest_frame
        if frame is not None:
            jpeg_bytes, _, _ = encode_frame(frame, quality=70, max_dimension=0)
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
                + jpeg_bytes + b"\r\n"
            )
        await asyncio.sleep(0.2)


@app.get("/stream/{client_id}")
async def stream_frames(client_id: str):
    """MJPEG stream of frames from a connected client."""
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    return StreamingResponse(
        _mjpeg_generator(client_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>ChromaCatch-Go Dashboard</title>
    <meta charset="utf-8">
    <style>
        body { font-family: -apple-system, sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }
        h1 { color: #e94560; }
        .client { border: 1px solid #333; border-radius: 8px; padding: 16px; margin: 16px 0; background: #16213e; }
        .client h2 { margin-top: 0; color: #4ecca3; }
        .status { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; margin: 8px 0; }
        .status-item { background: #0f3460; padding: 8px 12px; border-radius: 4px; }
        .status-item .label { font-size: 0.8em; color: #888; }
        .status-item .value { font-size: 1.1em; font-weight: bold; }
        .ok { color: #4ecca3; }
        .fail { color: #e94560; }
        .stream { max-width: 100%; border-radius: 4px; background: #000; }
        .no-clients { color: #888; font-style: italic; padding: 40px; text-align: center; }
    </style>
</head>
<body>
    <h1>ChromaCatch-Go</h1>
    <div id="clients"><div class="no-clients">Loading...</div></div>
    <script>
        async function refresh() {
            try {
                const resp = await fetch('/status');
                const data = await resp.json();
                const container = document.getElementById('clients');
                if (data.total_clients === 0) {
                    container.innerHTML = '<div class="no-clients">No clients connected. Start the airplay client to begin.</div>';
                    return;
                }
                let html = '';
                for (const clientId of data.connected_clients) {
                    let statusHtml = '';
                    try {
                        const sResp = await fetch('/clients/' + clientId + '/status');
                        const s = await sResp.json();
                        if (s.airplay_running !== undefined) {
                            statusHtml = `
                                <div class="status-item"><span class="label">AirPlay</span><br>
                                    <span class="value ${s.airplay_running ? 'ok' : 'fail'}">${s.airplay_running ? 'Running' : 'Stopped'}</span></div>
                                <div class="status-item"><span class="label">ESP32</span><br>
                                    <span class="value ${s.esp32_reachable ? 'ok' : 'fail'}">${s.esp32_reachable ? 'Reachable' : 'Unreachable'}</span></div>
                                <div class="status-item"><span class="label">ESP32 BLE</span><br>
                                    <span class="value ${s.esp32_ble_connected ? 'ok' : 'fail'}">${s.esp32_ble_connected ? 'Connected' : 'Disconnected'}</span></div>
                                <div class="status-item"><span class="label">Frames Sent</span><br>
                                    <span class="value">${s.frames_sent || 0}</span></div>
                                <div class="status-item"><span class="label">Uptime</span><br>
                                    <span class="value">${Math.floor((s.uptime_seconds || 0) / 60)}m ${Math.floor((s.uptime_seconds || 0) % 60)}s</span></div>`;
                        } else {
                            statusHtml = '<div class="status-item"><span class="label">Status</span><br><span class="value">Waiting for report...</span></div>';
                        }
                    } catch(e) {
                        statusHtml = '<div class="status-item"><span class="label">Status</span><br><span class="value fail">Error loading</span></div>';
                    }
                    html += `
                        <div class="client">
                            <h2>Client: ${clientId}</h2>
                            <div class="status">${statusHtml}</div>
                            <h3>Live Frame Stream</h3>
                            <img class="stream" src="/stream/${clientId}" alt="Waiting for frames..." width="640">
                        </div>`;
                }
                container.innerHTML = html;
            } catch(e) {
                document.getElementById('clients').innerHTML = '<div class="no-clients">Error: ' + e.message + '</div>';
            }
        }
        refresh();
        setInterval(refresh, 10000);
    </script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Browser dashboard showing connected clients and their frame streams."""
    return HTMLResponse(content=DASHBOARD_HTML)
