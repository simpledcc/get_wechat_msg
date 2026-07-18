# 微信 HDC 聊天截图导出脚本能力说明与测试方案

生成时间：2026-06-30

本文档说明当前 `D:\demo\wechat_info\TOOLS` 下微信聊天截图导出脚本的能力、已处理的特殊场景、限制，以及建议测试用例。当前如果已有导出任务正在运行，请不要执行本文档中的 HDC 测试命令，等任务结束后再测试。

## 一、脚本组成

| 文件 | 作用 |
|---|---|
| `run_wechat_export_0630_001.ps1` | 一键启动批量导出。支持 `-OutDir` 指定输出目录，默认生成时间戳目录。 |
| `run_wechat_export_tablet_001.ps1` | 平板/备用启动脚本，当前也固定使用 HDC。 |
| `wechat_hdc_export_0630.py` | 批量控制入口：解锁、启动微信、读取聊天列表、跳过系统会话、进入折叠群聊、调用截图脚本。 |
| `wechat_hdc_capture.py` | 单个会话截图入口：定位聊天区域、滑到历史开头、向最新方向截图、判断是否到末尾。 |
| `README_WECHAT_HDC_TOOLS.md` | 基础使用说明。 |

## 二、当前运行模式

当前脚本入口已经切回 HDC-only：

```powershell
--transport hdc
```

也就是说，当前正式入口不会再自动退回 ADB。原因是 ADB 在部分华为平板上只能截图，无法稳定读取微信 UI 结构，因此不能可靠获取聊天名称、跳过公众号、识别折叠群聊。HDC 可以通过 `uitest dumpLayout` 读取真实 UI 节点，更适合本任务。

## 三、可以解决的问题

### 1. 批量导出微信聊天截图

脚本可以从微信聊天列表开始，逐个进入会话，将聊天记录截图保存到本地目录。

输出结构示例：

```text
D:\demo\wechat_info\20260630_hdc_001\
  会话名\
    chat_crops\
      chat_00000.jpeg
      chat_00001.jpeg
    full_frames\
      frame_00000.jpeg
      frame_00001.jpeg
    debug\
    metrics.csv
    chat_name.txt
    ui_entry.json
    export_summary.json
  detected_chats.csv
  export_index.json
```

### 2. 支持命令行指定输出目录

可以直接指定最后输出目录：

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -OutDir D:\demo\wechat_info\20260630_hdc_001
```

如果不传 `-OutDir`，脚本自动生成：

```text
D:\demo\wechat_info\yyyyMMdd_HHmmss
```

### 3. 自动选择 HDC USB 设备

脚本会从：

```powershell
hdc list targets -v
```

里选择 `USB` 且状态为 `Connected` 或 `Ready` 的设备。它不会把 `COM256 UART Ready` 当成手机或平板目标。

### 4. 无密码锁屏自动解锁

如果屏幕关闭，会先按电源键。如果检测到无密码锁屏，会执行上滑解锁。

适用场景：

- 屏幕关闭
- 无密码锁屏
- 已授权 HDC
- 设备可以通过上滑进入桌面或微信

### 5. 自动启动微信

HDC 模式下使用：

```text
aa start -b com.tencent.wechat -a EntryAbility
```

启动微信。如果微信已经打开，会继续使用当前微信页面。

### 6. 读取聊天列表真实名称

HDC 模式下，脚本通过 `uitest dumpLayout` 获取 UI 结构，并识别 `id=Title` 的会话标题。

已验证可以识别：

- 文件传输助手
- 公众号
- 折叠的群聊
- 微信团队
- 微信支付
- 微信公众平台
- 普通个人会话
- 普通群聊
- 包含 emoji 的群聊名

### 7. 默认跳过系统/服务类会话

默认跳过：

```text
公众号
订阅号
服务通知
微信支付
微信团队
客服消息
微信公众平台
```

这些会话不会被当成普通聊天进入截图。

### 8. 处理“折叠的群聊”

`折叠的群聊` 不再当作普通聊天截图。

脚本会：

1. 在主聊天列表识别到 `折叠的群聊`
2. 点击进入二级列表
3. 扫描里面的真实群聊
4. 将群聊导出到：

```text
输出目录\折叠的群聊_展开\真实群聊名\
```

### 9. 自动滚动聊天列表

`scan-list` 模式会：

1. 先尝试回到聊天列表顶部
2. 读取当前可见会话
3. 处理可见会话
4. 向下滑动列表
5. 连续多轮没有新会话时停止

关键参数：

```text
--max-list-swipes
--max-list-top-swipes
--list-stable-count
--list-wait
```

### 10. 已完成会话自动跳过

如果某会话目录中已经存在：

```text
export_summary.json
```

再次运行时默认会跳过该会话，避免重复截图。

日志里会显示：

```text
RESUME-SKIP 会话名
```

### 11. 支持中文、特殊字符和 emoji 会话名

已处理：

- Windows 控制台 GBK 编码导致 emoji 输出失败
- 包含 emoji 的目录名导致 HDC 文件接收失败
- Windows 文件名非法字符清理

截图临时目录不放在 emoji 会话目录内部，而是放到 ASCII 安全临时目录中，降低路径兼容问题。

### 12. 自动裁剪聊天区域

单会话截图脚本支持：

```text
--crop auto
```

优先通过 UI 结构识别 `chat_list` 等聊天区域。如果自动裁剪不正确，可以使用单会话 probe 模式查看裁剪图：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_capture.py `
  --transport hdc `
  --out D:\demo\wechat_info\probe_one_chat `
  --crop auto `
  --probe-only
```

然后检查：

```text
debug\probe_full.jpeg
debug\probe_crop.jpeg
debug\crop_source.json
```

### 13. 到头判断支持动态表情场景

截图脚本不是只用单一 hash 判断是否滑到底，而是使用混合稳定性判断：

- UI 结构签名相似度
- 全图 SSIM
- 感知 hash / pHash
- 网格区域稳定比例
- 像素变化比例
- 边缘结构 SSIM
- 连续多次稳定计数

这用于处理：

- 最新位置有动态表情
- 历史开头有动态表情
- 同屏多个动态表情
- 局部动画导致单帧 hash 一直变化

关键参数：

```text
--stable-count
--stability-mode hybrid
--grid-stable-ratio
--diff-ratio-threshold
--ui-similarity-threshold
--edge-ssim-threshold
```

### 14. 输出可追踪日志和指标

每个会话目录下会保存：

```text
capture_stdout.log
capture_stderr.log
metrics.csv
debug\
```

批量结果会保存：

```text
detected_chats.csv
export_index.json
```

这些文件用于复查脚本识别了哪些会话、每个会话保存了多少截图，以及停止截图的判断依据。

## 四、已处理的特殊场景

| 场景 | 处理方式 |
|---|---|
| HDC 列表里有 UART COM 目标 | 只选择 USB 且 Connected/Ready 的目标。 |
| 屏幕关闭 | 自动按 Power 唤醒。 |
| 无密码锁屏 | 自动上滑解锁。 |
| 微信未打开 | 自动启动微信。 |
| 聊天列表不在顶部 | 先尝试滑到顶部。 |
| 系统服务会话 | 默认跳过。 |
| 折叠的群聊 | 进入二级列表，导出内部群聊。 |
| 会话名包含 emoji | UTF-8 输出，安全临时目录。 |
| 会话名包含 Windows 非法字符 | 目录名自动清理。 |
| 已导出的会话 | 根据 `export_summary.json` resume-skip。 |
| 动态表情导致画面变化 | 使用混合稳定性判断，不只依赖 hash。 |
| 新设备分辨率不同 | 列表识别、滑动、裁剪尽量按屏幕比例和 UI 结构计算。 |
| PowerShell 中文编码问题 | 启动脚本不直接写中文参数，中文配置在 Python 内部处理。 |
| 输出目录需要自定义 | PowerShell 支持 `-OutDir` 参数。 |

## 五、当前限制

1. 当前正式入口只支持 HDC。
2. 必须保证设备 HDC 授权正常。
3. 微信 UI 结构如果大版本变化，`id=Title` 或 `chat_list` 识别可能需要调整。
4. 脚本输出的是截图，不是结构化文本聊天记录。
5. 截图期间不要手动操作设备，否则可能打断当前会话或改变列表位置。
6. 如果会话历史极长，截图耗时会很久，需要合理设置 `--max-seek` 和 `--max-shots`。
7. 如果同名会话很多，目录复用/跳过逻辑需要结合 `export_summary.json` 检查。
8. ADB 相关历史代码仍在文件中，但当前命令行入口已限制为 `--transport hdc`，正式流程不使用 ADB。

## 六、推荐使用命令

正式导出：

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -OutDir D:\demo\wechat_info\20260630_hdc_001
```

只验证启动脚本解析，不控制设备：

```powershell
powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 `
  -DryRun `
  -OutDir D:\demo\wechat_info\launcher_test
```

只识别当前可见聊天，不进入会话：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_export_0630.py `
  --transport hdc `
  --dry-run `
  --mode visible `
  --out D:\demo\wechat_info\visible_probe `
  --crop auto
```

短列表扫描 dry-run：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_export_0630.py `
  --transport hdc `
  --dry-run `
  --mode scan-list `
  --out D:\demo\wechat_info\scan_probe `
  --crop auto `
  --max-list-swipes 3 `
  --max-list-top-swipes 5
```

单会话裁剪探针，需要先手动进入某个聊天：

```powershell
python D:\demo\wechat_info\TOOLS\wechat_hdc_capture.py `
  --transport hdc `
  --out D:\demo\wechat_info\capture_probe `
  --crop auto `
  --probe-only
```

## 七、测试原则

当前如果正式导出任务正在运行，不要执行会控制设备的测试。推荐测试分三类：

| 类型 | 是否影响当前设备 | 适合当前正在运行任务时执行 |
|---|---:|---:|
| 静态检查 | 否 | 是 |
| PowerShell `-DryRun` | 否，不启动 Python 导出 | 是 |
| HDC dry-run / probe / 正式导出 | 会操作设备 | 否 |

## 八、测试用例设计

### A. 静态与启动脚本测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| A01 | Python 语法检查 | 无 | `python -m py_compile D:\demo\wechat_info\TOOLS\wechat_hdc_export_0630.py D:\demo\wechat_info\TOOLS\wechat_hdc_capture.py` | 无输出，退出码为 0。 |
| A02 | PowerShell 启动脚本解析 | 无 | `powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 -DryRun -OutDir D:\demo\wechat_info\launcher_test` | 打印输出目录和 dry-run 成功，不启动设备控制。 |
| A03 | 自定义输出目录参数 | 无 | 同 A02，换不同 `-OutDir` | 打印的输出目录与传入路径一致。 |
| A04 | 默认时间戳目录 | 无 | `powershell -ExecutionPolicy Bypass -File D:\demo\wechat_info\TOOLS\run_wechat_export_0630_001.ps1 -DryRun` | 输出目录形如 `D:\demo\wechat_info\yyyyMMdd_HHmmss`。 |
| A05 | PowerShell 中文编码安全 | 无 | 查看脚本内容 | `.ps1` 内不直接传中文跳过名单，不出现乱码导致的引号损坏。 |

### B. HDC 设备连接测试

这些测试会读取或控制设备，正式任务运行时不要执行。

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| B01 | HDC 设备在线 | 设备 USB 连接并授权 | `hdc list targets -v` | 至少一个目标为 `USB Connected` 或 `USB Ready`。 |
| B02 | 不误选 UART | HDC 列表中存在 `COM256 UART Ready` | 执行 visible dry-run | 日志中的 `Target` 是 USB 设备，不是 `COM256`。 |
| B03 | 自动获取屏幕尺寸 | HDC 设备在线 | visible dry-run | 日志打印真实 `Screen: 宽x高`。 |
| B04 | 无密码锁屏解锁 | 设备处于无密码锁屏 | visible dry-run | 日志显示解锁尝试，最终进入微信或聊天列表。 |
| B05 | 微信自动启动 | 微信不在前台 | visible dry-run | 日志显示 `Launched WeChat with: aa start ...`，随后识别聊天列表。 |

### C. 聊天列表识别测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| C01 | 当前可见列表识别 | 微信在聊天列表 | visible dry-run | 打印当前可见聊天名称和 bounds。 |
| C02 | 中文会话名识别 | 列表中有中文会话 | visible dry-run | `detected_chats.csv` 包含真实中文名称。 |
| C03 | emoji 会话名识别 | 列表中有 emoji 群名 | scan-list dry-run | 日志和 CSV 正常写入，不出现编码异常。 |
| C04 | 长名称识别 | 列表中有长群名 | scan-list dry-run | 能识别 UI 显示出的标题文本，目录名合法化。 |
| C05 | 列表顶部寻址 | 当前列表不在顶部 | scan-list dry-run | 日志显示 `Seeking conversation list top...`，随后从顶部附近开始扫描。 |
| C06 | 多轮列表扫描 | 聊天列表超过一屏 | `--max-list-swipes 3` dry-run | 日志显示多轮 `LIST ROUND`，每轮新增可见会话。 |

### D. 跳过名单测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| D01 | 跳过公众号 | 列表中有 `公众号` | 正式小范围导出或观察日志 | 日志显示 `SKIP 公众号`，不创建普通截图目录。 |
| D02 | 跳过客服消息 | 列表中有 `客服消息` | 正式小范围导出或观察日志 | 日志显示 `SKIP 客服消息`。 |
| D03 | 跳过微信团队 | 列表中有 `微信团队` | 正式小范围导出或观察日志 | 日志显示 `SKIP 微信团队`。 |
| D04 | 跳过微信支付 | 列表中有 `微信支付` | 正式小范围导出或观察日志 | 日志显示 `SKIP 微信支付`。 |
| D05 | 跳过微信公众平台 | 列表中有 `微信公众平台` | 正式小范围导出或观察日志 | 日志显示 `SKIP 微信公众平台`。 |

### E. 折叠群聊测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| E01 | 折叠入口识别 | 主列表有 `折叠的群聊` | scan-list dry-run | dry-run 中识别该入口名称。 |
| E02 | 不把折叠入口当普通会话 | 正式导出且遇到折叠入口 | 正式导出 | 日志显示 `OPEN-FOLDER 折叠的群聊`，不是 `OPEN 折叠的群聊`。 |
| E03 | 折叠内群聊导出目录 | 折叠列表中有群聊 | 正式导出 | 输出到 `折叠的群聊_展开\群聊名\`。 |
| E04 | 折叠列表滚动 | 折叠群聊超过一屏 | 正式导出 | 日志显示 `FOLDER 折叠的群聊 ROUND ...` 多轮扫描。 |
| E05 | 防止重复扫描折叠入口 | 主列表滑动过程中再次看见折叠入口 | 正式导出 | 日志显示 `FOLDER-SKIP` 或只处理一次。 |

### F. 单会话截图与裁剪测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| F01 | 自动裁剪探针 | 手动进入一个普通聊天 | `wechat_hdc_capture.py --probe-only --crop auto` | 生成 `debug\probe_full.jpeg`、`debug\probe_crop.jpeg`。 |
| F02 | 自动裁剪准确性 | 已执行 F01 | 查看 `probe_crop.jpeg` | 裁剪图主要包含聊天内容区域，不包含过多顶部/底部导航。 |
| F03 | 手动裁剪参数 | 已知裁剪区域 | `--crop left,top,right,bottom --probe-only` | `crop_source.json` 中记录手动裁剪区域。 |
| F04 | 保存全屏与裁剪图 | 正式导出单个会话 | 正式导出 | `full_frames` 和 `chat_crops` 数量一致或接近。 |
| F05 | 指标文件生成 | 正式导出单个会话 | 正式导出 | `metrics.csv` 包含 ssim、phash、grid、diff、edge、ui 等字段。 |

### G. 到头判断与动态表情测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| G01 | 普通聊天到历史开头 | 聊天历史较短 | 正式导出 | `seek` 阶段连续稳定后停止上滑。 |
| G02 | 普通聊天到最新末尾 | 聊天历史较短 | 正式导出 | `capture` 阶段连续稳定后停止下滑。 |
| G03 | 最新页有动态表情 | 最新消息区域有动态表情 | 正式导出 | 不因单个动画无限截图，最终稳定停止。 |
| G04 | 历史开头有动态表情 | 历史开头附近有动态表情 | 正式导出 | 不因动画无限 seek，最终进入截图阶段。 |
| G05 | 同屏多个动态表情 | 同屏多个动画元素 | 正式导出 | 通过网格/边缘/UI 混合判断停止。 |
| G06 | 调整稳定阈值 | 默认判断过严或过松 | 用不同 `--stable-count`、`--grid-stable-ratio` 测试 | 日志中的 stable 次数与停止时机符合预期。 |

### H. Resume 与重复运行测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| H01 | 已完成会话跳过 | 某会话已有 `export_summary.json` | 再次正式导出同目录 | 日志显示 `RESUME-SKIP 会话名`。 |
| H02 | 未完成会话复用目录 | 某会话目录存在但无 `export_summary.json` | 再次正式导出同目录 | 复用该目录继续导出。 |
| H03 | 强制重导 | 已有完成目录 | 加 `--force` 运行 Python 主脚本 | 不执行 resume-skip，重新导出。 |
| H04 | 同名目录处理 | 有同名或相似会话目录 | 正式导出 | 目录名不非法，summary 能记录对应会话。 |

### I. 输出结果完整性测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| I01 | 批量索引生成 | 批量导出结束 | 查看输出目录 | 存在 `export_index.json`。 |
| I02 | 检测列表 CSV | dry-run 或正式导出结束 | 查看输出目录 | 存在 `detected_chats.csv`。 |
| I03 | 单会话 summary | 正式导出某会话完成 | 查看会话目录 | 存在 `export_summary.json`，含截图数量。 |
| I04 | 日志落盘 | 正式导出某会话完成 | 查看会话目录 | 存在 `capture_stdout.log` 和 `capture_stderr.log`。 |
| I05 | 图片数量合理 | 正式导出某会话完成 | 统计 `chat_crops` | 至少 1 张，长聊天多张。 |

### J. 异常与边界测试

| ID | 测试目标 | 前置条件 | 命令 | 预期结果 |
|---|---|---|---|---|
| J01 | HDC 不在线 | 断开设备或取消授权 | visible dry-run | 报错提示没有 USB HDC target ready。 |
| J02 | 设备在非微信页面 | 设备在桌面 | visible dry-run | 自动启动微信并尝试进入聊天列表。 |
| J03 | 微信不在聊天 tab | 微信在通讯录/发现/我 | visible dry-run | 尝试点击底部 `微信` tab。 |
| J04 | 聊天列表为空或未登录 | 微信未登录或无聊天 | visible dry-run | 报错 `No chat entries found`，不生成误导出。 |
| J05 | HDC 文件接收失败 | 设备存储或授权异常 | 任意导出 | 报错指出 layout/screenshot 未接收。 |
| J06 | 输出目录无权限 | 指定不可写目录 | 正式导出 | 本地创建目录失败，PowerShell/Python 报错。 |

## 九、建议的分阶段验收流程

在当前正式任务结束后，建议按这个顺序验证：

1. 执行 A01、A02、A03。
2. 执行 B01，确认 HDC 设备在线。
3. 执行 C01 visible dry-run，确认能读出当前会话名。
4. 执行 C06 scan-list 短 dry-run，确认列表滚动正常。
5. 手动进入一个短聊天，执行 F01/F02，确认裁剪区域正确。
6. 选择一个短会话做小范围正式导出，检查 F04/F05/I03/I04/I05。
7. 确认跳过名单和折叠群聊逻辑，再运行完整批量导出。

## 十、风险提示

- 正式导出运行期间不要手动点击设备。
- 不要同时启动多个导出任务控制同一台设备。
- 如果当前已有导出任务在跑，不要执行 dry-run、probe 或任何 HDC 命令测试。
- 如果微信升级后 UI 结构变化，优先用 visible dry-run 检查是否还能识别 `Title`。
- 若导出耗时很长，优先观察日志和当前输出目录，不要直接强制中断，除非确认需要停止。
