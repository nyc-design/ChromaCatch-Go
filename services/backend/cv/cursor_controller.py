"""Cursor positioning via corner-reset + closed-loop CV correction.

The iOS AssistiveTouch cursor (created by BLE HID mouse) can only be
moved with relative dx/dy. This module provides absolute positioning by:

1. **Corner reset**: Send large movement to a known corner so we know
   exactly where the cursor is (cursor stops at screen edge).
2. **Approximate move**: Use a calibrated scale factor to send dx/dy
   toward the target pixel coordinates.
3. **CV correction loop**: Detect cursor position in the frame, compute
   error, send correction. Repeat until within tolerance.

BLE HID mouse.move() accepts int8_t (-128..127), so large movements
are chunked into steps of at most MAX_STEP with inter-step delays.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from shared.messages import HIDCommandMessage

logger = logging.getLogger(__name__)

# BLE HID mouse.move() takes signed 8-bit values: -128 to 127
MAX_STEP = 100  # Conservative max per-chunk (stay well within int8 range)
CHUNK_DELAY_S = 0.012  # 12ms between chunks (BLE needs time to transmit)

# Corner reset: send enough to guarantee hitting the edge from anywhere
RESET_OVERSHOOT = 5000  # Total units to send (will be chunked)

# Default calibration: mouse units per normalized screen unit (0..1)
# This is a rough starting estimate; auto-calibration refines it.
DEFAULT_SCALE_X = 600.0
DEFAULT_SCALE_Y = 1000.0

# CV correction loop parameters
MAX_CORRECTION_ITERATIONS = 3
POSITION_TOLERANCE_NORM = 0.015  # ~1.5% of screen = close enough

# Path to cursor template image (placeholder — replace with real screenshot)
CURSOR_TEMPLATE_DIR = Path(__file__).parent / "templates"
CURSOR_TEMPLATE_PATH = CURSOR_TEMPLATE_DIR / "ios_cursor.png"


@dataclass
class CursorPosition:
    """Detected cursor position in normalized coordinates (0..1)."""

    x: float  # 0 = left edge, 1 = right edge
    y: float  # 0 = top edge, 1 = bottom edge
    confidence: float  # Detection confidence (0..1)


@dataclass
class CalibrationData:
    """Mouse-units-to-normalized-screen calibration."""

    scale_x: float = DEFAULT_SCALE_X
    scale_y: float = DEFAULT_SCALE_Y
    last_calibrated: float = 0.0


@dataclass
class MoveResult:
    """Result of a move_to or click_at operation."""

    success: bool
    final_position: CursorPosition | None = None
    iterations: int = 0
    total_commands: int = 0
    error_norm: float = 0.0  # Remaining distance to target (normalized)


class CursorController:
    """Closed-loop cursor positioning using BLE HID mouse + CV feedback.

    Usage:
        controller = CursorController(send_command_fn=session_mgr.send_command)
        await controller.reset_to_corner()
        result = await controller.move_to(0.5, 0.3, current_frame)
        result = await controller.click_at(0.5, 0.3, current_frame)
    """

    def __init__(
        self,
        send_command: SendCommandFn,
        *,
        calibration: CalibrationData | None = None,
        cursor_template: np.ndarray | None = None,
    ):
        """
        Args:
            send_command: Async callable that sends an HIDCommandMessage.
                Signature: async def send_command(cmd: HIDCommandMessage) -> None
            calibration: Optional pre-loaded calibration data.
            cursor_template: Optional cursor template image (BGR).
                If None, attempts to load from CURSOR_TEMPLATE_PATH.
        """
        self.send_command = send_command
        self.calibration = calibration or CalibrationData()
        self._known_position: CursorPosition | None = None
        self._total_commands = 0

        # Load cursor template
        if cursor_template is not None:
            self._cursor_template = cursor_template
        elif CURSOR_TEMPLATE_PATH.exists():
            self._cursor_template = cv2.imread(str(CURSOR_TEMPLATE_PATH))
            logger.info("Loaded cursor template from %s", CURSOR_TEMPLATE_PATH)
        else:
            self._cursor_template = None
            logger.warning(
                "No cursor template at %s — cursor detection will use fallback",
                CURSOR_TEMPLATE_PATH,
            )

    @property
    def known_position(self) -> CursorPosition | None:
        """Last known cursor position (from reset or CV detection)."""
        return self._known_position

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    async def reset_to_corner(self, corner: str = "top-left") -> None:
        """Move cursor to a known screen corner by overshooting.

        Since the cursor stops at screen edges, sending a large movement
        toward a corner guarantees we end up exactly at that corner.

        Args:
            corner: "top-left", "top-right", "bottom-left", "bottom-right"
        """
        dx_sign, dy_sign = {
            "top-left": (-1, -1),
            "top-right": (1, -1),
            "bottom-left": (-1, 1),
            "bottom-right": (1, 1),
        }[corner]

        logger.info("Resetting cursor to %s corner", corner)
        await self._send_chunked_move(
            dx_sign * RESET_OVERSHOOT, dy_sign * RESET_OVERSHOOT
        )

        # After reset, we know exactly where we are
        corner_x = 0.0 if dx_sign < 0 else 1.0
        corner_y = 0.0 if dy_sign < 0 else 1.0
        self._known_position = CursorPosition(x=corner_x, y=corner_y, confidence=1.0)
        logger.info("Cursor reset complete — position: (%.2f, %.2f)", corner_x, corner_y)

    async def move_to(
        self,
        target_x: float,
        target_y: float,
        get_frame: GetFrameFn,
        *,
        max_iterations: int = MAX_CORRECTION_ITERATIONS,
        tolerance: float = POSITION_TOLERANCE_NORM,
    ) -> MoveResult:
        """Move cursor to target position using closed-loop CV correction.

        Args:
            target_x: Target X in normalized coords (0..1).
            target_y: Target Y in normalized coords (0..1).
            get_frame: Async callable returning current frame (BGR ndarray).
            max_iterations: Max CV correction loops.
            tolerance: Acceptable error in normalized coords.

        Returns:
            MoveResult with success status and final position.
        """
        commands_before = self._total_commands

        # Step 1: Approximate move from known position (or blind)
        if self._known_position is not None:
            dx_norm = target_x - self._known_position.x
            dy_norm = target_y - self._known_position.y
        else:
            # No known position — try CV detection first
            frame = await get_frame()
            detected = self.detect_cursor(frame)
            if detected is not None:
                self._known_position = detected
                dx_norm = target_x - detected.x
                dy_norm = target_y - detected.y
            else:
                # Blind — reset to corner first
                logger.warning("No known position and cursor not detected — resetting")
                await self.reset_to_corner()
                dx_norm = target_x - self._known_position.x
                dy_norm = target_y - self._known_position.y

        # Convert normalized delta to mouse units
        dx_mouse = int(dx_norm * self.calibration.scale_x)
        dy_mouse = int(dy_norm * self.calibration.scale_y)

        if abs(dx_mouse) > 0 or abs(dy_mouse) > 0:
            await self._send_chunked_move(dx_mouse, dy_mouse)

        # Update estimated position
        self._known_position = CursorPosition(x=target_x, y=target_y, confidence=0.5)

        # Step 2: CV correction loop
        for iteration in range(max_iterations):
            await asyncio.sleep(0.05)  # Wait for frame to update after movement
            frame = await get_frame()
            detected = self.detect_cursor(frame)

            if detected is None:
                logger.debug("Correction %d: cursor not detected", iteration + 1)
                continue

            self._known_position = detected
            error_x = target_x - detected.x
            error_y = target_y - detected.y
            error_norm = math.sqrt(error_x**2 + error_y**2)

            logger.debug(
                "Correction %d: cursor at (%.3f, %.3f), target (%.3f, %.3f), error=%.3f",
                iteration + 1,
                detected.x,
                detected.y,
                target_x,
                target_y,
                error_norm,
            )

            if error_norm <= tolerance:
                return MoveResult(
                    success=True,
                    final_position=detected,
                    iterations=iteration + 1,
                    total_commands=self._total_commands - commands_before,
                    error_norm=error_norm,
                )

            # Send correction
            corr_dx = int(error_x * self.calibration.scale_x)
            corr_dy = int(error_y * self.calibration.scale_y)
            if abs(corr_dx) > 0 or abs(corr_dy) > 0:
                await self._send_chunked_move(corr_dx, corr_dy)

        # Exhausted iterations — return best effort
        return MoveResult(
            success=False,
            final_position=self._known_position,
            iterations=max_iterations,
            total_commands=self._total_commands - commands_before,
            error_norm=math.sqrt(
                (target_x - (self._known_position.x if self._known_position else 0)) ** 2
                + (target_y - (self._known_position.y if self._known_position else 0)) ** 2
            ),
        )

    async def click_at(
        self,
        target_x: float,
        target_y: float,
        get_frame: GetFrameFn,
        **move_kwargs,
    ) -> MoveResult:
        """Move to target position and click.

        Args:
            target_x, target_y: Target in normalized coords (0..1).
            get_frame: Async callable returning current frame.
            **move_kwargs: Passed to move_to().
        """
        result = await self.move_to(target_x, target_y, get_frame, **move_kwargs)

        # Click regardless of whether we hit exact target — best effort
        await self._send_click()
        result.total_commands += 1

        return result

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    async def calibrate(self, get_frame: GetFrameFn) -> CalibrationData:
        """Auto-calibrate mouse-to-pixel ratio.

        Resets to top-left corner, moves a known amount, measures pixel
        displacement via CV, and computes the scale factor.
        """
        await self.reset_to_corner("top-left")
        await asyncio.sleep(0.2)

        frame_before = await get_frame()
        pos_before = self.detect_cursor(frame_before)
        if pos_before is None:
            logger.warning("Calibration failed: cursor not detected at corner")
            return self.calibration

        # Send a known movement
        test_dx = 300
        test_dy = 300
        await self._send_chunked_move(test_dx, test_dy)
        await asyncio.sleep(0.2)

        frame_after = await get_frame()
        pos_after = self.detect_cursor(frame_after)
        if pos_after is None:
            logger.warning("Calibration failed: cursor not detected after move")
            return self.calibration

        # Compute scale factors
        pixel_dx = pos_after.x - pos_before.x
        pixel_dy = pos_after.y - pos_before.y

        if abs(pixel_dx) > 0.01:
            self.calibration.scale_x = test_dx / pixel_dx
        if abs(pixel_dy) > 0.01:
            self.calibration.scale_y = test_dy / pixel_dy
        self.calibration.last_calibrated = time.time()

        logger.info(
            "Calibration complete: scale_x=%.1f, scale_y=%.1f",
            self.calibration.scale_x,
            self.calibration.scale_y,
        )

        # Reset back to corner after calibration
        await self.reset_to_corner("top-left")
        return self.calibration

    # ------------------------------------------------------------------
    # Cursor detection
    # ------------------------------------------------------------------

    def detect_cursor(self, frame: np.ndarray) -> CursorPosition | None:
        """Detect the iOS AssistiveTouch cursor in a frame.

        Uses multi-scale template matching if a template is available,
        otherwise falls back to circle detection.

        Args:
            frame: BGR image from screen capture.

        Returns:
            CursorPosition or None if cursor not found.
        """
        if self._cursor_template is not None:
            result = self._detect_cursor_template(frame, self._cursor_template)
            if result is not None:
                return result

        # Fallback: circle detection (iOS cursor is a small dark circle)
        return self._detect_cursor_circles(frame)

    def _detect_cursor_template(
        self, frame: np.ndarray, template: np.ndarray
    ) -> CursorPosition | None:
        """Detect cursor via multi-scale template matching (NCC)."""
        gray_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray_template = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)

        h_frame, w_frame = gray_frame.shape[:2]
        h_tmpl, w_tmpl = gray_template.shape[:2]

        best_val = -1.0
        best_loc = None
        best_scale = 1.0

        # Try multiple scales (cursor size varies with device/resolution)
        for scale in np.linspace(0.5, 2.0, 15):
            new_w = max(1, int(w_tmpl * scale))
            new_h = max(1, int(h_tmpl * scale))
            if new_w >= w_frame or new_h >= h_frame:
                continue

            scaled = cv2.resize(gray_template, (new_w, new_h))
            result = cv2.matchTemplate(gray_frame, scaled, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)

            if max_val > best_val:
                best_val = max_val
                best_loc = max_loc
                best_scale = scale

        if best_val < 0.5 or best_loc is None:
            return None

        # Convert to center point in normalized coords
        cx = (best_loc[0] + int(w_tmpl * best_scale) / 2) / w_frame
        cy = (best_loc[1] + int(h_tmpl * best_scale) / 2) / h_frame

        return CursorPosition(x=cx, y=cy, confidence=best_val)

    @staticmethod
    def _detect_cursor_circles(frame: np.ndarray) -> CursorPosition | None:
        """Fallback: detect iOS cursor via HoughCircles.

        The iOS AssistiveTouch pointer is a small, semi-transparent
        dark circle (~15-30px on 720p). We look for small circles
        with appropriate contrast.
        """
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        blurred = cv2.GaussianBlur(gray, (9, 9), 2)

        # iOS cursor is roughly 1-3% of frame height
        min_r = max(3, int(h * 0.008))
        max_r = max(10, int(h * 0.025))

        circles = cv2.HoughCircles(
            blurred,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=max_r * 4,
            param1=80,
            param2=30,
            minRadius=min_r,
            maxRadius=max_r,
        )

        if circles is None or len(circles[0]) == 0:
            return None

        # Take the best candidate (highest accumulator vote = first result)
        best = circles[0][0]
        cx, cy, r = best
        return CursorPosition(
            x=float(cx / w),
            y=float(cy / h),
            confidence=0.4,  # Low confidence for fallback method
        )

    # ------------------------------------------------------------------
    # Low-level movement (chunked for BLE HID int8 limits)
    # ------------------------------------------------------------------

    async def _send_chunked_move(self, dx: int, dy: int) -> int:
        """Send a mouse movement, chunking into BLE-safe steps.

        BLE HID mouse.move() takes int8_t (-128..127). Large movements
        are split into multiple steps of at most MAX_STEP each.

        Args:
            dx: Total horizontal movement in mouse units.
            dy: Total vertical movement in mouse units.

        Returns:
            Number of HID commands sent.
        """
        if dx == 0 and dy == 0:
            return 0

        # Determine number of chunks needed
        max_abs = max(abs(dx), abs(dy))
        n_chunks = max(1, math.ceil(max_abs / MAX_STEP))

        chunk_dx = dx / n_chunks
        chunk_dy = dy / n_chunks

        commands_sent = 0
        remaining_dx = 0.0
        remaining_dy = 0.0

        for i in range(n_chunks):
            # Accumulate fractional parts to avoid drift
            remaining_dx += chunk_dx
            remaining_dy += chunk_dy

            step_dx = int(round(remaining_dx))
            step_dy = int(round(remaining_dy))

            remaining_dx -= step_dx
            remaining_dy -= step_dy

            # Clamp to int8 range (safety)
            step_dx = max(-128, min(127, step_dx))
            step_dy = max(-128, min(127, step_dy))

            if step_dx == 0 and step_dy == 0:
                continue

            cmd = HIDCommandMessage(
                action="move",
                params={"dx": step_dx, "dy": step_dy},
            )
            await self.send_command(cmd)
            commands_sent += 1
            self._total_commands += 1

            # Small delay between chunks for BLE transmission
            if i < n_chunks - 1:
                await asyncio.sleep(CHUNK_DELAY_S)

        return commands_sent

    async def _send_click(self) -> None:
        """Send a left click at current position."""
        cmd = HIDCommandMessage(action="click", params={"x": 0, "y": 0})
        await self.send_command(cmd)
        self._total_commands += 1


# Type aliases for callback signatures
from typing import Awaitable, Callable

SendCommandFn = Callable[[HIDCommandMessage], Awaitable[None]]
GetFrameFn = Callable[[], Awaitable[np.ndarray]]
