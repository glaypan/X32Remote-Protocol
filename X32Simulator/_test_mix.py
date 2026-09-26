# -*- coding: utf-8 -*-
"""端到端: 新版两页结构 + Aux/USB/FX返回/Matrix 通道(Web+OSC) + 快照覆盖新通道。"""
import json, socket, struct, time, urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:28080"
ok = fail = 0

def check(name, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print("PASS", name, detail)
    else: fail += 1; print("FAIL", name, detail)

def get(path):
    with OPENER.open(BASE + path, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

def post(path, obj):
    req = urllib.request.Request(BASE + path, data=json.dumps(obj).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with OPENER.open(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

# 0. 等待 Web 就绪
html = ""
for i in range(20):
    try:
        html = OPENER.open(BASE + "/", timeout=3).read().decode("utf-8")
        break
    except Exception:
        time.sleep(1)
check("新版页面结构", "pg-mix" in html and "pg-tile" in html and 'data-p="mix"' in html
      and 'data-p="ch"' not in html and 'data-p="show"' not in html)
check("调音台页含全部区块", all(k in html for k in
      ("auxlist", "usblist", "fxretlist", "mtxlist", "chlist", "buslist", "dcalist", "mainrow")))
check("卡片页无静音墙", "tileCh" not in html and "tileBus" not in html)

st = get("/api/state")
check("state 含新通道组", all(g in st for g in ("auxin", "usb", "fxret", "mtx")),
      "auxin=%d usb=%d fxret=%d mtx=%d" % (len(st["auxin"]), len(st["usb"]), len(st["fxret"]), len(st["mtx"])))

# 1. Web 设置新通道
post("/api/set", {"kind": "auxin", "idx": 2, "param": "fader", "value": 0.4})
post("/api/set", {"kind": "mtx", "idx": 3, "param": "on", "value": 0})
st = get("/api/state")
check("Aux2 推子=0.4", abs(st["auxin"]["2"]["fader"] - 0.4) < 0.001)
check("MTX3 已静音", st["mtx"]["3"]["on"] == 0)
post("/api/set", {"kind": "mtx", "idx": 3, "param": "on", "value": 1})

# 2. OSC 读写新通道 (含 fxret 01L 写法)
def pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)

def osc_msg(addr, args=()):
    pkt = pad4(addr.encode() + b"\x00")
    tags = b","
    payload = b""
    for a in args:
        tags += b"f"
        payload += struct.pack(">f", a)
    pkt += pad4(tags + b"\x00") + payload
    return pkt

def osc_get(addr):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(osc_msg(addr), ("127.0.0.1", 10023))
    data, _ = s.recvfrom(2048)
    z = data.index(b"\x00")
    rsp = data[:z].decode()
    off = (z + 1 + 3) // 4 * 4
    zend = data.index(b"\x00", off)
    tags = data[off + 1:zend].decode()
    off = (zend + 1 + 3) // 4 * 4
    vals = []
    for t in tags:
        if t == "f":
            (v,) = struct.unpack(">f", data[off:off + 4]); vals.append(v); off += 4
        elif t == "i":
            (v,) = struct.unpack(">i", data[off:off + 4]); vals.append(v); off += 4
    s.close()
    return rsp, vals

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.sendto(osc_msg("/fxret/03L/mix/on", (0,)), ("127.0.0.1", 10023))
s.sendto(osc_msg("/mtx/02/mix/fader", (0.6,)), ("127.0.0.1", 10023))
time.sleep(0.2)
rsp, vals = osc_get("/fxret/03/mix/on")
check("OSC fxret mute 读写", rsp == "/fxret/03/mix/on" and vals == [0], str(vals))
rsp, vals = osc_get("/mtx/02/mix/fader")
check("OSC mtx fader 读写", rsp == "/mtx/02/mix/fader" and abs(vals[0] - 0.6) < 0.001, str(vals))
rsp, vals = osc_get("/auxin/02/mix/fader")
check("OSC auxin 读取", rsp == "/auxin/02/mix/fader" and abs(vals[0] - 0.4) < 0.001, str(vals))
s.close()
post("/api/set", {"kind": "fxret", "idx": 3, "param": "on", "value": 1})

# 3. 控制键目标 Aux In
post("/api/keyadd", {"icon": "🎚", "name": "Aux2 增", "kind": "auxin", "idx": 2, "sign": 1})
st = get("/api/state")
k = next(k for k in st["quickKeys"] if k["name"] == "Aux2 增")
before = st["auxin"]["2"]["fader"]
post("/api/key", {"id": k["id"]})
time.sleep(0.8)
st = get("/api/state")
check("控制键作用于 Aux2", st["auxin"]["2"]["fader"] > before + 0.02,
      "%.3f→%.3f" % (before, st["auxin"]["2"]["fader"]))
post("/api/keydel", {"id": k["id"]})

# 4. 快照覆盖新通道
post("/api/set", {"kind": "usb", "idx": 1, "param": "fader", "value": 0.5})
post("/api/snapshot", {"action": "save"})
post("/api/set", {"kind": "usb", "idx": 1, "param": "fader", "value": 0.9})
snap = get("/api/state")["snapshots"][0]
post("/api/snapshot", {"action": "recall", "id": snap["id"], "seconds": 1})
time.sleep(1.6)
st = get("/api/state")
check("预设回归覆盖 USB1", abs(st["usb"]["1"]["fader"] - 0.5) < 0.02, "%.3f" % st["usb"]["1"]["fader"])
post("/api/snapshot", {"action": "delete", "id": snap["id"]})

print("\n%d passed, %d failed" % (ok, fail))
exit(1 if fail else 0)
