"""ESP32 command client (WebSocket-first, HTTP fallback)."""

import asyncio
import json
import logging
import urllib.error
import urllib.request

import httpx
import websockets
from websockets.exceptions import WebSocketException

from airplay_client.config import client_settings as settings

logger = logging.getLogger(__name__)


class HIDCommand:
    """Represents a mouse HID command to send to the ESP32."""

    def __init__(self, action: str, **kwargs: int | float):
        self.action = action
        self.params = kwargs

    def to_dict(self) -> dict:
        return {"action": self.action, **self.params}

    @staticmethod
    def move(dx: int, dy: int) -> "HIDCommand":
        return HIDCommand("move", dx=dx, dy=dy)

    @staticmethod
    def click(x: int, y: int) -> "HIDCommand":
        return HIDCommand("click", x=x, y=y)

    @staticmethod
    def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> "HIDCommand":
        return HIDCommand("swipe", x1=x1, y1=y1, x2=x2, y2=y2, duration_ms=duration_ms)

    @staticmethod
    def press() -> "HIDCommand":
        return HIDCommand("press")

    @staticmethod
    def release() -> "HIDCommand":
        return HIDCommand("release")


class ESP32Client:
    """Sends HID commands to ESP32 over WiFi with WS-first transport."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        ws_port: int | None = None,
        timeout: float | None = None,
    ):
        self.host = host or settings.esp32_host
        self.port = port or settings.esp32_port
        self.ws_port = ws_port or settings.esp32_ws_port
        self.timeout = timeout or settings.esp32_timeout
        self._base_url = f"http://{self.host}:{self.port}"
        self._ws_url = f"ws://{self.host}:{self.ws_port}"
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self.timeout,
            trust_env=False,
            limits=limits,
        )
        self._ws = None
        self._ws_lock = asyncio.Lock()
        self._ws_seq = 0

    def _urllib_request(self, method: str, path: str, payload: dict | None = None) -> dict:
        """Fallback HTTP request using stdlib urllib (runs in thread)."""
        url = f"{self._base_url}{path}"
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=self.timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}

    async def send_command(self, command: HIDCommand) -> dict:
        """Send a HID command to the ESP32."""
        payload = command.to_dict()
        logger.debug("Sending command to ESP32 (WS preferred): %s", payload)
        try:
            return await self._send_command_ws(payload)
        except Exception as e:
            logger.debug("ESP32 WS command failed, falling back to HTTP: %s", e)
            return await self._send_command_http(payload)

    async def _send_command_ws(self, payload: dict) -> dict:
        async with self._ws_lock:
            ws = await self._ensure_ws()
            self._ws_seq += 1
            seq = self._ws_seq
            message = {"type": "command", "seq": seq, **payload}
            await ws.send(json.dumps(message))
            raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            result = json.loads(raw) if raw else {}
            if isinstance(result, dict) and result.get("seq") is not None and result.get("seq") != seq:
                logger.warning("ESP32 WS response seq mismatch: expected=%s got=%s", seq, result.get("seq"))
            return result

    async def _send_command_http(self, payload: dict) -> dict:
        logger.debug("Sending command to ESP32 via HTTP fallback: %s", payload)
        try:
            response = await self._client.post("/command", json=payload)
            if response.status_code in (200, 409):
                result = response.json()
                logger.debug("ESP32 HTTP response: %s", result)
                return result
            response.raise_for_status()
            result = response.json()
            logger.debug("ESP32 response (http): %s", result)
            return result
        except httpx.ConnectError as e:
            logger.warning("HTTPX connect failed to ESP32 (%s), retrying with urllib", e)
            last_error: Exception | None = None
            for _ in range(6):
                try:
                    result = await asyncio.to_thread(
                        self._urllib_request,
                        "POST",
                        "/command",
                        payload,
                    )
                    logger.debug("ESP32 response (urllib fallback): %s", result)
                    return result
                except Exception as ex:
                    last_error = ex
                    await asyncio.sleep(0.15)
            # Final fallback: shell out to curl (separate process/network stack).
            try:
                result = await self._curl_command(payload)
                logger.debug("ESP32 response (curl fallback): %s", result)
                return result
            except Exception as ex:
                last_error = ex
            logger.error("Cannot connect to ESP32 at %s (HTTP fallback)", self._base_url)
            if last_error:
                raise last_error
            raise
        except httpx.HTTPStatusError as e:
            logger.error("ESP32 returned error: %s", e.response.text)
            raise

    async def _ensure_ws(self):
        if self._ws is not None and not self._ws.closed:
            return self._ws
        self._ws = await websockets.connect(
            self._ws_url,
            open_timeout=self.timeout,
            ping_interval=20,
            ping_timeout=self.timeout,
            close_timeout=1,
            max_queue=16,
        )
        logger.debug("Connected to ESP32 WS at %s", self._ws_url)
        return self._ws

    async def move(self, dx: int, dy: int) -> dict:
        return await self.send_command(HIDCommand.move(dx, dy))

    async def click(self, x: int, y: int) -> dict:
        return await self.send_command(HIDCommand.click(x, y))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict:
        return await self.send_command(HIDCommand.swipe(x1, y1, x2, y2, duration_ms))

    async def ping(self) -> bool:
        """Check if the ESP32 is reachable."""
        # Try lightweight WS ping first (preferred command channel)
        try:
            async with self._ws_lock:
                ws = await self._ensure_ws()
                self._ws_seq += 1
                seq = self._ws_seq
                await ws.send(json.dumps({"type": "ping", "seq": seq}))
                raw = await asyncio.wait_for(ws.recv(), timeout=self.timeout)
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                result = json.loads(raw) if raw else {}
                if isinstance(result, dict) and result.get("type") == "pong":
                    return True
        except (TimeoutError, OSError, WebSocketException, json.JSONDecodeError):
            await self._close_ws()

        try:
            response = await self._client.get("/ping")
            return response.status_code == 200
        except httpx.HTTPError:
            try:
                await asyncio.to_thread(self._urllib_request, "GET", "/ping", None)
                return True
            except Exception:
                return False

    async def status(self) -> dict:
        """Get ESP32 status (BLE connection state, etc.)."""
        try:
            response = await self._client.get("/status")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return await asyncio.to_thread(self._urllib_request, "GET", "/status", None)

    async def get_mode(self) -> dict:
        """Get ESP32 current mode (input, output delivery, output mode)."""
        try:
            response = await self._client.get("/mode")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return await asyncio.to_thread(self._urllib_request, "GET", "/mode", None)

    async def set_mode(self, **kwargs: str) -> dict:
        """Set ESP32 mode.

        Accepted keys (v3 firmware): mode, delivery_policy.
        Backward-compatible keys: input_mode, output_delivery, output_mode, hid_mode.
        """
        try:
            response = await self._client.post("/mode", json=kwargs)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError:
            return await asyncio.to_thread(self._urllib_request, "POST", "/mode", kwargs)

    async def close(self) -> None:
        await self._close_ws()
        await self._client.aclose()

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _curl_command(self, payload: dict) -> dict:
        """Fallback command sender via curl subprocess."""
        cmd = [
            "curl",
            "-sS",
            "--max-time",
            str(max(1, int(self.timeout))),
            "-H",
            "Content-Type: application/json",
            "-X",
            "POST",
            "-d",
            json.dumps(payload),
            f"{self._base_url}/command",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())
        body = stdout.decode("utf-8", errors="replace").strip()
        return json.loads(body) if body else {}
