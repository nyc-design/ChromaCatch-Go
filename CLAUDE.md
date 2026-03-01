# ChromaCatch-Go

Automated shiny hunting bot for Pokemon Go using AirPlay screen mirroring, computer vision, and Bluetooth HID emulation.

## Architecture Overview

```
 [Local Network]                                    [Cloud / Remote]
┌──────────┐  AirPlay   ┌──────────────────────┐        ┌───────────────────┐
│  iPhone   │ ────────► │   Local Client        │        │  Remote Backend   │
│(Pkmn Go)  │           │                      │  WS    │                   │
└──────────┘           │  UxPlay → FrameCapture ├──────►│  FastAPI + CV     │
      ▲                 │                      │ frames │                   │
      │ BLE HID         │  ESP32Forwarder ◄────┤◄──────┤  Orchestrator     │
      │                 └──────────┬───────────┘  cmds  └───────────────────┘
      │                            │ HTTP
┌─────┴──────┐◄────────────────────┘
│   ESP32    │
│ (BLE HID)  │
└────────────┘
```

### Two-Service Architecture
- **Airplay Client** (`services/airplay-client/`): Runs on the same network as iPhone + ESP32. Manages AirPlay reception, captures frames, forwards them over WebSocket to the backend, and relays HID commands from the backend to the ESP32. Deployed as a CLI tool.
- **Remote Backend** (`services/backend/`): Runs in the cloud (Cloud Run, VM, etc.). Receives frames, runs CV analysis, makes decisions, and sends HID commands back through the WebSocket.
- **ESP32 Firmware** (`services/esp32/`): Dumb HID device. Receives mouse commands over HTTP, emits BLE HID events.
- **Shared** (`services/shared/`): Protocol contract between services — message models, frame codec, constants.

### Data Flow
1. iPhone runs Pokemon Go, screen mirrors via AirPlay to UxPlay (on local client)
2. UxPlay decrypts H.264 stream, forwards as RTP over localhost UDP
3. Local client captures frames via a unified source layer (`airplay` / `capture` / `screen`), JPEG-encodes (720px, q65 default), sends over WebSocket
4. Local client captures audio via a unified audio layer (`auto` / `airplay` / `system`), sends PCM chunks over WebSocket
5. Remote backend decodes frames, runs CV pipeline, decides next action
6. Backend sends HID command over WebSocket to local client
7. Local client forwards command to ESP32 via HTTP
8. ESP32 emits BLE HID mouse event to iPhone

### WebSocket Protocol
- **Frames (client → backend)**: Two-message pattern — JSON `FrameMetadata` followed by binary JPEG bytes
- **Commands (backend → client)**: JSON `HIDCommandMessage` with action + params (+ command id/sequence)
- **Command ACKs (client → backend)**: JSON `CommandAck` after ESP32 forward attempt
- **Audio (client → backend)**: JSON `AudioChunk` metadata + binary PCM payload
- **Status (client → backend)**: Periodic JSON `ClientStatus` updates
- **Channel split**:
  - `/ws/client` = frame/status channel
  - `/ws/control` = low-jitter command/ack channel (with backend fallback to frame channel)
- **Auth**: API key via query param or Authorization header

## Project Structure

```
ChromaCatch-Go/
├── CLAUDE.md
├── pyproject.toml
├── conftest.py                              # Root conftest (sets up sys.path for services/)
├── services/
│   ├── shared/                              # Protocol contract between services
│   │   ├── constants.py                     # Message types, defaults
│   │   ├── messages.py                      # Pydantic models for all WS messages
│   │   └── frame_codec.py                  # JPEG encode/decode with resize
│   ├── backend/                             # REMOTE: CV brain + command dispatch
│   │   ├── config.py                        # BackendSettings (CC_BACKEND_ prefix)
│   │   ├── main.py                          # FastAPI + WS endpoints + dashboard
│   │   ├── ws_handler.py                    # WebSocket frame/control handler
│   │   ├── session_manager.py               # Dual-channel client session tracking
│   │   ├── cv/                              # Phase 2: computer vision
│   │   ├── orchestrator/                    # Phase 3: state machine
│   │   └── tests/                           # Backend tests (81 tests)
│   │       ├── test_backend_api.py
│   │       ├── test_session_manager.py
│   │       ├── test_ws_handler.py
│   │       ├── test_messages.py             # Shared protocol tests
│   │       ├── test_frame_codec.py          # Shared codec tests
│   │       └── integration/
│   │           └── test_client_backend.py   # End-to-end round-trip
│   ├── airplay-client/                      # LOCAL: AirPlay + ESP32 bridge (CLI tool)
│   │   ├── airplay_client/                  # Python package (airplay_client)
│   │   │   ├── cli.py                        # CLI entry point (connect, run)
│   │   │   ├── config.py                    # ClientSettings (CC_CLIENT_ prefix)
│   │   │   ├── main.py                      # asyncio entrypoint (dual WS + source factory)
│   │   │   ├── audio/
│   │   │   │   ├── factory.py               # Runtime audio source selection
│   │   │   │   ├── airplay_audio_source.py  # AirPlay RTP audio source adapter
│   │   │   │   └── ffmpeg_audio_source.py   # System/capture-device audio adapter
│   │   │   ├── ws_client.py                 # WebSocket client (auto-reconnect + command ack)
│   │   │   ├── esp32_forwarder.py           # WS command → ESP32 HTTP bridge (+ ack timing)
│   │   │   ├── capture/
│   │   │   │   ├── airplay_manager.py       # UxPlay process management
│   │   │   │   ├── frame_capture.py         # OpenCV GStreamer/FFmpeg frame capture
│   │   │   │   └── audio_capture.py         # AirPlay RTP audio capture (PCM chunks)
│   │   │   ├── sources/
│   │   │   │   ├── airplay_source.py        # AirPlay/UxPlay source adapter
│   │   │   │   ├── capture_card_source.py   # USB capture/camera source adapter
│   │   │   │   ├── screen_source.py         # Desktop/window capture source adapter
│   │   │   │   └── factory.py               # Runtime source selection
│   │   │   └── commander/
│   │   │       └── esp32_client.py          # HTTP client for ESP32 commands
│   │   └── tests/                           # Client tests (78 tests)
│   │       ├── test_airplay_manager.py
│   │       ├── test_esp32_client.py
│   │       ├── test_esp32_forwarder.py
│   │       ├── test_frame_capture.py
│   │       ├── test_ws_client.py
│   │       └── test_cli.py
│   └── esp32/                               # ESP32 firmware
│       ├── platformio.ini
│       └── src/main.cpp                     # BLE HID + WiFi HTTP server
└── scripts/
    ├── start.sh                             # Dev: run both services locally
    ├── start_client.sh                      # Launch client locally
    ├── start_backend.sh                     # Launch backend
    └── test_mouse.py                        # HID mouse movement test
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11+, FastAPI, uvicorn, pydantic |
| Client-Backend transport | WebSocket (websockets library) |
| CV | OpenCV (with GStreamer support), numpy |
| Frame encoding | JPEG via OpenCV (720px max, quality 65 default) |
| AirPlay | UxPlay (C, installed separately) |
| Frame capture | OpenCV GStreamer pipeline from RTP/UDP |
| ESP32 firmware | C++ / Arduino / PlatformIO |
| ESP32 comms | WiFi HTTP (REST) |
| BLE HID | ESP32 BLE HID library (BleCombo) |
| Testing | pytest, pytest-asyncio (159 tests) |
| Linting | ruff, black, mypy |

## Phases

### Phase 1: Connectivity [COMPLETE]
- [x] ESP32 BLE HID firmware with WiFi command server
- [x] AirPlay receiver management (UxPlay wrapper)
- [x] Frame capture service (GStreamer RTP → OpenCV)
- [x] Shared protocol (messages, frame codec)
- [x] WebSocket client (local client → remote backend)
- [x] WebSocket server (backend receives frames, dispatches commands)
- [x] ESP32 command forwarder (backend → client → ESP32)
- [x] Integration tests (159 total)
- [x] CLI tool packaging (pip-installable `chromacatch-client`)
- [x] Backend live dashboard with MJPEG frame streaming
- [x] HID mouse test script
- [x] GStreamer CLI capture stability fixes (single long-lived pipeline + reliable resolution detection from caps)
- [x] Dual WebSocket channels (`/ws/client`, `/ws/control`) with command sequencing + ACK telemetry
- [x] Unified client frame source layer (AirPlay, capture card, desktop capture)
- [x] Initial AirPlay audio transport (`UxPlay -artp` + backend audio chunk ingestion)

### Phase 2: Computer Vision
- [ ] Screen state detection (battle, overworld, menu, etc.)
- [ ] Pokemon encounter detection
- [ ] Shiny detection (color comparison techniques)
- [ ] UI element recognition (buttons, prompts)

### Phase 3: Shiny Hunt Automation
- [ ] Hunt loop state machine
- [ ] Action sequences (encounter → check → flee/catch)
- [ ] Error recovery (crash detection, reconnection)
- [ ] Logging and statistics

## Development Commands

```bash
# Install
poetry install

# Run all tests (159 tests)
poetry run pytest

# Run by suite
poetry run pytest services/backend/tests/              # Backend + shared + integration
poetry run pytest services/airplay-client/tests/        # Client component tests
poetry run pytest services/backend/tests/integration/   # End-to-end round-trip only

# Start backend (cloud/remote)
./scripts/start_backend.sh
# Dashboard: http://localhost:8000/dashboard

# Start client (local, near iPhone + ESP32)
./scripts/start_client.sh

# Start both (dev mode)
./scripts/start.sh

# Install client as CLI tool (on MacBook)
pip install ./services/airplay-client
chromacatch-client connect --backend-url ws://<host>:8000/ws/client
chromacatch-client run --backend-url ws://<host>:8000/ws/client

# Test HID mouse commands
python scripts/test_mouse.py --backend-url http://<host>:8000

# ESP32 Firmware (requires PlatformIO)
cd services/esp32 && pio run              # Build
cd services/esp32 && pio run -t upload    # Flash
```

## Configuration

**Client** (`.env` with `CC_CLIENT_` prefix):
```bash
CC_CLIENT_BACKEND_WS_URL=wss://your-backend.run.app/ws/client
CC_CLIENT_BACKEND_CONTROL_WS_URL=wss://your-backend.run.app/ws/control
CC_CLIENT_CLIENT_ID=macbook-station-1
CC_CLIENT_API_KEY=your-secret-key
CC_CLIENT_ESP32_HOST=192.168.1.100
CC_CLIENT_ESP32_PORT=80
CC_CLIENT_AIRPLAY_UDP_PORT=5000
CC_CLIENT_AIRPLAY_AUDIO_UDP_PORT=5002
CC_CLIENT_AIRPLAY_NAME=ChromaCatch
CC_CLIENT_CLEANUP_STALE_AIRPLAY_PROCESSES=true
CC_CLIENT_CAPTURE_SOURCE=airplay            # airplay | capture | screen
CC_CLIENT_CAPTURE_DEVICE=0                  # device index/path for capture source
CC_CLIENT_CAPTURE_FPS=30
CC_CLIENT_SCREEN_MONITOR=1                  # used by screen source
CC_CLIENT_SCREEN_REGION=                    # optional x,y,width,height
CC_CLIENT_AIRPLAY_RECONNECT_TIMEOUT_S=8.0  # restart capture if stream stalls post-connect
CC_CLIENT_JPEG_QUALITY=65
CC_CLIENT_MAX_DIMENSION=720
CC_CLIENT_FRAME_INTERVAL_MS=33
CC_CLIENT_AUDIO_ENABLED=true
CC_CLIENT_AUDIO_SOURCE=auto                 # auto | airplay | system | none
CC_CLIENT_AUDIO_SAMPLE_RATE=44100
CC_CLIENT_AUDIO_CHANNELS=2
CC_CLIENT_AUDIO_CHUNK_MS=100
CC_CLIENT_AUDIO_INPUT_BACKEND=auto          # auto | avfoundation | pulse | dshow
CC_CLIENT_AUDIO_INPUT_DEVICE=               # backend-specific input selector
```

**Backend** (`.env` with `CC_BACKEND_` prefix):
```bash
CC_BACKEND_API_KEY=your-secret-key
CC_BACKEND_HOST=0.0.0.0
CC_BACKEND_PORT=8000
CC_BACKEND_MAX_FRAME_BYTES=500000
```

## Backend API

| Method | Endpoint | Description |
|--------|----------|-------------|
| WS | `/ws/client` | Frame/status WebSocket (frames up, command fallback) |
| WS | `/ws/control` | Low-latency command/ack WebSocket |
| GET | `/health` | Health check |
| GET | `/status` | Connected clients count |
| POST | `/command` | Send HID command to client(s) |
| GET | `/clients/{id}/status` | Get client's latest status |
| GET | `/clients/{id}/frame` | Get latest frame as JPEG |
| GET | `/clients/{id}/audio` | Get latest audio chunk as WAV snippet |
| GET | `/stream/{id}` | MJPEG live frame stream |
| GET | `/dashboard` | Browser dashboard with status + streams |
