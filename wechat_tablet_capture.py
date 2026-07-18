import argparse
import csv
import hashlib
import io
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_ADB = (
    r"C:\Users\Q\AppData\Local\Microsoft\WinGet\Packages"
    r"\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\platform-tools\adb.exe"
)


def run_adb(adb, serial, args, capture=True, timeout=20):
    cmd = [adb]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def screenshot(adb, serial):
    result = run_adb(adb, serial, ["exec-out", "screencap", "-p"], timeout=30)
    data = result.stdout
    if not data.startswith(b"\x89PNG"):
        raise RuntimeError("ADB screencap did not return PNG data.")
    image = Image.open(io.BytesIO(data)).convert("RGB")
    return image


def parse_crop(value, width, height):
    if not value:
        if width > height:
            # Landscape tablet default: skip left conversation list/title/input/nav bars.
            ratios = (0.28, 0.08, 0.98, 0.88)
        else:
            ratios = (0.03, 0.08, 0.97, 0.86)
        return tuple(int(v) for v in (
            ratios[0] * width,
            ratios[1] * height,
            ratios[2] * width,
            ratios[3] * height,
        ))

    parts = [float(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop must have four comma-separated values.")
    if all(0 <= p <= 1 for p in parts):
        return tuple(int(v) for v in (
            parts[0] * width,
            parts[1] * height,
            parts[2] * width,
            parts[3] * height,
        ))
    return tuple(int(p) for p in parts)


def crop_image(image, box):
    left, top, right, bottom = box
    return image.crop((left, top, right, bottom))


def normalized_gray(image, max_width=900):
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    h, w = gray.shape[:2]
    if w > max_width:
        scale = max_width / w
        gray = cv2.resize(gray, (max_width, int(h * scale)), interpolation=cv2.INTER_AREA)
    return gray


def ssim_score(a, b):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2

    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)
    mu_a2 = mu_a * mu_a
    mu_b2 = mu_b * mu_b
    mu_ab = mu_a * mu_b

    sigma_a2 = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a2
    sigma_b2 = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b2
    sigma_ab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_ab

    score = ((2 * mu_ab + c1) * (2 * sigma_ab + c2)) / (
        (mu_a2 + mu_b2 + c1) * (sigma_a2 + sigma_b2 + c2)
    )
    return float(score.mean())


def phash(image):
    gray = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    low = dct[:8, :8]
    med = np.median(low[1:, 1:])
    bits = low > med
    return bits.flatten()


def hamming(a, b):
    return int(np.count_nonzero(a != b))


def file_digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def swipe(adb, serial, width, height, direction, duration_ms, retries=3):
    x = int(width * 0.64 if width > height else width * 0.50)
    if direction == "older":
        y1 = int(height * 0.35)
        y2 = int(height * 0.78)
    else:
        y1 = int(height * 0.78)
        y2 = int(height * 0.35)
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            run_adb(
                adb,
                serial,
                ["shell", "input", "swipe", str(x), str(y1), str(x), str(y2), str(duration_ms)],
                capture=True,
                timeout=10,
            )
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            time.sleep(1.0 * attempt)
    raise last_error


def save_image(image, path):
    image.save(path, optimize=True)


def main():
    parser = argparse.ArgumentParser(
        description="Capture the currently open WeChat chat by scrolling and screenshotting via ADB."
    )
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--crop", default="", help="Pixels or ratios: left,top,right,bottom.")
    parser.add_argument("--direction", choices=["older", "newer"], default="older")
    parser.add_argument("--max-shots", type=int, default=800)
    parser.add_argument("--stable-count", type=int, default=4)
    parser.add_argument("--ssim-threshold", type=float, default=0.985)
    parser.add_argument("--phash-threshold", type=int, default=4)
    parser.add_argument("--wait", type=float, default=1.15)
    parser.add_argument("--duration-ms", type=int, default=650)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()

    adb = args.adb
    if not os.path.exists(adb):
        raise FileNotFoundError(f"adb not found: {adb}")

    base = Path(args.out) if args.out else Path(__file__).resolve().parent / (
        "wechat_capture_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    frames = base / "frames"
    crops = base / "crops"
    debug = base / "debug"
    for folder in (frames, crops, debug):
        folder.mkdir(parents=True, exist_ok=True)

    first = screenshot(adb, args.serial)
    width, height = first.size
    box = parse_crop(args.crop, width, height)
    save_image(first, debug / "probe_full.png")
    save_image(crop_image(first, box), debug / "probe_crop.png")
    print(f"device screenshot size: {width}x{height}")
    print(f"crop box: {box}")
    print(f"output: {base}")
    if args.probe_only:
        print("probe-only complete")
        return 0

    metrics_path = base / "metrics.csv"
    last_gray = normalized_gray(crop_image(first, box))
    last_hash = phash(crop_image(first, box))
    stable = 0
    saved = 0

    first_path = frames / f"frame_{saved:05d}.png"
    first_crop_path = crops / f"crop_{saved:05d}.png"
    save_image(first, first_path)
    save_image(crop_image(first, box), first_crop_path)
    saved += 1

    with open(metrics_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "saved", "ssim", "phash_hamming", "stable", "frame_sha256"])
        writer.writerow([0, 1, "", "", stable, file_digest(first_path)])

        for i in range(1, args.max_shots + 1):
            swipe(adb, args.serial, width, height, args.direction, args.duration_ms)
            time.sleep(args.wait)

            img = screenshot(adb, args.serial)
            cropped = crop_image(img, box)
            gray = normalized_gray(cropped)
            current_hash = phash(cropped)
            score = ssim_score(last_gray, gray)
            dist = hamming(last_hash, current_hash)
            no_change = score >= args.ssim_threshold and dist <= args.phash_threshold

            if no_change:
                stable += 1
                temp_path = debug / f"stable_candidate_{i:05d}.png"
                save_image(img, temp_path)
                writer.writerow([i, 0, f"{score:.6f}", dist, stable, file_digest(temp_path)])
                print(f"{i:05d} stable={stable} ssim={score:.6f} phash={dist}")
                if stable >= args.stable_count:
                    print("stop: reached consecutive stable threshold")
                    break
                continue

            stable = 0
            frame_path = frames / f"frame_{saved:05d}.png"
            crop_path = crops / f"crop_{saved:05d}.png"
            save_image(img, frame_path)
            save_image(cropped, crop_path)
            writer.writerow([i, 1, f"{score:.6f}", dist, stable, file_digest(frame_path)])
            print(f"{i:05d} saved={saved} ssim={score:.6f} phash={dist}")
            saved += 1
            last_gray = gray
            last_hash = current_hash
        else:
            print("stop: reached max shots")

    print(f"saved changed frames: {saved}")
    print(f"metrics: {metrics_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
