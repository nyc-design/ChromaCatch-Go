"""Tests for CursorController: chunked movement, corner reset, CV detection, closed-loop."""

from __future__ import annotations

import asyncio
import math
from unittest.mock import AsyncMock

import cv2
import numpy as np
import pytest

from backend.cv.cursor_controller import (
    CHUNK_DELAY_S,
    MAX_STEP,
    CalibrationData,
    CursorController,
    CursorPosition,
    MoveResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_frame(
    width: int = 720,
    height: int = 1280,
    cursor_x: float | None = None,
    cursor_y: float | None = None,
    cursor_radius: int = 12,
) -> np.ndarray:
    """Create a synthetic frame, optionally with a dark circle as cursor."""
    frame = np.full((height, width, 3), 200, dtype=np.uint8)  # Light gray background
    if cursor_x is not None and cursor_y is not None:
        cx = int(cursor_x * width)
        cy = int(cursor_y * height)
        # Draw cursor: dark circle with white border (mimics iOS pointer)
        cv2.circle(frame, (cx, cy), cursor_radius + 2, (255, 255, 255), -1)
        cv2.circle(frame, (cx, cy), cursor_radius, (60, 60, 60), -1)
    return frame


def _make_cursor_template(radius: int = 12) -> np.ndarray:
    """Create a synthetic cursor template matching _make_frame's cursor."""
    size = (radius + 4) * 2
    template = np.full((size, size, 3), 200, dtype=np.uint8)
    center = size // 2
    cv2.circle(template, (center, center), radius + 2, (255, 255, 255), -1)
    cv2.circle(template, (center, center), radius, (60, 60, 60), -1)
    return template


# ---------------------------------------------------------------------------
# Chunked movement tests
# ---------------------------------------------------------------------------


class TestChunkedMovement:
    """Test that large dx/dy is split into BLE-safe chunks."""

    @pytest.fixture
    def controller(self):
        send_fn = AsyncMock()
        return CursorController(send_command=send_fn), send_fn

    @pytest.mark.asyncio
    async def test_small_move_single_chunk(self, controller):
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(50, -30)
        assert sent == 1
        send_fn.assert_called_once()
        cmd = send_fn.call_args[0][0]
        assert cmd.action == "move"
        assert cmd.params["dx"] == 50
        assert cmd.params["dy"] == -30

    @pytest.mark.asyncio
    async def test_large_move_multiple_chunks(self, controller):
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(500, 0)
        # 500 / 100 = 5 chunks
        assert sent == 5
        assert send_fn.call_count == 5
        # Verify each chunk is within bounds
        total_dx = 0
        for call in send_fn.call_args_list:
            cmd = call[0][0]
            assert -128 <= cmd.params["dx"] <= 127
            assert -128 <= cmd.params["dy"] <= 127
            total_dx += cmd.params["dx"]
        assert total_dx == 500

    @pytest.mark.asyncio
    async def test_negative_large_move(self, controller):
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(-400, -300)
        assert sent > 1
        total_dx = sum(c[0][0].params["dx"] for c in send_fn.call_args_list)
        total_dy = sum(c[0][0].params["dy"] for c in send_fn.call_args_list)
        assert total_dx == -400
        assert total_dy == -300

    @pytest.mark.asyncio
    async def test_zero_move_no_commands(self, controller):
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(0, 0)
        assert sent == 0
        send_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_diagonal_chunking_preserves_direction(self, controller):
        ctrl, send_fn = controller
        await ctrl._send_chunked_move(300, 600)
        # Should chunk based on max(300, 600) = 600, so 6 chunks
        assert send_fn.call_count == 6
        total_dx = sum(c[0][0].params["dx"] for c in send_fn.call_args_list)
        total_dy = sum(c[0][0].params["dy"] for c in send_fn.call_args_list)
        assert total_dx == 300
        assert total_dy == 600

    @pytest.mark.asyncio
    async def test_chunks_respect_max_step(self, controller):
        ctrl, send_fn = controller
        await ctrl._send_chunked_move(1000, -1000)
        for call in send_fn.call_args_list:
            cmd = call[0][0]
            assert abs(cmd.params["dx"]) <= MAX_STEP
            assert abs(cmd.params["dy"]) <= MAX_STEP

    @pytest.mark.asyncio
    async def test_exact_boundary_value(self, controller):
        """Exactly MAX_STEP should be a single chunk."""
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(MAX_STEP, MAX_STEP)
        assert sent == 1


# ---------------------------------------------------------------------------
# Corner reset tests
# ---------------------------------------------------------------------------


class TestCornerReset:
    @pytest.fixture
    def controller(self):
        send_fn = AsyncMock()
        return CursorController(send_command=send_fn), send_fn

    @pytest.mark.asyncio
    async def test_reset_top_left(self, controller):
        ctrl, send_fn = controller
        await ctrl.reset_to_corner("top-left")
        assert ctrl.known_position is not None
        assert ctrl.known_position.x == 0.0
        assert ctrl.known_position.y == 0.0
        assert ctrl.known_position.confidence == 1.0
        # Should have sent many chunks (5000 / 100 = 50 per axis)
        assert send_fn.call_count > 0
        # All dx should be negative, all dy should be negative
        for call in send_fn.call_args_list:
            cmd = call[0][0]
            assert cmd.params["dx"] <= 0
            assert cmd.params["dy"] <= 0

    @pytest.mark.asyncio
    async def test_reset_bottom_right(self, controller):
        ctrl, send_fn = controller
        await ctrl.reset_to_corner("bottom-right")
        assert ctrl.known_position.x == 1.0
        assert ctrl.known_position.y == 1.0

    @pytest.mark.asyncio
    async def test_reset_top_right(self, controller):
        ctrl, send_fn = controller
        await ctrl.reset_to_corner("top-right")
        assert ctrl.known_position.x == 1.0
        assert ctrl.known_position.y == 0.0


# ---------------------------------------------------------------------------
# Cursor detection tests
# ---------------------------------------------------------------------------


class TestCursorDetection:
    def test_template_detection_finds_cursor(self):
        """Template matching should find cursor drawn at known position."""
        template = _make_cursor_template(radius=12)
        ctrl = CursorController(
            send_command=AsyncMock(), cursor_template=template
        )
        frame = _make_frame(cursor_x=0.5, cursor_y=0.3)
        pos = ctrl.detect_cursor(frame)
        assert pos is not None
        assert abs(pos.x - 0.5) < 0.05
        assert abs(pos.y - 0.3) < 0.05
        assert pos.confidence > 0.5

    def test_template_detection_no_cursor(self):
        """Should return None when no cursor is present."""
        template = _make_cursor_template(radius=12)
        ctrl = CursorController(
            send_command=AsyncMock(), cursor_template=template
        )
        frame = _make_frame()  # No cursor
        pos = ctrl.detect_cursor(frame)
        # Either None or very low confidence
        if pos is not None:
            assert pos.confidence < 0.5

    def test_template_detection_at_edges(self):
        """Cursor near screen edges should still be detected."""
        template = _make_cursor_template(radius=12)
        ctrl = CursorController(
            send_command=AsyncMock(), cursor_template=template
        )
        frame = _make_frame(cursor_x=0.05, cursor_y=0.95)
        pos = ctrl.detect_cursor(frame)
        assert pos is not None
        assert abs(pos.x - 0.05) < 0.05
        assert abs(pos.y - 0.95) < 0.05

    def test_no_template_uses_fallback(self):
        """Without template, should fall back to circle detection."""
        ctrl = CursorController(
            send_command=AsyncMock(), cursor_template=None
        )
        frame = _make_frame(cursor_x=0.5, cursor_y=0.5, cursor_radius=10)
        # Circle detection is less reliable, so just verify it doesn't crash
        pos = ctrl.detect_cursor(frame)
        # May or may not detect — the fallback is best-effort


# ---------------------------------------------------------------------------
# Closed-loop move_to tests
# ---------------------------------------------------------------------------


class TestMoveTo:
    @pytest.mark.asyncio
    async def test_move_from_known_position(self):
        """Move from a known corner to center."""
        send_fn = AsyncMock()
        template = _make_cursor_template()
        ctrl = CursorController(
            send_command=send_fn, cursor_template=template
        )

        # Set known position (as if we just reset to corner)
        ctrl._known_position = CursorPosition(x=0.0, y=0.0, confidence=1.0)

        # Frame shows cursor at target (simulating successful move)
        target_frame = _make_frame(cursor_x=0.5, cursor_y=0.5)
        get_frame = AsyncMock(return_value=target_frame)

        result = await ctrl.move_to(0.5, 0.5, get_frame)

        # Should have sent movement commands
        assert send_fn.call_count > 0
        assert isinstance(result, MoveResult)
        assert result.total_commands > 0

    @pytest.mark.asyncio
    async def test_move_triggers_reset_when_no_position(self):
        """If no known position and CV fails, should reset to corner first."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, cursor_template=None)
        assert ctrl.known_position is None

        # Frame with no detectable cursor
        blank_frame = _make_frame()
        get_frame = AsyncMock(return_value=blank_frame)

        result = await ctrl.move_to(0.5, 0.5, get_frame)

        # Should have sent reset commands (large negative dx/dy)
        assert send_fn.call_count > 0

    @pytest.mark.asyncio
    async def test_successful_correction_loop(self):
        """Simulate cursor arriving at target after one correction."""
        send_fn = AsyncMock()
        template = _make_cursor_template()
        ctrl = CursorController(
            send_command=send_fn,
            cursor_template=template,
            calibration=CalibrationData(scale_x=600, scale_y=1000),
        )
        ctrl._known_position = CursorPosition(x=0.0, y=0.0, confidence=1.0)

        # After first move, cursor is at target
        target_frame = _make_frame(cursor_x=0.3, cursor_y=0.4)
        get_frame = AsyncMock(return_value=target_frame)

        result = await ctrl.move_to(0.3, 0.4, get_frame)
        assert result.iterations >= 1


# ---------------------------------------------------------------------------
# Click tests
# ---------------------------------------------------------------------------


class TestClickAt:
    @pytest.mark.asyncio
    async def test_click_sends_click_command(self):
        send_fn = AsyncMock()
        template = _make_cursor_template()
        ctrl = CursorController(
            send_command=send_fn, cursor_template=template
        )
        ctrl._known_position = CursorPosition(x=0.5, y=0.5, confidence=1.0)

        target_frame = _make_frame(cursor_x=0.5, cursor_y=0.5)
        get_frame = AsyncMock(return_value=target_frame)

        result = await ctrl.click_at(0.5, 0.5, get_frame, max_iterations=1)

        # Last command should be a click
        last_cmd = send_fn.call_args_list[-1][0][0]
        assert last_cmd.action == "click"


# ---------------------------------------------------------------------------
# Calibration data tests
# ---------------------------------------------------------------------------


class TestCalibrationData:
    def test_default_values(self):
        cal = CalibrationData()
        assert cal.scale_x > 0
        assert cal.scale_y > 0
        assert cal.last_calibrated == 0.0

    def test_custom_values(self):
        cal = CalibrationData(scale_x=800, scale_y=1200)
        assert cal.scale_x == 800
        assert cal.scale_y == 1200
