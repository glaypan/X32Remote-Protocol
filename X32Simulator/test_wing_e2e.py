# -*- coding: utf-8 -*-
"""WING 端到端验收: 起模拟器 + 假真台 UDP 收包, 验证真正发出的报文。

单元测试只证明"换算函数对"，这里证明**线上字节对**：
  切到 WING → 桥接 127.0.0.1 → 网页接口改推子/静音/算法/干湿
  → 抓 UDP，逐条断言地址不补零、推子是 dB、静音 1=muted、算法是 mdl 名。
"""
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
WEB = 28099
OSC = 10099
FAKE = 10199

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
got = []
PEER = [None]          # 最近一个发过包给假真台的客户端地址（用来主动回推）
SUB = [""]             # 收到的订阅命令（WING: /%<port>/*S）


def fake_console():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", FAKE))
    s.settimeout(0.5)
    end = time.time() + 40
    while time.time() < end:
        try:
            data, peer = s.recvfrom(8192)
        except socket.timeout:
            continue
        got.append(data)
        PEER[0] = peer
        try:
            a = data[:data.index(b"\x00")].decode("utf-8", "replace")
        except ValueError:
            continue
        if a.endswith("/*S"):
            SUB[0] = a
    s.close()


def push_readback(addr, value):
    """从假真台主动推一条回读（模拟台面上有人动了推子）。"""
    if not PEER[0]:
        return False
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(enc_osc(addr, value), PEER[0])
    s.close()
    return True


def enc_osc(addr, value=None):
    def pad(b):
        return b + b"\x00" * (4 - len(b) % 4)
    out = pad(addr.encode())
    if value is None:                      # 无参 trigger（/scene/N/load）
        return out + pad(b",")
    if isinstance(value, str):
        return out + pad(b",s") + pad(value.encode())
    if isinstance(value, int) and not isinstance(value, bool):
        return out + pad(b",i") + struct.pack(">i", value)
    return out + pad(b",f") + struct.pack(">f", float(value))


def send_to_sim(addr, value=None):
    """以"外部客户端"身份给模拟器发一条 OSC（测 oscsrv 那条入线路径）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(enc_osc(addr, value), ("127.0.0.1", OSC))
    s.close()


def post(path, obj):
    req = urllib.request.Request(
        "http://127.0.0.1:%d%s" % (WEB, path),
        data=json.dumps(obj).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        return json.loads(OPENER.open(req, timeout=5).read())
    except Exception as e:
        return {"ok": False, "error": repr(e)}


def wait_web(timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            OPENER.open("http://127.0.0.1:%d/api/state" % WEB, timeout=2).read()
            return True
        except Exception:
            time.sleep(0.3)
    return False


def parse(pkt):
    """极简 OSC 解析: 返回 (地址, [参数])。够用来验地址与字符串/浮点。"""
    i = pkt.index(b"\x00")
    addr = pkt[:i].decode("utf-8", "replace")
    j = (i + 4) & ~3
    tags = ""
    args = []
    if j < len(pkt) and pkt[j:j + 1] == b",":
        e = pkt.index(b"\x00", j)
        tags = pkt[j + 1:e].decode()
        k = (e + 4) & ~3
        for t in tags:
            if t == "s":
                e2 = pkt.index(b"\x00", k)
                args.append(pkt[k:e2].decode("utf-8", "replace"))
                k = (e2 + 4) & ~3
            elif t in "if":
                args.append(int.from_bytes(pkt[k:k + 4], "big", signed=True))
                k += 8 if t == "f" else 4
                if t == "f":
                    import struct
                    args[-1] = struct.unpack(">f", pkt[k - 8:k - 4])[0]
            else:
                k += 4
    return addr, args


def main():
    cfg = os.path.join(BASE, "_wing_e2e.json")
    logf = os.path.join(BASE, "_wing_e2e.log")
    if os.path.exists(cfg):
        os.remove(cfg)
    t = threading.Thread(target=fake_console, daemon=True)
    t.start()
    fh = open(logf, "wb")
    proc = subprocess.Popen(
        [PY, "x32_simulator.py", "--port", str(OSC), "--web-port", str(WEB),
         "--config", cfg],
        cwd=BASE, stdout=fh, stderr=subprocess.STDOUT)
    try:
        if not wait_web():
            print("FAIL 模拟器未起来")
            return 1
        print("模拟器已启动", post("/api/ctrlprofile", {"id": "wing"}))
        print("桥接", post("/api/bridge", {"addr": "127.0.0.1:%d" % FAKE}))
        time.sleep(1.0)
        got.clear()

        post("/api/set", {"kind": "ch", "param": "fader", "idx": 1, "value": 0.75})
        time.sleep(0.3)
        post("/api/set", {"kind": "ch", "param": "fader", "idx": 2, "value": 0.5})
        time.sleep(0.3)
        post("/api/set", {"kind": "ch", "param": "on", "idx": 3, "value": 1})
        time.sleep(0.3)
        post("/api/set", {"kind": "main", "param": "fader", "idx": 0, "value": 0.75})
        time.sleep(0.3)
        post("/api/fx", {"slot": 1, "field": "type", "value": "CRS"})
        time.sleep(0.3)
        post("/api/fx", {"slot": 1, "field": "mix", "value": 0.5})
        time.sleep(0.6)

        print("\n--- 收到 %d 个 UDP 包 ---" % len(got))
        seen = []
        for p in got:
            a, args = parse(p)
            seen.append((a, args))
            print("  %-18s %s" % (a, args))

        want = {
            ("/ch/1/fdr", 0.0), ("/ch/2/fdr", -10.0),
            ("/ch/3/mute", 0), ("/main/1/fdr", 0.0),
            ("/fx/1/mdl", "CHORUS"), ("/fx/1/fxmix", 50),
        }
        pairs = set()
        for a, args in seen:
            if args:
                pairs.add((a, args[0]))
        bad = []
        for a, v in want:
            hit = [x for x in pairs if x[0] == a and
                   (abs(float(x[1]) - float(v)) < 1e-6 if not isinstance(v, str) else x[1] == v)]
            if not hit:
                bad.append((a, v))
        zero_pad = [a for a, _ in seen if a in ("/ch/01/fdr", "/ch/01/mute")]
        print("\n--- 断言 ---")
        for a, v in sorted(want, key=lambda x: x[0]):
            ok = (a, v) not in [(b[0], b[1]) for b in bad]
            print(("PASS " if ok else "FAIL ") + "%s = %s" % (a, v))
        print(("PASS " if not zero_pad else "FAIL ") + "没有补零地址")
        print(("PASS " if not bad else "FAIL ") + "全部报文符合 WING 语义")

        # ---------------- N1: 通道处理参数 ----------------
        # 验的是**真正发出的地址与线值**：WING 的 EQ 段是 /ch/1/eq/1f（不是 X32 的
        # /ch/01/eq/1/f），低切在 flt/lc*，取点/检波/压缩比要发字符串。
        # 这一层错了的症状是"界面动、台面不动、日志干净"。
        got.clear()
        post("/api/set", {"kind": "ch", "param": "eq1_f", "idx": 1, "value": 500})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "eq2_g", "idx": 1, "value": 3.5})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "gate_thr", "idx": 2, "value": -40})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "dyn_thr", "idx": 1, "value": -30})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "dyn_ratio", "idx": 1, "value": 4.0})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "hpf_f", "idx": 1, "value": 100})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "hpf_slope", "idx": 1, "value": 12})
        time.sleep(0.15)
        post("/api/set", {"kind": "ch", "param": "trim", "idx": 1, "value": 3.5})
        time.sleep(0.15)
        post("/api/set", {"kind": "bus", "param": "dyn_thr", "idx": 1, "value": -22})
        time.sleep(0.15)
        post("/api/set", {"kind": "main", "param": "dyn_thr", "idx": 0, "value": -18})
        time.sleep(0.8)

        print("\n--- N1 通道参数报文 (%d 个) ---" % len(got))
        n1_seen = []
        for p in got:
            a, args = parse(p)
            if a:
                n1_seen.append((a, args[0] if args else None))
                print("  %-22s %r" % (a, args[0] if args else None))

        # 频率类是对数档位量化（logsteps=201 → 201 个点），500 Hz 落在最近档
        # 500.83 上，不是"值错了"。容差按协议的半步（约 1.5%）给。
        n1_want = [
            ("/ch/1/eq/1f", 500.0, False, 0.02),
            ("/ch/1/eq/2g", 3.5, False, 1e-4),
            ("/ch/2/gate/thr", -40.0, False, 1e-4),
            ("/ch/1/dyn/thr", -30.0, False, 1e-4),
            ("/ch/1/dyn/ratio", "4.0", True, 0),   # WING ch 的压缩比是**字符串**
            ("/ch/1/flt/lcf", 100.0, False, 0.02),  # 低切在 flt 子树
            ("/ch/1/flt/lcs", "12", True, 0),       # 斜率是 S 类型
            ("/ch/1/in/set/trim", 3.5, False, 1e-4),  # 输入段在 in/set
            ("/bus/1/dyn/thr", -22.0, False, 1e-4),   # bus 不补零
            ("/main/1/dyn/thr", -18.0, False, 1e-4),  # main 在 WING 是 /main/1
        ]
        n1_bad = []
        for a, v, is_str, tol in n1_want:
            hit = [x for x in n1_seen if x[0] == a]
            if not hit:
                n1_bad.append((a, v, "未发出"))
                continue
            got_v = hit[0][1]
            if is_str:
                ok = str(got_v) == v
            else:
                ok = got_v is not None and abs(float(got_v) - float(v)) <= \
                    max(tol, tol * abs(float(v)))
            if not ok:
                n1_bad.append((a, v, got_v))
        print("\n--- N1 断言 ---")
        for a, v, _s, _t in n1_want:
            ok = not any(b[0] == a for b in n1_bad)
            print(("PASS " if ok else "FAIL ") + "%s = %r" % (a, v))
        bad_x32_addr = [a for a, _ in n1_seen
                        if a and (a.startswith("/ch/0") or "/eq/1/" in a
                                  or a.startswith("/auxin/") or a.startswith("/fxrtn/"))]
        print(("PASS " if not bad_x32_addr else "FAIL ")
              + "没有 X32 地址混入（补零 / eq/N/x / auxin / fxrtn）")
        print(("PASS " if not n1_bad else "FAIL ") + "N1 全部报文符合 WING 语义")

        n1_ok = (not n1_bad) and (not bad_x32_addr)

        # ---------------- N2: 场景召回（两段式） ----------------
        # WING 没有 /scene/N/load 这种一步到位的地址，必须
        #   $actionidx = N  →  $action = "GO"
        # 只写号 = 高亮不执行；只写 GO = 切上一次的场景。
        got.clear()
        send_to_sim("/scene/7/load")       # 外部客户端发给模拟器
        time.sleep(0.8)
        n2_seen = []
        for p in got:
            a, args = parse(p)
            if a:
                n2_seen.append((a, args[0] if args else None))
                print("  %-24s %r" % (a, args[0] if args else None))
        idx_hit = [v for a, v in n2_seen if a == "/$ctl/lib/$actionidx"]
        go_hit = [v for a, v in n2_seen if a == "/$ctl/lib/$action"]
        x32_leak = [a for a, _ in n2_seen if a.startswith("/scene/")]
        n2_ok = (idx_hit == [7]) and (go_hit == ["GO"]) and not x32_leak
        print("\n--- N2 场景召回 ---")
        print(("PASS " if idx_hit == [7] else "FAIL ")
              + "$actionidx = 7（实测 %r）" % (idx_hit,))
        print(("PASS " if go_hit == ["GO"] else "FAIL ")
              + "$action = 'GO'（实测 %r）" % (go_hit,))
        print(("PASS " if not x32_leak else "FAIL ")
              + "没有把 X32 的 /scene/N/load 发给 WING" + (" %r" % (x32_leak,)))
        print(("PASS " if n2_ok else "FAIL ") + "两段式顺序执行")

        # N2b: 切回 X32 —— 同一个场景动作必须变成 X32 的形状（单条、不补零）。
        # 这条测的是"地址由 profile 决定"，而不是在调用方写 if。
        post("/api/ctrlprofile", {"id": "x32"})
        time.sleep(0.5)
        got.clear()
        send_to_sim("/scene/9/load")
        time.sleep(0.8)
        x32_seen = [parse(p)[0] for p in got]
        x32_scene = [a for a in x32_seen if a and a.startswith("/scene/")]
        wing_leak = [a for a in x32_seen if a and a.startswith("/$ctl/")]
        n2b_ok = (x32_scene == ["/scene/9/load"]) and not wing_leak
        print(("PASS " if n2b_ok else "FAIL ")
              + "切回 X32 后场景召回是 /scene/9/load（实测 %r）" % (x32_scene,))
        print(("PASS " if not wing_leak else "FAIL ")
              + "X32 下不发 WING 的 $ctl 地址" + (" %r" % (wing_leak,) if wing_leak else ""))
        n2_ok = n2_ok and n2b_ok
        # 必须切回 WING：下面 P3 的假真台推的是 /ch/5/fdr（WING 地址），
        # 留在 X32 会一条都解析不出来 —— 表现为"回读断言莫名失败"。
        post("/api/ctrlprofile", {"id": "wing"})
        time.sleep(0.4)

        # ---------------- P3: 订阅与回读 ----------------
        time.sleep(1.2)
        print("\n--- 订阅与回读 ---")
        sub_ok = SUB[0].startswith("/%") and SUB[0].endswith("/*S")
        print(("PASS " if sub_ok else "FAIL ") + "订阅命令是 /%%<port>/*S: %r" % SUB[0])
        push_readback("/ch/5/fdr", -20.0)
        time.sleep(0.9)
        st = json.loads(OPENER.open("http://127.0.0.1:%d/api/state" % WEB,
                                    timeout=5).read())
        chs = st.get("ch") or {}
        row = chs.get("5") or chs.get(5) or {}
        f5 = row.get("fader")
        ok_rb = f5 is not None and abs(float(f5) - 0.375) < 0.01
        print(("PASS " if ok_rb else "FAIL ")
              + "真台推 /ch/5/fdr -20 dB → 本地推子 0.375 (实测 %r)" % (f5,))

        all_ok = (not bad) and (not zero_pad) and sub_ok and ok_rb and n1_ok and n2_ok
        return 0 if all_ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        fh.close()
        if os.path.exists(cfg):
            os.remove(cfg)
        # 出错时把模拟器日志尾部打出来 —— 桥接线程异常退出只在这里留痕
        try:
            tail = open(logf, "r", encoding="utf-8", errors="replace").read().splitlines()[-12:]
            print("\n--- 模拟器日志尾部 ---")
            print("\n".join(tail))
        except OSError:
            pass


raise SystemExit(main())
