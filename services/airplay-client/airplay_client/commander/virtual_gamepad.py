"""Virtual gamepad commander — creates OS-level virtual controller for emulators.

Linux: Uses python-evdev to create a virtual input device via uinput.
Windows: Uses ViGEmBus + vgamepad to create a virtual Xbox 360 controller.
macOS: Not supported (no uinput equivalent).

The virtual controller appears as a real gamepad to any emulator
(Ryujinx, Citra/Azahar, mGBA, etc.).
"""

from __future__ import annotations

import logging
import platform
import time

from airplay_client.commander.base import Commander, CommandResult

logger = logging.getLogger(__name__)

# Button name → evdev/ViGEm button code mapping (populated per platform)
_SYSTEM = platform.system()


class VirtualGamepadCommander(Commander):
    """Routes game commands to a virtual OS-level gamepad."""

    def __init__(self) -> None:
        self._connected = False
        self._device = None  # Platform-specific device handle

    async def send_command(self, action: str, params: dict) -> CommandResult:
        if not self._connected:
            await self.connect()

        forwarded_at = time.time()
        try:
            if _SYSTEM == "Linux":
                self._send_linux(action, params)
            elif _SYSTEM == "Windows":
                self._send_windows(action, params)
            else:
                return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=f"Unsupported platform: {_SYSTEM}")
            return CommandResult(success=True, forwarded_at=forwarded_at, completed_at=time.time())
        except Exception as e:
            logger.error("Virtual gamepad command failed: %s", e)
            return CommandResult(success=False, forwarded_at=forwarded_at, completed_at=time.time(), error=str(e))

    def _send_linux(self, action: str, params: dict) -> None:
        """Send input via evdev/uinput on Linux."""
        import evdev  # noqa: F811
        from evdev import ecodes

        if self._device is None:
            raise RuntimeError("Virtual gamepad not connected")

        if action == "button_press":
            btn_code = self._linux_button_code(params.get("button", ""))
            if btn_code is not None:
                self._device.write(ecodes.EV_KEY, btn_code, 1)
                self._device.syn()
        elif action == "button_release":
            btn_code = self._linux_button_code(params.get("button", ""))
            if btn_code is not None:
                self._device.write(ecodes.EV_KEY, btn_code, 0)
                self._device.syn()
        elif action == "stick":
            stick_id = params.get("stick_id", "left")
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            if stick_id == "left":
                self._device.write(ecodes.EV_ABS, ecodes.ABS_X, x)
                self._device.write(ecodes.EV_ABS, ecodes.ABS_Y, y)
            else:
                self._device.write(ecodes.EV_ABS, ecodes.ABS_RX, x)
                self._device.write(ecodes.EV_ABS, ecodes.ABS_RY, y)
            self._device.syn()

    def _send_windows(self, action: str, params: dict) -> None:
        """Send input via ViGEm/vgamepad on Windows."""
        if self._device is None:
            raise RuntimeError("Virtual gamepad not connected")

        # vgamepad API: press_button, release_button, left_joystick, right_joystick, update
        if action == "button_press":
            btn = self._vgamepad_button(params.get("button", ""))
            if btn is not None:
                self._device.press_button(button=btn)
                self._device.update()
        elif action == "button_release":
            btn = self._vgamepad_button(params.get("button", ""))
            if btn is not None:
                self._device.release_button(button=btn)
                self._device.update()
        elif action == "stick":
            stick_id = params.get("stick_id", "left")
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            # vgamepad expects float -1.0 to 1.0
            fx = max(-1.0, min(1.0, x / 32767.0))
            fy = max(-1.0, min(1.0, y / 32767.0))
            if stick_id == "left":
                self._device.left_joystick_float(x_value_float=fx, y_value_float=fy)
            else:
                self._device.right_joystick_float(x_value_float=fx, y_value_float=fy)
            self._device.update()

    @staticmethod
    def _linux_button_code(button: str) -> int | None:
        """Map button name to Linux evdev button code."""
        from evdev import ecodes
        _map = {
            "a": ecodes.BTN_SOUTH, "b": ecodes.BTN_EAST,
            "x": ecodes.BTN_NORTH, "y": ecodes.BTN_WEST,
            "l": ecodes.BTN_TL, "r": ecodes.BTN_TR,
            "zl": ecodes.BTN_TL2, "zr": ecodes.BTN_TR2,
            "start": ecodes.BTN_START, "select": ecodes.BTN_SELECT,
            "plus": ecodes.BTN_START, "minus": ecodes.BTN_SELECT,
            "lstick": ecodes.BTN_THUMBL, "rstick": ecodes.BTN_THUMBR,
        }
        return _map.get(str(button).lower())

    @staticmethod
    def _vgamepad_button(button: str) -> int | None:
        """Map button name to vgamepad XUSB_BUTTON code."""
        try:
            from vgamepad import XUSB_BUTTON
        except ImportError:
            return None
        _map = {
            "a": XUSB_BUTTON.XUSB_GAMEPAD_A,
            "b": XUSB_BUTTON.XUSB_GAMEPAD_B,
            "x": XUSB_BUTTON.XUSB_GAMEPAD_X,
            "y": XUSB_BUTTON.XUSB_GAMEPAD_Y,
            "l": XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
            "r": XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
            "start": XUSB_BUTTON.XUSB_GAMEPAD_START,
            "select": XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "plus": XUSB_BUTTON.XUSB_GAMEPAD_START,
            "minus": XUSB_BUTTON.XUSB_GAMEPAD_BACK,
            "lstick": XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
            "rstick": XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
            "up": XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
            "down": XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
            "left": XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
            "right": XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
        }
        return _map.get(str(button).lower())

    async def connect(self) -> None:
        try:
            if _SYSTEM == "Linux":
                import evdev
                from evdev import AbsInfo, UInput, ecodes
                cap = {
                    ecodes.EV_KEY: [
                        ecodes.BTN_SOUTH, ecodes.BTN_EAST, ecodes.BTN_NORTH, ecodes.BTN_WEST,
                        ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_TL2, ecodes.BTN_TR2,
                        ecodes.BTN_START, ecodes.BTN_SELECT,
                        ecodes.BTN_THUMBL, ecodes.BTN_THUMBR,
                    ],
                    ecodes.EV_ABS: [
                        (ecodes.ABS_X, AbsInfo(0, -32768, 32767, 0, 0, 0)),
                        (ecodes.ABS_Y, AbsInfo(0, -32768, 32767, 0, 0, 0)),
                        (ecodes.ABS_RX, AbsInfo(0, -32768, 32767, 0, 0, 0)),
                        (ecodes.ABS_RY, AbsInfo(0, -32768, 32767, 0, 0, 0)),
                    ],
                }
                self._device = UInput(cap, name="ChromaCatch Virtual Gamepad", bustype=ecodes.BUS_USB)
                self._connected = True
                logger.info("Virtual gamepad created (Linux uinput)")
            elif _SYSTEM == "Windows":
                import vgamepad as vg
                self._device = vg.VX360Gamepad()
                self._connected = True
                logger.info("Virtual gamepad created (Windows ViGEm Xbox 360)")
            else:
                logger.error("Virtual gamepad not supported on %s", _SYSTEM)
                self._connected = False
        except ImportError as e:
            logger.error("Virtual gamepad dependency missing: %s", e)
            self._connected = False
        except Exception as e:
            logger.error("Virtual gamepad creation failed: %s", e)
            self._connected = False

    async def disconnect(self) -> None:
        if self._device is not None:
            if _SYSTEM == "Linux":
                self._device.close()
            elif _SYSTEM == "Windows":
                del self._device
        self._device = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def commander_name(self) -> str:
        return "virtual-gamepad"

    @property
    def supported_command_types(self) -> list[str]:
        return ["gamepad"]
