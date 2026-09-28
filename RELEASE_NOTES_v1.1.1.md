# X32 Remote v1.1.1 发布说明

> **本版主题：把 v1.1.0 里修好的东西，同样修到 Android 上。**
>
> v1.1.0 修掉的是「服务端语义 vs iOS 实现」的偏差。本版把同一批偏差在
> **Android 端**修完，并额外修掉一处 Android 卡片链的实现缺陷。至此三端行为一致。
>
> 发布对象是 **Behringer X32 / Midas M32** 数字调音台，走 OSC over UDP（端口 10023），
> 手机与调音台同处一个局域网即可直接控制，不依赖任何中间服务。

---

## 一、这版修了什么

### ⚠️ 1. `wait` 曾经把整个引擎线程睡死（Android）

v1.1.0 之前，Android 的 `wait` 动作实现是：

```kotlin
"wait" -> Thread.sleep((a.duration * 1000).toLong())
```

两处错：

| 问题 | 后果 |
|---|---|
| **双重计时** | 等待效果本已由时间轴 cursor 提供（`wait` 的结束时刻成为下一个顺序动作的开始），再 `sleep` 一次 → 卡片总时长翻倍 |
| **阻塞整个引擎线程** | `Thread.sleep` 卡住的是执行卡片的那个线程，**并行动作与正在跑的渐变会一起停住** |

服务端 `_exec_instant` **根本没有 wait 分支** —— `wait` 是**纯 no-op**：不写 OSC、
不 sleep，只占用时间轴。现已改为一致实现。

> v1.1.0 已在 iOS 修过同一个问题（那边是 `Task.sleep`）。Android 因为用了
> 阻塞式 `Thread.sleep`，症状比 iOS 更重。

### ⚠️ 2. `unmute_all` 漏了 31 路（Android）

原实现只写通道与 Bus：

```kotlin
for (i in 1..32) sender.setValue("ch_on", i, 1f, "action")
for (i in 1..16) sender.setValue("bus_on", i, 1f, "action")
```

漏掉 DCA / AuxIn / USB / FX Return / Matrix / Main。服务端的定义是 **8 组共 79 路**：

| 组 | 路数 |
|---|---|
| ch | 32 |
| bus | 16 |
| dca | 8 |
| auxin | 6 |
| usb | 2 |
| fxret | 8 |
| mtx | 6 |
| main | 1（idx=0） |
| **合计** | **79** |

### ⚠️ 3. `fade_from` 的起点参数形同虚设（Android）

`fade_from` / `fade_from_bus` 的语义是「**从指定值** `frm` 渐变到 `to`」。
服务端的做法是先写 `frm`、再快照起点：

```python
if act["kind"] in ("fade_from", "fade_from_bus"):
    fk, idx = targets[0][0], targets[0][1]
    set_value(fk, idx, float(act.get("frm", 0.0)), source="action")
starts = [(k, i, get_value(k, i), b) for k, i, b in targets]
```

Android 的卡片循环把瞬时动作与渐变分开处理（`if (!e.isFade)`），渐变的
`startValues` 直接在首次激活时取当前值 —— **`frm` 填了也不会被用到**，
实际总是从推子当前值出发。现已在取起点前先写入 `frm`。

### 🔧 4. 卡片链接续会把自己停掉（Android，额外发现）

原 `finishChain` 的实现是：

```kotlin
run(nxt, chainDepth + 1)   // 注意：run() 内部会 stopAll() 并新建线程
```

而 `run()` 第一件事就是 `stopAll()`（置 `stopFlag=true` 并清 fadeZone）。
在链式接续里这样调用，等于**当前线程自己把自己停掉**，还留下竞态。
同时它少了服务端的 **0.4 秒喘息**。现已改为就地接续 + 喘息。

### 🔩 5. 顺带修掉的测试基建问题（Android）

`ShowEngine` 之前直接调用 `android.os.SystemClock.elapsedRealtime()`。
这在 JVM 单元测试里是**抛异常的空桩**，导致**卡片执行路径从来无法被测试覆盖**
（既有的 66 项测试只覆盖纯逻辑：解析 / 净化 / 目标列表）。

现已把时钟做成可注入：

```kotlin
class ShowEngine(
    private val state: MixerState,
    private val sender: OscSender,
    private val clockMs: () -> Long = { SystemClock.elapsedRealtime() }
)
```

补充了 8 项执行语义回归测试（`wait` 不写值 / `wait` 不阻塞并行 / cursor 确实推进 /
`unmute_all` 的 79 路 / `fade_from` 先写 `frm` / `fade_from_bus` 同 / 链式接续）。
Android 测试总数 **66 → 73 项，全部通过**。

---

## 二、契约文档已纳入版本控制

`共享协议规范.md` 是三端共同依赖的契约，此前**游离在版本控制之外** —— 没有历史可追，
出问题只能靠记忆比对（v1.1.0 这三处偏差正是这么来的）。

现建立仓库 `x32遥控/` 并纳入：

| 纳入 | 说明 |
|---|---|
| `共享协议规范.md` | **核心契约**，本版升到 v1.2 |
| `X32Simulator/` | 服务端权威实现 + 全部回归脚本 |
| `tools/` | 发布与校验工具链 |
| 安装指南 / 发布说明 / 校验和 | |

刻意不纳入：`X32Remote-ios/`（已是独立仓库，避免双份真相）、
二进制产物（走 Release 附件）、本机运行时状态。

---

## 三、三端功能对照（本版后）

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

> **Android 为什么没有桥接模式**：Android 是「手机自己跑引擎直连真台」，
> 本身就等效于桥接，不需要再中转一层。
>
> **定位差异**：iOS 后台约 30 秒会被系统挂起，**只能当前台遥控器，当不了常驻服务端**。
> 需要「手机独立控台且常驻」请用 Android。

---

## 四、下载

| 文件 | 说明 |
|---|---|
| `X32Remote-v1.1.1-assets.zip` | **推荐**：仅成品（Android APK + iOS IPA + 发布说明） |
| `X32Remote-v1.1.1-full.zip` | 完整包：全部源码 + 成品 + 全部文档 |
| `X32Remote-1.1.0.apk` | Android 安装包（单文件，本版更新） |
| `X32Remote-unsigned.ipa` | iOS 未签名包（单文件，需自行签名） |
| `SHA256SUMS.txt` | 校验和 |

### Android 安装

直接把 APK 传到手机点击安装。系统提示「禁止安装未知应用」时，进入
**设置 → 允许来自此来源的应用**。要求 Android 8.0（API 26）+。

本版 APK 与上一版**使用同一个签名密钥**，可直接覆盖安装升级，不会冲突。

### iOS 安装（IPA 未签名）

1. 电脑上装 [Sideloadly](https://sideloadly.io/)，连上 iPhone
2. 把 IPA 拖进 Sideloadly，填你的 Apple ID，点 Start
3. 手机上 **设置 → 通用 → VPN 与设备管理 → 信任** 你的证书

⚠️ **要求 iOS 17.0+**（代码使用 Observation 框架，硬要求）
⚠️ 免费 Apple ID **每 7 天需重新签名**，最多同时 3 个自签 App
📖 完整流程见包内 `docs/iOS-真机直装指南.md`

---

## 五、升级注意

1. **iOS 需要 iOS 17+**，停在 iOS 16 或更低装不上。
2. **iOS 卡片库会回落到出厂卡片**：卡片格式两代差异较大，本版不做自动迁移。
   这是刻意选择 —— 自动转换容易产生错误动作，宁可让你重配。
3. 首次连接会申请**「本地网络」权限**，用于 OSC 通信与自动发现。
4. **Android 无破坏性变更**，直接覆盖安装即可。

---

## 六、已知限制

- **iOS IPA 必须自行签名**：无付费开发者账号，7 天重签是免费账号的硬限制。
- **iOS 不能后台常驻**：系统约 30 秒挂起后台网络活动。
- **Android 端桥接模式未实现**：见上文说明——它不需要。
- **真机联调项待实测**：OSC 连发最小间隔、真台推送实际延迟、`/xremote` 保活真实表现。
  目前按规范取值，需在真台上调优。
- **M32 / Wing 地址差异**未适配，仅覆盖 X32 系列。

---

## 七、本版提交

**Android 仓库**（`X32RemoteAndroid`）：卡片引擎三处语义偏差 + 链式接续 + 可注入时钟
**契约仓库**（`x32遥控`）：建立仓库，纳入 `共享协议规范.md`（升 v1.2）及配套内容

**变化对比**：https://github.com/glaypan/X32Remote/compare/v1.1.0...v1.1.1
