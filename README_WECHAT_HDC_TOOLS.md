# WeChat HDC Export Tools

These scripts export WeChat chats from a connected HarmonyOS phone/tablet by using HDC UI layout data and screenshots.

## Files

- `wechat_hdc_export_0630.py`: batch entry point. It unlocks the phone, launches WeChat, reads the chat list, opens chats, and calls the capture script.
- `wechat_hdc_capture.py`: captures the currently opened chat from history start to latest message.
- `run_wechat_export_0630_001.ps1`: one-command PowerShell launcher. It creates a timestamped output directory such as `D:\demo\wechat_info\20260630_153012`.
- `requirements.txt`: Python package dependencies.

## Prerequisites

1. HarmonyOS phone/tablet connected by USB.
2. HDC authorization accepted on the device.
3. HDC available from DevEco Studio or PATH.
4. Phone has no lock password, or is already unlockable by swipe.
5. WeChat installed as `com.tencent.wechat`.
6. Python packages installed: `pillow`, `opencv-python`, `numpy`.

## Deployment

For moving this tool to another Windows computer, see `DEPLOY_WECHAT_HDC_TOOLS.md`. The PowerShell launchers support `-OutRoot`, `-OutDir`, `-PythonExe`, `-HdcPath`, and `-Target`, so you normally do not need to edit Python source files after copying the `TOOLS` folder.

## Dry Run

Dry run only detects visible chat names. It does not click chats or export screenshots.

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_export_0630.py `
  --transport hdc `
  --dry-run `
  --mode visible `
  --out D:\demo\wechat_info\20260630_153012 `
  --crop auto
```

## Full Export

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1
```

Use a custom output directory from the command line:

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -OutDir D:\demo\wechat_info\20260630_hdc_test
```

## Speed Tuning

The launcher now uses separate speed settings for the two chat-scroll phases:

- Seek-to-start phase: `--max-seek 0 --seek-swipe-mode fling --seek-fling-count 1 --seek-fling-velocity 6000 --seek-fling-step-length 0 --seek-wait 1.0 --seek-confirm-wait 1.0`
- Capture-to-end phase: `--capture-velocity 900 --capture-wait 1.0`

For HDC, `fling` mode uses the native `uitest uiInput fling` command, which is closer to a manual quick flick than repeated `swipe` commands. `--max-seek 0` means the script does not stop after a fixed number of seek attempts; it keeps seeking upward until the stable-screen rule confirms that the chat start has been reached. When the script starts seeing stable screens, it stops using fling and switches to a single normal confirm swipe plus the confirm wait before counting the screen as unchanged. The capture phase stays more conservative because this is where screenshots are saved and deduplicated.

You can tune these values from the command line if a device is very fast or very slow. Keep `--stable-count 4` unless you have verified the new setting on a small chat first.

## Output

Each chat is saved in its own directory:

```text
D:\demo\wechat_info\20260630_153012\
  ChatName\
    chat_crops\
    full_frames\
    metrics.csv
    chat_name.txt
    export_summary.json
  detected_chats.csv
  export_index.json
```

Folded group chats are treated as a secondary chat list, not as a normal conversation screenshot:

```text
D:\demo\wechat_info\20260630_153012\
  折叠的群聊_展开\
    GroupName\
      chat_crops\
      full_frames\
      metrics.csv
      chat_name.txt
      export_summary.json
```

## Notes

- `--crop auto` tries to detect the chat content area from WeChat UI layout nodes such as `chat_list`.
- The batch exporter now logs a page location before reading the conversation list. If WeChat is on Contacts/Discover/Me, it clicks the bottom `WeChat` tab before continuing. If the capture worker detects that the screen changed back to a WeChat main tab during a chat capture, it aborts that chat instead of saving the wrong page.
- End detection uses `--stability-mode hybrid` by default. It combines UI-layout stability with image checks, so animated stickers or multiple dynamic emoji at the first/latest screen do not prevent the script from detecting that scrolling has reached the end.
- Conversation-list order changes caused by new messages are handled by chat-name de-duplication and re-reading the visible list before every click. A chat that moves out of the current scan window may still require rerunning the same output directory to resume.
- The image checks include full-frame SSIM, pHash, grid-cell majority voting, changed-pixel ratio, and edge-structure SSIM. Useful tuning flags include `--grid-stable-ratio`, `--diff-ratio-threshold`, and `--ui-similarity-threshold`.
- If auto crop is wrong on a new phone, run a single chat with `--probe-only` on `wechat_hdc_capture.py`, inspect `debug\probe_crop.jpeg`, then pass a manual crop like `--crop 0,300,1071,2537`.
- The batch script skips public/service entries by default: `公众号`, `订阅号`, `服务通知`, `微信支付`, `微信团队`, `客服消息`, `微信公众平台`.
- The batch script opens `折叠的群聊` by default and exports the real chats inside it into `折叠的群聊_展开`.
