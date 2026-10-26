# X32 Remote v1.5.0 发布说明

服务端规范 **v1.9** · 新增 **App 扫码配对**（电脑端两张码，手机两条路都能扫）
适配调音台：Behringer X32 / Midas M32（OSC over UDP，端口 10023）

## 本版重点

### 1. App 扫码配对码（新协议载荷）

新增 `x32remote://pair?v=1&host=…&osc=…&web=…` 与 `GET /pair.svg`。

起因是一个会**安静失败**的坑：网页二维码里编码的是 `http://ip:8080/`，
那个端口是 **HTTP** 的；原生 App 走 **OSC/UDP**（默认 10023）。
用户把 UDP 端口改成 10025 之后，拿 App 去扫浏览器那张码，会"连上了但一条回包都没有"。
所以本版把两张码拆开：

| 码 | 端点 | 内容 | 谁扫 |
|---|---|---|---|
| 浏览器 | `/qr.svg` | `http://ip:web/` | 手机相机 / 微信 → 开网页 |
| App | `/pair.svg` | `x32remote://pair?v=1&host&osc&web` | App 内「扫码连接」 |

配对码 58 字节 → 二维码版本 4，屏幕上 118 px 也够扫。
`/api/info` 新增 `pairCode`（配对码原文，探测不到局域网时为空串）与 `pairQr`。

### 2. 设置页双二维码

**设置 → 手机 / 安卓平板连接** 现在并排两张码，各带「复制地址」「复制配对码」按钮，
下方文字说明各自给谁扫。桌面版（Windows / macOS Commander）同一份内核，行为一致。

### 3. iOS / Android 内置扫码

- **iOS**：设置 → 快速连接 →「扫码连接」，AVFoundation 取景，识别即回调；
  无相机权限时给「去设置」引导。版本 **1.3.0 (build 4)**。
- **Android**：菜单首项「扫码连接」，ZXing 取景；相机权限自己先申请
  （ZXing 的 CaptureActivity 在不同版本上权限处理不一致，交给它会出现"黑屏但不报错"），
  拒绝时给明确提示。版本 **1.3.0 (code 4)**，清单补 `CAMERA` 权限
  （`camera` 标记为非必需，无相机设备仍可安装，手填 IP 全功能可用）。

两端扫到即连；**识别失败不动当前地址**——半截地址比不连更让人困惑。
扫左边那张浏览器码也可以用：App 按默认端口 10023 处理。

### 4. 三端解析器同一套规则

服务端 `x32sim/pair.py`、iOS `PairCode.swift`、Android `PairCode.kt` 逐条对齐：

1. `x32remote://pair?...` —— 完整配对
2. `http(s)://<host>[:port]/` —— 网页码，`osc` 按默认 10023、`web` 取 URL 端口
3. `<host>[:port]` —— 手输 / 其它工具生成的码

坏值一律拒绝，**不猜一个地址去连**：`javascript:`、`ftp://`、端口越界（1–65535）、
回环地址（`127.*` / `localhost` / `0.0.0.0`）、长度 > 200。

### 5. 终端横幅

启动横幅多打一行配对码原文（`App 扫码配对码: x32remote://pair?...`），
可复制发给别人。二维码仍只在真终端里绘制（`--no-qr` 或重定向输出时跳过）。

## 升级注意

- **无破坏性变更**。`user_config.json` 无新增键；App 端扫码是纯新增入口。
- 规范 v1.8 → **v1.9**，新增 §16.9（App 扫码配对码）。旧版 App 不受影响。
- macOS 桌面版 **1.1.1**（内核 spec-v1.9）、Windows 桌面版 **1.1.1**、
  Android **1.3.0 (4)**、iOS **1.3.0 (4)**。
- 仍未接 deep link：`x32remote://` 目前只被 App 内扫码界面识别，
  用系统相机扫右边那张码不会自动拉起 App（需要 `CFBundleURLTypes` / intent-filter）。

## 验证

- Python 全量回归 **12/12 组**（新增 `pair` 组 29 项；`params` 组补 6 条接口断言）。
- 双形态一致：`/pair.svg` 在包与单文件下逐字节相同。
- Windows 发布包实测：`pairCode=…osc=10028&web=8091`、`/pair.svg` 200（4198 字节 SVG）。
- Android：`PairCodeTest` 10 项通过；APK 1.3.0 (4)、5.1 MB，含 CAMERA 与 CaptureActivity。
- iOS：CI 成功（1.3.0 / build 4），二进制含 `PairCode` / `QRScanner` /
  `AVCaptureMetadataOutput` 符号，Info.plist 含 `NSCameraUsageDescription`。
- macOS：离线校验通过——65 个 Mach-O 最高 minos **10.14.6**、全 x86_64、
  内置内核 sha256 与协议仓 `spec-v1.9` 一致（`716b45a2…22ca`）。

## 已知限制

1. **服务端仍无鉴权**：同网段谁扫到谁都能控，与真台 OSC 同一信任模型；
   演出网络建议单独 SSID。
2. **iOS 后台约 30 秒挂起** → 只能当前台遥控器；需常驻服务端请用 Android / 桌面版。
3. **macOS 未签名、未公证**：首次需右键打开（$99 Apple 开发者账号可解）。
4. **Windows 未签名**：SmartScreen 会警告，点"更多信息 → 仍要运行"。
5. **26 项参数出厂默认值仍待真机核对**（`/api/info.calibration` 会列出）。
6. **网页试用版快照停留在 2026-10-18**：不含扫码配对、自动增益页签与通道参数页签，
   看完整新界面请用在线版或本地跑 X32Simulator。
7. **`x32remote://` 未接系统级 deep link**：系统相机扫 App 码不会拉起 App，
   需在 App 内使用「扫码连接」。

## 产物

`X32Remote-v1.5.0-full.zip`（全部源码 + 移动端产物 + 文档）·
`X32Remote-v1.5.0-assets.zip`（移动端成品 + 发布说明）·
`SHA256SUMS.txt`（`sha256sum -c` 可直接校验）·
桌面版（macOS / Windows）与移动端（APK / IPA）为独立附件。
