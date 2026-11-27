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


def enc_osc(addr, value):
    def pad(b):
        return b + b"\x00" * (4 - len(b) % 4)
    out = pad(addr.encode())
    if isinstance(value, str):
        return out + pad(b",s") + pad(value.encode())
    return out + pad(b",f") + struct.pack(">f", float(value))


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

        all_ok = (not bad) and (not zero_pad) and sub_ok and ok_rb
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
