# -*- coding: utf-8 -*-
"""核对「出厂值未标定」的参数 —— 在真台上跑一次就有答案。

背景: 有些出厂值在官方手册里只有**范围**、没有出厂值；社区场景文件里的是
使用者自己的设置，不是出厂状态。模拟器给的是合理猜测，并在 ParamSpec 里标了
`unverified=True`（这个标记会进 /api/info，界面上能看到"未标定"）。

本脚本做两件事:
  1. 逐条 GET 这些参数，和模拟器的假设值对比；
  2. 打印差异，并可选写一份 JSON（给自动化/回归用）。

用法:
  1. 真台做一次出厂初始化 (开机按住 Scenes/Undo，或 Setup/Config → Initialize)
  2. python tools/verify_defaults.py 192.168.1.100
     python tools/verify_defaults.py 192.168.1.100 10023 --json out.json
     (全程只发 GET，不改任何东西)

判读: 「真台」列就是出厂值。若与「假设」不同，把真台值填回 x32sim/params.py
对应 ParamSpec 的 default，并把 unverified=True 删掉（note 里的"需真机确认"同步删）。
"""
import json
import os
import socket
import sys
import time

# 脚本在 tools/ 下跑, sys.path[0] 是 tools/, 直接 import x32sim 会失败
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from x32sim.osc import OscMsg, decode_packet, encode_message  # noqa: E402
from x32sim.params import spec_addr, unverified_kinds  # noqa: E402

# 优先核对这 5 个（手册明确写了范围、但没写出厂值，且影响演出默认状态）
FOCUS = ("ch_hpf_f", "ch_hpf_slope", "ch_ins_pos", "ch_gate_thr", "ch_gate_range")

CONSOLE = None


def checks():
    """从登记表生成核对清单 —— 新增的未标定参数自动进列表，不用手工维护。"""
    rows = []
    for sp in unverified_kinds():
        if sp.group != "ch" or sp.nidx != 1:      # 发送是二维, 地址要 (ch,bus)
            continue
        rows.append({
            "kind": sp.kind,
            "addr": spec_addr(sp, 1),
            "label": sp.label,
            "assumed": sp.default,
            "vtype": sp.vtype,
            "note": sp.note,
        })
    rows.sort(key=lambda r: (0 if r["kind"] in FOCUS else 1, r["kind"]))
    return rows


def ask(sock, addr, timeout=1.5):
    """发 GET 等一个回包。X32 对 GET 会回同地址的消息。"""
    sock.sendto(encode_message(OscMsg(addr, [])), CONSOLE)
    end = time.time() + timeout
    while time.time() < end:
        sock.settimeout(max(0.05, end - time.time()))
        try:
            data, _ = sock.recvfrom(65536)
        except socket.timeout:
            break
        for m in decode_packet(data):
            if m.address.rstrip("/") == addr.rstrip("/") and m.args:
                return m.args[0]
    return None


def same(a, b):
    try:
        return abs(float(a) - float(b)) < 1e-6
    except (TypeError, ValueError):
        return str(a) == str(b)


def main():
    global CONSOLE
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    host = sys.argv[1]
    port = 10023
    out_path = None
    args = sys.argv[2:]
    i = 0
    while i < len(args):
        if args[i] == "--json" and i + 1 < len(args):
            out_path = args[i + 1]
            i += 2
            continue
        if args[i].isdigit():
            port = int(args[i])
        i += 1

    CONSOLE = (host, port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", 0))
    sock.settimeout(1.5)
    sock.sendto(encode_message(OscMsg("/xinfo", [])), CONSOLE)
    try:
        sock.recvfrom(65536)
    except socket.timeout:
        print("!! %s:%d 没有回应 —— 检查 IP / 是否同一网段 / 端口" % CONSOLE)
        return 2

    rows = checks()
    print("%-26s %-12s %-12s %-12s %s" % ("地址", "项目", "真台", "假设", "结论"))
    print("-" * 86)
    diff, miss = [], []
    for r in rows:
        got = ask(sock, r["addr"])
        if got is None:
            miss.append(r)
            print("%-26s %-12s %-12s %-12s 读不到" % (r["addr"], r["label"], "-", r["assumed"]))
            continue
        r["actual"] = got
        if same(got, r["assumed"]):
            r["match"] = True
            print("%-26s %-12s %-12s %-12s 一致" % (r["addr"], r["label"], got, r["assumed"]))
        else:
            r["match"] = False
            diff.append(r)
            try:
                pct = " (Δ%.1f%%)" % (abs(float(got) - float(r["assumed"]))
                                      / max(abs(float(r["assumed"])), 1e-9) * 100)
            except (TypeError, ValueError):
                pct = ""
            print("%-26s %-12s %-12s %-12s ★ 需修正%s"
                  % (r["addr"], r["label"], got, r["assumed"], pct))
    print("-" * 86)
    print("共 %d 项: 一致 %d / 不一致 %d / 读不到 %d"
          % (len(rows), len(rows) - len(diff) - len(miss), len(diff), len(miss)))
    if diff:
        print("\n把「真台」列的值填回 x32sim/params.py 对应 ParamSpec 的 default，"
              "并删掉 unverified=True 与 note 里的「需真机确认」。")
    elif not miss:
        print("\n全部一致 —— 可以把这些 ParamSpec 的 unverified=True 删掉，"
              "note 里注明「真机核对 %s」。" % time.strftime("%Y-%m-%d"))

    if out_path:
        payload = {
            "console": "%s:%d" % CONSOLE,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "rows": rows,
            "diff": [r["kind"] for r in diff],
            "missing": [r["kind"] for r in miss],
        }
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("已写 %s" % out_path)
    return 0 if not diff else 3


if __name__ == "__main__":
    sys.exit(main())
