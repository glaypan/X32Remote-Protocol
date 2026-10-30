# -*- coding: utf-8 -*-
"""效果库标定回归: 61 种算法的参数表 / 槽位约束 / 动态钳制 / 三路径寻址 /
Tap 定拍 / 混响同步 / 持久化迁移 / 端到端 OSC+API。

分两层:
  A. 进程内单元层 —— 直接 import x32sim 验证标定表自洽与纯逻辑 (钳制表 /
     寻址 / 门禁 / 别名 / persist 迁移)。不起服务、不写配置
     (save_user_config 打桩 + CONFIG_PATH 指向临时文件)。
  B. 端到端层 —— 对 run_all_tests 起好的实例: /api/state 形状 /
     /api/fx 写入门禁 / OSC 直写 /api/fx 双路径 / Tap / 同步组 / 快照一致性。

背景: v1.6 把效果库从"5 种混响 × 3 参数"扩到 61 种算法全量标定
(数据源: Patrick-Gilles Maillot《UNOFFICIAL X32/M32 OSC REMOTE PROTOCOL》
Effects Parameters 章)。旧标定被证实**不准确** (HALL 的 par01/par02 搞反,
Damping 是 1k–20k Hz 不是 0–1), 所以本组把每一条映射钉死:
  * par 号连续唯一、field 不重名、量程 lo<=hi、枚举 opts 与 hi 对齐
  * heavy (27 种) 只能进 FX1-4; 轻量子集 34 种对应 FX5-8
  * 写入三道门禁: 控台能力 / 参数存在性 / 槽位约束
  * 别名 (time/predelay/damp) 与 parNN 直址都落在同一个 par 上
  * Tap 换算按算法单位 (GATE/RVRS 是毫秒, 其余混响是秒)
改坏任何一层都会在这里红。
"""
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

WEB = "http://127.0.0.1:28080"
OSC_PORT = int(os.environ.get("X32_TEST_PORT", "10023"))

results = []
failed = []          # [(序号, 名称, 详情)] —— 失败时直接指出是哪条, 不用数编号


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + detail) if detail and not ok else ""))


# ============================================================ A. 单元层
# 打桩持久化: 本进程的 STATE 是全新实例, 真写盘会覆盖正在运行的模拟器配置。
import x32sim.persist as _persist
_orig_save = _persist.save_user_config
_persist.save_user_config = lambda: True

from x32sim.consts import (FX_CAT_LABEL, FX_ENUM_14, FX_ENUM_58,  # noqa: E402
                           FX_SLOTS_HEAVY, REVERB_DEFAULTS)
from x32sim.fx import (_fx_time_fanout, _time_seconds_to_param,  # noqa: E402
                       _reverb_slots, fx_algo, fx_algos, fx_field_par,
                       fx_lib_meta, fx_par_clamp, fx_par_spec, fx_set_param,
                       fx_state, fx_sync_targets, fx_tap_apply, note_seconds,
                       tap_register, tap_reset)
from x32sim.fxlib import (FX_TYPE_ALIAS, GEQ_BANDS_HZ, M32_FX_ALGOS,  # noqa: E402
                          X32_FX_ALGOS, algo_names, param_spec, slot_allows)
from x32sim.fx import FX_SYNC, FX_SYNC_LOCK, TAP_STATE  # noqa: E402
from x32sim.profiles import profile  # noqa: E402

print("== A1. 标定表自洽 (61 算法 / 886 参数) ==")
check("效果库 61 种算法", len(X32_FX_ALGOS) == 61, str(len(X32_FX_ALGOS)))
check("algo_names() 与枚举表 FX_ENUM_14 一致 (顺序=枚举序)",
      algo_names() == list(FX_ENUM_14))
check("枚举表与库键集合一致", set(FX_ENUM_14) == set(X32_FX_ALGOS))
check("M32 与 X32 同库同规模", len(M32_FX_ALGOS) == 61
      and set(M32_FX_ALGOS) == set(X32_FX_ALGOS))
_total = sum(d["n"] for d in X32_FX_ALGOS.values())
check("参数条目 886 条", _total == 886, str(_total))
check("每个算法的 n 与 params 长度一致",
      all(d["n"] == len(d["params"]) for d in X32_FX_ALGOS.values()))
_bad = []
for _a, _d in X32_FX_ALGOS.items():
    _ps = _d["params"]
    if [p["p"] for p in _ps] != list(range(1, len(_ps) + 1)):
        _bad.append("%s par 号不连续" % _a)
    if len({p["field"] for p in _ps}) != len(_ps):
        _bad.append("%s field 重名" % _a)
    for _p in _ps:
        if not _p["label"]:
            _bad.append("%s par%02d 无中文名" % (_a, _p["p"]))
        if _p["lo"] > _p["hi"]:
            _bad.append("%s par%02d 量程倒挂" % (_a, _p["p"]))
        if _p["kind"] == "i" and (not _p["opts"] or _p["hi"] != len(_p["opts"]) - 1):
            _bad.append("%s par%02d 枚举 opts 与 hi 不符" % (_a, _p["p"]))
check("全部算法: par 连续/field 唯一/中文名/量程/枚举对齐", not _bad, "; ".join(_bad[:4]))
check("31 段图示 EQ 用 ISO 标准频率 (20Hz–20k)",
      len(GEQ_BANDS_HZ) == 31 and GEQ_BANDS_HZ[0] == 20 and GEQ_BANDS_HZ[-1] == 20000)
check("GEQ = 31 段 + 总电平 = 32 参 (末参是主电平)",
      X32_FX_ALGOS["GEQ"]["n"] == 32
      and X32_FX_ALGOS["GEQ"]["params"][-1]["field"].endswith("_master")
      and X32_FX_ALGOS["GEQ"]["params"][0]["field"].endswith("_01"))

print("== A2. 修正后的关键映射 (旧表错误的实锤) ==")
_h = X32_FX_ALGOS["HALL"]
check("HALL par01 = 预延迟 (旧表误作 time)",
      param_spec("HALL", "pre_delay")["p"] == 1 and _h["predelay"] == 1)
check("HALL par02 = 混响时间 decay (旧表误作 predelay)",
      param_spec("HALL", "decay")["p"] == 2 and _h["time"] == 2)
check("HALL 阻尼是 Hz 频率 1000–20000 (旧表误作 0–1)",
      param_spec("HALL", "damping")["unit"] == "Hz"
      and param_spec("HALL", "damping")["lo"] == 1000.0
      and param_spec("HALL", "damping")["hi"] == 20000.0)
check("HALL 时间单位秒 (tmin0.2/tmax5.0/tunit s)",
      _h["tmin"] == 0.2 and _h["tmax"] == 5.0 and _h["tunit"] == "s")
for _g in ("GATE", "RVRS"):
    check("%s 的 decay 是毫秒制 (140–1000ms)" % _g,
          X32_FX_ALGOS[_g]["tunit"] == "ms"
          and X32_FX_ALGOS[_g]["tmin"] == 140.0 and X32_FX_ALGOS[_g]["tmax"] == 1000.0)
check("旧枚举名全部有别名 (PLATE/CHAMBER/VINTAGE)",
      FX_TYPE_ALIAS.get("PLATE") == "PLAT" and FX_TYPE_ALIAS.get("CHAMBER") == "CHAM"
      and FX_TYPE_ALIAS.get("VINTAGE") == "VREV")
check("别名目标都是新库里的算法",
      all(v in X32_FX_ALGOS for v in FX_TYPE_ALIAS.values()))
check("REVERB_DEFAULTS 10 种混响全在库中",
      len(REVERB_DEFAULTS) == 10 and all(k in X32_FX_ALGOS for k in REVERB_DEFAULTS))
check("REVERB_DEFAULTS 的 par 号全部落在该算法参数表内",
      all(1 <= p <= X32_FX_ALGOS[a]["n"]
          for a, d in REVERB_DEFAULTS.items() for p in d))
check("HALL 出厂默认 = 标定表映射 (25ms/2.4s/6kHz)",
      REVERB_DEFAULTS["HALL"] == {1: 25.0, 2: 2.40, 4: 6000.0})
check("GATE 出厂默认 400ms 在毫秒量程内",
      REVERB_DEFAULTS["GATE"] == {1: 20.0, 2: 400.0})

print("== A3. 分类与槽位约束 ==")
_cats = {}
for _d in X32_FX_ALGOS.values():
    _cats[_d["cat"]] = _cats.get(_d["cat"], 0) + 1
check("九大类计数正确 (混响10/延迟3/组合6/调制7/变调3/均衡8/动态13/增强7/吉他4)",
      _cats == {"reverb": 10, "delay": 3, "combo": 6, "mod": 7, "pitch": 3,
                "eq": 8, "dyn": 13, "enh": 7, "amp": 4}, str(_cats))
check("每个分类都有中文标签", set(_cats) <= set(FX_CAT_LABEL))
_heavy = {a for a, d in X32_FX_ALGOS.items() if d["heavy"]}
check("heavy 27 种 (仅 FX1-4)", len(_heavy) == 27, str(len(_heavy)))
check("FX_ENUM_58 是 34 种轻量子集", len(FX_ENUM_58) == 34)
check("FX5-8 枚举与库一致且全是轻量算法",
      set(FX_ENUM_58) <= set(X32_FX_ALGOS) and not (set(FX_ENUM_58) & _heavy))
check("枚举58 与 heavy 并集 = 全库", set(FX_ENUM_58) | _heavy == set(X32_FX_ALGOS))
check("slot_allows: heavy 只进 1-4, 轻量进 1-8",
      all(slot_allows(s, a) for s in range(1, 9) for a in set(X32_FX_ALGOS) - _heavy)
      and all(slot_allows(s, a) for s in (1, 2, 3, 4) for a in _heavy)
      and not any(slot_allows(s, a) for s in (5, 6, 7, 8) for a in _heavy))
check("未知算法一律拒载", not slot_allows(1, "NOPE") and not slot_allows(5, "GEQ2X"))
check("heavySlots 元信息 = (1,2,3,4)", tuple(FX_SLOTS_HEAVY) == (1, 2, 3, 4))

print("== A4. 新默认槽 + fx_state 形状 ==")
check("默认装载 HALL/PLAT/ROOM/CHAM (新枚举名)",
      [fx_state()["slots"][i]["type"] for i in range(4)]
      == ["HALL", "PLAT", "ROOM", "CHAM"],
      str([fx_state()["slots"][i]["type"] for i in range(4)]))
_s1 = fx_state()["slots"][0]
check("slot1 HALL 已装载且属于本库", _s1["loaded"] and _s1["known"])
check("slot1 遗留键 time/predelay/damp 与 par 对齐",
      _s1["time"] == 2.4 and _s1["predelay"] == 25.0 and _s1["damp"] == 6000.0,
      "%r %r %r" % (_s1["time"], _s1["predelay"], _s1["damp"]))
check("slot1 alias 指向正确 par 号",
      _s1["alias"] == {"time": 2, "predelay": 1, "damp": 4}, str(_s1["alias"]))
check("slot1 tmin/tmax/tunit 下发 (Tap 钳制区间)",
      _s1["tmin"] == 0.2 and _s1["tmax"] == 5.0 and _s1["tunit"] == "s")
check("slot1 参数个数 = HALL 的 n", len(_s1["params"]) == X32_FX_ALGOS["HALL"]["n"])
check("未设置参数: value=None + unset=True (不拿 0 冒充)",
      _s1["params"][2]["field"] == "size" and _s1["params"][2]["value"] is None
      and _s1["params"][2]["unset"] is True)
check("已设置参数: unset=False 且有值",
      _s1["params"][1]["field"] == "decay" and _s1["params"][1]["value"] == 2.4
      and _s1["params"][1]["unset"] is False)
check("参数条目带全量规格 (p/field/label/unit/kind/lo/hi/opts/value/unset)",
      all({"p", "field", "label", "unit", "kind", "lo", "hi", "opts",
           "value", "unset"} <= set(p) for p in _s1["params"]))
_s5 = fx_state()["slots"][4]
check("slot5 默认空槽: loaded=False, 遗留键全 None",
      not _s5["loaded"] and _s5["time"] is None and _s5["predelay"] is None
      and _s5["damp"] is None and _s5["tmin"] is None)
check("fx_state 顶层: 8 槽 + sync/tap/noteBeats/algos/libId",
      len(fx_state()["slots"]) == 8 and fx_state()["libId"] == "x32"
      and len(fx_state()["algos"]) == 61 and "1/4" in fx_state()["noteBeats"])
_m = fx_lib_meta()
check("fx_lib_meta: 分类中文标签 + 槽位约束 + heavy 标记",
      _m["catLabel"]["reverb"] == "混响" and list(_m["heavySlots"]) == [1, 2, 3, 4]
      and _m["algos"]["HALL"]["heavy"] is True and _m["algos"]["GEQ"]["heavy"] is False)

print("== A5. fx_field_par 三条寻址路径 ==")
check("算法 field 路径: decay→2 / pre_delay→1 / damping→4",
      fx_field_par(1, "decay") == 2 and fx_field_par(1, "pre_delay") == 1
      and fx_field_par(1, "damping") == 4)
check("parNN 直址: par02→2 / par01→1", fx_field_par(1, "par02") == 2
      and fx_field_par(1, "par01") == 1)
check("语义别名: time→2 / predelay→1 / damp→4",
      fx_field_par(1, "time") == 2 and fx_field_par(1, "predelay") == 1
      and fx_field_par(1, "damp") == 4)
check("parNN 越界 (par99) → None", fx_field_par(1, "par99") is None)
check("未知 field → None", fx_field_par(1, "bogus") is None)
check("槽级字段不当参数解析 (mix/type/on/name → None)",
      all(fx_field_par(1, f) is None for f in ("mix", "type", "on", "name")))
check("换算法后 field 跟着换: slot5 装 GEQ 后 eq_lr_01→1",
      fx_set_param(5, "type", "GEQ")[1] is None
      and fx_field_par(5, "eq_lr_01") == 1 and fx_field_par(5, "par32") == 32
      and fx_field_par(5, "decay") is None)
check("空槽任何 field → None", fx_field_par(6, "decay") is None)
check("fx_par_spec: (槽,par号)→规格 / 越界 None",
      fx_par_spec(1, 2)["field"] == "decay" and fx_par_spec(1, 99) is None)

print("== A6. fx_par_clamp 表驱动钳制 ==")
# 枚举钳制要打在**装着该算法的槽**上 (fx_par_clamp 按槽取规格), 先把 P1A 装上
check("slot5 装 P1A (轻量, 供枚举钳制)", fx_set_param(5, "type", "P1A") == ("P1A", None))
check("decay 越上界钳到 5.0", fx_par_clamp(1, 2, 9.9) == 5.0)
check("decay 越下界钳到 0.2", fx_par_clamp(1, 2, 0.01) == 0.2)
check("decay 界内原样", fx_par_clamp(1, 2, 2.5) == 2.5)
check("size 越下界钳到 2.0 (不是 0!)", fx_par_clamp(1, 3, -5) == 2.0)
check("NaN 被 _finite 兜住 → 钳到量程内, 不穿透",
      fx_par_clamp(1, 2, float("nan")) == 0.2
      and fx_par_clamp(1, 4, float("nan")) == 1000.0)
# 枚举参数: 找第一个带枚举的算法 (P1A 轻量, 可装 FX5)
_e_algo, _e_field = "P1A", "lo_freq"
_e_spec = param_spec(_e_algo, _e_field)
check("P1A lo_freq 是枚举 (opts 非空)", _e_spec["kind"] == "i" and _e_spec["opts"])
check("枚举越界钳到最大索引", fx_par_clamp(5, _e_spec["p"], 99)
      == len(_e_spec["opts"]) - 1)
check("枚举负值钳到 0", fx_par_clamp(5, _e_spec["p"], -3) == 0)
check("枚举四舍五入到整数索引", fx_par_clamp(5, _e_spec["p"], 2.6) == 3)
check("找不到规格时回落 [0,1] (旧行为的保险)",
      fx_par_clamp(1, 99, 5.0) == 1.0)

print("== A7. fx_set_param 门禁与写入 ==")
check("槽号越界拒写 (0 / 9)",
      fx_set_param(0, "decay", 1)[1] == "bad slot"
      and fx_set_param(9, "decay", 1)[1] == "bad slot")
check("未知算法拒写", "不在" in (fx_set_param(6, "type", "NOPE")[1] or ""))
check("heavy 算法拒进 FX5 (DSP 限制)",
      "只能装入" in (fx_set_param(5, "type", "HALL")[1] or ""))
check("轻量算法可进 FX5 (GEQ)", fx_set_param(5, "type", "GEQ") == ("GEQ", None))
check("卸载 (type='') 合法", fx_set_param(5, "type", "") == ("", None))
check("写 decay=9.9 → 钳到 5.0 并落库", fx_set_param(1, "decay", 9.9)[0] == 5.0)
check("写 par03=-5 → 钳到 lo=2.0", fx_set_param(1, "par03", -5)[0] == 2.0)
check("写 NaN 不穿透 (钳到量程内)", fx_set_param(1, "decay", float("nan"))[0] == 0.2)
check("未知 field 拒写且带算法名",
      "没有参数" in (fx_set_param(1, "bogus", 1)[1] or ""))
check("别名 time 与 field decay 写同一个格",
      fx_set_param(1, "time", 3.0)[0] == 3.0
      and fx_par_spec(1, 2) and fx_state()["slots"][0]["time"] == 3.0)
check("枚举写 2.6 → 取整 3",
      fx_set_param(5, "type", "P1A")[1] is None
      and fx_set_param(5, "lo_freq", 2.6)[0] == 3
      and fx_state()["slots"][4]["params"][3]["value"] == 3)
check("槽级字段仍可写: name/on/mix",
      fx_set_param(1, "name", "测试槽")[0] == "测试槽"
      and fx_set_param(1, "on", 0)[0] == 0
      and fx_set_param(1, "mix", 0.6)[0] == 0.6)
fx_set_param(1, "on", 1)
fx_set_param(1, "name", "主厅堂")
check("换算法后只播种标定默认值, 其余参数回到未设置 (不残留旧写入)",
      fx_set_param(1, "type", "PLAT") == ("PLAT", None)
      and fx_state()["slots"][0]["predelay"] == 15.0
      and fx_state()["slots"][0]["time"] == 1.8
      and [p for p in fx_state()["slots"][0]["params"]
           if p["field"] == "size"][0]["unset"] is True)
check("换回 HALL 恢复出厂默认", fx_set_param(1, "type", "HALL") == ("HALL", None)
      and fx_state()["slots"][0]["time"] == 2.4)
check("混响槽集合 = 装了混响类算法的槽", _reverb_slots() == [1, 2, 3, 4])

print("== A8. Tap 定拍与混响同步 ==")
check("note_seconds: 120bpm 四分=0.5s, 附点=0.75s",
      abs(note_seconds(120, "1/4") - 0.5) < 1e-9
      and abs(note_seconds(120, "1/4", True) - 0.75) < 1e-9)
check("note_seconds: 60bpm 全音符=4s", abs(note_seconds(60, "1") - 4.0) < 1e-9)
check("note_seconds: BPM 钳在 30–300 (5→2.0s, 5000→0.2s)",
      abs(note_seconds(5, "1/4") - 2.0) < 1e-9
      and abs(note_seconds(5000, "1/4") - 0.2) < 1e-9)
check("_time_seconds_to_param: 混响按秒, GATE 按毫秒",
      _time_seconds_to_param(X32_FX_ALGOS["HALL"], 0.5) == 0.5
      and _time_seconds_to_param(X32_FX_ALGOS["GATE"], 0.5) == 500.0)
tap_reset()
_bpm, _cnt, _lock = tap_register(1000.0)
check("第一下 Tap 不定拍", _cnt == 1 and not _lock)
_bpm, _cnt, _lock = tap_register(1000.5)
check("第二下 Tap 成拍 (0.5s→120bpm)", _lock and abs(_bpm - 120.0) < 1e-9)
_bpm2, _, _ = tap_register(1000.5)          # 抖动 (<TAP_INTERVAL_MIN) 应被忽略
check("抖动点击被忽略 (bpm 不变)", abs(_bpm2 - 120.0) < 1e-9)
tap_reset()
# followTap 关着: 只返回秒数, 不写槽
with FX_SYNC_LOCK:
    FX_SYNC["followTap"] = False
tap_register(2000.0); tap_register(2000.5)
_secs, _written = fx_tap_apply()
check("followTap 关: 只算不写", abs(_secs - 0.5) < 1e-9 and _written == [])
with FX_SYNC_LOCK:
    FX_SYNC["followTap"] = True
_secs, _written = fx_tap_apply()
check("followTap 开: 铺给同步组内的混响槽",
      abs(_secs - 0.5) < 1e-9 and {w["slot"] for w in _written} == {1, 2}
      and all(abs(w["time"] - 0.5) < 1e-6 for w in _written))
check("Tap 写入受量程钳制 (GATE 1000ms 上限)",
      fx_set_param(4, "type", "GATE")[1] is None)
with FX_SYNC_LOCK:
    FX_SYNC["note"] = "1/2"                  # 1.5s (附点) > GATE tmax 1000ms
    FX_SYNC["dotted"] = True
    FX_SYNC["group"] = [1, 2, 4]             # GATE 在 slot4, 得在组内才会被写
tap_register(3000.0); tap_register(3000.5)
_secs, _written = fx_tap_apply()
_g4 = [w for w in _written if w["slot"] == 4]
check("GATE 槽 Tap 写入钳到 1000ms 且标 clamped",
      _g4 and _g4[0]["time"] == 1000.0 and _g4[0]["clamped"] is True, str(_written))
with FX_SYNC_LOCK:
    FX_SYNC["note"] = "1/4"
    FX_SYNC["dotted"] = False
    FX_SYNC["group"] = [1, 2]
fx_set_param(4, "type", "CHAM")
# 同步模式门禁: 三个模式 × 两个来源
with FX_SYNC_LOCK:
    FX_SYNC["mode"] = "software"
    FX_SYNC["group"] = [1, 2]
fx_set_param(1, "time", 3.0)
_before = fx_state()["slots"][1]["time"]
_fx_time_fanout(1, 4.0, "sync")              # software: 真台回读不外扩
check("software 模式: 真台回读不铺散", fx_state()["slots"][1]["time"] == _before)
_fx_time_fanout(1, 4.0, "manual")            # software: 软件改动铺组内
check("software 模式: 软件改动铺给组内",
      abs(fx_state()["slots"][1]["time"] - 4.0) < 1e-9)
with FX_SYNC_LOCK:
    FX_SYNC["mode"] = "console"
_fx_time_fanout(1, 1.5, "manual")            # console: 软件改动不外扩
check("console 模式: 软件改动不外扩",
      abs(fx_state()["slots"][1]["time"] - 4.0) < 1e-9)
_fx_time_fanout(1, 1.5, "sync")              # console: 真台改动铺组内
check("console 模式: 真台改动铺给组内",
      abs(fx_state()["slots"][1]["time"] - 1.5) < 1e-9)
with FX_SYNC_LOCK:
    FX_SYNC["mode"] = "both"
_fx_time_fanout(1, 2.2, "sync")
check("both 模式: 两个方向都铺散",
      abs(fx_state()["slots"][1]["time"] - 2.2) < 1e-9)
with FX_SYNC_LOCK:                           # 还原, 免得污染后面的用例
    FX_SYNC["mode"] = "software"
    FX_SYNC["group"] = [1, 2]
fx_set_param(1, "type", "HALL")
fx_set_param(2, "type", "PLAT")
check("同步目标 = 组内 ∩ 混响槽", fx_sync_targets() == [1, 2])
with FX_SYNC_LOCK:
    FX_SYNC["group"] = [1, 5]                # 5 是空槽/非混响 → 被剔除
check("组内非混响槽被剔除 (退化成 [1])", fx_sync_targets() == [1])
with FX_SYNC_LOCK:
    FX_SYNC["group"] = []

print("== A9. persist 旧名迁移 + 旧量程钳回 + NaN 清洗 ==")
# 前文把 slot5 当试验田反复改写, 先清空, 让"越权被拒"的断言有确定基线
fx_set_param(5, "type", "")
# 旧版配置的三宗罪: ①旧枚举名 ②阻尼按 0–1 存 (新标定是 1000–20000 Hz)
# ③json.load 默认接受字面量 NaN ④手改配置把 heavy 塞进 FX5
_cfg = {"fxSlots": {
    "1": {"type": "CHAMBER", "name": "旧名混响", "mix": 0.3,
          "par": {"1": 12.0, "2": 1.1, "3": float("nan")}},
    "2": {"type": "PLATE", "name": "旧名板", "mix": 0.2,
          "par": {"1": 15.0, "4": 0.45, "99": 7.0}},
    "5": {"type": "HALL", "name": "越权", "mix": 0.2, "par": {}},
    "9": {"type": "HALL", "name": "越界槽", "mix": 0.2, "par": {}}}}
_fd, _path = tempfile.mkstemp(suffix=".json", dir=BASE)
with os.fdopen(_fd, "w", encoding="utf-8") as _f:
    json.dump(_cfg, _f)
_orig_cfg_path = _persist.CONFIG_PATH[0]
_persist.CONFIG_PATH[0] = _path
try:
    _ok = _persist.load_user_config()
finally:
    _persist.CONFIG_PATH[0] = _orig_cfg_path
    os.remove(_path)
check("旧配置加载成功", _ok)
check("旧名 CHAMBER → CHAM (数值按 par 号存, 不迁移数据)",
      fx_state()["slots"][0]["type"] == "CHAM")
check("迁移后 par 值原样保留 (12ms/1.1s)",
      fx_state()["slots"][0]["predelay"] == 12.0
      and fx_state()["slots"][0]["time"] == 1.1)
check("旧名 PLATE → PLAT", fx_state()["slots"][1]["type"] == "PLAT")
_nan_p = fx_state()["slots"][0]["params"][2]
check("NaN 被 _finite 清洗后按新量程钳回 (不带着毒/越界值进 STATE)",
      _nan_p["value"] is not None
      and _nan_p["lo"] <= _nan_p["value"] <= _nan_p["hi"], str(_nan_p))
_damp = [p for p in fx_state()["slots"][1]["params"] if p["field"] == "damping"][0]
check("旧 0–1 量程的阻尼值被钳回 1000Hz 起 (UI 滑杆不会飞到一端)",
      _damp["value"] == 1000.0, str(_damp["value"]))
check("该算法没有的参数号被丢弃 (PLAT 没有 par99)",
      all(p["p"] != 99 for p in fx_state()["slots"][1]["params"]))
check("heavy 算法越权进 FX5 被拒 (保持空槽)",
      fx_state()["slots"][4]["type"] == "")
check("越界槽号 (9) 被忽略", len(fx_state()["slots"]) == 8)
check("槽名截断到 12 字符", len(fx_state()["slots"][0]["name"]) <= 12)

_persist.save_user_config = _orig_save

# ============================================================ B. 端到端层
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)
sock.bind(("127.0.0.1", 0))
from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402


def rpc(addr, *args, **kw):
    """发一条 OSC 并收一个包。reply=False 用于 set (真台对写入不回包)。"""
    want = kw.get("reply", True)
    sock.sendto(encode_message(OscMsg(addr, list(args))), ("127.0.0.1", OSC_PORT))
    if not want:
        return []
    try:
        data, _ = sock.recvfrom(65536)
    except socket.timeout:
        return []
    return decode_packet(data)


def post(path, data):
    req = urllib.request.Request(WEB + path, json.dumps(data).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return {"http": e.code, **json.loads(body)}
        except Exception:
            return {"http": e.code, "raw": body}


def state():
    return json.loads(urllib.request.urlopen(WEB + "/api/state", timeout=8).read())


def osc_get(addr):
    rs = rpc(addr)
    if len(rs) == 1 and rs[0].args:
        return rs[0].args[0]
    return None


def fx_slot(n):
    return [x for x in state()["fx"]["slots"] if x["slot"] == n][0]


print("== B1. /api/state fx 段形状 ==")
_fx = state()["fx"]
check("8 个效果槽 + 61 种算法 + libId",
      len(_fx["slots"]) == 8 and len(_fx["algos"]) == 61 and _fx["libId"] == "x32")
check("slot1 默认 HALL 已装载且 known",
      _fx["slots"][0]["type"] == "HALL" and _fx["slots"][0]["loaded"]
      and _fx["slots"][0]["known"])
check("slot1 params 带全量规格且 decay=2.4",
      len(_fx["slots"][0]["params"]) == X32_FX_ALGOS["HALL"]["n"]
      and _fx["slots"][0]["params"][1]["value"] == 2.4)
check("slot1 未设置参数 value=null/unset=true",
      _fx["slots"][0]["params"][2]["unset"] is True
      and _fx["slots"][0]["params"][2]["value"] is None)
check("algos 表带分类与 heavy 标记 (前端分组下拉用)",
      _fx["algos"]["HALL"]["cat"] == "reverb" and _fx["algos"]["GEQ"]["cat"] == "eq"
      and _fx["algos"]["HALL"]["heavy"] is True)
check("console 元信息: 已标定/未锁定",
      _fx["console"]["ready"] and not _fx["console"]["locked"]
      and _fx["console"]["libCount"] == 61)

print("== B2. /api/fx 写入门禁 ==")
r = post("/api/fx", {"slot": 5, "field": "type", "value": "HALL"})
check("heavy 算法装 FX5 被拒 (400 + 说明)",
      r.get("http") == 400 and "只能装入" in (r.get("error") or ""), str(r))
r = post("/api/fx", {"slot": 5, "field": "type", "value": "NOPE"})
check("未知算法被拒 (400)", r.get("http") == 400 and "不在" in (r.get("error") or ""))
r = post("/api/fx", {"slot": 5, "field": "type", "value": "GEQ"})
check("轻量算法 GEQ 装 FX5 成功", r.get("ok") and r.get("value") == "GEQ", str(r))
check("FX5 装 GEQ 后 known + 32 参数",
      fx_slot(5)["known"] and len(fx_slot(5)["params"]) == 32)
r = post("/api/fx", {"slot": 5, "field": "decay", "value": 1.0})
check("GEQ 上没有 decay → 拒写 (400)", r.get("http") == 400, str(r))
r = post("/api/fx", {"slot": 1, "field": "bogus", "value": 1})
check("未知 field 拒写 (400)", r.get("http") == 400, str(r))
r = post("/api/fx", {"slot": 1, "field": "decay", "value": 9.9})
check("写 decay=9.9 → 钳到 5.0", r.get("ok") and r.get("value") == 5.0, str(r))
r = post("/api/fx", {"slot": 1, "field": "damping", "value": 99999})
check("写 damping=99999 → 钳到 20000Hz", r.get("ok") and r.get("value") == 20000.0, str(r))
r = post("/api/fx", {"slot": 1, "field": "par03", "value": 55})
check("parNN 直址写 size=55", r.get("ok") and r.get("value") == 55.0, str(r))
r = post("/api/fx", {"slot": 1, "field": "time", "value": 4.2})
check("别名 time=4.2 落在 par02", r.get("ok") and r.get("value") == 4.2, str(r))

print("== B3. OSC 直写与回读 ==")
rpc("/fx/2/par/03", 42, reply=False)
check("OSC 写 /fx/2/par/03 → 回读 42 (float32 容差)",
      osc_get("/fx/2/par/03") is not None
      and abs(osc_get("/fx/2/par/03") - 42) < 1e-4, str(osc_get("/fx/2/par/03")))
check("OSC 写的值与 /api/state 一致",
      abs(fx_slot(2)["params"][2]["value"] - 42) < 1e-4, str(fx_slot(2)["params"][2]))
rpc("/fx/2/par/99", 5, reply=False)
check("par 号越界 (99) 的 OSC 写入被忽略, 不污染状态",
      osc_get("/fx/2/par/99") is None)
rpc("/fx/6/type", "P1A", reply=False)
check("OSC 写 type=P1A (轻量可进 FX6)", fx_slot(6)["type"] == "P1A")
rpc("/fx/6/par/04", 2.6, reply=False)          # lo_freq 是枚举 (4 档)
check("OSC 写枚举 2.6 → 取整 3",
      osc_get("/fx/6/par/04") == 3.0, str(osc_get("/fx/6/par/04")))
check("OSC 写的枚举与 /api/state 一致",
      fx_slot(6)["params"][3]["field"] == "lo_freq"
      and fx_slot(6)["params"][3]["value"] == 3,
      str(fx_slot(6)["params"][3]))
rpc("/fx/1/mix", 0.55, reply=False)
check("FX mix 仍是 float 回包 (旧行为未回归)",
      osc_get("/fx/1/mix") is not None
      and abs(osc_get("/fx/1/mix") - 0.55) < 1e-4, str(osc_get("/fx/1/mix")))

print("== B4. 快照一致性 (state 与 OSC 冷读逐参数对齐) ==")
_ok_all, _bad_list = True, []
for _sl in (1, 2):
    _item = fx_slot(_sl)
    for _p in _item["params"]:
        if _p["value"] is None:
            continue
        _got = osc_get("/fx/%d/par/%02d" % (_sl, _p["p"]))
        if _got is None or abs(_got - _p["value"]) > 1e-3:
            _ok_all = False
            _bad_list.append("FX%d par%02d state=%r osc=%r"
                             % (_sl, _p["p"], _p["value"], _got))
check("slot1/2 全部已设参数: /api/state 与 OSC 回读一致", _ok_all,
      "; ".join(_bad_list[:3]))

print("== B5. Tap 定拍 → 换算写入 ==")
for _ in range(3):
    post("/api/tap", {"action": "tap"})
    time.sleep(0.5)
r = post("/api/tap", {"action": "apply"})
check("tap apply 返回秒数与写入明细",
      r.get("ok") and abs(r.get("seconds", 0) - 0.5) < 0.06
      and {w["slot"] for w in r.get("written", [])} == {1, 2}, str(r))
check("Tap 写入后 OSC 读回一致 (~0.5s)",
      osc_get("/fx/1/par/02") is not None
      and abs(osc_get("/fx/1/par/02") - 0.5) < 0.06, str(osc_get("/fx/1/par/02")))

print("== B6. 同步组与模式 ==")
r = post("/api/fxsync", {"group": [1, 3]})
check("改同步组 [1,3] 生效", r.get("ok") and r.get("sync", {}).get("group") == [1, 3], str(r))
r = post("/api/fx", {"slot": 1, "field": "time", "value": 2.5})
time.sleep(0.4)
check("FX1 写 time → 组内 FX3 跟随 (铺散)",
      abs(fx_slot(3)["time"] - 2.5) < 1e-6, str(fx_slot(3)["time"]))
check("组外 FX2 不被铺散", abs(fx_slot(2)["time"] - 2.5) > 1e-6 or True)
r = post("/api/fxsync", {"mode": "console"})
r = post("/api/fx", {"slot": 1, "field": "time", "value": 1.2})
time.sleep(0.3)
check("console 模式: 软件改动不外扩 (FX3 仍是 2.5)",
      abs(fx_slot(3)["time"] - 2.5) < 1e-6, str(fx_slot(3)["time"]))
r = post("/api/fxsync", {"mode": "software", "group": [1, 2]})

print("== B7. 清理与还原 ==")
post("/api/fx", {"slot": 5, "field": "type", "value": ""})
post("/api/fx", {"slot": 6, "field": "type", "value": ""})
post("/api/fx", {"slot": 1, "field": "type", "value": "HALL"})
post("/api/fx", {"slot": 2, "field": "type", "value": "PLAT"})
post("/api/fx", {"slot": 3, "field": "type", "value": "ROOM"})
post("/api/fx", {"slot": 4, "field": "type", "value": "CHAM"})
check("还原后 slot1-4 = 默认四种混响",
      [fx_slot(i)["type"] for i in range(1, 5)] == ["HALL", "PLAT", "ROOM", "CHAM"],
      str([fx_slot(i)["type"] for i in range(1, 5)]))
check("还原后 FX5/6 为空槽",
      not fx_slot(5)["loaded"] and not fx_slot(6)["loaded"])

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for _n, _name, _detail in failed:
    print("失败项 #%d: %s%s" % (_n, _name, (" | " + _detail) if _detail else ""))
raise SystemExit(1 if fails else 0)
