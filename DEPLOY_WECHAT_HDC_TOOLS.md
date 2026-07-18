# 微信 HDC 截图导出工具部署与使用说明

本文说明如何把当前工具部署到另一台 Windows 电脑上，并导出已登录微信账号中的聊天截图。

## 1. 工具能力

- 通过 HDC 控制鸿蒙手机或平板。
- 自动解锁无密码设备、启动微信、识别微信聊天列表。
- 跳过默认不需要导出的入口：公众号、订阅号、服务通知、微信支付、微信团队、客服消息、微信公众平台。
- 支持进入“折叠的群聊”，并把里面的真实群聊分别导出到独立目录。
- 每个聊天会单独创建目录，保存完整截图、聊天区域裁剪图、指标文件和摘要文件。
- 向上寻找聊天开头使用 HDC 原生 `fling`，`--max-seek 0` 表示不限轮次，直到稳定判断到达开头。
- 向下截图阶段使用较稳的普通滑动，避免漏截。

当前版本只建议使用 HDC 通道，适用于鸿蒙手机/平板。ADB 模式不作为正式部署目标。

## 2. 需要复制的文件

把整个工具目录复制到目标电脑，例如：

```text
D:\demo\wechat_info\TOOLS\
  wechat_hdc_export_0630.py
  wechat_hdc_capture.py
  run_wechat_export_0630_001.ps1
  run_wechat_export_tablet_001.ps1
  requirements.txt
  README_WECHAT_HDC_TOOLS.md
  DEPLOY_WECHAT_HDC_TOOLS.md
```

目录可以放在别的位置，例如 `C:\wechat_info\TOOLS`。启动脚本会自动以自身所在目录作为工具目录。

## 3. 目标电脑需要安装的软件

### 必需

1. Windows 10/11，带 PowerShell。
2. Python 3.10 或更新版本。
3. Python 依赖包：

```powershell
python -m pip install -r D:\demo\wechat_info\TOOLS\requirements.txt
```

4. HDC 工具。常见来源是 DevEco Studio / OpenHarmony SDK，例如：

```text
D:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe
```

如果目标电脑的 HDC 不在这个路径，需要执行时传 `-HdcPath`。

### 设备侧

1. 鸿蒙手机或平板通过 USB 连接电脑。
2. 设备已开启开发者选项 / HDC 调试 / USB 调试相关授权。
3. 手机弹出授权提示时选择允许。
4. 微信已登录目标账号。
5. 设备无锁屏密码，或已经处于可直接滑动解锁状态。

## 4. 部署后验证

### 验证 Python

```powershell
python --version
python -m pip show pillow opencv-python numpy
```

如果缺包，执行：

```powershell
python -m pip install -r D:\demo\wechat_info\TOOLS\requirements.txt
```

### 验证 HDC

如果 `hdc.exe` 已经加入 PATH：

```powershell
hdc list targets -v
```

如果没有加入 PATH，用完整路径：

```powershell
& "D:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe" list targets -v
```

正常时应看到 USB 设备处于 `Connected` 或 `Ready`。如果是 `Offline`，通常需要重新插拔 USB、重新授权，或检查 HDC/USB 调试开关。

## 5. 推荐执行方式

### 默认输出到时间戳目录

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1
```

输出目录形如：

```text
D:\demo\wechat_info\20260630_153012
```

### 指定输出根目录

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -OutRoot E:\wechat_exports
```

脚本会在 `E:\wechat_exports` 下创建时间戳目录。

### 指定完整输出目录

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -OutDir E:\wechat_exports\case_001
```

### HDC 不在默认路径时

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -HdcPath "C:\DevEco Studio\sdk\default\openharmony\toolchains\hdc.exe" `
  -OutRoot E:\wechat_exports
```

### 多台设备同时连接时

先看设备 ID：

```powershell
hdc list targets -v
```

再指定目标设备：

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -Target 3ZF0124807000600 `
  -OutRoot E:\wechat_exports
```

### Python 不在 PATH 时

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -PythonExe "C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe"
```

## 6. 导出结果目录结构

每个聊天一个目录：

```text
E:\wechat_exports\20260630_153012\
  detected_chats.csv
  export_index.json
  钱闯\
    chat_name.txt
    ui_entry.json
    capture_stdout.log
    capture_stderr.log
    export_summary.json
    metrics.csv
    chat_crops\
      chat_00000.jpeg
      chat_00001.jpeg
    full_frames\
      frame_00000.jpeg
      frame_00001.jpeg
    debug\
```

`chat_crops` 是裁剪后的聊天区域，`full_frames` 是完整屏幕截图。

## 7. 当前默认速度参数

正式启动脚本默认参数：

```text
--max-seek 0
--seek-swipe-mode fling
--seek-fling-count 1
--seek-fling-velocity 6000
--seek-fling-step-length 0
--seek-wait 1.0
--seek-confirm-wait 1.0
--capture-velocity 900
--capture-wait 1.0
--stable-count 4
```

含义：

- 向上找聊天开头不限轮次。
- 找开头使用 HDC 原生快速 `fling`。
- 疑似到达开头后切换为普通滑动确认。
- 连续稳定 4 次才认为到达开头或结尾。
- 正式向下截图阶段保持保守速度。

## 8. 常见问题

### PowerShell 不允许执行脚本

使用：

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1
```

### 找不到 hdc

使用 `-HdcPath` 传入完整路径，或把 `hdc.exe` 所在目录加入系统 PATH。

### 设备显示 Offline

重新插拔 USB，检查开发者选项和 HDC 调试授权。设备上弹出授权框时必须允许。

### 自动裁剪区域不对

先对当前聊天单独探测：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_capture.py `
  --transport hdc `
  --crop auto `
  --probe-only `
  --out E:\wechat_exports\probe
```

查看：

```text
E:\wechat_exports\probe\debug\probe_crop.jpeg
```

如果裁剪不对，可以改用手动裁剪参数，例如：

```text
--crop 0,300,1071,2537
```

### 不想导出某些入口

默认会跳过公众号、微信支付、微信团队等服务入口。如果需要改规则，修改 `wechat_hdc_export_0630.py` 顶部的 `DEFAULT_SKIP`。

### 只想先检测聊天列表

可以用 Python 入口：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_export_0630.py `
  --transport hdc `
  --dry-run `
  --mode visible `
  --crop auto `
  --out E:\wechat_exports\dry_run
```

这只识别当前可见聊天，不会点击导出。

## 9. 部署检查清单

- 已复制 `TOOLS` 整个目录。
- Python 能运行。
- `pip install -r requirements.txt` 已完成。
- `hdc list targets -v` 能看到 USB `Connected` 或 `Ready` 设备。
- 微信已登录。
- 设备屏幕已解锁或可无密码解锁。
- 输出目录磁盘空间足够。
- 先用一个短聊天或当前窗口做小规模测试，再跑完整批量导出。
