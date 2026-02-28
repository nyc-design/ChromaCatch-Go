"""CLI entry point for ChromaCatch-Go airplay client."""

import argparse
import asyncio
import logging
import sys

import httpx

from shared.constants import make_auth_headers, setup_logging

setup_logging()
logger = logging.getLogger("chromacatch")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chromacatch-client",
        description="ChromaCatch-Go AirPlay client",
    )
    parser.add_argument("--backend-url", help="Backend WebSocket URL (overrides CC_CLIENT_BACKEND_WS_URL)")
    parser.add_argument("--backend-control-url", help="Backend control WebSocket URL (overrides CC_CLIENT_BACKEND_CONTROL_WS_URL)")
    parser.add_argument("--client-id", help="Stable client ID for WS pairing (overrides CC_CLIENT_CLIENT_ID)")
    parser.add_argument("--api-key", help="API key for backend auth (overrides CC_CLIENT_API_KEY)")
    parser.add_argument("--esp32-host", help="ESP32 IP address (overrides CC_CLIENT_ESP32_HOST)")
    parser.add_argument("--esp32-port", type=int, help="ESP32 HTTP port (overrides CC_CLIENT_ESP32_PORT)")
    parser.add_argument("--capture-source", choices=["airplay", "capture", "screen"], help="Capture source to use (overrides CC_CLIENT_CAPTURE_SOURCE)")
    parser.add_argument("--capture-device", help="Capture card/camera device index or path (overrides CC_CLIENT_CAPTURE_DEVICE)")
    parser.add_argument("--capture-fps", type=int, help="Capture FPS target (overrides CC_CLIENT_CAPTURE_FPS)")
    parser.add_argument("--audio-enabled", choices=["true", "false"], help="Enable/disable audio transport (overrides CC_CLIENT_AUDIO_ENABLED)")
    parser.add_argument("--audio-source", choices=["auto", "airplay", "system", "none"], help="Audio source mode (overrides CC_CLIENT_AUDIO_SOURCE)")
    parser.add_argument("--audio-rate", type=int, help="Audio sample rate (overrides CC_CLIENT_AUDIO_SAMPLE_RATE)")
    parser.add_argument("--audio-channels", type=int, help="Audio channels (overrides CC_CLIENT_AUDIO_CHANNELS)")
    parser.add_argument(
        "--audio-input-backend",
        choices=["auto", "avfoundation", "pulse", "dshow"],
        help="System audio input backend (overrides CC_CLIENT_AUDIO_INPUT_BACKEND)",
    )
    parser.add_argument("--audio-input-device", help="System audio input device selector (overrides CC_CLIENT_AUDIO_INPUT_DEVICE)")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging (shows UxPlay/FFmpeg output)")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connect", help="Test connectivity to backend and ESP32")
    subparsers.add_parser("run", help="Start the full AirPlay client")
    subparsers.add_parser("debug-capture", help="Debug: run only UxPlay + FFmpeg capture (no WS/ESP32)")

    return parser


def apply_cli_overrides(args: argparse.Namespace) -> None:
    """Apply CLI args to the global client_settings singleton."""
    from airplay_client.config import client_settings

    if args.backend_url:
        client_settings.backend_ws_url = args.backend_url
    if args.backend_control_url:
        client_settings.backend_control_ws_url = args.backend_control_url
    if args.client_id:
        client_settings.client_id = args.client_id
    if args.api_key:
        client_settings.api_key = args.api_key
    if args.esp32_host:
        client_settings.esp32_host = args.esp32_host
    if args.esp32_port is not None:
        client_settings.esp32_port = args.esp32_port
    if args.capture_source:
        client_settings.capture_source = args.capture_source
    if args.capture_device:
        client_settings.capture_device = args.capture_device
    if args.capture_fps is not None:
        client_settings.capture_fps = args.capture_fps
    if args.audio_enabled is not None:
        client_settings.audio_enabled = args.audio_enabled.lower() == "true"
    if args.audio_source is not None:
        client_settings.audio_source = args.audio_source
    if args.audio_rate is not None:
        client_settings.audio_sample_rate = args.audio_rate
    if args.audio_channels is not None:
        client_settings.audio_channels = args.audio_channels
    if args.audio_input_backend is not None:
        client_settings.audio_input_backend = args.audio_input_backend
    if args.audio_input_device is not None:
        client_settings.audio_input_device = args.audio_input_device


async def cmd_connect(args: argparse.Namespace) -> None:
    """Test connectivity to backend and optionally ESP32."""
    from airplay_client.config import client_settings

    ws_url = client_settings.backend_ws_url
    control_ws_url = (
        client_settings.backend_control_ws_url
        or ws_url.replace("/ws/client", "/ws/control")
    )
    # Derive HTTP URL from WebSocket URL for health check
    base_url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
    if "/ws/" in base_url:
        base_url = base_url.rsplit("/ws/", 1)[0]

    print(f"Backend WS URL: {ws_url}")
    print(f"Control WS URL: {control_ws_url}")
    print(f"Backend HTTP:   {base_url}")

    print(f"\n--- Backend Connectivity ---")
    try:
        headers = make_auth_headers(client_settings.api_key)
        async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=5.0) as http:
            resp = await http.get("/health")
            data = resp.json()
            print(f"  Health: {data['status']} (role: {data.get('role', '?')})")

            resp = await http.get("/status")
            data = resp.json()
            print(f"  Connected clients: {data['total_clients']}")

        print("  [OK] Backend is reachable")
    except Exception as e:
        print(f"  [FAIL] Cannot reach backend: {e}")
        return

    print(f"\n--- ESP32 Connectivity ---")
    print(f"  ESP32: {client_settings.esp32_host}:{client_settings.esp32_port}")
    try:
        from airplay_client.commander.esp32_client import ESP32Client

        esp = ESP32Client()
        if await esp.ping():
            status = await esp.status()
            ble = status.get("ble_connected", False)
            print(f"  BLE connected: {ble}")
            print(f"  Device: {status.get('device_name', '?')}")
            print(f"  IP: {status.get('ip', '?')}")
            if ble:
                print("  [OK] ESP32 is ready (BLE connected to iPhone)")
            else:
                print("  [OK] ESP32 is reachable (BLE not yet paired)")
        else:
            print("  [FAIL] ESP32 not responding to ping")
        await esp.close()
    except Exception as e:
        print(f"  [SKIP] Cannot reach ESP32: {e}")
        print("  (This is OK if ESP32 is not powered on yet)")

    print("\nDone.")


def cmd_run(args: argparse.Namespace) -> None:
    """Start the full AirPlay client pipeline."""
    from airplay_client.main import main as run_client

    run_client()


def cmd_debug_capture(args: argparse.Namespace) -> None:
    """Debug: run only UxPlay + frame capture, no WS or ESP32."""
    import time

    from airplay_client.capture.airplay_manager import AirPlayManager
    from airplay_client.capture.frame_capture import FrameCapture

    logging.getLogger().setLevel(logging.DEBUG)
    logging.getLogger("airplay_client").setLevel(logging.DEBUG)

    print("=== ChromaCatch Debug Capture ===")
    print("This runs ONLY UxPlay + frame capture (no backend/ESP32).")
    print()

    airplay = AirPlayManager()
    capture = FrameCapture()

    print(f"[1/3] Starting frame capture on UDP port {airplay.udp_port}...")
    print("  (Must listen BEFORE UxPlay — iPhone only sends SPS/PPS+IDR once)")
    capture.start()

    print(f"\n[2/3] Starting UxPlay: {' '.join(airplay.build_command())}")
    try:
        airplay.start()
        print(f"  UxPlay started (pid={airplay.pid})")
    except RuntimeError as e:
        print(f"  FAILED: {e}")
        capture.stop()
        return

    print(f"\n  **Connect your iPhone to AirPlay '{airplay.airplay_name}' now.**")
    print("  Keep the screen active (play a video, scroll, etc).\n")

    print("[3/3] Watching for frames (Ctrl+C to stop)...\n")
    frame_count = 0
    try:
        while True:
            frame = capture.get_frame(timeout=1.0)
            if frame is not None:
                frame_count += 1
                h, w = frame.shape[:2]
                print(f"  Frame #{frame_count}: {w}x{h}")
                if frame_count >= 5:
                    print(f"\n  SUCCESS: Received {frame_count} frames!")
                    break
            else:
                print(f"  ... no frame yet (UxPlay running={airplay.is_running}, "
                      f"capture running={capture.is_running})")
    except KeyboardInterrupt:
        print(f"\n  Stopped. Total frames received: {frame_count}")
    finally:
        capture.stop()
        airplay.stop()


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("airplay_client").setLevel(logging.DEBUG)

    apply_cli_overrides(args)

    if args.command == "connect":
        asyncio.run(cmd_connect(args))
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "debug-capture":
        cmd_debug_capture(args)


if __name__ == "__main__":
    main()
