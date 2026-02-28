#!/usr/bin/env python3
"""ChromaCatch-Go HID Mouse Test Script.

Sends HID commands through the backend REST API to verify the full
command pipeline: script -> backend -> WebSocket -> client -> ESP32 -> iPhone.

Usage:
    python scripts/test_mouse.py --backend-url http://localhost:8000
    python scripts/test_mouse.py --backend-url https://your-backend.run.app --api-key SECRET
"""

import argparse
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from shared.constants import make_auth_headers


def send_command(client: httpx.Client, action: str, params: dict, client_id: str | None = None) -> None:
    """Send a single HID command through the backend."""
    payload: dict = {"action": action, "params": params}
    if client_id:
        payload["client_id"] = client_id

    print(f"  Sending: {action} {params} ... ", end="", flush=True)
    try:
        resp = client.post("/command", json=payload)
        if resp.status_code == 200:
            print("OK")
        else:
            print(f"FAILED ({resp.status_code}: {resp.text})")
    except httpx.ConnectError as e:
        print(f"FAILED (connection error: {e})")


def check_backend(client: httpx.Client) -> bool:
    """Verify the backend is reachable and a client is connected."""
    print("--- Checking Backend ---")
    try:
        resp = client.get("/health")
        data = resp.json()
        print(f"  Health: {data.get('status', '?')}")
    except Exception as e:
        print(f"  Cannot reach backend: {e}")
        return False

    resp = client.get("/status")
    data = resp.json()
    total = data.get("total_clients", 0)
    clients = data.get("connected_clients", [])
    print(f"  Connected clients: {total}")

    if total == 0:
        print("\n  No clients connected! Start the airplay client first.")
        print("  Run: chromacatch-client run --backend-url ws://<host>:8000/ws/client")
        return False

    cid = clients[0]
    try:
        resp = client.get(f"/clients/{cid}/status")
        cdata = resp.json()
        esp32_reachable = cdata.get("esp32_reachable", False)
        esp32_ble = cdata.get("esp32_ble_connected")

        print(f"  Client {cid}:")
        print(f"    ESP32 reachable: {esp32_reachable}")
        print(f"    ESP32 BLE connected: {esp32_ble}")

        if not esp32_reachable:
            print("\n  WARNING: ESP32 is not reachable from the client.")
        if esp32_ble is not True:
            print("  WARNING: ESP32 BLE is not connected to iPhone.")
            print("  Pair 'ChromaCatch Mouse' in iPhone Bluetooth settings first.")
    except Exception:
        print(f"  Client {cid}: no status received yet")

    return True


def run_test_sequence(client: httpx.Client, delay: float) -> None:
    """Run a sequence of mouse movements to verify HID works."""
    print("\n--- Mouse Test Sequence ---")
    print(f"  (Pausing {delay}s between commands)\n")

    steps = [
        ("Step 1: Move right +50", "move", {"dx": 50, "dy": 0}),
        ("Step 2: Move down +50", "move", {"dx": 0, "dy": 50}),
        ("Step 3: Move left -50", "move", {"dx": -50, "dy": 0}),
        ("Step 4: Move up -50", "move", {"dx": 0, "dy": -50}),
        ("Step 5: Click", "click", {"x": 0, "y": 0}),
        ("Step 6: Move diagonal +30, +30", "move", {"dx": 30, "dy": 30}),
        ("Step 7: Swipe right", "swipe", {"x1": 0, "y1": 0, "x2": 100, "y2": 0, "duration_ms": 300}),
        ("Step 8: Move back left -100", "move", {"dx": -100, "dy": 0}),
        ("Step 9: Press and release", "press", {}),
    ]

    for label, action, params in steps:
        print(f"\n{label}")
        send_command(client, action, params)
        if action == "press":
            time.sleep(0.3)
            print("  (releasing)")
            send_command(client, "release", {})
        time.sleep(delay)

    print("\n--- Test Complete ---")
    print("If the cursor moved on your iPhone, the HID pipeline is working!")


def main():
    parser = argparse.ArgumentParser(description="Test HID mouse commands through the ChromaCatch-Go backend")
    parser.add_argument("--backend-url", default="http://localhost:8000", help="Backend HTTP URL (default: http://localhost:8000)")
    parser.add_argument("--api-key", default="", help="API key for backend authentication")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between commands in seconds (default: 1.0)")
    args = parser.parse_args()

    headers = make_auth_headers(args.api_key)

    with httpx.Client(base_url=args.backend_url, headers=headers, timeout=10.0) as client:
        if not check_backend(client):
            sys.exit(1)

        input("\nPress Enter to start the mouse test sequence...")
        run_test_sequence(client, args.delay)


if __name__ == "__main__":
    main()
