# -*- coding: utf-8 -*-
"""新功能综合回归: DCA级联/rel_db/并行at/卡片链/step显示/置顶/批量写入/日志增量/订阅计数"""
import json, math, socket, struct, time, urllib.request

WEB = "http://127.0.0.1:28080"


def post(path, data):
    req = urllib.request.Request(WEB + path, json.dumps(data).encode(),
                                 {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def state(**params):
    q = "&".join("%s=%s" % kv for kv in params.items())
    return json.loads(urllib.request.urlopen(WEB + "/api/state" + ("?" + q if q else ""), timeout=5).read())


def _pad(b):
    return b + b"\0" * ((-len(b)) % 4)


def osc_msg(addr, *args):
    types, body = ",", b""
    for a in args:
        if isinstance(a, float):
            types += "f"; body += struct.pack(">f", a)
        elif isinstance(a, int):
            types += "i"; body += struct.pack(">i", a)
        else:
            types += "s"; body += _pad(a.encode() + b"\0")
    return _pad(addr.encode() + b"\0") + _pad(types.encode() + b"\0") + body


def fader(g, i):
    return state()["ch" if g == "ch" else g][str(i)]["fader"]


results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


for _ in range(40):
    try:
        state(); break
    except Exception:
        time.sleep(0.5)

# ---------- A. 日志增量 (性能#19) ----------
s1 = state()
full_len = len(s1["log"])
s2_seq = s1["logseq"]
s2 = state(logseq=s2_seq)
check("日志增量: logseq 相同则空增量", s2_seq >= s1["logseq"] - 40 and len(s2["log"]) == 0,
      "logseq=%s 增量=%d" % (s1["logseq"], len(s2["log"])))

# ---------- B. 批量写入 (性能#18) ----------
sets = [{"kind": "ch", "param": "fader", "idx": i, "value": 0.4} for i in (1, 2, 3)]
r = post("/api/setbatch", {"sets": sets})
check("批量写入 3 条生效", r["ok"] and r["applied"] == 3 and
      all(abs(fader("ch", i) - 0.4) < 0.01 for i in (1, 2, 3)), str(r))

# ---------- C. DCA 级联 (手感#16) ----------
# 默认 DCA1 成员 ch1-8; 把 ch1-8 拉到 0.75, ch9 (非成员) 拉到 0.4
for i in range(1, 9):
    post("/api/set", {"kind": "ch", "param": "fader", "idx": i, "value": 0.75})
post("/api/set", {"kind": "ch", "param": "fader", "idx": 9, "value": 0.4})
post("/api/set", {"kind": "dca", "param": "fader", "idx": 1, "value": 1.0})
time.sleep(0.3)
db1 = state()["ch"]["1"]["fader"]
db9 = state()["ch"]["9"]["fader"]
check("DCA1 推满 → 成员 ch1 升到顶", db1 > 0.99, "ch1=%.3f" % db1)
check("非成员 ch9 不受影响 (仍 0.4)", abs(db9 - 0.4) < 0.01, "ch9=%.3f" % db9)
# OSC 位掩码: 把 ch9 归入 DCA1 (bit0=1)
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(osc_msg("/ch/09/mix/dca", 1), ("127.0.0.1", 10023))
time.sleep(0.3)
post("/api/set", {"kind": "dca", "param": "fader", "idx": 1, "value": 0.75})
time.sleep(0.3)
# DCA1 从 1.0(-10dB) 回到 0.75(0dB), 成员 ch9 也应 -10dB: 0.4 → 0.4*10^(-10/20)? 不, ch9=0.4dB≈-5.6dB → -15.6dB
ch9_db = 20 * math.log10(0.4 / 0.75) if 0.4 < 0.75 else (0.4 - 0.75) / 0.25 * 10
expect9 = 0.75 * (10 ** ((ch9_db - 10) / 20))
check("OSC 设 DCA 成员后级联生效 (ch9 跟随 -10dB)", abs(fader("ch", 9) - expect9) < 0.05,
      "ch9=%.3f 期望=%.3f" % (fader("ch", 9), expect9))
sock.sendto(osc_msg("/ch/09/mix/dca", 0), ("127.0.0.1", 10023))  # 还原成员表
time.sleep(0.2)

# ---------- D. rel_db 相对动作 (P1) ----------
post("/api/cardadd", {"card": {"name": "ZZ测试rel", "desc": "", "actions": [
    {"kind": "rel_db", "group": "ch", "chs": [1], "delta_db": -6.0, "duration": 1.0, "curve": "linear"}]}})
cid_rel = next(c["id"] for c in state()["cards"] if c["name"] == "ZZ测试rel")
before = state()["ch"]["1"]["fader"]          # 0.75 → -6dB ≈ 0.376
post("/api/show", {"id": cid_rel})
time.sleep(2.0)
after = state()["ch"]["1"]["fader"]
expect = 0.75 * (10 ** (-6 / 20))
check("rel_db -6dB 从当前值换算", abs(after - expect) < 0.02,
      "%.3f → %.3f (期望 %.3f)" % (before, after, expect))
post("/api/show", {})

# ---------- E. 并行 at + step 显示 (P1 调度器 + 手感#14) ----------
post("/api/cardadd", {"card": {"name": "ZZ测试at", "desc": "", "actions": [
    {"kind": "fade_ch", "chs": [1], "to": 0.9, "duration": 2.0, "curve": "linear"},
    {"kind": "fade_ch", "chs": [2], "to": 0.0, "duration": 2.0, "curve": "linear", "at": 0.0},
    {"kind": "wait", "duration": 0.5},
    {"kind": "mute", "chs": [3]},
]}})
cid_at = next(c["id"] for c in state()["cards"] if c["name"] == "ZZ测试at")
for i in (1, 2):
    post("/api/set", {"kind": "ch", "param": "fader", "idx": i, "value": 0.5})
post("/api/show", {"id": cid_at})
time.sleep(1.0)
mid = state()
c1, c2 = mid["ch"]["1"]["fader"], mid["ch"]["2"]["fader"]
check("并行: ch1↑ 同时 ch2↓ (at=0)", c1 > 0.6 and c2 < 0.35, "ch1=%.3f ch2=%.3f" % (c1, c2))
check("step 显示当前动作", bool(mid["show"]["step"]) and "·" in mid["show"]["step"],
      mid["show"]["step"])
time.sleep(2.0)
fin = state()
check("并行渐变到位 ch1=0.9", abs(fin["ch"]["1"]["fader"] - 0.9) < 0.02,
      "ch1=%.3f" % fin["ch"]["1"]["fader"])
check("瞬时动作照常执行 ch3 静音", fin["ch"]["3"]["on"] == 0)
post("/api/show", {})

# ---------- F. 卡片链 (P1) ----------
post("/api/cardadd", {"card": {"name": "ZZ链头", "desc": "", "actions": [
    {"kind": "wait", "duration": 1.0}], "next": ""}})
post("/api/cardadd", {"card": {"name": "ZZ链尾", "desc": "", "actions": [
    {"kind": "mute", "chs": [4]}]}})
head = next(c for c in state()["cards"] if c["name"] == "ZZ链头")
tail = next(c for c in state()["cards"] if c["name"] == "ZZ链尾")
post("/api/cardedit", {"id": head["id"], "card": {"next": tail["id"]}})
post("/api/show", {"id": head["id"]})
time.sleep(2.6)
fin = state()
check("链: 链头跑完自动执行链尾 (ch4 静音)", fin["ch"]["4"]["on"] == 0,
      "running=%s" % fin["show"]["running"])
post("/api/show", {})

# ---------- G. 置顶 (手感#17) ----------
post("/api/cardpin", {"id": tail["id"]})
cards = state()["cards"]
t = next(c for c in cards if c["id"] == tail["id"])
check("置顶标记已保存", t.get("pin") == 1)
post("/api/cardpin", {"id": tail["id"]})   # 取消置顶还原

# ---------- H. 日志有新内容 (增量管道) ----------
s3 = state(logseq=s1["logseq"])
check("日志增量能取到中间的行", len(s3["log"]) > 0)

# ---------- 清理 ----------
for nm in ("ZZ测试rel", "ZZ测试at", "ZZ链头", "ZZ链尾"):
    c = next((c for c in state()["cards"] if c["name"] == nm), None)
    if c:
        post("/api/carddel", {"id": c["id"]})
check("清理完成", not any(c["name"].startswith("ZZ") for c in state()["cards"]))

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
raise SystemExit(1 if fails else 0)
