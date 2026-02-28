"""HTTP client for sending HID commands to the ESP32."""

import asyncio
import json
import logging
import urllib.error
import urllib.request

import httpx

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
    """Sends HID mouse commands to the ESP32 over WiFi HTTP."""

    def __init__(self, host: str | None = None, port: int | None = None, timeout: float | None = None):
        self.host = host or settings.esp32_host
        self.port = port or settings.esp32_port
        self.timeout = timeout or settings.esp32_timeout
        self._base_url = f"http://{self.host}:{self.port}"
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=0)
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self.timeout,
            trust_env=False,
            headers={"Connection": "close"},
            limits=limits,
        )

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
        logger.debug("Sending command to ESP32: %s", payload)
        try:
            response = await self._client.post("/command", json=payload)
            response.raise_for_status()
            result = response.json()
            logger.debug("ESP32 response: %s", result)
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
            logger.error("Cannot connect to ESP32 at %s", self._base_url)
            if last_error:
                raise last_error
            raise
        except httpx.HTTPStatusError as e:
            logger.error("ESP32 returned error: %s", e.response.text)
            raise

    async def move(self, dx: int, dy: int) -> dict:
        return await self.send_command(HIDCommand.move(dx, dy))

    async def click(self, x: int, y: int) -> dict:
        return await self.send_command(HIDCommand.click(x, y))

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict:
        return await self.send_command(HIDCommand.swipe(x1, y1, x2, y2, duration_ms))

    async def ping(self) -> bool:
        """Check if the ESP32 is reachable."""
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

    async def close(self) -> None:
        await self._client.aclose()

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
