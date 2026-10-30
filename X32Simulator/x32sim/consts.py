# -*- coding: utf-8 -*-
"""x32sim/consts — 数据表: 容量/默认名/地址模板/动作与预设白名单/FX 元数据"""

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

from .params import CH_PARAM_SPECS, PARAM_SPECS, SEND_PARAM_SPECS
# ⚠️ make_single.py 只认单行相对导入 (多行会被判"不支持"直接拒生成)
from .fxlib import FX_CAT_LABEL, FX_ENUM_14, FX_ENUM_58, FX_SLOTS_HEAVY, FX_TYPE_ALIAS, M32_FX_ALGOS, X32_FX_ALGOS, algo_names, param_spec, slot_allows  # noqa: E501

# ---------------------------------------------------------------- 混响 / FX 机架
# X32 有 8 个 FX 槽. 效果库（算法 → 参数表）已全量标定，见 **fxlib.py**：
#   * 61 种算法（FX 1–4 全量；FX 5–8 仅 34 种轻量算法，DSP 算力限制）；
#   * 每种算法的 par 号顺序、参数名、类型（linf/logf/enum）、量程，全部来自
#     Patrick-Gilles Maillot《UNOFFICIAL X32/M32 OSC REMOTE PROTOCOL》
#     Effects Parameters 章 —— 与真台 OSC 地址逐条对齐；
#   * 参数地址 = `/fx/<槽>/par/<两位补零参数号>`，槽号不补零。
#
# ⚠️ 与 v1.5.x 旧标定的差异（旧表缺真机验证，已证实不准确）：
#   * 混响 par01 = **Pre Delay**，par02 才是 **Decay**（旧表 time/predelay 弄反了）；
#   * Damping 在真台是 **1k–20k Hz**（旧表当成 0–1 归一化值）。
#   type 名迁移：PLATE→PLAT / CHAMBER→CHAM / VINTAGE→VREV（persist 层别名表）。
#
# ⚠️ 效果库**属于控台驱动**，不是全局表（见「控台驱动层」一节）。
#    X32 与 M32 使用**同一套效果引擎**（M32 是 Midas 贴牌的同代产品），因此
#    两张表当前内容一致；但**分别持有**，将某一版固件效果库出现出入时，只需改
#    对应的那一张，公共代码与卡片动作都不动。
FX_COUNT = 8
# 出厂默认混响参数。索引 = par 号（按 fxlib 正确映射：predelay=01, decay=02, damp=04）。
# 数值为工程经验值，**未经真机核对** —— 出厂值核对走 /api/info.calibration 机制。
REVERB_DEFAULTS = {
    "HALL":  {1: 25.0, 2: 2.40, 4: 6000.0},
    "PLAT":  {1: 15.0, 2: 1.80, 4: 6000.0},
    "ROOM":  {1: 12.0, 2: 1.10, 4: 6000.0},
    "CHAM":  {1: 20.0, 2: 1.60, 4: 6000.0},
    "VREV":  {1: 18.0, 2: 1.50},
    "AMBI":  {1: 20.0, 2: 2.00, 4: 6000.0},
    "RPLT":  {1: 20.0, 2: 2.60, 4: 6000.0},
    "VRM":   {1: 5.0, 2: 2.00},
    "GATE":  {1: 20.0, 2: 400.0},
    "RVRS":  {1: 20.0, 2: 400.0},
}

# 图示/参数均衡的出厂态: 全部频段 0 dB (平直)。真台加载 GEQ/TEQ 即此状态,
# 且 0 在 [-15,15] 量程内 —— 不播种的话 32/64 个频段全是"未设置", 页面上一片
# 未设置, 用户根本没法用。同样**未经真机核对** (与 REVERB_DEFAULTS 同款约定)。
_EQ_FLAT = ("GEQ", "GEQ2", "TEQ", "TEQ2")
EQ_FLAT_DEFAULTS = {a: {p: 0.0 for p in range(1, X32_FX_ALGOS[a]["n"] + 1)}
                    for a in _EQ_FLAT}
# 切换算法时播种的出厂默认值 (按 par 号)。查不到的算法 → 空 (全部未设置)。
FX_SWITCH_DEFAULTS = dict(REVERB_DEFAULTS)
FX_SWITCH_DEFAULTS.update(EQ_FLAT_DEFAULTS)


# Tap 定拍 / 时值换算
TAP_INTERVAL_MIN = 0.15      # 两次点击靠得比这还近 → 抖动, 忽略
TAP_RESET_GAP = 2.0          # 超过这么久没点 → 视为新的一组, 重头开始
TAP_MAX_INTERVALS = 8        # 最多用最近 8 个间隔求均值
TAP_BPM_MIN, TAP_BPM_MAX = 30.0, 300.0
NOTE_BEATS = {"1/16": 0.25, "1/8": 0.5, "1/4": 1.0, "1/2": 2.0, "1": 4.0, "2": 8.0}


# 各组通道数量 (名称读写与编辑器共用); main 单独处理 idx=0
NAME_COUNTS = {"ch": 32, "bus": 16, "dca": 8, "auxin": 6, "usb": 2, "fxret": 8, "mtx": 6}
# 出厂默认名称: 与内置卡片/控制键呼应 (15=音乐, Bus15=耳返), 均可在网页或 OSC 里改
DEFAULT_NAMES = {
    ("ch", 1): "底鼓", ("ch", 2): "军鼓", ("ch", 3): "踩镲", ("ch", 4): "嗵鼓",
    ("ch", 5): "吊镲", ("ch", 6): "贝斯", ("ch", 7): "电吉1", ("ch", 8): "电吉2",
    ("ch", 9): "木吉", ("ch", 10): "键盘1", ("ch", 11): "键盘2", ("ch", 12): "采样",
    ("ch", 13): "无线麦1", ("ch", 14): "主持麦", ("ch", 15): "音乐", ("ch", 16): "视频声",
    ("bus", 1): "鼓监听", ("bus", 2): "乐队监听", ("bus", 3): "主唱监听", ("bus", 15): "耳返",
    ("dca", 1): "乐队", ("dca", 2): "人声", ("dca", 3): "鼓组",
    ("main", 0): "主输出",
}


# 网页分组前缀 → 推子/静音 kind 映射 (/api/set 与 /api/setbatch 共用)
# ch 一行由 params.CH_PARAM_SPECS 推导: 新增通道参数只改 params.py 一处,
# 这里、卡片动作白名单、预设范围白名单、前端下拉 全部跟着走 (前端另有同内容镜像)
SET_KINDMAP = {"ch": {"fader": "ch_fader", "on": "ch_on"},
               "bus": {"fader": "bus_fader", "on": "bus_on"},
               "dca": {"fader": "dca_fader", "on": "dca_on"},
               "main": {"fader": "main_fader", "on": "main_on"},
               "auxin": {"fader": "auxin_fader", "on": "auxin_on"},
               "usb": {"fader": "usb_fader", "on": "usb_on"},
               "fxret": {"fader": "fxret_fader", "on": "fxret_on"},
               "mtx": {"fader": "mtx_fader", "on": "mtx_on"}}
SET_KINDMAP["ch"].update({sp.field: sp.kind for sp in CH_PARAM_SPECS})
# 发送是二维目标: 参数名与 kind 同名 (send_level/send_on/send_tap),
# 调用方用 (group="send", param="send_level", idx=通道, bus=Bus) 寻址,
# HTTP/动作层负责 send_pack 打包。**故意不用 level/on/tap 这种短名** ——
# "on" 已经属于通道静音, 同名会让预设/卡片白名单串味。
SET_KINDMAP["send"] = {sp.kind: sp.kind for sp in SEND_PARAM_SPECS}

# 推子/静音的 OSC 地址模板: 由 params.PARAM_SPECS **推导** (单一事实来源,
# 曾经这里是字面字典, 改一个地址要同时惦记 profiles.addr 里的另一份)。
# 只收 push=True 的项 —— 新增的 12 个通道参数不进 150ms 周期推送
# (见 params.py 里 push 字段的说明: 那笔流量治理的账不能还回去)。
FADER_ADDR = {sp.kind: sp.osc for sp in PARAM_SPECS.values()
              if sp.push and sp.kind.endswith("_fader")}
ON_ADDR = {sp.kind: sp.osc for sp in PARAM_SPECS.values()
           if sp.push and sp.kind.endswith("_on")}
KIND_LABEL = {"ch": "通道", "bus": "输出Bus", "dca": "DCA", "main": "Main",
              "auxin": "AuxIn", "usb": "USB", "fxret": "FX返回", "mtx": "Matrix",
              "send": "发送",
              "fx_par": "FX参数", "fx_type": "FX算法", "fx_name": "FX名称",
              "fx_on": "FX开关", "fx_mix": "FX混合比"}

# ---- 预设范围：组 → 该组能存的参数 -------------------------------------
# 为什么要有"范围"：以前一条预设永远是"79 路 × 推子+静音"的全量回归，用户想只还原
# 人声那几路做不到，一动就是整台。范围把"存什么"和"还原什么"绑在一起，
# 回归只写范围内的键 —— 没勾的通道一个字节都不碰。
#   fader/on/name 是 79 路通用三项；dca 只对输入通道有意义（位掩码归属）；
#   FX 整槽存（算法/名称/开关/混合比/参数）—— 参数号随算法变化，拆开存没有意义；
#   scene 只记录当前场景号（回归时不发 OSC，发不发由卡片的场景动作决定）。
PRESET_GROUP_PARAMS = {
    "ch": ("fader", "on", "name", "dca"),
    "bus": ("fader", "on", "name"),
    "dca": ("fader", "on", "name"),
    "auxin": ("fader", "on", "name"),
    "usb": ("fader", "on", "name"),
    "fxret": ("fader", "on", "name"),
    "mtx": ("fader", "on", "name"),
    "main": ("fader", "on", "name"),
    "fx": ("slot",),
    "scene": ("scene",),
    # 发送: 范围里的"编号"指**通道号** (1-32), 抓取时按 16 个 Bus 展开。
    # 512 个 (通道,Bus) 目标没法在界面上一个个勾, 用通道做单位才可用。
    "send": ("send_level", "send_on", "send_tap"),
}
# 输入通道的参数白名单跟上登记表 (增益/反相/延时/低切/插入/门)
PRESET_GROUP_PARAMS["ch"] = tuple(PRESET_GROUP_PARAMS["ch"]) + tuple(
    sp.field for sp in CH_PARAM_SPECS)
PRESET_GROUP_LABEL = {"ch": "输入通道", "bus": "Mix Bus", "dca": "DCA",
                      "auxin": "Aux In", "usb": "USB", "fxret": "FX 返回",
                      "mtx": "Matrix", "main": "主输出", "fx": "FX 机架",
                      "scene": "场景号", "send": "发送 (通道→Bus)"}
PRESET_PARAM_LABEL = {"fader": "推子", "on": "静音", "name": "名称",
                      "dca": "DCA 归属", "slot": "整槽", "scene": "当前场景",
                      "send_level": "发送电平", "send_on": "发送开关",
                      "send_tap": "发送取点"}
PRESET_PARAM_LABEL.update({sp.field: sp.label for sp in CH_PARAM_SPECS})
PRESET_DEFAULT_SCOPE = {
    "groups": ["ch", "bus", "dca", "auxin", "usb", "fxret", "mtx", "main"],
    "params": ["fader", "on"],
    "indices": {},
}


FADE_ACT_KINDS = ("fade_all", "fade_odd_even", "fade_ch", "fade_bus",
                  "fade_from", "fade_from_bus", "rel_db")


# ---------------------------------------------------------------- 通用动作 set
#
# 背景: 原有 12 个动作是"常见演出套装"（全体渐变 / 奇偶过渡 / 相对增减 dB …），
# 覆盖面广但补不全 —— 例如"把 DCA 3 拉到 80%""把 FX1 混响时间改成 2.4 秒"就做不到。
#
# 通用动作把它收敛成一件事: 指定**目标**和**值**。
#   {"kind":"set", "target":{"group":"dca","id":3,"param":"fader"}, "value":0.8}
#   {"kind":"set", "target":{"group":"fx","id":1,"param":"time"}, "value":2.4}
#   {"kind":"set", "target":{"group":"scene","id":5}}
#   target 里带 duration>0 时走渐变 (仅 param="fader" 有意义)。
#
# ⚠️ target 描述的是**语义**（哪个分组的第几路、改哪个参数），不含任何协议信息；
#    落到真实地址的活交给 ConsoleProfile（见"控台驱动层"）。所以同一个 set 动作
#    天然跨控台 —— 这不是额外做的适配，而是它本来就走在语义管道上。

SET_GROUPS = ("ch", "bus", "dca", "main", "auxin", "usb", "fxret", "mtx",
              "fx", "scene", "send")
SET_PARAMS = {
    "ch": ("fader", "on"), "bus": ("fader", "on"), "dca": ("fader", "on"),
    "main": ("fader", "on"), "auxin": ("fader", "on"), "usb": ("fader", "on"),
    "fxret": ("fader", "on"), "mtx": ("fader", "on"),
    "fx": ("type", "name", "on", "mix", "time", "predelay", "damp"),
    "scene": ("load",),
    # 发送: 目标 = (id=通道, bus=Bus), 见 actions._clean_set_action
    "send": ("send_level", "send_on", "send_tap"),
}
# 通用设置动作的通道参数白名单 (与 SET_KINDMAP["ch"] 同源)
SET_PARAMS["ch"] = tuple(SET_PARAMS["ch"]) + tuple(sp.field for sp in CH_PARAM_SPECS)
SET_GROUP_LABEL = {"ch": "通道", "bus": "Bus", "dca": "DCA", "main": "主输出",
                   "auxin": "AuxIn", "usb": "USB", "fxret": "FX返回",
                   "mtx": "Matrix", "fx": "FX", "scene": "场景",
                   "send": "发送"}
SET_PARAM_LABEL = {"fader": "推子", "on": "开关", "type": "算法", "name": "名称",
                   "mix": "混合比", "time": "混响时间", "predelay": "预延迟",
                   "damp": "阻尼", "load": "调用",
                   "send_level": "发送电平", "send_on": "发送开关",
                   "send_tap": "发送取点"}
SET_PARAM_LABEL.update({sp.field: sp.label for sp in CH_PARAM_SPECS})


CURVES = ("linear", "ease_out", "ease_in", "smooth")


# 保存期只做**格式**清洗：防负数、防几百几千这种荒唐值。
# 真正的范围校验在**执行期**做（target_exists / _fade_targets_for 的过滤），
# 这样卡片天然跨控台 —— 在 32 路的台子上存下"写到第 40 路"的卡片，
# 换到 48/64 路的台子就能直接生效，而不是保存时就被钳死成 32。
# （曾经的写法在保存时就按当前控台规模钳 id，与"换到 64 通道后台子能写到 64 路"
#   的注释自相矛盾。）
SAVE_MAX = {"ch": 64, "bus": 32, "dca": 16, "auxin": 16, "usb": 8,
            "fxret": 16, "mtx": 16, "main": 0}
