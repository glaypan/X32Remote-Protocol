# -*- coding: utf-8 -*-
"""x32sim/params — 参数登记表 (ParamSpec): 语义 kind ↔ 台面字段 ↔ OSC 地址 ↔ 取值范围

为什么要有这张表
----------------
在此之前，"某个参数"的五个事实散落在五处：
  · 它在 STATE.ch[i] 里叫什么字段      → state.MixerState
  · 它的 OSC 地址长什么样              → consts.FADER_ADDR / profiles.addr
  · 超出范围该怎么办                  → state.set_value 里一个个 _clamp01/_truthy
  · 界面/卡片/预设能不能用它           → consts.SET_PARAMS / PRESET_GROUP_PARAMS + 前端硬编码
  · 它的量程与出厂值是多少             → 没人知道, 只在注释里
于是每加一个参数就要改五个地方、漏一处就是"接口返回 ok 但台子没反应"。
本表把上面五件事合成一行；新增参数 = 加一行（+ 三处白名单各加一个名字）。

边界（很重要）
--------------
本表只收**按路存储的标量参数**（推子/静音/通道的 38 个参数 + 发送的 3 个）。
以下三类**故意不入表**，继续走各自的显式分支：
  · 名称  —— 存在 STATE.names（(group, idx) 键），不在行字典里，且带持久化/日志
  · DCA 归属 —— 不是存储值，而是 dca_members 的位掩码投影（set 时要改成员表）
  · FX 机架 —— 参数号随算法变化，整槽存/整槽回归才有意义（见 consts.REVERB_DEFAULTS）
把它们塞进这张表只会得到一堆永远为真的 if，不会得到任何复用。

发送是唯一的**二维**参数（通道 × Bus）。它入表、但不进 STATE.ch 行 ——
512 个 (通道,Bus) 目标单独放 STATE.send，键由 send_pack 打包; 也不进
/api/state（会把 4G 流量账借回来），由 /api/sends?ch=N 按需取。

地址与量程来源
--------------
全部通道参数逐条对齐 **Behringer X32 Protocol Manual**（官方 OSC 表，M32 同协议）
与《Unofficial X32/M32 OSC Remote Protocol》v4.06。三处文档之间有出入：
  · preamp/trim：官方 [-12,12] / 非官方 [-18,18] → 取 [-18,18]。
    依据是社区实测：「固件 1.15 起卡输入的数字 trim 从 ±12 改为 ±18」
    （soundforums X32 讨论），官方手册那一行是 2012 年的旧值。
  · dyn/thr：官方 [-80,0,0.5] / 非官方 [-60,0,0.5] → 取宽 [-80,0]，
    回读若被真台钳到 -60 说明该固件下限只有 -60（见 note）。
  · delay/time：linf [0.300, 500.000, 0.100] ms（下限不是 0！）
  · preamp/hpf：logf [20,400,101] Hz —— 101 个对数档位
  · EQ: f logf[20,20000,201] / g linf[-15,15,0.25] / q logf[10,0.3,72]（**反向**）
  · dyn/ratio 是**索引**枚举（线上 0..11），本地存比值 —— 见 param_wire/param_from_wire
  · insert/pos、dyn/pos enum{PRE,POST} → 线上传 0/1
  · 发送取点 /ch/NN/mix/NN/type enum int[0..5] {IN/LC,PreEQ,PostEQ,PreFader,PostFader,Sub}
凡"出厂默认值"在公开资料里找不到的，一律在 note 里标注「需真机确认」——
模拟器可以给一个合理值，但不能假装自己核对过。
"""

import math

from .util import _finite, _truthy


class ParamSpec:
    """一个可寻址参数的完整描述。新增参数 =  new 一个实例并登记。"""

    def __init__(self, kind, group, field, osc, vtype="float",
                 lo=0.0, hi=1.0, default=None, step=0.0, logsteps=0,
                 enum=None, label="", unit="", dec=2, labels=None,
                 push=False, preset=True, note="", nidx=1, dims=0,
                 enum_wire="value", logdesc=False, linsteps=0,
                 unverified=False):
        self.kind = kind                  # 语义管道里的名字, 如 "ch_trim"
        self.group = group                # 台面分组 (STATE 的属性名), 如 "ch"
        self.field = field                # 行字典里的键, 如 "trim"
        self.osc = osc                    # OSC 地址模板; main 系列没有 %02d
        self.vtype = vtype                # "float" | "int" | "bool"
        # nidx: idx 里打包了几个地址索引 (发送是 (通道, Bus) 二维 → 2)
        # dims: 二阶维度的基数 (X32 是 16 个 Bus), 只在 nidx=2 时有意义
        self.nidx = int(nidx)
        self.dims = int(dims)
        # enum_wire:
        #   "value" —— 线上传枚举**字面值** (hpslope 传 12/18/24)
        #   "index" —— 线上传枚举**下标** (dyn/ratio 传 0..11, 本地存 4.0=4:1)
        # 这两种在真台上都真实存在, 混为一谈就会"本地 4.0 发给真台 4"→ 变成 5:1。
        self.enum_wire = enum_wire
        self.lo = float(lo)
        self.hi = float(hi)
        if default is None:
            default = 0.0
        # bool/int 必须存**整数**：旧代码 on 的默认值是 1 不是 1.0，
        # /api/state 会因此从 1 变成 1.0（JSON 形状变化，前端虽不炸但不干净）
        self.default = int(default) if vtype in ("bool", "int") else float(default)
        self.step = float(step)           # 线性档位 (真台旋钮是"有级"的)
        self.logsteps = int(logsteps)     # 对数档位数 (手册里的 logf [lo,hi,N])
        self.logdesc = bool(logdesc)      # True = 该对数表从 hi 递减到 lo (EQ 的 Q)
        # linsteps: **线性等分档位数** (协议第三列的数字)。
        #   · level 类型 161 档 → 值域按 f=k/160 等分 (k=0..160), 不是"等 dB";
        #   · fader 类型 1024 档 → f=k/1023。
        # 与 step 的区别: step 是"步进量", linsteps 是"点数", 后者才能避免
        # 0.1 这类步长在浮点下的累积偏差 (161 档时 1/160 无法用十进制 step 表达)。
        self.linsteps = int(linsteps)
        # unverified: 出厂值/默认值是**推断值**, 公开资料里查不到, 尚未在真台核对。
        # 单独一个布尔而不是靠 note 里写"需真机确认"—— 注释不会进接口, 前端和
        # 核对工具都看不见; 布尔会进 /api/info, 让"假设"这件事在界面上可见。
        self.unverified = bool(unverified)
        self.enum = tuple(enum) if enum else None
        self.label = label or kind        # 界面/日志用名
        self.unit = unit                  # 显示单位
        self.dec = int(dec)               # 显示小数位
        self.labels = tuple(labels) if labels else None   # 开关型的两态文案
        self.push = bool(push)            # 进 150ms 周期推送名单 (见下方说明)
        self.preset = bool(preset)        # 允许被预设范围勾选
        self.note = note                  # 标定备注 / 待确认项


# ------------------------------------------------------------------ 登记表
#
# ⚠️ push 只对**推子/静音**开启：周期推送每 150ms 一轮，名单里每多一个参数就多
#    32 条 OSC。新增的 12 个通道参数一律**不进周期推送** —— 它们只在手动写入时
#    立即推一次（state._push_manual_now，真台面板行为），/api/state 也早就改了
#    增量指纹（106 号任务：15-21KB×4/s → 命中不变只回 30 字节）。谁再把通道参数
#    加进 PUSH_KINDS，就是把那笔流量治理的账全部还回去。

_FADER_OSC = {"ch": "/ch/%02d/mix/fader", "bus": "/bus/%02d/mix/fader",
              "dca": "/dca/%02d/fader",                     # DCA 没有 /mix/ 一级
              "auxin": "/auxin/%02d/mix/fader", "usb": "/usb/%02d/mix/fader",
              # ⚠️ FX 返回组真台拼写是 **fxrtn** (Maillot 协议原文:
              #    "/fxrtn/[01...08]/mix/fader", FX Return (/fxrtn) data 章)。
              #    fxret 是本模拟器早年的拼写变体: match_address 两个拼写都收
              #    (旧客户端兼容), 但对外回包/推送一律 fxrtn, 与真台逐字一致。
              #    内部组名/kind 保持 fxret (卡片配置存的是 kind, 改名炸存档)。
              "fxret": "/fxrtn/%02d/mix/fader", "mtx": "/mtx/%02d/mix/fader",
              "main": "/main/st/mix/fader"}                 # main 无 %02d！
_ON_OSC = {"ch": "/ch/%02d/mix/on", "bus": "/bus/%02d/mix/on",
           "dca": "/dca/%02d/on",
           "auxin": "/auxin/%02d/mix/on", "usb": "/usb/%02d/mix/on",
           "fxret": "/fxrtn/%02d/mix/on", "mtx": "/mtx/%02d/mix/on",
           "main": "/main/st/mix/on"}
# 顺序即 consts.FADER_ADDR/ON_ADDR 的推导顺序（与历史上的字面字典一致）
_LEGACY_GROUPS = ("ch", "bus", "dca", "auxin", "usb", "fxret", "mtx", "main")

_SPECS = []
for _g in _LEGACY_GROUPS:
    _SPECS.append(ParamSpec(_g + "_fader", _g, "fader", _FADER_OSC[_g],
                            vtype="float", lo=0.0, hi=1.0, default=0.75,
                            dec=3, push=True, label="推子", unit="%"))
    _SPECS.append(ParamSpec(_g + "_on", _g, "on", _ON_OSC[_g],
                            vtype="bool", default=1, push=True, label="静音"))

# ---- 新增: 输入通道 1-32 的参数 (官方 X32 Protocol Manual) ----------------
# 按信号流顺序排：增益 → 反相 → 延时 → 低切 → 插入 → 门

# 侧链源 (keysrc) 在协议里是 **int [0..64]**, 不是 enum:
#   {OFF, In01..32, Aux1..6, USB L, USB R, Fx1L..Fx4R, Bus01..16}
# 0 + 32 + 6 + 2 + 8 + 16 = 65 → 0..64。顺序即索引，官方表逐项一致。
# ⚠️ 正因为它是 int 而不是 enum，真台**只接受整数**（不接受字符串 "Bus05"），
#    所以这里只用 labels 做显示，不把 enum_wire 设成 label。
_KEYS = (("OFF",)
         + tuple("In%02d" % i for i in range(1, 33))
         + tuple("Aux%d" % i for i in range(1, 7))
         + ("USB L", "USB R")
         + tuple("Fx%d%s" % (i, s) for i in range(1, 5) for s in ("L", "R"))
         + tuple("Bus%02d" % i for i in range(1, 17)))
assert len(_KEYS) == 65, "keysrc 枚举长度应为 65 (0..64), 实际 %d" % len(_KEYS)

# 侧链滤波器类型: enum int[0..8]。前 4 档是固定斜率的高低通，后 5 档是
# 「Keysolo (Solo/Q)」模式下的 Q 值。名称按官方表原样保留。
_FILTER_TYPES = ("LC6", "LC12", "HC6", "HC12", "1.0", "2.0", "3.0", "5.0", "10.0")

# 插入点目标: enum int[0..22] {OFF, FX1L..FX8R, AUX1..6}
_INS_TARGETS = (("OFF",)
                + tuple("FX%d%s" % (i, s) for i in range(1, 9) for s in ("L", "R"))
                + tuple("AUX%d" % i for i in range(1, 7)))
assert len(_INS_TARGETS) == 23, "insert/sel 枚举长度应为 23 (0..22), 实际 %d" % len(_INS_TARGETS)

_CH_EXTRA = (
    ParamSpec("ch_trim", "ch", "trim", "/ch/%02d/preamp/trim",
              vtype="float", lo=-18.0, hi=18.0, default=0.0, step=0.25,
              label="增益", unit="dB", dec=2,   # 档位 0.25 dB → 必须留两位
              note="手册 [-12,12](仅数字源)/非官方 [-18,18], 取宽; 步长 0.25 dB"),
    ParamSpec("ch_invert", "ch", "invert", "/ch/%02d/preamp/invert",
              vtype="bool", default=0, label="反相",
              note="极性反转; 真台 48V/反相同在 preamp 页"),
    ParamSpec("ch_delay_on", "ch", "delay_on", "/ch/%02d/delay/on",
              vtype="bool", default=0, label="延时"),
    ParamSpec("ch_delay_ms", "ch", "delay_ms", "/ch/%02d/delay/time",
              vtype="float", lo=0.3, hi=500.0, default=0.3, step=0.1,
              label="延时时间", unit="ms", dec=1,
              note="手册 linf [0.300,500.000,0.100] ms —— 下限 0.3 不是 0"),
    ParamSpec("ch_hpf_on", "ch", "hpf_on", "/ch/%02d/preamp/hpon",
              vtype="bool", default=0, label="低切"),
    ParamSpec("ch_hpf_slope", "ch", "hpf_slope", "/ch/%02d/preamp/hpslope",
              vtype="int", lo=12, hi=24, default=12, enum=(12, 18, 24),
              label="低切斜率", unit="dB/oct", dec=0, unverified=True,
              note="手册 enum{12,18,24} 按字面值; 出厂默认需真机确认"),
    ParamSpec("ch_hpf_f", "ch", "hpf_f", "/ch/%02d/preamp/hpf",
              vtype="float", lo=20.0, hi=400.0, default=80.0, logsteps=101,
              label="低切频率", unit="Hz", dec=0, unverified=True,
              note="手册 logf[20,400,101] Hz; 出厂默认频率需真机确认, "
                   "暂取人声常用 80 Hz"),
    ParamSpec("ch_ins_on", "ch", "ins_on", "/ch/%02d/insert/on",
              vtype="bool", default=0, label="插入"),
    ParamSpec("ch_ins_pos", "ch", "ins_pos", "/ch/%02d/insert/pos",
              vtype="bool", default=0, label="插入位置", labels=("PRE", "POST"),
              unverified=True,
              note="enum{PRE,POST} → 0/1; 出厂位置需真机确认, 暂取 PRE"),
    ParamSpec("ch_ins_sel", "ch", "ins_sel", "/ch/%02d/insert/sel",
              vtype="int", lo=0, hi=22, default=0, dec=0, labels=_INS_TARGETS,
              label="插入目标", preset=True, unverified=True,
              note="enum int[0..22] {OFF, FX1L..FX8R, AUX1..6}; "
                   "这是**插入点接到哪里** (与 ins_pos 的 PRE/POST 是两个维度); "
                   "出厂值需真机确认, 暂取 OFF"),
    ParamSpec("ch_gate_on", "ch", "gate_on", "/ch/%02d/gate/on",
              vtype="bool", default=0, label="噪声门"),
    ParamSpec("ch_gate_thr", "ch", "gate_thr", "/ch/%02d/gate/thr",
              vtype="float", lo=-80.0, hi=0.0, default=-40.0, step=0.5,
              label="门限", unit="dB", dec=1, unverified=True,
              note="手册 linf[-80,0,0.5] dB; 出厂阈值需真机确认, 暂取 -40 dB"),
    ParamSpec("ch_gate_range", "ch", "gate_range", "/ch/%02d/gate/range",
              vtype="float", lo=3.0, hi=60.0, default=15.0, step=1.0,
              label="门范围", unit="dB", dec=0, unverified=True,
              note="手册 linf[3,60,1] dB(正值=衰减量); 出厂值需真机确认"),
)
_SPECS.extend(_CH_EXTRA)

# ---- 噪声门: 补齐剩下的 8 个旋钮 ----------------------------------------
# 官方表 (通道 gate 组):
#   mode    enum int[0..4] {EXP2, EXP3, EXP4, GATE, DUCK}
#   attack  linf[0, 120, 1] ms
#   hold    logf[0.02, 2000, 101] ms
#   release logf[5, 4000, 101] ms
#   keysrc  int[0..64]  (侧链源, 见 _KEYS)
#   filter/on | filter/type | filter/f   (侧链滤波器: 5 段地址)
# ⚠️ filter/* 是**五段地址** (/ch/01/gate/filter/on)，与 EQ 的
#    /ch/01/eq/1/g 同深度，因此单独建 CH_DEEP_ADDR 表，不能塞进 CH_EXTRA_ADDR
#    (那张表只认四段)。
_GATE = (
    ParamSpec("ch_gate_mode", "ch", "gate_mode", "/ch/%02d/gate/mode",
              vtype="int", lo=0, hi=4, default=3, dec=0,
              labels=("EXP2", "EXP3", "EXP4", "GATE", "DUCK"),
              label="门类型", unverified=True,
              note="enum int[0..4]; 索引即线上值; 出厂模式需真机确认, 暂取 GATE"),
    ParamSpec("ch_gate_attack", "ch", "gate_attack", "/ch/%02d/gate/attack",
              vtype="float", lo=0.0, hi=120.0, default=10.0, step=1.0,
              label="门启动", unit="ms", dec=0, unverified=True,
              note="linf[0,120,1] ms; 出厂值需真机确认, 暂取 10 ms"),
    ParamSpec("ch_gate_hold", "ch", "gate_hold", "/ch/%02d/gate/hold",
              vtype="float", lo=0.02, hi=2000.0, default=20.0, logsteps=101,
              label="门保持", unit="ms", dec=1, unverified=True,
              note="logf[0.02,2000,101] ms; 出厂值需真机确认, 暂取 20 ms"),
    ParamSpec("ch_gate_release", "ch", "gate_release", "/ch/%02d/gate/release",
              vtype="float", lo=5.0, hi=4000.0, default=200.0, logsteps=101,
              label="门释放", unit="ms", dec=0, unverified=True,
              note="logf[5,4000,101] ms; 出厂值需真机确认, 暂取 200 ms"),
    ParamSpec("ch_gate_keysrc", "ch", "gate_keysrc", "/ch/%02d/gate/keysrc",
              vtype="int", lo=0, hi=64, default=0, dec=0, labels=_KEYS,
              label="门侧链源", unverified=True,
              note="int[0..64] {OFF,In01..32,Aux1..6,USB L/R,Fx1L..Fx4R,Bus01..16}; "
                   "**不是 enum** —— 真台只吃整数, 所以 labels 仅用于显示; "
                   "出厂值需真机确认"),
    ParamSpec("ch_gate_filter_on", "ch", "gate_filter_on",
              "/ch/%02d/gate/filter/on",
              vtype="bool", default=0, label="门侧链滤波", unverified=True,
              note="出厂值需真机确认, 暂取关"),
    ParamSpec("ch_gate_filter_type", "ch", "gate_filter_type",
              "/ch/%02d/gate/filter/type",
              vtype="int", lo=0, hi=8, default=0, dec=0, labels=_FILTER_TYPES,
              label="门滤波类型", unverified=True,
              note="enum int[0..8] {LC6,LC12,HC6,HC12,1.0,2.0,3.0,5.0,10.0}; "
                   "后 5 档是 Keysolo(Solo/Q) 的 Q 值"),
    ParamSpec("ch_gate_filter_f", "ch", "gate_filter_f",
              "/ch/%02d/gate/filter/f",
              vtype="float", lo=20.0, hi=20000.0, default=1000.0, logsteps=201,
              label="门滤波频率", unit="Hz", dec=0, unverified=True,
              note="logf[20,20000,201] Hz; 出厂值需真机确认, 暂取 1 kHz"),
)
_SPECS.extend(_GATE)

# ---- 动态处理 (压缩/扩展) -------------------------------------------------
# 地址/量程逐条取自 Behringer X32 Protocol Manual 与《Unofficial X32/M32 OSC
# Remote Protocol》v4.06 —— 两者在 thr 上不一致 (官方 [-80,0] / 社区 [-60,0])，
# 按"取宽 + 注释"处理：本地允许到 -80，真台若只认到 -60 会在回读时被拉回。
#   ratio 线上是 **0..11 的索引**，不是比值本身 —— 这是本表里唯一的 index 型枚举。
#   hold 是 logf[0.02,2000,101]；keysrc 是 int[0..64]，与 gate 同表；
#   filter/type 是 enum int[0..8]，filter/f 是 logf[20,20000,201]。
# 排列顺序 = 协议表的顺序 (on→mode→det→env→thr→ratio→knee→mgain→attack→
# hold→release→pos→keysrc→mix→auto→filter)，前端控件表按同一顺序展开。
#
# ⚠️ 各组的参数**不是同一套** —— 这是真台的事实，不是我们偷懒：
#   ch  : 18 项 (含 keysrc)
#   bus : 18 项 (含 keysrc)
#   mtx : 17 项 (**协议表里 mtx 段没有 dyn/keysrc** —— 矩阵没有侧链源可选)
#   main: 15 项 (**没有 keysrc / mix / auto** —— 主输出动态是精简版)
#   若哪天真机回读证明某项其实存在，加一行即可 —— 但绝不能先"顺手补上"：
#   多一个控件就多一处"拖了没反应"，比少一个控件难查得多。
_DYN_FIELDS = (
    # (field 后缀, vtype, lo, hi, default, step, logsteps, enum, labels, label, unit, dec, 备注)
    ("on", "bool", 0, 1, 0, 0, 0, None, None, "压缩", "", 0, ""),
    ("mode", "int", 0, 1, 0, 0, 0, None, ("COMP", "EXP"), "动态类型", "", 0,
     "enum int[0..1] {COMP,EXP}"),
    ("det", "int", 0, 1, 0, 0, 0, None, ("PEAK", "RMS"), "检波方式", "", 0,
     "enum int[0..1] {PEAK,RMS}"),
    ("env", "int", 0, 1, 0, 0, 0, None, ("LIN", "LOG"), "包络", "", 0,
     "enum int[0..1] {LIN,LOG}"),
    ("thr", "float", -80.0, 0.0, -20.0, 0.5, 0, None, None, "压缩阈值", "dB", 1,
     "官方 [-80,0,0.5] / 社区 [-60,0,0.5] 不一致 → 取宽"),
    ("ratio", "int", 1.1, 100.0, 4.0, 0, 0,
     (1.1, 1.3, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, 10.0, 20.0, 100.0),
     None, "压缩比", ":1", 1, "线上枚举 0..11, 本地存比值"),
    ("knee", "float", 0.0, 5.0, 2.0, 1.0, 0, None, None, "拐点", "", 0, ""),
    ("mgain", "float", 0.0, 24.0, 0.0, 0.5, 0, None, None, "补偿增益", "dB", 1, ""),
    ("attack", "float", 0.0, 120.0, 10.0, 1.0, 0, None, None, "启动时间", "ms", 0, ""),
    ("hold", "float", 0.02, 2000.0, 20.0, 0, 101, None, None, "保持时间", "ms", 1,
     "logf[0.02,2000,101] ms"),
    ("release", "float", 5.0, 4000.0, 200.0, 0, 101, None, None, "释放时间", "ms", 0,
     "手册 logf[5,4000,101] ms"),
    ("pos", "bool", 0, 1, 1, 0, 0, None, ("PRE", "POST"), "压缩位置", "", 0,
     "enum{PRE,POST} → 0/1 (相对 EQ)"),
    ("keysrc", "int", 0, 64, 0, 0, 0, None, None, "压缩侧链源", "", 0,
     "int[0..64]; **不是 enum**, 真台只吃整数"),
    ("mix", "float", 0.0, 100.0, 100.0, 5.0, 0, None, None, "干湿比", "%", 0,
     "手册 linf[0,100,5] %, 100 = 全湿"),
    ("auto", "bool", 0, 1, 0, 0, 0, None, None, "自动增益", "", 0,
     "enum{OFF,ON}; 自动补偿增益"),
    ("filter_on", "bool", 0, 1, 0, 0, 0, None, None, "侧链滤波", "", 0, ""),
    ("filter_type", "int", 0, 8, 0, 0, 0, None, tuple(_FILTER_TYPES),
     "侧链滤波类型", "", 0, "enum int[0..8]"),
    ("filter_f", "float", 20.0, 20000.0, 1000.0, 0, 201, None, None,
     "侧链滤波频率", "Hz", 0, "logf[20,20000,201] Hz"),
)
_DYN_SKIP = {"mtx": ("keysrc",), "main": ("keysrc", "mix", "auto")}
# main 只有一路, 地址里没有路号 (/main/st/dyn/thr 而不是 /main/01/dyn/thr)
GADDR = {"main": "/main/st", "fxret": "/fxrtn"}


def gaddr(g, tail, idx=True):
    """组 + 子路径 → 地址模板。idx=False 表示没有路号 (main)。"""
    pre = GADDR.get(g, "/" + g)
    return "%s/%s" % (pre, tail) if not idx else "%s/%%02d/%s" % (pre, tail)


def _dyn_specs(g):
    """给一个通道组生成 dyn 参数 (地址 /<g>/NN/dyn/<项>)。"""
    out = []
    skip = _DYN_SKIP.get(g, ())
    for (f, vt, lo, hi, dv, st, lg, en, lb, lab, unit, dec, note) in _DYN_FIELDS:
        if f in skip:
            continue
        # filter_on/type/f 的真地址是 dyn/**filter**/on —— 多一层, 不能拼成 dyn/filter_on
        _suf = ("filter/" + f[7:]) if f.startswith("filter_") else f
        out.append(ParamSpec(
            "%s_dyn_%s" % (g, f), g, "dyn_" + f,
            gaddr(g, "dyn/" + _suf, idx=(g != "main")),
            vtype=vt, lo=lo, hi=hi, default=dv, step=st, logsteps=lg,
            enum=en, enum_wire=("index" if en is not None else "value"),
            labels=lb, label=lab, unit=unit, dec=dec,
            unverified=("出厂值需真机确认" in note) or vt != "bool" and f in
                       ("hold", "keysrc", "filter_type", "filter_f", "mode", "det", "env"),
            note=note))
    return out


_DYN = tuple(_dyn_specs("ch"))
_SPECS.extend(_DYN)
for _g in ("bus", "mtx", "main"):
    _SPECS.extend(_dyn_specs(_g))


# ---- 均衡: 4 段 / 6 段 ---------------------------------------------------
# 输入通道与 Aux In 是 **4 段**；Bus / Matrix / Main 是 **6 段**。这是真台的硬规格,
# 不是配置项 —— 前端按本表自动长控件, 所以"Bus 上多出来的两个频段"不需要另写界面。
# 频段号**写死在地址模板里** (每段一个独立 kind) —— 这样 idx 仍然只是通道号,
# 不需要在行字典里做二维打包, 预设/卡片/桥接全部沿用同一条路径。
#   f: logf[20,20000,201] Hz  |  g: linf[-15,15,0.25] dB  |  q: logf[10,0.3,72] (反向)
_EQ_TYPES = ("LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut")
# 各频段的"合理起点" (非手册值): 4 段按人声常用点, 6 段把低频再拆细一点。
_EQ_DEFAULT_F = {4: (100.0, 500.0, 2000.0, 8000.0),
                 6: (80.0, 200.0, 600.0, 2000.0, 6000.0, 12000.0)}
_EQ_BANDS = {"ch": 4, "bus": 6, "auxin": 4, "mtx": 6, "main": 6}


def _eq_specs(g):
    """一个通道组的整组 EQ 参数 (eq_on + 每段 4 项)。"""
    n = _EQ_BANDS[g]
    fs = _EQ_DEFAULT_F[n]
    out = [ParamSpec("%s_eq_on" % g, g, "eq_on",
                     gaddr(g, "eq/on", idx=(g != "main")),
                     vtype="bool", default=0, label="均衡")]
    for b in range(1, n + 1):
        out.append(ParamSpec(
            "%s_eq%d_type" % (g, b), g, "eq%d_type" % b,
            gaddr(g, "eq/%d/type" % b, idx=(g != "main")),
            vtype="int", lo=0.0, hi=5.0, default=2, dec=0, labels=_EQ_TYPES,
            label="EQ%d 类型" % b,
            note="enum int[0..5] {LCut,LShv,PEQ,VEQ,HShv,HCut}; 索引即线上值; "
                 "mtx 01/06 在真台上还能选 BU6..LR24 (0..13), 这里只给公共 6 档"))
        out.append(ParamSpec(
            "%s_eq%d_f" % (g, b), g, "eq%d_f" % b,
            gaddr(g, "eq/%d/f" % b, idx=(g != "main")),
            vtype="float", lo=20.0, hi=20000.0, default=fs[b - 1],
            logsteps=201, label="EQ%d 频率" % b, unit="Hz", dec=0,
            note="手册 logf[20,20000,201] Hz"))
        out.append(ParamSpec(
            "%s_eq%d_g" % (g, b), g, "eq%d_g" % b,
            gaddr(g, "eq/%d/g" % b, idx=(g != "main")),
            vtype="float", lo=-15.0, hi=15.0, default=0.0, step=0.25,
            label="EQ%d 增益" % b, unit="dB", dec=2,
            note="手册 linf[-15,15,0.25] dB"))
        out.append(ParamSpec(
            "%s_eq%d_q" % (g, b), g, "eq%d_q" % b,
            gaddr(g, "eq/%d/q" % b, idx=(g != "main")),
            vtype="float", lo=0.3, hi=10.0, default=2.0,
            logsteps=72, logdesc=True, label="EQ%d Q值" % b, dec=2,
            note="手册 logf[10,0.3,72] —— 表是**从 10 到 0.3 递减**的 (Q 越小频带越宽); "
                 "lo/hi 这里写自然上下界 0.3..10, 递减方向由 logdesc 表达"))
    return out


for _g in ("ch", "bus", "auxin", "mtx", "main"):
    _SPECS.extend(_eq_specs(_g))

# ---- 插入 (通道 → FX/AUX 插入点) -----------------------------------------
# ch 的插入在 _CH_EXTRA 里 (与增益/低切同批进表); Bus/Matrix/Main 的插入项
# 地址与量程**完全相同**, 只是组名不同 —— 所以这里只补后三组。
_INS_TARGETS_ = _INS_TARGETS


def _ins_specs(g):
    return [
        ParamSpec("%s_ins_on" % g, g, "ins_on",
                  gaddr(g, "insert/on", idx=(g != "main")),
                  vtype="bool", default=0, label="插入"),
        ParamSpec("%s_ins_pos" % g, g, "ins_pos",
                  gaddr(g, "insert/pos", idx=(g != "main")),
                  vtype="bool", default=0, label="插入位置", labels=("PRE", "POST"),
                  unverified=True,
                  note="enum{PRE,POST} → 0/1; 出厂位置需真机确认, 暂取 PRE"),
        ParamSpec("%s_ins_sel" % g, g, "ins_sel",
                  gaddr(g, "insert/sel", idx=(g != "main")),
                  vtype="int", lo=0, hi=22, default=0, dec=0, labels=_INS_TARGETS_,
                  label="插入目标", unverified=True,
                  note="enum int[0..22] {OFF, FX1L..FX8R, AUX1..6}; "
                       "出厂值需真机确认, 暂取 OFF"),
    ]


for _g in ("bus", "mtx", "main"):
    _SPECS.extend(_ins_specs(_g))

# ---- 声像 (pan) ---------------------------------------------------------
# 协议类型是 **linf [-100, 100, 2]** —— 不是 -1..1。写成 -1..1 的话, 界面拖到
# 最左发 -1, 真台收到 -1 (相当于中间偏左 1%), 用户会觉得"pan 坏了"。
# Matrix 没有 pan (矩阵输出是单声道), 所以不在本表里 —— 少一个死控件。
# ⚠️ 内部组名仍是 fxret (卡片/预设存的是 kind), 但**地址**必须是 fxrtn。
OSC_GROUP = {"fxret": "fxrtn"}
for _g in ("ch", "bus", "auxin", "fxret"):
    _SPECS.append(ParamSpec(
        "%s_pan" % _g, _g, "pan", "/%s/%%02d/mix/pan" % OSC_GROUP.get(_g, _g),
        vtype="float", lo=-100.0, hi=100.0, default=0.0, step=2.0,
        label="声像", dec=0,
        note="linf[-100,100,2]; 0=居中, -100=全左, +100=全右"))
# 主输出只有一路, 地址里没有路号 (/main/st/mix/pan)
_SPECS.append(ParamSpec(
    "main_pan", "main", "pan", "/main/st/mix/pan",
    vtype="float", lo=-100.0, hi=100.0, default=0.0, step=2.0,
    label="声像", dec=0, note="linf[-100,100,2]; 主输出的左右平衡"))

# ---- Aux In 的前置增益 ---------------------------------------------------
# Aux In 只有数字 trim, 没有话放 (输入是线路电平) —— 也没有门/动态/插入。
_SPECS.append(ParamSpec(
    "auxin_trim", "auxin", "trim", "/auxin/%02d/preamp/trim",
    vtype="float", lo=-18.0, hi=18.0, default=0.0, step=0.25,
    label="增益", unit="dB", dec=2,
    note="手册 [-12,12] / 非官方 [-18,18] → 取宽; 与 ch 同一套 trim 语义"))
_SPECS.append(ParamSpec(
    "auxin_invert", "auxin", "invert", "/auxin/%02d/preamp/invert",
    vtype="bool", default=0, label="反相"))

# ---- 话放模拟增益 (/headamp) --------------------------------------------
# ⚠️ 这才是**模拟增益**; /ch/NN/preamp/trim 是数字微调。两者是串联的:
#    声音 = 话放增益 → 数字 trim → 推子。
# 地址是 **三位补零** (/headamp/000/gain), 与通道的两位不同 —— 写错的症状是
# "界面在动、台子没反应"。索引默认按本地 XLR 直连映射 (headamp 000..031 = ch 01..32);
# 若现场用了 AES50 分体接口箱, 这个映射不成立, 需要在台面配置里改。
_SPECS.append(ParamSpec(
    "headamp_gain", "headamp", "gain", "/headamp/%03d/gain",
    vtype="float", lo=-12.0, hi=60.0, default=0.0, step=0.5,
    label="话放增益", unit="dB", dec=1, unverified=True,
    note="linf[-12,60,0.5] dB; 三位补零地址; 索引假定本地 XLR 直连 (000..031 → ch01..32), "
         "AES50 接口箱时映射不同"))

# ---- 发送 (通道 → Mix Bus) ---------------------------------------------
# ⚠️ 这是全表唯一的**二维**参数: 目标 = (通道, Bus)。128 个组合 × 3 个字段,
#    若当成 512 个通道字段塞进 STATE.ch, /api/state 会直接涨 25KB (那笔流量账
#    刚结清, 不能再借回来)。所以:
#      · 单独一张 STATE.send 表, 键 = send_pack(ch, bus);
#      · **不进 /api/state**, 由 /api/sends?ch=N 按需取 (见 httpapi)。
#    tap 就是"推子前后/取点"本身: enum int[0..5] {IN/LC, PreEQ, PostEQ,
#    PreFader, PostFader, Sub} —— HON wiki 与官方手册一致。
SEND_BUS = 16          # 兼容旧名: = 输入通道的发送路数 (16 个 Mix Bus)

# 各组的发送目标数 + 打包基址
#   ch    → Bus 1-16
#   auxin → Bus 1-16   (辅助输入也要能送监听)
#   fxret → Bus 1-16   (混响送回耳返是现场常规操作)
#   bus   → Matrix 1-6
#   main  → Matrix 1-6
# 基址分段的理由: 卡片/预设/HTTP 都把发送目标存成**一个整数 idx**, 改成元组
# 会炸掉所有存档。用 1000 起步的分段, 既保持 ch 的 1..512 原样, 又不会撞号。
SEND_GROUPS = (("ch", 32, 16, 0), ("auxin", 6, 16, 1000),
               ("fxret", 8, 16, 2000), ("bus", 16, 6, 3000), ("main", 1, 6, 4000))
SEND_BUSES = {g: n for g, _src, n, _base in SEND_GROUPS}
SEND_BASE = {g: base for g, _src, _n, base in SEND_GROUPS}
SEND_SRC_MAX = {g: src for g, src, _n, _base in SEND_GROUPS}


def send_group_of(idx):
    """打包键 → 组名。落在哪个基址段就是哪一组 (ch 段从 0 起, 1..512)。"""
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return "ch"
    best = "ch"
    for g in ("ch", "auxin", "fxret", "bus", "main"):
        if i > SEND_BASE[g]:
            best = g
    return best


def send_pack(ch, bus, group="ch"):
    """(源, 目标) → 一维键。按**源主序**打包, 便于按通道整段遍历。"""
    return SEND_BASE.get(group, 0) + (int(ch) - 1) * SEND_BUSES.get(group, SEND_BUS) \
        + int(bus)


def send_unpack(idx):
    """send_pack 的逆运算 → (组, 源号, 目标号)。越界编号由调用方判。"""
    g = send_group_of(idx)
    rel = int(idx) - SEND_BASE[g] - 1
    n = SEND_BUSES[g]
    return g, rel // n + 1, rel % n + 1


# 官方协议: /ch/../mix/01/type enum {<-EQ, EQ->, PRE, POST, GRP}
# (实测固件按字面值发 0..4；线上是枚举索引)
_SEND_TAP_LABELS = ("<-EQ", "EQ->", "PRE", "POST", "GRP")


def _send_specs(g, buses, has_tap):
    """一组的发送参数。kind 前缀: ch 保持历史的 send_*, 其余加组名前缀。"""
    k = "send" if g == "ch" else g + "_send"
    # 主输出只有一路, 地址里没有源号: /main/st/mix/01/level
    src_t = "/main/st/mix/%02d/" if g == "main" \
        else "/%s/%%02d/mix/%%02d/" % OSC_GROUP.get(g, g)
    out = [
        ParamSpec(k + "_level", "send", "level",
                  src_t + "level",
                  vtype="float", lo=0.0, hi=1.0, default=0.0, dec=3,
                  linsteps=161, label="发送电平", unit="%", nidx=2, dims=buses,
                  unverified=True,
                  note="协议类型 **level**: [0..1(+10dB), 161 档] —— 161 个点就是 "
                       "f=k/160 (k=0..160) 的等分, 不是等 dB; dB 换算见 level_db()。"
                       "出厂值需真机确认, 暂取 0(-∞)"),
        ParamSpec(k + "_on", "send", "on",
                  src_t + "on",
                  vtype="bool", default=0, label="发送开关", nidx=2, dims=buses,
                  unverified=True,
                  note="出厂值需真机确认, 暂取关"),
    ]
    if has_tap:
        out.append(ParamSpec(
            k + "_tap", "send", "tap", src_t + "type",
            vtype="int", lo=0.0, hi=4.0, default=3, dec=0,
            labels=_SEND_TAP_LABELS, label="发送取点",
            nidx=2, dims=buses, unverified=True,
            note="enum int[0..4] {<-EQ,EQ->,PRE,POST,GRP}; "
                 "**只对奇数 Bus 定义** (协议表), 真台按 1&2 / 3&4 成对共享取点 → "
                 "本地把偶数 Bus 统一落到奇数 Bus, 只留一份真相; "
                 "线上用整数索引 (真台也接受字符串, 见 --tap-wire); "
                 "出厂默认需真机确认, 暂取 POST"))
    return out


for _g, _src, _nb, _base in SEND_GROUPS:
    # Bus / Main 的发送只有 on + level —— 协议表里没有 /mix/NN/type。
    _SPECS.extend(_send_specs(_g, _nb, _g not in ("bus", "main")))

PARAM_SPECS = {sp.kind: sp for sp in _SPECS}

# 各组的分组规模 (地址通配展开、卡片目标上限、/api/state 循环共用)
GROUP_SIZE = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2,
              "fxret": 8, "mtx": 6, "main": 1, "headamp": 32}

# 某组的**行参数** (推子/静音之外的处理参数)。前端面板、预设白名单、卡片白名单
# 全部从这里取 —— 各组能力不同, 谁都不许自己维护一份副本。
GROUP_PARAM_SPECS = {}
for _sp in _SPECS:
    if _sp.push or _sp.group in ("send",):
        continue
    GROUP_PARAM_SPECS.setdefault(_sp.group, []).append(_sp)
GROUP_PARAM_SPECS = {g: tuple(v) for g, v in GROUP_PARAM_SPECS.items()}

# 新增的通道参数 (不含推子/静音) —— 历史名, 保留给现有调用方
CH_PARAM_SPECS = GROUP_PARAM_SPECS["ch"]
# 发送参数 (二维, 单独一张 STATE.send 表)
SEND_PARAM_SPECS = tuple(sp for sp in _SPECS if sp.group == "send")
# ⚠️ 键必须是 **URL 末段** 而不是字段名: 取点的地址是 /mix/%02d/**type**，
#    字段名却叫 **tap** —— 用 field 做键的话，OSC 读 /ch/01/mix/03/type 会
#    一条都匹配不上（静默返回空，不是报错，所以只能靠回归抓）。
#    每组一份: level/on 在五组里同名, 合成一张表会互相覆盖。
SEND_BY_SEG = {}
for _sp in SEND_PARAM_SPECS:
    _parts = _sp.osc.strip("/").split("/")
    _grp = _parts[0]
    SEND_BY_SEG.setdefault(_grp, {})[_parts[-1]] = _sp
assert all(len(v) == len([s for s in SEND_PARAM_SPECS
                          if s.osc.strip("/").split("/")[0] == g])
           for g, v in SEND_BY_SEG.items()), "发送参数地址末段撞名了"

# ---- 地址索引: 由登记表反推, match_address 因此不再有"每组一个分支" ----
# 三段地址 (/dca/01/fader、/headamp/000/gain)
ADDR3 = {}
# 四段地址 (/bus/01/dyn/thr、/bus/01/mix/pan、/ch/01/preamp/trim …)
ADDR4 = {}
# 五段带段号 (/bus/01/eq/3/g)：键里不放段号, 段号从 spec 的地址模板里取
ADDR5_BAND = {}
# 五段定值 (/bus/01/dyn/filter/f、/ch/01/gate/filter/on …)
ADDR5_DEEP = {}
for _sp in _SPECS:
    if _sp.group == "send":
        continue                      # 发送是二维目标, 走自己的分支
    _parts = _sp.osc.strip("/").split("/")
    if len(_parts) == 3:
        ADDR3.setdefault((_parts[0], _parts[2]), []).append(_sp)
    elif len(_parts) == 4:
        ADDR4.setdefault((_parts[0], _parts[2], _parts[3]), []).append(_sp)
    elif len(_parts) == 5:
        if _parts[3].isdigit():
            ADDR5_BAND.setdefault((_parts[0], _parts[2], _parts[4]), []).append(_sp)
        else:
            ADDR5_DEEP.setdefault(
                (_parts[0], _parts[2], _parts[3], _parts[4]), []).append(_sp)

# 历史表名保留: 有些回归按 CH_* 名字做断言 (内容现在覆盖全部组)
CH_EXTRA_ADDR = {(k[1], k[2]): v for k, v in ADDR4.items() if k[0] == "ch"}
CH_EQ_ADDR = {(k[1], k[2]): v for k, v in ADDR5_BAND.items() if k[0] == "ch"}
CH_DEEP_ADDR = {k: v for k, v in ADDR5_DEEP.items() if k[0] == "ch"}


# 控台实际能接受的范围 (kind → (lo, hi))。由 app 在选好控台后写入,
# 目前只有 trim: 手册 ±12 / 固件 1.15+ ±18。空表 = 全部用 spec 自带量程。
_ACTIVE_LIMITS = {}


def set_param_limits(limits):
    """设置活动量程覆盖。传 None/{} 恢复成 spec 默认。"""
    global _ACTIVE_LIMITS
    _ACTIVE_LIMITS = dict(limits or {})


def active_limits(kind):
    return _ACTIVE_LIMITS.get(kind)


def set_enum_wire(overrides):
    """运行时切换枚举的线上形式 (kind → "value"/"index"/"label")。

    存在的理由: 真台对**枚举字符串**的接受度是明确的 (合法 OSC), 但**回包**形式
    随固件不同, 而这一点在没有真机时无法确认。与其猜一个全局默认, 不如:
      · 默认按 spec 声明 (索引);
      · 用一个独立的启动开关 `--tap-wire label` 切换;
      · 两种形式都由回归覆盖 —— 万一真台回字符串, 改一个参数就能对上。
    """
    changed = []
    for kind, style in (overrides or {}).items():
        sp = PARAM_SPECS.get(kind)
        if sp is None or style not in ("value", "index", "label"):
            continue
        sp.enum_wire = style
        changed.append("%s=%s" % (kind, style))
    return changed


def unverified_kinds():
    """出厂值是推断值、尚未真机核对的参数清单 (给 /api/info 与核对工具用)。"""
    return [sp for sp in _SPECS if sp.unverified]


def spec_addr(sp, idx):
    """spec → 真实地址。

    三种模板:
      · 无占位符        → main 系列, 直接返回 (历史 bug: `%` 会抛 TypeError)
      · 一个 %02d       → 绝大多数参数, idx 就是路号
      · 两个 %02d       → 发送, idx 是 send_pack 打包后的 (通道, Bus)
    """
    if sp.nidx >= 2:
        _g, ch, bus = send_unpack(idx)
        # 取点在真台上按成对 Bus 共享 (1&2 / 3&4 …), 协议表只给奇数 Bus 地址。
        # 偶数 Bus 统一落到它的奇数搭档 —— 否则本地写 Bus02、真台改的却是 Bus01,
        # 界面与台面从此各说各话。
        if sp.kind == "send_tap" and bus % 2 == 0:
            bus -= 1
        # 主输出的发送地址没有源号 (/main/st/mix/NN/level) —— 只有一个占位符
        if sp.osc.count("%") == 1:
            return sp.osc % bus
        return sp.osc % (ch, bus)
    if sp.kind == "headamp_gain":
        # 话放地址是**三位**补零, 且 000 对应 ch01 (索引偏移 -1)
        return sp.osc % (int(idx) - 1)
    return sp.osc % idx if "%" in sp.osc else sp.osc


def _nearest_enum(sp, v):
    return min(sp.enum, key=lambda e: (abs(e - v), e))


# ---------------------------------------------------------------- level 曲线
# 协议里 `level` 类型 (推子 / 发送电平 / 主输出…) 不是等 dB, 而是 **4 段伪对数**:
#   f >= 0.5        → dB = 40f  - 30
#   0.25 <= f < 0.5 → dB = 80f  - 50
#   0.0625<= f <0.25→ dB = 160f - 70
#   f < 0.0625      → dB = 480f - 90
# 锚点: 0.75→0 dB, 0.5→-10, 0.25→-30, 0.0625→-60, 1→+10, 0→-∞。
# ⚠️ 本函数是**唯一一份**曲线定义: 后端显示、DCA/相对 dB 动作、前端刻度都引用它。
#    曾经这里写的是 20*log10(f/0.75) —— 只有 0.75 一个点对得上, 0.25 处偏 +20 dB,
#    于是"台面 -30 dB、界面 -9.5 dB", 且 rel_db 动作移动量完全不对。
_LEVEL_SEGS = ((0.5, 40.0, -30.0), (0.25, 80.0, -50.0),
               (0.0625, 160.0, -70.0), (0.0, 480.0, -90.0))


def level_db(f):
    """归一值 0..1 → 真台显示的 dB。f=0 是真台的 -∞ 特殊点, 返回 -90 便于排序。"""
    try:
        f = float(f)
    except (TypeError, ValueError):
        return -90.0
    if f <= 0.0:
        return -90.0
    f = min(f, 1.0)
    for lo, k, b in _LEVEL_SEGS:
        if f >= lo:
            return k * f + b
    return -90.0


def level_from_db(db):
    """level_db 的逆: dB → 0..1 (-90..+10)。"""
    try:
        db = float(db)
    except (TypeError, ValueError):
        return 0.0
    db = min(max(db, -90.0), 10.0)
    if db >= -10.0:
        return (db + 30.0) / 40.0
    if db >= -30.0:
        return (db + 50.0) / 80.0
    if db >= -60.0:      # 第三段覆盖 -60..-30, 分界是 -60 不是 -50 ——
        return (db + 70.0) / 160.0   # 写 -50 会让 -54 落进第四段, f 从 0.1 跳到 0.075
    return (db + 90.0) / 480.0


# ------------------------------------------------------- 平台值转换（WING）
# WING 的推子是 **dB 浮点**（-144 = -∞，上限 +10），X32 是 0..1 归一值。
# 曲线本身两家一致（WING 文档实测 0.75→0 dB、0.7→-2 dB、0.675→-3 dB，
# 与 level_db 第一段 dB = 40f - 30 逐点吻合），差别只在 **-∞ 的表示**：
# X32 把 f=0 记作 -90，WING 发 -144。所以下行用 level_db、上行钳到 0，
# 只在两端各加一个 -144 的特例 —— 不要另造一条曲线。
WING_DB_SILENT = -144.0


def fader_to_db(f):
    """0..1 归一值 → WING 的 dB 推子值（0 = -144 即 -∞）。"""
    f = _finite(f, 0.0)
    if f <= 0.0:
        return WING_DB_SILENT
    return round(level_db(f), 2)


def fader_from_db(db):
    """WING 的 dB 推子值 → 0..1（-144 与 -90 都落到 0）。"""
    db = _finite(db, WING_DB_SILENT)
    if db <= -90.0:
        return 0.0
    return round(level_from_db(db), 4)


def _is_group_fader(sp):
    """是不是组级推子（ch_fader / bus_fader …），排除 EQ/门 里的同名 field。"""
    return sp.kind == sp.group + "_fader"


def _is_group_mute(sp):
    """是不是组级静音（ch_on / bus_on …）。"""
    return sp.kind == sp.group + "_on"


def _snap(sp, v):
    """钳制 + 按档位量化。真台的旋钮是"有级"的，不是连续模拟量 ——
    存一个连续值回去，回读时就会发现"设 124.3 Hz 回来变成 124.3 但台上是 124.3±档"。"""
    if sp.enum is not None:
        # 最近的枚举成员。hpslope 手册记 enum{12,18,24}，按**字面值**传
        # （config/color 那种 {OFF,RD,...} 才是索引制）——万一真机按索引回
        # 0/1/2，会被吸附成 12/12/18：不精确，但不会静默变成 0。
        # dyn/ratio 相反：线上是索引、本地存比值 (enum_wire="index")，吸附目标
        # 仍是**比值**，索引换算在 param_wire 里做。
        return _nearest_enum(sp, v)
    lo, hi = sp.lo, sp.hi
    lim = _ACTIVE_LIMITS.get(sp.kind)
    if lim is not None:
        lo, hi = max(lo, lim[0]), min(hi, lim[1])
        if lo > hi:                 # 防御: 量程表写反了也不该把值锁死
            lo, hi = hi, lo
    v = min(max(v, min(lo, hi)), max(lo, hi))
    if sp.logsteps:
        # 对数表: 默认从 lo 走到 hi; logdesc=True 时方向相反 (EQ 的 Q: 10 → 0.3)。
        # 方向只影响**量化点的排列**, 不影响取值范围 —— lo/hi 永远是自然上下界,
        # 这样前端滑块 (min<max) 与后端钳制用同一对数, 不用为"反向"各写一套。
        a, b = (hi, lo) if sp.logdesc else (lo, hi)
        span = math.log(b / a)
        i = int(round((sp.logsteps - 1) * math.log(max(v, 1e-9) / a) / span))
        i = min(max(i, 0), sp.logsteps - 1)
        return a * (b / a) ** (i / (sp.logsteps - 1))
    if sp.linsteps > 1:
        # 线性等分档位 (level 161 档 / fader 1024 档): 值是 k/(n-1), k 取整。
        # 用"点数"而不是 step, 是因为 1/160 无法用十进制 step 精确表达 ——
        # 0.00625 这种步长跑 160 次会漂 1e-15, 回读比对就会误判成"变了"。
        n = sp.linsteps - 1
        v = round(v * n) / n
        return round(min(max(v, lo), hi), 6)
    if sp.step:
        v = round(v / sp.step) * sp.step
        return round(min(max(v, lo), hi), 6)         # 量化后再钳 (0.3 这种非整步长下限)
    return v


def param_coerce(kind, value):
    """外部输入 → 该参数的合法值。未知 kind 抛 KeyError（调用方按"不支持"处理）。"""
    sp = PARAM_SPECS[kind]
    if sp.vtype == "bool":
        return _truthy(value)
    return _snap(sp, _finite(value, sp.default))


def param_wire(sp, v, prof=None):
    """本地存的值 → **线上**该发的值 (OSC 回包与桥接转发共用)。

    四种换算:
      · prof.fader_db       → 组级推子发 dB (WING)，不是 0..1
      · prof.mute_inverted  → 组级静音线上 1=muted (WING)，本地 1=开
      · enum_wire="index"   → 本地比值/自然值换算成枚举下标 (dyn/ratio 4.0 → 6)
      · enum_wire="label"   → 发**枚举名字符串** (发送取点 "POST")
      · 其余按 vtype 定型 (float 发浮点 / bool 发 0-1 / int 发整数)

    prof 省略时按 X32 规则（历史行为不变）—— 调用方拿不到控台实例就别传。
    """
    if prof is not None:
        if getattr(prof, "fader_db", False) and _is_group_fader(sp):
            return fader_to_db(v)
        if getattr(prof, "mute_inverted", False) and _is_group_mute(sp):
            return 0 if _truthy(v) else 1
    if sp.vtype == "bool":
        return 1 if _truthy(v) else 0
    if sp.enum is not None and sp.enum_wire == "index":
        return sp.enum.index(_nearest_enum(sp, _finite(v, sp.default)))
    if sp.labels and sp.enum_wire == "label":
        # 真台两种形式都收 (enum 的字符串形式是合法 OSC), 但回包形式因固件而异。
        # 这个分支的存在意义是**可被探测/切换**, 不是默认路径 —— 见 --tap-wire。
        k = int(round(_finite(v, sp.default)))
        return sp.labels[k] if 0 <= k < len(sp.labels) else str(k)
    return float(v) if sp.vtype == "float" else int(round(float(v)))


def param_from_wire(sp, v, prof=None):
    """**线上**来的值 → 本地存的值 (外部 OSC 写入与真台回读共用)。

    真台的 dyn/ratio 收到的是索引 (0..11)，直接落库会把 6 存成 "6:1" ——
    实际是 4:1。这一层换算只在入线处做一次，落库之后全项目都看到比值。

    prof.fader_db / prof.mute_inverted 的反向换算与 param_wire 对称：
    WING 的 -144 dB 落回本地 0，`mute=1` 落回本地"开=0"。

    另外支持**枚举名字符串** (收到 "POST" / "GRP" / "GATE")：协议允许多种写法,
    真台不同固件回包可能是字符串。以前这里会把字符串当非法值 → 落回默认值,
    表现为"读一次真台，取点就从 POST 变成默认值"——静默改值, 最难查的一类。
    """
    if prof is not None and not (isinstance(v, str) and sp.labels):
        if getattr(prof, "fader_db", False) and _is_group_fader(sp):
            return fader_from_db(v)
        if getattr(prof, "mute_inverted", False) and _is_group_mute(sp):
            # 真台回包可能是 `1` / `1.0` / `"1"` 三种形态，一律先转数值再取反；
            # 转不动（返回 "NODE NOT FOUND" 之类）就交回通用路径，不吞掉错误。
            try:
                x = float(v) if isinstance(v, str) else v
            except (TypeError, ValueError):
                return param_coerce(sp.kind, v)
            return 0 if _truthy(x) else 1
    if isinstance(v, str) and sp.labels:
        s = v.strip().upper()
        for _i, _lab in enumerate(sp.labels):
            if _lab.upper() == s:
                if sp.vtype == "bool":
                    return int(_i)
                if sp.enum is not None and sp.enum_wire == "index":
                    return sp.enum[_i]
                return float(_i)
        # 名字不认识 → 继续按数值解析; 解析不了会落回 default (由 _finite 兜底)
    if sp.vtype == "bool":
        return _truthy(v)
    if sp.enum is not None and sp.enum_wire == "index":
        try:
            i = int(round(float(v)))
        except (TypeError, ValueError):
            return param_coerce(sp.kind, sp.default)
        if 0 <= i < len(sp.enum):
            return sp.enum[i]
        # 超范围: 当作字面值处理 (有人直接发 4 表示 4:1), 由 _snap 吸附
        return _snap(sp, _finite(v, sp.default))
    return param_coerce(sp.kind, v)


def param_fmt(sp, v):
    """日志/界面用的值文本（带单位与符号）。"""
    if sp.vtype == "bool":
        i = 1 if _truthy(v) else 0
        if sp.labels:                 # 插入位置: PRE/POST 而不是"开/关"
            return sp.labels[i]
        return "开" if i else "关"
    if sp.labels and sp.vtype == "int":
        # 索引型枚举 (EQ 类型 / 发送取点): 显示名字比显示 0..5 有用得多
        k = int(round(_finite(v, sp.default)))
        return sp.labels[k] if 0 <= k < len(sp.labels) else str(k)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if sp.unit == "dB":
        return "%+.*f dB" % (sp.dec, x)
    # "%" ":1" 这类贴边单位不加空格 (75% / 4.0:1), 其余加
    sep = sp.unit if sp.unit in ("%", "°", ":1") else ((" " + sp.unit) if sp.unit else "")
    return "%.*f%s" % (sp.dec, x, sep)


def blank_row(group):
    """一个空行字典: 该组所有登记参数的出厂值（推子 0.75 / 静音开 / 参数缺省）。"""
    return {sp.field: sp.default for sp in _SPECS if sp.group == group}


def spec_section(sp):
    """参数属于通道面板的哪一页: pre | gate | dyn | eq。

    **三端共用的唯一规则** —— 网页 (app.js)、iOS、Android 都读这里下发的 sec,
    不许各自再写一份 if/startswith。曾经网页自己按前缀猜, 原生端各抄一遍,
    加参数时漏改一处就是"控件跑到别的页签里"。
    插入没有独立页: ins_* 归到 pre (前置), 与真台的 BLOCK/PRE 顺序一致。
    """
    f = sp.field
    if f == "pan":
        return "pre"
    if f.startswith("gate_"):
        return "gate"
    if f.startswith("dyn_"):
        return "dyn"
    if f.startswith("eq"):
        return "eq"
    return "pre"


def ui_specs(group):
    """一组的参数元数据 (给前端/App 生成控件, 不含任何协议地址)。

    字段名刻意短: 这个包每次打开通道详情都会拉一次, 6 组加起来约 8KB,
    用长键名 (label/lo/hi/step/…) 会白白多出 30%。
    sec = 页签 (见 spec_section), 前端只按它分组, 不再自己猜。
    """
    out = []
    for sp in GROUP_PARAM_SPECS.get(group, ()):
        out.append({
            "f": sp.field, "l": sp.label, "t": sp.vtype,
            "lo": sp.lo, "hi": sp.hi, "st": sp.step, "dec": sp.dec,
            "u": sp.unit, "lg": sp.logsteps, "ld": 1 if sp.logdesc else 0,
            "lb": list(sp.labels) if sp.labels else None,
            "en": list(sp.enum) if sp.enum else None,
            "dv": sp.default, "un": 1 if sp.unverified else 0,
            "sec": spec_section(sp),
        })
    return out
