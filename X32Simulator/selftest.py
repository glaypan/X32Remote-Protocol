#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模拟器自测脚本:模拟 iOS App 的完整交互流程
用法: python3 selftest.py [模拟器IP] (默认 127.0.0.1)
"""

import math
import socket
import struct
import sys
import time

sys.path.insert(0, __file__.rsplit("\\", 1)[0] if "\\" in __file__ else ".")
from x32_simulator import decode_packet, encode_message, encode_bundle, OscMsg

HOST = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
PORT = 10023
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.settimeout(2.0)
sock.bind(("127.0.0.1", 0))

passed, failed = [], []


def rpc(msg, expect_reply=True, timeout=2.0):
    sock.sendto(encode_message(msg), (HOST, PORT))
    if not expect_reply:
        time.sleep(0.2)
        return []
    end = time.time() + timeout
    replies = []
    while time.time() < end and not replies:
        try:
            data, _ = sock.recvfrom(8192)
            replies = decode_packet(data)
        except socket.timeout:
            break
        except OSError:
            # 模拟器没启动时 Windows 会回 ICMP 端口不可达 (10054), 不该炸脚本
            break
    return replies


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  " + detail) if detail and not cond else ""))


print("== 1. /xinfo 自动发现 ==")
replies = rpc(OscMsg("/xinfo"))
ok = len(replies) == 1 and replies[0].address == "/xinfo" and len(replies[0].args) == 4
check("xinfo 返回 4 字符串", ok, str(replies and replies[0].args))
if ok:
    print("        name=%s model=%s fw=%s" % (replies[0].args[1], replies[0].args[2], replies[0].args[3]))

print("== 2. 单条查询 /ch/01/mix/fader ==")
replies = rpc(OscMsg("/ch/01/mix/fader"))
check("返回 fader=0.75", len(replies) == 1 and abs(replies[0].args[0] - 0.75) < 0.001, str(replies))

print("== 3. 设置推子并回读 ==")
rpc(OscMsg("/ch/05/mix/fader", [0.42]), expect_reply=False)
replies = rpc(OscMsg("/ch/05/mix/fader"))
check("回读 fader=0.42", len(replies) == 1 and abs(replies[0].args[0] - 0.42) < 0.001, str(replies))

print("== 4. 静音设置 (int) ==")
rpc(OscMsg("/ch/07/mix/on", [0]), expect_reply=False)
replies = rpc(OscMsg("/ch/07/mix/on"))
check("ch07 on=0", len(replies) == 1 and replies[0].args[0] == 0, str(replies))
rpc(OscMsg("/ch/07/mix/on", [1]), expect_reply=False)

print("== 5. DCA 补零地址 ==")
rpc(OscMsg("/dca/03/fader", [0.6]), expect_reply=False)
replies = rpc(OscMsg("/dca/03/fader"))
check("dca/03 fader=0.6", len(replies) == 1 and abs(replies[0].args[0] - 0.6) < 0.001, str(replies))

print("== 6. Main LR ==")
rpc(OscMsg("/main/st/mix/fader", [0.8]), expect_reply=False)
replies = rpc(OscMsg("/main/st/mix/fader"))
check("main/st fader=0.8", len(replies) == 1 and abs(replies[0].args[0] - 0.8) < 0.001, str(replies))

print("== 7. 通配符查询 (Bundle 回包) ==")
replies = rpc(OscMsg("/ch/*/mix/fader"))
check("返回 32 条 Bundle 消息", len(replies) == 32, "got %d" % len(replies))
ok_addr = all(r.address == "/ch/%02d/mix/fader" % i for i, r in enumerate(replies, 1))
check("Bundle 内地址补零正确", ok_addr)
rpc(OscMsg("/dca/*/fader"))
replies = rpc(OscMsg("/dca/*/fader"))
check("DCA 通配符返回 8 条", len(replies) == 8, "got %d" % len(replies))

print("== 8. Bundle 指令输入 ==")
bundle = encode_bundle([OscMsg("/ch/10/mix/fader", [0.33]), OscMsg("/dca/01/on", [0])])
sock.sendto(bundle, (HOST, PORT))
time.sleep(0.3)
r1 = rpc(OscMsg("/ch/10/mix/fader"))
r2 = rpc(OscMsg("/dca/01/on"))
check("Bundle 中 fader 生效", len(r1) == 1 and abs(r1[0].args[0] - 0.33) < 0.001, str(r1))
check("Bundle 中 mute 生效", len(r2) == 1 and r2[0].args[0] == 0, str(r2))
rpc(OscMsg("/dca/01/on", [1]), expect_reply=False)

print("== 9. 电平表 blob 流 ==")
sock.sendto(encode_message(OscMsg("/meters/1")), (HOST, PORT))
blobs = 0
values_ok = True
deadline = time.time() + 2.5
while time.time() < deadline and blobs < 5:
    try:
        data, _ = sock.recvfrom(8192)
    except socket.timeout:
        continue
    for m in decode_packet(data):
        if m.address == "/meters/1" and isinstance(m.args[0], bytes):
            blobs += 1
            (count,) = struct.unpack(">i", m.args[0][:4])
            floats = struct.unpack(">%df" % count, m.args[0][4:4 + count * 4])
            if count != 48 or len(floats) != 48:   # 32 输入通道 + 16 Bus
                values_ok = False
            if all(v == 0 for v in floats):
                values_ok = False
check("2.5 秒内收到 >=5 个 blob", blobs >= 5, "got %d" % blobs)
check("blob 均为 48 路(32通道+16Bus)且电平动态变化", values_ok)
sock.sendto(encode_message(OscMsg("/meters/1", [0])), (HOST, PORT))
time.sleep(0.4)
try:
    sock.settimeout(1.0)
    leftover = sock.recvfrom(8192)
    check("退订后不再推送", False, "still receiving")
except socket.timeout:
    check("退订后不再推送", True)
sock.settimeout(2.0)

print()
print("结果: %d 通过, %d 失败" % (len(passed), len(failed)))
if failed:
    print("失败项:", failed)
sys.exit(1 if failed else 0)
