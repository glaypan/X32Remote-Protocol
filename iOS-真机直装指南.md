# iOS 真机直装指南（免费 Apple ID · 7 天重签）

> 目标：把这套 X32 遥控器装到你的 iPhone 上，**不花 688 元开发者年费**。
> 前提：你手上只有 Windows，没有 Mac。

---

## 一、先理解为什么绕这么一圈

iOS 和 Android 有个根本差异：

| | Android | iOS |
|---|---|---|
| 编译产物 | 任意电脑可产出 APK | **只能由 macOS + Xcode 产出** |
| 安装 | 双击 APK 即可 | 必须**签名**才能装 |
| 免费签名 | 不需要 | 免费 Apple ID 可签，但**证书只有 7 天** |

所以链路必然是三段：

```
① 云端 macOS 编译  →  ② 产出无签名 IPA  →  ③ Windows 上用你的 Apple ID 签名安装
   (GitHub Actions)      (下载到本地)          (Sideloadly)
```

**已经帮你验证过的网络情况**：本机访问 `github.com` 网页会超时（22 秒无响应），
但 `api.github.com`、`codeload.github.com`、`objects.githubusercontent.com` 以及 **git 协议本身都是通的**。
因此代码推送和产物下载都没问题 —— 下载脚本特意全程走 API，不碰网页。

---

## 二、准备清单

| 项 | 说明 | 是否必须 |
|---|---|---|
| iPhone | 系统 **iOS 17** 或更高（App 用了 iOS 17 的 Observation 框架） | ✅ |
| Apple ID | 普通免费账号即可，**无需开发者账号** | ✅ |
| GitHub 账号 | 用来跑免费的 macOS 云端编译 | ✅ |
| GitHub PAT | 一串令牌，给下载脚本用 | ✅ |
| Sideloadly | Windows 上的签名安装工具，免费 | ✅ |
| iTunes | 提供 Apple 设备驱动（微软商店版即可） | ✅ |

### 构建状态（已实测跑通）

整条链路已在 2026-09-25 端到端验证：**云端编译成功**，产物
`outputs/X32Remote-unsigned.ipa`（0.50 MB，arm64，无签名）已确认结构正常，
可直接交给 Sideloadly。

首次真正编译时暴露出三个此前无法发现的问题，均已修复并推送：

| 问题 | 症状 | 原因 |
|---|---|---|
| 工程缺 SPM 包引用 | `Missing package product 'X32RemoteCore'` | 工程声明了产品依赖，却没有 `XCLocalSwiftPackageReference`，也没在 `packageReferences` 里登记 —— 语法合法但语义不完整 |
| 部署目标设错 | `'Observable()' is only available in iOS 17.0` 等约 80 条 | 工程写的是 iOS 15，但代码用了 iOS 17 的 `@Observable`、`@Environment(Type.self)` |
| App 层代码错误 | `$0` 指向错误闭包、actor 隔离冲突、`Color.tertiary` 不存在 | UI 层从未被编译过（此前 92 项测试只覆盖 SPM 核心库） |

> 这也是为什么现在多了 `verify_xcodeproj.py` —— 它检查的是**语义完整性**
> （引用闭合、包路径能否解析到 `Package.swift`、产品名是否真的被声明），
> 而不只是「文件能不能解析」。前两类问题语法上都合法，本地完全看不出。

### 关于 GitHub Actions 费用

- **公开仓库**：macOS runner 免费，额度基本用不完
- **私有仓库**：每月 2000 分钟，但 macOS runner 按 **10 倍**计费 → 实际约 **200 分钟/月**
- 一次构建约 **4–6 分钟** → 私有仓库每月约能构建 30 多次，够用

> 代码里没有任何隐私内容，但如果你想省额度，**用公开仓库更划算**。

### 创建 GitHub Token

1. 登录 GitHub → 右上角头像 → **Settings**
2. 左侧拉到最底 → **Developer settings**
3. **Personal access tokens → Tokens (classic) → Generate new token (classic)**
4. 勾选下面**两项**（缺一不可）：

| 勾选项 | 用途 | 不勾的后果 |
|---|---|---|
| **`public_repo`**（公开仓库）<br>或 **`repo`**（私有仓库、以及公开仓库更稳） | 建仓库 + 推送代码 | 建仓被拒（HTTP 403） |
| **`workflow`** | 推送 `.github/workflows/` 下的流水线文件 | 推送被拒：`refusing to allow a Personal Access Token to create or update workflow ... without 'workflow' scope` |

5. 有效期选 90 天，生成后**立刻复制**（只显示一次）

> ⚠️ **`workflow` 这一项最容易漏。** 本仓库的第一个提交里就包含
> `.github/workflows/build-ios.yml`，只要该提交含 workflow 文件，GitHub 就会
> 校验这个权限 —— 哪怕你只是改了一个 `.md` 文件也会被拦。
>
> 补勾时**不需要重新生成 Token**：经典 Token 改 scope 后值不变，改完直接重跑脚本即可。
> `push_to_github.py` 会在推送前自动核对 scope（GitHub 在响应头里回 `X-OAuth-Scopes`），
> 缺哪项会直接列给你，不会等推到一半才失败。

> 若用**细粒度 Token**：资源所有者选本人、仓库访问选 **All repositories**
> （新仓库尚不存在，无法预先选中），并授予
> `Administration: Read and write`（建仓）、`Contents: Read and write`（推送）、
> `Workflows: Read and write`（推 workflow 文件）、`Actions: Read`（读构建状态与产物）。

---

## 三、第一步 · 推送 + 编译 + 下载（一条命令）

**本地仓库已经帮你初始化并提交好了**（含云端流水线、共享 scheme、ID 已规范化），
所以从「推到 GitHub」到「拿到 IPA」只需要一条命令：

```bash
cd "E:/AI/workbuddy/x32遥控/X32Remote-ios/tools"

set GITHUB_TOKEN=ghp_你刚才生成的Token
python ios_build.py
```

它会依次做四件事，每步都有明确输出，失败会告诉你是哪一步：

| 步骤 | 做什么 |
|---|---|
| 1 | 用 Token 确认你的 GitHub 身份 |
| 2 | 创建公开仓库 `X32Remote`（已存在则复用），推送 `main` 分支 |
| 3 | 轮询等待云端 macOS 编译（首次约 4~6 分钟，跑完才继续） |
| 4 | 下载产物并解包为 `outputs/X32Remote-unsigned.ipa` |

### 为什么不能直接在网页上点？

因为 `github.com` 网页在你的网络下 **22 秒超时打不开** —— 连「New repository」
这个按钮都点不了。所以建仓、推送、下载全部改走 **REST API + git 协议**，
这两条通道实测都是通的。

> **Token 权限**：经典 Token 勾 `public_repo`（公开仓库）或 `repo`（私有仓库必须），**外加 `workflow`**（见上一节，最容易漏）。

---

### 分步版本（想单独控制某一步时用）

```bash
python push_to_github.py --repo X32Remote --public   # 只建仓 + 推送
python ios_build.py --skip-push                      # 只等构建 + 下载
python ios_build.py --status                         # 只看状态，不下载
```

`push_to_github.py` 推送时把 Token 临时拼进 remote URL，推完**立即改回不带 Token 的地址**，
不会在本地 git 配置里留下凭据。

---

## 四、第二步 · 触发云端编译

1. 如果推送后没有自动触发：进入仓库 → **Actions** 标签页 → 左侧选 **Build Unsigned iOS IPA** → 右侧 **Run workflow** → 绿色按钮
2. 等待约 **4–6 分钟**（第一次会慢些，需要下载编译缓存）
3. 绿色勾 ✅ 表示成功

**构建做了什么**：

| 步骤 | 内容 |
|---|---|
| Select Xcode | 选 runner 上最新的 Xcode 15.x |
| Run core unit tests | 跑核心库单测（**非阻塞**，失败也不影响出包） |
| Build app (unsigned) | `CODE_SIGNING_ALLOWED=NO` 编译出 `.app` |
| Inspect app bundle | 打印架构、Bundle ID、本地网络权限说明，便于排查 |
| Package into unsigned IPA | 按 `Payload/App.app` 结构压成 `.ipa` |
| Upload IPA artifact | 上传为可下载产物，保留 30 天 |

---

## 五、第三步 · 下载 IPA 到本机（已包含在一条命令里）

用了第三节的 `ios_build.py`，这一步就已经自动完成了。IPA 会落在：

```
E:\AI\workbuddy\x32遥控\outputs\X32Remote-unsigned.ipa
```

需要单独重下时：

```bash
cd "E:/AI/workbuddy/x32遥控/X32Remote-ios/tools"
set GITHUB_TOKEN=ghp_你的Token
python fetch_ipa.py --repo 你的GitHub用户名/X32Remote --wait
```

全程走 `api.github.com`，不碰打不开的网页。构建失败会明确报「构建未成功」并给出日志地址。

---

## 六、第四步 · Sideloadly 签名安装

### 1. 安装与连接

1. 装 **iTunes**（微软商店搜 iTunes 即可，装它是为了让 Windows 认设备）
2. 装 **Sideloadly**：官网 sideloadly.io 下载 Windows 版
3. iPhone 用**数据线**连电脑，手机上点「信任此电脑」

### 2. 签名

1. 打开 Sideloadly
2. **IPA 栏**：把 `outputs\X32Remote-unsigned.ipa` 拖进去
3. **Apple ID 栏**：填你的 Apple ID
   - ⚠️ 如果账号开了**双重认证**，普通密码会失败，要去 [appleid.apple.com](https://appleid.apple.com)
     生成一个 **App 专用密码**（App-Specific Password），填那串密码
4. **Bundle ID**：留默认或改成独一无二的，如 `com.你的名字.x32remote`
   （免费账号的 App ID 有数量限制，用独一无二的名字可避免撞车）
5. 点 **Start**，输入 Apple ID 密码
6. 看着进度条走完，显示 **Done** 即成功（手机上图标会自动出现）

### 3. iPhone 上信任证书

装完第一次点图标会提示「不受信任的开发者」，需要：

**设置 → 通用 → VPN 与设备管理 → 开发者 App → 点你的 Apple ID → 信任**

然后就能正常打开了。

---

## 七、第五步 · 7 天到期怎么办

免费 Apple ID 签出来的证书**有效期只有 7 天**，到期后 App 打不开（图标还在，点了闪退）。

### 方案 A · 手动重签（简单直接）

7 天到了，重新执行一次第六步（Sideloadly 重签即可，不用重新编译）。
**IPA 文件本身不用重下**，本地那份一直有效。

### 方案 B · AltStore 自动重签（一劳永逸）

装 **AltStore** + 电脑端 **AltServer**，让 AltServer 常驻在 Windows 上（同一 Wi-Fi），
它会在后台每隔几天自动帮你续签，基本不用管。

代价是电脑要常开着。**如果你只是偶尔用用，方案 A 更省事。**

### 免费账号的三个硬限制

| 限制 | 数值 |
|---|---|
| 证书有效期 | 7 天 |
| 同时安装的侧载 App 数 | 3 个 |
| 每 7 天可注册的 App ID 数 | 10 个 |

---

## 八、常见问题

**Q：推送报 `refusing to allow a Personal Access Token to create or update workflow ... without 'workflow' scope`？**
A：Token 少勾了 **`workflow`**。仓库里有 `.github/workflows/build-ios.yml`，GitHub 会校验这个权限。
去 https://github.com/settings/tokens 编辑该 Token 补勾即可，**值不变、不用重新生成**。
（`push_to_github.py` 现在会在推送前就自动检查出来，见第二节的权限表。）

**Q：建仓报 HTTP 403 / `Resource not accessible by personal access token`？**
A：Token 少了建仓权限。公开仓库需 `public_repo`（或 `repo`），私有仓库必须 `repo`。
若用细粒度 Token，还需 `Administration: Read and write`，且资源所有者要选对（本人账号）。

**Q：构建失败，怎么排查？**
A：`fetch_ipa.py` 会把日志地址打出来。最可能的三个原因：
1. **scheme 找不到** —— 工程里已补了共享 scheme（`X32Remote.xcodeproj/xcshareddata/xcschemes/X32Remote.xcscheme`），确认它被一起 push 上去了
2. **Swift 版本/API 不兼容** —— runner 的 Xcode 比预期新
3. **Info.plist 生成冲突** —— 工程用的是 `GENERATE_INFOPLIST_FILE = YES`

**Q：Sideloadly 提示 "Provision.cpp:150" 之类的错误？**
A：多半是 Apple ID 有双重认证但填了普通密码，改用 **App 专用密码**。

**Q：提示 "Maximum number of apps for free development profiles has been reached"？**
A：免费账号最多同时装 3 个侧载 App。卸载一个不用的，或等 7 天周期刷新。

**Q：App 装上了但连不上调音台？**
A：iOS 14+ 首次访问局域网会弹窗要权限，**一定要点「允许」**。
若误点了拒绝：设置 → 隐私与安全性 → 本地网络 → 打开 X32 Remote 的开关。

**Q：为什么 Android 版不用这么麻烦？**
A：Android 允许安装任意来源的 APK，没有强制签名链。这是平台的本质差异，
不是工程复杂度问题 —— 两端的**业务代码是等价的**。

---

## 九、文件清单

| 文件 | 作用 |
|---|---|
| `X32Remote-ios/.github/workflows/build-ios.yml` | 云端 macOS 编译流水线 |
| `X32Remote-ios/X32Remote.xcodeproj/xcshareddata/xcschemes/X32Remote.xcscheme` | 共享 scheme，供 xcodebuild 使用（原工程缺失，已补） |
| `X32Remote-ios/tools/ios_build.py` | **主入口**：一条命令串起「建仓 → 推送 → 等编译 → 下载」 |
| `X32Remote-ios/tools/push_to_github.py` | 走 API 建仓并推送（单独使用） |
| `X32Remote-ios/tools/fetch_ipa.py` | 走 API 下载构建产物（单独使用） |
| `X32Remote-ios/tools/gh_logs.py` | 走 API 拉 Actions 的步骤结论与失败日志（网页打不开也能排查） |
| `X32Remote-ios/tools/verify_xcodeproj.py` | 无 Mac 也能校验工程：引用闭合、SPM 包引用、产品名、Frameworks 链接、文件是否在磁盘上 |
| `X32Remote-ios/tools/normalize_pbxproj_ids.py` | 把手写 pbxproj 的 ID 规范成 24 位十六进制（已执行过，保留备用） |
| `X32Remote-ios/.gitattributes` | 强制 iOS 工程文件使用 LF 行尾，避免 diff 噪音 |
| `outputs/X32Remote-unsigned.ipa` | **云端编译产物**，喂给 Sideloadly |
| `outputs/X32Remote-1.0.0.apk` | Android 版安装包（对比参考） |

---

## 十、这套方案的能力边界

**能做**：把完整功能的原生 App 装到你自己的 iPhone 上，随时遥控调音台。

**不能做**：
- 分发给别人的 iPhone（免费证书绑你的账号，只有 7 天）
- App Store 上架（需要 688 元/年开发者账号）
- 后台常驻做服务端（iOS 会在 30 秒内挂起后台网络）

最后一点值得强调：**iPhone 不能当服务端**。它只能当遥控器。
如果你想「手机直接控制真台」，Android 版是更好的载体 —— 它没有这些限制。
