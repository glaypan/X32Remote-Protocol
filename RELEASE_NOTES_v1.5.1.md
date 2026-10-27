# X32 Remote v1.5.1 发布说明

服务端规范 **v1.9** · 新增 **系统级 Deep Link**（系统相机扫码自动打开 App）
+ **macOS 桌面版紧急修复**（v1.1.0–1.1.2 在干净 Mac 上无法启动）
适配调音台：Behringer X32 / Midas M32（OSC over UDP，端口 10023）

## 本版重点

### 1. 系统级 Deep Link：相机扫码直接打开 App

v1.5.0 的配对码只能被 **App 内** 的扫码界面识别；用系统相机或微信扫
电脑端右边那张 App 码，只会识别出一段 `x32remote://pair?...` 文本，
不会拉起 App。本版把 `x32remote://` 注册为系统 URL scheme：

- **iOS**：`CFBundleURLTypes` 声明 scheme `x32remote` / host `pair`，
  `onOpenURL` → `AppModel.handleOpenURL`（`@MainActor`，解析失败弹提示不崩）。
- **Android**：`intent-filter`（VIEW + DEFAULT + BROWSABLE）+ `launchMode="singleTask"`，
  `onNewIntent` 收深链；**冷启动深链优先于自动重连**（先满足用户刚扫的意图）。
- 两端与 App 内扫码共用同一个解析器（`PairCode.swift` / `PairCode.kt`），
  规则和服务端 `x32sim/pair.py` 逐条对齐：配对码 / `http(s)://host:port` / `host:port`；
  回环、越界端口、`javascript:`、超长一律拒，**不猜地址**。

现在两条路真正打通：扫左边浏览器码 → 开网页控制台；扫右边 App 码 →
无论用 App 内扫码还是系统相机，都会自动打开 App 并连上。

### 2. macOS 桌面版 1.1.3：紧急修复（重要）

**事故**：v1.1.0 / v1.1.1 / v1.1.2（含 v1.5.0 发布包里的 macOS 附件）内置的
Python 是在带 Homebrew 的构建机上编译的，configure 自动探测把 Homebrew 的
keg-only 库链了进来，产物写死构建机私有绝对路径：

```
dyld: Library not loaded: /usr/local/opt/gettext/lib/libintl.8.dylib
  Referenced from: X32Remote.app/Contents/Resources/python/bin/python3
  Reason: image not found
```

中招 9 个 Mach-O：`python3`/`python3.14`（libintl）、`_dbm`/`_gdbm`（gdbm）、
`_decimal`（mpdecimal）、`_ssl`/`_hashlib`（openssl@3）、`_lzma`（xz）、`_zstd`（zstd）。
只要 Mac 上没装这些 Homebrew 包，模拟器内核就起不来，App 弹「模拟器未能启动」。
旧校验脚本只查架构和最低系统版本，查不到这类问题，所以漏过了。

**修复（三层，全部进 CI 硬闸）**：

1. configure 缓存变量开关禁用 libintl / gdbm / ndbm / openssl / lzma / zstd 探测，
   `_decimal` 改链 CPython 自带静态 libmpdec；
2. pkg-config 过滤器拒绝 liblzma / libzstd（configure 没有 `--without` 开关，
   `PKG_CHECK_MODULES` 只认 pkg-config 结果）；
3. 构建后硬闸：`make install` 后与组装 `.app` 后各扫一遍 Mach-O，
   任何指向 `/usr/local/opt`、`/opt/homebrew` 的动态库直接让构建失败；
   离线校验脚本同步新增「外部私有动态库引用必须为 0」检查项。

被禁用的模块（ssl / lzma / zstd / gdbm / dbm）本应用均不使用：内核只依赖
纯标准库与 hashlib 内置 `_sha*`，功能不受影响。**临时兜底**（不必升级）：
`brew install gettext gdbm mpdecimal openssl@3 xz zstd`，但建议直接升级。

## 版本一览

| 平台 | 版本 | 说明 |
|---|---|---|
| iOS | **1.3.1 (build 5)** | 系统级 deep link |
| Android | **1.3.1 (code 5)** | 系统级 deep link + singleTask |
| macOS | **1.1.3** | Homebrew 依赖修复 + spec-v1.9 内核 |
| Windows | **1.1.2** | spec-v1.9 内核（配对码 / 双二维码） |
| 在线控制台 | — | 已随 spec-v1.9 更新 |
| 网页试用版 | 2026-10-18 快照 | 纯前端虚拟台，不含本版界面（见已知限制） |

## 升级注意

- **无破坏性变更**：服务端规范仍为 **v1.9**，`user_config.json` 无新增键；
  本版是 App / 桌面端增强 + 打包修复，三端协议零变化。
- **macOS 用户请务必升级**：旧版在未安装上述 Homebrew 包的机器上无法启动内核。
- iOS / Android 升级后，系统相机扫 App 码即可直接打开 App（旧版无此能力，
  但 App 内「扫码连接」照常可用，不升级也不影响原有功能）。

## 验证

- macOS 1.1.3：离线校验通过——60 个 Mach-O 最高 minos **10.14.6**、全 x86_64、
  **外部私有动态库引用 = 0**、内置内核 sha256 与协议仓 `spec-v1.9` 一致。
- CI 冒烟：标准库导入、重定位、部署目标断言、模拟器无头启动 + `/api/state` 全过。
- Python 全量回归 **12/12 组**（含 `pair` 组 29 项）；
  Android `PairCodeTest` 10 项通过。
- iOS / Android 深链：`Info.plist` 含 `CFBundleURLTypes`（scheme x32remote）、
  清单含 `x32remote` scheme 的 intent-filter 与 `singleTask`。

## 已知限制

1. **服务端仍无鉴权**：同网段谁扫到谁都能控，与真台 OSC 同一信任模型；
   演出网络建议单独 SSID。
2. **iOS 后台约 30 秒挂起** → 只能当前台遥控器；需常驻服务端请用 Android / 桌面版。
3. **macOS / Windows 未签名**：首次需右键打开（macOS 还需在
   系统设置 → 隐私与安全性 点「仍要打开」）。
4. **网页试用版快照停留在 2026-10-18**：不含本版的扫码配对、深链相关界面，
   看完整新界面请用在线版或本地跑 X32Simulator。
5. **26 项参数出厂默认值仍待真机核对**（`/api/info.calibration` 会列出）。

## 产物

`X32Remote-v1.5.1-full.zip`（全部源码 + 移动端产物 + 文档）·
`X32Remote-v1.5.1-assets.zip`（移动端成品 + 发布说明）·
`SHA256SUMS.txt`（`sha256sum -c` 可直接校验）·
桌面版（macOS 1.1.3 / Windows 1.1.2）与移动端（APK 1.3.1 / IPA 1.3.1）为独立附件。
