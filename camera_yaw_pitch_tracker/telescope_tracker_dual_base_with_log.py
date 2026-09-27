#!/usr/bin/env python3
"""Four-AprilTag telescope tracker - fast macOS version with dual-tag base fusion.

Expected printed markers (AprilTag 36h11, 150 mm black-square side):
  ID 0 = BASE PRIMARY (existing tag)
  ID 1 = TELESCOPE
  ID 2 = FLOOR / ROOM REFERENCE
  ID 3 = BASE AUXILIARY (new tag, mounted roughly 90 degrees to ID 0)

The two base tags are treated as one rigid object. You do NOT need to measure the
exact offset or angle between them. After mounting ID 3 rigidly to the same yaw
platform as ID 0, run the tracker with both visible and press B. The program
observes their rigid transform for a few seconds and saves it. Thereafter either
base tag can recover the base pose, and when both are visible their estimates are
fused.

Recommended setup:
  FLOOR ID 2: fixed to the room/floor, face up if convenient.
  BASE IDs 0 + 3: same rigid rotating base; put them on perpendicular faces when
                  possible (for example one flat, one vertical).
  TELESCOPE ID 1: rigidly attached to the telescope/cradle.

Keys in tracking mode:
  B : learn/relearn the rigid transform between base tags 0 and 3 (~2-3 sec)
  C : clear the saved base-tag relationship
  Z : set current telescope position as yaw=0, pitch=0
  X : clear saved mechanical zero
  Q / ESC : quit

Rolling output:
  telescope_angles.csv is atomically rewritten every 0.5 seconds and contains
  only the last 10 samples with columns: datetime,yaw,pitch. If the current
  pose cannot be measured, yaw/pitch are written as nan so readers can detect
  a temporarily invalid measurement rather than consuming stale coordinates.

Performance defaults are kept Mac-friendly: 1920x1080 @ 25 fps, AVFoundation,
grayscale detection, SUBPIX corner refinement, AprilTag quad decimation, and a
small preview. Camera intrinsics are automatically scaled if calibration was
performed at another resolution.

Portrait mode: use --display-rotate 90 (or -90/270) to rotate only the preview
window. The scene is rotated first and the HUD/UI is drawn afterwards, so the
status text stays upright. Detection and calibration keep using the raw
landscape frame, so the saved intrinsics stay valid and no recalibration is
needed. Moving the camera does not invalidate the camera calibration either;
only a change of resolution, focus, or the frame fed to detection does. Re-run
--calibrate whenever you actually want to redo it.

Capture defaults to 1920x1080. If processing cannot keep up, lower
--width/--height (for example 1280x720) or raise --quad-decimate. Some cameras
only offer a few modes; the actual negotiated size is printed at startup and the
saved camera calibration is rescaled to match.

Mirroring: a reflected (mirrored) feed makes every AprilTag undecodable even
though the image looks fine. --mirror auto (default) samples a few frames and
keeps whichever of none/horizontal/vertical decodes the most tags, so you do not
have to guess per camera. The resolved value is printed and shown in the HUD.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import time
import platform
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

TAG_SIZE_M = 0.150
BASE_ID = 0
TELESCOPE_ID = 1
FLOOR_ID = 2
BASE_AUX_ID = 3

CHECKER_INNER = (9, 6)
CHECKER_SQUARE_M = 0.020


def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64).reshape(3)
    n = float(np.linalg.norm(v))
    if n < 1e-12:
        return v.copy()
    return v / n



def make_T(R: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Homogeneous transform T_a_b from rotation/translation."""
    T = np.eye(4, dtype=np.float64)
    T[:3, :3] = np.asarray(R, dtype=np.float64).reshape(3, 3)
    T[:3, 3] = np.asarray(t, dtype=np.float64).reshape(3)
    return T


def invert_T(T: np.ndarray) -> np.ndarray:
    R = T[:3, :3]
    t = T[:3, 3]
    out = np.eye(4, dtype=np.float64)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out


def average_rotations(rotations, weights=None) -> np.ndarray:
    """Chordal SO(3) mean via weighted SVD projection."""
    if len(rotations) == 1:
        return np.asarray(rotations[0], dtype=np.float64).copy()
    if weights is None:
        weights = np.ones(len(rotations), dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    weights = np.maximum(weights, 1e-12)
    M = np.zeros((3, 3), dtype=np.float64)
    for R, w in zip(rotations, weights):
        M += float(w) * np.asarray(R, dtype=np.float64)
    U, _, Vt = np.linalg.svd(M)
    Rm = U @ Vt
    if np.linalg.det(Rm) < 0:
        U[:, -1] *= -1.0
        Rm = U @ Vt
    return Rm


def average_transforms(transforms, weights=None) -> np.ndarray:
    if len(transforms) == 1:
        return np.asarray(transforms[0], dtype=np.float64).copy()
    if weights is None:
        weights = np.ones(len(transforms), dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    weights = np.maximum(weights, 1e-12)
    weights /= weights.sum()
    R = average_rotations([T[:3, :3] for T in transforms], weights)
    t = sum(float(w) * T[:3, 3] for T, w in zip(transforms, weights))
    return make_T(R, t)


def rotation_angle_deg(R: np.ndarray) -> float:
    c = float(np.clip((np.trace(R) - 1.0) * 0.5, -1.0, 1.0))
    return math.degrees(math.acos(c))


def pose_quality_weight(R: np.ndarray, reproj_err: float) -> float:
    """Favor face-on, low-reprojection-error tag observations."""
    # Tag +Z is its surface normal. Its camera-Z component approaches 1 when
    # the tag is viewed face-on. abs() makes this robust to normal convention.
    face_on = max(abs(float(R[2, 2])), 0.15)
    err = max(float(reproj_err), 0.08)
    return (face_on * face_on) / (err * err)


def pose_to_T(pose: dict) -> np.ndarray:
    return make_T(pose["R"], pose["tvec"])

def unwrap_deg(previous: float | None, wrapped: float) -> float:
    """Unwrap a [-180,180) angle into a continuous angle."""
    if previous is None:
        return wrapped
    prev_wrapped = (previous + 180.0) % 360.0 - 180.0
    delta = (wrapped - prev_wrapped + 180.0) % 360.0 - 180.0
    return previous + delta


def load_angle_history(path: Path, max_rows: int = 10):
    """Load existing angle rows so the file remains a rolling last-N history across restarts."""
    rows = deque(maxlen=max_rows)
    if not path.exists():
        return rows
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if {"datetime", "yaw", "pitch"}.issubset(row):
                    rows.append((row["datetime"], row["yaw"], row["pitch"]))
    except Exception as exc:
        print(f"WARNING: could not read existing angle log {path}: {exc}")
    return rows


def write_angle_history_atomic(path: Path, rows) -> None:
    """Atomically replace the CSV so another process never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["datetime", "yaw", "pitch"])
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def marker_object_points(size_m: float) -> np.ndarray:
    """Object points in the order required by SOLVEPNP_IPPE_SQUARE."""
    h = size_m / 2.0
    return np.array(
        [
            [-h, +h, 0.0],  # top-left
            [+h, +h, 0.0],  # top-right
            [+h, -h, 0.0],  # bottom-right
            [-h, -h, 0.0],  # bottom-left
        ],
        dtype=np.float32,
    )


def estimate_square_pose(
    corners: np.ndarray,
    K: np.ndarray,
    D: np.ndarray,
    size_m: float = TAG_SIZE_M,
):
    """Return (R_camera_tag, rvec, tvec, reprojection_error_px)."""
    obj = marker_object_points(size_m)
    img = np.asarray(corners, dtype=np.float32).reshape(4, 2)

    result = cv2.solvePnPGeneric(
        obj,
        img,
        K,
        D,
        flags=cv2.SOLVEPNP_IPPE_SQUARE,
    )

    ok, rvecs, tvecs = result[:3]
    if not ok or len(rvecs) == 0:
        return None

    reproj = result[3] if len(result) > 3 else None
    candidates = []

    for i, (rv, tv) in enumerate(zip(rvecs, tvecs)):
        rv = np.asarray(rv, dtype=np.float64).reshape(3, 1)
        tv = np.asarray(tv, dtype=np.float64).reshape(3, 1)

        if tv[2, 0] <= 0:
            continue

        err = (
            float(np.asarray(reproj[i]).ravel()[0])
            if reproj is not None
            else 0.0
        )
        candidates.append((err, rv, tv))

    if not candidates:
        return None

    err, rvec, tvec = min(candidates, key=lambda x: x[0])
    R, _ = cv2.Rodrigues(rvec)
    return R, rvec, tvec, err


def scale_intrinsics(
    K: np.ndarray,
    from_size: tuple[int, int],
    to_size: tuple[int, int],
) -> np.ndarray:
    fw, fh = from_size
    tw, th = to_size
    if (fw, fh) == (tw, th):
        return K.copy()

    sx, sy = tw / fw, th / fh
    Ks = K.copy()
    Ks[0, 0] *= sx
    Ks[0, 2] *= sx
    Ks[1, 1] *= sy
    Ks[1, 2] *= sy
    return Ks


def load_calibration(path: Path, image_size: tuple[int, int]):
    if path.exists():
        data = np.load(path)
        K = data["K"].astype(np.float64)
        D = data["D"].astype(np.float64)
        cw = int(data["width"])
        ch = int(data["height"])
        K = scale_intrinsics(K, (cw, ch), image_size)
        return K, D, True

    # Good enough only to test detection. Calibrate for real angle measurements.
    w, h = image_size
    f = 0.9 * w
    K = np.array(
        [[f, 0, w / 2.0], [0, f, h / 2.0], [0, 0, 1]],
        dtype=np.float64,
    )
    D = np.zeros((5, 1), dtype=np.float64)
    return K, D, False


def open_camera(index: int, width: int, height: int, fps: float = 15.0):
    # On macOS, explicitly using AVFoundation is usually lower-latency than
    # letting OpenCV choose a backend. Fall back to the default backend if needed.
    if platform.system() == "Darwin":
        cap = cv2.VideoCapture(index, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(index)
    else:
        cap = cv2.VideoCapture(index)

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera {index}")

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"Camera: {actual_w}x{actual_h} @ {actual_fps:.1f} fps")
    return cap


def grab_frame(cap, timeout: float = 5.0):
    """Read one frame, retrying briefly while the stream starts up.

    Some cameras (notably AVFoundation devices asked for a resolution they do
    not support) open successfully but take one or more attempts before they
    return their first frame. A single cap.read() right after opening can
    therefore fail even though the camera is working.
    """
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        ok, frame = cap.read()
        if ok and frame is not None:
            return frame
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def correct_frame(frame: np.ndarray, mirror: str) -> np.ndarray:
    """Undo camera mirroring before calibration/detection.

    Many webcam/virtual-camera feeds are horizontally mirrored. AprilTags are
    not reflection-invariant, so a mirrored feed makes every valid tag fail to
    decode even though it looks visually correct.
    """
    if mirror == "horizontal":
        return cv2.flip(frame, 1)
    if mirror == "vertical":
        return cv2.flip(frame, 0)
    return frame


def rotate_for_display(frame: np.ndarray, degrees: int) -> np.ndarray:
    """Rotate the displayed image only; detection/calibration stay untouched.

    This is purely cosmetic: the processing frame keeps its original
    orientation, so the saved camera intrinsics remain valid and you never need
    to recalibrate just to view the feed in portrait.
    """
    d = int(degrees) % 360
    if d == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if d == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if d == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def choose_mirror_for_tags(
    cap,
    detector,
    candidates=("none", "horizontal", "vertical"),
    samples: int = 12,
):
    """Pick the mirror setting that decodes the most AprilTags.

    A reflected feed makes every AprilTag undecodable, so this removes the
    guesswork of matching --mirror to a particular webcam.
    """
    counts = {c: 0 for c in candidates}
    for _ in range(samples):
        frame = grab_frame(cap, timeout=0.5)
        if frame is None:
            continue
        for c in candidates:
            gray = cv2.cvtColor(correct_frame(frame, c), cv2.COLOR_BGR2GRAY)
            _, ids, _ = detector.detectMarkers(gray)
            if ids is not None:
                counts[c] += len(ids)
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        best = "none"
    return best, counts


def choose_mirror_for_chessboard(
    cap,
    candidates=("none", "horizontal", "vertical"),
    samples: int = 10,
):
    """Pick the mirror setting that finds the calibration checkerboard most often."""
    counts = {c: 0 for c in candidates}
    for _ in range(samples):
        frame = grab_frame(cap, timeout=0.5)
        if frame is None:
            continue
        for c in candidates:
            gray = cv2.cvtColor(correct_frame(frame, c), cv2.COLOR_BGR2GRAY)
            found, _ = cv2.findChessboardCornersSB(
                gray, CHECKER_INNER, flags=cv2.CALIB_CB_NORMALIZE_IMAGE
            )
            if found:
                counts[c] += 1
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        best = "none"
    return best, counts


def calibration_mode(args):
    cap = open_camera(args.camera, args.width, args.height, args.fps)

    obj_template = np.zeros(
        (CHECKER_INNER[0] * CHECKER_INNER[1], 3), np.float32
    )
    obj_template[:, :2] = np.mgrid[
        0 : CHECKER_INNER[0], 0 : CHECKER_INNER[1]
    ].T.reshape(-1, 2)
    obj_template *= CHECKER_SQUARE_M

    objpoints = []
    imgpoints = []
    last_corners = None
    last_size = None

    mirror = args.mirror
    if mirror == "auto":
        mirror, counts = choose_mirror_for_chessboard(cap)
        print(f"Auto mirror: chose '{mirror}' (checkerboard hits: {counts})")

    print("Calibration mode")
    print("Move/tilt the checkerboard around the image.")
    print("SPACE = capture view, S = solve/save, Q = quit")

    while True:
        frame = grab_frame(cap, timeout=0.5)
        if frame is None:
            continue

        frame = correct_frame(frame, mirror)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        last_size = (gray.shape[1], gray.shape[0])

        found, corners = cv2.findChessboardCornersSB(
            gray,
            CHECKER_INNER,
            flags=cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE,
        )
        last_corners = corners if found else None

        if found:
            cv2.drawChessboardCorners(frame, CHECKER_INNER, corners, found)

        # Rotate the scene first, then draw the UI so the text stays upright.
        display = rotate_for_display(frame, args.display_rotate)

        cv2.putText(
            display,
            f"Calibration views: {len(imgpoints)}",
            (20, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (0, 255, 0),
            2,
        )
        cv2.putText(
            display,
            "SPACE=capture  S=solve/save  Q=quit",
            (20, 68),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (255, 255, 255),
            2,
        )
        if not found:
            cv2.putText(
                display,
                "Checkerboard not detected",
                (20, 100),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 0, 255),
                2,
            )

        cv2.imshow("Camera calibration", display)
        key = cv2.waitKey(1) & 0xFF

        if key == ord(" ") and last_corners is not None:
            objpoints.append(obj_template.copy())
            imgpoints.append(last_corners.astype(np.float32))
            print(f"Captured view {len(imgpoints)}")

        elif key in (ord("s"), ord("S")):
            if len(imgpoints) < 10:
                print("Need at least 10 views; 15-25 varied views is better.")
                continue

            rms, K, D, _, _ = cv2.calibrateCamera(
                objpoints,
                imgpoints,
                last_size,
                None,
                None,
            )
            np.savez(
                args.calibration,
                K=K,
                D=D,
                width=last_size[0],
                height=last_size[1],
                rms=rms,
            )
            print(
                f"Saved {args.calibration}; RMS reprojection error = {rms:.4f} px"
            )
            break

        elif key in (27, ord("q"), ord("Q")):
            break

    cap.release()
    cv2.destroyAllWindows()


def yaw_from_floor_base(
    R_floor_base: np.ndarray,
    R_floor_base_zero: np.ndarray,
) -> float:
    """Yaw change from zero, assuming FLOOR tag lies flat, face upward.

    R_floor_base transforms BASE-frame vectors into FLOOR coordinates.
    If the base yaws around the floor tag's +Z axis:
        R_current = Rz(yaw) @ R_zero
    therefore:
        R_delta = R_current @ R_zero.T ~= Rz(yaw)
    """
    R_delta = R_floor_base @ R_floor_base_zero.T
    return math.degrees(math.atan2(R_delta[1, 0], R_delta[0, 0]))


def telescope_elevation_in_base(R_base_scope: np.ndarray) -> float:
    """Telescope elevation from TELESCOPE tag +X direction in BASE coordinates.

    Mount convention:
      BASE tag top edge = physically up  -> BASE +Y is up
      TELESCOPE tag right edge = objective/front -> TELESCOPE +X is tube direction
    """
    tube_dir_base = unit(R_base_scope[:, 0])
    return math.degrees(
        math.asin(float(np.clip(tube_dir_base[1], -1.0, 1.0)))
    )


def tracking_mode(args):
    cap = open_camera(args.camera, args.width, args.height, args.fps)
    frame = grab_frame(cap, timeout=10.0)
    if frame is None:
        cap.release()
        raise RuntimeError(
            "Camera opened but did not return a frame. Check that the camera is "
            "not in use by another app and that this terminal has camera access "
            "(System Settings > Privacy & Security > Camera)."
        )

    frame = correct_frame(frame, args.mirror)
    h, w = frame.shape[:2]
    K, D, calibrated = load_calibration(Path(args.calibration), (w, h))

    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    params = cv2.aruco.DetectorParameters()
    params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
    params.cornerRefinementWinSize = 5
    params.cornerRefinementMaxIterations = 15
    params.cornerRefinementMinAccuracy = 0.05
    params.aprilTagQuadDecimate = args.quad_decimate
    params.aprilTagMinClusterPixels = 5
    detector = cv2.aruco.ArucoDetector(dictionary, params)

    # A reflected feed makes AprilTags undecodable, so when --mirror is "auto"
    # (the default) test the candidates on a few live frames and keep whichever
    # decodes the most tags.
    mirror = args.mirror
    if mirror == "auto":
        mirror, counts = choose_mirror_for_tags(cap, detector)
        print(f"Auto mirror: chose '{mirror}' (tag hits: {counts})")

    # Mechanical zero.
    zero_path = Path(args.zero_file)
    Rfb0 = None
    pitch_elev0 = None
    if zero_path.exists():
        try:
            z = np.load(zero_path)
            Rfb0 = z["Rfb0"].astype(np.float64)
            pitch_elev0 = float(z["pitch_elev0"])
            print(f"Loaded mechanical zero from {zero_path}")
        except Exception:
            print(f"Ignoring incompatible zero file {zero_path}; press Z to recreate it.")

    # Rigid relationship between base tag 0 and auxiliary base tag 3.
    # T_0_3 means BASE0 <- BASE3.
    pair_path = Path(args.base_pair_file)
    T_0_3 = None
    if pair_path.exists():
        try:
            pair = np.load(pair_path)
            T_0_3 = pair["T_0_3"].astype(np.float64)
            print(f"Loaded dual-base calibration from {pair_path}")
        except Exception:
            print(f"Ignoring incompatible base-pair file {pair_path}; press B to recreate it.")

    pair_collecting = False
    pair_samples = []
    pair_started = 0.0

    yaw_unwrapped = None
    yaw_smooth = None
    pitch_smooth = None
    last_print = 0.0

    # Rolling inter-process angle output. The entire 10-row CSV is replaced
    # atomically every 0.5 s (configurable), so a polling reader always sees a
    # complete snapshot. Missing current angles are written as NaN rather than
    # silently publishing stale coordinates.
    angles_path = Path(args.angles_file)
    angle_history = load_angle_history(angles_path, args.angle_history)
    last_angle_write = time.monotonic() - args.angle_interval
    print(
        f"Angle output: {angles_path} every {args.angle_interval:.3f}s "
        f"(last {args.angle_history} rows)"
    )

    if not calibrated:
        print("WARNING: no camera calibration found; angles are only approximate.")
        print("Run this script with --calibrate first.")

    print("Tracking IDs: FLOOR=2, BASE0=0, BASE-AUX=3, TELESCOPE=1")
    if T_0_3 is None:
        print("After mounting base tag 3 rigidly to the same base, show IDs 0+3 and press B once.")

    while True:
        frame = grab_frame(cap, timeout=0.5)
        if frame is None:
            continue

        loop_start = time.perf_counter()
        frame = correct_frame(frame, mirror)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        corners, ids, _ = detector.detectMarkers(gray)
        poses = {}

        if ids is not None:
            cv2.aruco.drawDetectedMarkers(frame, corners, ids)
            for c, marker_id in zip(corners, ids.flatten().tolist()):
                if marker_id not in (FLOOR_ID, BASE_ID, BASE_AUX_ID, TELESCOPE_ID):
                    continue
                pose = estimate_square_pose(c, K, D, TAG_SIZE_M)
                if pose is None:
                    continue
                R, rvec, tvec, err = pose
                poses[marker_id] = {"R": R, "rvec": rvec, "tvec": tvec, "err": err}
                if args.draw_axes:
                    cv2.drawFrameAxes(frame, K, D, rvec, tvec, TAG_SIZE_M * 0.45, 2)

        # Learn the exact rigid transform between the two base tags from camera
        # observations. This means the user does not need to measure their offset
        # or achieve a perfect 90-degree mount.
        if pair_collecting and BASE_ID in poses and BASE_AUX_ID in poses:
            p0 = poses[BASE_ID]
            p3 = poses[BASE_AUX_ID]
            if p0["err"] <= args.pair_max_reproj and p3["err"] <= args.pair_max_reproj:
                T_c_0 = pose_to_T(p0)
                T_c_3 = pose_to_T(p3)
                pair_samples.append(invert_T(T_c_0) @ T_c_3)

            if len(pair_samples) >= args.pair_samples:
                # First average, then reject any unusually noisy sample and average again.
                rough = average_transforms(pair_samples)
                residuals = []
                for Ti in pair_samples:
                    d = invert_T(rough) @ Ti
                    residuals.append((np.linalg.norm(d[:3, 3]), rotation_angle_deg(d[:3, :3])))
                trans = np.array([r[0] for r in residuals])
                ang = np.array([r[1] for r in residuals])
                tlim = max(np.median(trans) * 3.0, 0.003)
                alim = max(np.median(ang) * 3.0, 0.5)
                kept = [Ti for Ti, (dt, da) in zip(pair_samples, residuals) if dt <= tlim and da <= alim]
                if len(kept) < max(8, args.pair_samples // 3):
                    kept = pair_samples
                T_0_3 = average_transforms(kept)
                np.savez(pair_path, T_0_3=T_0_3)

                # Report observed repeatability of the learned relation.
                dt_mm, da_deg = [], []
                for Ti in kept:
                    d = invert_T(T_0_3) @ Ti
                    dt_mm.append(np.linalg.norm(d[:3, 3]) * 1000.0)
                    da_deg.append(rotation_angle_deg(d[:3, :3]))
                print(
                    f"\nSaved base-tag relationship to {pair_path} using {len(kept)} samples; "
                    f"scatter ~{np.std(dt_mm):.2f} mm / {np.std(da_deg):.3f} deg"
                )
                pair_collecting = False
                pair_samples = []

        # Build CAMERA <- BASE0 from any visible base tag. When both are visible,
        # fuse the two independent estimates. ID0 remains the definition of the
        # base coordinate frame; ID3 is simply an auxiliary observation.
        base_candidates = []
        base_weights = []
        base_sources = []

        if BASE_ID in poses:
            p0 = poses[BASE_ID]
            base_candidates.append(pose_to_T(p0))
            base_weights.append(pose_quality_weight(p0["R"], p0["err"]))
            base_sources.append("0")

        if BASE_AUX_ID in poses and T_0_3 is not None:
            p3 = poses[BASE_AUX_ID]
            T_c_3 = pose_to_T(p3)
            # T_c_0 = T_c_3 @ T_3_0
            T_c_0_from_3 = T_c_3 @ invert_T(T_0_3)
            base_candidates.append(T_c_0_from_3)
            base_weights.append(pose_quality_weight(p3["R"], p3["err"]))
            base_sources.append("3")

        T_c_base = average_transforms(base_candidates, base_weights) if base_candidates else None
        Rcb = T_c_base[:3, :3] if T_c_base is not None else None

        have_floor_base = FLOOR_ID in poses and Rcb is not None
        have_base_scope = Rcb is not None and TELESCOPE_ID in poses
        yaw = None
        pitch = None

        if Rfb0 is not None and pitch_elev0 is not None:
            if have_floor_base:
                Rcf = poses[FLOOR_ID]["R"]
                Rfb = Rcf.T @ Rcb
                yaw_wrapped = yaw_from_floor_base(Rfb, Rfb0) * args.yaw_sign + args.yaw_offset
                yaw_unwrapped = unwrap_deg(yaw_unwrapped, yaw_wrapped)
                yaw = yaw_unwrapped

            if have_base_scope:
                Rct = poses[TELESCOPE_ID]["R"]
                Rbt = Rcb.T @ Rct
                elev = telescope_elevation_in_base(Rbt)
                pitch = (elev - pitch_elev0) * args.pitch_sign + args.pitch_offset

            a = float(np.clip(args.alpha, 0.0, 1.0))
            if yaw is not None:
                yaw_smooth = yaw if yaw_smooth is None else a * yaw + (1.0 - a) * yaw_smooth
            if pitch is not None:
                pitch_smooth = pitch if pitch_smooth is None else a * pitch + (1.0 - a) * pitch_smooth

            now = time.time()
            if now - last_print >= 0.1:
                yt = f"{yaw_smooth:8.3f}" if yaw_smooth is not None else " missing"
                pt = f"{pitch_smooth:8.3f}" if pitch_smooth is not None else " missing"
                src = "+".join(base_sources) if base_sources else "none"
                print(f"yaw={yt} deg   pitch={pt} deg   base={src:>3}", end="\r", flush=True)
                last_print = now

        # Publish one timestamped sample at a fixed cadence for other processes.
        # Require angles from this frame; if a required tag is missing, publish
        # NaN instead of reusing an old smoothed value as if it were current.
        log_now = time.monotonic()
        if log_now - last_angle_write >= args.angle_interval:
            timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
            current_yaw = yaw_smooth if yaw is not None and yaw_smooth is not None else float("nan")
            current_pitch = pitch_smooth if pitch is not None and pitch_smooth is not None else float("nan")
            angle_history.append(
                (
                    timestamp,
                    f"{current_yaw:.6f}" if math.isfinite(current_yaw) else "nan",
                    f"{current_pitch:.6f}" if math.isfinite(current_pitch) else "nan",
                )
            )
            try:
                write_angle_history_atomic(angles_path, angle_history)
            except Exception as exc:
                print(f"\nWARNING: could not write angle log {angles_path}: {exc}")
            last_angle_write = log_now

        # ---------------- HUD ----------------
        # Rotate the scene (with detection overlays) first, then draw the UI on
        # the already-rotated canvas so the text stays upright in portrait mode.
        display = rotate_for_display(frame, args.display_rotate)
        dh, dw = display.shape[:2]

        statuses = [
            ("FLOOR 2", FLOOR_ID),
            ("BASE 0", BASE_ID),
            ("BASE 3", BASE_AUX_ID),
            ("SCOPE 1", TELESCOPE_ID),
        ]
        x = 20
        for label, marker_id in statuses:
            ok_marker = marker_id in poses
            cv2.putText(
                display, f"{label}: {'OK' if ok_marker else 'MISS'}", (x, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52,
                (0, 255, 0) if ok_marker else (0, 0, 255), 2,
            )
            x += 165

        pair_text = "BASE PAIR: CALIBRATED" if T_0_3 is not None else "BASE PAIR: press B with tags 0+3 visible"
        if pair_collecting:
            pair_text = f"LEARNING BASE PAIR: {len(pair_samples)}/{args.pair_samples}"
        cv2.putText(
            display, pair_text, (20, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.58,
            (80, 255, 80) if T_0_3 is not None and not pair_collecting else (0, 220, 255), 2,
        )

        source_text = "base pose: " + ("fused 0+3" if len(base_sources) == 2 else (f"tag {base_sources[0]}" if base_sources else "MISSING"))
        cv2.putText(display, source_text, (20, 84), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (220, 220, 220), 1)

        if Rfb0 is None:
            cv2.putText(
                display, "After base-pair calibration, put mount at 0/0 and press Z",
                (20, 116), cv2.FONT_HERSHEY_SIMPLEX, 0.68, (0, 220, 255), 2,
            )
        else:
            yaw_text = f"YAW   {yaw_smooth:8.2f} deg" if yaw_smooth is not None else "YAW   -- floor/base missing --"
            pitch_text = f"PITCH {pitch_smooth:8.2f} deg" if pitch_smooth is not None else "PITCH -- base/scope missing --"
            cv2.putText(display, yaw_text, (20, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                        (50, 255, 50) if yaw_smooth is not None else (0, 180, 255), 2)
            cv2.putText(display, pitch_text, (20, 158), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                        (50, 255, 50) if pitch_smooth is not None else (0, 180, 255), 2)

        yerr = 190
        for label, marker_id in statuses:
            if marker_id in poses:
                cv2.putText(display, f"{label} reproj {poses[marker_id]['err']:.2f}px",
                            (20, yerr), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (220, 220, 220), 1)
                yerr += 19

        cv2.putText(display, f"mirror: {mirror}",
                    (20, dh - 70), cv2.FONT_HERSHEY_SIMPLEX,
                    0.48, (220, 220, 220), 1)
        cv2.putText(display, "CALIBRATED" if calibrated else "UNCALIBRATED APPROXIMATION",
                    (20, dh - 46), cv2.FONT_HERSHEY_SIMPLEX, 0.50,
                    (180, 255, 180) if calibrated else (0, 180, 255), 1)
        cv2.putText(display, "B=learn base pair  C=clear pair  Z=zero  X=clear zero  Q=quit",
                    (20, dh - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (255, 255, 255), 1)

        elapsed = max(time.perf_counter() - loop_start, 1e-6)
        proc_fps = 1.0 / elapsed
        cv2.putText(display, f"processing {proc_fps:4.1f} fps", (dw - 240, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1)

        preview = display
        if args.preview_width > 0 and preview.shape[1] > args.preview_width:
            scale = args.preview_width / float(preview.shape[1])
            preview = cv2.resize(preview, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        cv2.imshow("Telescope yaw / pitch - dual base tag", preview)
        key = cv2.waitKey(1) & 0xFF

        if args.max_fps > 0:
            target = 1.0 / args.max_fps
            spent = time.perf_counter() - loop_start
            if spent < target:
                time.sleep(target - spent)

        if key in (27, ord("q"), ord("Q")):
            break

        elif key in (ord("b"), ord("B")):
            if BASE_ID not in poses or BASE_AUX_ID not in poses:
                print("\nCannot learn base pair: both base tags 0 and 3 must be visible.")
                continue
            pair_collecting = True
            pair_samples = []
            pair_started = time.time()
            print(f"\nLearning rigid relationship between base tags 0 and 3 ({args.pair_samples} good frames)...")

        elif key in (ord("c"), ord("C")):
            T_0_3 = None
            pair_collecting = False
            pair_samples = []
            if pair_path.exists():
                pair_path.unlink()
            print("\nBase-tag relationship cleared. Press B with both base tags visible to relearn it.")

        elif key in (ord("z"), ord("Z")):
            if FLOOR_ID not in poses or Rcb is None or TELESCOPE_ID not in poses:
                print("\nCannot zero: floor, at least one calibrated base tag, and telescope must be visible.")
                continue
            Rcf = poses[FLOOR_ID]["R"]
            Rct = poses[TELESCOPE_ID]["R"]
            Rfb0 = Rcf.T @ Rcb
            Rbt0 = Rcb.T @ Rct
            pitch_elev0 = telescope_elevation_in_base(Rbt0)
            np.savez(zero_path, Rfb0=Rfb0, pitch_elev0=pitch_elev0)
            yaw_unwrapped = None
            yaw_smooth = None
            pitch_smooth = None
            print(f"\nSaved mechanical zero to {zero_path}")

        elif key in (ord("x"), ord("X")):
            Rfb0 = None
            pitch_elev0 = None
            yaw_unwrapped = None
            yaw_smooth = None
            pitch_smooth = None
            if zero_path.exists():
                zero_path.unlink()
            print("\nMechanical zero cleared.")

    cap.release()
    cv2.destroyAllWindows()
    print()


def main():
    p = argparse.ArgumentParser(
        description="Track telescope yaw/pitch with floor + telescope + dual rigid base AprilTags"
    )
    p.add_argument("--camera", type=int, default=0, help="OpenCV camera index")
    p.add_argument("--width", type=int, default=1920, help="capture width; default 1920")
    p.add_argument("--height", type=int, default=1080, help="capture height; default 1080")
    p.add_argument("--fps", type=float, default=25.0, help="requested camera FPS")
    p.add_argument("--max-fps", type=float, default=10.0, help="max processing/display FPS; 0=unlimited")
    p.add_argument("--preview-width", type=int, default=1100, help="resize display only; 0=full size")
    p.add_argument("--quad-decimate", type=float, default=2.0, help="AprilTag quad decimation")
    p.add_argument("--draw-axes", action="store_true", help="draw 3D axes (off by default for speed)")
    p.add_argument(
        "--mirror", choices=("auto", "horizontal", "vertical", "none"), default="auto",
        help="undo camera mirroring before detection/calibration; 'auto' (default) "
             "picks whichever setting decodes the most tags (a reflected feed "
             "makes AprilTags undecodable)",
    )
    p.add_argument(
        "--display-rotate", "--rotate", dest="display_rotate",
        type=int, choices=(0, 90, 180, -90, 270), default=0,
        help="rotate only the displayed preview for portrait mode "
             "(90 = clockwise, -90/270 = counter-clockwise); "
             "detection/calibration are unaffected",
    )
    p.add_argument("--calibration", default="camera_calibration.npz")
    p.add_argument("--zero-file", default="mount_zero_dual_base.npz")
    p.add_argument("--base-pair-file", default="base_tag_pair_0_3.npz")
    p.add_argument(
        "--angles-file", default="telescope_angles.csv",
        help="rolling CSV output for other processes (datetime,yaw,pitch)",
    )
    p.add_argument(
        "--angle-interval", type=float, default=0.5,
        help="seconds between angle CSV snapshots; default 0.5",
    )
    p.add_argument(
        "--angle-history", type=int, default=10,
        help="number of samples retained in the rolling angle CSV; default 10",
    )
    p.add_argument("--pair-samples", type=int, default=35, help="good frames used when B learns base-tag relation")
    p.add_argument("--pair-max-reproj", type=float, default=2.0, help="max per-tag reprojection error accepted while learning pair")
    p.add_argument("--calibrate", action="store_true", help="run checkerboard camera calibration mode")
    p.add_argument("--alpha", type=float, default=0.25, help="EMA smoothing factor 0..1")
    p.add_argument("--yaw-sign", type=float, default=1.0, choices=(-1.0, 1.0))
    p.add_argument("--pitch-sign", type=float, default=1.0, choices=(-1.0, 1.0))
    p.add_argument("--yaw-offset", type=float, default=0.0)
    p.add_argument("--pitch-offset", type=float, default=0.0)
    args = p.parse_args()
    if args.angle_interval <= 0:
        p.error("--angle-interval must be > 0")
    if args.angle_history < 1:
        p.error("--angle-history must be >= 1")

    if args.calibrate:
        calibration_mode(args)
    else:
        tracking_mode(args)


if __name__ == "__main__":
    main()
