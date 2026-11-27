# -*- coding: utf-8 -*-
"""WING 控台驱动回归 (P1: 基础控制 + 算法选择)。

为什么单开一组：
  WING 与 X32 有**三处语义反向/不同量纲**的差异 —— 编号不补零、推子是 dB、
  静音 1=muted。任何一处搞反，症状都一样：**界面正常、日志干净、台子没反应
  或者响错**。这类错误抓包很难覆盖全（它只在 20ms 合并器刷新时跑到），
  所以把换算抽成纯函数在这里逐条钉死。

分四段：
  A 地址映射（不补零 / main 偏移 / FX 叶子 / 不支持项必须 None）
  B 值转换（dB 双向 / 静音反向 / X32 行为未变）
  C 效果库（27 项映射 / 枚举合法性 / 反查唯一）
  D 桥接换算出口 _wire_arg（fx_mix ×100 / fx_type → mdl / 未知算法拒发）
  E 切控台后的回包形态（推子回 dB、算法回 mdl、干湿回 0..100）

全程进程内，不起服务、不占端口；save_user_config 打桩，避免把测试状态
写进正在运行的实例配置（这是本项目踩过的坑）。
"""
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import x32sim.persist as _persist                                  # noqa: E402

_persist.save_user_config = lambda *a, **k: None                   # noqa: E402

from x32sim.bridge import _wire_arg                                 # noqa: E402
from x32sim.fxlib import (WING_FX_ALGOS, WING_MDL, WING_MDL_LIST,   # noqa: E402
                          X32_FX_ALGOS, algorithm_for_wing_mdl, wing_mdl)
from x32sim.params import (PARAM_SPECS, WING_DB_SILENT, fader_from_db,   # noqa: E402
                           fader_to_db, param_from_wire, param_wire)
from x32sim.profiles import CONSOLE_PROFILES, profile, set_profile   # noqa: E402
from x32sim.state import STATE, set_value, value_reply               # noqa: E402

X = CONSOLE_PROFILES["x32"]
W = CONSOLE_PROFILES["wing"]

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name
          + (("  | " + detail) if detail and not ok else ""))


print("=== A 地址映射 ===")
# 编号一律不补零：WING 文档所有示例都是 /ch/1/…，补零会 NODE NOT FOUND
check("ch_fader(1) → /ch/1/fdr（不补零）", W.addr("ch_fader", 1) == "/ch/1/fdr",
      str(W.addr("ch_fader", 1)))
check("ch_on(12) → /ch/12/mute", W.addr("ch_on", 12) == "/ch/12/mute")
check("ch_pan(3) → /ch/3/pan", W.addr("ch_pan", 3) == "/ch/3/pan")
check("bus_fader(16) → /bus/16/fdr", W.addr("bus_fader", 16) == "/bus/16/fdr")
check("auxin_fader(3) → /aux/3/fdr（组名改 aux）",
      W.addr("auxin_fader", 3) == "/aux/3/fdr")
check("mtx_on(6) → /mtx/6/mute", W.addr("mtx_on", 6) == "/mtx/6/mute")
check("dca_fader(8) → /dca/8/fdr", W.addr("dca_fader", 8) == "/dca/8/fdr")
# 本地 main 只有一路且 idx 恒 0，WING 的 main 是 1..4 → 地址要 +1
check("main_fader(0) → /main/1/fdr（本地 0 基 → 协议 1 基）",
      W.addr("main_fader", 0) == "/main/1/fdr", str(W.addr("main_fader", 0)))
check("main_on(0) → /main/1/mute", W.addr("main_on", 0) == "/main/1/mute")
check("fx_type(2) → /fx/2/mdl", W.addr("fx_type", 2) == "/fx/2/mdl")
check("fx_mix(2) → /fx/2/fxmix", W.addr("fx_mix", 2) == "/fx/2/fxmix")
# WING 没有这些组/参数 —— 必须 None，绝不能编一个"看着像"的地址
for _k, _i in (("fxret_fader", 1), ("usb_fader", 1), ("ch_trim", 1),
               ("ch_dca", 1), ("ch_eq_on", 1), ("send_level", 1),
               ("fx_name", 1), ("fx_on", 1)):
    check("%s 未标定 → None（不猜地址）" % _k, W.addr(_k, _i) is None,
          str(W.addr(_k, _i)))
# fx_par 是**条件支持**：名字取决于该槽当前算法，故在 C2 段按算法断言
check("空槽的 fx_par_01 → None", W.addr("fx_par_01", 99) is None,
      str(W.addr("fx_par_01", 99)))
check("X32 地址未变（/ch/01/mix/fader）",
      X.addr("ch_fader", 1) == "/ch/01/mix/fader", str(X.addr("ch_fader", 1)))
check("X32 main 地址未变（/main/st/mix/fader）",
      X.addr("main_fader", 0) == "/main/st/mix/fader")
check("pending 里仍列出 fx_par_NN", "fx_par_NN" in W.pending())

print("\n=== B 值转换 ===")
_F, _ON = PARAM_SPECS["ch_fader"], PARAM_SPECS["ch_on"]
check("WING 推子 0.75 → 0.0 dB（文档实测锚点）",
      param_wire(_F, 0.75, W) == 0.0, str(param_wire(_F, 0.75, W)))
check("WING 推子 0.5 → -10.0 dB", param_wire(_F, 0.5, W) == -10.0,
      str(param_wire(_F, 0.5, W)))
check("WING 推子 1.0 → +10.0 dB", param_wire(_F, 1.0, W) == 10.0)
check("WING 推子 0（-∞）→ -144", param_wire(_F, 0.0, W) == WING_DB_SILENT,
      str(param_wire(_F, 0.0, W)))
check("WING 推子回读 -10 → 0.5", param_from_wire(_F, -10.0, W) == 0.5,
      str(param_from_wire(_F, -10.0, W)))
check("WING 推子回读 -144 → 0", param_from_wire(_F, -144, W) == 0.0)
check("WING 推子 dB 往返误差 ≤ 0.01",
      all(abs(fader_from_db(fader_to_db(f / 100.0)) - f / 100.0) <= 0.01
          for f in range(1, 101)))
check("WING 静音：本地开(1) → 线上 0（不静音）",
      param_wire(_ON, 1, W) == 0, str(param_wire(_ON, 1, W)))
check("WING 静音：本地关(0) → 线上 1（静音）",
      param_wire(_ON, 0, W) == 1, str(param_wire(_ON, 0, W)))
check("WING 静音回读 1 → 本地 0", param_from_wire(_ON, 1, W) == 0)
check("WING 静音回读 \"1\"（字符串）→ 本地 0",
      param_from_wire(_ON, "1", W) == 0)
check("WING 静音往返一致",
      all(param_from_wire(_ON, param_wire(_ON, v, W), W) == v for v in (0, 1)))
check("X32 推子 0.75 → 0.75（未受 WING 影响）",
      param_wire(_F, 0.75, X) == 0.75, str(param_wire(_F, 0.75, X)))
check("X32 静音 1 → 1（/mix/on 语义未变）", param_wire(_ON, 1, X) == 1)
check("X32 不传 prof 时行为不变（兼容旧调用）",
      param_wire(_F, 0.75) == 0.75 and param_wire(_ON, 1) == 1)
# 只该影响"组级"推子/静音，不能误伤通道处理参数里的 on
check("ch_hpf_on 不被当作组级静音取反",
      param_wire(PARAM_SPECS["ch_hpf_on"], 1, W) == 1)
check("ch_gate_on 不被当作组级静音取反",
      param_wire(PARAM_SPECS["ch_gate_on"], 1, W) == 1)

print("\n=== C 效果库 ===")
check("WING 库 = 27 种（X32 库里语义能对上的）", len(WING_FX_ALGOS) == 27,
      str(len(WING_FX_ALGOS)))
check("WING 库每项都带 wing_mdl",
      all(v.get("wing_mdl") for v in WING_FX_ALGOS.values()))
check("WING 库是 X32 库的子集（内部 key 不变）",
      set(WING_FX_ALGOS) <= set(X32_FX_ALGOS))
check("mdl 值全部在官方 mdl list 内",
      all(m in WING_MDL_LIST for m in WING_MDL.values()),
      str([m for m in WING_MDL.values() if m not in WING_MDL_LIST]))
check("mdl 值无重复（反查唯一）",
      len(set(WING_MDL.values())) == len(WING_MDL))
check("反查表往返一致",
      all(algorithm_for_wing_mdl(m) == k for k, m in WING_MDL.items()))
check("mdl 大小写不敏感（真台回大写）",
      algorithm_for_wing_mdl("chorus") == "CRS"
      and algorithm_for_wing_mdl("CHORUS") == "CRS")
check("wing_mdl('CRS') → CHORUS", wing_mdl("CRS") == "CHORUS")
check("X32 独有算法在 WING 下缺席（TEQ）", wing_mdl("TEQ") is None)
check("WING 的 note 已写明未上真台", "待标定" in W.note or "未" in W.note)
check("WING 标为 ready 但未 verified", W.ready and not W.verified)
check("WING 支持 fx_par（仅限已标定名字）", "fx_par" in W.caps)

print("\n=== D 桥接换算出口 ===")
check("_wire_arg fx_mix 0.5 → 50（0..100）",
      _wire_arg("fx_mix", 0.5, W) == 50, str(_wire_arg("fx_mix", 0.5, W)))
check("_wire_arg fx_mix 0 → 0", _wire_arg("fx_mix", 0.0, W) == 0)
check("_wire_arg fx_type CRS → CHORUS",
      _wire_arg("fx_type", "CRS", W) == "CHORUS",
      str(_wire_arg("fx_type", "CRS", W)))
check("_wire_arg fx_type 未知算法（TEQ）→ None 拒发",
      _wire_arg("fx_type", "TEQ", W) is None)
check("_wire_arg ch_fader 0.75 → 0.0", _wire_arg("ch_fader", 0.75, W) == 0.0)
check("_wire_arg ch_on 1 → 0", _wire_arg("ch_on", 1, W) == 0)
check("_wire_arg X32 fx_type 原样（HALL）",
      _wire_arg("fx_type", "HALL", X) == "HALL")
check("_wire_arg X32 fx_mix 不缩放",
      _wire_arg("fx_mix", 0.5, X) == 0.5, str(_wire_arg("fx_mix", 0.5, X)))

print("\n=== C2 WING 命名参数（P2）===")
from x32sim import wingfx                                             # noqa: E402
from x32sim.fx import fx_state                                        # noqa: E402

check("ROOM 表覆盖 15 项（文档原文）", len(wingfx.wing_params("ROOM")) == 15,
      str(len(wingfx.wing_params("ROOM"))))
check("ROOM par01 → pdel", wingfx.wing_par_name("ROOM", 1) == "pdel")
check("ROOM par02 → dcy", wingfx.wing_par_name("ROOM", 2) == "dcy")
check("ROOM par16 → efr", wingfx.wing_par_name("ROOM", 16) == "efr")
check("ROOM 的 level（X32 par06）在 WING 无对应 → None",
      wingfx.wing_par_name("ROOM", 6) is None)
check("DLY par02 → time（社区实测）", wingfx.wing_par_name("DLY", 2) == "time")
check("CRS 无可信名字 → None（不猜）", wingfx.wing_par_name("CRS", 1) is None)
check("越界参数号 → None", wingfx.wing_par_name("ROOM", 99) is None)
check("未装载算法 → None", wingfx.wing_par_name("", 1) is None)
check("证据分级里有 doc 与 inferred",
      set(wingfx.wing_par_sources()["ROOM"].values()) == {"doc"}
      and "inferred" in set(wingfx.wing_par_sources()["HALL"].values()),
      str(wingfx.wing_par_sources()["HALL"]))

print("\n=== D2 节点批量写（P4，默认关）===")
from x32sim.bridge import (WING_NODE_WRITE, _node_path, _node_value,   # noqa: E402
                           _send_node_batch)
from x32sim.osc import decode_packet                                   # noqa: E402


class _FakeSock(object):
    def __init__(self):
        self.sent = []

    def sendto(self, data, addr):
        self.sent.append(data)


check("节点路径点分隔", _node_path("/ch/1/fdr") == "/ch.1.fdr", _node_path("/ch/1/fdr"))
check("节点路径去空段", _node_path("/fx/2/mdl") == "/fx.2.mdl")
check("节点值整数不带小数点", _node_value(0.0) == "0", _node_value(0.0))
check("节点值负小数", _node_value(-10.5) == "-10.5", _node_value(-10.5))
check("节点值字符串原样", _node_value("CHORUS") == "CHORUS")
_fs = _FakeSock()
_send_node_batch(_fs, [("/ch/1/fdr", 0.0, 0.05, False),
                       ("/ch/2/fdr", -10.0, 0.05, False)], ("127.0.0.1", 1))
_msgs = decode_packet(_fs.sent[0]) if _fs.sent else []
check("批量写发一条 / ,s", len(_fs.sent) == 1 and len(_msgs) == 1,
      str(len(_fs.sent)))
check("批量写载荷是节点串",
      bool(_msgs) and _msgs[0].args[0] == "/ch.1.fdr=0,/ch.2.fdr=-10",
      _msgs[0].args[0] if _msgs else "")
check("批量写默认关闭（逐条发更稳）", WING_NODE_WRITE[0] is False,
      str(WING_NODE_WRITE[0]))

print("\n=== E 切控台后的回包形态 ===")
_keep = {s: dict(STATE.fx.get(s, {})) for s in STATE.fx}
ok, _label, _extra = set_profile("wing")
check("set_profile('wing') 成功", ok, str(_label))
check("当前 profile 生效", profile().id == "wing")
check("效果槽同步会清掉 WING 没有的算法",
      all(not (STATE.fx[s].get("type") or "")
          or STATE.fx[s]["type"] in WING_FX_ALGOS for s in STATE.fx),
      str({s: STATE.fx[s].get("type") for s in STATE.fx}))
set_value("ch_fader", 1, 0.75)
_msg = value_reply("ch_fader", 1)
check("WING 回包：推子回 0.0 dB（不是 0.75）",
      _msg is not None and _msg.args[0] == 0.0,
      str(_msg.args if _msg else None))
set_value("ch_on", 1, 1)
_msg = value_reply("ch_on", 1)
check("WING 回包：未静音回 0（线上 1=muted）",
      _msg is not None and _msg.args[0] == 0,
      str(_msg.args if _msg else None))
set_value("fx_type", 1, "CRS")
_msg = value_reply("fx_type", 1)
check("WING 回包：算法回 mdl（CHORUS）",
      _msg is not None and _msg.args[0] == "CHORUS",
      str(_msg.args if _msg else None))
set_value("fx_mix", 1, 0.5)
_msg = value_reply("fx_mix", 1)
check("WING 回包：干湿 0.5 → 50",
      _msg is not None and _msg.args[0] == 50,
      str(_msg.args if _msg else None))
check("WING 下 fx_par 无地址（回包返回 None）",
      value_reply("fx_par_01", 1) is None)

# 前端按 w 灰掉没有地址的参数: 宁可没滑杆, 也不要"拖了没反应"
set_value("fx_type", 1, "ROOM")
_slot = [x for x in fx_state()["slots"] if x["slot"] == 1][0]
_w = {p["p"]: p["w"] for p in _slot["params"]}
check("ROOM par01 可写（w=True）", _w.get(1) is True)
check("ROOM par06(level) 不可写（w=False）", _w.get(6) is False)
set_value("fx_type", 1, "CRS")
_slot = [x for x in fx_state()["slots"] if x["slot"] == 1][0]
check("CRS 参数全部不可写（WING 无名字表）",
      all(not p["w"] for p in _slot["params"]))

# 收尾: 切回 X32 并恢复槽内容, 别把测试状态留给后面的组
set_profile("x32")
for _s, _v in _keep.items():
    STATE.fx[_s].update(_v)
check("收尾切回 X32", profile().id == "x32")

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for _n, _name, _detail in failed:
    print("失败项 #%d: %s%s" % (_n, _name, (" | " + _detail) if _detail else ""))
raise SystemExit(1 if fails else 0)
