# -*- coding: utf-8 -*-
"""P2 四项 + 桥接离线版 端到端测试
假真台: 127.0.0.1:20023 (Web 28081)
桥接实例: 127.0.0.1:10023 (Web 28080, --bridge 指向假真台)
"""
import json, socket, struct, time, urllib.request

BR_WEB, FC_WEB = "http://127.0.0.1:28080", "http://127.0.0.1:28081"
BR_OSC, FC_OSC = ("127.0.0.1", 10023), ("127.0.0.1", 20023)
PASS, FAIL = 0, []


def check(name, cond, detail=""):
    global PASS
    tag = "✓" if cond else "✗"
    print("%s %s %s" % (tag, name, detail))
    if cond:
        PASS += 1
    else:
        FAIL.append(name)


def post(base, path, obj):
    req = urllib.request.Request(base + path, json.dumps(obj).encode(),
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def state(base):
    return json.loads(urllib.request.urlopen(base + "/api/state", timeout=5).read())


def fader(base, ch):
    return state(base)["ch"][str(ch)]["fader"]


def osc_msg(addr, *args):
    def _pad(b):
        return b + b"\0" * ((-len(b)) % 4)
    b = _pad(addr.encode() + b"\0")
    types, body = ",", b""
    for a in args:
        if isinstance(a, float):
            types += "f"; body += struct.pack(">f", a)
        elif isinstance(a, int):
            types += "i"; body += struct.pack(">i", a)
        else:
            types += "s"; body += _pad(a.encode() + b"\0")
    return b + _pad(types.encode() + b"\0") + body


time.sleep(2)

# ---------- A. 桥接: 写入转发 (任务#30) ----------
post(BR_WEB, "/api/set", {"kind": "ch", "param": "fader", "idx": 3, "value": 0.42})
time.sleep(0.6)
v_fc = fader(FC_WEB, 3)
check("桥接写入转发: 网页写桥接实例 → 假真台 ch3=0.42", abs(v_fc - 0.42) < 0.01,
      "假真台 ch3=%.3f" % v_fc)
v_br = fader(BR_WEB, 3)
check("桥接本地乐观更新 ch3 同步", abs(v_br - 0.42) < 0.01, "桥接 ch3=%.3f" % v_br)

# ---------- B. 桥接: /xremote 保活 + 订阅注册 ----------
sub = state(FC_WEB)["subscribers"]
check("桥接保活: 假真台侧出现订阅者 (/xremote)", sub >= 1, "订阅者=%d" % sub)

# ---------- C. 桥接: 状态回读 (真台手动变化 → 桥接实例同步) ----------
post(FC_WEB, "/api/set", {"kind": "ch", "param": "fader", "idx": 7, "value": 0.66})
time.sleep(1.0)
v_br = fader(BR_WEB, 7)
check("状态回读: 假真台 ch7=0.66 → 桥接实例同步", abs(v_br - 0.66) < 0.02,
      "桥接 ch7=%.3f" % v_br)

# ---------- D. 桥接冲突: 渐变中真台手动干预 (任务#31) ----------
post(BR_WEB, "/api/set", {"kind": "ch", "param": "fader", "idx": 5, "value": 0.30})
post(BR_WEB, "/api/set", {"kind": "ch", "param": "fader", "idx": 6, "value": 0.30})
evt0 = state(BR_WEB)["bridge"]
# 造一张 6 秒渐变卡片并运行 (两通道同时渐变)
card = post(BR_WEB, "/api/cardadd", {"card": {
    "name": "桥接冲突测试卡",
    "desc": "ch5/ch6 六秒渐强",
    "actions": [{"kind": "fade_ch", "chs": [5, 6], "to": 0.9, "duration": 6.0,
                 "curve": "linear"}]}})["id"]
post(BR_WEB, "/api/show", {"id": card})
time.sleep(1.2)
# 真台面板上手动干预 ch5 (直接写假真台)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(osc_msg("/ch/05/mix/fader", 0.15), FC_OSC)
time.sleep(2.5)
v5 = fader(BR_WEB, 5)
v6 = fader(BR_WEB, 6)
check("真台手动干预: ch5 停在手动位置 0.15", abs(v5 - 0.15) < 0.03, "ch5=%.3f" % v5)
check("其余通道继续: ch6 继续向 0.9 渐变 (>0.45)", v6 > 0.45, "ch6=%.3f" % v6)
evs = state(BR_WEB).get("events", [])
hit = [e for e in evs if "真台" in e.get("text", "") and "手动" in e.get("text", "")]
check("冲突通知事件生成 (真台手动干预)", bool(hit), hit[0]["text"] if hit else "无")
# 后续无新动作,卡片跑完即可; 清理
post(BR_WEB, "/api/show", {})
post(BR_WEB, "/api/carddel", {"id": card})
time.sleep(0.3)

# ---------- E. 卡片定时触发 (任务#26) ----------
card2 = post(BR_WEB, "/api/cardadd", {"card": {
    "name": "定时测试卡",
    "desc": "两秒后自动启动",
    "actions": [{"kind": "mute", "chs": [9]}]}})["id"]
post(BR_WEB, "/api/schedule", {"id": card2, "mode": "delay", "delay": 2.0})
scheds = state(BR_WEB)["schedules"]
check("定时任务已登记", len(scheds) == 1 and scheds[0]["card"] == card2,
      "%d 个" % len(scheds))
time.sleep(3.5)
st = state(BR_WEB)
check("定时触发: 卡片自动执行 (ch9 已静音)", not st["ch"]["9"]["on"],
      "ch9 on=%d" % st["ch"]["9"]["on"])
check("定时任务执行后清除", len(state(BR_WEB)["schedules"]) == 0)
check("定时执行留有撤销点", st["canUndo"])
post(BR_WEB, "/api/carddel", {"id": card2})

# ---------- F. 配置导入导出 (任务#28) ----------
raw = urllib.request.urlopen(BR_WEB + "/api/export", timeout=5).read()
cfg = json.loads(raw)
check("导出: 含 cards/quickKeys/names",
      isinstance(cfg.get("cards"), list) and isinstance(cfg.get("quickKeys"), list)
      and isinstance(cfg.get("names"), dict),
      "%d 卡片 / %d 按键" % (len(cfg["cards"]), len(cfg["quickKeys"])))
n_cards = len(cfg["cards"])
cfg["cards"].append({"id": "imp_test1", "name": "导入测试卡",
                     "desc": "导入新增", "actions": [{"kind": "unmute_all"}]})
cfg["quickKeys"] = cfg["quickKeys"][:2]
r = post(BR_WEB, "/api/import", {"config": cfg})
st = state(BR_WEB)
names_after = [c["name"] for c in st["cards"]]
check("导入: 新卡片生效", r.get("ok") and "导入测试卡" in names_after,
      "%d 卡片" % len(st["cards"]))
check("导入: 按键裁剪生效", len(st["quickKeys"]) == 2, "%d 按键" % len(st["quickKeys"]))
# 还原: 去掉测试卡再导回
cfg["cards"] = [c for c in cfg["cards"] if c.get("id") != "imp_test1"]
post(BR_WEB, "/api/import", {"config": cfg})
check("导入还原: 卡片数回到 %d" % n_cards, len(state(BR_WEB)["cards"]) == n_cards,
      "%d 卡片" % len(state(BR_WEB)["cards"]))

# ---------- G. 状态高亮数据链 (任务#29, 前端计算, 这里验证数据来源) ----------
st = state(BR_WEB)
ch1 = st["ch"]["1"]
check("状态高亮数据来源: /api/state 含 on/fader/scene",
      "on" in ch1 and "fader" in ch1 and "scene" in st)
# 把 ch1 静音后, 内置静音类动作应可被前端判为已生效
post(BR_WEB, "/api/set", {"kind": "ch", "param": "on", "idx": 1, "value": 0})
time.sleep(0.4)
check("ch1 静音已下发(桥接转发+回读闭环)", not state(BR_WEB)["ch"]["1"]["on"]
      and not state(FC_WEB)["ch"]["1"]["on"])

# ---------- H. 桥接状态字段 ----------
b = state(BR_WEB)["bridge"]
check("桥接状态字段: on/addr/live", b["on"] and b["addr"] == "127.0.0.1:20023",
      "%s live=%s" % (b["addr"], b["live"]))

print("\n=== %d 通过, %d 失败 ===" % (PASS, len(FAIL)))
if FAIL:
    print("失败项:", "、".join(FAIL))
    raise SystemExit(1)
