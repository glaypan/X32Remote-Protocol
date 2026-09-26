# -*- coding: utf-8 -*-
"""端到端测试: 新增/删除控制键 + 新建/运行/删除演出卡片 + 持久化。"""
import json, time, urllib.request

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕过系统代理
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

# 0. 服务器在线
info = get("/api/info")
check("服务器在线", "lanIp" in info, str(info))

# 1. 新增控制键
r = post("/api/keyadd", {"icon": "🎤", "name": "人声 增大", "kind": "ch", "idx": 3, "sign": 1})
check("keyadd 返回 ok", r.get("ok") is True, str(r))
st = get("/api/state")
newkey = next((k for k in st["quickKeys"] if k["name"] == "人声 增大"), None)
check("状态中出现新按键", newkey is not None, newkey and newkey["id"])

# 2. 按一下 → 通道3 推子按 3dB 步长上升
before = st["ch"]["3"]["fader"]
post("/api/key", {"id": newkey["id"]})
time.sleep(0.8)
st = get("/api/state")
after = st["ch"]["3"]["fader"]
check("按键生效(推子上升)", after > before + 0.02, "before=%.3f after=%.3f" % (before, after))

# 3. 删除控制键
post("/api/keydel", {"id": newkey["id"]})
st = get("/api/state")
check("keydel 后消失", not any(k["id"] == newkey["id"] for k in st["quickKeys"]))

# 4. 新建演出卡片: 静音通道2 → Bus3 渐到50% → 通道15 渐到80%
card = {"name": "🧪 测试卡片", "desc": "自动测试用",
        "actions": [
            {"kind": "mute", "ch": 2},
            {"kind": "fade_bus", "bus": 3, "to": 0.5, "duration": 2.0, "curve": "smooth"},
            {"kind": "fade_ch", "ch": 15, "to": 0.8, "duration": 3.0, "curve": "linear"},
        ]}
r = post("/api/cardadd", {"card": card})
check("cardadd 返回 ok", r.get("ok") is True, str(r))
st = get("/api/state")
newcard = next((c for c in st["cards"] if c["name"] == "🧪 测试卡片"), None)
check("状态中出现新卡片", newcard is not None, newcard and newcard["id"])
check("动作被保留", newcard and len(newcard["actions"]) == 3)

# 5. 运行卡片 → 结束后 Bus3≈0.5, 通道15≈0.8, 通道2 静音
post("/api/show", {"id": newcard["id"]})
time.sleep(6.0)
st = get("/api/state")
check("Bus3 渐到 50%%", abs(st["bus"]["3"]["fader"] - 0.5) < 0.02, "%.3f" % st["bus"]["3"]["fader"])
check("通道15 渐到 80%%", abs(st["ch"]["15"]["fader"] - 0.8) < 0.02, "%.3f" % st["ch"]["15"]["fader"])
check("通道2 已静音", st["ch"]["2"]["on"] == 0)

# 6. 删除卡片
post("/api/carddel", {"id": newcard["id"]})
st = get("/api/state")
check("carddel 后消失", not any(c["id"] == newcard["id"] for c in st["cards"]))

# 7. 非法动作被过滤
post("/api/cardadd", {"card": {"name": "脏数据", "actions": [{"kind": "hack", "x": 1}, {"kind": "mute", "ch": 999}]}})
st = get("/api/state")
dirty = next((c for c in st["cards"] if c["name"] == "脏数据"), None)
check("非法动作被过滤+越界钳制", dirty and len(dirty["actions"]) == 1 and dirty["actions"][0]["ch"] == 32,
      str(dirty and dirty["actions"]))
post("/api/carddel", {"id": dirty["id"]})

# 8. 持久化文件
import os
p = r"E:\AI\workbuddy\x32遥控\X32Simulator\user_config.json"
check("user_config.json 已生成", os.path.isfile(p))
d = json.load(open(p, encoding="utf-8"))
check("配置文件含 keys/cards", isinstance(d.get("quickKeys"), list) and isinstance(d.get("cards"), list),
      "keys=%d cards=%d" % (len(d["quickKeys"]), len(d["cards"])))

print("\n%d passed, %d failed" % (ok, fail))
exit(1 if fail else 0)
