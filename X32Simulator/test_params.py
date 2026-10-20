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
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

WEB = "http://127.0.0.1:28080"
OSC_PORT = int(os.environ.get("X32_TEST_PORT", "10023"))

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + detail) if detail and not ok else ""))


# ============================================================ A. 单元层
# 打桩持久化: 本进程的 STATE/SNAPSHOTS 是全新实例, 真写盘会覆盖正在运行的
# 模拟器配置 (presets/cards 全被清成空) —— 单元层一个字节都不该落盘。
import x32sim.persist as _persist
_orig_save = _persist.save_user_config
_persist.save_user_config = lambda: True

from x32sim.actions import _clean_actions            # noqa: E402
from x32sim.params import (CH_EXTRA_ADDR, CH_PARAM_SPECS, PARAM_SPECS,  # noqa: E402
                           blank_row, param_coerce, spec_addr)
from x32sim.presets import _recall_instant, _snap_group_items_locked  # noqa: E402
from x32sim.state import STATE, match_address, set_value, value_reply  # noqa: E402

print("== A1. 登记表自洽 ==")
check("登记表 28 项 (16 既有 + 12 新)", len(PARAM_SPECS) == 28, str(len(PARAM_SPECS)))
check("新增通道参数 12 项", len(CH_PARAM_SPECS) == 12, str(len(CH_PARAM_SPECS)))
kinds = [sp.kind for sp in PARAM_SPECS.values()]
check("kind 无重复", len(kinds) == len(set(kinds)))
main_specs = [sp for sp in PARAM_SPECS.values() if sp.group == "main"]
check("main 系列模板不带 %02d", all("%02d" not in sp.osc for sp in main_specs))
check("spec_addr(main_fader,0) 直接给地址",
      spec_addr(PARAM_SPECS["main_fader"], 0) == "/main/st/mix/fader",
      spec_addr(PARAM_SPECS["main_fader"], 0))
check("spec_addr(ch_trim,7) 补零",
      spec_addr(PARAM_SPECS["ch_trim"], 7) == "/ch/07/preamp/trim")
ch_addrs = {(sp.osc % 1) for sp in CH_PARAM_SPECS}
check("12 个新地址互不重复", len(ch_addrs) == 12, str(len(ch_addrs)))
check("地址索引覆盖 12 个 (seg2,seg3)",
      sum(len(v) for v in CH_EXTRA_ADDR.values()) == 12)

print("== A2. 出厂行 (blank_row) ==")
row = blank_row("ch")
check("ch 行 14 个字段 (推子/静音 + 12 参数)", len(row) == 14, str(sorted(row)))
check("推子出厂 0.75 / 静音出厂 1", row["fader"] == 0.75 and row["on"] == 1)
check("增益出厂 0 dB / 门限出厂 -40 dB / 门范围 15 dB",
      row["trim"] == 0.0 and row["gate_thr"] == -40.0 and row["gate_range"] == 15.0)
check("低切频率出厂 80 Hz (需真机确认项有 note)",
      row["hpf_f"] == 80.0 and "真机" in PARAM_SPECS["ch_hpf_f"].note)
check("开关默认值是 int 不是 float (JSON 形状)",
      isinstance(row["on"], int) and isinstance(row["invert"], int))
check("低切斜率默认 12 (int)", row["hpf_slope"] == 12 and isinstance(row["hpf_slope"], int))
check("bus 行只有推子/静音 (新参数仅 ch)", len(blank_row("bus")) == 2)

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

print("== A8. 前端控件表 vs 后端登记表 (防漂移) ==")
# web/app.js 的 CH_PARAM_UI 是"第二份量程表" —— 界面滑块拖到 400、后端只认 20–400
# 这种事不会报错, 只会让显示值和真实值悄悄分家。这里逐项比对 (格式变了就红,
# 逼着一起更新, 而不是让两边各说各话)。
import re  # noqa: E402

_js = open(os.path.join(BASE, "web", "app.js"), encoding="utf-8").read()
_m = re.search(r"const CH_PARAM_UI=\[(.*?)\n\];", _js, re.S)
_block = _m.group(1) if _m else ""
_entries = {}
for _part in _block.split("{f:'")[1:]:
    _entries[_part.split("'", 1)[0]] = _part
check("前端控件表解析到 12 项", len(_entries) == 12, str(sorted(_entries)))
check("字段名与后端完全一致且同序",
      re.findall(r"\{f:'([a-z_]+)'", _block) == [sp.field for sp in CH_PARAM_SPECS],
      str(list(_entries)))


def _jnum(key, part):
    _mm = re.search(key + r":(-?[\d.]+)", part)
    return float(_mm.group(1)) if _mm else None


for sp in CH_PARAM_SPECS:
    part = _entries.get(sp.field, "")
    if sp.enum is not None:
        opts = [float(x) for x in re.findall(r"\[(-?[\d.]+),'", part)]
        check("前端 %s 枚举档位与后端一致" % sp.field,
              opts == [float(e) for e in sp.enum], "%s vs %s" % (opts, list(sp.enum)))
    elif sp.vtype == "float":
        lo, hi = _jnum("lo", part), _jnum("hi", part)
        check("前端 %s 量程与后端一致 [%g, %g]" % (sp.field, sp.lo, sp.hi),
              lo is not None and hi is not None
              and abs(lo - sp.lo) < 1e-9 and abs(hi - sp.hi) < 1e-9,
              "lo=%r hi=%r" % (lo, hi))
        if sp.step:
            st = _jnum("step", part)
            check("前端 %s 档位与后端一致 (%g)" % (sp.field, sp.step),
                  st is not None and abs(st - sp.step) < 1e-9, str(st))
        dv = _jnum("dv", part)
        check("前端 %s 默认值与后端一致 (%g)" % (sp.field, sp.default),
              dv is not None and abs(dv - sp.default) < 1e-9, str(dv))
    else:
        # bool 型: 要么两态按钮, 要么 0/1 两档下拉 —— ins_pos 用 PRE/POST 下拉
        # 更可读 (后端仍按 bool 存 0/1), 所以两种形态都算对。
        two = ([float(v) for v in re.findall(r"\[(-?[\d.]+),'", part)] == [0.0, 1.0])
        check("前端 %s 是两态控件 (bool 或 0/1 枚举)" % sp.field,
              "t:'bool'" in part or two, part[:60])

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


def osc_get(addr):
    """无参查询 → 单值。返回 (地址, 值) 或 None。"""
    rs = rpc(addr)
    if len(rs) == 1 and rs[0].args:
        return rs[0].args[0]
    return None


FLOAT_TOL = 1e-4     # OSC 线上是 float32, 12.3 往返成 12.30000019 (与真台一致)
HPF_TOL = 3.0        # 低切频率是 101 个对数档, 半步约 1.5%

print("== B1. OSC 往返 (12 个新地址 set + get) ==")
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
check("ch 行 14 个字段", len(row) == 14, str(sorted(row)))
check("on 是 int / fader 是 float (JSON 形状未变)",
      isinstance(row["on"], int) and not isinstance(row["on"], bool)
      and isinstance(row["fader"], float))
check("trim 是 float / 斜率是 int",
      isinstance(row["trim"], float) and isinstance(row["hpf_slope"], int))
check("bus 行仍只有 2 个字段", len(s["bus"]["1"]) == 2, str(sorted(s["bus"]["1"])))
check("main 行只有推子/静音 (无 %02d 那套)", len(s["main"]) == 2, str(sorted(s["main"])))

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
for nm in ("ZZ参数卡",):
    c = next((c for c in state()["cards"] if c["name"] == nm), None)
    if c:
        post("/api/carddel", {"id": c["id"]})
for sp in [x for x in state()["snapshots"] if x["name"].startswith("ZZ")]:
    post("/api/snapshot", {"action": "delete", "id": sp["id"]})
left = [c["name"] for c in state()["cards"] if c["name"].startswith("ZZ")]
check("清理完成", not left, str(left))

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for i, ok in enumerate(results):
    if not ok:
        print("失败项 #%d" % (i + 1))
raise SystemExit(1 if fails else 0)
