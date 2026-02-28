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
    parser.add_argument("--api-key", help="API key for backend auth (overrides CC_CLIENT_API_KEY)")
    parser.add_argument("--esp32-host", help="ESP32 IP address (overrides CC_CLIENT_ESP32_HOST)")
    parser.add_argument("--esp32-port", type=int, help="ESP32 HTTP port (overrides CC_CLIENT_ESP32_PORT)")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("connect", help="Test connectivity to backend and ESP32")
    subparsers.add_parser("run", help="Start the full AirPlay client")

    return parser


def apply_cli_overrides(args: argparse.Namespace) -> None:
    """Apply CLI args to the global client_settings singleton."""
    from airplay_client.config import client_settings

    if args.backend_url:
        client_settings.backend_ws_url = args.backend_url
    if args.api_key:
        client_settings.api_key = args.api_key
    if args.esp32_host:
        client_settings.esp32_host = args.esp32_host
    if args.esp32_port is not None:
        client_settings.esp32_port = args.esp32_port


async def cmd_connect(args: argparse.Namespace) -> None:
    """Test connectivity to backend and optionally ESP32."""
    from airplay_client.config import client_settings

    ws_url = client_settings.backend_ws_url
    # Derive HTTP URL from WebSocket URL for health check
    base_url = ws_url.replace("ws://", "http://").replace("wss://", "https://")
    if "/ws/" in base_url:
        base_url = base_url.rsplit("/ws/", 1)[0]

    print(f"Backend WS URL: {ws_url}")
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


def main():
    parser = build_parser()
    args = parser.parse_args()
    apply_cli_overrides(args)

    if args.command == "connect":
        asyncio.run(cmd_connect(args))
    elif args.command == "run":
        cmd_run(args)


if __name__ == "__main__":
    main()
