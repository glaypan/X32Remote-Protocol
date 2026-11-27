# X32 Remote v1.9.0 发布说明

服务端规范 **v1.13** · **效果器名一律用真台写法**：界面上写的就是台子上的字
（`Stereo Chorus`、`Dual Graphic EQ`、`Dual Tube Stage`），中文降为括号/副行
适配调音台：Behringer X32 / Midas M32（OSC over UDP，端口 10023）

## 这次改了什么

上一版（v1.8.0）把 61 个算法的真机面板图放进了软件，但**名字还是中文译名**。
你在台子或 X32-Edit 上看到的是 `Stereo Chorus`，App 里写的是「立体声合唱」——
现场急着找一个效果器时，这一层翻译就是成本，还容易翻错
（`HALL` 可以叫大厅/厅堂/礼堂，谁都对不上谁）。

**现在：英文真台名是主名，中文只作辅助。**

| 位置 | 改前 | 改后 |
|---|---|---|
| 网页算法下拉 | 立体声合唱 | `Stereo Chorus（立体声合唱）` |
| iOS 效果器列表 | 立体声合唱 · 11 参数 | `Stereo Chorus · 立体声合唱 · 11 参数` |
| Android 机架行 | 双通道图示 EQ | `Dual Graphic EQ` / 副行 `双通道图示 EQ · 均衡 · 64 参数` |
| 算法选择器 | 分类 · 中文名 (CODE) | `Stereo Chorus (CRS) · 调制` |
| WING 图鉴 | AMBIENCE | `AMBIENCE`(WING 原名，本来就没有中文) |

M32 与 X32 是同一 Klark Teknik 引擎，名字逐条相同。
WING 是另一套效果库，用 WING 自己的名字（`BBD DELAY`、`BLUE PLATE`、`DOUBLE VOCAL`），
不拿 X32 的名字套。

## 效果器页：从"混响页"改成覆盖全部 9 类

原来这一页叫「🎛 混响控制」，标题、文案、Tap 定拍全在讲混响，
看起来像"只支持混响"。实际上 61 种算法一直都在，只是**没被讲清楚**。

现在页面叫「🎛 效果器 FX 1-8」，导航栏「混响」→「效果」，说明里点明：

| 分类 | 数量 | 代表算法（真台名） |
|---|---|---|
| 混响 | 10 | Hall Reverb / Plate Reverb / Gated Reverb / Reverse Reverb |
| 延迟 | 3 | Stereo Delay / 3-Tap Delay / Rhythm Delay |
| 组合效果 | 6 | Delay + Chorus / Chorus + Chamber / Modulation Delay |
| 调制 | 7 | Stereo Chorus / Stereo Flanger / Stereo Phaser / Rotary Speaker |
| 变调 | 3 | Suboctaver / Stereo Pitch Shifter / Dual Pitch Shifter |
| 均衡 | 8 | Dual Graphic EQ / Stereo TruEQ / Stereo Xtec EQ1 · EQ5 |
| 动态 | 13 | Combinator / Fair·Leisure·Ultimo Compressor / Precision Limiter / DeEsser |
| 增强/成像 | 7 | Stereo Enhancer / Exciter / Sound Maxer / Stereo Imager / Edison EX1 |
| 吉他 | 4 | Stereo/Dual Guitar Amp / Stereo/Dual Tube Stage |

**Tap 定拍与同步组只对带时间参数的算法有效**（混响的衰减、延迟的拍值）；
EQ / 动态 / 增强类没有时间参数，卡片副标签会显示"N 个参数"而不是量程 ——
这不是毛病，是它本来就没有。

⚠️ 「仅 FX1-4」的 27 种算法占 DSP 算力，装不进 FX 5-8（真台同样限制），
下拉里选中前就标出来。

## 平台版本

| 平台 | 本版版本 | 形态 |
|---|---|---|
| 服务端 / 网页控制台 | 1.9.0 | `x32_simulator.py` 单文件（零依赖，Python 3.9+） |
| iOS | 1.7.0 (9) | 未签名 IPA，需自行签名（iOS 17.0+） |
| Android | 1.7.0 (9) | 已签名 APK（Android 8.0+） |
| macOS 桌面版 | 1.2.1 (9) | 内置 Python 与内核，未签名（右键打开） |
| Windows 桌面版 | 1.9.0 | 单 exe 便携版，免安装 |

## 下载哪个

| 你要的 | 下载这个包 |
|---|---|
| Windows 上双击就能用 | `X32Remote-1.9.0-windows-x64.zip` |
| Mac 上双击就能用（Intel / macOS 10.14.6+） | `X32Remote-macOS-1.2.1-unsigned.zip` |
| 安卓手机 | `X32Remote-1.9.0-android.zip`（内含 APK + 安装说明） |
| iPhone / iPad（需自签） | `X32Remote-1.9.0-ios.zip`（内含未签名 IPA + 真机直装指南） |
| 只想要服务端 + 网页控制台 | `X32Remote-1.9.0-server.zip` |
| 想改代码 | `X32Remote-1.9.0-source.zip`（四端完整源码） |

校验和见 `SHA256SUMS.txt`。

## 验证

- 回归 **14 组 / 全绿**，其中效果器图鉴组 **38 项**（本版新增 9 项锁名称：
  `en` 齐全、与登记表逐条一致、全局无重名、中文仍在、
  iOS/Android 标定表都带 `en`、网页确实用 `en` 拼显示名）
- Windows 便携版实跑：`/api/state` 里 61 个算法全部带真台名，
  `/fx/fxlib.json` 与 `/fx/wing/premium/ambience.webp` 均 200
- macOS 包内核版本 1.9.0、IPA 1.7.0 (9) 含 119 个图鉴条目、
  APK 内含 114 个图鉴资源

## 已知限制

- 服务端**故意无鉴权**（局域网工具，公开即用）
- 桌面版未签名：macOS 需右键→打开，Windows 可能触发 SmartScreen
- 在线版 `x32-mixer-console.app.workbuddy.host` 仍是旧版，未随本次重新部署
- WING 控台目前只能看**图鉴**，不能选算法（未做 WING 的参数地址标定，
  强行放开等于往真台写未知地址）
