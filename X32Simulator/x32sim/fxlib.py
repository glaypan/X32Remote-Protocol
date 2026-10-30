# -*- coding: utf-8 -*-
"""x32sim/fxlib — X32/M32 效果库全量标定表（61 种算法）。

数据来源（权威）：
  * Patrick-Gilles Maillot《UNOFFICIAL X32/M32 OSC REMOTE PROTOCOL》
    Effects Parameters 章（文档第 59–86 页）——逐算法列出 par 号顺序、
    参数名、类型（linf/logf/enum）与量程；附录给出 61 个算法枚举名。
  * 类型规则（同文档第 8 页）：linf/logf 都是**单个浮点实际值**
    （如 2000 = 2 kHz），enum 是选项列表的整数索引。

槽位约束（同文档 /fx 节）：
  * FX 1–4：61 种算法全可用（heavy=True 的 27 种只能进这四槽）；
  * FX 5–8：仅 34 种轻量算法（DSP 算力限制，混响/延迟类进不去）。
  ⚠️ 该约束与枚举号分组以文档为准，未经真机核对 —— 见 calibration 机制。

参数地址即 OSC 真台地址：`/fx/<槽>/par/<两位补零参数号>`，槽号不补零。
field 名是内部/API 用的稳定标识（由文档英文名规范化而来），UI 显示用中文 label。

⚠️ 与旧标定（v1.5.x 及以前）的差异 —— 旧表现在被证实**不准确**（缺真机验证）：
  * HALL 等混响的 par01 是 **Pre Delay**，par02 才是 **Decay**（旧表把
    time=1/predelay=2 搞反了，桥接真台时会写错参数）；
  * Damping 在真台是 **1k–20k Hz 频率**（旧表当成 0–1 归一化值）；
  * 旧表只有 5 种混响、每种 3 个参数；本表 61 种算法全部参数。
  迁移：持久化按 par 号存值，不迁移数据；type 名 CHAMBER→CHAM /
  VINTAGE→VREV / PLATE→PLAT 由 persist 层的别名表转换。
"""

# ---------------------------------------------------------------- 工具

def _slug(name: str) -> str:
    """文档英文参数名 → 稳定 field 标识。"""
    s = "".join(c if (c.isalnum()) else "_" for c in name.lower())
    while "__" in s:
        s = s.replace("__", "_")
    s = s.strip("_")
    if s and s[0].isdigit():
        s = "n" + s
    return s or "par"


# 少数需要手工定名的 field（自动 slug 有歧义或不宜作 API 名时）
_FIELD_OVERRIDE = {
    "4_pole": "pole4",
    "env_modulation": "env_mod",
    "hi_shv_gain": "hi_shelf_gain",
    "lo_multiply": "lo_multi",
    "hi_multiply": "hi_multi",
    "xover_slope": "xover_slope",
    "sbc_on": "sbc_on",
    "eq_level": "eq_level",
}


def _f(field, label, unit, lo, hi):
    """数值参数（linf/logf 都按实际值存取，UI 侧再决定滑杆刻度）。"""
    return (field, label, unit, "f", float(lo), float(hi), None)


def _i(field, label, opts):
    """枚举参数：opts 顺序即真台 enum 索引。"""
    return (field, label, "", "i", 0.0, float(len(opts) - 1), list(opts))


def _A(label, cat, heavy, params, zh=None):
    """一个算法条目。params 顺序 = par 号顺序（par01 起）。"""
    out = []
    for n, p in enumerate(params, 1):
        field = _FIELD_OVERRIDE.get(p[0], p[0])
        kind = p[3]
        out.append({"p": n, "field": field, "label": p[1], "unit": p[2],
                    "kind": kind, "lo": p[4], "hi": p[5], "opts": p[6]})
    fields = [x["field"] for x in out]
    assert len(set(fields)) == len(fields), "field 重名: %s" % label
    d = {"label": zh or label, "cat": cat, "heavy": bool(heavy),
         "params": out, "n": len(out)}
    # 语义别名（兼容旧结构与 Tap/同步组逻辑）：time=衰减, predelay=预延迟, damp=阻尼
    for f, alias in (("decay", "time"), ("time", "time"),
                     ("pre_delay", "predelay"), ("reverb_delay", "predelay"),
                     ("damping", "damp")):
        if f in fields:
            d[alias] = fields.index(f) + 1
    if "time" in d:
        tp = out[d["time"] - 1]
        d["tmin"], d["tmax"], d["tunit"] = tp["lo"], tp["hi"], tp["unit"]
    return d


# 31 段图示 EQ 的 ISO 标准中心频率（仅作 UI 频段标签，不经 OSC 发送）
GEQ_BANDS_HZ = [20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250,
                315, 400, 500, 630, 800, 1000, 1250, 1600, 2000, 2500,
                3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000]

_FACTOR = ["1/4", "3/8", "1/2", "2/3", "1", "4/3", "3/2", "2", "3"]
_PATTERN = ["1/4", "1/3", "3/8", "1/2", "2/3", "3/4", "1",
            "1/4X", "1/3X", "3/8X", "1/2X", "2/3X", "3/4X", "1X"]
_ONOFF = ["OFF", "ON"]
_BANDSOLO = ["OFF", "Bd1", "Bd2", "Bd3", "Bd4", "Bd5"]
_RATIO = ["1.1", "1.2", "1.3", "1.5", "1.7", "2", "2.5", "3", "3.5",
          "4", "5", "7", "10", "LIM"]


def _geq(prefix, count):
    """GEQ/TEQ 的 31 段 + 主电平参数。"""
    out = []
    for i, hz in enumerate(GEQ_BANDS_HZ[:count], 1):
        lab = ("%g" % hz) if hz < 1000 else ("%gk" % (hz / 1000.0))
        out.append(_f("%s_%02d" % (prefix, i), lab + " Hz", "dB", -15, 15))
    out.append(_f("%s_master" % prefix, "总电平", "dB", -15, 15))
    return out


def _dual(prefix, base):
    """双引擎算法（A/B 两套）的参数拼接。"""
    out = []
    for side in ("a", "b"):
        for p in base:
            out.append((p[0] + "_" + side, p[1] + " " + side.upper(),
                        p[2], p[3], p[4], p[5], p[6]))
    return out


# ---------------------------------------------------------------- 标定表
# heavy=True → 只能进 FX 1–4；False → 8 槽均可。

_RAW = {

# ---- 混响 (10) ----
"HALL": _A("大厅混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.2, 5),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("bass_multi", "低频倍增", "", 0.5, 2),
    _f("spread", "立体声展开", "", 0, 50),
    _f("shape", "包络形状", "", 0, 250),
    _f("mod_speed", "调制速度", "", 0, 100),
]),
"PLAT": _A("板式混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.2, 10),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("bass_multi", "低频倍增", "", 0.5, 2),
    _f("xover", "分频点", "Hz", 10, 500),
    _f("mod", "调制量", "", 0, 50),
    _f("mod_speed", "调制速度", "", 0, 100),
]),
"AMBI": _A("氛围混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.2, 7.3),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("modulate", "调制", "", 0, 100),
    _f("tail_gain", "尾音增益", "", 0, 100),
]),
"RPLT": _A("Rich 板式混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.3, 29),
    _f("size", "空间大小", "", 4, 39),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("bass_multi", "低频倍增", "", 0.25, 4),
    _f("spread", "立体声展开", "", 0, 50),
    _f("attack", "启动", "", 0, 100),
    _f("spin", "旋转", "", 0, 100),
    _f("echo_l", "回声 L", "ms", 0, 1200),
    _f("echo_r", "回声 R", "ms", 0, 1200),
    _f("echo_feed_l", "回声反馈 L", "", -100, 100),
    _f("echo_feed_r", "回声反馈 R", "", -100, 100),
]),
"ROOM": _A("房间混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.3, 29),
    _f("size", "空间大小", "", 4, 72),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("bass_multi", "低频倍增", "", 0.25, 4),
    _f("spread", "立体声展开", "", 0, 50),
    _f("shape", "包络形状", "", 0, 250),
    _f("spin", "旋转", "", 0, 100),
    _f("echo_l", "回声 L", "ms", 0, 1200),
    _f("echo_r", "回声 R", "ms", 0, 1200),
    _f("echo_feed_l", "回声反馈 L", "", -100, 100),
    _f("echo_feed_r", "回声反馈 R", "", -100, 100),
]),
"CHAM": _A("密室混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.3, 29),
    _f("size", "空间大小", "", 4, 72),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("diffuse", "扩散", "", 1, 30),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("bass_multi", "低频倍增", "", 0.25, 4),
    _f("spread", "立体声展开", "", 0, 50),
    _f("shape", "包络形状", "", 0, 250),
    _f("spin", "旋转", "", 0, 100),
    _f("reflection_l", "早期反射 L", "ms", 0, 500),
    _f("reflection_r", "早期反射 R", "ms", 0, 500),
    _f("reflection_gain_l", "反射增益 L", "", 0, 100),
    _f("reflection_gain_r", "反射增益 R", "", 0, 100),
]),
"VREV": _A("复古混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 120),
    _f("decay", "混响时间", "s", 0.3, 4.5),
    _f("modulate", "调制", "", 0, 10),
    _i("vintage", "复古模式", _ONOFF),
    _i("position", "位置", ["FRONT", "REAR"]),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("lo_multi", "低频倍增", "", 0.5, 2),
    _f("hi_multi", "高频倍增", "", 0.25, 1),
]),
"VRM": _A("复古房间", "reverb", True, [
    _f("reverb_delay", "混响延迟", "ms", 0, 20),
    _f("decay", "混响时间", "s", 0.1, 20),
    _f("size", "空间大小", "", 0, 10),
    _f("density", "密度", "", 1, 30),
    _f("er_level", "早期反射电平", "", 0, 190),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_multi", "低频倍增", "", 0.1, 10),
    _f("hi_multi", "高频倍增", "", 0.1, 10),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("er_left", "早期反射 L", "", 0, 10),
    _f("er_right", "早期反射 R", "", 0, 10),
    _i("freeze", "冻结", _ONOFF),
]),
"GATE": _A("门混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "ms", 140, 1000),
    _f("attack", "启动", "", 0, 30),
    _f("density", "密度", "", 1, 30),
    _f("spread", "立体声展开", "", 0, 100),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("hi_shelf_gain", "高频增益", "dB", -30, 0),
    _f("diffuse", "扩散", "", 1, 30),
]),
"RVRS": _A("反向混响", "reverb", True, [
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "ms", 140, 1000),
    _f("rise", "上升", "", 0, 50),
    _f("diffuse", "扩散", "", 1, 30),
    _f("spread", "立体声展开", "", 1, 100),
    _f("level", "电平", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("hi_shelf_gain", "高频增益", "dB", -30, 0),
]),

# ---- 延迟 (3) ----
"DLY": _A("立体声延迟", "delay", True, [
    _f("mix", "混合", "%", 0, 100),
    _f("time", "延迟时间", "ms", 0, 3000),
    _i("mode", "模式", ["ST", "X", "M"]),
    _i("factor_l", "左声道因数", _FACTOR),
    _i("factor_r", "右声道因数", _FACTOR),
    _f("offset_lr", "左右偏移", "", -100, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("feed_lo_cut", "反馈低切", "Hz", 10, 500),
    _f("feed_left", "左反馈", "%", 1, 100),
    _f("feed_right", "右反馈", "%", 1, 100),
    _f("feed_hi_cut", "反馈高切", "Hz", 200, 20000),
]),
"3TAP": _A("3 段延迟", "delay", True, [
    _f("dry", "干声", "ms", 0, 3000),
    _f("gain_base", "基础增益", "%", 0, 100),
    _f("pan_base", "基础声像", "", -100, 100),
    _f("feedback", "反馈量", "%", 0, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _i("factor_a", "A 段因数", _FACTOR),
    _f("gain_a", "A 段增益", "%", 0, 100),
    _f("pan_a", "A 段声像", "", -100, 100),
    _i("factor_b", "B 段因数", _FACTOR),
    _f("gain_b", "B 段增益", "%", 0, 100),
    _f("pan_b", "B 段声像", "", -100, 100),
    _i("cross_feed", "交叉反馈", _ONOFF),
    _i("mono", "单声道", _ONOFF),
    _i("dry_on", "干声开关", _ONOFF),
]),
"4TAP": _A("节奏延迟", "delay", True, [
    _f("time", "延迟时间", "ms", 1, 3000),
    _f("gain_base", "基础增益", "%", 0, 100),
    _f("feedback", "反馈量", "%", 0, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("spread", "立体声展开", "", 0, 6),
    _i("factor_a", "A 段因数", _FACTOR),
    _f("gain_a", "A 段增益", "%", 0, 100),
    _i("factor_b", "B 段因数", _FACTOR),
    _f("gain_b", "B 段增益", "%", 0, 100),
    _i("factor_c", "C 段因数", _FACTOR),
    _f("gain_c", "C 段增益", "%", 0, 100),
    _i("cross_feed", "交叉反馈", _ONOFF),
    _i("mono", "单声道", _ONOFF),
    _i("dry_on", "干声开关", _ONOFF),
]),

# ---- 组合效果 (6) ----
"D/RV": _A("延迟+密室", "combo", True, [
    _f("time", "延迟时间", "ms", 1, 3000),
    _i("pattern", "模式", _PATTERN),
    _f("feed_hi_cut", "反馈高切", "Hz", 1000, 20000),
    _f("feedback", "反馈量", "%", 0, 100),
    _f("cross_feed", "交叉反馈", "%", 0, 100),
    _f("balance", "延迟/混响平衡", "", -100, 100),
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.1, 5),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("mix", "混合", "%", 0, 100),
]),
"D/CR": _A("延迟+合唱", "combo", True, [
    _f("time", "延迟时间", "ms", 1, 3000),
    _i("pattern", "模式", _PATTERN),
    _f("feed_hi_cut", "反馈高切", "Hz", 1000, 20000),
    _f("feedback", "反馈量", "%", 0, 100),
    _f("cross_feed", "交叉反馈", "%", 0, 100),
    _f("balance", "延迟/合唱平衡", "", -100, 100),
    _f("speed", "速度", "Hz", 0.05, 4),
    _f("depth", "深度", "%", 0, 100),
    _f("delay", "延迟", "ms", 0.5, 50),
    _f("phase", "相位", "°", 0, 180),
    _f("wave", "波形", "%", 0, 100),
    _f("mix", "混合", "%", 0, 100),
]),
"D/FL": _A("延迟+镶边", "combo", True, [
    _f("time", "延迟时间", "ms", 1, 3000),
    _i("pattern", "模式", _PATTERN),
    _f("feed_hi_cut", "反馈高切", "Hz", 1000, 20000),
    _f("feedback", "反馈量", "%", 0, 100),
    _f("cross_feed", "交叉反馈", "%", 0, 100),
    _f("balance", "延迟/镶边平衡", "", -100, 100),
    _f("speed", "速度", "Hz", 0.05, 4),
    _f("depth", "深度", "%", 0, 100),
    _f("delay", "延迟", "ms", 0.5, 20),
    _f("phase", "相位", "°", 0, 180),
    _f("feed", "反馈", "", -90, 90),
    _f("mix", "混合", "%", 0, 100),
]),
"CR/R": _A("合唱+密室", "combo", True, [
    _f("speed", "速度", "Hz", 0.05, 4),
    _f("depth", "深度", "%", 0, 100),
    _f("delay", "延迟", "ms", 0.5, 50),
    _f("phase", "相位", "°", 0, 180),
    _f("wave", "波形", "%", 0, 100),
    _f("balance", "合唱/混响平衡", "", -100, 100),
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.1, 5),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("mix", "混合", "%", 0, 100),
]),
"FL/R": _A("镶边+密室", "combo", True, [
    _f("speed", "速度", "Hz", 0.05, 4),
    _f("depth", "深度", "%", 0, 100),
    _f("delay", "延迟", "ms", 0.5, 20),
    _f("phase", "相位", "°", 0, 180),
    _f("feed", "反馈", "", -90, 90),
    _f("balance", "镶边/混响平衡", "", -100, 100),
    _f("pre_delay", "预延迟", "ms", 0, 200),
    _f("decay", "混响时间", "s", 0.1, 5),
    _f("size", "空间大小", "", 2, 100),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("mix", "混合", "%", 0, 100),
]),
"MODD": _A("调制延迟", "combo", True, [
    _f("time", "延迟时间", "ms", 1, 3000),
    _i("delay", "延迟类型", ["1", "1/2", "2/3", "3/2"]),
    _f("feed", "反馈", "%", 0, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("depth_rate", "调制深度速率", "%", 0, 100),
    _f("rate", "速率", "Hz", 0.05, 10),
    _i("setup", "结构", ["PAR", "SER"]),
    _i("type", "混响类型", ["AMB", "CLUB", "HALL"]),
    _f("decay", "混响时间", "s", 1, 10),
    _f("damping", "阻尼", "Hz", 1000, 20000),
    _f("balance", "平衡", "", -100, 100),
    _f("mix", "混合", "%", 0, 100),
]),

# ---- 调制 (7) ----
"CRS": _A("立体声合唱", "mod", True, [
    _f("speed", "速度", "Hz", 0.05, 5),
    _f("depth_l", "左深度", "%", 0, 100),
    _f("depth_r", "右深度", "%", 0, 100),
    _f("delay_l", "左延迟", "ms", 0.5, 20),
    _f("delay_r", "右延迟", "ms", 0.5, 20),
    _f("mix", "混合", "%", 0, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("phase", "相位", "°", 0, 180),
    _f("wave", "波形", "%", 0, 100),
    _f("spread", "立体声展开", "%", 0, 100),
]),
"FLNG": _A("立体声镶边", "mod", True, [
    _f("speed", "速度", "Hz", 0.05, 5),
    _f("depth_l", "左深度", "%", 0, 100),
    _f("depth_r", "右深度", "%", 0, 100),
    _f("delay_l", "左延迟", "ms", 0.5, 20),
    _f("delay_r", "右延迟", "ms", 0.5, 20),
    _f("mix", "混合", "%", 0, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 200, 20000),
    _f("phase", "相位", "°", 0, 180),
    _f("feed_lo_cut", "反馈低切", "Hz", 10, 500),
    _f("feed_hi_cut", "反馈高切", "Hz", 200, 20000),
    _f("feed", "反馈", "", -90, 90),
]),
"PHAS": _A("立体声移相", "mod", False, [
    _f("speed", "速度", "Hz", 0.05, 5),
    _f("depth", "深度", "%", 0, 100),
    _f("resonance", "谐振", "", 0, 80),
    _f("base", "基频", "", 0, 50),
    _f("stages", "级数", "", 2, 12),
    _f("mix", "混合", "%", 0, 100),
    _f("wave", "波形", "", -50, 50),
    _f("phase", "相位", "°", 0, 180),
    _f("env_mod", "包络调制", "", -100, 100),
    _f("attack", "启动", "ms", 10, 1000),
    _f("hold", "保持", "ms", 1, 2000),
    _f("release", "释放", "ms", 10, 1000),
]),
"DIMC": _A("Dimension-C 合唱", "mod", True, [
    _i("active", "启用", _ONOFF),
    _i("mode", "模式", ["M", "ST"]),
    _i("dry", "干声", _ONOFF),
    _i("mode_1", "模式 1", _ONOFF),
    _i("mode_2", "模式 2", _ONOFF),
    _i("mode_3", "模式 3", _ONOFF),
    _i("mode_4", "模式 4", _ONOFF),
]),
"FILT": _A("情绪滤波", "mod", False, [
    _f("speed", "速度", "Hz", 0.05, 20),
    _f("depth", "深度", "%", 0, 100),
    _f("resonance", "谐振", "", 0, 100),
    _f("base", "基频", "Hz", 10, 15000),
    _i("mode", "模式", ["LP", "HP", "BP", "NO"]),
    _f("mix", "混合", "%", 0, 100),
    _i("wave", "波形", ["TRI", "SIN", "SAW", "SAW-", "RMP", "SQU/RND"]),
    _f("phase", "相位", "°", 0, 180),
    _f("env_mod", "包络调制", "", -100, 100),
    _f("attack", "启动", "ms", 10, 250),
    _f("release", "释放", "ms", 10, 500),
    _f("drive", "驱动", "%", 0, 100),
    _i("pole4", "4 极点", _ONOFF),
    _i("side_chain", "侧链", _ONOFF),
]),
"ROTA": _A("旋转音箱", "mod", True, [
    _f("lo_speed", "低速", "Hz", 0.1, 4),
    _f("hi_speed", "高速", "Hz", 2, 10),
    _f("accelerate", "加速", "", 0, 100),
    _f("distance", "距离", "", 0, 100),
    _f("balance", "平衡", "", -100, 100),
    _f("mix", "混合", "%", 0, 100),
    _i("stop", "停止", _ONOFF),
    _i("slow", "慢速", _ONOFF),
]),
"PAN": _A("颤音/声像", "mod", False, [
    _f("speed", "速度", "Hz", 0.05, 4),
    _f("phase", "相位", "°", 0, 180),
    _f("wave", "波形", "", -50, 50),
    _f("depth", "深度", "%", 0, 100),
    _f("env_speed", "包络速度", "", 0, 100),
    _f("env_depth", "包络深度", "", 0, 100),
    _f("attack", "启动", "ms", 10, 1000),
    _f("hold", "保持", "ms", 1, 2000),
    _f("release", "释放", "ms", 10, 1000),
]),

# ---- 变调 (3) ----
"SUB": _A("低八度", "pitch", False, [
    _i("active", "启用", _ONOFF),
    _i("range", "范围", ["LO", "MID", "HI"]),
    _f("dry", "干声", "%", 0, 100),
    _f("octave_1", "低八度 -1", "%", 0, 100),
    _f("octave_2", "低八度 -2", "%", 0, 100),
]),
"PIT": _A("立体声变调", "pitch", True, [
    _f("semitone", "半音", "", -12, 12),
    _f("cent", "音分", "", -50, 50),
    _f("delay", "延迟", "ms", 1, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 2000, 20000),
    _f("mix", "混合", "%", 0, 100),
]),
"PIT2": _A("双声道变调", "pitch", True, _dual("", [
    _f("semitone", "半音", "", -12, 12),
    _f("cent", "音分", "", -50, 50),
    _f("delay", "延迟", "ms", 1, 100),
    _f("lo_cut", "低切", "Hz", 10, 500),
    _f("hi_cut", "高切", "Hz", 2000, 20000),
    _f("mix", "混合", "%", 0, 100),
])),

# ---- 均衡 (8) ----
"GEQ2": _A("双通道图示 EQ", "eq", False,
           _geq("eq_a", 31) + _geq("eq_b", 31)),
"GEQ": _A("立体声图示 EQ", "eq", False, _geq("eq_lr", 31)),
"TEQ2": _A("双通道 True EQ", "eq", False,
           _geq("eq_a", 31) + _geq("eq_b", 31)),
"TEQ": _A("立体声 True EQ", "eq", False, _geq("eq_lr", 31)),
"P1A": _A("立体声 Program EQ", "eq", False, [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "dB", -12, 12),
    _f("lo_boost", "低频提升", "", 0, 10),
    _i("lo_freq", "低频频率", ["0", "30", "60", "100"]),
    _f("mid_width", "中频宽度", "", 0, 10),
    _f("mid_boost", "中频提升", "", 0, 10),
    _i("mid_freq", "中频频率", ["3k", "4k", "5k", "8k", "10k", "12k", "16k"]),
    _f("hi_attenuation", "高频衰减", "", 0, 10),
    _i("hi_freq", "高频频率", ["5k", "10k", "20k"]),
    _i("transformer", "变压器", _ONOFF),
]),
"P1A2": _A("双通道 Program EQ", "eq", False, _dual("", [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "dB", -12, 12),
    _f("lo_boost", "低频提升", "", 0, 10),
    _i("lo_freq", "低频频率", ["0", "30", "60", "100"]),
    _f("mid_width", "中频宽度", "", 0, 10),
    _f("mid_boost", "中频提升", "", 0, 10),
    _i("mid_freq", "中频频率", ["3k", "4k", "5k", "8k", "10k", "12k", "16k"]),
    _f("hi_attenuation", "高频衰减", "", 0, 10),
    _i("hi_freq", "高频频率", ["5k", "10k", "20k"]),
    _i("transformer", "变压器", _ONOFF),
])),
"PQ5": _A("立体声 Midrange EQ", "eq", False, [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "dB", -12, 12),
    _i("lo_freq", "低频频率", ["200", "300", "500", "700", "1000"]),
    _f("lo_boost", "低频提升", "", 0, 10),
    _i("mid_freq", "中频频率", ["200", "300", "500", "700", "1k", "1k5",
                               "2k", "3k", "4k", "5k", "7k"]),
    _f("mid_boost", "中频提升", "", 0, 10),
    _i("hi_freq", "高频频率", ["1k5", "2k", "3k", "4k", "5k"]),
    _f("hi_boost", "高频提升", "", 0, 10),
    _i("transformer", "变压器", _ONOFF),
]),
"PQ5S": _A("双通道 Midrange EQ", "eq", False, _dual("", [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "dB", -12, 12),
    _i("lo_freq", "低频频率", ["200", "300", "500", "700", "1000"]),
    _f("lo_boost", "低频提升", "", 0, 10),
    _i("mid_freq", "中频频率", ["200", "300", "500", "700", "1k", "1k5",
                               "2k", "3k", "4k", "5k", "7k"]),
    _f("mid_boost", "中频提升", "", 0, 10),
    _i("hi_freq", "高频频率", ["1k5", "2k", "3k", "4k", "5k"]),
    _f("hi_boost", "高频提升", "", 0, 10),
    _i("transformer", "变压器", _ONOFF),
])),

# ---- 动态 (13) ----
"DES": _A("立体声去咝声", "dyn", False, [
    _f("lo_band_l", "左低段", "", 0, 50),
    _f("hi_band_l", "左高段", "", 0, 50),
    _f("lo_band_r", "右低段", "", 0, 50),
    _f("hi_band_r", "右高段", "", 0, 50),
    _i("voice", "人声", ["FEM", "MALE"]),
    _i("mode", "模式", ["ST", "M/S"]),
]),
"DES2": _A("双通道去咝声", "dyn", False, [
    _f("lo_band_a", "A 低段", "", 0, 50),
    _f("hi_band_a", "A 高段", "", 0, 50),
    _f("lo_band_b", "B 低段", "", 0, 50),
    _f("hi_band_b", "B 高段", "", 0, 50),
    _i("voice_a", "A 人声", ["FEM", "MALE"]),
    _i("voice_b", "B 人声", ["FEM", "MALE"]),
]),
"LIM": _A("精密限幅器", "dyn", False, [
    _f("input_gain", "输入增益", "dB", 0, 18),
    _f("out_gain", "输出增益", "dB", -18, 18),
    _f("squeeze", "压缩", "", 0, 100),
    _f("knee", "膝宽", "", 0, 10),
    _f("attack", "启动", "ms", 0.05, 1),
    _f("release", "释放", "ms", 20, 2000),
    _i("stereo_link", "立体声链接", _ONOFF),
    _i("auto_gain", "自动增益", _ONOFF),
]),
"CMB": _A("立体声 Combinator", "dyn", True, [
    _i("active", "启用", _ONOFF),
    _i("band_solo", "频段独奏", _BANDSOLO),
    _f("mix", "混合", "%", 0, 100),
    _f("attack", "启动", "", 0, 19),
    _f("release", "释放", "ms", 20, 3000),
    _i("autorelease", "自动释放", _ONOFF),
    _f("sbc_speed", "SBC 速度", "", 0, 10),
    _i("sbc_on", "SBC 开关", _ONOFF),
    _f("xover", "分频点", "", -50, 50),
    _i("xover_slope", "分频斜率", ["12", "48"]),
    _i("ratio", "比率", _RATIO),
    _f("threshold", "阈值", "dB", -40, 0),
    _f("gain", "增益", "dB", -10, 10),
    _f("band1_threshold", "频段1 阈值", "dB", -10, 10),
    _f("band1_gain", "频段1 增益", "dB", -10, 10),
    _i("band1_lock", "频段1 锁定", ["0", "1"]),
    _f("band2_threshold", "频段2 阈值", "dB", -10, 10),
    _f("band2_gain", "频段2 增益", "dB", -10, 10),
    _i("band2_lock", "频段2 锁定", ["0", "1"]),
    _f("band3_threshold", "频段3 阈值", "dB", -10, 10),
    _f("band3_gain", "频段3 增益", "dB", -10, 10),
    _i("band3_lock", "频段3 锁定", ["0", "1"]),
    _f("band4_threshold", "频段4 阈值", "dB", -10, 10),
    _f("band4_gain", "频段4 增益", "dB", -10, 10),
    _i("band4_lock", "频段4 锁定", ["0", "1"]),
    _f("band5_threshold", "频段5 阈值", "dB", -10, 10),
    _f("band5_gain", "频段5 增益", "dB", -10, 10),
    _i("band5_lock", "频段5 锁定", ["0", "1"]),
    _i("meter_mode", "表头模式", ["GR", "SBC", "PEAK"]),
]),
"CMB2": _A("双通道 Combinator", "dyn", True, _dual("", [
    _i("active", "启用", _ONOFF),
    _i("band_solo", "频段独奏", _BANDSOLO),
    _f("mix", "混合", "%", 0, 100),
    _f("attack", "启动", "", 0, 19),
    _f("release", "释放", "ms", 20, 3000),
    _i("autorelease", "自动释放", _ONOFF),
    _f("sbc_speed", "SBC 速度", "", 0, 10),
    _i("sbc_on", "SBC 开关", _ONOFF),
    _f("xover", "分频点", "", -50, 50),
    _i("xover_slope", "分频斜率", ["12", "48"]),
    _i("ratio", "比率", _RATIO),
    _f("threshold", "阈值", "dB", -40, 0),
    _f("gain", "增益", "dB", -10, 10),
    _f("band1_threshold", "频段1 阈值", "dB", -10, 10),
    _f("band1_gain", "频段1 增益", "dB", -10, 10),
    _i("band1_lock", "频段1 锁定", ["0", "1"]),
    _f("band2_threshold", "频段2 阈值", "dB", -10, 10),
    _f("band2_gain", "频段2 增益", "dB", -10, 10),
    _i("band2_lock", "频段2 锁定", ["0", "1"]),
    _f("band3_threshold", "频段3 阈值", "dB", -10, 10),
    _f("band3_gain", "频段3 增益", "dB", -10, 10),
    _i("band3_lock", "频段3 锁定", ["0", "1"]),
    _f("band4_threshold", "频段4 阈值", "dB", -10, 10),
    _f("band4_gain", "频段4 增益", "dB", -10, 10),
    _i("band4_lock", "频段4 锁定", ["0", "1"]),
    _f("band5_threshold", "频段5 阈值", "dB", -10, 10),
    _f("band5_gain", "频段5 增益", "dB", -10, 10),
    _i("band5_lock", "频段5 锁定", ["0", "1"]),
    _i("meter_mode", "表头模式", ["GR", "SBC", "PEAK"]),
])),
"FAC": _A("立体声 Fair 压缩", "dyn", False, [
    _i("active", "启用", _ONOFF),
    _f("input_gain", "输入增益", "dB", -20, 20),
    _f("threshold", "阈值", "", 0, 10),
    _f("time", "时间", "", 0, 6),
    _f("bias", "偏置", "", 0, 100),
    _f("gain", "增益", "dB", -18, 6),
    _f("balance", "平衡", "", -100, 100),
]),
"FAC1M": _A("M/S Fair 压缩", "dyn", False, [
    _i("active", "启用", _ONOFF),
    _f("input_gain", "输入增益", "dB", -20, 20),
    _f("threshold", "阈值", "", 0, 10),
    _f("time", "时间", "", 0, 6),
    _f("bias", "偏置", "", 0, 100),
    _f("gain", "增益", "dB", -18, 6),
    _f("balance", "平衡", "", -100, 100),
]),
"FAC2": _A("双通道 Fair 压缩", "dyn", False, _dual("", [
    _i("active", "启用", _ONOFF),
    _f("input_gain", "输入增益", "dB", -20, 20),
    _f("threshold", "阈值", "", 0, 10),
    _f("time", "时间", "", 0, 6),
    _f("bias", "偏置", "", 0, 100),
    _f("gain", "增益", "dB", -18, 6),
    _f("balance", "平衡", "", -100, 100),
])),
"LEC": _A("立体声 Leisure 压缩", "dyn", False, [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "", 0, 100),
    _f("peak", "峰值", "", 0, 100),
    _i("mode", "模式", ["COMP", "LIM"]),
    _f("out_gain", "输出增益", "dB", -18, 6),
]),
"LEC2": _A("双通道 Leisure 压缩", "dyn", False, _dual("", [
    _i("active", "启用", _ONOFF),
    _f("gain", "增益", "", 0, 100),
    _f("peak", "峰值", "", 0, 100),
    _i("mode", "模式", ["COMP", "LIM"]),
    _f("out_gain", "输出增益", "dB", -18, 6),
])),
"ULC": _A("立体声 Ultimo 压缩", "dyn", False, [
    _i("active", "启用", _ONOFF),
    _f("input_gain", "输入增益", "dB", -48, 0),
    _f("out_gain", "输出增益", "dB", -48, 0),
    _f("attack", "启动", "", 1, 7),
    _f("release", "释放", "", 1, 7),
    _i("ratio", "比率", ["4", "8", "12", "20", "ALL"]),
]),
"ULC2": _A("双通道 Ultimo 压缩", "dyn", False, _dual("", [
    _i("active", "启用", _ONOFF),
    _f("input_gain", "输入增益", "dB", -48, 0),
    _f("out_gain", "输出增益", "dB", -48, 0),
    _f("attack", "启动", "", 1, 7),
    _f("release", "释放", "", 1, 7),
    _i("ratio", "比率", ["4", "8", "12", "20", "ALL"]),
])),
"WAVD": _A("波形设计器", "dyn", False, [
    _f("attack_a", "A 启动", "", -100, 100),
    _f("sustain_a", "A 延持", "", -100, 100),
    _f("gain_a", "A 增益", "dB", -24, 24),
    _f("attack_b", "B 启动", "", -100, 100),
    _f("sustain_b", "B 延持", "", -100, 100),
    _f("gain_b", "B 增益", "dB", -24, 24),
]),

# ---- 增强/成像 (7) ----
"ENH": _A("立体声增强器", "enh", False, [
    _f("out_gain", "输出增益", "dB", -12, 12),
    _f("speed", "速度", "", 0, 100),
    _f("bass_gain", "低频增益", "", 0, 100),
    _f("bass_freq", "低频频率", "", 1, 50),
    _f("mid_gain", "中频增益", "", 0, 100),
    _f("mid_freq", "中频频率", "", 1, 50),
    _f("hi_gain", "高频增益", "", 0, 100),
    _f("hi_freq", "高频频率", "", 1, 50),
    _i("solo", "独奏", _ONOFF),
]),
"ENH2": _A("双通道增强器", "enh", False, _dual("", [
    _f("out_gain", "输出增益", "dB", -12, 12),
    _f("speed", "速度", "", 0, 100),
    _f("bass_gain", "低频增益", "", 0, 100),
    _f("bass_freq", "低频频率", "", 1, 50),
    _f("mid_gain", "中频增益", "", 0, 100),
    _f("mid_freq", "中频频率", "", 1, 50),
    _f("hi_gain", "高频增益", "", 0, 100),
    _f("hi_freq", "高频频率", "", 1, 50),
    _i("solo", "独奏", _ONOFF),
])),
"EXC": _A("立体声激励器", "enh", False, [
    _f("tune", "调音", "Hz", 1000, 10000),
    _f("peak", "峰值", "", 0, 100),
    _f("zero_fill", "零填充", "", 0, 100),
    _f("timbre", "音色", "", -50, 50),
    _f("harmonics", "泛音", "", 0, 100),
    _f("mix", "混合", "%", 0, 100),
    _i("solo", "独奏", _ONOFF),
]),
"EXC2": _A("双通道激励器", "enh", False, _dual("", [
    _f("tune", "调音", "Hz", 1000, 10000),
    _f("peak", "峰值", "", 0, 100),
    _f("zero_fill", "零填充", "", 0, 100),
    _f("timbre", "音色", "", -50, 50),
    _f("harmonics", "泛音", "", 0, 100),
    _f("mix", "混合", "%", 0, 100),
    _i("solo", "独奏", _ONOFF),
])),
"SON": _A("Sound Maxer", "enh", False, [
    _i("active_a", "A 启用", _ONOFF),
    _f("lo_contour_a", "A 低频轮廓", "", 0, 10),
    _f("process_a", "A 处理", "", 0, 10),
    _f("out_gain_a", "A 输出增益", "dB", -12, 12),
    _i("active_b", "B 启用", _ONOFF),
    _f("lo_contour_b", "B 低频轮廓", "", 0, 10),
    _f("process_b", "B 处理", "", 0, 10),
    _f("out_gain_b", "B 输出增益", "dB", -12, 12),
]),
"IMG": _A("立体声成像", "enh", False, [
    _f("balance", "平衡", "", -100, 100),
    _f("mono_pan", "单声道声像", "", -100, 100),
    _f("stereo_pan", "立体声声像", "", -100, 100),
    _f("shv_gain", "增益", "dB", 0, 12),
    _f("shv_freq", "频率", "Hz", 100, 1000),
    _f("shv_q", "Q 值", "", 1, 10),
    _f("out_gain", "输出增益", "dB", -12, 12),
]),
"EDI": _A("Edison EX1", "enh", False, [
    _i("active", "启用", _ONOFF),
    _i("stereo_input", "立体声输入", ["ST", "M/S"]),
    _i("stereo_output", "立体声输出", ["ST", "M/S"]),
    _f("st_spread", "立体声展开", "", -50, 50),
    _f("lmf_spread", "LMF 展开", "", -50, 50),
    _f("balance", "平衡", "", -50, 50),
    _f("center_distance", "中心距离", "", -50, 50),
    _f("out_gain", "输出增益", "dB", -12, 12),
]),

# ---- 吉他 (4) ----
"AMP": _A("立体声吉他箱头", "amp", False, [
    _f("preamp", "前置放大", "", 0, 10),
    _f("buzz", "噪声", "", 0, 10),
    _f("punch", "冲击", "", 0, 10),
    _f("crunch", "碎裂", "", 0, 10),
    _f("drive", "驱动", "", 0, 10),
    _f("low", "低音", "", 0, 10),
    _f("high", "高音", "", 0, 10),
    _f("level", "电平", "", 0, 10),
    _i("cabinet", "音箱", _ONOFF),
]),
"AMP2": _A("双通道吉他箱头", "amp", False, _dual("", [
    _f("preamp", "前置放大", "", 0, 10),
    _f("buzz", "噪声", "", 0, 10),
    _f("punch", "冲击", "", 0, 10),
    _f("crunch", "碎裂", "", 0, 10),
    _f("drive", "驱动", "", 0, 10),
    _f("low", "低音", "", 0, 10),
    _f("high", "高音", "", 0, 10),
    _f("level", "电平", "", 0, 10),
    _i("cabinet", "音箱", _ONOFF),
])),
"DRV": _A("立体声电子管", "amp", False, [
    _f("drive", "驱动", "", 0, 100),
    _f("even_ear", "偶次谐波", "", 0, 50),
    _f("odd_ear", "奇次谐波", "", 0, 50),
    _f("gain", "增益", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 20, 200),
    _f("hi_cut", "高切", "Hz", 4000, 20000),
    _f("lo_gain", "低频增益", "dB", -12, 12),
    _f("lo_freq", "低频频率", "Hz", 50, 400),
    _f("hi_gain", "高频增益", "dB", -12, 12),
    _f("hi_freq", "高频频率", "Hz", 1000, 10000),
]),
"DRV2": _A("双通道电子管", "amp", False, _dual("", [
    _f("drive", "驱动", "", 0, 100),
    _f("even_ear", "偶次谐波", "", 0, 50),
    _f("odd_ear", "奇次谐波", "", 0, 50),
    _f("gain", "增益", "dB", -12, 12),
    _f("lo_cut", "低切", "Hz", 20, 200),
    _f("hi_cut", "高切", "Hz", 4000, 20000),
    _f("lo_gain", "低频增益", "dB", -12, 12),
    _f("lo_freq", "低频频率", "Hz", 50, 400),
    _f("hi_gain", "高频增益", "dB", -12, 12),
    _f("hi_freq", "高频频率", "Hz", 1000, 10000),
])),
}

# ---------------------------------------------------------------- 派生表

# FX 1–4 的 type 枚举（int → 算法名），来自协议文档附录
FX_ENUM_14 = ["HALL", "AMBI", "RPLT", "ROOM", "CHAM", "PLAT", "VREV", "VRM",
              "GATE", "RVRS", "DLY", "3TAP", "4TAP", "CRS", "FLNG", "PHAS",
              "DIMC", "FILT", "ROTA", "PAN", "SUB", "D/RV", "CR/R", "FL/R",
              "D/CR", "D/FL", "MODD", "GEQ2", "GEQ", "TEQ2", "TEQ", "DES2",
              "DES", "P1A", "P1A2", "PQ5", "PQ5S", "WAVD", "LIM", "CMB",
              "CMB2", "FAC", "FAC1M", "FAC2", "LEC", "LEC2", "ULC", "ULC2",
              "ENH2", "ENH", "EXC2", "EXC", "IMG", "EDI", "SON", "AMP2",
              "AMP", "DRV2", "DRV", "PIT2", "PIT"]

# FX 5–8 的 type 枚举（轻量算法子集，DSP 算力限制）
FX_ENUM_58 = ["GEQ2", "GEQ", "TEQ2", "TEQ", "DES2", "DES", "P1A", "P1A2",
              "PQ5", "PQ5S", "WAVD", "LIM", "FAC", "FAC1M", "FAC2", "LEC",
              "LEC2", "ULC", "ULC2", "ENH2", "ENH", "EXC2", "EXC", "IMG",
              "EDI", "SON", "AMP2", "AMP", "DRV2", "DRV", "PHAS", "FILT",
              "PAN", "SUB"]

# 分类中文标签（UI 下拉分组用）
FX_CAT_LABEL = {"reverb": "混响", "delay": "延迟", "combo": "组合效果",
                "mod": "调制", "pitch": "变调", "eq": "均衡",
                "dyn": "动态", "enh": "增强/成像", "amp": "吉他"}

# 槽位约束：heavy 算法只能进 FX 1–4（文档 /fx 节，待真机核对）
FX_SLOTS_HEAVY = (1, 2, 3, 4)

# 旧 type 名 → 真台枚举名（v1.5.x 及以前的持久化数据迁移用）
FX_TYPE_ALIAS = {"PLATE": "PLAT", "CHAMBER": "CHAM", "VINTAGE": "VREV",
                 "ROOM": "ROOM", "HALL": "HALL"}

X32_FX_ALGOS = dict(_RAW)
# M32 与 X32 同一 Klark Teknik 引擎：独立副本，便于日后分化
M32_FX_ALGOS = {k: dict(v) for k, v in _RAW.items()}


def algo_names():
    """效果库全部算法名（顺序 = 枚举顺序，便于 UI）。"""
    return list(FX_ENUM_14)


def slot_allows(slot: int, algo: str) -> bool:
    """该槽位能否装载这个算法（heavy 约束）。"""
    d = X32_FX_ALGOS.get(algo)
    if not d:
        return False
    if d.get("heavy") and slot not in FX_SLOTS_HEAVY:
        return False
    return True


def param_spec(algo: str, field: str):
    """(算法, field) → 参数规格 dict；找不到返回 None。"""
    d = X32_FX_ALGOS.get(algo)
    if not d:
        return None
    for p in d["params"]:
        if p["field"] == field:
            return p
    return None
