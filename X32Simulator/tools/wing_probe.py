# -*- coding: utf-8 -*-
"""WING 参数探测 —— 真机一键枚举 `/fx/<槽>` 的参数树，补全命名参数表。

为什么需要它：
  WING 的 FX 参数是命名叶子（`/fx/1/pdel`），名字随算法变化。官方文档只对
  ROOM 给出了完整清单，其余算法要靠真机 `/fx/N ,s ?` 枚举（返回值就是
  "参数名 + 类型 + 量程/枚举"）。这个脚本把那次枚举做成一条命令。

安全约定：
  · 默认**只读**：只 dump 当前装着的算法，不装载/不修改任何参数；
  · `--load ALGO` 才会临时装载一次（装载后自动恢复原算法）；
  · 只写 `wing_params.json`，不碰台面上的音频参数。

用法：
  python tools/wing_probe.py --host 192.168.1.71
  python tools/wing_probe.py --host 192.168.1.71 --load HALL --load CHORUS
  python tools/wing_probe.py --host 192.168.1.71 --json          # 机器可读

输出文件放在模拟器目录下（wing_params.json），运行时会自动读取：
  · 已知名字 → 标记为 "probe" 确认；
  · 未知名字 → 列进 unmapped，等人补 field 映射（**不自动猜**，猜错就是串味）。
"""
import argparse
import json
import os
import socket
import struct
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from x32sim.fxlib import X32_FX_ALGOS                      # noqa: E402
from x32sim.wingfx import WING_FX_PARAMS, probe_path       # noqa: E402


def _pad(b):
    """OSC 字符串: 至少一个 null 终止, 再补齐到 4 字节。

    ⚠️ `4 - len % 4` 而不是 `(4 - len % 4) % 4` —— 长度正好是 4 的倍数时
    必须补满 4 个 null, 否则对端按标准读会跨过边界（本文件的 dec 就是这么炸的）。
    """
    return b + b"\x00" * (4 - len(b) % 4)


def enc(addr, args=None):
    out = _pad(addr.encode("utf-8"))
    tags = ","
    body = b""
    for a in (args or []):
        if isinstance(a, str):
            tags += "s"
            body += _pad(a.encode("utf-8"))
        elif isinstance(a, float):
            tags += "f"
            body += struct.pack(">f", a)
        else:
            tags += "i"
            body += struct.pack(">i", int(a))
    return out + _pad(tags.encode("ascii")) + body


def dec(pkt):
    """→ (addr, args)。只处理单条 Message（WING 不发 Bundle）。"""
    if pkt.startswith(b"#bundle"):
        return None, []
    i = pkt.index(b"\x00")
    addr = pkt[:i].decode("utf-8", "replace")
    j = (i + 4) & ~3
    args = []
    if j < len(pkt) and pkt[j:j + 1] == b",":
        e = pkt.index(b"\x00", j)
        tags = pkt[j + 1:e].decode("ascii", "replace")
        k = (e + 4) & ~3
        for t in tags:
            if t == "s":
                try:
                    e2 = pkt.index(b"\x00", k)
                except ValueError:
                    break
                args.append(pkt[k:e2].decode("utf-8", "replace"))
                k = (e2 + 4) & ~3
            elif t == "f":
                args.append(struct.unpack(">f", pkt[k:k + 4])[0])
                k += 4
            elif t == "i":
                args.append(struct.unpack(">i", pkt[k:k + 4])[0])
                k += 4
            else:
                k += 4
    return addr, args


class Wing(object):
    def __init__(self, host, port=2223, timeout=1.5):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(timeout)

    def send(self, addr, args=None):
        self.sock.sendto(enc(addr, args), (self.host, self.port))

    def ask(self, addr, args=None, wait=1.5):
        """发一条并等它的回包（地址前缀匹配）。超时返回 (None, [])。"""
        self.send(addr, args)
        end = time.time() + wait
        while time.time() < end:
            try:
                pkt, _ = self.sock.recvfrom(32768)
            except socket.timeout:
                break
            a, v = dec(pkt)
            if a and (a == addr or a.startswith(addr.rstrip("/") + "/")
                      or a.startswith("/fx/")):
                return a, v
        return None, []


def parse_spec(text):
    """把 `?` 返回的描述串拆成 [{name, spec, ro}]。

    WING 回的是**一个长字符串**，项目之间用分隔符隔开。文档里统一画成 `~`
    （作者用 `~` 代表分隔），实际字节在不同固件上可能是 `\\n` 或 `\\0`——
    两种都吃，别赌固件。只读/临时项以 `$` 开头（$esrc/$emode…），标 ro=True
    保留但不参与映射。
    """
    out = []
    raw = str(text or "").replace("~", "\n")
    for chunk in raw.split("\n"):
        for sub in chunk.split("\0"):
            s = sub.strip()
            if not s:
                continue
            name = s.split(" ")[0]
            if not name:
                continue
            out.append({"name": name, "spec": s, "ro": name.startswith("$")})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=2223)
    ap.add_argument("--load", action="append", default=[], help="临时装载该算法后 dump（默认只读）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON（不打印报告）")
    ap.add_argument("--out", default="", help="输出路径，默认 <模拟器>/wing_params.json")
    args = ap.parse_args()

    w = Wing(args.host, args.port)
    ident = w.ask("/?", wait=2.0)
    if not ident[1]:
        print("连不上 %s:%d —— 检查 IP、同网段、台子 OSC 是否开启" % (args.host, args.port))
        return 2
    print("台子: %s" % ident[1][0])

    report = {"version": 1, "host": args.host, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
              "identity": str(ident[1][0]), "slots": {}, "algos": {}, "unmapped": {}}

    # 1) 列出 16 个槽与各自算法
    _a, v = w.ask("/fx", ["*"], wait=2.0)
    slots = [int(x) for x in v if str(x).isdigit()] or list(range(1, 17))
    print("效果槽: %d 个" % len(slots))

    cur = {}
    for s in slots:
        _a, mv = w.ask("/fx/%d/mdl" % s)
        mdl = str(mv[0]) if mv else ""
        cur[s] = mdl
        report["slots"][str(s)] = {"mdl": mdl}

    # 2) dump：默认当前算法；--load 时临时装载
    targets = {}
    for algo in args.load:
        targets[str(algo).upper()] = None
    if not targets:
        for s, mdl in cur.items():
            if mdl and mdl != "NONE":
                targets.setdefault(mdl, s)
    else:
        # 找一个空槽做装载，找不到就用第一个槽（会恢复）
        free = next((s for s, m in cur.items() if m in ("", "NONE")), slots[0])
        for algo in list(targets):
            targets[algo] = free

    for algo, slot in targets.items():
        original = cur.get(slot, "")
        if args.load:
            w.send("/fx/%d/mdl" % slot, [algo.lower()])
            time.sleep(0.45)
        _a, v = w.ask("/fx/%d" % slot, ["?"], wait=2.0)
        specs = parse_spec(v[0] if v else "")
        names = [x["name"] for x in specs if not x["ro"] and x["name"] not in ("mdl", "fxmix")]
        report["algos"][algo] = specs
        known = set((WING_FX_PARAMS.get(algo) or {}).values())
        miss = [n for n in names if n not in known]
        report["unmapped"][algo] = miss
        print("\n%s (槽 %d): %d 个可写参数" % (algo, slot, len(names)))
        print("  已标定: %s" % (", ".join(sorted(known)) or "（无）"))
        print("  未标定: %s" % (", ".join(miss) or "（无）"))
        if args.load and original:
            w.send("/fx/%d/mdl" % slot, [original.lower()])
            time.sleep(0.3)

    out = args.out or probe_path()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n已写出 %s" % out)
    if report["unmapped"]:
        print("提示: unmapped 里的名字需要在 x32sim/wingfx.py 补 field 映射（不会自动猜）")
    if args.json:
        print(json.dumps({"out": out, "unmapped": report["unmapped"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
