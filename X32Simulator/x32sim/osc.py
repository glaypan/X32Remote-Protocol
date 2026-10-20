# -*- coding: utf-8 -*-
"""x32sim/osc — OSC 编解码 (纯函数, 零状态)"""

import struct

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ---------------------------------------------------------------- OSC 编解码

def pad4(data: bytes) -> bytes:
    return data + b"\x00" * ((4 - len(data) % 4) % 4)


def encode_string(s: str) -> bytes:
    return pad4(s.encode("utf-8") + b"\x00")


def next_aligned(offset: int) -> int:
    return (offset + 3) // 4 * 4


class OscMsg:
    def __init__(self, address, args=None):
        self.address = address
        self.args = args or []


def encode_message(msg: OscMsg) -> bytes:
    out = pad4(msg.address.encode("utf-8") + b"\x00")
    tags = b","
    payloads = b""
    for a in msg.args:
        if isinstance(a, int) and not isinstance(a, bool):
            tags += b"i"
            payloads += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += b"f"
            payloads += struct.pack(">f", a)
        elif isinstance(a, str):
            tags += b"s"
            payloads += encode_string(a)
        elif isinstance(a, bytes):
            tags += b"b"
            payloads += struct.pack(">i", len(a)) + pad4(a)
        elif a is True:
            tags += b"T"
        elif a is False:
            tags += b"F"
    out += pad4(tags + b"\x00") + payloads
    return out


def encode_bundle(messages) -> bytes:
    out = b"#bundle\x00" + b"\x00" * 8  # timetag 置零
    for m in messages:
        body = encode_message(m) if isinstance(m, OscMsg) else m
        out += struct.pack(">i", len(body)) + body
    return out


def decode_packet(data: bytes):
    """解码收到的 UDP 包,返回 OscMsg 列表 (自动展开 Bundle)。"""
    if data.startswith(b"#bundle"):
        out = []
        offset = 16
        while offset + 4 <= len(data):
            (size,) = struct.unpack(">i", data[offset:offset + 4])
            offset += 4
            if size <= 0 or offset + size > len(data):
                break
            out.extend(decode_packet(data[offset:offset + size]))
            offset += size
        return out
    z = data.index(b"\x00")
    address = data[:z].decode("utf-8", errors="replace")
    offset = next_aligned(z + 1)
    if offset >= len(data) or data[offset:offset + 1] != b",":
        return [OscMsg(address)]
    zend = data.index(b"\x00", offset)
    tags = data[offset + 1:zend].decode("ascii", errors="replace")
    offset = next_aligned(zend + 1)
    args = []
    for t in tags:
        if t == "i":
            (v,) = struct.unpack(">i", data[offset:offset + 4]); args.append(v); offset += 4
        elif t == "f":
            (v,) = struct.unpack(">f", data[offset:offset + 4]); args.append(v); offset += 4
        elif t == "s":
            end = data.find(b"\x00", offset)
            if end < 0:                  # 畸形包: 字符串没有结尾符, 停止解析
                break
            args.append(data[offset:end].decode("utf-8", errors="replace"))
            offset = next_aligned(end + 1)
        elif t == "b":
            (n,) = struct.unpack(">i", data[offset:offset + 4]); offset += 4
            args.append(data[offset:offset + n]); offset = next_aligned(offset + n)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
    return [OscMsg(address, args)]
