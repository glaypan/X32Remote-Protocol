# X32 Remote v1.1.0

> **本版主题：把「演出编排」搬进手机。**
> iOS 端补齐桥接模式与时间轴调度器，三端（服务端 / Android / iOS）功能全面对齐。

适配 **Behringer X32 / Midas M32** 数字调音台，走 OSC over UDP（端口 10023），手机与调音台同处一个局域网即可直接控制，不依赖中间服务。

---

## 本版新增

### 📱 iOS · 桥接模式

iPhone 直连真台，**App 内部维护一份本地虚拟台面**，不需要电脑中转。

- **写入转发 + 乐观更新**：界面立刻响应，同时转发给真台
- **回声抑制**：自己写出去的值回读时静默吸收（2 秒窗口 / 0.02 容差），不会误判成手动干预
- **桥接状态机**：`off` / `link` / `online` / `stale` 四态，界面实时可见
- **保活**：每 5 秒发 `/xremote`，防止真台取消遥控链接
- **真台手动干预检测**：真台面板被人动推子时，若该路正被卡片驱动即触发冲突策略
- **事件流**：冲突与通知在 App 内滚动展示

### ⏱ iOS · 时间轴调度器

与网页版、Android 版**完全同一套语义**（以服务端 `x32_simulator.py` 为权威实现）：

- **12 种动作**：`mute` / `mute_bus` / `scene` / `unmute_all` / `wait` / `fade_all` / `fade_odd_even` / `fade_ch` / `fade_bus` / `fade_from` / `fade_from_bus` / `rel_db`
- **`at` 时间轴偏移**：给了 `at` 的动作并行；**其后的顺序动作会顺延**（cursor 恒等于动作结束时刻）
- **4 条曲线**：`linear` / `ease_out` / `ease_in` / `smooth`
- **卡片链**：`next` 自动接续，带防环（已执行 id 集合 + 深度上限 16）
- **相对 dB**：`rel_db` 按当前推子位置换算，高低端手感一致
- **冲突策略**：手动干预**只冻结被碰的那一路**，其余通道与后续动作照常执行

### 🔧 修掉三处语义偏差

这三处是写完之后做「逐条对照权威实现」审查时才发现的。**语法检查、类型检查、完整语义检查全部通过也查不出来** —— 只能对着 `x32_simulator.py` 一行行读：

| # | 问题 | 后果 |
|---|---|---|
| 1 | `wait` 在时间轴引擎里额外 sleep 了一次 | 等待被**双重计时**：卡片总时长翻倍；更严重的是 sleep 期间调度循环被冻住，**并行动作与正在跑的渐变会一起卡停** |
| 2 | `unmute_all` 只写了通道与 Bus 共 48 路 | 漏掉 DCA / AuxIn / USB / FX Return / Matrix / Main 共 **31 路**未取消静音 |
| 3 | 卡片链之间少了 0.4 秒喘息 | 链式接续时台面与界面来不及看清切换 |

配套把 `共享协议规范.md` 升到 **v1.1**，把这些语义写死成契约（原文只写「`wait` 占用 `duration`」，正是这个含糊让三端各写各的）。

### 🧹 iOS 工程内务

- **删除 945 行废弃代码**：旧 `ShowRunner` 引擎与 `ShowCard`/`ShowAction`/`ShowFade` 模型等，避免两套调度器并存让人改错目标
- **修掉 CI 的长期隐患**：单元测试步骤原先在仓库根执行 `swift test`，而 `Package.swift` 在 `X32Remote-ios/` 子目录 → **测试从未真正运行过**，`continue-on-error` 把失败掩盖了。已修正工作目录并校验 `Executed N test` 输出
- **本地校验流程收进仓库**：`tools/local_verify.sh` + `tools/network_stub.py`，没有 Mac 也能对核心库做深度校验
- **DCA 成员地址修正**：`/ch/NN/grp/dca` → **`/ch/NN/mix/dca`**（旧写法的 DCA 成员读写会静默失效）
- **修掉 6 处 Swift 并发隔离错误**：`Button` 的 action、`onDelete`/`Binding.set` 的闭包都是 `@escaping` 闭包，**不继承外层 actor 隔离**，直接调用 `@MainActor` 方法会编译失败。已在调用处显式 `Task { @MainActor in … }`

---

## 构建与验证

| 项 | 结果 |
|---|---|
| 云端构建 | GitHub Actions run [`36260594878`](https://github.com/glaypan/X32Remote/actions/runs/36260594878) — **全部步骤通过** |
| 核心库单测 | ✅ 通过（SPM，在真机 SDK 上编译并执行） |
| 产物 | `X32Remote-unsigned.ipa` — 版本 **1.1.0**，build **2**，要求 **iOS 17.0+** |
| IPA SHA256 | `7551d96ba852e130e34ebe1a7c71e609f35edfdb94c9259cad4a2fb654408617` |

> 两个 zip 里嵌的 IPA 与单独提供的 `X32Remote-unsigned.ipa` **字节完全一致**（SHA256 已核对），可放心任选一种下载。

---

## 三端功能对照

| 能力 | 服务端+Web | Android | iOS |
|---|:---:|:---:|:---:|
| 局域网自动发现调音台 | ✅ | ✅ | ✅ |
| 通道 / Bus / DCA / Main 控制 | ✅ | ✅ | ✅ |
| EQ / 压缩 / LowCut / Bus Send / FX | ✅ | ✅ | ✅ |
| 电平表 | ✅ | ✅ | ✅ |
| 演出卡片（12 动作 + 4 曲线） | ✅ | ✅ | ✅ |
| 时间轴调度（`at` 并行 / `next` 链） | ✅ | ✅ | ✅ |
| `rel_db` 相对 dB 动作 | ✅ | ✅ | ✅ |
| 冲突策略（只冻结被碰那一路） | ✅ | ✅ | ✅ |
| 桥接模式 | ✅ | — | ✅ |
| 定时触发 / 危险卡防误触 | ✅ | — | — |
| 配置导入导出 | ✅ | — | — |
| 可脱离电脑独立使用 | ❌ | ✅ | ✅ |

> **定位差异**：iOS 后台约 30 秒会被系统挂起，**只能当前台遥控器，当不了常驻服务端**。需要「手机独立控台且常驻」请用 Android。

---

## 下载

| 文件 | 说明 |
|---|---|
| `X32Remote-v1.1.0-assets.zip` | **推荐**：仅成品（iOS IPA + Android APK + 发布说明），4.31 MB |
| `X32Remote-v1.1.0-full.zip` | 完整包：三端源码 + 成品 + 全部文档，4.53 MB |
| `X32Remote-1.0.0.apk` | Android 安装包（单文件） |
| `X32Remote-unsigned.ipa` | iOS 未签名包（单文件，需自行签名） |
| `SHA256SUMS.txt` | 校验和 |

### Android 安装

直接把 APK 传到手机点击安装。系统提示「禁止安装未知应用」时，进入 **设置 → 允许来自此来源的应用**。
要求 Android 8.0（API 26）+。

### iOS 安装（重要：IPA 未签名）

本项目没有付费 Apple 开发者账号，所以只提供**无签名** IPA，需用电脑签名后侧载：

1. 电脑上装 [Sideloadly](https://sideloadly.io/)，连上 iPhone
2. 把 IPA 拖进 Sideloadly，填你的 Apple ID，点 Start
3. 手机上 **设置 → 通用 → VPN 与设备管理 → 信任** 你的证书

⚠️ **要求 iOS 17.0+**（代码使用 Observation 框架，硬要求）
⚠️ 免费 Apple ID **每 7 天需重新签名**，最多同时 3 个自签 App
📖 完整流程见包内 `docs/iOS-真机直装指南.md`

---

## 升级注意

1. **iOS 需要 iOS 17+**，停在 iOS 16 或更低装不上。
2. **iOS 卡片库会回落到出厂卡片**：卡片格式两代差异较大，本版**不做自动迁移**。这是刻意选择 —— 自动转换容易产生错误动作（比如把静音错解成渐变），宁可让你重配。
3. 首次连接会申请**「本地网络」权限**，用于 OSC 通信与自动发现。不允许则连不上调音台。

---

## 已知限制

- **iOS IPA 必须自行签名**：无付费开发者账号，7 天重签是免费账号的硬限制。
- **iOS 不能后台常驻**：系统约 30 秒挂起后台网络活动。
- **Android 端桥接模式未实现**：Android 是「手机自跑引擎直连真台」，本身不需要桥接中转。但 **Android 的时间轴引擎存在与本版修复的同源三处偏差**，计划下一版修复。
- **真机联调项待实测**：OSC 连发最小间隔、真台推送实际延迟、`/xremote` 保活真实表现。目前按规范取值，需在真台上调优。
- **M32 / Wing 地址差异**未适配，仅覆盖 X32 系列。

---

## 本版提交

```
ece8fff  fix(app): 修正 Button/onDelete 逃逸闭包调用 @MainActor 方法的并发隔离错误
dd2d290  chore(ios): 升版本号到 1.1.0 (build 2)
a668527  fix(ios): 逐条比对服务端后修正时间轴引擎的三处语义偏差
7445c34  chore(tools): 把无 Mac 本地校验流程收进仓库
7b01d3b  refactor(ios): 移除被 ShowEngine 取代的旧时间轴实现（-945 行）
75af171  feat(ios): 内置桥接模式 + 时间轴调度器，对齐服务端权威语义
```

**完整变更**：https://github.com/glaypan/X32Remote/compare/462af1c...v1.1.0
