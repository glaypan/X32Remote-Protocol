# -*- coding: utf-8 -*-
"""预设范围 / 局部场景 / 预设动作 回归。

核心语义（改坏了会静默变成"整台被换掉"，所以必须断言）：
  1. 预设可以只存某个范围，值数量可核对；
  2. 回归只写范围内的键，范围外的通道一个字节都不动；
  3. 卡片的「局部调用场景」不发场景号、也不动范围外通道；找不到预设要明确跳过；
  4. 「调用预设」动作能按渐变把推子拉回；
  5. 预设跨重启（落盘到 user_config.json）。
"""
import json, os, time
import urllib.error
import urllib.request

WEB = "http://127.0.0.1:28080"
CFG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_config.json")


def post(path, data):
    req = urllib.request.Request(WEB + path, json.dumps(data).encode(),
                                 {"Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=8).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except Exception:
            return {"http": e.code, "raw": body}


def state():
    return json.loads(urllib.request.urlopen(WEB + "/api/state", timeout=8).read())


def set_fader(i, v, g="ch"):
    post("/api/set", {"kind": g, "param": "fader", "idx": i, "value": v})


def set_on(i, v, g="ch"):
    post("/api/set", {"kind": g, "param": "on", "idx": i, "value": v})


def fader(g, i):
    return state()[g][str(i)]["fader"]


results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + ("  | " + detail if detail else ""))


# ---------- 布置台面 ----------
for i, v in ((1, 0.40), (2, 0.60), (3, 0.80)):
    set_fader(i, v)
post("/api/name", {"group": "ch", "idx": 5, "name": "ZZ测试麦"})
set_on(4, 0)

# ---------- 1. 范围保存：只有 ch 1-2 的推子 ----------
r = post("/api/snapshot", {"action": "save", "name": "ZZ局部-12路推子",
                           "scope": {"groups": ["ch"], "params": ["fader"],
                                     "indices": {"ch": [1, 2]}}})
pid1 = r.get("id")
check("范围预设保存返回 id/count", r.get("ok") and pid1 and r.get("count") == 2, str(r))

# ---------- 2. 范围回归：只还原 1-2，3 不动 ----------
set_fader(1, 0.95); set_fader(2, 0.95); set_fader(3, 0.10)
post("/api/snapshot", {"action": "recall", "id": pid1, "seconds": 1.5})
time.sleep(2.2)
check("ch1 回到预设值", abs(fader("ch", 1) - 0.40) < 0.02, "%.3f" % fader("ch", 1))
check("ch2 回到预设值", abs(fader("ch", 2) - 0.60) < 0.02, "%.3f" % fader("ch", 2))
check("ch3 未被范围回归碰到", abs(fader("ch", 3) - 0.10) < 0.001, "%.3f" % fader("ch", 3))

# ---------- 3. 名称 + 静音范围 ----------
r = post("/api/snapshot", {"action": "save", "name": "ZZ名称静音",
                           "scope": {"groups": ["ch"], "params": ["name", "on"],
                                     "indices": {"ch": [4, 5]}}})
pid2 = r.get("id")
check("名称+静音范围 count=4", r.get("count") == 4, str(r))
post("/api/name", {"group": "ch", "idx": 5, "name": "ZZ被改掉"})
set_on(4, 1)
post("/api/snapshot", {"action": "recall", "id": pid2, "seconds": 0.5})
time.sleep(1.0)
s = state()
check("名称随预设回来", s["names"]["ch"]["5"] == "ZZ测试麦", str(s["names"]["ch"].get("5")))
check("静音随预设回来", s["ch"]["4"]["on"] == 0, str(s["ch"]["4"]))

# ---------- 4. preset 动作 ----------
r = post("/api/cardadd", {"card": {"name": "ZZ调用预设卡", "actions": [
    {"kind": "preset", "presetId": pid1, "duration": 0.5}]}})
set_fader(1, 0.05)
post("/api/show", {"id": r.get("id")})
time.sleep(1.6)
check("preset 动作把 ch1 拉回", abs(fader("ch", 1) - 0.40) < 0.02, "%.3f" % fader("ch", 1))

# ---------- 5. 场景局部调用 ----------
scene_before = state()["scene"]
r = post("/api/cardadd", {"card": {"name": "ZZ局部场景卡", "actions": [
    {"kind": "scene", "scene": 9, "mode": "partial", "presetId": pid1,
     "duration": 0.5}]}})
set_fader(1, 0.05); set_fader(3, 0.95)
post("/api/show", {"id": r.get("id")})
time.sleep(1.6)
s = state()
check("场景局部: ch1 被应用", abs(s["ch"]["1"]["fader"] - 0.40) < 0.02, "%.3f" % s["ch"]["1"]["fader"])
check("场景局部: ch3 不受影响", abs(s["ch"]["3"]["fader"] - 0.95) < 0.01, "%.3f" % s["ch"]["3"]["fader"])
check("场景局部: 场景号没变", s["scene"] == scene_before,
      "%s -> %s" % (scene_before, s["scene"]))

# ---------- 6. 校验：局部场景必须带预设 ----------
r = post("/api/cardadd", {"card": {"name": "ZZ缺预设", "actions": [
    {"kind": "scene", "scene": 9, "mode": "partial"}]}})
check("缺预设的局部场景被拒（有原因）", (not r.get("ok")) or r.get("dropped"), str(r)[:120])

# ---------- 7. 持久化 ----------
time.sleep(0.4)
try:
    data = json.load(open(CFG, encoding="utf-8"))
except Exception as e:
    data = {}
check("预设已落盘", isinstance(data.get("presets"), list) and len(data["presets"]) >= 2,
      str(type(data.get("presets"))))
check("落盘的预设带 scope", all("scope" in p for p in data.get("presets", [])), "scope 缺失")

# ---------- 清理 ----------
for nm in ("ZZ调用预设卡", "ZZ局部场景卡", "ZZ缺预设"):
    c = next((c for c in state()["cards"] if c["name"] == nm), None)
    if c:
        post("/api/carddel", {"id": c["id"]})
for sp in [x for x in state()["snapshots"] if x["name"].startswith("ZZ")]:
    post("/api/snapshot", {"action": "delete", "id": sp["id"]})
post("/api/show", {})
check("清理完成", not any(c["name"].startswith("ZZ") for c in state()["cards"]))

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
raise SystemExit(1 if fails else 0)
