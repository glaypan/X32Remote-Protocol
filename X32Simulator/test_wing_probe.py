# -*- coding: utf-8 -*-
"""WING 参数探测脚本回归（无真机）。

起一个"假 WING"UDP 服务，按官方报文格式回包，验证 tools/wing_probe.py：
  · 能握手、能列 16 槽、能读 mdl；
  · 能把 `/fx/N ,s ?` 的描述串拆成 参数名 + 只读项；
  · 已知名字不进 unmapped，未知名字进 unmapped（**不自动猜 field**）；
  · 输出 JSON 能被 x32sim.wingfx 读回并覆盖内置表。
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tools"))

import wing_probe as WP                                        # noqa: E402
from x32sim import wingfx                                       # noqa: E402

PORT = 10223
OUT = os.path.join(BASE, "_tmp_wing_params.json")

ROOM_SPEC = ("  mdl list [NONE, EXT, HALL, ROOM]\n fmxix lin [0 .. 100 %], 101 steps\n"
             "$esrc int [0 .. 400]\n$emode list [M, ST, M/S]\n"
             "pdel linf [0 .. 200] ms\nsize linf [2.0 .. 200] \ndcy linf [0.2 .. 20] s\n"
             "mult linf [0.2 .. 4] \ndamp linf [1000 .. 20000] Hz\nlc linf [10 .. 500] Hz\n"
             "hc linf [200 .. 20000] Hz\nshp linf [0 .. 250] \nsprd linf [0 .. 50] \n"
             "diff linf [1 .. 30] \nspin linf [0 .. 100] %\n"
             "ecl linf [0 .. 1000] ms\necr linf [0 .. 1000] ms\n"
             "efl linf [-100 .. 100] \nefr linf [-100 .. 100] \nnewthing int [0 .. 3] \n")

stop = [False]


def fake_wing():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT))
    s.settimeout(0.3)
    while not stop[0]:
        try:
            pkt, peer = s.recvfrom(8192)
        except socket.timeout:
            continue
        a, _ = WP.dec(pkt)
        if a is None:
            continue
        if a in ("/?", "/?"):
            s.sendto(WP.enc("/?", ["WING,127.0.0.1,PGM,ngc-full,NO_SERIAL,3.0.6"]), peer)
        elif a == "/fx":
            s.sendto(WP.enc("/fx", [str(i) for i in range(1, 17)]), peer)
        elif a.endswith("/mdl"):
            slot = a.split("/")[2]
            s.sendto(WP.enc(a, ["ROOM" if slot == "1" else "NONE"]), peer)
        elif a in ("/fx/1", "/fx/1/"):
            s.sendto(WP.enc("/fx/1", [ROOM_SPEC]), peer)
        elif a.startswith("/fx/"):
            s.sendto(WP.enc(a, ["NONE"]), peer)
    s.close()


results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name
          + (("  | " + detail) if detail and not ok else ""))


t = threading.Thread(target=fake_wing, daemon=True)
t.start()
time.sleep(0.2)

if os.path.exists(OUT):
    os.remove(OUT)

p = subprocess.run([sys.executable, os.path.join(BASE, "tools", "wing_probe.py"),
                    "--host", "127.0.0.1", "--port", str(PORT), "--out", OUT],
                   cwd=BASE, capture_output=True, text=True, timeout=90)
print(p.stdout[-600:] if p.stdout else "")
stop[0] = True

check("脚本退出码 0", p.returncode == 0, (p.stderr or "")[-300:])
check("输出文件已生成", os.path.exists(OUT))

if os.path.exists(OUT):
    with open(OUT, "r", encoding="utf-8") as f:
        rep = json.load(f)
    check("握手信息落盘", "WING" in rep.get("identity", ""))
    check("列出 16 个槽", len(rep.get("slots") or {}) == 16, str(len(rep.get("slots") or {})))
    check("槽 1 算法 = ROOM", rep["slots"]["1"]["mdl"] == "ROOM")
    specs = rep["algos"].get("ROOM") or []
    names = [x["name"] for x in specs]
    check("解析出参数名", "pdel" in names and "dcy" in names, str(names[:6]))
    check("只读项标 ro（$esrc/$emode）",
          all(x["ro"] for x in specs if x["name"].startswith("$")))
    check("mdl/fxmix 不算可写参数",
          "mdl" not in (rep["unmapped"].get("ROOM") or [])
          and "fxmix" not in (rep["unmapped"].get("ROOM") or []))
    miss = rep["unmapped"].get("ROOM") or []
    check("已标定的 15 项不进 unmapped",
          not ({"pdel", "dcy", "size", "damp", "lc", "hc", "shp", "sprd",
                "diff", "spin", "ecl", "ecr", "efl", "efr", "mult"} & set(miss)),
          str(miss))
    check("未知参数 newthing 进 unmapped（不自动猜）", "newthing" in miss, str(miss))

# 运行时覆盖：把探测结果塞进 wing_params.json，wing_par_name 仍只认内置 field
wingfx._PROBE["mtime"] = None
wingfx._PROBE["table"] = None
_orig = wingfx.probe_path
wingfx.probe_path = lambda: OUT
try:
    check("运行时表能读到探测结果", "pdel" in (wingfx.wing_params("ROOM").values()))
    check("覆盖表不改变 field 语义（level 仍无对应）",
          wingfx.wing_par_name("ROOM", 6) is None)
finally:
    wingfx.probe_path = _orig
    wingfx._PROBE["mtime"] = None
    wingfx._PROBE["table"] = None
    if os.path.exists(OUT):
        os.remove(OUT)

fails = results.count(False)
print("\n%d/%d 通过" % (len(results) - fails, len(results)))
for _n, _name, _detail in failed:
    print("失败项 #%d: %s%s" % (_n, _name, (" | " + _detail) if _detail else ""))
raise SystemExit(1 if fails else 0)
