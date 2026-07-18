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

from PIL import Image


DEFAULT_HDC = r"D:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"
DEFAULT_ADB = ""
DEFAULT_OUT_ROOT = r"D:\demo\wechat_info"
DEFAULT_OUT = str(Path(DEFAULT_OUT_ROOT) / datetime.now().strftime("%Y%m%d_%H%M%S"))
DEFAULT_CROP = "auto"
DEFAULT_SKIP = [
    "公众号",
    "订阅号",
    "服务通知",
    "微信支付",
    "微信团队",
    "客服消息",
    "微信公众平台",
]
DEFAULT_FOLDER_ENTRIES = ["折叠的群聊"]
DEFAULT_FOLDER_SUFFIX = "_展开"
DEFAULT_WECHAT_BUNDLE = "com.tencent.wechat"
DEFAULT_WECHAT_ABILITY = "EntryAbility"
DEFAULT_ADB_WECHAT_PACKAGE = "com.tencent.mm"
MAIN_TAB_TEXTS = {"微信", "通讯录", "发现", "我"}
ACTIVE_TRANSPORT = "hdc"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_cmd(cmd, timeout=30):
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


def run_hdc(hdc, target, args, timeout=30):
    cmd = [hdc]
    if target:
        cmd.extend(["-t", target])
    cmd.extend(args)
    return run_cmd(cmd, timeout=timeout)


def run_adb(adb, target, args, timeout=30):
    cmd = [adb]
    if target:
        cmd.extend(["-s", target])
    cmd.extend(args)
    return run_cmd(cmd, timeout=timeout)


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


def shell(hdc, target, command, timeout=30):
    if ACTIVE_TRANSPORT == "adb":
        return run_adb(hdc, target, ["shell", command], timeout=timeout)
    return run_hdc(hdc, target, ["shell", command], timeout=timeout)


def recv_file(tool, target, remote, local_path, timeout=30):
    if ACTIVE_TRANSPORT == "adb":
        return run_adb(tool, target, ["pull", remote, str(local_path)], timeout=timeout)
    return run_hdc(tool, target, ["file", "recv", remote, str(local_path)], timeout=timeout)


def choose_hdc_target(hdc, explicit_target):
    if explicit_target:
        return explicit_target
    result = run_hdc(hdc, "", ["list", "targets", "-v"], timeout=10)
    available = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        target_id, connection, status = parts[0], parts[1].lower(), parts[2].lower()
        available.append(line)
        if connection == "usb" and status in ("connected", "ready"):
            return target_id
    details = "\n".join(available) if available else result.stdout.strip()
    if details:
        raise RuntimeError(
            "No USB HDC target is ready. Current targets:\n"
            f"{details}\n"
            "Reconnect the phone/tablet, enable HDC/USB debugging, accept authorization, "
            "then run: hdc list targets -v"
        )
    raise RuntimeError("No connected HDC target found. Run: hdc list targets -v")


def choose_adb_target(adb, explicit_target):
    if explicit_target:
        return explicit_target
    result = run_adb(adb, "", ["devices", "-l"], timeout=10)
    available = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        available.append(line)
        if parts[1] == "device":
            return parts[0]
    details = "\n".join(available) if available else result.stdout.strip()
    if details:
        raise RuntimeError(
            "No ADB device is ready. Current devices:\n"
            f"{details}\n"
            "Reconnect the phone/tablet, enable USB debugging, accept authorization, "
            "then run: adb devices -l"
        )
    raise RuntimeError("No ADB device found. Run: adb devices -l")


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


def get_screen_size(hdc, target):
    if ACTIVE_TRANSPORT == "adb":
        try:
            result = shell(hdc, target, "wm size", timeout=10)
            matches = re.findall(r"(?:Physical|Override) size:\s*(\d+)x(\d+)", result.stdout)
            if matches:
                width, height = matches[-1]
                return int(width), int(height)
        except Exception:
            pass
        return 1600, 2560

    try:
        result = shell(hdc, target, "hidumper -s RenderService -a screen 2>/dev/null", timeout=10)
        match = re.search(r"render resolution=(\d+)x(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception:
        pass
    return 1316, 2832


def is_screen_on(hdc, target):
    if ACTIVE_TRANSPORT == "adb":
        try:
            result = shell(hdc, target, "dumpsys power", timeout=10)
            text = result.stdout
            if "Display Power: state=ON" in text or "mWakefulness=Awake" in text:
                return True
            if "Display Power: state=OFF" in text or "mWakefulness=Asleep" in text:
                return False
        except Exception:
            pass
        return True

    try:
        result = shell(hdc, target, "hidumper -s RenderService -a screen 2>/dev/null", timeout=10)
        match = re.search(r"powerStatus=([A-Z_]+)", result.stdout)
        if match:
            return match.group(1) == "POWER_STATUS_ON"
    except Exception:
        pass
    return True


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
            "content-desc": attrs.get("content-desc") or "",
            "bounds": attrs.get("bounds") or "",
            "id": resource_id_short(resource_id),
            "resource_id": resource_id,
            "type": class_name.rsplit(".", 1)[-1] if class_name else "",
            "class": class_name,
            "package": attrs.get("package") or "",
            "bundleName": attrs.get("package") or "",
            "visible": "true",
            "clickable": attrs.get("clickable") or "false",
            "scrollable": attrs.get("scrollable") or "false",
            "enabled": attrs.get("enabled") or "true",
            "hashcode": "",
        },
        "children": [android_xml_to_layout(child) for child in list(element)],
    }


def parse_android_layout_xml(path):
    root = ET.parse(path).getroot()
    return android_xml_to_layout(root)


def layout_has_package(layout, package_name):
    for node in walk(layout):
        attrs = node.get("attributes", {})
        if attrs.get("package") == package_name or attrs.get("bundleName") == package_name:
            return True
    return False


def adb_screenshot(tool, target, local_path):
    cmd = [tool]
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
        local_path.write_bytes(result.stdout)
        return image
    except Exception:
        remote = "/sdcard/codex_wechat_export_screen.png"
        shell(tool, target, f"screencap -p {remote}", timeout=30)
        recv_file(tool, target, remote, local_path, timeout=30)
        return Image.open(local_path).convert("RGB")


def dump_layout(hdc, target, local_path):
    if ACTIVE_TRANSPORT == "adb":
        remote = "/sdcard/codex_wechat_export_layout.xml"
        local_xml = local_path.with_suffix(".xml")
        shell(hdc, target, f"uiautomator dump {remote}", timeout=30)
        recv_file(hdc, target, remote, local_xml, timeout=30)
        if not local_xml.exists():
            raise RuntimeError(
                f"Layout dump was not received from ADB target {target}. "
                "Check USB debugging authorization and current screen state."
            )
        layout = parse_android_layout_xml(local_xml)
        local_path.write_text(json.dumps(layout, ensure_ascii=False), encoding="utf-8")
        return layout

    remote = "/data/local/tmp/codex_wechat_export_layout.json"
    shell(hdc, target, f"uitest dumpLayout -p {remote}", timeout=30)
    recv_file(hdc, target, remote, local_path, timeout=30)
    if not local_path.exists():
        raise RuntimeError(
            f"Layout dump was not received from target {target}. "
            "This usually means the selected HDC target is not a ready USB phone/tablet."
        )
    return json.loads(local_path.read_text(encoding="utf-8"))


def parse_bounds(bounds):
    values = [int(v) for v in re.findall(r"\d+", bounds or "")]
    if len(values) != 4:
        raise ValueError(f"Invalid bounds: {bounds}")
    return values


def bounds_center(bounds):
    x1, y1, x2, y2 = parse_bounds(bounds)
    return (x1 + x2) // 2, (y1 + y2) // 2


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


def walk(node):
    yield node
    for child in node.get("children", []) or []:
        yield from walk(child)


def node_text(attrs):
    return (attrs.get("text") or attrs.get("originalText") or "").strip()


def is_lock_screen(layout):
    lock_ids = {
        "sl_clock",
        "normal_clock",
        "ScreenLock-QuickToolBar",
        "id_persist_camera_icon",
    }
    lock_text_hits = 0
    has_unlock_hint = False
    wechat_hits = 0
    for node in walk(layout):
        attrs = node.get("attributes", {})
        node_id = attrs.get("id") or ""
        text = node_text(attrs)
        is_unlock_hint = text in ("上滑解锁", "滑动解锁", "向上滑动解锁")
        if is_unlock_hint:
            has_unlock_hint = True
        if node_id in lock_ids or node_id.startswith("Text_Digital_Text_") or is_unlock_hint:
            lock_text_hits += 1
        if attrs.get("bundleName") in (DEFAULT_WECHAT_BUNDLE, DEFAULT_ADB_WECHAT_PACKAGE) or text in (
            "微信",
            "通讯录",
            "发现",
            "我",
        ):
            wechat_hits += 1
    return (lock_text_hits >= 2 or has_unlock_hint) and wechat_hits == 0


def input_key(tool, target, key_name, timeout=15):
    if ACTIVE_TRANSPORT == "adb":
        adb_keys = {
            "Back": "BACK",
            "Home": "HOME",
            "Power": "POWER",
        }
        shell(tool, target, f"input keyevent {adb_keys.get(key_name, key_name)}", timeout=timeout)
    else:
        shell(tool, target, f"uitest uiInput keyEvent {key_name}", timeout=timeout)


def input_swipe(tool, target, x1, y1, x2, y2, duration_ms, timeout=20):
    if ACTIVE_TRANSPORT == "adb":
        shell(tool, target, f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=timeout)
    else:
        shell(tool, target, f"uitest uiInput swipe {x1} {y1} {x2} {y2} {duration_ms}", timeout=timeout)


def unlock_phone(hdc, target, work_dir, args):
    if not args.unlock:
        return

    width, height = args.screen_size
    if not is_screen_on(hdc, target):
        print("Screen appears off; pressing Power.", flush=True)
        input_key(hdc, target, "Power", timeout=15)
        time.sleep(1.2)

    for attempt in range(args.unlock_attempts):
        layout_path = work_dir / f"unlock_layout_{attempt}.json"
        layout = dump_layout(hdc, target, layout_path)
        if not is_lock_screen(layout):
            print("Phone is not on lock screen.", flush=True)
            return
        x = width // 2
        y1 = int(height * 0.82)
        y2 = int(height * 0.30)
        print(f"Unlock swipe attempt {attempt + 1}: ({x},{y1}) -> ({x},{y2})", flush=True)
        input_swipe(hdc, target, x, y1, x, y2, 1200, timeout=20)
        time.sleep(1.5)


def launch_wechat(hdc, target, args):
    if not args.launch_wechat:
        return
    if ACTIVE_TRANSPORT == "adb":
        commands = [
            f"am start -n {args.adb_wechat_package}/.ui.LauncherUI",
            f"monkey -p {args.adb_wechat_package} -c android.intent.category.LAUNCHER 1",
        ]
    else:
        commands = [f"aa start -b {args.wechat_bundle} -a {args.wechat_ability}"]

    last_error = None
    for command in commands:
        try:
            shell(hdc, target, command, timeout=15)
            time.sleep(args.launch_wait)
            print(f"Launched WeChat with: {command}", flush=True)
            return
        except subprocess.CalledProcessError as error:
            last_error = error
            print(f"WARN launch command failed: {command}. {error}", flush=True)
    if last_error:
        print(f"WARN launch failed; continuing. {last_error}", flush=True)


def collect_visible_chats(layout):
    chats = []
    seen = set()
    _width, height = infer_layout_size(layout)
    top_cut = max(180, int(height * 0.12)) if height else 430
    bottom_cut = int(height * 0.93) if height else 2550
    if ACTIVE_TRANSPORT == "adb":
        return collect_visible_chats_adb(layout, top_cut, bottom_cut)

    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = node_text(attrs)
        bounds = attrs.get("bounds") or ""
        visible = attrs.get("visible")
        node_id = attrs.get("id") or ""
        if not text or not bounds:
            continue
        if visible not in ("true", True):
            continue
        # On the current HarmonyOS WeChat build, conversation titles expose id=Title.
        if node_id != "Title":
            continue
        x1, y1, x2, y2 = parse_bounds(bounds)
        if y2 < top_cut or y1 > bottom_cut:
            continue
        if text in seen:
            continue
        seen.add(text)
        chats.append(
            {
                "name": text,
                "bounds": bounds,
                "center": bounds_center(bounds),
                "id": node_id,
            }
        )
    chats.sort(key=lambda item: parse_bounds(item["bounds"])[1])
    return chats


def classify_wechat_location(layout):
    width, height = infer_layout_size(layout)
    top_limit = max(170, int(height * 0.12)) if height else 340
    bottom_limit = int(height * 0.82) if height else 2300
    top_titles = set()
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
            x1, y1, x2, y2 = parse_bounds(bounds)
        except ValueError:
            continue
        if y2 <= top_limit:
            top_titles.add(text)
        if y1 >= bottom_limit and text in MAIN_TAB_TEXTS:
            bottom_tabs.add(text)

    if len(bottom_tabs) >= 2:
        if "微信" in top_titles:
            return "main_wechat"
        if "通讯录" in top_titles:
            return "main_contacts"
        if "发现" in top_titles:
            return "main_discover"
        if "我" in top_titles:
            return "main_me"
        return "main_unknown"
    if has_chat_content_list:
        return "chat_detail_or_sub_list"
    return "unknown"


def has_adb_chat_list_context(layout, bottom_cut):
    texts = []
    has_scrollable = False
    has_chat_input = False
    has_wechat_package = False
    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = node_text(attrs)
        node_class = attrs.get("class") or ""
        bounds = attrs.get("bounds") or ""
        if attrs.get("package") == DEFAULT_ADB_WECHAT_PACKAGE or attrs.get("bundleName") == DEFAULT_ADB_WECHAT_PACKAGE:
            has_wechat_package = True
        if text:
            texts.append((text, bounds))
        if attrs.get("scrollable") == "true":
            has_scrollable = True
        if "EditText" in node_class or text in ("发送", "按住 说话"):
            has_chat_input = True

    if not has_wechat_package:
        return False

    bottom_tabs = {"微信", "通讯录", "发现", "我"}
    bottom_tab_hits = 0
    for text, bounds in texts:
        if text not in bottom_tabs or not bounds:
            continue
        try:
            _x1, y1, _x2, _y2 = parse_bounds(bounds)
        except ValueError:
            continue
        if y1 >= bottom_cut:
            bottom_tab_hits += 1

    if bottom_tab_hits >= 2:
        return True
    return has_scrollable and not has_chat_input


def looks_like_non_title_text(text):
    if not text:
        return True
    if text in {"微信", "通讯录", "发现", "我", "搜索", "取消", "返回", "更多功能按钮"}:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d+条?", text):
        return True
    if text in {"昨天", "星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"}:
        return True
    return False


def collect_visible_chats_adb(layout, top_cut, bottom_cut):
    if not has_adb_chat_list_context(layout, bottom_cut):
        return []

    text_nodes = []
    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = node_text(attrs)
        bounds = attrs.get("bounds") or ""
        node_class = attrs.get("class") or ""
        if not text or not bounds or "TextView" not in node_class:
            continue
        if looks_like_non_title_text(text):
            continue
        try:
            x1, y1, x2, y2 = parse_bounds(bounds)
        except ValueError:
            continue
        if y2 < top_cut or y1 > bottom_cut:
            continue
        if x1 < 80:
            continue
        text_nodes.append(
            {
                "name": text,
                "bounds": bounds,
                "center": bounds_center(bounds),
                "id": attrs.get("id") or "",
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
            }
        )

    if not text_nodes:
        return []

    row_gap = max(72, int((bottom_cut - top_cut) * 0.04))
    rows = []
    for item in sorted(text_nodes, key=lambda node: (node["y1"], node["x1"])):
        center_y = (item["y1"] + item["y2"]) // 2
        matched = None
        for row in rows:
            if abs(row["center_y"] - center_y) <= row_gap:
                matched = row
                break
        if matched is None:
            rows.append({"center_y": center_y, "items": [item]})
        else:
            matched["items"].append(item)
            matched["center_y"] = int(
                sum((entry["y1"] + entry["y2"]) // 2 for entry in matched["items"]) / len(matched["items"])
            )

    chats = []
    seen = set()
    for row in rows:
        candidates = sorted(row["items"], key=lambda item: (item["y1"], item["x1"]))
        title = candidates[0]
        if title["name"] in seen:
            continue
        seen.add(title["name"])
        chats.append(
            {
                "name": title["name"],
                "bounds": title["bounds"],
                "center": title["center"],
                "id": title["id"],
            }
        )
    chats.sort(key=lambda item: parse_bounds(item["bounds"])[1])
    return chats


def visual_row_hash(image, box):
    crop = image.crop(box).resize((96, 24))
    buf = io.BytesIO()
    crop.save(buf, format="PNG", optimize=True)
    return hashlib.sha1(buf.getvalue()).hexdigest()[:8]


def collect_visual_chats_adb(tool, target, work_dir, args):
    if ACTIVE_TRANSPORT != "adb":
        return []
    image_path = work_dir / "adb_visual_fallback.png"
    image = adb_screenshot(tool, target, image_path)
    width, height = image.size

    # WeChat tablet portrait layout: title bar, repeated chat rows, bottom tab bar.
    top = int(height * 0.070)
    bottom = int(height * 0.855)
    row_h = max(120, int(height * 0.064))
    min_non_white = 0.012
    chats = []
    for index, y1 in enumerate(range(top, bottom, row_h), 1):
        y2 = min(y1 + row_h, bottom)
        if y2 - y1 < 80:
            continue
        sample = image.crop((0, y1, width, y2)).resize((160, max(8, int((y2 - y1) * 160 / width))))
        data = sample.convert("RGB").tobytes()
        total_pixels = max(1, len(data) // 3)
        non_white = sum(1 for i in range(0, len(data), 3) if min(data[i], data[i + 1], data[i + 2]) < 242)
        ratio = non_white / total_pixels
        if ratio < min_non_white:
            continue
        digest = visual_row_hash(image, (0, y1, width, y2))
        name = f"ADB_chat_{index:02d}_{digest}"
        chats.append(
            {
                "name": name,
                "bounds": f"[0,{y1}][{width},{y2}]",
                "center": (width // 2, (y1 + y2) // 2),
                "id": "adb_visual_fallback",
                "visual_fallback": True,
                "non_white_ratio": ratio,
            }
        )
    if chats:
        print(
            "WARN ADB UI tree has no WeChat text nodes; using screenshot row fallback. "
            "Directory names will be visual ids, not real chat names.",
            flush=True,
        )
    return chats


def sanitize_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "未命名会话"


def unique_dir(base, name):
    path = base / sanitize_name(name)
    if not path.exists():
        return path
    suffix = 2
    while True:
        candidate = base / f"{sanitize_name(name)}_{suffix}"
        if not candidate.exists():
            return candidate
        suffix += 1


def existing_chat_dirs(base, name):
    safe = sanitize_name(name)
    paths = []
    first = base / safe
    if first.exists():
        paths.append(first)
    paths.extend(sorted(base.glob(f"{safe}_*"), key=lambda item: item.name))
    return paths


def find_completed_chat_dir(base, name):
    for path in existing_chat_dirs(base, name):
        if (path / "export_summary.json").exists():
            return path
    return None


def reusable_chat_dir(base, name):
    existing = existing_chat_dirs(base, name)
    if existing:
        return existing[0]
    return base / sanitize_name(name)


def click(hdc, target, x, y):
    if ACTIVE_TRANSPORT == "adb":
        shell(hdc, target, f"input tap {x} {y}", timeout=15)
    else:
        shell(hdc, target, f"uitest uiInput click {x} {y}", timeout=15)


def key_back(hdc, target):
    input_key(hdc, target, "Back", timeout=15)


def swipe_chat_list(hdc, target, args):
    # Finger swipes upward to reveal lower/older conversation-list entries.
    width, height = args.screen_size
    x = width // 2
    input_swipe(hdc, target, x, int(height * 0.78), x, int(height * 0.27), 900, timeout=20)


def swipe_chat_list_to_top(hdc, target, args):
    # Finger swipes downward to return toward the top/pinned/newest conversations.
    width, height = args.screen_size
    x = width // 2
    input_swipe(hdc, target, x, int(height * 0.27), x, int(height * 0.78), 900, timeout=20)


def chat_signature(chats):
    return tuple((item["name"], item["bounds"]) for item in chats)


def seek_list_top(hdc, target, work_dir, args):
    stable = 0
    previous = None
    for step in range(args.max_list_top_swipes):
        chats = ensure_chat_list(hdc, target, work_dir)
        signature = chat_signature(chats)
        if signature == previous:
            stable += 1
            if stable >= args.list_stable_count:
                print(f"LIST TOP reached after {step} checks", flush=True)
                return
        else:
            stable = 0
            previous = signature
        swipe_chat_list_to_top(hdc, target, args)
        time.sleep(args.list_wait)


def click_wechat_bottom_tab(hdc, target, work_dir, args):
    layout_path = work_dir / "layout_bottom_tab.json"
    layout = dump_layout(hdc, target, layout_path)
    width, height = infer_layout_size(layout)
    if (not width or not height) and args is not None:
        width, height = args.screen_size
    candidates = []
    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = node_text(attrs)
        bounds = attrs.get("bounds") or ""
        if text != "微信" or not bounds:
            continue
        x1, y1, x2, y2 = parse_bounds(bounds)
        if y1 > height * 0.82:
            candidates.append((y1, bounds))
    if not candidates:
        return False
    candidates.sort(reverse=True)
    x, y = bounds_center(candidates[0][1])
    print(f"Clicking WeChat bottom tab at ({x},{y}).", flush=True)
    click(hdc, target, x, y)
    time.sleep(1.0)
    return True


def ensure_chat_list(hdc, target, work_dir, recover=True):
    layout_path = work_dir / "layout_check.json"
    for attempt in range(3 if recover else 1):
        layout = dump_layout(hdc, target, layout_path)
        location = classify_wechat_location(layout)
        print(f"PAGE location={location}", flush=True)

        if location in ("main_contacts", "main_discover", "main_me", "main_unknown"):
            if click_wechat_bottom_tab(hdc, target, work_dir, None):
                continue

        chats = collect_visible_chats(layout)
        if chats and location in ("main_wechat", "chat_detail_or_sub_list", "unknown"):
            return chats
        if ACTIVE_TRANSPORT == "adb" and layout_has_package(layout, DEFAULT_ADB_WECHAT_PACKAGE):
            visual_chats = collect_visual_chats_adb(hdc, target, work_dir, None)
            if visual_chats:
                return visual_chats

        if not recover:
            break

        # One Back usually recovers from an opened chat page to the chat list.
        key_back(hdc, target)
        time.sleep(1.2)
    return []


def should_skip(name, skip_words):
    return any(word and word in name for word in skip_words)


def is_folder_entry(name, folder_entries):
    return any(entry and (name == entry or entry in name) for entry in folder_entries)


def folder_container_dir(base, name):
    return base / f"{sanitize_name(name)}{DEFAULT_FOLDER_SUFFIX}"


def write_detected_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["round", "name", "bounds", "center_x", "center_y", "status", "time"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def run_capture(capture_script, hdc, target, chat_dir, args):
    cmd = [
        sys.executable,
        str(capture_script),
        "--transport",
        ACTIVE_TRANSPORT,
        "--hdc",
        hdc,
        "--adb",
        hdc,
        "--target",
        target,
        "--crop",
        args.crop,
        "--out",
        str(chat_dir),
        "--max-seek",
        str(args.max_seek),
        "--max-shots",
        str(args.max_shots),
        "--stable-count",
        str(args.stable_count),
        "--wait",
        str(args.wait),
        "--velocity",
        str(args.velocity),
        "--seek-wait",
        str(args.seek_wait),
        "--seek-confirm-wait",
        str(args.seek_confirm_wait),
        "--capture-wait",
        str(args.capture_wait),
        "--seek-velocity",
        str(args.seek_velocity),
        "--capture-velocity",
        str(args.capture_velocity),
        "--seek-swipe-mode",
        args.seek_swipe_mode,
        "--seek-fling-count",
        str(args.seek_fling_count),
        "--seek-fling-velocity",
        str(args.seek_fling_velocity),
        "--seek-fling-step-length",
        str(args.seek_fling_step_length),
        "--seek-fling-gap",
        str(args.seek_fling_gap),
    ]
    stdout = chat_dir / "capture_stdout.log"
    stderr = chat_dir / "capture_stderr.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        subprocess.run(cmd, check=True, stdout=out, stderr=err)


def count_frames(chat_dir):
    crops = chat_dir / "chat_crops"
    full = chat_dir / "full_frames"
    return {
        "chat_crops": len(list(crops.glob("*.jpeg"))) if crops.exists() else 0,
        "full_frames": len(list(full.glob("*.jpeg"))) if full.exists() else 0,
    }


def export_one_chat(hdc, target, chat, out_dir, capture_script, args, exported_map, map_key=None):
    name = chat["name"]
    key = map_key or name
    if should_skip(name, args.skip):
        print(f"SKIP {name}", flush=True)
        return "skipped"

    if key in exported_map and not args.force:
        print(f"ALREADY {name}", flush=True)
        return "already"

    completed_dir = find_completed_chat_dir(out_dir, name)
    if completed_dir and not args.force:
        exported_map[key] = completed_dir
        print(f"RESUME-SKIP {name}: already completed at {completed_dir}", flush=True)
        return "already"

    if args.force and key in exported_map:
        chat_dir = exported_map[key]
    else:
        chat_dir = reusable_chat_dir(out_dir, name)
        exported_map[key] = chat_dir

    chat_dir.mkdir(parents=True, exist_ok=True)
    (chat_dir / "chat_name.txt").write_text(name, encoding="utf-8")
    (chat_dir / "ui_entry.json").write_text(
        json.dumps(chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    x, y = chat["center"]
    print(f"OPEN {name} at ({x},{y}) -> {chat_dir}", flush=True)
    click(hdc, target, x, y)
    time.sleep(args.open_wait)
    run_capture(capture_script, hdc, target, chat_dir, args)

    summary = count_frames(chat_dir)
    (chat_dir / "export_summary.json").write_text(
        json.dumps(
            {
                "name": name,
                "finished_at": now_text(),
                "chat_crops": summary["chat_crops"],
                "full_frames": summary["full_frames"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"DONE {name}: crops={summary['chat_crops']} full={summary['full_frames']}",
        flush=True,
    )
    key_back(hdc, target)
    time.sleep(args.back_wait)
    return "exported"


def export_folder_chats(
    hdc,
    target,
    folder_chat,
    out_dir,
    work_dir,
    capture_script,
    args,
    exported_map,
    detected_rows,
    processed_folders,
):
    folder_name = folder_chat["name"]
    if folder_name in processed_folders:
        print(f"FOLDER-SKIP {folder_name}: already scanned", flush=True)
        return "already"
    processed_folders.add(folder_name)

    folder_dir = folder_container_dir(out_dir, folder_name)
    folder_dir.mkdir(parents=True, exist_ok=True)
    (folder_dir / "folder_entry.json").write_text(
        json.dumps(folder_chat, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    x, y = folder_chat["center"]
    print(f"OPEN-FOLDER {folder_name} at ({x},{y}) -> {folder_dir}", flush=True)
    click(hdc, target, x, y)
    time.sleep(args.open_wait)

    if args.seek_list_top:
        print(f"Seeking folder list top: {folder_name}...", flush=True)
        seek_list_top(hdc, target, work_dir, args)

    observed = set()
    no_new_rounds = 0
    for round_index in range(args.max_folder_swipes + 1):
        chats = ensure_chat_list(hdc, target, work_dir)
        if not chats:
            print(f"WARN {folder_name}: no folded group entries found", flush=True)
            break

        new_this_round = 0
        print(
            f"FOLDER {folder_name} ROUND {round_index}: {len(chats)} visible entries",
            flush=True,
        )
        for chat in chats:
            child_name = chat["name"]
            x, y = chat["center"]
            status = f"folder:{folder_name}:seen"
            if child_name not in observed:
                observed.add(child_name)
                new_this_round += 1
                status = f"folder:{folder_name}:new"
            detected_rows.append(
                {
                    "round": f"{folder_name}:{round_index}",
                    "name": child_name,
                    "bounds": chat["bounds"],
                    "center_x": x,
                    "center_y": y,
                    "status": status,
                    "time": now_text(),
                }
            )

        for chat in list(chats):
            child_name = chat["name"]
            if is_folder_entry(child_name, args.folder_entries):
                print(f"SKIP nested folder entry {child_name}", flush=True)
                continue
            child_key = f"{folder_name}/{child_name}"
            if child_key in exported_map and not args.force:
                continue
            current = ensure_chat_list(hdc, target, work_dir)
            match = next((item for item in current if item["name"] == child_name), None)
            if not match:
                print(f"MISS {folder_name}/{child_name}: no longer visible", flush=True)
                continue
            export_one_chat(
                hdc,
                target,
                match,
                folder_dir,
                capture_script,
                args,
                exported_map,
                map_key=child_key,
            )

        if new_this_round == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
        if no_new_rounds >= args.list_stable_count:
            print(f"STOP folder scan {folder_name}: no new visible entries", flush=True)
            break

        swipe_chat_list(hdc, target, args)
        time.sleep(args.list_wait)

    key_back(hdc, target)
    time.sleep(args.back_wait)
    return "folder"


def export_visible(hdc, target, out_dir, work_dir, capture_script, args, exported_map, detected_rows):
    chats = ensure_chat_list(hdc, target, work_dir)
    if not chats:
        raise RuntimeError("No chat entries found. Unlock the phone and open WeChat chat list.")
    processed_folders = set()
    print("Visible chats:", flush=True)
    for i, chat in enumerate(chats, 1):
        x, y = chat["center"]
        print(f"{i}. {chat['name']} {chat['bounds']}", flush=True)
        detected_rows.append(
            {
                "round": 0,
                "name": chat["name"],
                "bounds": chat["bounds"],
                "center_x": x,
                "center_y": y,
                "status": "visible",
                "time": now_text(),
            }
        )
    if args.dry_run:
        return

    for chat in chats:
        current = ensure_chat_list(hdc, target, work_dir)
        match = next((item for item in current if item["name"] == chat["name"]), None)
        if not match:
            print(f"MISS {chat['name']}: no longer visible", flush=True)
            continue
        if is_folder_entry(match["name"], args.folder_entries):
            export_folder_chats(
                hdc,
                target,
                match,
                out_dir,
                work_dir,
                capture_script,
                args,
                exported_map,
                detected_rows,
                processed_folders,
            )
        else:
            export_one_chat(hdc, target, match, out_dir, capture_script, args, exported_map)


def export_by_scanning_list(hdc, target, out_dir, work_dir, capture_script, args, exported_map, detected_rows):
    no_new_rounds = 0
    observed = set()
    processed_folders = set()

    if args.seek_list_top:
        print("Seeking conversation list top...", flush=True)
        seek_list_top(hdc, target, work_dir, args)

    for round_index in range(args.max_list_swipes + 1):
        chats = ensure_chat_list(hdc, target, work_dir)
        if not chats:
            raise RuntimeError("No chat entries found. Unlock the phone and open WeChat chat list.")

        new_this_round = 0
        print(f"LIST ROUND {round_index}: {len(chats)} visible entries", flush=True)
        for chat in chats:
            x, y = chat["center"]
            status = "seen"
            if chat["name"] not in observed:
                observed.add(chat["name"])
                new_this_round += 1
                status = "new"
            detected_rows.append(
                {
                    "round": round_index,
                    "name": chat["name"],
                    "bounds": chat["bounds"],
                    "center_x": x,
                    "center_y": y,
                    "status": status,
                    "time": now_text(),
                }
            )

        if args.dry_run:
            for chat in chats:
                print(f"  {chat['name']} {chat['bounds']}", flush=True)
        else:
            # Export each currently visible, not-yet-exported chat before moving the list.
            for chat in list(chats):
                if is_folder_entry(chat["name"], args.folder_entries):
                    current = ensure_chat_list(hdc, target, work_dir)
                    match = next((item for item in current if item["name"] == chat["name"]), None)
                    if not match:
                        print(f"MISS {chat['name']}: no longer visible", flush=True)
                        continue
                    export_folder_chats(
                        hdc,
                        target,
                        match,
                        out_dir,
                        work_dir,
                        capture_script,
                        args,
                        exported_map,
                        detected_rows,
                        processed_folders,
                    )
                    continue
                if chat["name"] in exported_map and not args.force:
                    continue
                current = ensure_chat_list(hdc, target, work_dir)
                match = next((item for item in current if item["name"] == chat["name"]), None)
                if not match:
                    print(f"MISS {chat['name']}: no longer visible", flush=True)
                    continue
                export_one_chat(hdc, target, match, out_dir, capture_script, args, exported_map)

        if new_this_round == 0:
            no_new_rounds += 1
        else:
            no_new_rounds = 0
        if no_new_rounds >= args.list_stable_count:
            print("STOP list scan: no new visible entries", flush=True)
            break

        swipe_chat_list(hdc, target, args)
        time.sleep(args.list_wait)


def main():
    parser = argparse.ArgumentParser(
        description="Export WeChat conversations from a connected HarmonyOS phone/tablet via HDC UI layout and screenshots."
    )
    parser.add_argument("--transport", choices=["hdc"], default="hdc")
    parser.add_argument("--hdc", default=DEFAULT_HDC)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--target", default="", help="Device target id. Empty means auto-pick first ready HDC/ADB target.")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--crop", default=DEFAULT_CROP)
    parser.add_argument("--mode", choices=["visible", "scan-list"], default="visible")
    parser.add_argument("--dry-run", action="store_true", help="Only detect chat entries; do not click/export.")
    parser.add_argument("--force", action="store_true", help="Re-export chats even if already exported in this run.")
    parser.add_argument("--skip", nargs="*", default=DEFAULT_SKIP)
    parser.add_argument(
        "--folder-entry",
        dest="folder_entries",
        action="append",
        default=None,
        help="Conversation-list entry that opens a folded/archive chat list. Repeat for multiple names.",
    )
    parser.add_argument("--max-list-swipes", type=int, default=20)
    parser.add_argument("--max-folder-swipes", type=int, default=20)
    parser.add_argument("--max-list-top-swipes", type=int, default=8)
    parser.add_argument("--list-stable-count", type=int, default=2)
    parser.add_argument("--seek-list-top", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--list-wait", type=float, default=1.0)
    parser.add_argument(
        "--max-seek",
        type=int,
        default=0,
        help="Maximum seek-to-start attempts per chat. Use 0 or a negative value for unlimited seek.",
    )
    parser.add_argument("--max-shots", type=int, default=500)
    parser.add_argument("--stable-count", type=int, default=4)
    parser.add_argument("--wait", type=float, default=1.0)
    parser.add_argument("--velocity", type=int, default=900)
    parser.add_argument("--seek-wait", type=float, default=0.6)
    parser.add_argument("--seek-confirm-wait", type=float, default=1.0)
    parser.add_argument("--capture-wait", type=float, default=1.0)
    parser.add_argument("--seek-velocity", type=int, default=550)
    parser.add_argument("--capture-velocity", type=int, default=900)
    parser.add_argument("--seek-swipe-mode", choices=["normal", "fling"], default="fling")
    parser.add_argument("--seek-fling-count", type=int, default=1)
    parser.add_argument("--seek-fling-velocity", type=int, default=6000)
    parser.add_argument("--seek-fling-step-length", type=int, default=0)
    parser.add_argument("--seek-fling-gap", type=float, default=0.0)
    parser.add_argument("--open-wait", type=float, default=1.5)
    parser.add_argument("--back-wait", type=float, default=1.5)
    parser.add_argument("--unlock", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--unlock-attempts", type=int, default=3)
    parser.add_argument("--launch-wechat", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--launch-wait", type=float, default=2.0)
    parser.add_argument("--wechat-bundle", default=DEFAULT_WECHAT_BUNDLE)
    parser.add_argument("--wechat-ability", default=DEFAULT_WECHAT_ABILITY)
    parser.add_argument("--adb-wechat-package", default=DEFAULT_ADB_WECHAT_PACKAGE)
    args = parser.parse_args()
    if args.folder_entries is None:
        args.folder_entries = DEFAULT_FOLDER_ENTRIES

    transport, tool, target = resolve_device(args)
    args.transport = transport
    args.screen_size = get_screen_size(tool, target)

    out_dir = Path(args.out)
    work_dir = out_dir / "_work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    capture_script = Path(__file__).resolve().with_name("wechat_hdc_capture.py")
    if not capture_script.exists():
        raise FileNotFoundError(f"capture script not found: {capture_script}")

    print(f"Transport: {transport}", flush=True)
    print(f"Tool: {tool}", flush=True)
    print(f"Target: {target}", flush=True)
    print(f"Screen: {args.screen_size[0]}x{args.screen_size[1]}", flush=True)
    print(f"Output: {out_dir}", flush=True)
    print(f"Mode: {args.mode}", flush=True)
    print(f"Skip: {args.skip}", flush=True)
    print(f"Folder entries: {args.folder_entries}", flush=True)

    unlock_phone(tool, target, work_dir, args)
    launch_wechat(tool, target, args)
    initial_chats = ensure_chat_list(tool, target, work_dir)
    if not initial_chats:
        click_wechat_bottom_tab(tool, target, work_dir, args)

    detected_rows = []
    exported_map = {}
    if args.mode == "visible":
        export_visible(tool, target, out_dir, work_dir, capture_script, args, exported_map, detected_rows)
    else:
        export_by_scanning_list(tool, target, out_dir, work_dir, capture_script, args, exported_map, detected_rows)

    write_detected_csv(out_dir / "detected_chats.csv", detected_rows)
    summary = []
    for name, chat_dir in exported_map.items():
        counts = count_frames(chat_dir)
        summary.append(
            {
                "name": name,
                "path": str(chat_dir),
                "chat_crops": counts["chat_crops"],
                "full_frames": counts["full_frames"],
            }
        )
    (out_dir / "export_index.json").write_text(
        json.dumps(
            {
                "finished_at": now_text(),
                "transport": transport,
                "tool": tool,
                "target": target,
                "mode": args.mode,
                "output": str(out_dir),
                "exports": summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Index: {out_dir / 'export_index.json'}", flush=True)
    print(f"Detected chats: {out_dir / 'detected_chats.csv'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
