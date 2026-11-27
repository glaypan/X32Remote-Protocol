# WING 控台标定方案 v1

> 目标：把 `WingProfile` 从空壳（`ready=False`，只能看图鉴）做到可读写——选算法、推子、静音、参数、回读。
> 状态：**方案，未改代码**。所有地址事实均来自官方文档，标注 `待核实` 的需要一次真机命令确认。

---

## 0. 结论

- WING 的 OSC 协议**有官方公开文档**，不是黑盒：`WING Remote Protocols V3.1`，作者 Patrick-Gilles Maillot，Behringer 授权维护。UDP **2223**。
- FX **算法选择是一条地址**：`/fx/N/mdl`，值就是官方 `mdl list` 里的 61 个枚举名（`HALL` `CHORUS` `ST-DL` `*BUS*` …）。放开选算法不需要真机。
- FX **参数不是序号而是命名**：`/fx/1/time`、`/fx/2/pdel`、`/fx/5/dcy`。官方文档「Effects and Plugins」章逐算法列出了参数名/类型/范围/枚举。
- 真机只承担**最后校验**（把 `verified` 置 True），且一条 `dump` 命令即可，不需要抓包。
- 真正的成本不在"找不到地址"，而在**5 处结构差异**（见 §3），改错任何一处都是"写成功但声音错"。

---

## 1. 事实基座

### 1.1 来源

| 来源 | 用途 | 可靠性 |
|---|---|---|
| `WING Remote Protocols V3.1`（digital-mixing.com / cdn.mediavalet.com） | 地址、类型、枚举、效果参数表 | 权威 |
| `wing-docs.com/pdf/OSC_Documentation.pdf` | HTML 化的同版正文，便于逐节取用 | 权威 |
| 社区实测（behringer.world t=1830、arlomedia 论坛） | 交叉验证 `/fx/1/time 500`、`/fx/2/pdel 26`、`/fx/2/dcy 1.7` | 佐证 |
| 本文档 `待核实` 项 | 需真机确认 | 未验 |

### 1.2 核心地址表

| 语义 | 地址 | 线格式 | 备注 |
|---|---|---|---|
| 通道推子 | `/ch/N/fdr` | float **dB**，-144 = -∞，上限 +10 | 也接受字符串 `"-2"` |
| 通道静音 | `/ch/N/mute` | int，**1 = 静音** | 发 `-1` 可 toggle |
| 通道声像 | `/ch/N/pan` | float，-100 = 全左，0 = 中，+100 = 全右 | |
| 通道名 | `/ch/N/name` | string | 长度上限 待核实 |
| 输出推子/静音 | `/aux/N/…` `/bus/N/…` `/mtx/N/…` `/main/N/…` `/dca/N/…` | 同上 | 编号**一律不补零** |
| 静音组 | `/mgrp/N/mute` | int，1 = 组生效（静音） | N 1–8 |
| FX 算法 | `/fx/N/mdl` | string 枚举，写小写**读回大写** | N 1–16 |
| FX 干湿 | `/fx/N/fxmix` | int 0–100 % | X32 是 0..1 |
| FX 参数 | `/fx/N/<参数名>` | 依参数表（float / int / string 枚举） | 名字随算法变 |

### 1.3 传输与会话

| 项 | 事实 |
|---|---|
| 端口 | UDP 2223，单包上限 32 KB，不支持 Bundle，地址不支持通配符 |
| 批量写 | `/ ,s "/ch.1.fdr=-1,mute=0,.2.fdr=0,mute=1"` → 回 `/*,s OK`；错误集：`NODE NOT FOUND` `VALUE ERROR` `BUFFER OVERFLOW` `NODE IS NOT PAR` `INCOMPLETE DATA` |
| 指定回包端口 | 任意命令加前缀 `/%<port>`，如 `/%10027/ch/1/mute` |
| 节点枚举 | `/fx ,s *` 列槽号；`/fx/1 ,s ?` 返回参数描述（含范围/枚举）；`,s #` 含当前值 |
| 订阅回读 | `/%<port>/*S`，**10 秒不续即死**，建议 9 s 续；**全台只允许 1 个订阅** |
| 握手 | `/?` → `WING,<ip>,PGM,ngc-full,NO_SERIAL,<固件版本>` |
| 客户端上限 | TCP 16 个；OSC(UDP) 不受限 |

---

## 2. 现状盘点

| 位置 | 现状 | 结论 |
|---|---|---|
| `x32sim/profiles.py` `WingProfile` | 继承 `_StubProfile`，`addr()` 全返回 None | 骨架就位，只缺实现 |
| `profiles.py` `WingProfile.counts` | `ch:48, bus:16, dca:8, auxin:8, usb:4, fxret:8, mtx:8, main:1, fx:16` | **4 处错**：ch 应为 40、dca 应为 16、main 应为 4、无 `fxret` 组 |
| `params.py` `ParamSpec.osc` | 模板硬编码 `%02d`，如 `/ch/%02d/preamp/trim` | WING 要不补零，模板必须能按控台重写 |
| `params.py` `spec_addr()` | 只负责填充，不感知控台 | 需加 profile 入参（或返回模板由 profile 渲染） |
| `kinds.py` `fx_par_num()` | 仅解析 `fx_par_NN` 的序号 | WING 需要"序号 → 参数名"翻译层 |
| `bridge.py` `_bridge_forward()` | 走 `param_wire(sp, value)` 定线上值；`fx_mix`/`fx_par_NN` 直接发 `float(value)` | 这是值转换的唯一出口，WING 分支插在这里 |
| `bridge.py` `arg = str(value)[:12]` | `fx_type`/`fx_name` 截断 12 字符 | WING 名字可能更长，待核实后定 |
| `consts.py` `FX_COUNT` | 8 | 模拟器本地槽数；WING 有 16 槽（9–16 仅 Standard 库） |

---

## 3. 与 X32 的 5 处结构差异（**全部必须适配**）

| # | 差异 | 后果（若不处理） | 适配位置 |
|---|---|---|---|
| 1 | 编号**不补零**：`/ch/1/fdr` ≠ `/ch/01/fdr` | 244 项参数地址全废；真台返回 NODE NOT FOUND，日志干净、界面正常 | `ParamSpec.osc` 增加平台无关写法，profile 渲染 |
| 2 | 推子是 **dB 浮点**，不是 0..1 | 本地 0.75 发出去 = -0.75 dB，几乎 -∞，现场直接没声 | `param_wire/param_from_wire` 加 `profile().fader_scale` 分支，复用 `params.level_db` 四段伪对数 |
| 3 | 静音 **1 = muted**（X32 是 `/mix/on` 1 = 开） | 所有静音动作反向：按"静音"反而开 | 同上，bool 取反 |
| 4 | FX 参数是**命名**地址，不是 `par/NN` | 参数推子写到不存在地址，或串味到别的参数 | `kinds.fx_par_num` + `wingfx.py` 序号→名字表 |
| 5 | FX 干湿 0–100 而非 0..1；`mdl` 枚举名与 X32 库名不同（`STEREO CHORUS` → `CHORUS`） | 干湿差 100 倍；选算法写不进 | `bridge.py` fx_mix 分支；`fxlib` 增加 `wing_mdl` 字段 |

补充：WING **没有 `fxrtn` 组**。X32 的 8 条效果返回在 WING 里就是普通 ch/bus，所以卡片目标清单在 WING 下要隐藏 `fxret_*`。

---

## 4. 实施阶段

### P1 基础控制 + 算法选择（最小可用，不依赖真机）

| 文件 | 改动 |
|---|---|
| `x32sim/profiles.py` | `WingProfile(ConsoleProfile)` 实装：`ready=True` `verified=False`；`counts` 修正为 `ch:40, aux:8, bus:16, main:4, mtx:8, dca:16, fx:16`；实现 `addr()` 覆盖 `fdr/mute/name/pan/scene(待核实)/fx_type/fx_name/fx_mix`；新增 `fader_db=True` `mute_inverted=True` `zero_pad=False` `fx_slots=16` `fx_slot_bank_rule` |
| `x32sim/params.py` | `spec_addr(sp, idx, prof)`；模板 `%02d` 改由 `prof.fmt_index(n)` 决定（X32 → `%02d`，WING → `%d`） |
| `x32sim/params.py` | `param_wire/param_from_wire` 增加 `fader_db` 分支（0..1 ↔ dB，复用 `level_db`）、`mute_inverted` 分支 |
| `x32sim/bridge.py` | `fx_mix` 走 `×100` 取整；`fx_type` 走 `en → wing_mdl` 映射（不是 `[:12]` 截断）；日志里区分"地址不存在"与"值非法" |
| `x32sim/fxlib.py` | 每条算法补 `wing_mdl`（`STEREO CHORUS`→`CHORUS`、`STEREO DELAY`→`ST-DL`、`TAP DELAY`→`TAP-DL`、`TAPE DELAY`→`TAPE-DL`、`BBD DELAY`→`BBD-DL`、`PITCH SHIFTER`→`PITCH`、`DUAL PITCH`→`D-PITCH`、`BUS COMPRESSOR`→`*BUS*`、`MASTER EQ`→`*MASTER*`；同名直接沿用）；61 项与官方 `mdl list` 逐条对齐校验 |
| `x32sim/fx.py` | `/api/state.fx` 增 `wingMdl`，前端按控台取显示名 |
| `test_wing_profile.py`（新增） | 地址串逐条断言（不补零/dB/mute 取反/枚举映射全覆盖/16 槽 Bank 规则） |

验收：
- 切到 WING 后 `console_listing()` 里 `ready=true`、`fxLocked=false`
- 卡片选算法 → 抓 `_bridge_forward` 实际发出 `mdl=<枚举>`，且枚举全部落在官方 61 项内
- 推子 0.75 → 发 `0.0`（0 dB）；推子 0 → 发 `-144`
- 静音 1 → 发 `1`；取消 → `0`
- 未支持的 kind（`fxret_*`、`usb_*`）显式记日志跳过，不静默

### P2 FX 命名参数（需整理官方表）

| 文件 | 改动 |
|---|---|
| `x32sim/wingfx.py`（新增） | `WING_FX_PARAMS = {算法: [ {n:序号, name:WING 参数名, type, lo, hi, step, list, unit} ]}`。序号是内部 ABI（`fx_par_NN` 不变，存档兼容），名字才是线格式 |
| `x32sim/kinds.py` | `fx_par_num` 保留；新增 `fx_par_name(kind, algo)` — 由 profile 提供 |
| `x32sim/profiles.py` | `WingProfile.addr("fx_par_07", slot)` → 查 wingfx 得名字 → `/fx/7/<name>`；查不到返回 None（拒绝写入并记日志） |
| `tools/export_param_specs.py` | 导出物增 `wing: {algo: params}`，供三端渲染参数名 |
| `test_wing_fx.py`（新增） | 61 算法参数表非空；序号连续无洞；参数名无重复；与官方枚举一致 |

设计要点：
- **不要**改内部 kind 为名字。`fx_par_NN` 是存档/卡片/预设里的既有键，改名会炸全部旧配置（与 `fxret_fader` 同理）。
- WING 参数顺序与 X32 的 `par/NN` 顺序**不保证一致**。表必须"每个算法独立有序"，不能全局套用。
- 值域也要独立存（`dcy` 是秒、`pdel` 是 ms、`fxmix` 是 %），不能沿用 X32 的 `tmin/tmax`。

### P3 订阅回读

| 文件 | 改动 |
|---|---|
| `x32sim/bridge.py` | 新增 `_wing_sub_loop`：每 9 s 发 `/%<port>/*S`，解析回包 → `set_value(..., source="console")`；与现有 `FWD_RECENT` 回声抑制对齐 |
| `x32sim/profiles.py` | `WingProfile.subscribe_spec = ("/%{port}/*S", 9.0, 1)` |
| 冲突处理 | **全台只允许 1 个订阅**。启动前发 `/?` 无法探测占用 → 由 UI 明示"若同时使用 Co-Pilot / Mixing Station 会互相抢订阅"，并在日志记录订阅失败 |

### P4（可选）节点批量写

WING 支持一条 `/ ` 命令写多个参数（`"/ch.1.fdr=-1,mute=0"`）。当前 `BRIDGE_FWD` 合并器把同刻多路写入合成 Bundle——WING **不支持 Bundle**，所以 WING 下应改走节点批量写，减少包数。优先级低（先保证正确性）。

---

## 5. 数据链

```
官方参数表 / wing_probe.py 实测
        ↓
x32sim/wingfx.py  （唯一事实来源，WING 侧）
        ↓  tools/export_param_specs.py
outputs/param_specs.json + iOS ParamSpecs+Generated.swift + Android assets/param_specs.json
        ↓
网页 / iOS / Android 渲染（算法名用 en，参数名按控台取）
```

`wing_mdl` 与 `en` 同源（`fxlib.py`），避免三端各译一遍。

---

## 6. `tools/wing_probe.py` 规格

一条命令完成真机校验与参数表导出：

```
python tools/wing_probe.py --host 192.168.1.71 [--port 2223] [--out outputs/wing_effects.json]
```

序列：
1. `/?` → 校验型号/固件版本，落 `identity`
2. `/%<本地端口>/fx ,s *` → 列 16 槽
3. 逐槽 `/fx/N/mdl` → 当前算法
4. 对每种算法：`/fx/N/mdl ,s <枚举>` 装载 → `/fx/N ,s ?` dump 参数描述 → 记 `{name, type, lo, hi, step, list}` → 恢复原算法
5. 只读探测 `/$stat` `/$cfg` 关键节点（场景、静音组）
6. 输出 JSON + 与 `wingfx.py` 逐项 diff，列出"文档有实测无""实测有文档无"

不写任何非 `mdl` 参数（除非 `--write-test` 显式指定），避免动到现场混音。

---

## 7. 验收清单

| # | 项 | 判定 |
|---|---|---|
| 1 | 地址不补零 | 抓包见 `/ch/1/fdr`，无 `/ch/01/` |
| 2 | dB 换算 | 0.75→`0.0`；0.5→`-10.0`；0→`-144`；回读反解误差 ≤ 0.5 dB |
| 3 | 静音取反 | 本地静音 1 → 发 `1`；回读 `1` → 本地 1 |
| 4 | 算法枚举 | 61 项全部能在 `mdl` 写入后读回（大写比对） |
| 5 | 槽位规则 | 9–16 槽不出现 Premium 算法（UI 过滤） |
| 6 | 干湿 | 本地 0.5 → 发 `50` |
| 7 | 未支持目标 | 记一条明确日志，不写任何地址 |
| 8 | 无 NaN | 非有限值拒发，日志留痕 |
| 9 | 订阅 | 9 s 续；断连 10 s 后自动重订阅；被占用时日志明确 |
| 10 | 回归 | `run_all_tests.py` 全绿；新增 `test_wing_*` 全绿 |

---

## 8. 风险与限制

1. **单订阅**：WING 全局只允许一个订阅。与 Co-Pilot / Mixing Station 同屏使用时必有一方失效，且无法提前探测。
2. **枚举大小写**：写小写、读回大写。比对一律 `.upper()`，但下发要用官方表里的原样。
3. **16 槽 / 8 槽**：模拟器 `FX_COUNT=8`，WING 16 槽且 9–16 只能装 Standard 库。UI 若照搬 8 槽不会错，但会浪费一半；放开 16 槽需要 `FX_COUNT` 变按控台取值（影响存档与卡片）。
4. **无 `fxrtn`**：X32 的 8 条效果返回不存在，卡片里引用 `fxret_*` 的动作在 WING 下要显式失效。
5. **无 Bundle**：X32 侧用 Bundle 合并转发，WING 不支持 → WING 分支必须逐条发或改用节点批量写。
6. **参数顺序不通用**：X32 的 `par/NN` 语义不能映射到 WING，逐算法独立建表。
7. **固件差异**：`mdl list` 随固件增长（V3.0.5 → V3.0.6 名称就有出入）。表要带固件下限，未知枚举拒写而不是猜。
8. **未验证状态**：P1 上线后 `verified=False` 必须在界面上可见（与现有"待标定"文案一致），不能因为"能写进去"就当成已标定。

---

## 9. 待核实（一次真机可全解）

| 假设 | 验证命令 |
|---|---|
| 场景召回地址（X32 是 `/-show/scene/N/name`） | `/show ,s *`、`/$stat ,s ?` |
| 通道→Bus 发送地址与量纲 | `/ch/1 ,s ?`，查 send 子树 |
| 名字长度上限 | `/ch/1/name ,s <超长串>` 观察截断 |
| 订阅确切语法与频率 | `/%23456/*S` 后观察回包端口与内容 |
| DCA 成员写法和 X32 `/ch/N/mix/dca` 的等价物 | `/dca/1 ,s ?` |
| `fx_type` 是否也接受 `EXT` 等非算法项 | `/fx/1/mdl ,s EXT` |

---

## 10. 版本影响

- 本次只出方案，不动代码，不影响 v1.9.0 已发布产物。
- P1 落地时：模拟器版本 +1（次版本），`共享协议规范.md` 增 "WING 控台地址表" 一节并升 spec 版本；三端若只改显示名则跟随补丁号。
- `verified` 未通过前，Release Notes 必须写明"WING 支持为基础控制，未经真机校验"。
