"""Full calibration + move to Luna. Simple open-loop approach."""

import asyncio
import math
import os
import sys

import cv2
import httpx
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")
CLIENT_ID = os.environ.get("CLIENT_ID", "ios-app")
OUT = "/workspaces/ChromaCatch-Go"
THRESHOLD = 25
MAX_STEP = 60
CALIBRATION_STEPS = [60, 40, 20, 10, 5]  # largest first


async def main():
    http = httpx.AsyncClient(base_url=BACKEND_URL, timeout=10)
    print(f"Clients: {(await http.get('/status')).json()['connected_clients']}")

    async def move(dx, dy):
        await http.post("/command", json={
            "client_id": CLIENT_ID, "action": "move", "params": {"dx": dx, "dy": dy}
        })

    async def frame(label=""):
        r = await http.get(f"/clients/{CLIENT_ID}/frame")
        img = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if label:
            cv2.imwrite(os.path.join(OUT, f"{label}.jpg"), img)
        return img

    async def push_bottom_left():
        for _ in range(37):
            await move(-60, 60)
            await asyncio.sleep(0.012)
        await asyncio.sleep(1.5)

    async def to_baseline():
        await move(10, -30)     # right 10, up 30
        await asyncio.sleep(1.0)

    async def chunked_move(dx, dy):
        dominant = max(abs(dx), abs(dy))
        if dominant == 0:
            return
        n = math.ceil(dominant / MAX_STEP)
        for i in range(n):
            cdx = round(dx * (i + 1) / n) - round(dx * i / n)
            cdy = round(dy * (i + 1) / n) - round(dy * i / n)
            await move(cdx, cdy)
            if i < n - 1:
                await asyncio.sleep(0.012)

    # ==============================================================
    # CALIBRATION
    # ==============================================================
    print("\n========== CALIBRATION ==========")
    test_frame = await frame()
    h_frame, w_frame = test_frame.shape[:2]
    print(f"Frame: {w_frame}x{h_frame}")

    cursor_height = None
    calibration = {}
    baseline_pos = None  # (x, y) in frame pixels

    for step in CALIBRATION_STEPS:
        print(f"\n  dy={step}: ", end="", flush=True)
        await push_bottom_left()
        await to_baseline()

        region_h = min(400, 100 + step * 6)
        search_box = (0, h_frame - region_h, w_frame // 3, h_frame)

        f0 = await frame()

        await chunked_move(0, -step)  # test move UP
        await asyncio.sleep(1.0)
        f1 = await frame()

        # Diff in search region
        gray0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
        gray1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        absdiff = cv2.absdiff(gray0, gray1)
        x1, y1, x2, y2 = search_box
        roi = absdiff[y1:y2, x1:x2]
        _, binary = cv2.threshold(roi, THRESHOLD, 255, cv2.THRESH_BINARY)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k)
        nz = cv2.findNonZero(binary)

        if nz is None:
            print("FAILED (no diff)")
            continue

        ys = nz[:, 0, 1]
        xs = nz[:, 0, 0]
        top_y = int(ys.min())
        bot_y = int(ys.max())
        extent_h = bot_y - top_y

        # First step: get cursor height from blob analysis
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

        if cursor_height is None:
            print("FAILED (no cursor height)")
            continue

        displacement = max(0, extent_h - cursor_height)
        calibration[step] = displacement

        # Baseline position: bottom of the diff region is where cursor was BEFORE moving up
        # The "before" cursor sits at the bottom of the diff extent
        # In absolute frame coords: y1 + bot_y, and x centroid
        if baseline_pos is None:
            baseline_x = float(np.mean(xs)) + x1
            baseline_y = float(y1 + bot_y) - cursor_height / 2  # center of bottom blob
            baseline_pos = (baseline_x, baseline_y)

        print(f"{displacement}px ({displacement / step:.2f} px/dy)")

    print(f"\n  Cursor height: {cursor_height}px")
    print(f"  Baseline: ({baseline_pos[0]:.0f}, {baseline_pos[1]:.0f})")
    print(f"  Vertical calibration: {calibration}")

    if not calibration:
        print("Vertical calibration failed")
        await http.aclose()
        return

    # ==============================================================
    # HORIZONTAL CALIBRATION
    # ==============================================================
    print("\n========== HORIZONTAL CALIBRATION ==========")
    cursor_width = cursor_height  # cursor is roughly circular
    calibration_x = {}

    for step in CALIBRATION_STEPS:
        print(f"\n  dx={step}: ", end="", flush=True)
        await push_bottom_left()
        await to_baseline()

        region_w = min(w_frame, 100 + step * 8)
        search_box = (0, h_frame - 200, region_w, h_frame)

        f0 = await frame()

        await chunked_move(step, 0)  # test move RIGHT
        await asyncio.sleep(1.0)
        f1 = await frame()

        gray0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
        gray1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
        absdiff = cv2.absdiff(gray0, gray1)
        x1, y1, x2, y2 = search_box
        roi = absdiff[y1:y2, x1:x2]
        _, binary = cv2.threshold(roi, THRESHOLD, 255, cv2.THRESH_BINARY)
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, k)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, k)
        nz = cv2.findNonZero(binary)

        if nz is None:
            print("FAILED (no diff)")
            continue

        xs = nz[:, 0, 0]
        left_x = int(xs.min())
        right_x = int(xs.max())
        extent_w = right_x - left_x

        displacement = max(0, extent_w - cursor_width)
        if displacement > 0.5:
            calibration_x[step] = displacement
            print(f"{displacement}px ({displacement / step:.2f} px/dx)")
        else:
            print("FAILED (displacement too small)")

    print(f"\n  Horizontal calibration: {calibration_x}")

    if not calibration_x:
        print("Horizontal calibration failed, falling back to vertical values")
        calibration_x = calibration

    # Build sorted calibration tables per axis
    cal_table_x = sorted(calibration_x.items())
    cal_table_y = sorted(calibration.items())

    def px_to_cmd_axis(target_px, cal_table):
        """Interpolate: pixel displacement → HID command units."""
        if target_px <= 0:
            return 0
        for i in range(len(cal_table) - 1):
            cmd_lo, px_lo = cal_table[i]
            cmd_hi, px_hi = cal_table[i + 1]
            if px_lo <= target_px <= px_hi:
                t = (target_px - px_lo) / (px_hi - px_lo) if px_hi != px_lo else 0
                return max(1, round(cmd_lo + t * (cmd_hi - cmd_lo)))
        if target_px > cal_table[-1][1]:
            cmd_last, px_last = cal_table[-1]
            return max(1, round(cmd_last * target_px / px_last))
        cmd_first, px_first = cal_table[0]
        return max(1, round(cmd_first * target_px / px_first))

    def plan_path(dx_px, dy_px):
        """Plan movement from baseline to target as list of (dx_cmd, dy_cmd) steps.

        Uses separate calibration tables per axis. Largest steps first.
        """
        steps_out = []
        remaining_x = dx_px
        remaining_y = dy_px

        # X axis: largest steps first
        for cmd_size in reversed([c for c, _ in cal_table_x]):
            px_per_cmd = calibration_x[cmd_size]
            if px_per_cmd <= 0:
                continue
            while abs(remaining_x) >= px_per_cmd:
                sx = cmd_size if remaining_x > 0 else -cmd_size
                remaining_x -= px_per_cmd if remaining_x > 0 else -px_per_cmd
                steps_out.append((sx, 0))

        # Y axis: largest steps first
        for cmd_size in reversed([c for c, _ in cal_table_y]):
            px_per_cmd = calibration[cmd_size]
            if px_per_cmd <= 0:
                continue
            while abs(remaining_y) >= px_per_cmd:
                sy = cmd_size if remaining_y > 0 else -cmd_size
                remaining_y -= px_per_cmd if remaining_y > 0 else -px_per_cmd
                steps_out.append((0, sy))

        # Remaining small amounts
        if abs(remaining_x) > 1:
            cmd = px_to_cmd_axis(abs(remaining_x), cal_table_x)
            steps_out.append((cmd if remaining_x > 0 else -cmd, 0))
        if abs(remaining_y) > 1:
            cmd = px_to_cmd_axis(abs(remaining_y), cal_table_y)
            steps_out.append((0, cmd if remaining_y > 0 else -cmd))

        return steps_out

    # ==============================================================
    # DETECT LUNA
    # ==============================================================
    print("\n========== DETECTING LUNA ==========")
    await http.post("/command", json={
        "client_id": CLIENT_ID, "action": "key",
        "params": {"key": "h", "modifiers": "gui"},
        "command_type": "keyboard"
    })
    await asyncio.sleep(1.5)

    home = await frame("luna_detect_frame")
    hsv = cv2.cvtColor(home, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (120, 50, 50), (165, 255, 255))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area < 500:
            continue
        M = cv2.moments(c)
        if M["m00"] == 0:
            continue
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]
        if best is None or area > best[2]:
            best = (cx, cy, area)

    if best:
        target_x, target_y = int(best[0]), int(best[1])
        print(f"  Luna at ({target_x}, {target_y})")
    else:
        target_x, target_y = w_frame // 2, h_frame // 2
        print(f"  Luna not found, using center ({target_x}, {target_y})")

    # ==============================================================
    # PLAN & EXECUTE
    # ==============================================================
    bx, by = baseline_pos
    dx_px = target_x - bx
    dy_px = target_y - by
    print(f"\n========== MOVE: baseline ({bx:.0f},{by:.0f}) → target ({target_x},{target_y}) ==========")
    print(f"  Delta: ({dx_px:.0f}, {dy_px:.0f}) px")

    path = plan_path(dx_px, dy_px)
    print(f"  Path: {len(path)} steps")
    for i, (dx, dy) in enumerate(path):
        print(f"    [{i}] dx={dx}, dy={dy}")

    # Execute: reset → baseline → capture baseline → path → capture final
    print("\n  Executing...")
    await push_bottom_left()
    await to_baseline()

    f_baseline = await frame("frame_baseline")
    print("  Captured baseline frame")

    for dx_cmd, dy_cmd in path:
        await chunked_move(dx_cmd, dy_cmd)
        await asyncio.sleep(0.012)

    await asyncio.sleep(1.0)

    f_final = await frame("frame_final")
    print("  Captured final frame")

    # ==============================================================
    # VERIFY: grayscale diff baseline vs final
    # ==============================================================
    print("\n========== VERIFICATION ==========")
    gray_bl = cv2.cvtColor(f_baseline, cv2.COLOR_BGR2GRAY)
    gray_fn = cv2.cvtColor(f_final, cv2.COLOR_BGR2GRAY)
    verify_diff = cv2.absdiff(gray_bl, gray_fn)
    cv2.imwrite(os.path.join(OUT, "verify_diff_grayscale.jpg"), verify_diff)
    print("  Saved: verify_diff_grayscale.jpg")

    # Search for cursor in region around target
    margin = 100
    vx1 = max(0, target_x - margin)
    vy1 = max(0, target_y - margin)
    vx2 = min(w_frame, target_x + margin)
    vy2 = min(h_frame, target_y + margin)
    roi = verify_diff[vy1:vy2, vx1:vx2]
    _, vbin = cv2.threshold(roi, THRESHOLD, 255, cv2.THRESH_BINARY)
    vk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    vbin = cv2.morphologyEx(vbin, cv2.MORPH_CLOSE, vk)
    vbin = cv2.morphologyEx(vbin, cv2.MORPH_OPEN, vk)
    vnz = cv2.findNonZero(vbin)

    if vnz is not None:
        vxs = vnz[:, 0, 0]
        vys = vnz[:, 0, 1]
        detected_x = float(np.mean(vxs)) + vx1
        detected_y = float(np.mean(vys)) + vy1
        error = math.sqrt((target_x - detected_x) ** 2 + (target_y - detected_y) ** 2)
        print(f"  Cursor detected at ({detected_x:.0f}, {detected_y:.0f})")
        print(f"  Target was ({target_x}, {target_y})")
        print(f"  Error: {error:.1f} px")
    else:
        # Wider search
        vnz_full = cv2.findNonZero(cv2.threshold(verify_diff, THRESHOLD, 255, cv2.THRESH_BINARY)[1])
        if vnz_full is not None:
            vxs = vnz_full[:, 0, 0]
            vys = vnz_full[:, 0, 1]
            detected_x = float(np.mean(vxs))
            detected_y = float(np.mean(vys))
            error = math.sqrt((target_x - detected_x) ** 2 + (target_y - detected_y) ** 2)
            print(f"  Cursor detected (wide search) at ({detected_x:.0f}, {detected_y:.0f})")
            print(f"  Target was ({target_x}, {target_y})")
            print(f"  Error: {error:.1f} px")
        else:
            print("  No diff detected — cursor may not have moved from baseline")

    # Annotated final
    annotated = f_final.copy()
    cv2.circle(annotated, (target_x, target_y), 30, (0, 255, 0), 3)
    cv2.putText(annotated, "TARGET", (target_x + 35, target_y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
    cv2.circle(annotated, (int(bx), int(by)), 10, (0, 255, 255), 2)
    cv2.putText(annotated, "BASELINE", (int(bx) + 15, int(by)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
    if vnz is not None:
        cv2.circle(annotated, (int(detected_x), int(detected_y)), 20, (0, 0, 255), 3)
        cv2.putText(annotated, "DETECTED", (int(detected_x) + 25, int(detected_y)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
    cv2.imwrite(os.path.join(OUT, "final_annotated.jpg"), annotated)
    print("  Saved: final_annotated.jpg")

    await http.aclose()


if __name__ == "__main__":
    asyncio.run(main())
