"""Cursor positioning via calibration curve + frame-differencing detection.

Works on both iPhone (translucent grey circle cursor) and iPad (mouse
pointer) by detecting cursor motion rather than cursor appearance.

Approach:
1. **Calibration**: From bottom-left corner baseline, measure pixel
   displacement for several dy values [60, 40, 20, 10, 5] (largest first).
   First step also extracts cursor height. Displacement is measured as
   (topmost-to-bottommost diff extent) minus cursor height — works even
   when before/after cursor positions overlap at small steps.
2. **Movement**: To reach any target, compute path from baseline using
   calibration curve (large steps first, then small for precision).
   Execute: push to bottom-left → baseline → pre-calculated path.
3. **Verification**: After movement, diff baseline frame vs final frame
   to locate where cursor ended up. If too far from target, recalibrate
   once and retry.

BLE HID mouse.move() accepts int8_t (-128..127), so large movements
are chunked into steps of at most MAX_STEP with inter-step delays.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import cv2
import numpy as np

from shared.messages import BaseMessage, GameCommandMessage, HIDCommandMessage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------
SendCommandFn = Callable[[BaseMessage], Awaitable[None]]
GetFrameFn = Callable[[], Awaitable[np.ndarray]]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# BLE HID limits
MAX_STEP = 60  # Max per-report delta (int8 safe zone)
CHUNK_DELAY_S = 0.012  # 12ms between chunks

# Corner reset
RESET_OVERSHOOT = 2200  # Enough to hit any edge from anywhere

# Calibration
CALIBRATION_STEPS = [60, 40, 20, 10, 5]  # Largest first (to get cursor height)
BASELINE_DX = 10  # Baseline: right from corner
BASELINE_DY = -30  # Baseline: up from corner (negative = up on screen)

# Frame differencing
DIFF_THRESHOLD = 25  # Pixel intensity threshold
DIFF_MORPH_KERNEL_SIZE = 5
SETTLE_S = 1.0  # Wait after move for BLE→phone→screen→WS round-trip

# Verification
VERIFY_TOLERANCE_PX = 40.0  # Max acceptable error after move


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class CalibrationPoint:
    """Single calibration measurement: dy command → pixel displacement."""

    dx_cmd: int
    px_moved: float


@dataclass
class CalibrationCurve:
    """Piecewise-linear mapping from HID command units to pixel displacement."""

    points_x: list[CalibrationPoint] = field(default_factory=list)
    points_y: list[CalibrationPoint] = field(default_factory=list)
    frame_width: int = 0
    frame_height: int = 0
    cursor_height: int = 0
    baseline_pos: tuple[float, float] = (0.0, 0.0)
    calibrated_at: float = 0.0

    def _interpolate(self, points: list[CalibrationPoint], target_px: float) -> int:
        """Given a desired pixel displacement, return the best command units."""
        if not points:
            return int(target_px)

        sorted_pts = sorted(points, key=lambda p: p.px_moved)

        if target_px <= sorted_pts[0].px_moved:
            if sorted_pts[0].px_moved > 0:
                ratio = target_px / sorted_pts[0].px_moved
                return max(1, int(round(sorted_pts[0].dx_cmd * ratio)))
            return sorted_pts[0].dx_cmd

        if target_px >= sorted_pts[-1].px_moved:
            if sorted_pts[-1].px_moved > 0:
                ratio = target_px / sorted_pts[-1].px_moved
                return int(round(sorted_pts[-1].dx_cmd * ratio))
            return sorted_pts[-1].dx_cmd

        for i in range(len(sorted_pts) - 1):
            lo, hi = sorted_pts[i], sorted_pts[i + 1]
            if lo.px_moved <= target_px <= hi.px_moved:
                span_px = hi.px_moved - lo.px_moved
                if span_px < 0.001:
                    return lo.dx_cmd
                t = (target_px - lo.px_moved) / span_px
                dx = lo.dx_cmd + t * (hi.dx_cmd - lo.dx_cmd)
                return max(1, int(round(dx)))

        return sorted_pts[-1].dx_cmd

    def _lookup(self, points: list[CalibrationPoint], dx_cmd: int) -> float:
        """Given command units, return expected pixel displacement."""
        if not points:
            return float(dx_cmd)

        sorted_pts = sorted(points, key=lambda p: p.dx_cmd)

        if dx_cmd <= sorted_pts[0].dx_cmd:
            if sorted_pts[0].dx_cmd > 0:
                return sorted_pts[0].px_moved * dx_cmd / sorted_pts[0].dx_cmd
            return sorted_pts[0].px_moved

        if dx_cmd >= sorted_pts[-1].dx_cmd:
            if sorted_pts[-1].dx_cmd > 0:
                return sorted_pts[-1].px_moved * dx_cmd / sorted_pts[-1].dx_cmd
            return sorted_pts[-1].px_moved

        for i in range(len(sorted_pts) - 1):
            lo, hi = sorted_pts[i], sorted_pts[i + 1]
            if lo.dx_cmd <= dx_cmd <= hi.dx_cmd:
                span = hi.dx_cmd - lo.dx_cmd
                if span == 0:
                    return lo.px_moved
                t = (dx_cmd - lo.dx_cmd) / span
                return lo.px_moved + t * (hi.px_moved - lo.px_moved)

        return sorted_pts[-1].px_moved

    def px_to_cmd(self, target_px: float, axis: str = "x") -> int:
        """Pixel displacement → HID command units."""
        points = self._points_for_axis(axis)
        return self._interpolate(points, target_px)

    def cmd_to_px(self, cmd: int, axis: str = "x") -> float:
        """HID command units → expected pixel displacement."""
        points = self._points_for_axis(axis)
        return self._lookup(points, cmd)

    def _points_for_axis(self, axis: str) -> list[CalibrationPoint]:
        if axis == "y" and self.points_y:
            return self.points_y
        if axis == "x" and self.points_x:
            return self.points_x
        return self.points_y or self.points_x

    def to_dict(self) -> dict:
        return {
            "points_x": [{"dx_cmd": p.dx_cmd, "px_moved": p.px_moved} for p in self.points_x],
            "points_y": [{"dx_cmd": p.dx_cmd, "px_moved": p.px_moved} for p in self.points_y],
            "frame_width": self.frame_width,
            "frame_height": self.frame_height,
            "cursor_height": self.cursor_height,
            "baseline_pos": list(self.baseline_pos),
            "calibrated_at": self.calibrated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CalibrationCurve:
        bp = data.get("baseline_pos", [0.0, 0.0])
        return cls(
            points_x=[CalibrationPoint(**p) for p in data.get("points_x", [])],
            points_y=[CalibrationPoint(**p) for p in data.get("points_y", [])],
            frame_width=data.get("frame_width", 0),
            frame_height=data.get("frame_height", 0),
            cursor_height=data.get("cursor_height", 0),
            baseline_pos=tuple(bp) if isinstance(bp, (list, tuple)) else (0.0, 0.0),
            calibrated_at=data.get("calibrated_at", 0.0),
        )


@dataclass
class CursorPosition:
    x: float
    y: float
    confidence: float


@dataclass
class MoveResult:
    success: bool
    final_position: CursorPosition | None = None
    error_px: float = 0.0
    iterations: int = 0
    total_commands: int = 0
    recalibrated: bool = False


# ---------------------------------------------------------------------------
# CursorController
# ---------------------------------------------------------------------------
class CursorController:
    """Open-loop cursor positioning with post-move verification.

    Movement approach:
    1. Push cursor to bottom-left corner (overshoot)
    2. Move to baseline (known pixel position from calibration)
    3. Execute pre-calculated path from baseline to target
    4. Verify final position via grayscale diff

    If verification fails (cursor too far from target), recalibrate
    once and retry. If still fails, report error.
    """

    def __init__(self, send_command: SendCommandFn, *, calibration: CalibrationCurve | None = None):
        self.send_command = send_command
        self.calibration = calibration or CalibrationCurve()
        self._total_commands: int = 0
        self._last_move_time: float = 0.0
        self._initialized: bool = False

    @property
    def initialized(self) -> bool:
        return self._initialized

    # -- Profile persistence -------------------------------------------------

    def export_profile(self) -> dict:
        return {"calibration": self.calibration.to_dict()}

    def apply_profile(self, profile: dict) -> bool:
        try:
            self.calibration = CalibrationCurve.from_dict(profile["calibration"])
            self._initialized = bool(self.calibration.points_x)
            return True
        except Exception:
            logger.exception("Failed to apply cursor profile")
            return False

    # -- Low-level movement --------------------------------------------------

    async def _send_move(self, dx: int, dy: int) -> None:
        dx = max(-127, min(127, dx))
        dy = max(-127, min(127, dy))
        if dx == 0 and dy == 0:
            return
        cmd = HIDCommandMessage(action="move", params={"dx": dx, "dy": dy})
        await self.send_command(cmd)
        self._total_commands += 1
        self._last_move_time = time.time()

    async def _send_chunked_move(self, dx: int, dy: int, *, max_step: int = MAX_STEP, chunk_delay_s: float = CHUNK_DELAY_S) -> int:
        if dx == 0 and dy == 0:
            return 0

        dominant = max(abs(dx), abs(dy))
        n_chunks = math.ceil(dominant / max_step)
        chunks_sent = 0

        for i in range(n_chunks):
            cdx = _distribute(dx, i, n_chunks)
            cdy = _distribute(dy, i, n_chunks)
            await self._send_move(cdx, cdy)
            chunks_sent += 1
            if i < n_chunks - 1:
                await asyncio.sleep(chunk_delay_s)

        return chunks_sent

    async def _send_home_button(self) -> None:
        cmd = GameCommandMessage(command_type="keyboard", action="key", params={"key": "h", "modifiers": "gui"})
        await self.send_command(cmd)
        self._total_commands += 1

    # -- Corner and baseline -------------------------------------------------

    async def _push_to_corner(self) -> None:
        """Push cursor to bottom-left corner by overshooting."""
        await self._send_chunked_move(-RESET_OVERSHOOT, RESET_OVERSHOOT)
        await asyncio.sleep(1.5)

    async def _move_to_baseline(self) -> None:
        """From bottom-left corner, move to baseline position.

        The first move after idle may be eaten by iOS cursor wake,
        but that's fine — the baseline move (10, -30) is what matters
        since push_to_corner already woke the cursor with hundreds of moves.
        """
        await self._send_move(BASELINE_DX, BASELINE_DY)
        await asyncio.sleep(SETTLE_S)

    async def _to_baseline(self) -> None:
        """Full sequence: push to bottom-left corner → move to baseline."""
        await self._push_to_corner()
        await self._move_to_baseline()

    # -- Frame differencing --------------------------------------------------

    @staticmethod
    def _diff_extent_in_region(f0: np.ndarray, f1: np.ndarray, search_box: tuple[int, int, int, int]) -> tuple[np.ndarray, np.ndarray | None]:
        """Compute grayscale diff and find non-zero extent in search region.

        search_box: (x1, y1, x2, y2).
        Returns (roi_binary, nonzero_points_or_None).
        """
        gray0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
        gray1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        absdiff = cv2.absdiff(gray0, gray1)

        x1, y1, x2, y2 = search_box
        roi = absdiff[y1:y2, x1:x2]

        _, binary = cv2.threshold(roi, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (DIFF_MORPH_KERNEL_SIZE, DIFF_MORPH_KERNEL_SIZE))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

        nz = cv2.findNonZero(binary)
        return binary, nz

    # -- Calibration ---------------------------------------------------------

    async def calibrate(self, get_frame: GetFrameFn, *, steps: list[int] | None = None) -> CalibrationCurve:
        """Run calibration sweep for both axes (bottom-left, largest step first).

        Vertical pass (→ points_y): move UP, measure vertical extent.
        Horizontal pass (→ points_x): move RIGHT, measure horizontal extent.

        The first vertical step also determines cursor_height from
        blob analysis (two separate blobs at large displacement).
        """
        steps = steps or CALIBRATION_STEPS
        logger.info("Starting calibration with steps: %s", steps)

        frame = await get_frame()
        h, w = frame.shape[:2]
        self.calibration.frame_width = w
        self.calibration.frame_height = h
        logger.info("Frame dimensions: %dx%d", w, h)

        # Go to home screen for clean background
        await self._send_home_button()
        await asyncio.sleep(1.5)

        cursor_height = None
        points_y: list[CalibrationPoint] = []
        baseline_pos = None

        # --- Vertical calibration (dy moves → points_y) ---
        logger.info("Vertical calibration pass")
        for step in steps:
            await self._to_baseline()

            # Search region: bottom-left, height scaled by step size
            region_h = min(400, 100 + step * 6)
            search_box = (0, h - region_h, w // 3, h)

            f0 = await get_frame()

            # Test move UP (negative dy)
            await self._send_chunked_move(0, -step)
            await asyncio.sleep(SETTLE_S)

            f1 = await get_frame()

            binary, nz = self._diff_extent_in_region(f0, f1, search_box)
            if nz is None:
                logger.warning("  Calibration dy=%d → FAILED (no diff)", step)
                continue

            ys = nz[:, 0, 1]
            xs = nz[:, 0, 0]
            top_y = int(ys.min())
            bot_y = int(ys.max())
            extent_h = bot_y - top_y

            # First step: extract cursor height from blob analysis
            if cursor_height is None:
                contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                blob_heights = []
                for c in contours:
                    if cv2.contourArea(c) < 10:
                        continue
                    _, _, _, bh = cv2.boundingRect(c)
                    blob_heights.append(bh)
                if len(blob_heights) >= 2:
                    blob_heights.sort()
                    cursor_height = int(np.mean(blob_heights[:2]))
                elif len(blob_heights) == 1:
                    cursor_height = blob_heights[0]

            if cursor_height is None:
                logger.warning("  Calibration dy=%d → FAILED (no cursor height)", step)
                continue

            displacement = max(0, extent_h - cursor_height)

            # Baseline position from first successful step
            if baseline_pos is None:
                bx = float(np.mean(xs)) + search_box[0]
                by = float(search_box[1] + bot_y) - cursor_height / 2.0
                baseline_pos = (bx, by)

            if displacement > 0.5:
                points_y.append(CalibrationPoint(dx_cmd=step, px_moved=displacement))
                logger.info("  Calibration dy=%d → %.1f px (%.2f px/dy)", step, displacement, displacement / step)
            else:
                logger.warning("  Calibration dy=%d → FAILED (displacement too small)", step)

        # --- Horizontal calibration (dx moves → points_x) ---
        logger.info("Horizontal calibration pass")
        cursor_width = cursor_height or 0  # cursor is roughly circular
        points_x: list[CalibrationPoint] = []

        for step in steps:
            await self._to_baseline()

            # Search region: bottom strip, width scaled by step size
            region_w = min(w, 100 + step * 8)
            search_box = (0, h - 200, region_w, h)

            f0 = await get_frame()

            # Test move RIGHT (positive dx)
            await self._send_chunked_move(step, 0)
            await asyncio.sleep(SETTLE_S)

            f1 = await get_frame()

            binary, nz = self._diff_extent_in_region(f0, f1, search_box)
            if nz is None:
                logger.warning("  Calibration dx=%d → FAILED (no diff)", step)
                continue

            xs = nz[:, 0, 0]
            left_x = int(xs.min())
            right_x = int(xs.max())
            extent_w = right_x - left_x

            displacement = max(0, extent_w - cursor_width)

            if displacement > 0.5:
                points_x.append(CalibrationPoint(dx_cmd=step, px_moved=displacement))
                logger.info("  Calibration dx=%d → %.1f px (%.2f px/dx)", step, displacement, displacement / step)
            else:
                logger.warning("  Calibration dx=%d → FAILED (displacement too small)", step)

        self.calibration.points_x = points_x
        self.calibration.points_y = points_y
        self.calibration.cursor_height = cursor_height or 0
        self.calibration.baseline_pos = baseline_pos or (0.0, 0.0)
        self.calibration.calibrated_at = time.time()
        self._initialized = bool(points_x) or bool(points_y)

        logger.info(
            "Calibration complete: %d x-points, %d y-points, cursor_h=%d, baseline=(%.0f, %.0f)",
            len(points_x), len(points_y), cursor_height or 0, *(baseline_pos or (0, 0)),
        )
        return self.calibration

    # -- Movement planning ---------------------------------------------------

    def plan_path(self, dx_px: float, dy_px: float) -> list[tuple[int, int]]:
        """Plan movement from baseline to target as (dx_cmd, dy_cmd) steps.

        Uses separate calibration tables per axis. Largest calibrated
        steps first for each axis, then smaller for precision.
        """
        pts_x = self.calibration._points_for_axis("x")
        pts_y = self.calibration._points_for_axis("y")

        cal_x = sorted([(p.dx_cmd, p.px_moved) for p in pts_x], key=lambda x: x[1], reverse=True) if pts_x else []
        cal_y = sorted([(p.dx_cmd, p.px_moved) for p in pts_y], key=lambda x: x[1], reverse=True) if pts_y else []

        if not cal_x and not cal_y:
            if abs(dx_px) < 1 and abs(dy_px) < 1:
                return []
            return [(max(-MAX_STEP, min(MAX_STEP, int(round(dx_px)))), max(-MAX_STEP, min(MAX_STEP, int(round(dy_px)))))]

        steps: list[tuple[int, int]] = []
        remaining_x = dx_px
        remaining_y = dy_px

        # X axis: greedy largest steps first
        for cmd_size, px_per_cmd in cal_x:
            if px_per_cmd <= 0:
                continue
            while abs(remaining_x) >= px_per_cmd:
                sx = cmd_size if remaining_x > 0 else -cmd_size
                remaining_x -= px_per_cmd if remaining_x > 0 else -px_per_cmd
                steps.append((sx, 0))

        # Y axis: greedy largest steps first
        for cmd_size, px_per_cmd in cal_y:
            if px_per_cmd <= 0:
                continue
            while abs(remaining_y) >= px_per_cmd:
                sy = cmd_size if remaining_y > 0 else -cmd_size
                remaining_y -= px_per_cmd if remaining_y > 0 else -px_per_cmd
                steps.append((0, sy))

        # Remaining small amounts via interpolation
        if abs(remaining_x) > 1:
            cmd = self.calibration.px_to_cmd(abs(remaining_x), axis="x")
            steps.append((cmd if remaining_x > 0 else -cmd, 0))
        if abs(remaining_y) > 1:
            cmd = self.calibration.px_to_cmd(abs(remaining_y), axis="y")
            steps.append((0, cmd if remaining_y > 0 else -cmd))

        return steps

    # -- Post-move verification ----------------------------------------------

    def verify_position(self, f_baseline: np.ndarray, f_final: np.ndarray, target_x: float, target_y: float) -> CursorPosition | None:
        """Verify cursor ended up near target by diffing baseline vs final.

        Searches for diff candidates in a region around the expected target.
        Returns detected cursor position, or None if not found.
        """
        h, w = f_baseline.shape[:2]

        # Search region: generous box around target
        margin = 100
        x1 = max(0, int(target_x) - margin)
        y1 = max(0, int(target_y) - margin)
        x2 = min(w, int(target_x) + margin)
        y2 = min(h, int(target_y) + margin)
        search_box = (x1, y1, x2, y2)

        binary, nz = self._diff_extent_in_region(f_baseline, f_final, search_box)
        if nz is None:
            # Try wider search
            search_box = (0, 0, w, h)
            binary, nz = self._diff_extent_in_region(f_baseline, f_final, search_box)
            if nz is None:
                return None

        xs = nz[:, 0, 0]
        ys = nz[:, 0, 1]
        cx = float(np.mean(xs)) + search_box[0]
        cy = float(np.mean(ys)) + search_box[1]

        return CursorPosition(x=cx, y=cy, confidence=0.8)

    # -- Core movement -------------------------------------------------------

    async def move_to(self, target_x: float, target_y: float, get_frame: GetFrameFn, *, tolerance_px: float = VERIFY_TOLERANCE_PX) -> MoveResult:
        """Move cursor to target position (pixel coords).

        1. Reset to bottom-left → baseline
        2. Capture baseline frame
        3. Execute pre-calculated path
        4. Capture final frame
        5. Verify via diff — if too far, recalibrate + retry once

        Returns MoveResult with success/failure and final position.
        """
        if not self._initialized:
            logger.error("Cannot move — not calibrated")
            return MoveResult(success=False, error_px=float("inf"))

        commands_before = self._total_commands
        bx, by = self.calibration.baseline_pos
        dx_px = target_x - bx
        dy_px = target_y - by

        for attempt in range(2):
            recalibrated = attempt > 0

            # Reset to baseline
            await self._to_baseline()

            # Capture at baseline
            f_baseline = await get_frame()

            # Plan and execute
            path = self.plan_path(dx_px, dy_px)
            logger.info("Move attempt %d: baseline=(%.0f,%.0f) → target=(%.0f,%.0f), %d steps", attempt, bx, by, target_x, target_y, len(path))

            for dx_cmd, dy_cmd in path:
                await self._send_chunked_move(dx_cmd, dy_cmd)
                await asyncio.sleep(CHUNK_DELAY_S)

            await asyncio.sleep(SETTLE_S)

            # Capture final
            f_final = await get_frame()

            # Verify
            detected = self.verify_position(f_baseline, f_final, target_x, target_y)

            if detected is not None:
                error = math.sqrt((target_x - detected.x) ** 2 + (target_y - detected.y) ** 2)
                logger.info("  Detected at (%.0f, %.0f), error=%.1f px", detected.x, detected.y, error)

                if error <= tolerance_px:
                    return MoveResult(
                        success=True,
                        final_position=detected,
                        error_px=error,
                        iterations=attempt + 1,
                        total_commands=self._total_commands - commands_before,
                        recalibrated=recalibrated,
                    )
            else:
                error = float("inf")
                logger.warning("  Could not detect cursor after move")

            # First attempt failed — recalibrate and retry
            if attempt == 0:
                logger.info("  Verification failed (error=%.1f px > %.1f), recalibrating...", error, tolerance_px)
                await self.calibrate(get_frame)
                bx, by = self.calibration.baseline_pos
                dx_px = target_x - bx
                dy_px = target_y - by

        # Both attempts failed
        return MoveResult(
            success=False,
            final_position=detected if detected is not None else None,
            error_px=error,
            iterations=2,
            total_commands=self._total_commands - commands_before,
            recalibrated=True,
        )

    async def click_at(self, target_x: float, target_y: float, get_frame: GetFrameFn, **move_kwargs) -> MoveResult:
        """Move to target and click."""
        result = await self.move_to(target_x, target_y, get_frame, **move_kwargs)
        if result.success:
            cmd = HIDCommandMessage(action="click", params={"button": 1})
            await self.send_command(cmd)
            self._total_commands += 1
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _distribute(total: int, chunk_index: int, n_chunks: int) -> int:
    if n_chunks <= 0:
        return 0
    sign = 1 if total >= 0 else -1
    abs_total = abs(total)
    base = abs_total // n_chunks
    remainder = abs_total % n_chunks
    value = base + (1 if chunk_index < remainder else 0)
    return sign * value


def detect_purple_icon(frame: np.ndarray, *, min_area: int = 500, max_area: int = 50000) -> tuple[float, float] | None:
    """Detect the Amazon Luna icon (purple) and return its center."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lower_purple = np.array([120, 50, 50])
    upper_purple = np.array([165, 255, 255])
    mask = cv2.inRange(hsv, lower_purple, upper_purple)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_contour = None
    best_area = 0
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if min_area <= area <= max_area and area > best_area:
            best_contour = cnt
            best_area = area

    if best_contour is None:
        return None

    M = cv2.moments(best_contour)
    if M["m00"] < 1:
        return None

    return (M["m10"] / M["m00"], M["m01"] / M["m00"])
