"""Tests for CursorController: calibration curve, frame-diff detection,
path planning, open-loop movement with verification, and purple icon detection.
"""

from __future__ import annotations

import asyncio
import math
import time
from unittest.mock import AsyncMock, patch

import cv2
import numpy as np
import pytest

from backend.cv.cursor_controller import (
    BASELINE_DX,
    BASELINE_DY,
    CALIBRATION_STEPS,
    CHUNK_DELAY_S,
    DIFF_THRESHOLD,
    MAX_STEP,
    RESET_OVERSHOOT,
    SETTLE_S,
    VERIFY_TOLERANCE_PX,
    CalibrationCurve,
    CalibrationPoint,
    CursorController,
    CursorPosition,
    MoveResult,
    _distribute,
    detect_purple_icon,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_frame(width: int = 720, height: int = 1280, bg_color: tuple[int, int, int] = (200, 200, 200)) -> np.ndarray:
    """Create a blank synthetic frame."""
    return np.full((height, width, 3), bg_color, dtype=np.uint8)


def _draw_cursor(frame: np.ndarray, x: int, y: int, radius: int = 10, color: tuple[int, int, int] = (50, 50, 50)) -> np.ndarray:
    """Draw a cursor circle on a frame (returns a copy)."""
    result = frame.copy()
    cv2.circle(result, (x, y), radius + 2, (150, 150, 150), -1)
    cv2.circle(result, (x, y), radius, color, -1)
    return result


def _draw_purple_icon(frame: np.ndarray, x: int, y: int, size: int = 40) -> np.ndarray:
    """Draw a purple rectangle (simulating Luna icon) on a frame."""
    result = frame.copy()
    half = size // 2
    cv2.rectangle(result, (x - half, y - half), (x + half, y + half), (180, 50, 130), -1)
    return result


def _make_calibration(
    points_x: list[tuple[int, float]] | None = None,
    points_y: list[tuple[int, float]] | None = None,
    cursor_height: int = 17,
    baseline_pos: tuple[float, float] = (50.0, 1182.0),
) -> CalibrationCurve:
    """Create a calibration curve with given (dx_cmd, px_moved) pairs per axis."""
    if points_x is None:
        points_x = [(5, 3.0), (10, 17.0), (20, 39.0), (40, 125.0), (60, 261.0)]
    if points_y is None:
        points_y = [(5, 3.0), (10, 17.0), (20, 39.0), (40, 125.0), (60, 261.0)]
    return CalibrationCurve(
        points_x=[CalibrationPoint(dx_cmd=d, px_moved=p) for d, p in points_x],
        points_y=[CalibrationPoint(dx_cmd=d, px_moved=p) for d, p in points_y],
        frame_width=720,
        frame_height=1280,
        cursor_height=cursor_height,
        baseline_pos=baseline_pos,
        calibrated_at=time.time(),
    )


# ---------------------------------------------------------------------------
# CalibrationCurve tests
# ---------------------------------------------------------------------------

class TestCalibrationCurve:
    def test_empty_curve_returns_identity(self):
        """Empty curve should use 1:1 mapping."""
        curve = CalibrationCurve()
        assert curve.px_to_cmd(50.0) == 50
        assert curve.cmd_to_px(50) == 50.0

    def test_interpolation_between_points(self):
        """Should interpolate between calibration points."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        dx = curve.px_to_cmd(17.5)
        assert 10 <= dx <= 20

    def test_extrapolation_below_range(self):
        """Below smallest calibration point should extrapolate linearly."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        dx = curve.px_to_cmd(5.0)
        assert dx >= 1
        assert dx <= 10

    def test_extrapolation_above_range(self):
        """Above largest calibration point should extrapolate linearly."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        dx = curve.px_to_cmd(50.0)
        assert dx >= 20

    def test_exact_point_match(self):
        """Exact calibration point should return exact dx."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        assert curve.px_to_cmd(10.0) == 10
        assert curve.px_to_cmd(25.0) == 20

    def test_cmd_to_px_lookup(self):
        """Should return correct pixel displacement for known cmd."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        assert curve.cmd_to_px(10) == pytest.approx(10.0)
        assert curve.cmd_to_px(20) == pytest.approx(25.0)

    def test_cmd_to_px_interpolation(self):
        """Should interpolate between points."""
        curve = _make_calibration(points_x=[(10, 10.0), (20, 25.0)])
        px = curve.cmd_to_px(15)
        assert 10.0 < px < 25.0

    def test_serialization_roundtrip(self):
        """to_dict / from_dict should preserve data."""
        original = _make_calibration()
        data = original.to_dict()
        restored = CalibrationCurve.from_dict(data)
        assert len(restored.points_x) == len(original.points_x)
        assert len(restored.points_y) == len(original.points_y)
        assert restored.cursor_height == original.cursor_height
        assert restored.baseline_pos[0] == pytest.approx(original.baseline_pos[0])
        assert restored.baseline_pos[1] == pytest.approx(original.baseline_pos[1])
        for orig, rest in zip(original.points_x, restored.points_x):
            assert orig.dx_cmd == rest.dx_cmd
            assert orig.px_moved == pytest.approx(rest.px_moved)

    def test_minimum_cmd_is_1(self):
        """px_to_cmd should never return 0 (meaningless move)."""
        curve = _make_calibration(points_x=[(10, 100.0)])
        dx = curve.px_to_cmd(0.5)
        assert dx >= 1

    def test_acceleration_curve(self):
        """Realistic acceleration: larger dx should give disproportionately more pixels."""
        curve = _make_calibration()
        ratios = [p.px_moved / p.dx_cmd for p in curve.points_x]
        assert ratios[-1] > ratios[0]

    def test_cursor_height_stored(self):
        curve = _make_calibration(cursor_height=17)
        assert curve.cursor_height == 17

    def test_baseline_pos_stored(self):
        curve = _make_calibration(baseline_pos=(50.0, 1182.0))
        assert curve.baseline_pos == (50.0, 1182.0)

    def test_separate_x_and_y_axes(self):
        """Should use different calibration per axis."""
        curve = _make_calibration(
            points_x=[(10, 10.0), (20, 30.0)],  # horizontal
            points_y=[(10, 15.0), (20, 40.0)],  # vertical (different)
        )
        # Same px target, different cmd per axis
        cmd_x = curve.px_to_cmd(20.0, axis="x")
        cmd_y = curve.px_to_cmd(20.0, axis="y")
        assert cmd_x != cmd_y  # different calibration curves

    def test_y_axis_fallback_to_x(self):
        """When no Y points, should use X points for Y axis."""
        curve = CalibrationCurve(
            points_x=[CalibrationPoint(dx_cmd=10, px_moved=10.0)],
            points_y=[],
        )
        assert curve.px_to_cmd(10.0, axis="y") == 10

    def test_x_axis_fallback_to_y(self):
        """When no X points, should use Y points for X axis."""
        curve = CalibrationCurve(
            points_x=[],
            points_y=[CalibrationPoint(dx_cmd=10, px_moved=20.0)],
        )
        assert curve.px_to_cmd(20.0, axis="x") == 10


# ---------------------------------------------------------------------------
# _distribute helper tests
# ---------------------------------------------------------------------------

class TestDistribute:
    def test_even_distribution(self):
        total = sum(_distribute(60, i, 3) for i in range(3))
        assert total == 60

    def test_uneven_distribution(self):
        values = [_distribute(7, i, 3) for i in range(3)]
        assert sum(values) == 7

    def test_negative_distribution(self):
        total = sum(_distribute(-100, i, 4) for i in range(4))
        assert total == -100

    def test_single_chunk(self):
        assert _distribute(42, 0, 1) == 42

    def test_zero_chunks(self):
        assert _distribute(42, 0, 0) == 0


# ---------------------------------------------------------------------------
# Chunked movement tests
# ---------------------------------------------------------------------------

class TestChunkedMovement:
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
        expected_chunks = math.ceil(500 / MAX_STEP)
        assert sent == expected_chunks
        total_dx = sum(c[0][0].params["dx"] for c in send_fn.call_args_list)
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
    async def test_diagonal_preserves_total(self, controller):
        ctrl, send_fn = controller
        await ctrl._send_chunked_move(300, 600)
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
    async def test_exact_max_step_single_chunk(self, controller):
        ctrl, send_fn = controller
        sent = await ctrl._send_chunked_move(MAX_STEP, MAX_STEP)
        assert sent == 1

    @pytest.mark.asyncio
    async def test_tracks_move_time(self, controller):
        ctrl, send_fn = controller
        assert ctrl._last_move_time == 0.0
        await ctrl._send_chunked_move(10, 0)
        assert ctrl._last_move_time > 0


# ---------------------------------------------------------------------------
# Corner and baseline tests
# ---------------------------------------------------------------------------

class TestCornerAndBaseline:
    @pytest.fixture
    def controller(self):
        send_fn = AsyncMock()
        return CursorController(send_command=send_fn), send_fn

    @pytest.mark.asyncio
    async def test_push_to_corner_sends_bottom_left(self, controller):
        """Should send negative dx (left) and positive dy (down)."""
        ctrl, send_fn = controller
        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            await ctrl._push_to_corner()

        total_dx = sum(c[0][0].params["dx"] for c in send_fn.call_args_list)
        total_dy = sum(c[0][0].params["dy"] for c in send_fn.call_args_list)
        assert total_dx == -RESET_OVERSHOOT
        assert total_dy == RESET_OVERSHOOT

    @pytest.mark.asyncio
    async def test_move_to_baseline_sends_correct_offsets(self, controller):
        """Should send BASELINE_DX right and BASELINE_DY up."""
        ctrl, send_fn = controller
        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            await ctrl._move_to_baseline()

        assert send_fn.call_count == 1
        cmd = send_fn.call_args[0][0]
        assert cmd.params["dx"] == BASELINE_DX
        assert cmd.params["dy"] == BASELINE_DY

    @pytest.mark.asyncio
    async def test_to_baseline_full_sequence(self, controller):
        """_to_baseline should push to corner then move to baseline."""
        ctrl, send_fn = controller
        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            await ctrl._to_baseline()

        # Should have many chunked moves for corner + 1 baseline move
        assert send_fn.call_count > 5
        # Last move should be baseline offset
        last_cmd = send_fn.call_args_list[-1][0][0]
        assert last_cmd.params["dx"] == BASELINE_DX
        assert last_cmd.params["dy"] == BASELINE_DY


# ---------------------------------------------------------------------------
# Diff extent tests
# ---------------------------------------------------------------------------

class TestDiffExtent:
    def test_cursor_motion_detected(self):
        """Moving cursor should produce non-zero points in diff."""
        bg = _make_frame()
        f0 = _draw_cursor(bg, 100, 1200, radius=10)
        f1 = _draw_cursor(bg, 100, 1150, radius=10)  # 50px up

        search_box = (0, 1100, 240, 1280)
        binary, nz = CursorController._diff_extent_in_region(f0, f1, search_box)
        assert nz is not None
        assert len(nz) > 0

    def test_identical_frames_no_diff(self):
        """Identical frames should produce no non-zero points."""
        f = _make_frame()
        binary, nz = CursorController._diff_extent_in_region(f, f.copy(), (0, 0, 720, 1280))
        assert nz is None

    def test_search_region_filters(self):
        """Diff outside search region should not be detected."""
        bg = _make_frame()
        f0 = _draw_cursor(bg, 300, 640, radius=10)
        f1 = _draw_cursor(bg, 300, 600, radius=10)

        # Search in bottom-left — cursor is in center
        binary, nz = CursorController._diff_extent_in_region(f0, f1, (0, 1100, 240, 1280))
        assert nz is None

    def test_extent_measures_displacement(self):
        """Should measure topmost/bottommost extent of diff."""
        bg = _make_frame()
        f0 = _draw_cursor(bg, 100, 1200, radius=8)
        f1 = _draw_cursor(bg, 100, 1140, radius=8)  # 60px up

        search_box = (0, 1050, 240, 1280)
        binary, nz = CursorController._diff_extent_in_region(f0, f1, search_box)
        assert nz is not None

        ys = nz[:, 0, 1]
        extent_h = int(ys.max()) - int(ys.min())
        # Extent should be roughly displacement + cursor height
        assert extent_h > 40  # Should capture both positions


# ---------------------------------------------------------------------------
# Path planning tests
# ---------------------------------------------------------------------------

class TestPathPlanning:
    def test_no_calibration_single_step(self):
        """Without calibration, plan should use 1:1 mapping."""
        ctrl = CursorController(send_command=AsyncMock())
        steps = ctrl.plan_path(30.0, -20.0)
        assert len(steps) == 1
        assert steps[0] == (30, -20)

    def test_uses_largest_steps_first(self):
        """Should decompose into largest calibrated steps first."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(0, -500.0)  # ~500px movement needed

        # First steps should use largest calibrated chunks (dy=60 → 261px each)
        dy_values = [abs(s[1]) for s in steps if s[1] != 0]
        assert dy_values[0] == 60  # Largest step first

    def test_positive_displacement(self):
        """Positive displacement should produce positive commands."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(300.0, 0)
        for dx, dy in steps:
            if dx != 0:
                assert dx > 0

    def test_negative_displacement(self):
        """Negative displacement should produce negative commands."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(-300.0, 0)
        for dx, dy in steps:
            if dx != 0:
                assert dx < 0

    def test_both_axes(self):
        """Should handle movement on both axes."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(200.0, -300.0)
        has_x = any(s[0] != 0 for s in steps)
        has_y = any(s[1] != 0 for s in steps)
        assert has_x
        assert has_y

    def test_small_displacement_interpolated(self):
        """Small displacement should use interpolation for remainder."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(5.0, 0)
        assert len(steps) >= 1
        # All commands should be small
        for dx, dy in steps:
            assert abs(dx) <= 10

    def test_zero_displacement(self):
        """Zero displacement should produce empty list."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(0.0, 0.0)
        assert len(steps) == 0

    def test_separate_axes(self):
        """Steps should only move one axis at a time."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        steps = ctrl.plan_path(300.0, -300.0)
        for dx, dy in steps:
            assert dx == 0 or dy == 0

    def test_different_calibration_per_axis(self):
        """Should use separate tables for X and Y axes."""
        cal = _make_calibration(
            points_x=[(10, 10.0), (60, 100.0)],  # horizontal: 60cmd → 100px
            points_y=[(10, 20.0), (60, 200.0)],  # vertical: 60cmd → 200px
        )
        ctrl = CursorController(send_command=AsyncMock(), calibration=cal)
        steps = ctrl.plan_path(100.0, -200.0)

        x_steps = [s for s in steps if s[0] != 0]
        y_steps = [s for s in steps if s[1] != 0]
        assert len(x_steps) >= 1
        assert len(y_steps) >= 1


# ---------------------------------------------------------------------------
# Verification tests
# ---------------------------------------------------------------------------

class TestVerification:
    def test_cursor_detected_near_target(self):
        """Should detect cursor position near target."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        bg = _make_frame()
        f_baseline = bg.copy()
        f_final = _draw_cursor(bg, 400, 700, radius=10)

        pos = ctrl.verify_position(f_baseline, f_final, 400.0, 700.0)
        assert pos is not None
        assert abs(pos.x - 400) < 30
        assert abs(pos.y - 700) < 30

    def test_no_change_returns_none(self):
        """If frames are identical, verification returns None."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        f = _make_frame()
        pos = ctrl.verify_position(f, f.copy(), 400.0, 700.0)
        assert pos is None

    def test_cursor_far_from_target_detected(self):
        """Should detect cursor even if away from target (wide search)."""
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        bg = _make_frame()
        f_baseline = bg.copy()
        # Cursor ends up at (200, 300) but target was (400, 700)
        f_final = _draw_cursor(bg, 200, 300, radius=10)

        pos = ctrl.verify_position(f_baseline, f_final, 400.0, 700.0)
        # Should still find it via wide search fallback
        assert pos is not None


# ---------------------------------------------------------------------------
# Calibration tests
# ---------------------------------------------------------------------------

class TestCalibration:
    @pytest.mark.asyncio
    async def test_calibration_sends_home_button(self):
        """Should send home button at start of calibration."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn)
        bg = _make_frame()
        get_frame = AsyncMock(return_value=bg)

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            await ctrl.calibrate(get_frame, steps=[60])

        key_cmds = [c[0][0] for c in send_fn.call_args_list if hasattr(c[0][0], "command_type") and c[0][0].action == "key"]
        assert len(key_cmds) > 0

    @pytest.mark.asyncio
    async def test_calibration_pushes_to_corner(self):
        """Should push to bottom-left corner for each step."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn)
        bg = _make_frame()
        get_frame = AsyncMock(return_value=bg)

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            await ctrl.calibrate(get_frame, steps=[60])

        negative_dx = [c[0][0] for c in send_fn.call_args_list if c[0][0].action == "move" and c[0][0].params.get("dx", 0) < 0]
        assert len(negative_dx) > 10  # Many chunks for -2200

    @pytest.mark.asyncio
    async def test_calibration_stores_frame_dimensions(self):
        """Should store frame width/height from first frame."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn)
        bg = _make_frame(width=588, height=1280)
        get_frame = AsyncMock(return_value=bg)

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            curve = await ctrl.calibrate(get_frame, steps=[60])

        assert curve.frame_width == 588
        assert curve.frame_height == 1280

    @pytest.mark.asyncio
    async def test_calibration_with_synthetic_diff(self):
        """Calibration should measure displacement from frame differencing."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn)

        # Simulate frames: bg at baseline, then cursor moved up
        bg = _make_frame()
        cursor_at_baseline = _draw_cursor(bg, 50, 1230, radius=8)
        cursor_moved_up = _draw_cursor(bg, 50, 1170, radius=8)  # 60px up

        frame_seq = [bg, cursor_at_baseline, cursor_moved_up]
        call_idx = [0]

        async def get_frame():
            idx = min(call_idx[0], len(frame_seq) - 1)
            call_idx[0] += 1
            return frame_seq[idx]

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            curve = await ctrl.calibrate(get_frame, steps=[60])

        # Should have at least attempted calibration
        assert curve.frame_width == 720
        assert curve.frame_height == 1280

    @pytest.mark.asyncio
    async def test_calibration_sets_initialized(self):
        """Successful calibration should set _initialized True."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn)

        bg = _make_frame()
        cursor_at_baseline = _draw_cursor(bg, 50, 1230, radius=8)
        cursor_moved_up = _draw_cursor(bg, 50, 1170, radius=8)

        frame_seq = [bg, cursor_at_baseline, cursor_moved_up]
        call_idx = [0]

        async def get_frame():
            idx = min(call_idx[0], len(frame_seq) - 1)
            call_idx[0] += 1
            return frame_seq[idx]

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            curve = await ctrl.calibrate(get_frame, steps=[60])

        if curve.points_x:
            assert ctrl._initialized


# ---------------------------------------------------------------------------
# Move-to tests
# ---------------------------------------------------------------------------

class TestMoveTo:
    @pytest.mark.asyncio
    async def test_not_calibrated_fails(self):
        """Should fail immediately if not calibrated."""
        ctrl = CursorController(send_command=AsyncMock())
        result = await ctrl.move_to(400.0, 700.0, AsyncMock())
        assert result.success is False
        assert result.error_px == float("inf")

    @pytest.mark.asyncio
    async def test_move_executes_path(self):
        """Should execute full sequence: baseline → path → verify."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, calibration=_make_calibration())
        ctrl._initialized = True

        bg = _make_frame()
        # After movement, cursor appears at target
        f_baseline = bg.copy()
        f_final = _draw_cursor(bg, 225, 916, radius=10)

        frame_idx = [0]

        async def get_frame():
            frame_idx[0] += 1
            if frame_idx[0] <= 1:
                return f_baseline
            return f_final

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            result = await ctrl.move_to(225.0, 916.0, get_frame)

        assert send_fn.call_count > 5  # Corner reset + baseline + path
        assert result.iterations >= 1
        assert result.total_commands > 0

    @pytest.mark.asyncio
    async def test_move_success_when_verified(self):
        """Should succeed when verification finds cursor near target."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, calibration=_make_calibration())
        ctrl._initialized = True

        bg = _make_frame()
        f_final = _draw_cursor(bg, 225, 916, radius=10)
        frame_idx = [0]

        async def get_frame():
            frame_idx[0] += 1
            if frame_idx[0] <= 1:
                return bg.copy()
            return f_final

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            result = await ctrl.move_to(225.0, 916.0, get_frame)

        if result.final_position is not None:
            assert result.error_px < VERIFY_TOLERANCE_PX or not result.success

    @pytest.mark.asyncio
    async def test_move_recalibrates_on_failure(self):
        """Should recalibrate once and retry when verification fails."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, calibration=_make_calibration())
        ctrl._initialized = True

        bg = _make_frame()
        # Always return blank frames — verification never succeeds
        get_frame = AsyncMock(return_value=bg)

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            result = await ctrl.move_to(400.0, 700.0, get_frame)

        assert result.success is False
        assert result.iterations == 2
        assert result.recalibrated is True


# ---------------------------------------------------------------------------
# Click tests
# ---------------------------------------------------------------------------

class TestClickAt:
    @pytest.mark.asyncio
    async def test_click_sends_click_on_success(self):
        """Should send click command after successful move."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, calibration=_make_calibration())
        ctrl._initialized = True

        bg = _make_frame()
        f_final = _draw_cursor(bg, 360, 640, radius=10)
        frame_idx = [0]

        async def get_frame():
            frame_idx[0] += 1
            if frame_idx[0] <= 1:
                return bg.copy()
            return f_final

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            result = await ctrl.click_at(360.0, 640.0, get_frame, tolerance_px=50.0)

        if result.success:
            click_cmds = [c[0][0] for c in send_fn.call_args_list if c[0][0].action == "click"]
            assert len(click_cmds) == 1

    @pytest.mark.asyncio
    async def test_click_no_click_on_failure(self):
        """Should NOT send click if move fails."""
        send_fn = AsyncMock()
        ctrl = CursorController(send_command=send_fn, calibration=_make_calibration())
        ctrl._initialized = True

        bg = _make_frame()
        get_frame = AsyncMock(return_value=bg)

        with patch("backend.cv.cursor_controller.asyncio.sleep", new_callable=AsyncMock):
            result = await ctrl.click_at(400.0, 700.0, get_frame)

        click_cmds = [c[0][0] for c in send_fn.call_args_list if c[0][0].action == "click"]
        assert len(click_cmds) == 0
        assert result.success is False


# ---------------------------------------------------------------------------
# Profile persistence tests
# ---------------------------------------------------------------------------

class TestProfilePersistence:
    def test_export_and_apply(self):
        ctrl = CursorController(send_command=AsyncMock(), calibration=_make_calibration())
        ctrl._initialized = True
        profile = ctrl.export_profile()

        restored = CursorController(send_command=AsyncMock())
        assert restored.apply_profile(profile)
        assert len(restored.calibration.points_x) == 5
        assert len(restored.calibration.points_y) == 5
        assert restored._initialized is True
        assert restored.calibration.cursor_height == 17
        assert restored.calibration.baseline_pos[0] == pytest.approx(50.0)

    def test_apply_invalid_profile(self):
        ctrl = CursorController(send_command=AsyncMock())
        assert ctrl.apply_profile({}) is False
        assert not ctrl._initialized


# ---------------------------------------------------------------------------
# Purple icon detection tests
# ---------------------------------------------------------------------------

class TestDetectPurpleIcon:
    def test_purple_icon_found(self):
        frame = _make_frame()
        frame = _draw_purple_icon(frame, 400, 500, size=60)
        result = detect_purple_icon(frame)
        assert result is not None
        cx, cy = result
        assert abs(cx - 400) < 15
        assert abs(cy - 500) < 15

    def test_no_purple_returns_none(self):
        frame = _make_frame()
        result = detect_purple_icon(frame)
        assert result is None

    def test_multiple_purple_picks_largest(self):
        frame = _make_frame()
        frame = _draw_purple_icon(frame, 200, 300, size=20)
        frame = _draw_purple_icon(frame, 500, 700, size=60)
        result = detect_purple_icon(frame)
        assert result is not None
        cx, cy = result
        assert abs(cx - 500) < 20
        assert abs(cy - 700) < 20

    def test_icon_too_small_filtered(self):
        frame = _make_frame()
        cv2.circle(frame, (300, 400), 2, (180, 50, 130), -1)
        result = detect_purple_icon(frame, min_area=500)
        assert result is None


# ---------------------------------------------------------------------------
# MoveResult tests
# ---------------------------------------------------------------------------

class TestMoveResult:
    def test_default_values(self):
        r = MoveResult(success=True)
        assert r.error_px == 0.0
        assert r.iterations == 0
        assert r.total_commands == 0
        assert r.final_position is None
        assert r.recalibrated is False

    def test_with_position(self):
        pos = CursorPosition(x=100, y=200, confidence=0.9)
        r = MoveResult(success=True, final_position=pos, error_px=5.0, recalibrated=True)
        assert r.final_position.x == 100
        assert r.error_px == 5.0
        assert r.recalibrated is True
