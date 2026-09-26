# -*- coding: utf-8 -*-
"""冲突策略端到端测试: 手动干预 → 只停该通道, 其余通道/后续动作继续, 有通知。"""
import json, socket, struct, time, urllib.request

WEB = "http://127.0.0.1:28080"


def post(path, data):
    req = urllib.request.Request(WEB + path, json.dumps(data).encode(),
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def state():
    return json.loads(urllib.request.urlopen(WEB + "/api/state", timeout=5).read())


def _pad(b):
    return b + b"\0" * ((-len(b)) % 4)          # 含结尾 \0 一起对齐到 4 字节


def osc_msg(addr, *args):
    types, body = ",", b""
    for a in args:
        if isinstance(a, bool):
            types += "T" if a else "F"
        elif isinstance(a, float):
            types += "f"; body += struct.pack(">f", a)
        elif isinstance(a, int):
            types += "i"; body += struct.pack(">i", a)
        else:
            types += "s"; body += _pad(a.encode() + b"\0")
    return _pad(addr.encode() + b"\0") + _pad((types).encode() + b"\0") + body


def fader(group, idx):
    return state()[group][str(idx)]["fader"] if group != "main" else state()["main"]["fader"]


results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


# 0. 等服务起来
for _ in range(40):
    try:
        state(); break
    except Exception:
        time.sleep(0.5)

# 1. 建测试卡片: 全体推 6 秒 → 停 1.5 秒 → 通道05 3 秒拉到 10%
post("/api/cardadd", {"card": {
    "name": "ZZ测试冲突", "desc": "临时测试卡",
    "actions": [
        {"kind": "fade_all", "to": 0.8, "duration": 6.0, "curve": "linear"},
        {"kind": "wait", "duration": 1.5},
        {"kind": "fade_ch", "chs": [5], "to": 0.1, "duration": 3.0, "curve": "linear"},
    ]}})

# 2. 先把台面拉到低位, 便于观察上升
for i in range(1, 33):
    post("/api/set", {"kind": "ch", "param": "fader", "idx": i, "value": 0.1})
time.sleep(0.4)

# 3. 运行卡片, 等 2.5 秒 (全体渐变进行中)
cards = [c for c in state()["cards"] if c["name"] == "ZZ测试冲突"]
assert cards, "测试卡片未创建"
cid = cards[0]["id"]
post("/api/show", {"id": cid})
time.sleep(2.5)

ch5_a, ch6_a = fader("ch", 5), fader("ch", 6)
check("渐变进行中 (ch6 已在上升)", ch6_a > 0.12, "ch6=%.3f" % ch6_a)

# 4. 模拟手动干预: 外部 OSC 把通道05推子打到 0.15
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(osc_msg("/ch/05/mix/fader", 0.15), ("127.0.0.1", 10023))
time.sleep(1.6)

ch5_b, ch6_b = fader("ch", 5), fader("ch", 6)
check("被干预通道停住 (ch5≈0.15)", abs(ch5_b - 0.15) < 0.06, "ch5=%.3f" % ch5_b)
check("其余通道继续动作 (ch6 仍上升)", ch6_b > ch6_a + 0.02,
      "ch6 %.3f→%.3f" % (ch6_a, ch6_b))

# 5. 通知事件已推送
evs = state().get("events", [])
hit = [e for e in evs if "05" in e["text"] and "手动接管" in e["text"]]
check("网页通知事件已生成", bool(hit), hit[-1]["text"] if hit else "无事件")

# 6. 后续动作照常执行: wait 1.5s 后 fade_ch ch5 应重新接管, 把 ch5 拉向 0.1
time.sleep(4.5)
ch5_c = fader("ch", 5)
check("后续动作重新获得该通道 (ch5→0.1)", ch5_c < 0.3, "ch5=%.3f" % ch5_c)

# 7. 日志里有 CONFLICT 行
logtxt = "\n".join(state()["log"])
check("日志记录 CONFLICT", "CONFLICT" in logtxt)

# 8. 收尾: 停卡 + 删测试卡
post("/api/show", {})
time.sleep(0.3)
post("/api/carddel", {"id": cid})
check("清理完成", not any(c["name"] == "ZZ测试冲突" for c in state()["cards"]))

fails = [r for r in results if not r[1]]
print("\n%d/%d 通过" % (len(results) - len(fails), len(results)))
raise SystemExit(1 if fails else 0)
