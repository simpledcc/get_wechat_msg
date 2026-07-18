import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_HDC = r"D:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe"
DEFAULT_TARGET = "3ZF0124807000600"


def run_hdc(hdc, target, args, timeout=30):
    cmd = [hdc]
    if target:
        cmd.extend(["-t", target])
    cmd.extend(args)
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)


def shell(hdc, target, command, timeout=30):
    return run_hdc(hdc, target, ["shell", command], timeout=timeout)


def dump_layout(hdc, target, local_path):
    remote = "/data/local/tmp/codex_batch_layout.json"
    shell(hdc, target, f"uitest dumpLayout -p {remote}", timeout=30)
    run_hdc(hdc, target, ["file", "recv", remote, str(local_path)], timeout=30)
    return json.loads(local_path.read_text(encoding="utf-8"))


def parse_bounds(bounds):
    values = [int(v) for v in re.findall(r"\d+", bounds or "")]
    if len(values) != 4:
        raise ValueError(f"Invalid bounds: {bounds}")
    x1, y1, x2, y2 = values
    return x1, y1, x2, y2


def center(bounds):
    x1, y1, x2, y2 = parse_bounds(bounds)
    return (x1 + x2) // 2, (y1 + y2) // 2


def walk(node):
    yield node
    for child in node.get("children", []) or []:
        yield from walk(child)


def collect_visible_chats(layout):
    chats = []
    seen = set()
    for node in walk(layout):
        attrs = node.get("attributes", {})
        text = (attrs.get("text") or attrs.get("originalText") or "").strip()
        bounds = attrs.get("bounds") or ""
        node_id = attrs.get("id") or ""
        visible = attrs.get("visible")
        if not text or not bounds:
            continue
        if visible not in ("true", True):
            continue
        # WeChat conversation-list titles currently expose id=Title on this device.
        if node_id != "Title":
            continue
        if text in seen:
            continue
        seen.add(text)
        x1, y1, x2, y2 = parse_bounds(bounds)
        # Ignore accidental title-like nodes outside the conversation list area.
        if y2 < 450 or y1 > 2500:
            continue
        chats.append({"name": text, "bounds": bounds, "center": center(bounds)})
    return chats


def sanitize_name(name):
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return cleaned or "unnamed_chat"


def click(hdc, target, x, y):
    shell(hdc, target, f"uitest uiInput click {x} {y}", timeout=15)


def back(hdc, target):
    shell(hdc, target, "uitest uiInput keyEvent Back", timeout=15)


def ensure_chat_list(hdc, target, work_dir):
    layout_path = work_dir / "layout_check.json"
    layout = dump_layout(hdc, target, layout_path)
    chats = collect_visible_chats(layout)
    if chats:
        return chats
    # One back can recover from a chat page to the conversation list.
    back(hdc, target)
    time.sleep(1.2)
    layout = dump_layout(hdc, target, layout_path)
    chats = collect_visible_chats(layout)
    return chats


def find_chat(chats, name):
    for chat in chats:
        if chat["name"] == name:
            return chat
    for chat in chats:
        if name in chat["name"] or chat["name"] in name:
            return chat
    return None


def run_capture(capture_script, chat_dir, args):
    cmd = [
        sys.executable,
        str(capture_script),
        "--hdc",
        args.hdc,
        "--target",
        args.target,
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
    ]
    stdout = chat_dir / "batch_capture_stdout.log"
    stderr = chat_dir / "batch_capture_stderr.log"
    with stdout.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
        subprocess.run(cmd, check=True, stdout=out, stderr=err)


def main():
    parser = argparse.ArgumentParser(description="Batch export visible WeChat chats via HDC UI layout + screenshot capture.")
    parser.add_argument("--hdc", default=DEFAULT_HDC)
    parser.add_argument("--target", default=DEFAULT_TARGET)
    parser.add_argument("--out", default=r"D:\demo\wechat_info")
    parser.add_argument("--crop", default="0,300,1071,2537")
    parser.add_argument("--chats", nargs="*", default=[])
    parser.add_argument("--all-visible", action="store_true", help="Export all visible conversation-list entries.")
    parser.add_argument("--skip", nargs="*", default=["公众号"], help="Visible entries to skip.")
    parser.add_argument("--max-seek", type=int, default=300)
    parser.add_argument("--max-shots", type=int, default=500)
    parser.add_argument("--stable-count", type=int, default=4)
    parser.add_argument("--wait", type=float, default=1.0)
    parser.add_argument("--velocity", type=int, default=900)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = out_dir / "_batch_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    capture_script = Path(__file__).resolve().with_name("wechat_hdc_capture.py")

    chats = ensure_chat_list(args.hdc, args.target, work_dir)
    if not chats:
        raise RuntimeError("No visible WeChat chat entries found. Unlock the phone and open the WeChat chat list.")

    visible_names = [chat["name"] for chat in chats]
    print("Visible chats:")
    for i, chat in enumerate(chats, 1):
        print(f"{i}. {chat['name']} {chat['bounds']}")

    wanted = visible_names if args.all_visible else args.chats
    if not wanted:
        raise RuntimeError("Pass --all-visible or provide names after --chats.")

    for name in wanted:
        if name in args.skip:
            print(f"SKIP {name}")
            continue
        chats = ensure_chat_list(args.hdc, args.target, work_dir)
        chat = find_chat(chats, name)
        if not chat:
            print(f"MISS {name}: not visible in current chat list")
            continue

        chat_dir = out_dir / sanitize_name(chat["name"])
        chat_dir.mkdir(parents=True, exist_ok=True)
        (chat_dir / "chat_name.txt").write_text(chat["name"], encoding="utf-8")
        x, y = chat["center"]
        print(f"OPEN {chat['name']} at ({x},{y}) -> {chat_dir}")
        click(args.hdc, args.target, x, y)
        time.sleep(1.5)
        run_capture(capture_script, chat_dir, args)
        print(f"DONE {chat['name']}")
        back(args.hdc, args.target)
        time.sleep(1.5)

    print(f"Batch output: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
