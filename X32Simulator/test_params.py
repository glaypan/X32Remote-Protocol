# -*- coding: utf-8 -*-
"""参数建模 (B2) 回归: 12 个新增通道参数的
地址映射 / 量程 / 量化档位 / 线上类型 / 通配符 / API / 卡片动作 / 预设范围。

分两层:
  A. 进程内单元层 —— 直接 import x32sim 包验证纯逻辑 (钳制表 / 卡片清洗 /
     预设抓取与回归 / 通配符匹配 / 回包类型 / 出厂行)。不起服务、不写配置
     (save_user_config 打桩, 否则会用本进程的空白状态覆盖正在跑的实例配置)。
  B. 端到端层 —— 对 run_all_tests 起好的实例: OSC 往返 / 越界 / 通配符
     bundle / /api/set|setbatch / 卡片 set 动作 / 预设 scope / /api/state
     形状 / 旧行为未回归。

背景: 这 12 个参数 (增益/反相/延时/低切/插入/门) 在 B2 之前只存在于"设想"
里 —— 界面发得出去、台子存不下; 或者存下了但按 [0,1] 钳成 1 (trim 3.5 → 1)。
本组把每一条链路钉死, 改坏任何一层都会在这里红。
"""
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
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
# 打桩持久化: 本进程的 STATE/SNAPSHOTS 是全新实例, 真写盘会覆盖正在运行的
# 模拟器配置 (presets/cards 全被清成空) —— 单元层一个字节都不该落盘。
import x32sim.persist as _persist
_orig_save = _persist.save_user_config
_persist.save_user_config = lambda: True

from x32sim.actions import _clean_actions            # noqa: E402
from x32sim.params import (CH_DEEP_ADDR, CH_EQ_ADDR, CH_EXTRA_ADDR,  # noqa: E402
                           GROUP_PARAM_SPECS, CH_PARAM_SPECS,
                           PARAM_SPECS, SEND_PARAM_SPECS, blank_row,
                           level_db, level_from_db,
                           param_coerce, param_from_wire, param_wire, send_pack,
                           send_unpack, spec_addr, unverified_kinds)
from x32sim.presets import _recall_instant, _snap_group_items_locked  # noqa: E402
from x32sim.state import STATE, get_value, match_address, set_value, value_reply  # noqa: E402

print("== A1. 登记表自洽 ==")
# 数量断言改成"分组自洽"而不是写死一个数：以后再加一组通道不用回来改测试，
# 但"漏了一半参数"仍然能被抓出来。
_row_sum = sum(len(v) for v in GROUP_PARAM_SPECS.values())
check("登记表 = 16 推子/静音 + 各组行参数 + 13 发送 + 1 话放",
      len(PARAM_SPECS) == 16 + _row_sum + len(SEND_PARAM_SPECS),
      "%d vs 16+%d+%d" % (len(PARAM_SPECS), _row_sum, len(SEND_PARAM_SPECS)))
check("ch 参数 57 项 (13 前置/插入 + 11 门 + 18 动态 + 16 均衡 + 声像)",
      len(CH_PARAM_SPECS) == 57, str(len(CH_PARAM_SPECS)))
check("Bus 参数 47 项 (18 动态 + 3 插入 + 24 均衡 + 声像)",
      len(GROUP_PARAM_SPECS["bus"]) == 47, str(len(GROUP_PARAM_SPECS["bus"])))
check("Bus EQ 是 6 段 (ch 是 4 段)",
      len([sp for sp in GROUP_PARAM_SPECS["bus"] if sp.field.startswith("eq6_")]) == 4
      and not any(sp.field.startswith("eq5_") for sp in CH_PARAM_SPECS))
check("FX 返回只有声像 (真台没有 EQ/动态/插入)",
      [sp.field for sp in GROUP_PARAM_SPECS["fxret"]] == ["pan"],
      str([sp.field for sp in GROUP_PARAM_SPECS["fxret"]]))
check("发送参数 13 项 (ch/auxin/fxret 各 3 + bus/main 各 2)",
      len(SEND_PARAM_SPECS) == 13, str(len(SEND_PARAM_SPECS)))
kinds = [sp.kind for sp in PARAM_SPECS.values()]
check("kind 无重复", len(kinds) == len(set(kinds)))
main_specs = [sp for sp in PARAM_SPECS.values()
              if sp.group == "main" and sp.group != "send"]
check("main 的行参数模板不带 %02d (只有一路)",
      all("%02d" not in sp.osc for sp in main_specs))
check("spec_addr(main_fader,0) 直接给地址",
      spec_addr(PARAM_SPECS["main_fader"], 0) == "/main/st/mix/fader",
      spec_addr(PARAM_SPECS["main_fader"], 0))
check("spec_addr(ch_trim,7) 补零",
      spec_addr(PARAM_SPECS["ch_trim"], 7) == "/ch/07/preamp/trim")
ch_addrs = {spec_addr(sp, 1) for sp in CH_PARAM_SPECS}
check("57 个通道参数地址互不重复", len(ch_addrs) == 57, str(len(ch_addrs)))
# 4 段地址 (dyn/gate/preamp…) 走 CH_EXTRA_ADDR; EQ 段落是 5 段, 单独一张表
# 4 段地址的条数由登记表自己算: 写死一个数只会在加参数时变成噪音
_ch4 = [sp for sp in PARAM_SPECS.values() if sp.group == "ch"
        and len(sp.osc.strip("/").split("/")) == 4]   # 含推子/静音
check("4 段地址索引覆盖全部 4 段通道参数 (%d 项)" % len(_ch4),
      sum(len(v) for v in CH_EXTRA_ADDR.values()) == len(_ch4),
      "%d vs %d" % (sum(len(v) for v in CH_EXTRA_ADDR.values()), len(_ch4)))
check("侧链滤波 5 段地址索引 6 项 (gate/dyn × on/type/f)",
      sum(len(v) for v in CH_DEEP_ADDR.values()) == 6,
      str(sum(len(v) for v in CH_DEEP_ADDR.values())))
check("EQ 5 段地址索引 16 项",
      sum(len(v) for v in CH_EQ_ADDR.values()) == 16,
      str(sum(len(v) for v in CH_EQ_ADDR.values())))
check("发送地址是二维 (ch/auxin/fxret 两个 %02d, main 一个)",
      all(sp.osc.count("%02d") == (1 if sp.osc.startswith("/main/") else 2)
          for sp in SEND_PARAM_SPECS))
check("send_pack/send_unpack 互逆 (含各组基址)",
      all(send_unpack(send_pack(c, b, g)) == (g, c, b)
          for g, c, b in (("ch", 1, 1), ("ch", 32, 16), ("auxin", 6, 16),
                          ("fxret", 8, 16), ("bus", 16, 6), ("main", 1, 6))))
check("发送基址不撞号 (ch 1..512 / auxin 1001+ …)",
      send_pack(32, 16, "ch") == 512 and send_pack(1, 1, "auxin") == 1001
      and send_pack(1, 1, "bus") == 3001 and send_pack(1, 1, "main") == 4001)

print("== A2. 出厂行 (blank_row) ==")
row = blank_row("ch")
check("ch 行 59 个字段 (推子/静音 + 57 参数)", len(row) == 59, str(len(row)))
check("推子出厂 0.75 / 静音出厂 1", row["fader"] == 0.75 and row["on"] == 1)
check("增益出厂 0 dB / 门限出厂 -40 dB / 门范围 15 dB",
      row["trim"] == 0.0 and row["gate_thr"] == -40.0 and row["gate_range"] == 15.0)
check("低切频率出厂 80 Hz (需真机确认项有 note)",
      row["hpf_f"] == 80.0 and "真机" in PARAM_SPECS["ch_hpf_f"].note)
check("开关默认值是 int 不是 float (JSON 形状)",
      isinstance(row["on"], int) and isinstance(row["invert"], int))
check("低切斜率默认 12 (int)", row["hpf_slope"] == 12 and isinstance(row["hpf_slope"], int))
check("bus 行 = 推子/静音 + 47 个处理参数", len(blank_row("bus")) == 49,
      str(len(blank_row("bus"))))
check("声像出厂居中 (0), 范围 -100..100",
      blank_row("ch")["pan"] == 0.0
      and PARAM_SPECS["ch_pan"].lo == -100.0 and PARAM_SPECS["ch_pan"].hi == 100.0)

print("== A3. 量程钳制与量化档位 (param_coerce) ==")
_cases = [
    ("ch_trim", 25.0, 18.0, "trim 超上限钳到 +18"),
    ("ch_trim", -25.0, -18.0, "trim 超下限钳到 -18"),
    ("ch_trim", 3.3, 3.25, "trim 按 0.25 dB 档量化 (3.3→3.25)"),
    ("ch_trim", 3.4, 3.5, "trim 就近吸附 (3.4→3.5)"),
    ("ch_trim", 0.0, 0.0, "trim 0 不变"),
    ("ch_delay_ms", 0.0, 0.3, "延时下限是 0.3 不是 0"),
    ("ch_delay_ms", 999.0, 500.0, "延时上限 500"),
    ("ch_delay_ms", 12.34, 12.3, "延时按 0.1 ms 档量化"),
    ("ch_hpf_f", 10.0, 20.0, "低切频率下限 20"),
    ("ch_hpf_f", 1000.0, 400.0, "低切频率上限 400"),
    ("ch_hpf_slope", 5, 12, "斜率最近枚举 (5→12)"),
    ("ch_hpf_slope", 20, 18, "斜率最近枚举 (20→18)"),
    ("ch_hpf_slope", 30, 24, "斜率最近枚举 (30→24)"),
    ("ch_hpf_slope", 0, 12, "真机若按索引回 0 → 吸附 12 (不静默变 0)"),
    ("ch_gate_thr", 5.0, 0.0, "门限上限 0 dB"),
    ("ch_gate_thr", -100.0, -80.0, "门限下限 -80 dB"),
    ("ch_gate_thr", -35.3, -35.5, "门限按 0.5 dB 档"),
    ("ch_gate_range", 1.0, 3.0, "门范围下限 3"),
    ("ch_gate_range", 999.0, 60.0, "门范围上限 60"),
]
for kind, raw, want, name in _cases:
    got = param_coerce(kind, raw)
    ok = abs(got - want) < 1e-9 if isinstance(want, float) else got == want
    check(name, ok, "%s → %r (期望 %r)" % (raw, got, want))

# 低切频率是 101 个**对数**档: 120 Hz 应落在某个档上且误差不超过半步 (~1.5%)
g = param_coerce("ch_hpf_f", 120.0)
check("低切频率按 101 对数档量化 (120→就近档, 误差<3Hz)",
      20.0 <= g <= 400.0 and abs(g - 120.0) < 3.0, "got %r" % g)
check("低切频率档位可往返 (再写一次值不变)",
      param_coerce("ch_hpf_f", g) == g, "%r → %r" % (g, param_coerce("ch_hpf_f", g)))

print("== A4. 通配符与地址匹配 (match_address) ==")
check("单地址 /ch/01/preamp/trim → 1 条",
      match_address("/ch/01/preamp/trim") == [("ch_trim", 1)])
check("通配 /ch/*/preamp/trim → 32 条", len(match_address("/ch/*/preamp/trim")) == 32)
check("通配 /ch/*/preamp/hpon → 32 条", len(match_address("/ch/*/preamp/hpon")) == 32)
check("通配 /ch/*/gate/thr → 32 条", len(match_address("/ch/*/gate/thr")) == 32)
check("越界编号无匹配 (/ch/99/...)", match_address("/ch/99/preamp/trim") == [])
check("旧行为: /bus/01/mix/fader 仍匹配",
      match_address("/bus/01/mix/fader") == [("bus_fader", 1)])
check("旧行为: /main/st/mix/fader 仍匹配 (无 %02d)",
      match_address("/main/st/mix/fader") == [("main_fader", 0)])
check("旧行为: /dca/03/fader 补零才匹配",
      match_address("/dca/03/fader") == [("dca_fader", 3)]
      and match_address("/dca/3/fader") == [])

print("== A5. 回包线上类型 (value_reply) ==")
for kind, want in (("ch_trim", float), ("ch_delay_ms", float), ("ch_hpf_f", float),
                   ("ch_gate_thr", float), ("ch_hpf_slope", int), ("ch_invert", int),
                   ("ch_on", int), ("main_fader", float), ("dca_on", int)):
    m = value_reply(kind, 1 if not kind.startswith("main_") else 0)
    check("回包 %s 发 %s" % (kind, want.__name__),
          m is not None and len(m.args) == 1 and isinstance(m.args[0], want)
          and not isinstance(m.args[0], bool),
          "%s -> %r" % (kind, m and m.args))

print("== A6. 卡片 set 动作清洗 (_clean_actions) ==")
a, dropped = _clean_actions([{"kind": "set",
                              "target": {"group": "ch", "param": "trim", "id": 2},
                              "value": 3.5}])
check("卡片 trim 3.5 不被 [0,1] 钳成 1 (静默改值 bug)",
      a and a[0]["value"] == 3.5, str(a))
a, _ = _clean_actions([{"kind": "set",
                        "target": {"group": "ch", "param": "hpf_slope", "id": 2},
                        "value": 20}])
check("卡片 hpf_slope 20 清洗成 18 (注意: 参数名是字段名, 不是 OSC 段名 hpslope)",
      a and a[0]["value"] == 18, str(a))
a, _ = _clean_actions([{"kind": "set",
                        "target": {"group": "ch", "param": "gate_thr", "id": 2},
                        "value": 5}])
check("卡片 gate_thr 5 清洗成 0 (按量程)", a and a[0]["value"] == 0.0, str(a))
a, dropped = _clean_actions([{"kind": "set",
                              "target": {"group": "ch", "param": "nope", "id": 1},
                              "value": 1}])
check("不支持的参数名被拒并给原因", not a and dropped, str(dropped))
a, dropped = _clean_actions([{"kind": "set",
                              "target": {"group": "main", "param": "trim", "id": 0},
                              "value": 1}])
check("main 无 preamp, trim 被拒 (不是静默忽略)", not a and dropped, str(dropped))
a, _ = _clean_actions([{"kind": "set",
                        "target": {"group": "main", "param": "fader", "id": 7},
                        "value": 0.5}])
check("main 组 id 归零 (只有一路)", a is not None and a[0]["target"]["id"] == 0, str(a))
a, _ = _clean_actions([{"kind": "set",
                        "target": {"group": "ch", "param": "fader", "id": 99},
                        "value": 0.5}])
check("ch id 99 按格式上限钳到 64 (范围对不对由执行期 target_exists 判断)",
      a is not None and a[0]["target"]["id"] == 64, str(a and a[0]["target"]))

print("== A7. 预设抓取与回归 (含脏数据/旧格式) ==")
set_value("ch_trim", 1, 3.5, source="action")
set_value("ch_hpf_on", 1, 1, source="action")
set_value("ch_trim", 2, -2.0, source="action")
bag = _snap_group_items_locked("ch", ["fader", "trim", "hpf_on"], [1, 2])
check("抓取只含范围内参数", set(bag) == {"1", "2"}
      and set(bag["1"]) == {"fader", "trim", "hpf_on"}, str(bag))
check("抓到的是落库后的值", abs(bag["1"]["trim"] - 3.5) < 1e-9 and bag["1"]["hpf_on"] == 1)
bag2 = _snap_group_items_locked("ch", ["fader"], [1, 99])
check("抓取跳过越界编号", set(bag2) == {"1"}, str(sorted(bag2)))

# 新格式 + 越界编号 + 类型脏数据: 不能炸回归线程 (曾经 KeyError 会炸掉整个 worker)
dirty = {"data": {"ch": {"1": {"trim": 1.5, "hpf_on": 1},
                         "99": {"trim": 9.9},          # 越界编号
                         "x": {"trim": 1.0}}}}          # 非数字键
try:
    _recall_instant(dirty)
    ok, why = abs(STATE.ch[1]["trim"] - 1.5) < 1e-9, "未应用"
except Exception as e:
    ok, why = False, repr(e)
check("回归容忍越界/脏键 (ch1 应用, 不抛异常)", ok, why)

# 旧格式预设 (顶层就是 ch, 没有 data/scope): 兼容, 且只动带了的键。
# 注意: 推子**不**在 _recall_instant 的职责里 —— 它走 _recall_body 的渐变
# 线程 (见 test_presets 组的端到端覆盖), 这里只验证瞬时项不被旧格式误伤。
set_value("ch_trim", 3, 7.0, source="action")
old = {"ch": {"3": {"fader": 0.5, "on": 0}}}
try:
    _recall_instant(old)
    ok = (STATE.ch[3]["on"] == 0
          and abs(STATE.ch[3]["trim"] - 7.0) < 1e-9)
    why = "trim 被旧格式误动"
except Exception as e:
    ok, why = False, repr(e)
check("旧格式预设兼容且不碰新参数", ok, why)

print("== A8. 前端控件元数据 (ui_specs) vs 登记表 ==")
# 以前这段是"从 app.js 里正则抠 CH_PARAM_UI 再逐项比对" —— 因为前端有**第二份**
# 控件表。现在前端不再抄表, 控件由 /api/paramspecs 生成, 所以这里改成两件事:
#   ① ui_specs() 必须如实反映登记表 (量程/档位/精度/枚举/默认值);
#   ② 端到端层再验 /api/paramspecs 与 ui_specs 一致 (序列化不丢字段)。
# 前端的量程正确性由"只有一份来源"保证, 不再靠比对两份手抄表。
from x32sim.params import ui_specs  # noqa: E402


def _us(group, field):
    for r in ui_specs(group):
        if r["f"] == field:
            return r
    return None


_bad = []
for _g, _specs in GROUP_PARAM_SPECS.items():
    if len(ui_specs(_g)) != len(_specs):
        _bad.append("%s 条目数 %d vs %d" % (_g, len(ui_specs(_g)), len(_specs)))
        continue
    for sp, r in zip(_specs, ui_specs(_g)):
        if (r["f"] != sp.field or r["l"] != sp.label or r["t"] != sp.vtype
                or abs(r["lo"] - sp.lo) > 1e-9 or abs(r["hi"] - sp.hi) > 1e-9
                or abs(r["st"] - sp.step) > 1e-9 or r["dec"] != sp.dec
                or r["u"] != sp.unit or r["lg"] != sp.logsteps
                or (r["lb"] or None) != (list(sp.labels) if sp.labels else None)
                or abs(r["dv"] - sp.default) > 1e-9):
            _bad.append("%s/%s" % (_g, sp.field))
check("每组 ui_specs 与登记表逐项一致", not _bad, str(_bad[:5]))
# 页签 (sec) 是**三端共用**的规矩: 网页/iOS/Android 都按它分页, 谁都不许自己猜。
# 这里锁死易错的几条: 声像必须在前置页、插入归前置、Gate/Dyn/EQ 各归各页。
_secs = {g: {r["f"]: r.get("sec") for r in ui_specs(g)}
         for g in GROUP_PARAM_SPECS}
check("每个参数都带合法页签 (pre/gate/dyn/eq)",
      all(v in ("pre", "gate", "dyn", "eq")
          for m in _secs.values() for v in m.values()),
      str([(g, f, v) for g, m in _secs.items() for f, v in m.items()
           if v not in ("pre", "gate", "dyn", "eq")][:5]))
check("声像/插入/前置归 pre, 门/压缩/EQ 各归各页",
      _secs["ch"]["pan"] == "pre" and _secs["ch"]["ins_on"] == "pre"
      and _secs["ch"]["gate_thr"] == "gate" and _secs["ch"]["dyn_thr"] == "dyn"
      and _secs["ch"]["eq1_g"] == "eq" and _secs["bus"]["eq6_g"] == "eq",
      str(_secs["ch"]))

print("== A9. 三端共用的参数表导出物 ==")
# 原生 App 直连真台只走 OSC, 参数元数据只能随包带 —— 导出物 (iOS Swift /
# Android assets / outputs JSON) 一旦落后于登记表, 就会出现"网页能调、App
# 调到的却是另一个参数"。所以这里把"导出物必须等于 build_payload()"锁进回归:
# 改了 params.py 忘记重跑导出 → 这一步直接红。
import subprocess  # noqa: E402
_r = subprocess.run([sys.executable, os.path.join("tools", "export_param_specs.py"), "--check"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace")
check("参数表导出物未过期 (改了 params.py 必须重跑导出)",
      _r.returncode == 0, ((_r.stdout or "") + (_r.stderr or "")).strip()[:300])

# 原生端的地址反查是"具体地址 → 参数"的字典: 两条参数撞同一个地址时,
# 后写的那条会把前一条从字典里挤掉 —— 表现为"某个控件怎么拖都不生效"。
# 模拟器这边是逐个分支匹配, 撞了也看不出来, 所以必须在这里静态查一遍。
_payload = json.loads(subprocess.run(
    [sys.executable, "-c",
     "import sys,json;sys.path.insert(0,'tools');import export_param_specs as e;"
     "print(json.dumps(e.build_payload(),ensure_ascii=False))"],
    capture_output=True, text=True, encoding="utf-8").stdout)
_dup = {}
for _g, _rows in _payload["specs"].items():
    _size = _payload["groups"][_g]["size"]
    for _row in _rows:
        for _i in range(1, _size + 1):
            _a = _row["a"] % _i if "%" in _row["a"] else _row["a"]
            _dup.setdefault(_a, []).append("%s/%s/%d" % (_g, _row["f"], _i))
_collide = {k: v for k, v in _dup.items() if len(v) > 1}
check("参数地址在原生端反查表里不撞号", not _collide, str(list(_collide.items())[:3]))
check("导出物覆盖 6 种通道类型且带 OSC 前缀 (fxret→/fxrtn)",
      set(_payload["groups"]) == {"ch", "bus", "auxin", "fxret", "mtx", "main"}
      and _payload["groups"]["fxret"]["osc"] == "/fxrtn"
      and _payload["groups"]["main"]["osc"] == "/main/st",
      str({g: c["osc"] for g, c in _payload["groups"].items()}))
check("前端能拿到 6 种通道的元数据 (ch/bus/auxin/fxret/mtx/main)",
      set(GROUP_PARAM_SPECS) == {"ch", "bus", "auxin", "fxret", "mtx", "main", "headamp"},
      str(sorted(GROUP_PARAM_SPECS)))
check("声像元数据带 -100..100 / 档位 2",
      _us("ch", "pan")["lo"] == -100.0 and _us("ch", "pan")["hi"] == 100.0
      and _us("ch", "pan")["st"] == 2.0)
check("EQ 类型元数据带 6 个枚举名",
      _us("bus", "eq6_type")["lb"] == ["LCut", "LShv", "PEQ", "VEQ", "HShv", "HCut"],
      str(_us("bus", "eq6_type")["lb"]))
check("压缩比元数据带 12 个比值 (不是索引)",
      _us("ch", "dyn_ratio")["en"] and len(_us("ch", "dyn_ratio")["en"]) == 12,
      str(_us("ch", "dyn_ratio")["en"]))

print("== A9. 二维发送 / 索引型枚举 / 反向对数 ==")
# dyn/ratio: 线上是 0..11 的索引, 本地存比值。两个方向都必须对得上, 否则
# "本地 4:1、真台 5:1"这种静默改值会一直藏着。
_rs = PARAM_SPECS["ch_dyn_ratio"]
check("ratio 本地 4.0 → 线上索引 6", param_wire(_rs, 4.0) == 6, str(param_wire(_rs, 4.0)))
check("ratio 线上索引 6 → 本地 4.0", param_from_wire(_rs, 6) == 4.0,
      str(param_from_wire(_rs, 6)))
check("ratio 索引全部可往返",
      all(param_wire(_rs, param_from_wire(_rs, i)) == i for i in range(12)))
check("ratio 非枚举值按最近档吸附 (6.5 → 7.0)", param_coerce("ch_dyn_ratio", 6.5) == 7.0,
      str(param_coerce("ch_dyn_ratio", 6.5)))
check("ratio 平局取小的一侧 (6.0 → 5.0, 只要确定就行)",
      param_coerce("ch_dyn_ratio", 6.0) == 5.0, str(param_coerce("ch_dyn_ratio", 6.0)))
check("ratio 越界索引按字面值处理 (99 → 100)", param_from_wire(_rs, 99) == 100.0,
      str(param_from_wire(_rs, 99)))
check("hpslope 仍是字面值枚举 (不走索引)",
      param_wire(PARAM_SPECS["ch_hpf_slope"], 18) == 18,
      str(param_wire(PARAM_SPECS["ch_hpf_slope"], 18)))

# EQ 的 Q 是 logf[10,0.3,72]: 递减表, 但 lo/hi 写自然上下界
_q = PARAM_SPECS["ch_eq1_q"]
check("EQ Q 量程是 0.3..10 (自然上下界)",
      _q.lo == 0.3 and _q.hi == 10.0 and _q.logdesc, "%r %r" % (_q.lo, _q.hi))
check("EQ Q 超上限钳到 10", param_coerce("ch_eq1_q", 50.0) == 10.0)
check("EQ Q 超下限钳到 0.3", abs(param_coerce("ch_eq1_q", 0.01) - 0.3) < 1e-9)
check("EQ Q 量化后可往返",
      param_coerce("ch_eq1_q", param_coerce("ch_eq1_q", 2.0))
      == param_coerce("ch_eq1_q", 2.0))
check("EQ 频率按 201 对数档 (20k 往返不变)",
      abs(param_coerce("ch_eq1_f", 20000.0) - 20000.0) < 1e-6)
check("EQ 增益按 0.25 dB 档", param_coerce("ch_eq1_g", 3.3) == 3.25,
      str(param_coerce("ch_eq1_g", 3.3)))
check("EQ 类型索引 0..5, 越界钳住",
      param_coerce("ch_eq1_type", -3) == 0 and param_coerce("ch_eq1_type", 9) == 5)

# 二维地址: 通配符要能同时作用于两个位置
check("单发送 /ch/01/mix/05/level → (send_level, pack(1,5))",
      match_address("/ch/01/mix/05/level") == [("send_level", send_pack(1, 5))],
      str(match_address("/ch/01/mix/05/level")))
check("通道通配 /ch/*/mix/03/on → 32 条",
      len(match_address("/ch/*/mix/03/on")) == 32)
# ⚠️ 守护用例: 取点地址末段是 **type**, 字段名却叫 **tap**。曾按字段名建索引,
#    于是 /ch/*/mix/*/type 一条都匹配不上 —— 不报错, 只回空包, 只能靠这里抓。
# 取点只对**奇数** Bus 定义 (协议表), 32 通道 × 8 对 = 256
check("双通配 /ch/*/mix/*/type → 256 条 (取点只在奇数 Bus)",
      len(match_address("/ch/*/mix/*/type")) == 256,
      str(len(match_address("/ch/*/mix/*/type"))))
check("发送电平仍是 32×16 = 512 条",
      len(match_address("/ch/*/mix/*/level")) == 512,
      str(len(match_address("/ch/*/mix/*/level"))))
check("取点单地址 → (send_tap, pack(2,11))",
      match_address("/ch/02/mix/11/type") == [("send_tap", send_pack(2, 11))],
      str(match_address("/ch/02/mix/11/type")))
# 取点枚举按官方协议 {<-EQ, EQ->, PRE, POST, GRP} —— 5 档, 索引 0..4
_tap = PARAM_SPECS["send_tap"]
check("取点枚举 5 档且是官方标签",
      _tap.labels == ("<-EQ", "EQ->", "PRE", "POST", "GRP") and _tap.hi == 4.0,
      str(_tap.labels))
check("取点越界按 4 (GRP) 钳", param_coerce("send_tap", 9) == 4,
      str(param_coerce("send_tap", 9)))
# 成对共享: Bus02 的取点就是 Bus01 的, 地址也落到 01
check("Bus02 取点地址落到 Bus01 (成对共享)",
      spec_addr(_tap, send_pack(3, 2)) == "/ch/03/mix/01/type",
      spec_addr(_tap, send_pack(3, 2)))
check("奇数 Bus 地址原样", spec_addr(_tap, send_pack(3, 5)) == "/ch/03/mix/05/type",
      spec_addr(_tap, send_pack(3, 5)))
set_value("send_tap", send_pack(4, 6), 2, source="action")
check("写 Bus06 的取点 = 写 Bus05 (同一份真相)",
      get_value("send_tap", send_pack(4, 6)) == 2
      and get_value("send_tap", send_pack(4, 5)) == 2,
      "%r %r" % (get_value("send_tap", send_pack(4, 6)),
                 get_value("send_tap", send_pack(4, 5))))
check("偶数 Bus 地址查询也能命中奇数那一格",
      match_address("/ch/04/mix/06/type") == [("send_tap", send_pack(4, 5))],
      str(match_address("/ch/04/mix/06/type")))

check("发送地址补零且是二维",
      spec_addr(PARAM_SPECS["send_level"], send_pack(3, 12)) == "/ch/03/mix/12/level",
      spec_addr(PARAM_SPECS["send_level"], send_pack(3, 12)))
check("旧行为未受影响: /ch/05/mix/fader 仍是通道推子",
      match_address("/ch/05/mix/fader") == [("ch_fader", 5)])

# 发送不进 /api/state (512 行会把状态包撑爆)
from x32sim.snapshot import snapshot_state  # noqa: E402
_sn = snapshot_state()
check("快照里没有 send 段 (512 行不进热路径)", "send" not in _sn,
      str([k for k in _sn if k == "send"]))
check("快照 ch 行已带动态/均衡字段",
      "dyn_thr" in _sn["ch"]["1"] and "eq3_q" in _sn["ch"]["1"])

print("== A9b. 侧链/插入目标 + 枚举双形式 + level 档位 ==")

# ---- 18 个新增参数: 地址能匹配、能读写、越界能钳 ----
_new_params = (
    ("ch_ins_sel", "/ch/01/insert/sel", 12, 12),
    ("ch_gate_mode", "/ch/01/gate/mode", 1, 1),
    ("ch_gate_attack", "/ch/01/gate/attack", 12.0, 12.0),
    ("ch_gate_hold", "/ch/01/gate/hold", 100.0, 100.0),
    ("ch_gate_release", "/ch/01/gate/release", 500.0, 500.0),
    ("ch_gate_keysrc", "/ch/01/gate/keysrc", 49, 49),
    ("ch_gate_filter_on", "/ch/01/gate/filter/on", 1, 1),
    ("ch_gate_filter_type", "/ch/01/gate/filter/type", 3, 3),
    ("ch_gate_filter_f", "/ch/01/gate/filter/f", 2000.0, 2000.0),
    ("ch_dyn_mode", "/ch/01/dyn/mode", 1, 1),
    ("ch_dyn_det", "/ch/01/dyn/det", 1, 1),
    ("ch_dyn_env", "/ch/01/dyn/env", 1, 1),
    ("ch_dyn_hold", "/ch/01/dyn/hold", 100.0, 100.0),
    ("ch_dyn_keysrc", "/ch/01/dyn/keysrc", 5, 5),
    ("ch_dyn_auto", "/ch/01/dyn/auto", 1, 1),
    ("ch_dyn_filter_on", "/ch/01/dyn/filter/on", 1, 1),
    ("ch_dyn_filter_type", "/ch/01/dyn/filter/type", 4, 4),
    ("ch_dyn_filter_f", "/ch/01/dyn/filter/f", 4000.0, 4000.0),
)
for _k, _a, _v, _want in _new_params:
    _m = match_address(_a)
    check("新参数 %s 地址可匹配 (%s)" % (_k, _a),
          bool(_m) and _m[0][0] == _k and _m[0][1] == 1, str(_m))
    set_value(_k, 1, _v)
    _got = get_value(_k, 1)
    # 对数档位参数 (hold/release/filter/f) 会被吸附到 101/201 点表上, 断言用
    # 量化后的期望值 —— 真台同样会吸附, 用原值比就是在测一个台面不存在的数。
    _exp = param_coerce(_k, _v)
    check("新参数 %s 写入 %.4g 读回 %.4g" % (_k, _v, _exp),
          _got is not None and abs(float(_got) - _exp) < 1e-6, str(_got))

# keysrc 是 int[0..64], 不是 enum: 名字只用于显示/宽容解析, 线上仍发整数
check("keysrc 名字 → 索引 (Bus05 = 53)",
      param_from_wire(PARAM_SPECS["ch_gate_keysrc"], "Bus05") == 53.0)
check("keysrc 线上发整数 (不发字符串)",
      param_wire(PARAM_SPECS["ch_gate_keysrc"], 53) == 53)
check("keysrc 越界 99 被钳到 64", get_value("ch_gate_keysrc", 2) is not None
      and param_coerce("ch_gate_keysrc", 99) == 64.0,
      str(param_coerce("ch_gate_keysrc", 99)))
check("insert/sel 越界 99 被钳到 22", param_coerce("ch_ins_sel", 99) == 22.0)
check("gate/filter/f 越界钳到 20k", param_coerce("ch_gate_filter_f", 99999) == 20000.0)

# ---- 枚举的线上双形式: 整数索引 / 名字符串 ----
check("取点字符串 'POST' → 索引 3",
      param_from_wire(PARAM_SPECS["send_tap"], "POST") == 3.0)
check("取点字符串 ' grp ' 大小写/空格容错 → 4",
      param_from_wire(PARAM_SPECS["send_tap"], " grp ") == 4.0)
check("取点字符串 'EQ->' → 1",
      param_from_wire(PARAM_SPECS["send_tap"], "EQ->") == 1.0)
check("插入位置字符串 'POST' → 1 (bool 型也认名字)",
      param_from_wire(PARAM_SPECS["ch_ins_pos"], "POST") == 1)
check("默认线上形式是整数索引 (取点 3 → 3)",
      param_wire(PARAM_SPECS["send_tap"], 3) == 3)
_style = PARAM_SPECS["send_tap"].enum_wire
PARAM_SPECS["send_tap"].enum_wire = "label"
check("切到 label 后线上发 'POST'", param_wire(PARAM_SPECS["send_tap"], 3) == "POST")
PARAM_SPECS["send_tap"].enum_wire = _style
check("切回后仍是整数", param_wire(PARAM_SPECS["send_tap"], 3) == 3)

# ---- level 曲线 (161 档伪对数) ----
for _f, _db in ((0.75, 0.0), (0.5, -10.0), (0.25, -30.0),
                (0.0625, -60.0), (1.0, 10.0), (0.0, -90.0)):
    check("level 曲线 f=%.4f → %+.1f dB" % (_f, _db),
          abs(level_db(_f) - _db) < 1e-9, str(level_db(_f)))
check("level 曲线可逆 (0.01/0.1/0.3/0.6/0.9/1.0)",
      all(abs(level_from_db(level_db(f)) - f) < 1e-9
          for f in (0.01, 0.1, 0.3, 0.6, 0.9, 1.0)))
check("发送电平 161 档: 0.42 → 67/160",
      abs(param_coerce("send_level", 0.42) - 67 / 160.0) < 1e-9,
      str(param_coerce("send_level", 0.42)))
check("发送电平端点不漂 (0.0 / 1.0)",
      param_coerce("send_level", 0.0) == 0.0 and param_coerce("send_level", 1.0) == 1.0)
import re  # noqa: E402  (仅用于校验仍是手写的 SEND_UI 档位)
_js = open(os.path.join(BASE, "web", "app.js"), encoding="utf-8").read()
_ui = re.search(r"\{f:'send_level'.*?\}", _js)
_step = float(re.search(r"step:([\d.]+)", _ui.group(0)).group(1))
check("前端发送电平档位 = 100/160 = 0.625%",
      abs(_step - 100.0 / 160.0) < 1e-9, str(_step))

# ---- 未标定清单: 假设值必须是机器可读的, 不能只写在注释里 ----
_unv = {sp.kind for sp in unverified_kinds()}
check("5 个待真机核对的出厂值都在未标定清单里",
      {"ch_hpf_f", "ch_hpf_slope", "ch_ins_pos",
       "ch_gate_thr", "ch_gate_range"} <= _unv, str(sorted(_unv)))
check("已核对过的项不在清单里 (trim / dyn_thr / eq 增益)",
      not ({"ch_trim", "ch_dyn_thr", "ch_eq1_g"} & _unv))
check("未标定项的数量与 note 里写'需真机确认'的一致",
      len(_unv) == sum(1 for sp in PARAM_SPECS.values()
                       if sp.unverified), str(len(_unv)))

print("== A10. 控台量程覆盖 (trim ±12 / ±18) ==")
# 手册写 [-12,12] (旧固件), 实测 1.15+ 是 [-18,18]。默认按 18, 老固件用
# --trim-range 12 收窄。收窄必须在**写入层**生效 —— 否则界面 15 dB、真台 12 dB。
from x32sim.params import active_limits, set_param_limits  # noqa: E402
check("默认量程 ±18 (现代固件)", param_coerce("ch_trim", 15) == 15.0
      and param_coerce("ch_trim", -15) == -15.0)
set_param_limits({"ch_trim": (-12.0, 12.0)})
check("收窄到 ±12 后越界值被钳", param_coerce("ch_trim", 15) == 12.0
      and param_coerce("ch_trim", -15) == -12.0,
      "%r %r" % (param_coerce("ch_trim", 15), param_coerce("ch_trim", -15)))
check("收窄后仍按 0.25 dB 档量化", param_coerce("ch_trim", 3.3) == 3.25)
check("收窄只作用于 trim, 不动别的参数",
      param_coerce("ch_eq1_g", 12.0) == 12.0
      and param_coerce("ch_dyn_mgain", 24.0) == 24.0)
check("active_limits 如实反映当前量程", active_limits("ch_trim") == (-12.0, 12.0))
# 状态层也走同一套: 写 15 落库就是 12, 界面不会显示台子做不到的值
set_value("ch_trim", 2, 15.0, source="action")
check("状态层同样被收窄 (写 15 落 12)", get_value("ch_trim", 2) == 12.0,
      str(get_value("ch_trim", 2)))
set_param_limits({})
check("恢复默认后重新接受 15", param_coerce("ch_trim", 15) == 15.0)

_persist.save_user_config = _orig_save

# ============================================================ B. 端到端层
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)
sock.bind(("127.0.0.1", 0))
from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402


def rpc(addr, *args, **kw):
    """发一条 OSC 并收一个包 (单消息或 bundle)。reply=False 用于 set ——
    真台对写入不回包, 发了就走, 不白等超时。"""
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


def get_json(path):
    """GET 一个小端点 (/api/sends 这类不进 /api/state 的按需数据)。"""
    try:
        return json.loads(urllib.request.urlopen(WEB + path, timeout=8).read())
    except urllib.error.HTTPError as e:
        return {"http": e.code}


def osc_get(addr):
    """无参查询 → 单值。返回 (地址, 值) 或 None。"""
    rs = rpc(addr)
    if len(rs) == 1 and rs[0].args:
        return rs[0].args[0]
    return None


FLOAT_TOL = 1e-4     # OSC 线上是 float32, 12.3 往返成 12.30000019 (与真台一致)
HPF_TOL = 3.0        # 低切频率是 101 个对数档, 半步约 1.5%

print("== B1. OSC 往返 (12 个新地址 set + get) ==  UDP %d / Web %s" % (OSC_PORT, WEB))
_rt = [
    ("/ch/01/preamp/trim", 3.5, FLOAT_TOL, "增益 trim"),
    ("/ch/02/preamp/invert", 1, 0, "反相 invert"),
    ("/ch/03/delay/on", 1, 0, "延时开关 delay/on"),
    ("/ch/04/delay/time", 12.3, FLOAT_TOL, "延时时间 delay/time"),
    ("/ch/05/preamp/hpon", 1, 0, "低切开关 hpon"),
    ("/ch/06/preamp/hpslope", 18, 0, "低切斜率 hpslope"),
    ("/ch/07/preamp/hpf", 120.0, HPF_TOL, "低切频率 hpf"),
    ("/ch/08/insert/on", 1, 0, "插入开关 insert/on"),
    ("/ch/09/insert/pos", 1, 0, "插入位置 insert/pos"),
    ("/ch/10/gate/on", 1, 0, "噪声门开关 gate/on"),
    ("/ch/11/gate/thr", -35.0, FLOAT_TOL, "门限 gate/thr"),
    ("/ch/12/gate/range", 20.0, FLOAT_TOL, "门范围 gate/range"),
]
for addr, val, tol, name in _rt:
    rpc(addr, val, reply=False)
    time.sleep(0.05)
    got = osc_get(addr)
    if tol == 0:
        ok = got is not None and abs(got - val) < 1e-9
    else:
        ok = got is not None and abs(got - val) <= tol
    check("OSC 往返 %s" % name, ok, "set %r → get %r" % (val, got))

print("== B2. OSC 越界钳制与量化 ==")
for addr, val, want, tol, name in (
        ("/ch/13/preamp/trim", 25.0, 18.0, 1e-4, "trim 25→18"),
        ("/ch/13/delay/time", 0.0, 0.3, 1e-4, "延时 0→0.3"),
        ("/ch/13/preamp/hpslope", 20, 18, 1e-9, "斜率 20→18"),
        ("/ch/13/gate/thr", 5.0, 0.0, 1e-4, "门限 5→0"),
        ("/ch/13/gate/range", 999.0, 60.0, 1e-4, "门范围 999→60")):
    rpc(addr, val, reply=False)
    time.sleep(0.05)
    got = osc_get(addr)
    check("OSC %s" % name, got is not None and abs(got - want) <= tol,
          "set %r → get %r (期望 %r)" % (val, got, want))

print("== B3. 通配符 bundle ==")
rs = rpc("/ch/*/preamp/trim")
check("通配 /ch/*/preamp/trim 回 32 条 bundle", len(rs) == 32, "got %d" % len(rs))
check("bundle 每条都是 float 且带补零地址",
      all(isinstance(m.args[0], float) and m.address.startswith("/ch/")
          and len(m.address.split("/")[2]) == 2 for m in rs))
rs = rpc("/ch/01/preamp/trim")
check("单地址查询回单消息 (不进 bundle)", len(rs) == 1)

print("== B4. /api/set (新参数) ==")
r = post("/api/set", {"kind": "ch", "param": "trim", "idx": 1, "value": 3.5})
s = state()
check("API 设 trim 3.5 生效", r.get("ok") and abs(s["ch"]["1"]["trim"] - 3.5) < 1e-9,
      str(r))
r = post("/api/set", {"kind": "ch", "param": "trim", "idx": 1, "value": "abc"})
check("API trim 传非数字 → 400", r.get("http") == 400, str(r))
r = post("/api/set", {"kind": "ch", "param": "trim", "idx": 99, "value": 1.0})
check("API 越界 idx → 400", r.get("http") == 400, str(r))
r = post("/api/set", {"kind": "ch", "param": "nope", "idx": 1, "value": 1.0})
check("API 未知参数 → 400", r.get("http") == 400, str(r))
r = post("/api/set", {"kind": "ch", "param": "hpf_slope", "idx": 2, "value": 20})
s = state()
check("API hpf_slope 20 吸附成 18", r.get("ok") and s["ch"]["2"]["hpf_slope"] == 18,
      "%s / %s" % (r, s["ch"]["2"]["hpf_slope"]))
r = post("/api/set", {"kind": "ch", "param": "invert", "idx": 3, "value": 1})
s = state()
check("API invert 置 1 (int)", r.get("ok") and s["ch"]["3"]["invert"] == 1)

print("== B5. /api/setbatch (新参数) ==")
r = post("/api/setbatch", {"sets": [
    {"kind": "ch", "param": "trim", "idx": 4, "value": -3.0},
    {"kind": "ch", "param": "hpf_on", "idx": 4, "value": 1},
    {"kind": "ch", "param": "gate_thr", "idx": 4, "value": -25.0},
    {"kind": "ch", "param": "trim", "idx": 99, "value": 1.0}]})
s = state()
check("setbatch applied=3 (越界条目被跳过)", r.get("applied") == 3, str(r))
check("setbatch 三条都落库",
      abs(s["ch"]["4"]["trim"] + 3.0) < 1e-9 and s["ch"]["4"]["hpf_on"] == 1
      and abs(s["ch"]["4"]["gate_thr"] + 25.0) < 1e-9, str(s["ch"]["4"]))

print("== B6. 卡片 set 动作 (存的值 = 执行的值) ==")
r = post("/api/cardadd", {"card": {"name": "ZZ参数卡", "actions": [
    {"kind": "set", "target": {"group": "ch", "param": "trim", "id": 5}, "value": 3.5},
    {"kind": "set", "target": {"group": "ch", "param": "hpf_slope", "id": 5}, "value": 18}]}})
cid = r.get("id")
s = state()
card = next((c for c in s["cards"] if c.get("id") == cid), None)
act_vals = {a["target"]["param"]: a["value"] for a in (card or {}).get("actions", [])}
check("卡片存的就是 3.5 (没被 [0,1] 钳)", act_vals.get("trim") == 3.5, str(act_vals))
check("卡片斜率存 18", act_vals.get("hpf_slope") == 18, str(act_vals))
post("/api/show", {"id": cid})
time.sleep(0.4)
s = state()
check("执行后 ch5 trim=3.5", abs(s["ch"]["5"]["trim"] - 3.5) < 1e-9, str(s["ch"]["5"]))
check("执行后 ch5 斜率=18", s["ch"]["5"]["hpf_slope"] == 18, str(s["ch"]["5"]))

print("== B7. 预设 scope (勾选新参数) ==")
# 先给范围内两路一个已知值, 预设抓的才是它 (默认 80 Hz 也能抓, 但分不清
# "抓对了" 和 "根本没抓")
post("/api/set", {"kind": "ch", "param": "trim", "idx": 1, "value": 3.5})
post("/api/set", {"kind": "ch", "param": "hpf_f", "idx": 2, "value": 200.0})
r = post("/api/snapshot", {"action": "save", "name": "ZZ参数预设",
                           "scope": {"groups": ["ch"], "params": ["trim", "hpf_f"],
                                     "indices": {"ch": [1, 2]}}})
pid = r.get("id")
check("保存返回 id 且 count=4 (2 路 × 2 参数)", r.get("ok") and r.get("count") == 4, str(r))
post("/api/set", {"kind": "ch", "param": "trim", "idx": 1, "value": -9.0})
post("/api/set", {"kind": "ch", "param": "hpf_f", "idx": 2, "value": 300.0})
r = post("/api/snapshot", {"action": "recall", "id": pid, "seconds": 0.5})
time.sleep(1.2)
s = state()
check("回归 trim 回预设值", abs(s["ch"]["1"]["trim"] - 3.5) < 1e-9, str(s["ch"]["1"]))
check("回归低切频率回预设值 (~200, 101 对数档)",
      abs(s["ch"]["2"]["hpf_f"] - 200.0) <= 4.0, str(s["ch"]["2"]["hpf_f"]))

print("== B8. /api/state 形状 ==")
s = state()
row = s["ch"]["1"]
check("ch 行 59 个字段 (推子/静音 + 57 处理参数)", len(row) == 59, str(len(row)))
check("on 是 int / fader 是 float (JSON 形状未变)",
      isinstance(row["on"], int) and not isinstance(row["on"], bool)
      and isinstance(row["fader"], float))
check("trim 是 float / 斜率是 int",
      isinstance(row["trim"], float) and isinstance(row["hpf_slope"], int))
# 非输入组在 /api/state 里只带"推子/静音/声像"三项 —— 45~50 个处理参数走
# /api/params 按需取, 否则任何一处改动都会让整包快照多出 26KB。
check("bus 行只带 fader/on/pan (处理参数不进快照)",
      sorted(s["bus"]["1"]) == ["fader", "on", "pan"], str(sorted(s["bus"]["1"])))
check("main 行同上 (无 %02d 那套)", sorted(s["main"]) == ["fader", "on", "pan"],
      str(sorted(s["main"])))
check("/api/params 能取回 bus 的完整处理参数",
      len((get_json("/api/params?group=bus&idx=1").get("params") or {})) == 47,
      str(len(get_json("/api/params?group=bus&idx=1").get("params") or {})))

print("== B8b. 动态 / 均衡 / 发送 (端到端) ==")
# 动态: ratio 线上是索引 —— 这是本轮最容易"看起来对、实际错一档"的地方
rpc("/ch/04/dyn/ratio", 6)
got = osc_get("/ch/04/dyn/ratio")
check("dyn/ratio 线上发 6 时回包也是 6 (索引不跑偏)", got == 6, str(got))
st = state()
check("dyn/ratio 本地存比值 4.0", abs(st["ch"]["4"]["dyn_ratio"] - 4.0) < 1e-9,
      str(st["ch"]["4"].get("dyn_ratio")))
r = post("/api/set", {"kind": "ch", "param": "dyn_ratio", "idx": 4, "value": 7.0})
st = state()
check("API 写 7.0 (比值) 后本地是 7.0",
      r.get("ok") and abs(st["ch"]["4"]["dyn_ratio"] - 7.0) < 1e-9,
      str(st["ch"]["4"].get("dyn_ratio")))
check("API 写 7.0 后线上是索引 8", osc_get("/ch/04/dyn/ratio") == 8,
      str(osc_get("/ch/04/dyn/ratio")))

rpc("/ch/04/dyn/thr", -18.3)
st = state()
check("dyn/thr 按 0.5 dB 档量化 (-18.3→-18.5)",
      abs(st["ch"]["4"]["dyn_thr"] + 18.5) < 1e-9, str(st["ch"]["4"].get("dyn_thr")))
rpc("/ch/04/dyn/on", 1)
check("dyn_on 开关写入", state()["ch"]["4"]["dyn_on"] == 1)

# 均衡: 5 段地址 + 反向 Q
rpc("/ch/04/eq/2/g", 3.3)
check("EQ 段落增益按 0.25 dB 档", abs(state()["ch"]["4"]["eq2_g"] - 3.25) < 1e-9,
      str(state()["ch"]["4"].get("eq2_g")))
check("EQ 段落增益回包地址正确", osc_get("/ch/04/eq/2/g") is not None)
rpc("/ch/04/eq/3/f", 1000)
f3 = state()["ch"]["4"]["eq3_f"]
check("EQ 频率 1k 附近 (对数档)", abs(f3 - 1000) / 1000 < 0.05, str(f3))
rpc("/ch/04/eq/1/q", 0.5)
q1 = state()["ch"]["4"]["eq1_q"]
check("EQ Q 0.5 附近 (递减对数表)", abs(q1 - 0.5) < 0.06, str(q1))
rpc("/ch/04/eq/on", 1)
check("EQ 总开关", state()["ch"]["4"]["eq_on"] == 1)

# 发送: 二维目标, 走 POST 的 bus 字段 + 独立 GET
# 发送电平是 level 类型 161 档: 0.42 会被量化到最近的 67/160 = 0.41875。
# 断言用**量化后**的值, 否则测的就不是台面真实值 (真台同样会吸附)。
LEV42 = 67 / 160.0
LEV66 = 106 / 160.0
r = post("/api/set", {"kind": "send", "param": "send_level", "idx": 6, "bus": 3,
                      "value": 0.42})
check("API 写发送电平 CH06→Bus03", r.get("ok"), str(r))
r = post("/api/set", {"kind": "send", "param": "send_on", "idx": 6, "bus": 3, "value": 1})
check("API 写发送开关", r.get("ok"), str(r))
r = post("/api/set", {"kind": "send", "param": "send_tap", "idx": 6, "bus": 3, "value": 2})
check("API 写发送取点", r.get("ok"), str(r))
r = post("/api/set", {"kind": "send", "param": "send_level", "idx": 6, "bus": 99,
                      "value": 0.5})
check("Bus 越界给 400 而不是静默跳过", r.get("ok") is False, str(r))
sd = get_json("/api/sends?ch=6")
row3 = ((sd.get("sends") or {}).get("3") or {})
check("GET /api/sends 回 CH06→Bus03 的三个值",
      abs((row3.get("level") or 0) - LEV42) < 1e-9 and row3.get("on") == 1
      and row3.get("tap") == 2, str(row3))
check("GET /api/sends 回满 16 个 Bus", len(sd.get("sends") or {}) == 16,
      str(len(sd.get("sends") or {})))
check("OSC 5 段地址与 API 写的是同一个格",
      osc_get("/ch/06/mix/03/level") is not None
      and abs(osc_get("/ch/06/mix/03/level") - LEV42) < 1e-4,   # 线上 float32
      str(osc_get("/ch/06/mix/03/level")))
check("OSC 读发送取点: type 地址就是 tap 那一格",
      osc_get("/ch/06/mix/03/type") == 2, str(osc_get("/ch/06/mix/03/type")))
rpc("/ch/06/mix/04/level", 0.25)
sd = get_json("/api/sends?ch=6")
check("OSC 写 CH06→Bus04 后 API 能看到",
      abs(((sd.get("sends") or {}).get("4") or {}).get("level", 0) - 0.25) < 1e-9,
      str((sd.get("sends") or {}).get("4")))

# 卡片: 发送目标带 bus
r = post("/api/cardadd", {"card": {"name": "ZZ发送卡", "actions": [
    {"kind": "set", "target": {"group": "send", "param": "send_level",
                               "id": 7, "bus": 5}, "value": 0.66}]}})
cid2 = r.get("id")
card2 = next((c for c in state()["cards"] if c.get("id") == cid2), None)
tg = ((card2 or {}).get("actions") or [{}])[0].get("target") or {}
check("卡片保留 (id=通道, bus=Bus) 而不是打包值",
      tg.get("id") == 7 and tg.get("bus") == 5 and abs(
          ((card2 or {}).get("actions") or [{}])[0].get("value", 0) - LEV66) < 1e-9,
      str(tg))
post("/api/show", {"id": cid2})
time.sleep(0.4)
sd = get_json("/api/sends?ch=7")
check("执行后 CH07→Bus05 电平 0.6625 (161 档量化)",
      abs(((sd.get("sends") or {}).get("5") or {}).get("level", 0) - LEV66) < 1e-9,
      str((sd.get("sends") or {}).get("5")))

# 预设: 发送按通道抓取, 只还原范围内的
r = post("/api/snapshot", {"action": "save", "name": "ZZ发送预设",
                           "scope": {"groups": ["send"], "params": ["send_level"],
                                     "indices": {"send": [6]}}})
sid = r.get("id")
check("发送预设按通道展开 16 个 Bus", r.get("ok") and r.get("count") == 16, str(r))
post("/api/set", {"kind": "send", "param": "send_level", "idx": 6, "bus": 3, "value": 0.9})
r = post("/api/snapshot", {"action": "recall", "id": sid, "seconds": 0.4})
time.sleep(0.8)
sd = get_json("/api/sends?ch=6")
check("回归把发送电平还原成 0.41875 (161 档量化)",
      abs(((sd.get("sends") or {}).get("3") or {}).get("level", 0) - LEV42) < 1e-9,
      str((sd.get("sends") or {}).get("3")))

print("== B9. 旧行为未回归 ==")
rpc("/main/st/mix/fader", 0.8, reply=False)
got = osc_get("/main/st/mix/fader")
check("main 推子地址无 %02d 仍可往返", got is not None and abs(got - 0.8) < 1e-4, str(got))
rpc("/dca/03/fader", 0.6, reply=False)
got = osc_get("/dca/03/fader")
check("DCA 补零地址仍可往返", got is not None and abs(got - 0.6) < 1e-4, str(got))
got = osc_get("/fx/1/mix")
check("FX 混合比仍是 float 回包",
      got is not None and isinstance(got, float), str(got))
rpc("/ch/09/mix/on", 0, reply=False)
got = osc_get("/ch/09/mix/on")
check("ch09 静音 int 回包", got == 0 and isinstance(got, int), str(got))
rpc("/ch/09/mix/on", 1, reply=False)

# ---------- 清理 ----------
# 按前缀清, 别写死名单: 上轮残留 + 本轮新增都得走同一条路径, 否则重跑一次
# 就多一张僵尸卡, "清理完成"永远红。
for _ in range(20):
    c = next((c for c in state()["cards"] if c["name"].startswith("ZZ")), None)
    if not c:
        break
    post("/api/carddel", {"id": c["id"]})
for sp in [x for x in state()["snapshots"] if x["name"].startswith("ZZ")]:
    post("/api/snapshot", {"action": "delete", "id": sp["id"]})
left = [c["name"] for c in state()["cards"] if c["name"].startswith("ZZ")]
check("清理完成", not left, str(left))

print("\n== C1. 扫码入口 /qr.svg ==")


def get_raw(path):
    try:
        r = urllib.request.urlopen(WEB + path, timeout=5)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


code, body = get_raw("/qr.svg?u=" + urllib.parse.quote("http://192.168.1.5:8080/", safe=""))
check("扫码接口返回 200 + SVG", code == 200 and body.startswith("<svg") and "viewBox" in body,
      "%s %s" % (code, body[:60]))
check("扫码 SVG 里带白底（保证深色终端/深色页面也能扫）", 'fill="#ffffff"' in body)
code2, body2 = get_raw("/qr.svg")
check("不带 u 参数时回落到本机地址", code2 == 200 and "<svg" in body2, "%s %s" % (code2, body2[:60]))
code3, body3 = get_raw("/qr.svg?u=" + urllib.parse.quote("javascript:alert(1)", safe=""))
check("非 http(s) 地址被拒（400）", code3 == 400, "%s %s" % (code3, body3[:40]))
code4, body4 = get_raw("/qr.svg?u=" + "http://" + "a" * 210)
check("超长地址被拒（400）", code4 == 400, "%s %s" % (code4, body4[:40]))

# 配对码：网页二维码里是 HTTP 端口，App 要的是 OSC(UDP) 端口 —— 两张码不能混
from x32sim.qr import svg as _qr_svg  # noqa: E402
code5, body5 = get_raw("/pair.svg?h=192.168.1.5&o=10025&w=8090")
check("配对码接口返回 200 + SVG", code5 == 200 and body5.startswith("<svg"), "%s %s" % (code5, body5[:40]))
_want = "x32remote://pair?v=1&host=192.168.1.5&osc=10025&web=8090"
check("配对码二维码 = 配对码原文（逐字节）", body5 == _qr_svg(_want),
      "期望 %d 字节, 实得 %d" % (len(_qr_svg(_want)), len(body5)))
_info = json.loads(get_raw("/api/info")[1])
_pc = _info.get("pairCode", "")
check("/api/info 带配对码（含 osc/web 端口）",
      _pc.startswith("x32remote://pair?v=1&host=") and "&osc=" in _pc and "&web=" in _pc, _pc)
check("配对码端口与 info 的 udp/web 一致",
      ("&osc=%d" % _info["udpPort"]) in _pc and ("&web=%d" % _info["webPort"]) in _pc, _pc)
code6, _ = get_raw("/pair.svg?h=127.0.0.1")
check("配对码拒绝回环地址（400）", code6 == 400, str(code6))
code7, _ = get_raw("/pair.svg?o=70000")
check("配对码拒绝非法端口（400）", code7 == 400, str(code7))

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for _n, _name, _detail in failed:
    print("失败项 #%d: %s%s" % (_n, _name, (" | " + _detail) if _detail else ""))
raise SystemExit(1 if fails else 0)
