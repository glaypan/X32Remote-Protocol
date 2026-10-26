# X32 Remote v1.5.0

服务端规范 **v1.9** · 新增 **App 扫码配对**：电脑端设置页并排两张码，手机两条路都能扫——相机/微信扫「浏览器」码直接开网页控制台，App 内「扫码连接」扫「App」码自动填好 IP 与端口

## 重点

| # | 内容 |
|---|---|
| 1 | **App 扫码配对码**：新载荷 `x32remote://pair?v=1&host&osc&web` + `GET /pair.svg`。网页码里是 HTTP 端口、App 走 OSC/UDP，改过端口就会"连上了却没回包"——所以分两张码 |
| 2 | **设置页双二维码**：「浏览器」码给相机/微信，「App」码给 App 内扫码，各带复制按钮 |
| 3 | **iOS / Android 内置扫码**：AVFoundation / ZXing 取景，扫到即填 IP 与端口并连上；识别失败不动当前地址 |
| 4 | **三端解析器同一套规则**：接受配对码 / `http(s)://host:port` / `host:port`；回环地址、越界端口一律拒，不猜地址 |
| 5 | 终端横幅多打一行配对码原文，可复制转发 |

## 升级注意

- 无破坏性变更；`user_config.json` 无新增键。规范 v1.8 → **v1.9**（§16.9）。
- macOS 桌面版 **1.1.1**（内核 spec-v1.9）· Windows **1.1.1** · Android **1.3.0 (4)** · iOS **1.3.0 (4)**。
- App 需重装才有扫码入口（Android 1.3.0 / iOS 1.3.0）；网页端不受影响。
- 仍未接 deep link：系统相机扫「App」码不会拉起 App，需在 App 内用「扫码连接」。

## 验证

回归 **12/12 组**（新增 `pair` 组 29 项；`params` 386 项含 6 条接口断言）。
双形态一致：`/pair.svg` 在包与单文件下逐字节相同。
Windows 发布包实测 `pairCode` 与 `/pair.svg` 200；Android 单测 10 项通过；
iOS CI 成功且二进制含扫码符号；macOS 离线校验 65 个 Mach-O 最高 minos **10.14.6**、
内置内核 sha256 对 `spec-v1.9`。

## 下载（按平台）

| 平台 | 文件 | 要求 |
|---|---|---|
| **macOS（Commander）** | `X32Remote-macOS-1.1.1-unsigned.zip` | **macOS 10.14.6+ / Intel(x86_64)**，Apple Silicon 走 Rosetta 2；解压后拖进「应用程序」，首次右键打开 |
| **Windows** | `X32Remote-1.1.1-windows-x64.zip` | Windows 10 / 11 **64 位**；解压双击 `X32Remote.exe`，**免装 Python**；关窗口即停服务 |
| **Android** | `X32Remote-1.3.0.apk` | Android 8.0+，直接安装（含扫码连接，首扫要相机权限） |
| **iOS** | `X32Remote-unsigned.ipa` | iOS 17+，未签名，需用 Sideloadly 以免费 Apple ID 自签（每 7 天重签） |
| **网页试用** | `X32Remote-WebDemo.html` | 任意现代浏览器双击即开（纯前端快照，停留在 2026-10-18，不含扫码与自动页签） |

## 两条手机遥控路怎么用

1. **扫码 = 浏览器**（零安装）：电脑端打开控制台 → 设置 →「手机 / 安卓平板连接」，
   手机相机/微信扫左边「浏览器」码。手机和电脑看同一份状态（同一进程），
   推子、场景、卡片、自动增益都能点。
2. **原生 App = 扫码或手填**：iOS/Android 里点「扫码连接」扫右边「App」码
   （扫左码也行，按默认 10023 处理）；或手填电脑 IP、端口 10023。

> 连不上时按这个顺序查：同网段（别连 5G/访客网，酒店公司 WiFi 常开 AP 隔离）
> → 防火墙（Windows 首次运行勾「专用网络」；macOS 允许内置 python3 传入连接）
> → 端口顺延（8080 被占时会用 8081+，码里带的是实际端口）→ VPN 取错 IP（改手输）。

## 产物

`X32Remote-v1.5.0-full.zip`（全部源码 + 移动端产物 + 文档）·
`X32Remote-v1.5.0-assets.zip`（移动端成品 + 发布说明）·
`SHA256SUMS.txt`（`sha256sum -c` 可直接校验）·
桌面版是几十 MB 的自带运行时二进制，**不进** full/assets 包，单独作为附件。

安装与排错见 `outputs/发布流程与全平台安装指南-2026-10-23.md`。
