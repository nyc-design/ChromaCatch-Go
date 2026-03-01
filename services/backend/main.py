"""FastAPI application for ChromaCatch-Go remote backend."""

import asyncio
import io
import logging
import wave
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel

from backend.config import backend_settings
from backend.mediamtx_manager import MediaMTXManager
from backend.rtsp_consumer import RTSPFrameConsumer
from backend.session_manager import SessionManager
from backend.ws_handler import WebSocketHandler
from shared.frame_codec import encode_frame
from shared.constants import setup_logging
from shared.messages import HIDCommandMessage

setup_logging()
logger = logging.getLogger(__name__)

session_manager = SessionManager()
ws_handler = WebSocketHandler(session_manager)
mediamtx_manager = MediaMTXManager()
rtsp_consumer = RTSPFrameConsumer(session_manager)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ChromaCatch-Go backend starting")
    # Start MediaMTX if enabled
    mediamtx_manager.start()
    yield
    # Shutdown
    await rtsp_consumer.stop()
    mediamtx_manager.stop()
    logger.info("ChromaCatch-Go backend shutting down")


app = FastAPI(
    title="ChromaCatch-Go Backend",
    description="Remote backend for shiny hunting automation",
    version="0.2.0",
    lifespan=lifespan,
)


# --- WebSocket Endpoint --- $TODO: REORG: move to separate router file


@app.websocket("/ws/client")
async def websocket_endpoint(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Frame/status channel from client to backend."""
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="frame",
        client_id=client_id,
    )


@app.websocket("/ws/control")
async def websocket_control_endpoint(
    websocket: WebSocket,
    api_key: str = Query(default=None),
    client_id: str | None = Query(default=None),
):
    """Dedicated low-latency control channel (commands + acks)."""
    auth_header = websocket.headers.get("authorization", "")
    token = api_key or (
        auth_header.removeprefix("Bearer ").strip() if auth_header else None
    )
    await ws_handler.handle_connection(
        websocket,
        api_key=token,
        channel="control",
        client_id=client_id,
    )


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
            sent_cmd = await session_manager.send_command(req.client_id, cmd)
            return {
                "status": "sent",
                "action": req.action,
                "client_id": req.client_id,
                "command_id": sent_cmd.command_id,
                "command_sequence": sent_cmd.command_sequence,
            }
        else:
            sent = await session_manager.broadcast_command(cmd)
            return {
                "status": "sent",
                "action": req.action,
                "sent_to": len(sent),
            }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/clients/{client_id}/status")
async def get_client_status(client_id: str):
    """Get the latest status from a connected client."""
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if session.last_status:
        payload = session.last_status.model_dump()
        payload["backend_commands_sent"] = session.commands_sent
        payload["backend_commands_acked"] = session.commands_acked
        payload["backend_last_command_rtt_ms"] = session.last_command_rtt_ms
        payload["backend_audio_chunks_received"] = session.audio_chunks_received
        payload["backend_frame_latency_ms"] = session.last_frame_latency_ms
        return payload
    return {"detail": "No status received yet"}


@app.post("/clients/{client_id}/rtsp-start")
async def start_rtsp_consumer(client_id: str, stream_path: str = Query(default=None)):
    """Start consuming RTSP frames for a client (called when SRT client connects)."""
    if not backend_settings.rtsp_consumer_enabled:
        raise HTTPException(status_code=503, detail="RTSP consumer not enabled")
    path = stream_path or f"chromacatch/{client_id}"
    await rtsp_consumer.add_stream(client_id, path)
    return {"status": "started", "client_id": client_id, "stream_path": path}


@app.get("/clients/{client_id}/frame")
async def get_latest_frame(client_id: str):
    """Get the latest frame as JPEG (for debug viewing)."""
    jpeg_bytes, _ = session_manager.get_latest_frame_jpeg(client_id)
    if jpeg_bytes is None:
        frame = session_manager.get_latest_frame(client_id)
        if frame is None:
            raise HTTPException(status_code=404, detail="No frame available")
        jpeg_bytes, _, _ = encode_frame(frame, quality=85, max_dimension=0)
    return Response(content=jpeg_bytes, media_type="image/jpeg")


def _pcm_chunk_to_wav(
    pcm_bytes: bytes,
    sample_rate: int,
    channels: int,
    sample_format: str,
) -> bytes:
    """Wrap raw PCM bytes in a WAV container for easy playback/debug."""
    if sample_format.lower() != "s16le":
        raise HTTPException(status_code=415, detail="Unsupported sample format")
    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(max(1, channels))
            wav.setsampwidth(2)  # s16le
            wav.setframerate(max(1, sample_rate))
            wav.writeframes(pcm_bytes)
        return buffer.getvalue()


@app.get("/clients/{client_id}/audio")
async def get_latest_audio_chunk(client_id: str):
    """Get latest audio chunk wrapped as a WAV snippet (debug)."""
    session = session_manager.get_session(client_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Client not found")
    if session.latest_audio_chunk is None:
        raise HTTPException(status_code=404, detail="No audio available")
    wav_bytes = _pcm_chunk_to_wav(
        pcm_bytes=session.latest_audio_chunk,
        sample_rate=session.latest_audio_sample_rate,
        channels=session.latest_audio_channels,
        sample_format=session.latest_audio_format,
    )
    return Response(content=wav_bytes, media_type="audio/wav")


# --- MJPEG Stream + Dashboard --- #TODO: REORG: move to separate file


async def _mjpeg_generator(client_id: str):
    """Yield JPEG frames as a multipart MJPEG stream."""
    last_sequence = -1
    while True:
        session = session_manager.get_session(client_id)
        if session is None:
            return
        jpeg_bytes, sequence = session_manager.get_latest_frame_jpeg(client_id)
        if jpeg_bytes is not None and sequence != last_sequence:
            last_sequence = sequence
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Content-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n"
                + jpeg_bytes + b"\r\n"
            )
        else:
            await asyncio.sleep(0.01)


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
        .stream-container { position: relative; display: inline-block; }
        .stream { max-width: 100%; border-radius: 4px; background: #000; }
        .stream-badge { position: absolute; top: 8px; right: 8px; padding: 3px 8px; border-radius: 4px;
                        font-size: 0.75em; font-weight: bold; color: #fff; }
        .badge-webrtc { background: #4ecca3; }
        .badge-mjpeg { background: #e9a345; }
        .no-clients { color: #888; font-style: italic; padding: 40px; text-align: center; }
    </style>
</head>
<body>
    <h1>ChromaCatch-Go</h1>
    <div id="clients"><div class="no-clients">Loading...</div></div>
    <script>
        const WEBRTC_PORT = """ + str(backend_settings.mediamtx_webrtc_port) + """;
        const MEDIAMTX_ENABLED = """ + str(backend_settings.mediamtx_enabled).lower() + """;

        function toggleMute(clientId) {
            const videoEl = document.getElementById('webrtc-' + clientId);
            const btn = document.getElementById('unmute-' + clientId);
            if (videoEl) {
                videoEl.muted = !videoEl.muted;
                btn.textContent = videoEl.muted ? 'Unmute Audio' : 'Mute Audio';
                btn.style.borderColor = videoEl.muted ? '#4ecca3' : '#e94560';
                btn.style.color = videoEl.muted ? '#4ecca3' : '#e94560';
            }
        }

        async function startWHEP(videoEl, badgeEl, clientId) {
            if (!MEDIAMTX_ENABLED) return false;
            const whepUrl = location.protocol + '//' + location.hostname + ':' + WEBRTC_PORT
                + '/chromacatch/' + clientId + '/whep';
            try {
                const pc = new RTCPeerConnection({
                    iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
                });
                pc.addTransceiver('video', { direction: 'recvonly' });
                pc.addTransceiver('audio', { direction: 'recvonly' });
                pc.ontrack = (ev) => {
                    if (ev.streams[0]) videoEl.srcObject = ev.streams[0];
                };
                const offer = await pc.createOffer();
                await pc.setLocalDescription(offer);
                const resp = await fetch(whepUrl, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: pc.localDescription.sdp,
                });
                if (!resp.ok) throw new Error('WHEP ' + resp.status);
                const answer = await resp.text();
                await pc.setRemoteDescription({ type: 'answer', sdp: answer });
                videoEl.style.display = 'block';
                badgeEl.textContent = 'WebRTC';
                badgeEl.className = 'stream-badge badge-webrtc';
                return true;
            } catch (e) {
                console.warn('WebRTC unavailable for ' + clientId + ':', e.message);
                return false;
            }
        }

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
                                <div class="status-item"><span class="label">Transport</span><br>
                                    <span class="value">${s.transport_mode || s.capture_source || 'websocket'}</span></div>
                                <div class="status-item"><span class="label">Control WS</span><br>
                                    <span class="value ${s.control_channel_connected ? 'ok' : 'fail'}">${s.control_channel_connected ? 'Connected' : 'Disconnected'}</span></div>
                                <div class="status-item"><span class="label">Cmd Ack RTT</span><br>
                                    <span class="value">${s.last_command_rtt_ms ? Math.round(s.last_command_rtt_ms) + ' ms' : 'n/a'}</span></div>
                                <div class="status-item"><span class="label">Audio Chunks</span><br>
                                    <span class="value">${s.audio_chunks_sent || 0}</span></div>
                                <div class="status-item"><span class="label">Audio Source</span><br>
                                    <span class="value">${s.audio_source || 'n/a'}</span></div>
                                <div class="status-item"><span class="label">Frame Latency</span><br>
                                    <span class="value">${s.backend_frame_latency_ms ? Math.round(s.backend_frame_latency_ms) + ' ms' : 'n/a'}</span></div>
                                ${s.srt_rtt_ms != null ? `
                                <div class="status-item"><span class="label">SRT RTT</span><br>
                                    <span class="value">${Math.round(s.srt_rtt_ms)} ms</span></div>
                                <div class="status-item"><span class="label">SRT Bandwidth</span><br>
                                    <span class="value">${s.srt_bandwidth_kbps ? Math.round(s.srt_bandwidth_kbps) + ' kbps' : 'n/a'}</span></div>
                                <div class="status-item"><span class="label">SRT Loss</span><br>
                                    <span class="value ${(s.srt_packet_loss_pct || 0) > 1 ? 'fail' : 'ok'}">${s.srt_packet_loss_pct != null ? s.srt_packet_loss_pct.toFixed(1) + '%' : 'n/a'}</span></div>` : ''}
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
                            <h3>Live Stream</h3>
                            <div class="stream-container">
                                <video id="webrtc-${clientId}" class="stream" autoplay muted playsinline width="640" style="display:none"></video>
                                <img id="mjpeg-${clientId}" class="stream" src="/stream/${clientId}" alt="Waiting for frames..." width="640">
                                <span id="badge-${clientId}" class="stream-badge badge-mjpeg">MJPEG</span>
                            </div>
                            <button id="unmute-${clientId}" style="display:none; margin-top:8px; padding:6px 16px; border:1px solid #4ecca3; background:transparent; color:#4ecca3; border-radius:4px; cursor:pointer;" onclick="toggleMute('${clientId}')">Unmute Audio</button>
                        </div>`;
                }
                container.innerHTML = html;
                // Try WebRTC for each client, hide MJPEG fallback on success
                for (const clientId of data.connected_clients) {
                    const videoEl = document.getElementById('webrtc-' + clientId);
                    const imgEl = document.getElementById('mjpeg-' + clientId);
                    const badgeEl = document.getElementById('badge-' + clientId);
                    if (videoEl && imgEl && badgeEl) {
                        startWHEP(videoEl, badgeEl, clientId).then(ok => {
                            if (ok) {
                                imgEl.style.display = 'none';
                                const unmuteBtn = document.getElementById('unmute-' + clientId);
                                if (unmuteBtn) unmuteBtn.style.display = 'inline-block';
                            }
                        });
                    }
                }
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
