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
本表只收**按路存储的标量参数**（推子/静音/新增的 12 个通道参数）。
以下三类**故意不入表**，继续走各自的显式分支：
  · 名称  —— 存在 STATE.names（(group, idx) 键），不在行字典里，且带持久化/日志
  · DCA 归属 —— 不是存储值，而是 dca_members 的位掩码投影（set 时要改成员表）
  · FX 机架 —— 参数号随算法变化，整槽存/整槽回归才有意义（见 consts.REVERB_DEFAULTS）
把它们塞进这张表只会得到一堆永远为真的 if，不会得到任何复用。

地址与量程来源
--------------
新增 12 个通道参数的地址/类型/量程逐条对齐 **Behringer X32 Protocol Manual**
（官方 OSC 表，M32 同协议）。两处手册之间有出入的地方按"取宽 + 注释"处理：
  · preamp/trim：手册 [-12,12]（仅数字源），非官方表 [-18,18] → 取 [-18,18]
  · delay/time：手册 linf [0.300, 500.000, 0.100] ms（下限不是 0！）
  · preamp/hpf：手册 logf [20.000, 400.000, 101] Hz —— 真台是 101 个对数档位，
    所以这里用 logsteps=101 量化，而不是连续浮点
  · gate/thr [-80,0] / gate/range [3,60]（正值，表示衰减量）
  · insert/pos enum {PRE,POST} → 线上传 0/1（同 config/color 的 enum 语义）
  · preamp/hpslope enum {12,18,24} → 按字面值传（.scn 导出同款）；
    万一真机回 0/1/2，nearest-snap 会落到 12/18/24，不会静默变 0
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
                 push=False, preset=True, note=""):
        self.kind = kind                  # 语义管道里的名字, 如 "ch_trim"
        self.group = group                # 台面分组 (STATE 的属性名), 如 "ch"
        self.field = field                # 行字典里的键, 如 "trim"
        self.osc = osc                    # OSC 地址模板; main 系列没有 %02d
        self.vtype = vtype                # "float" | "int" | "bool"
        self.lo = float(lo)
        self.hi = float(hi)
        if default is None:
            default = 0.0
        # bool/int 必须存**整数**：旧代码 on 的默认值是 1 不是 1.0，
        # /api/state 会因此从 1 变成 1.0（JSON 形状变化，前端虽不炸但不干净）
        self.default = int(default) if vtype in ("bool", "int") else float(default)
        self.step = float(step)           # 线性档位 (真台旋钮是"有级"的)
        self.logsteps = int(logsteps)     # 对数档位数 (手册里的 logf [lo,hi,N])
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
              "fxret": "/fxret/%02d/mix/fader", "mtx": "/mtx/%02d/mix/fader",
              "main": "/main/st/mix/fader"}                 # main 无 %02d！
_ON_OSC = {"ch": "/ch/%02d/mix/on", "bus": "/bus/%02d/mix/on",
           "dca": "/dca/%02d/on",
           "auxin": "/auxin/%02d/mix/on", "usb": "/usb/%02d/mix/on",
           "fxret": "/fxret/%02d/mix/on", "mtx": "/mtx/%02d/mix/on",
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
_CH_EXTRA = (
    ParamSpec("ch_trim", "ch", "trim", "/ch/%02d/preamp/trim",
              vtype="float", lo=-18.0, hi=18.0, default=0.0, step=0.25,
              label="增益", unit="dB", dec=1,
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
              label="低切斜率", unit="dB/oct", dec=0,
              note="手册 enum{12,18,24} 按字面值; 出厂默认需真机确认"),
    ParamSpec("ch_hpf_f", "ch", "hpf_f", "/ch/%02d/preamp/hpf",
              vtype="float", lo=20.0, hi=400.0, default=80.0, logsteps=101,
              label="低切频率", unit="Hz", dec=0,
              note="手册 logf[20,400,101] Hz; 出厂默认频率需真机确认, "
                   "暂取人声常用 80 Hz"),
    ParamSpec("ch_ins_on", "ch", "ins_on", "/ch/%02d/insert/on",
              vtype="bool", default=0, label="插入"),
    ParamSpec("ch_ins_pos", "ch", "ins_pos", "/ch/%02d/insert/pos",
              vtype="bool", default=0, label="插入位置", labels=("PRE", "POST"),
              note="enum{PRE,POST} → 0/1; 出厂位置需真机确认, 暂取 PRE"),
    ParamSpec("ch_gate_on", "ch", "gate_on", "/ch/%02d/gate/on",
              vtype="bool", default=0, label="噪声门"),
    ParamSpec("ch_gate_thr", "ch", "gate_thr", "/ch/%02d/gate/thr",
              vtype="float", lo=-80.0, hi=0.0, default=-40.0, step=0.5,
              label="门限", unit="dB", dec=1,
              note="手册 linf[-80,0,0.5] dB; 出厂阈值需真机确认, 暂取 -40 dB"),
    ParamSpec("ch_gate_range", "ch", "gate_range", "/ch/%02d/gate/range",
              vtype="float", lo=3.0, hi=60.0, default=15.0, step=1.0,
              label="门范围", unit="dB", dec=0,
              note="手册 linf[3,60,1] dB(正值=衰减量); 出厂值需真机确认"),
)
_SPECS.extend(_CH_EXTRA)

PARAM_SPECS = {sp.kind: sp for sp in _SPECS}

# 新增的通道参数 (不含推子/静音) —— presets/前端面板/OSC 日志都遍历它
CH_PARAM_SPECS = tuple(sp for sp in _SPECS if sp.group == "ch" and not sp.push)

# 地址索引: (第3段, 第4段) → [spec, ...], 只收新增 kind。
# 推子/静音的老地址模式在 state.match_address 里有专门分支 (行为逐字未动)，
# 不在这里重复登记, 免得两条路径互相覆盖。
CH_EXTRA_ADDR = {}
for _sp in _SPECS:
    if _sp.group != "ch" or _sp.push:
        continue
    _parts = _sp.osc.strip("/").split("/")
    if len(_parts) == 4 and "%02d" in _parts[1]:
        CH_EXTRA_ADDR.setdefault((_parts[2], _parts[3]), []).append(_sp)


def spec_addr(sp, idx):
    """spec → 真实地址。main 系列模板里**没有 %02d**，直接 % 会抛 TypeError
    (历史 bug, 见共享协议规范 §3.5 第 4 条)。"""
    return sp.osc % idx if "%02d" in sp.osc else sp.osc


def _snap(sp, v):
    """钳制 + 按档位量化。真台的旋钮是"有级"的，不是连续模拟量 ——
    存一个连续值回去，回读时就会发现"设 124.3 Hz 回来变成 124.3 但台上是 124.3±档"。"""
    if sp.enum is not None:
        # 最近的枚举成员。hpslope 手册记 enum{12,18,24}，按**字面值**传
        # （config/color 那种 {OFF,RD,...} 才是索引制）——万一真机按索引回
        # 0/1/2，会被吸附成 12/12/18：不精确，但不会静默变成 0。
        return min(sp.enum, key=lambda e: (abs(e - v), e))
    v = min(max(v, sp.lo), sp.hi)
    if sp.logsteps:
        span = math.log(sp.hi / sp.lo)
        i = int(round((sp.logsteps - 1) * math.log(max(v, sp.lo) / sp.lo) / span))
        i = min(max(i, 0), sp.logsteps - 1)
        return sp.lo * (sp.hi / sp.lo) ** (i / (sp.logsteps - 1))
    if sp.step:
        v = round(v / sp.step) * sp.step
        return round(min(max(v, sp.lo), sp.hi), 6)   # 量化后再钳 (0.3 这种非整步长下限)
    return v


def param_coerce(kind, value):
    """外部输入 → 该参数的合法值。未知 kind 抛 KeyError（调用方按"不支持"处理）。"""
    sp = PARAM_SPECS[kind]
    if sp.vtype == "bool":
        return _truthy(value)
    return _snap(sp, _finite(value, sp.default))


def param_fmt(sp, v):
    """日志/界面用的值文本（带单位与符号）。"""
    if sp.vtype == "bool":
        i = 1 if _truthy(v) else 0
        if sp.labels:                 # 插入位置: PRE/POST 而不是"开/关"
            return sp.labels[i]
        return "开" if i else "关"
    if sp.enum is not None:
        return str(int(round(v)))
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    if sp.unit == "dB":
        return "%+.*f dB" % (sp.dec, x)
    return "%.*f%s" % (sp.dec, x, (" " + sp.unit) if sp.unit else "")


def blank_row(group):
    """一个空行字典: 该组所有登记参数的出厂值（推子 0.75 / 静音开 / 参数缺省）。"""
    return {sp.field: sp.default for sp in _SPECS if sp.group == group}
