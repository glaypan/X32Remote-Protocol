# X32 Remote v1.5.1

服务端规范 **v1.9** · 新增 **系统级 Deep Link**：系统相机 / 微信扫电脑端「App」码直接打开 App 并连上；
外加 **macOS 桌面版紧急修复**（v1.1.0–1.1.2 在干净 Mac 上无法启动，务必升级）

## 重点

| # | 内容 |
|---|---|
| 1 | **系统级 Deep Link**：`x32remote://` 注册为系统 URL scheme（iOS `CFBundleURLTypes` / Android intent-filter + `singleTask`）。相机扫码 → 自动打开 App → 自动填好连接；冷启动深链优先于自动重连 |
| 2 | **macOS 1.1.3 紧急修复**：v1.1.0–1.1.2 内置 Python 链了构建机上 Homebrew 的 keg-only 库（`/usr/local/opt/...` 绝对路径），干净 Mac 上 `dyld: Library not loaded`，模拟器起不来。已从构建层根治，CI 加硬闸 |
| 3 | **设置页双二维码**：「浏览器」码给相机/微信，「App」码给 App 扫码，并排展示各带复制按钮 |
| 4 | **iOS / Android 内置扫码**：AVFoundation / ZXing 取景，扫到即连；识别失败不动当前地址 |
| 5 | **三端解析器同一套规则**：配对码 / `http(s)://host:port` / `host:port`；回环地址、越界端口一律拒，不猜地址 |

## macOS 1.1.3 修了什么（重要）

v1.1.0 / v1.1.1 / v1.1.2（含 v1.5.0 附件里的 macOS 包）内置 Python 是在带 Homebrew 的构建机上编译的，
自动探测把 Homebrew keg-only 库链进产物并写死绝对路径，没装这些包的 Mac 上内核直接起不来：

```
dyld: Library not loaded: /usr/local/opt/gettext/lib/libintl.8.dylib
  Referenced from: X32Remote.app/Contents/Resources/python/bin/python3
  Reason: image not found
```

中招 9 个 Mach-O：libintl / gdbm / mpdecimal / openssl@3 / xz / zstd。
修复三层：configure 开关禁用对应探测（`_decimal` 改链自带静态库）、pkg-config 过滤器、
**构建后硬闸**（任何指向 `/usr/local/opt`、`/opt/homebrew` 的动态库使构建失败）。
被禁模块本应用均不用（内核只依赖纯标准库 + hashlib 内置 `_sha*`），功能不受影响。
**不想升级的临时兜底**：`brew install gettext gdbm mpdecimal openssl@3 xz zstd`。

## 升级注意

- **无破坏性变更**：规范仍为 v1.9，`user_config.json` 无新增键；本版是 App / 桌面端增强 + 打包修复。
- **macOS 用户务必升级**：旧版在未安装上述 Homebrew 包的机器上无法启动内核。
- iOS / Android 升级后系统相机扫「App」码即可直接打开 App（不升级也能用 App 内「扫码连接」）。

## 验证

- Python 全量回归 **12/12 组**（含 `pair` 组 29 项）；Android `PairCodeTest` 10 项通过。
- macOS 1.1.3 离线校验：60 个 Mach-O 最高 minos **10.14.6**、全 x86_64、
  **外部私有动态库引用 = 0**、内置内核 sha256 对 `spec-v1.9`（`3f8f4d06…11de`）。
- iOS 1.3.1 (5) 二进制含 `CFBundleURLTypes`（scheme `x32remote`）；
  Android 1.3.1 (5) 清单含 `x32remote` intent-filter 与相机权限。

## 下载（按平台）

| 平台 | 文件 | 要求 |
|---|---|---|
| **macOS（Commander）** | `X32Remote-macOS-1.1.3-unsigned.zip` | **macOS 10.14.6+ / Intel(x86_64)**，Apple Silicon 走 Rosetta 2；解压后拖进「应用程序」，首次右键打开 |
| **Windows** | `X32Remote-1.1.2-windows-x64.zip` | Windows 10 / 11 **64 位**；解压双击 `X32Remote.exe`，**免装 Python**；关窗口即停服务 |
| **Android** | `X32Remote-1.3.1.apk` | Android 8.0+，直接安装（含扫码连接，首扫要相机权限） |
| **iOS** | `X32Remote-unsigned.ipa` | iOS 17+，未签名，需用 Sideloadly 以免费 Apple ID 自签（每 7 天重签） |
| **网页试用** | `X32Remote-WebDemo.html` | 任意现代浏览器双击即开（纯前端快照，停留在 2026-10-18，不含深链与扫码界面） |

完整改动见 `RELEASE_NOTES_v1.5.1.md`；源码包 `X32Remote-v1.5.1-full.zip` / `-assets.zip`，
校验和 `SHA256SUMS.txt`（`sha256sum -c` 可直接校验）。
