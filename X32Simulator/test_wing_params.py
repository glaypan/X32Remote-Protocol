#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WING 通道处理参数回归 (N1) —— 纯计算, 不起服务。

覆盖五件事：
  A  地址映射：登记的 kind 全部精确匹配协议原文，未登记的一律 None
  B  回读闭环：每个登记的 kind 走 addr→parse_addr→addr 必须回到原地址
  C  值转换：发送电平走 dB、压缩比按组区分字符串/数值、检波包络发字符串
  D  钳制与档位吸附：超范围不发出去，档位不在交集里提前吸附
  E  参数规格过滤：WING 下不下发它没有地址的控件，X32 全量不变
  F  场景召回 / FX 返回组：形状差异收在 profile 里，调用方不写 if id=="wing"
  G  /api/fxcaps 载荷：App（OSC 直连，看不到 w 字段）靠它决定哪些滑杆置灰

⚠️ 这里的期望值全部来自《WING Remote Protocols》的地址表原文，
   不是"看起来像"。改 wingmap.py 时如果这里红了，先核文档再改测试。
"""
import sys

sys.path.insert(0, ".")

import x32sim.profiles as P  # noqa: E402
from x32sim.params import (GROUP_PARAM_SPECS, PARAM_SPECS, param_coerce,  # noqa: E402
                           param_from_wire, param_wire, send_pack, send_unpack,
                           ui_specs)
from x32sim.wingmap import (WING_PARAM_ADDR, WING_PARAM_LIMITS,  # noqa: E402
                            WING_RATIO, WING_VALUE_SNAP)

_ok = [0, 0]


def check(name, cond, extra=""):
    _ok[1] += 1
    if cond:
        _ok[0] += 1
    else:
        print("FAIL %s%s" % (name, ("  | " + str(extra)) if extra else ""))


W = P.WingProfile()
X = P.X32Profile()

# ============================================================ A 地址映射
print("== A 地址映射 ==")
# (kind, idx, 期望地址) —— 逐条对照文档原文
CASES = [
    # EQ：X32 的 4/6 段与 WING 的 band 1..N 一一对齐
    ("ch_eq_on", 1, "/ch/1/eq/on"),
    ("ch_eq1_f", 1, "/ch/1/eq/1f"),
    ("ch_eq1_g", 1, "/ch/1/eq/1g"),
    ("ch_eq1_q", 1, "/ch/1/eq/1q"),
    ("ch_eq4_q", 7, "/ch/7/eq/4q"),
    ("bus_eq_on", 3, "/bus/3/eq/on"),
    ("bus_eq6_g", 3, "/bus/3/eq/6g"),
    ("mtx_eq6_f", 2, "/mtx/2/eq/6f"),
    ("main_eq6_q", 0, "/main/1/eq/6q"),
    # 输入段
    ("ch_trim", 4, "/ch/4/in/set/trim"),
    ("ch_invert", 4, "/ch/4/in/set/inv"),
    ("auxin_trim", 2, "/aux/2/in/set/trim"),
    ("auxin_invert", 2, "/aux/2/in/set/inv"),
    # 低切（preamp/hpf → flt/lc）
    ("ch_hpf_on", 1, "/ch/1/flt/lc"),
    ("ch_hpf_f", 1, "/ch/1/flt/lcf"),
    ("ch_hpf_slope", 1, "/ch/1/flt/lcs"),
    # 门（只有 ch）
    ("ch_gate_on", 2, "/ch/2/gate/on"),
    ("ch_gate_thr", 2, "/ch/2/gate/thr"),
    ("ch_gate_range", 2, "/ch/2/gate/range"),
    ("ch_gate_attack", 2, "/ch/2/gate/att"),
    ("ch_gate_hold", 2, "/ch/2/gate/hld"),
    ("ch_gate_release", 2, "/ch/2/gate/rel"),
    # 动态（五组）
    ("ch_dyn_thr", 1, "/ch/1/dyn/thr"),
    ("ch_dyn_mgain", 1, "/ch/1/dyn/gain"),
    ("ch_dyn_attack", 1, "/ch/1/dyn/att"),
    ("ch_dyn_hold", 1, "/ch/1/dyn/hld"),
    ("ch_dyn_release", 1, "/ch/1/dyn/rel"),
    ("bus_dyn_thr", 2, "/bus/2/dyn/thr"),
    ("mtx_dyn_knee", 3, "/mtx/3/dyn/knee"),
    ("ch_dyn_det", 1, "/ch/1/dyn/det"),
    ("ch_dyn_env", 1, "/ch/1/dyn/env"),
    ("ch_dyn_ratio", 1, "/ch/1/dyn/ratio"),
]
for _k, _i, _want in CASES:
    check("%s(%s) → %s" % (_k, _i, _want), W.addr(_k, _i) == _want, str(W.addr(_k, _i)))

# 发送：二维索引
SEND_CASES = [
    ("send_on", send_pack(1, 1), "/ch/1/send/1/on"),
    ("send_level", send_pack(1, 1), "/ch/1/send/1/lvl"),
    ("send_tap", send_pack(1, 1), "/ch/1/send/1/mode"),
    ("send_level", send_pack(7, 12), "/ch/7/send/12/lvl"),
    ("auxin_send_level", send_pack(2, 3, "auxin"), "/aux/2/send/3/lvl"),
    ("bus_send_level", send_pack(4, 2, "bus"), "/bus/4/send/MX2/lvl"),
    ("main_send_on", send_pack(1, 6, "main"), "/main/1/send/MX6/on"),
]
for _k, _i, _want in SEND_CASES:
    check("%s → %s" % (_k, _want), W.addr(_k, _i) == _want, str(W.addr(_k, _i)))

# 结构不同 → 必须 None（宁可没控件，也不发错地址）
REJECT = [
    # WING 的 EQ 中间段没有类型（固定 PEQ）
    ("ch_eq1_type", 1), ("bus_eq3_type", 1), ("main_eq1_type", 0),
    # WING 的 aux 没有 EQ 整棵子树
    ("auxin_eq_on", 1), ("auxin_eq1_f", 1), ("auxin_eq4_q", 1),
    # 门/动态的模型与侧链：X32 是固定字段，WING 是 35 种模型 + 独立节点
    ("ch_gate_mode", 1), ("ch_gate_keysrc", 1), ("ch_gate_filter_on", 1),
    ("ch_dyn_mode", 1), ("ch_dyn_pos", 1), ("ch_dyn_keysrc", 1),
    ("ch_dyn_filter_f", 1),
    # 插入：WING 是 preins/postins 插槽节点
    ("ch_ins_on", 1), ("bus_ins_on", 1), ("main_ins_sel", 0),
    # 延时：WING 的单位取决于 in/set/dlymode，未标定
    ("ch_delay_on", 1), ("ch_delay_ms", 1),
    # 没有对应组
    ("fxret_pan", 1), ("usb_pan", 1),
    # X32 的 bus/main 发送本来就没有取点参数（params._send_specs has_tap=False）
    ("bus_send_tap", send_pack(1, 1, "bus")), ("main_send_tap", send_pack(1, 1, "main")),
    # X32 的 auxin 没有动态参数表（真台的 /auxin/NN/dyn/* 也不存在）→
    # WING 的 /aux/N/dyn/* 在内部语义管道里无处表达；main 没有 mix/auto 同理
    ("auxin_dyn_on", 2), ("auxin_dyn_thr", 2), ("main_dyn_auto", 0),
    ("main_dyn_mix", 0),
]
for _k, _i in REJECT:
    check("%s 结构不同 → None" % _k, W.addr(_k, _i) is None, str(W.addr(_k, _i)))

# 幽灵 kind：登记表里的每个 kind 都必须在 PARAM_SPECS 里
# （没有 ParamSpec = 没有 UI/没有写入入口，却会污染地址撞号检查）
_ghost = sorted(k for k in WING_PARAM_ADDR if k not in PARAM_SPECS)
check("登记表里没有幽灵 kind（%d 项）" % len(WING_PARAM_ADDR), not _ghost, _ghost)

# 地址不撞号：同一个地址不能对应两个 kind
_seen = {}
_clash = []
for _kind in WING_PARAM_ADDR:
    _sp = PARAM_SPECS.get(_kind)
    if _sp is not None and _sp.group == "send":
        _idx = send_pack(1, 1, "ch" if _kind.startswith("send_") else _kind.split("_")[0])
    else:
        _idx = 0 if _kind.startswith("main") else 1
    _a = W.addr(_kind, _idx)
    if _a is None:
        continue
    if _a in _seen:
        _clash.append((_a, _seen[_a], _kind))
    _seen[_a] = _kind
check("登记的 %d 个 kind 地址互不撞号" % len(_seen), not _clash, _clash[:3])

# ============================================================ B 回读闭环
print("\n== B 回读闭环 ==")
_rt_bad = []
for _kind in sorted(WING_PARAM_ADDR):
    _sp = PARAM_SPECS.get(_kind)
    if _sp is not None and _sp.group == "send":
        _sg = "ch" if _kind.startswith("send_") else _kind.split("_")[0]
        _idx = send_pack(2, 2, _sg)
    else:
        _idx = 0 if _kind.startswith("main") else 2
    _a = W.addr(_kind, _idx)
    if _a is None:
        _rt_bad.append((_kind, "写地址为 None"))
        continue
    _r = W.parse_addr(_a)
    if _r != (_kind, _idx):
        _rt_bad.append((_kind, _a, _r, (_kind, _idx)))
check("全部 %d 个 kind 地址往返一致" % len(WING_PARAM_ADDR), not _rt_bad, _rt_bad[:3])

check("不认识的地址返 None（不猜成别的参数）",
      W.parse_addr("/ch/1/wobble/xyz") is None)
check("X32 的地址在 WING 下不被误认",
      W.parse_addr("/ch/01/mix/fader") is None, str(W.parse_addr("/ch/01/mix/fader")))

# ============================================================ C 值转换
print("\n== C 值转换 ==")


def wire(kind, v):
    return param_wire(PARAM_SPECS[kind], v, W)


def back(kind, v):
    return param_from_wire(PARAM_SPECS[kind], v, W)


for _f, _db in ((0.0, -144.0), (0.0625, -60.0), (0.25, -30.0),
                (0.5, -10.0), (0.75, 0.0), (1.0, 10.0)):
    check("发送电平 %s → %s dB" % (_f, _db), abs(wire("send_level", _f) - _db) < 1e-6,
          str(wire("send_level", _f)))
check("发送电平 -144 回读为 0（不是 -90 那档）", back("send_level", -144.0) == 0.0,
      str(back("send_level", -144.0)))
check("发送电平 0 dB 回读为 0.75", abs(back("send_level", 0.0) - 0.75) < 1e-4,
      str(back("send_level", 0.0)))

# 压缩比：ch 发字符串（含 .0），bus 发数值
for _v, _s in ((1.1, "1.1"), (2.0, "2.0"), (4.0, "4.0"), (10.0, "10"), (100.0, "100")):
    check("ch 压缩比 %s → %r" % (_v, _s), wire("ch_dyn_ratio", _v) == _s,
          repr(wire("ch_dyn_ratio", _v)))
check("bus 压缩比发数值 4.0", wire("bus_dyn_ratio", 4.0) == 4.0,
      repr(wire("bus_dyn_ratio", 4.0)))
check("bus 压缩比 7.0 原样（WING 的 bus 是连续 1.1..100）",
      wire("bus_dyn_ratio", 7.0) == 7.0, repr(wire("bus_dyn_ratio", 7.0)))

# 压缩比往返：交集内不许漂移
for _v in WING_VALUE_SNAP["ch_dyn_ratio"]:
    _w = wire("ch_dyn_ratio", _v)
    _b = back("ch_dyn_ratio", _w)
    check("ch 压缩比 %s 往返不漂移" % _v, abs(_b - _v) < 1e-9, "%r → %r" % (_w, _b))

# 检波/包络/斜率：WING 收字符串
check("检波 0 → 'PEAK'", wire("ch_dyn_det", 0) == "PEAK", repr(wire("ch_dyn_det", 0)))
check("检波 1 → 'RMS'", wire("ch_dyn_det", 1) == "RMS", repr(wire("ch_dyn_det", 1)))
check("包络 1 → 'LOG'", wire("bus_dyn_env", 1) == "LOG", repr(wire("bus_dyn_env", 1)))
check("低切斜率 12 → '12'（S 类型）", wire("ch_hpf_slope", 12) == "12",
      repr(wire("ch_hpf_slope", 12)))
check("低切斜率 24 → '24'", wire("ch_hpf_slope", 24) == "24",
      repr(wire("ch_hpf_slope", 24)))

# 发送取点：5 档 → 3 档
for _i, _s in ((0, "PRE"), (1, "PRE"), (2, "PRE"), (3, "POST"), (4, "GRP")):
    check("取点 %d → %r" % (_i, _s), wire("send_tap", _i) == _s, repr(wire("send_tap", _i)))
check("取点 'GRP' 回读为 4", back("send_tap", "GRP") == 4.0, str(back("send_tap", "GRP")))

# X32 下这些转换一个都不许生效
for _k, _v, _want in (("send_level", 0.75, 0.75), ("ch_dyn_ratio", 4.0, 6.0),
                      ("ch_dyn_det", 0, 0), ("ch_hpf_slope", 12, 12)):
    _got = param_wire(PARAM_SPECS[_k], _v, X)
    check("X32 下 %s 不走 WING 转换（%r → %r）" % (_k, _v, _want),
          _got == _want or abs(float(_got) - float(_want)) < 1e-9, repr(_got))

# ============================================================ D 钳制与吸附
print("\n== D 钳制与档位吸附 ==")
LIMIT_CASES = [
    ("ch_dyn_thr", -80.0, -60.0),      # WING 下限 -60，X32 是 -80
    ("bus_dyn_mgain", 20.0, 12.0),     # WING 上限 12
    ("main_dyn_release", 2.0, 4.0),    # WING 下限 4
    ("ch_dyn_hold", 0.5, 1.0),         # WING 下限 1
    ("ch_eq1_q", 0.3, 0.44),           # WING 下限 0.44
    ("ch_gate_hold", 0.1, 0.1),        # 在 0..200 内，不动
    ("ch_gate_thr", -70.0, -70.0),     # 在 -80..0 内，不动
]
for _k, _in, _want in LIMIT_CASES:
    _got = wire(_k, _in)
    check("%s %s 钳到 %s" % (_k, _in, _want), abs(_got - _want) < 1e-9, str(_got))

check("X32 下不钳制（-80 原样）",
      abs(param_wire(PARAM_SPECS["ch_dyn_thr"], -80.0, X) + 80.0) < 1e-9)

# 档位吸附：本地 7.0 在 WING 上不存在 → 落 5.0（交集里最近）
check("ch 压缩比 7.0 吸附到 5.0（WING 无 7.0，且 6.0/8.0 不在本地表）",
      param_coerce("ch_dyn_ratio", 7.0, W) == 5.0,
      str(param_coerce("ch_dyn_ratio", 7.0, W)))
check("X32 下 7.0 保持不变", param_coerce("ch_dyn_ratio", 7.0, X) == 7.0,
      str(param_coerce("ch_dyn_ratio", 7.0, X)))

# 交集必须真的等于两边枚举的交 —— 改了任一侧忘了改表就红
_x32_ratio = set(PARAM_SPECS["ch_dyn_ratio"].enum)
_common = tuple(sorted(x for x in WING_RATIO if x in _x32_ratio))
check("WING_VALUE_SNAP 等于两侧枚举交集", _common == WING_VALUE_SNAP["ch_dyn_ratio"],
      "%r vs %r" % (_common, WING_VALUE_SNAP["ch_dyn_ratio"]))
check("本地 7.0 确实不在交集里（说明吸附是必要的）",
      7.0 in _x32_ratio and 7.0 not in WING_RATIO)

# 钳制表不许包含「两边本来就一样」的 kind（多余条目会让 reader 以为有差异）
_equal = ("ch_gate_thr", "ch_gate_range", "ch_gate_attack",
          "ch_eq1_f", "ch_eq1_g", "ch_dyn_attack", "ch_dyn_mix")
check("钳制表只列真正不同的项",
      not any(k in WING_PARAM_LIMITS for k in _equal),
      [k for k in _equal if k in WING_PARAM_LIMITS])

# ============================================================ E 规格过滤
print("\n== E 参数规格过滤 ==")


def _fields(g, prof):
    return {x["f"] for x in ui_specs(g, prof)}


for _g in ("ch", "auxin", "bus", "mtx", "main"):
    check("X32 的 %s 参数规格是全量（%d 项）" % (_g, len(GROUP_PARAM_SPECS[_g])),
          len(ui_specs(_g, X)) == len(GROUP_PARAM_SPECS[_g]),
          "%d vs %d" % (len(ui_specs(_g, X)), len(GROUP_PARAM_SPECS[_g])))

_wing_ch = _fields("ch", W)
for _f in ("eq_on", "eq1_f", "eq1_g", "eq1_q", "hpf_on", "hpf_f", "gate_on",
           "gate_thr", "dyn_thr", "dyn_ratio", "trim", "invert"):
    check("WING 的 ch 保留 %s" % _f, _f in _wing_ch)
for _f in ("eq1_type", "ins_on", "ins_sel", "gate_mode", "dyn_pos",
           "dyn_keysrc", "delay_ms"):
    check("WING 的 ch 去掉 %s" % _f, _f not in _wing_ch)

check("WING 的 auxin 只剩声像/增益/反相（没有 EQ）",
      _fields("auxin", W) == {"pan", "trim", "invert"}, sorted(_fields("auxin", W)))
check("WING 的 bus 有 6 段 EQ", all(("eq%d_f" % b) in _fields("bus", W) for b in range(1, 7)))
check("X32 的 auxin 仍有 4 段 EQ", "eq4_q" in _fields("auxin", X))

# ============================================================ F 场景召回
print("\n== F 场景召回 ==")
# X32 一条到位；WING 两段式（$actionidx 选号 → $action GO）。形状不同但都在
# profile 里，调用方（actions / oscsrv）只认 scene_msgs()，不写 if id=="wing"。
check("X32 场景召回是单条 /scene/N/load",
      X.scene_msgs(7) == [("/scene/7/load", [])], str(X.scene_msgs(7)))
check("X32 场景号不补零（/scene/2/load）",
      X.scene_msgs(2)[0][0] == "/scene/2/load", X.scene_msgs(2)[0][0])
check("WING 场景召回是两段式（先选号再 GO）",
      W.scene_msgs(7) == [("/$ctl/lib/$actionidx", [7]),
                          ("/$ctl/lib/$action", ["GO"])], str(W.scene_msgs(7)))
check("WING 不会发 X32 的 /scene/N/load",
      all(not a.startswith("/scene/") for a, _ in W.scene_msgs(7)))
check("WING 场景号原样传（0 是 NEXT/PREV 的起点，不做 ±1）",
      W.scene_msgs(1)[0][1] == [1] and W.scene_msgs(99)[0][1] == [99])

# FX 返回组：WING 的 FX 是插入式，没有 fxrtn 组 —— 必须显式缺席而不是翻译
check("WING 的 fxret 规模为 0（没有 FX 返回组）", W.counts.get("fxret") == 0,
      str(W.counts.get("fxret")))
check("WING 的 fxret 参数全部无地址",
      all(W.addr(k, 1) is None for k in ("fxret_fader", "fxret_on", "fxret_pan")))

# ============================================================ G 效果参数可写性
print("\n== G /api/fxcaps 载荷（App 端契约）==")
# App（iOS / Android）是 OSC 直连，拿不到 /api/state 里每个参数的 w 字段，
# 进 FX 页时单独拉一次 /api/fxcaps。这里的形状是两端解析器的前提：
#   {"console":"wing","all":false,"slots":{"1":[1,2], "2":[]}}
# App 用 Int(key) 把槽号转回来，用 par 号过滤滑杆。改了这里两端就得跟着改。
import json as _json  # noqa: E402

from x32sim.state import STATE  # noqa: E402


def _caps_body(prof):
    caps = prof.fx_param_caps()
    return _json.loads(_json.dumps(
        {"console": prof.id, "all": caps is None, "slots": caps or {}}, allow_nan=False))


check("X32 的载荷 all=true（没有未标定概念）", _caps_body(X)["all"] is True)
check("X32 的载荷不列槽", _caps_body(X)["slots"] == {})
check("X32 的 fx_param_caps 返回 None（语义: 无此概念）", X.fx_param_caps() is None)

# 摆一个确定的台面再断言：FX1=ROOM / FX2=HALL / 其余空槽
_old_fx = {k: dict(v or {}) for k, v in STATE.fx.items()}
try:
    STATE.fx.clear()
    STATE.fx[1] = {"type": "ROOM"}
    STATE.fx[2] = {"type": "HALL"}
    _caps = W.fx_param_caps()
    _body = _caps_body(W)
finally:
    STATE.fx.clear()
    STATE.fx.update(_old_fx)

check("WING 的载荷 all=false", _body["all"] is False)
check("WING 的载荷带控台 id", _body["console"] == "wing")
check("载荷覆盖全部 %d 个槽" % int(W.fx_slots), len(_body["slots"]) == int(W.fx_slots),
      str(len(_body["slots"])))
check("槽键在 JSON 里是字符串（App 端 Int(key) 要能转回来）",
      all(isinstance(k, str) for k in _body["slots"]), str(list(_body["slots"])[:3]))
# ROOM 在 WING 上有 15 项参数有地址，缺 par06（= level，真台没有这个叶子）
check("ROOM 槽可写 15 项且缺 par06",
      _caps[1] == [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16], str(_caps[1]))
# HALL 只有预延迟(par01)/混响时间(par02) 有证据，其余叶子名未核实 → 宁可不给
check("HALL 槽只有 pdel/dcy 两项", _caps[2] == [1, 2], str(_caps[2]))
check("空槽一项都不能写（空清单 ≠ 缺键）", _caps[3] == [], str(_caps[3]))
check("可写清单全是正整数",
      all(isinstance(p, int) and p >= 1 for ps in _caps.values() for p in ps))

# ============================================================ 收尾
print()
print("%d/%d 通过" % (_ok[0], _ok[1]))
sys.exit(0 if _ok[0] == _ok[1] else 1)
