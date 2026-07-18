# -*- coding: utf-8 -*-
import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


DEFAULT_HDC = r"D:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"
DEFAULT_ADB = ""
DEFAULT_TARGET = ""
MAIN_TAB_TEXTS = {"微信", "通讯录", "发现", "我"}
ACTIVE_TRANSPORT = "hdc"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def run_hdc(hdc, target, args, capture=True, timeout=30):
    cmd = [hdc]
    if target:
        cmd.extend(["-t", target])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def run_adb(adb, target, args, capture=True, timeout=30):
    cmd = [adb]
    if target:
        cmd.extend(["-s", target])
    cmd.extend(args)
    return subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def run_text(cmd, timeout=30):
    return subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def resolve_hdc(path):
    if path and Path(path).exists():
        return path
    found = shutil.which("hdc")
    if found:
        return found
    raise FileNotFoundError(f"hdc not found: {path}")


def adb_candidates(path):
    if path:
        yield path
    found = shutil.which("adb")
    if found:
        yield found
    env_roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "AppData" / "Local" / "Android" / "Sdk"),
    ]
    for root in env_roots:
        if root:
            yield str(Path(root) / "platform-tools" / "adb.exe")
    winget_root = Path.home() / "AppData" / "Local" / "Microsoft" / "WinGet" / "Packages"
    if winget_root.exists():
        yield from (str(path) for path in winget_root.glob("Google.PlatformTools_*/*/adb.exe"))
        yield from (str(path) for path in winget_root.glob("Google.PlatformTools_*/platform-tools/adb.exe"))


def resolve_adb(path):
    for candidate in adb_candidates(path):
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError("adb not found. Install Android platform-tools or add adb.exe to PATH.")


def choose_hdc_target(hdc, explicit_target):
    if explicit_target:
        return explicit_target
    result = run_text([hdc, "list", "targets", "-v"], timeout=10)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1].lower() == "usb" and parts[2].lower() in ("connected", "ready"):
            return parts[0]
    raise RuntimeError("No USB HDC target is ready. Run: hdc list targets -v")


def choose_adb_target(adb, explicit_target):
    if explicit_target:
        return explicit_target
    result = run_text([adb, "devices", "-l"], timeout=10)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    raise RuntimeError("No ADB device is ready. Run: adb devices -l")


def resolve_device(args):
    global ACTIVE_TRANSPORT
    errors = []
    if args.transport in ("auto", "hdc"):
        try:
            hdc = resolve_hdc(args.hdc)
            target = choose_hdc_target(hdc, args.target)
            ACTIVE_TRANSPORT = "hdc"
            return "hdc", hdc, target
        except Exception as error:
            errors.append(f"HDC: {error}")
            if args.transport == "hdc":
                raise
    if args.transport in ("auto", "adb"):
        try:
            adb = resolve_adb(args.adb)
            target = choose_adb_target(adb, args.target)
            ACTIVE_TRANSPORT = "adb"
            return "adb", adb, target
        except Exception as error:
            errors.append(f"ADB: {error}")
            if args.transport == "adb":
                raise
    raise RuntimeError("No usable device transport found.\n" + "\n\n".join(errors))


def shell(hdc, target, command, timeout=30):
    if ACTIVE_TRANSPORT == "adb":
        return run_adb(hdc, target, ["shell", command], timeout=timeout)
    return run_hdc(hdc, target, ["shell", command], timeout=timeout)


def recv_file(tool, target, remote, local_path, timeout=30):
    if ACTIVE_TRANSPORT == "adb":
        return run_adb(tool, target, ["pull", remote, str(local_path)], timeout=timeout)
    return run_hdc(tool, target, ["file", "recv", remote, str(local_path)], timeout=timeout)


def resource_id_short(resource_id):
    if not resource_id:
        return ""
    if "/id/" in resource_id:
        return resource_id.rsplit("/id/", 1)[-1]
    return resource_id.rsplit("/", 1)[-1]


def android_xml_to_layout(element):
    attrs = dict(element.attrib)
    resource_id = attrs.get("resource-id") or ""
    class_name = attrs.get("class") or ""
    return {
        "attributes": {
            "text": attrs.get("text") or "",
            "originalText": attrs.get("content-desc") or "",
            "bounds": attrs.get("bounds") or "",
            "id": resource_id_short(resource_id),
            "resource_id": resource_id,
            "type": class_name.rsplit(".", 1)[-1] if class_name else "",
            "class": class_name,
            "key": "",
            "package": attrs.get("package") or "",
            "bundleName": attrs.get("package") or "",
            "visible": "true",
            "scrollable": attrs.get("scrollable") or "false",
            "hashcode": "",
        },
        "children": [android_xml_to_layout(child) for child in list(element)],
    }


def parse_android_layout_xml(path):
    root = ET.parse(path).getroot()
    return android_xml_to_layout(root)


def dump_layout(hdc, target, temp_dir):
    local = temp_dir / "_layout.json"
    if ACTIVE_TRANSPORT == "adb":
        remote = "/sdcard/codex_wechat_capture_layout.xml"
        local_xml = temp_dir / "_layout.xml"
        shell(hdc, target, f"uiautomator dump {remote}", timeout=30)
        recv_file(hdc, target, remote, local_xml, timeout=30)
        layout = parse_android_layout_xml(local_xml)
        local.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        return layout

    remote = "/data/local/tmp/codex_wechat_hdc_layout.json"
    shell(hdc, target, f"uitest dumpLayout -p {remote}", timeout=30)
    recv_file(hdc, target, remote, local, timeout=30)
    return json.loads(local.read_text(encoding="utf-8"))


def walk(node):
    yield node
    for child in node.get("children", []) or []:
        yield from walk(child)


def node_text(attrs):
    return (attrs.get("text") or attrs.get("originalText") or "").strip()


def infer_layout_size(layout):
    max_x = 0
    max_y = 0
    for node in walk(layout):
        bounds = node.get("attributes", {}).get("bounds") or ""
        try:
            _x1, _y1, x2, y2 = parse_bounds(bounds)
        except ValueError:
            continue
        max_x = max(max_x, x2)
        max_y = max(max_y, y2)
    return max_x, max_y


def classify_capture_location(layout):
    _width, height = infer_layout_size(layout)
    bottom_limit = int(height * 0.82) if height else 2300
    bottom_tabs = set()
    has_chat_content_list = False
    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = node_text(attrs)
        bounds = attrs.get("bounds") or ""
        node_id = attrs.get("id") or ""
        key = attrs.get("key") or ""
        if node_id == "chat_list" or key == "chat_list":
            has_chat_content_list = True
        if not text or not bounds:
            continue
        try:
            _x1, y1, _x2, _y2 = parse_bounds(bounds)
        except ValueError:
            continue
        if y1 >= bottom_limit and text in MAIN_TAB_TEXTS:
            bottom_tabs.add(text)
    if len(bottom_tabs) >= 2:
        return "main_tab"
    if has_chat_content_list:
        return "chat_detail"
    return "unknown"


def parse_bounds(bounds):
    values = [int(v) for v in re.findall(r"\d+", bounds or "")]
    if len(values) != 4:
        raise ValueError(f"Invalid bounds: {bounds}")
    return tuple(values)


def clamp_box(box, width, height):
    left, top, right, bottom = box
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return left, top, right, bottom


def screenshot(hdc, target, temp_dir, index):
    if ACTIVE_TRANSPORT == "adb":
        local = temp_dir / f"_raw_{index:05d}.png"
        cmd = [hdc]
        if target:
            cmd.extend(["-s", target])
        cmd.extend(["exec-out", "screencap", "-p"])
        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
            image = Image.open(io.BytesIO(result.stdout)).convert("RGB")
            local.write_bytes(result.stdout)
            return image, local
        except Exception:
            remote = "/sdcard/codex_wechat_adb.png"
            shell(hdc, target, f"screencap -p {remote}", timeout=30)
            recv_file(hdc, target, remote, local, timeout=30)
            if not local.exists():
                raise FileNotFoundError(f"ADB did not receive screenshot to local path: {local}")
            return Image.open(local).convert("RGB"), local

    remote = "/data/local/tmp/codex_wechat_hdc.jpeg"
    local = temp_dir / f"_raw_{index:05d}.jpeg"
    shell(hdc, target, f"snapshot_display -f {remote}", timeout=30)
    recv_file(hdc, target, remote, local, timeout=30)
    if not local.exists():
        raise FileNotFoundError(f"HDC did not receive screenshot to local path: {local}")
    return Image.open(local).convert("RGB"), local


def parse_crop(value, width, height):
    if not value:
        return (0, int(height * 0.11), width, int(height * 0.90))
    if value.strip().lower() in ("auto", "auto-chat"):
        raise ValueError("auto crop must be resolved from UI layout before parse_crop")
    parts = [float(p.strip()) for p in value.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop must have four comma-separated values.")
    if all(0 <= p <= 1 for p in parts):
        return tuple(
            int(v)
            for v in (
                parts[0] * width,
                parts[1] * height,
                parts[2] * width,
                parts[3] * height,
            )
        )
    return tuple(int(p) for p in parts)


def auto_chat_crop(hdc, target, temp_dir, width, height):
    layout = dump_layout(hdc, target, temp_dir)
    if classify_capture_location(layout) == "main_tab":
        raise RuntimeError(
            "Current screen is a WeChat main tab, not an opened chat. "
            "Abort this capture to avoid saving the wrong page."
        )
    candidates = []
    for node in walk(layout):
        attrs = node.get("attributes", {})
        bounds = attrs.get("bounds") or ""
        if not bounds:
            continue
        node_id = attrs.get("id") or ""
        key = attrs.get("key") or ""
        node_type = attrs.get("type") or ""
        node_class = attrs.get("class") or ""
        visible = attrs.get("visible")
        if visible not in ("true", True):
            continue
        try:
            left, top, right, bottom = parse_bounds(bounds)
        except ValueError:
            continue
        area = max(0, right - left) * max(0, bottom - top)
        score = 0
        if node_id == "chat_list" or key == "chat_list":
            score += 1000
        if node_type == "List" and area > width * height * 0.25:
            score += 500
        if (
            ACTIVE_TRANSPORT == "adb"
            and area > width * height * 0.25
            and (
                attrs.get("scrollable") == "true"
                or "RecyclerView" in node_class
                or "ListView" in node_class
            )
        ):
            score += 700
        if "chat" in node_id.lower() or "chat" in key.lower():
            score += 100
        if score:
            candidates.append((score, area, (left, top, right, bottom), attrs))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        left, top, right, bottom = candidates[0][2]
        header_padding = max(80, int(height * 0.04))
        top = min(top + header_padding, bottom - 120)
        box = clamp_box((left, top, right, bottom), width, height)
        return box, candidates[0][3]

    if ACTIVE_TRANSPORT == "adb":
        return clamp_box((0, int(height * 0.10), width, int(height * 0.90)), width, height), {
            "source": "adb_fallback"
        }

    # Conservative fallback for portrait HarmonyOS WeChat screens.
    return clamp_box((0, int(height * 0.106), int(width * 0.82), int(height * 0.896)), width, height), {
        "source": "fallback"
    }


def crop_image(image, box):
    return image.crop(box)


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
    return (low > med).flatten()


def hamming(a, b):
    return int(np.count_nonzero(a != b))


def band_grid_scores(a, b, rows, cols, threshold):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    h, w = a.shape[:2]
    scores = []
    stable = 0
    total = 0
    for row in range(rows):
        y1 = int(h * row / rows)
        y2 = int(h * (row + 1) / rows)
        for col in range(cols):
            x1 = int(w * col / cols)
            x2 = int(w * (col + 1) / cols)
            if y2 - y1 < 16 or x2 - x1 < 16:
                continue
            score = ssim_score(a[y1:y2, x1:x2], b[y1:y2, x1:x2])
            scores.append(score)
            total += 1
            if score >= threshold:
                stable += 1
    ratio = stable / total if total else 0.0
    return ratio, scores


def changed_pixel_ratio(a, b, threshold):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    a_blur = cv2.GaussianBlur(a, (7, 7), 0)
    b_blur = cv2.GaussianBlur(b, (7, 7), 0)
    diff = cv2.absdiff(a_blur, b_blur)
    changed = diff > threshold
    return float(np.count_nonzero(changed) / changed.size)


def edge_ssim_score(a, b):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    a_edges = cv2.Canny(cv2.GaussianBlur(a, (5, 5), 0), 50, 150)
    b_edges = cv2.Canny(cv2.GaussianBlur(b, (5, 5), 0), 50, 150)
    return ssim_score(a_edges, b_edges)


def intersects_crop(bounds, box):
    left, top, right, bottom = bounds
    crop_left, crop_top, crop_right, crop_bottom = box
    return not (right <= crop_left or left >= crop_right or bottom <= crop_top or top >= crop_bottom)


def layout_signature(hdc, target, temp_dir, box, index):
    try:
        layout = dump_layout(hdc, target, temp_dir)
    except Exception:
        return None
    if classify_capture_location(layout) == "main_tab":
        raise RuntimeError(
            "Current screen changed to a WeChat main tab during capture. "
            "Abort this capture to avoid saving the wrong page."
        )

    entries = []
    crop_area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
    for node in walk(layout):
        attrs = node.get("attributes", {})
        bounds_text = attrs.get("bounds") or ""
        visible = attrs.get("visible")
        if not bounds_text or visible not in ("true", True):
            continue
        try:
            bounds = parse_bounds(bounds_text)
        except ValueError:
            continue
        if not intersects_crop(bounds, box):
            continue

        left, top, right, bottom = bounds
        width = max(0, right - left)
        height = max(0, bottom - top)
        area = width * height
        if width < 4 or height < 4 or area > crop_area * 0.90:
            continue

        node_type = attrs.get("type") or ""
        node_id = attrs.get("id") or ""
        key = attrs.get("key") or ""
        text = node_text(attrs)
        hashcode = attrs.get("hashcode") or ""
        if not text and node_type not in {"Image", "Text", "ListItem", "Row", "__Common__", "Stack"}:
            continue
        # hashcode helps distinguish repeated image-only messages while remaining stable at the same scroll position.
        entries.append((node_type, node_id, key, text, bounds_text, hashcode if not text else ""))

    if not entries:
        return None
    return frozenset(entries[:300])


def signature_similarity(a, b):
    if not a or not b:
        return None
    return len(a & b) / max(len(a), len(b), 1)


def image_stability(prev_gray, prev_hash, gray, current_hash, args):
    full_ssim = ssim_score(prev_gray, gray)
    phash_distance = hamming(prev_hash, current_hash)
    grid_ratio, grid_scores = band_grid_scores(
        prev_gray,
        gray,
        args.grid_rows,
        args.grid_cols,
        args.grid_ssim_threshold,
    )
    diff_ratio = changed_pixel_ratio(prev_gray, gray, args.diff_pixel_threshold)
    edge_score = edge_ssim_score(prev_gray, gray)
    image_same = (
        (full_ssim >= args.ssim_threshold and phash_distance <= args.phash_threshold)
        or grid_ratio >= args.grid_stable_ratio
        or diff_ratio <= args.diff_ratio_threshold
        or edge_score >= args.edge_ssim_threshold
    )
    return image_same, {
        "ssim": full_ssim,
        "phash": phash_distance,
        "grid_stable_ratio": grid_ratio,
        "grid_min_ssim": min(grid_scores) if grid_scores else 0.0,
        "diff_ratio": diff_ratio,
        "edge_ssim": edge_score,
    }


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save(image, path):
    image.save(path, quality=95, optimize=True)


def safe_temp_dir_for(base):
    parent = base.parent if base.parent else Path.cwd()
    digest_text = hashlib.sha1(str(base).encode("utf-8", errors="replace")).hexdigest()[:16]
    return parent / "_tmp_capture" / digest_text


def swipe(hdc, target, width, height, direction, velocity, retries=3):
    x = int(width * 0.50)
    if direction == "older":
        y1, y2 = int(height * 0.38), int(height * 0.80)
    elif direction == "newer":
        y1, y2 = int(height * 0.80), int(height * 0.38)
    else:
        raise ValueError(direction)

    last = None
    for attempt in range(1, retries + 1):
        try:
            if ACTIVE_TRANSPORT == "adb":
                shell(hdc, target, f"input swipe {x} {y1} {x} {y2} {velocity}", timeout=20)
            else:
                shell(
                    hdc,
                    target,
                    f"uitest uiInput swipe {x} {y1} {x} {y2} {velocity}",
                    timeout=20,
                )
            return
        except subprocess.CalledProcessError as error:
            last = error
            time.sleep(1.0 * attempt)
    raise last


def fling(hdc, target, width, height, direction, velocity, step_length, retries=3):
    x = int(width * 0.50)
    if direction == "older":
        y1, y2 = int(height * 0.38), int(height * 0.80)
    elif direction == "newer":
        y1, y2 = int(height * 0.80), int(height * 0.38)
    else:
        raise ValueError(direction)

    last = None
    for attempt in range(1, retries + 1):
        try:
            if ACTIVE_TRANSPORT == "adb":
                shell(hdc, target, f"input swipe {x} {y1} {x} {y2} {velocity}", timeout=20)
            else:
                extra = f" {step_length}" if step_length and step_length > 0 else ""
                shell(
                    hdc,
                    target,
                    f"uitest uiInput fling {x} {y1} {x} {y2} {velocity}{extra}",
                    timeout=20,
                )
            return
        except subprocess.CalledProcessError as error:
            last = error
            time.sleep(1.0 * attempt)
    raise last


def seek_swipe_older(hdc, target, width, height, args, stable):
    if args.seek_swipe_mode == "fling" and stable == 0:
        count = max(1, args.seek_fling_count)
        for n in range(count):
            fling(
                hdc,
                target,
                width,
                height,
                "older",
                args.seek_fling_velocity,
                args.seek_fling_step_length,
            )
            if n + 1 < count and args.seek_fling_gap > 0:
                time.sleep(args.seek_fling_gap)
        return "fling", count, args.seek_fling_velocity

    swipe(hdc, target, width, height, "older", args.seek_velocity)
    mode = "confirm" if args.seek_swipe_mode == "fling" and stable > 0 else "normal"
    return mode, 1, args.seek_velocity


def make_state(hdc, target, temp_dir, image, box, args, index):
    cropped = crop_image(image, box)
    gray = normalized_gray(cropped)
    return {
        "gray": gray,
        "hash": phash(cropped),
        "signature": layout_signature(hdc, target, temp_dir, box, index)
        if args.stability_mode in ("hybrid", "ui")
        else None,
    }


def is_same(hdc, target, temp_dir, prev_state, image, box, args, index):
    cropped = crop_image(image, box)
    gray = normalized_gray(cropped)
    current_hash = phash(cropped)
    image_same, metrics = image_stability(prev_state["gray"], prev_state["hash"], gray, current_hash, args)

    current_signature = (
        layout_signature(hdc, target, temp_dir, box, index)
        if args.stability_mode in ("hybrid", "ui")
        else None
    )
    ui_similarity = signature_similarity(prev_state.get("signature"), current_signature)
    ui_same = ui_similarity is not None and ui_similarity >= args.ui_similarity_threshold

    if args.stability_mode == "ui":
        same = ui_same
    elif args.stability_mode == "image":
        same = image_same
    else:
        same = ui_same or image_same

    reason = []
    if ui_same:
        reason.append("ui")
    if image_same:
        reason.append("image")
    metrics.update(
        {
            "ui_similarity": ui_similarity if ui_similarity is not None else -1.0,
            "reason": "+".join(reason) or "move",
        }
    )
    state = {"gray": gray, "hash": current_hash, "signature": current_signature}
    return same, metrics, state


def navigate_to_start(hdc, target, temp_dir, box, args, width, height):
    img, _ = screenshot(hdc, target, temp_dir, 0)
    prev_state = make_state(hdc, target, temp_dir, img, box, args, 0)
    stable = 0
    unlimited = args.max_seek <= 0
    i = 1

    while unlimited or i <= args.max_seek:
        wait_seconds = args.seek_confirm_wait if stable > 0 else args.seek_wait
        seek_mode, seek_swipes, seek_velocity = seek_swipe_older(
            hdc, target, width, height, args, stable
        )
        time.sleep(wait_seconds)
        img, _ = screenshot(hdc, target, temp_dir, i)
        same, metrics, current_state = is_same(hdc, target, temp_dir, prev_state, img, box, args, i)
        if same:
            stable += 1
            print(
                f"seek {i:05d} stable={stable} mode={seek_mode} swipes={seek_swipes} "
                f"velocity={seek_velocity} wait={wait_seconds:.2f} "
                f"reason={metrics['reason']} "
                f"ssim={metrics['ssim']:.6f} phash={metrics['phash']} "
                f"grid={metrics['grid_stable_ratio']:.3f} diff={metrics['diff_ratio']:.3f} "
                f"edge={metrics['edge_ssim']:.3f} ui={metrics['ui_similarity']:.3f}",
                flush=True,
            )
            if stable >= args.stable_count:
                return img
        else:
            stable = 0
            prev_state = current_state
            print(
                f"seek {i:05d} moved mode={seek_mode} swipes={seek_swipes} "
                f"velocity={seek_velocity} wait={wait_seconds:.2f} "
                f"reason={metrics['reason']} "
                f"ssim={metrics['ssim']:.6f} phash={metrics['phash']} "
                f"grid={metrics['grid_stable_ratio']:.3f} diff={metrics['diff_ratio']:.3f} "
                f"edge={metrics['edge_ssim']:.3f} ui={metrics['ui_similarity']:.3f}",
                flush=True,
            )
        i += 1
    print("seek reached max limit; continuing from current position", flush=True)
    return img


def capture_to_end(hdc, target, base, temp_dir, start_img, box, args, width, height):
    full_dir = base / "full_frames"
    crop_dir = base / "chat_crops"
    debug_dir = base / "debug"
    for folder in (full_dir, crop_dir, debug_dir):
        folder.mkdir(parents=True, exist_ok=True)

    metrics = base / "metrics.csv"
    saved = 0
    stable = 0
    prev_state = make_state(hdc, target, temp_dir, start_img, box, args, 100000)

    with open(metrics, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "step",
                "saved",
                "ssim",
                "phash_hamming",
                "grid_stable_ratio",
                "diff_ratio",
                "edge_ssim",
                "ui_similarity",
                "stable",
                "reason",
                "full_sha256",
            ]
        )

        def write_frame(image, step):
            nonlocal saved
            full_path = full_dir / f"frame_{saved:05d}.jpeg"
            crop_path = crop_dir / f"chat_{saved:05d}.jpeg"
            save(image, full_path)
            save(crop_image(image, box), crop_path)
            writer.writerow([step, 1, "", "", "", "", "", "", stable, "initial", digest(full_path)])
            saved += 1

        write_frame(start_img, 0)

        for step in range(1, args.max_shots + 1):
            swipe(hdc, target, width, height, "newer", args.capture_velocity)
            time.sleep(args.capture_wait)
            img, _ = screenshot(hdc, target, temp_dir, 100000 + step)
            same, current_metrics, current_state = is_same(
                hdc, target, temp_dir, prev_state, img, box, args, 100000 + step
            )
            if same:
                stable += 1
                temp_path = debug_dir / f"stable_candidate_{step:05d}.jpeg"
                save(img, temp_path)
                writer.writerow(
                    [
                        step,
                        0,
                        f"{current_metrics['ssim']:.6f}",
                        current_metrics["phash"],
                        f"{current_metrics['grid_stable_ratio']:.6f}",
                        f"{current_metrics['diff_ratio']:.6f}",
                        f"{current_metrics['edge_ssim']:.6f}",
                        f"{current_metrics['ui_similarity']:.6f}",
                        stable,
                        current_metrics["reason"],
                        digest(temp_path),
                    ]
                )
                print(
                    f"capture {step:05d} stable={stable} velocity={args.capture_velocity} "
                    f"wait={args.capture_wait:.2f} reason={current_metrics['reason']} "
                    f"ssim={current_metrics['ssim']:.6f} phash={current_metrics['phash']} "
                    f"grid={current_metrics['grid_stable_ratio']:.3f} "
                    f"diff={current_metrics['diff_ratio']:.3f} "
                    f"edge={current_metrics['edge_ssim']:.3f} "
                    f"ui={current_metrics['ui_similarity']:.3f}",
                    flush=True,
                )
                if stable >= args.stable_count:
                    break
                continue

            stable = 0
            full_path = full_dir / f"frame_{saved:05d}.jpeg"
            crop_path = crop_dir / f"chat_{saved:05d}.jpeg"
            save(img, full_path)
            save(crop_image(img, box), crop_path)
            writer.writerow(
                [
                    step,
                    1,
                    f"{current_metrics['ssim']:.6f}",
                    current_metrics["phash"],
                    f"{current_metrics['grid_stable_ratio']:.6f}",
                    f"{current_metrics['diff_ratio']:.6f}",
                    f"{current_metrics['edge_ssim']:.6f}",
                    f"{current_metrics['ui_similarity']:.6f}",
                    stable,
                    current_metrics["reason"],
                    digest(full_path),
                ]
            )
            print(
                f"capture {step:05d} saved={saved} velocity={args.capture_velocity} "
                f"wait={args.capture_wait:.2f} reason={current_metrics['reason']} "
                f"ssim={current_metrics['ssim']:.6f} phash={current_metrics['phash']} "
                f"grid={current_metrics['grid_stable_ratio']:.3f} "
                f"diff={current_metrics['diff_ratio']:.3f} "
                f"edge={current_metrics['edge_ssim']:.3f} "
                f"ui={current_metrics['ui_similarity']:.3f}",
                flush=True,
            )
            saved += 1
            prev_state = current_state

    return saved, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["hdc"], default="hdc")
    parser.add_argument("--hdc", default=DEFAULT_HDC)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--out", default="")
    parser.add_argument("--crop", default="")
    parser.add_argument("--wait", type=float, default=1.0)
    parser.add_argument("--velocity", type=int, default=900)
    parser.add_argument("--seek-wait", type=float, default=None)
    parser.add_argument("--seek-confirm-wait", type=float, default=None)
    parser.add_argument("--capture-wait", type=float, default=None)
    parser.add_argument("--seek-velocity", type=int, default=None)
    parser.add_argument("--capture-velocity", type=int, default=None)
    parser.add_argument("--seek-swipe-mode", choices=["normal", "fling"], default="fling")
    parser.add_argument("--seek-fling-count", type=int, default=1)
    parser.add_argument("--seek-fling-velocity", type=int, default=6000)
    parser.add_argument("--seek-fling-step-length", type=int, default=0)
    parser.add_argument("--seek-fling-gap", type=float, default=0.0)
    parser.add_argument("--stable-count", type=int, default=4)
    parser.add_argument("--stability-mode", choices=["hybrid", "image", "ui"], default="hybrid")
    parser.add_argument("--ssim-threshold", type=float, default=0.985)
    parser.add_argument("--phash-threshold", type=int, default=4)
    parser.add_argument("--grid-rows", type=int, default=12)
    parser.add_argument("--grid-cols", type=int, default=4)
    parser.add_argument("--grid-ssim-threshold", type=float, default=0.985)
    parser.add_argument("--grid-stable-ratio", type=float, default=0.68)
    parser.add_argument("--diff-pixel-threshold", type=int, default=26)
    parser.add_argument("--diff-ratio-threshold", type=float, default=0.08)
    parser.add_argument("--edge-ssim-threshold", type=float, default=0.94)
    parser.add_argument("--ui-similarity-threshold", type=float, default=0.90)
    parser.add_argument(
        "--max-seek",
        type=int,
        default=0,
        help="Maximum seek-to-start attempts. Use 0 or a negative value for unlimited seek.",
    )
    parser.add_argument("--max-shots", type=int, default=800)
    parser.add_argument("--probe-only", action="store_true")
    args = parser.parse_args()
    if args.seek_wait is None:
        args.seek_wait = args.wait
    if args.seek_confirm_wait is None:
        args.seek_confirm_wait = max(args.wait, args.seek_wait)
    if args.capture_wait is None:
        args.capture_wait = args.wait
    if args.seek_velocity is None:
        args.seek_velocity = args.velocity
    if args.capture_velocity is None:
        args.capture_velocity = args.velocity

    transport, tool, target = resolve_device(args)
    args.transport = transport

    base = Path(args.out) if args.out else Path(__file__).resolve().parent / (
        "wechat_hdc_capture_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    )
    temp_dir = safe_temp_dir_for(base)
    temp_dir.mkdir(parents=True, exist_ok=True)

    first, _ = screenshot(tool, target, temp_dir, 99999)
    width, height = first.size
    if args.crop.strip().lower() in ("auto", "auto-chat"):
        box, crop_source = auto_chat_crop(tool, target, temp_dir, width, height)
    else:
        box = parse_crop(args.crop, width, height)
        crop_source = {"source": "manual_or_default"}
    debug_dir = base / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    save(first, debug_dir / "probe_full.jpeg")
    save(crop_image(first, box), debug_dir / "probe_crop.jpeg")
    (debug_dir / "crop_source.json").write_text(
        json.dumps({"crop": box, "source": crop_source}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"transport: {transport}", flush=True)
    print(f"target: {target}", flush=True)
    print(f"screen: {width}x{height}", flush=True)
    print(f"crop: {box}", flush=True)
    print(f"out: {base}", flush=True)
    print(
        f"seek mode={args.seek_swipe_mode} velocity={args.seek_velocity} "
        f"fling_velocity={args.seek_fling_velocity} fling_count={args.seek_fling_count} "
        f"fling_step_length={args.seek_fling_step_length} "
        f"fling_gap={args.seek_fling_gap:.2f} wait={args.seek_wait:.2f} "
        f"confirm_wait={args.seek_confirm_wait:.2f}; "
        f"capture velocity={args.capture_velocity} wait={args.capture_wait:.2f}",
        flush=True,
    )
    if args.probe_only:
        return 0

    start_img = navigate_to_start(tool, target, temp_dir, box, args, width, height)
    saved, metrics = capture_to_end(tool, target, base, temp_dir, start_img, box, args, width, height)
    print(f"saved frames: {saved}", flush=True)
    print(f"metrics: {metrics}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
