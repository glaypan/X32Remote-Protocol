# -*- coding: utf-8 -*-
"""二维码生成（纯标准库，零第三方依赖）。

用途：手机扫码直达电脑端网页控制台——这是「方案 B」的入口环节，
避免让用户在手机上手工敲 `192.168.x.x:8080`。

实现范围按项目实际需要收窄，不做通用库：
- 字节模式（UTF-8）、纠错等级 **M**、版本 **1-10**（最多 213 字节；
  局域网 URL 约 30 字节，落在版本 2-3）
- 自动挑选掩码（按 ISO/IEC 18004 的四条罚分规则）
- 两种输出：`svg()` 给网页，`block_lines()` 给终端（半块字符，2 行合一）

正确性：`test_qr.py` 用开发环境安装的 segno 生成参考矩阵，与本文 `make()`
在**同版本同掩码**下逐位比对。运行时不需要 segno，也不引入任何依赖。
"""
# 版本 → (每块纠错码字数, 组1块数, 组1数据码字, 组2块数, 组2数据码字)，纠错等级 M
_ECC_M = {
    1: (10, 1, 16, 0, 0),
    2: (16, 1, 28, 0, 0),
    3: (26, 1, 44, 0, 0),
    4: (18, 2, 32, 0, 0),
    5: (24, 2, 43, 0, 0),
    6: (16, 4, 27, 0, 0),
    7: (18, 4, 31, 0, 0),
    8: (22, 2, 38, 2, 39),
    9: (22, 3, 36, 2, 37),
    10: (26, 4, 43, 1, 44),
}
MAX_BYTES = 213          # 版本 10-M 的字节容量

# 校正图形中心坐标（版本 1 无；2-10 行列坐标相同，故用一维表）
_ALIGN = {1: (), 2: (6, 18), 3: (6, 22), 4: (6, 26), 5: (6, 30), 6: (6, 34),
          7: (6, 22, 38), 8: (6, 24, 42), 9: (6, 26, 46), 10: (6, 28, 50)}


def _gf_mul(x, y):
    """GF(256) 乘法，模多项式 0x11D（QR 规定的生成多项式域）。"""
    z = 0
    for i in range(7, -1, -1):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    """生成多项式 ∏(x - 2^i)，返回除首项外的系数。"""
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(len(result)):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < len(result):
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, degree):
    """数据码字 → 纠错码字（RS 余式）。"""
    div = _rs_divisor(degree)
    result = [0] * degree
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i in range(degree):
            result[i] ^= _gf_mul(div[i], factor)
    return result


def _data_codewords(ver):
    _ec, b1, d1, b2, d2 = _ECC_M[ver]
    return b1 * d1 + b2 * d2


def _encode_bits(raw, ver):
    """字节模式编码 + 终止符 + 填充，返回数据码字；装不下返回 None。"""
    cci = 8 if ver <= 9 else 16
    if len(raw) >= (1 << cci):
        return None
    bits = []

    def put(val, n):
        for i in range(n - 1, -1, -1):
            bits.append((val >> i) & 1)

    put(0b0100, 4)                      # 字节模式
    put(len(raw), cci)
    for b in raw:
        put(b, 8)
    cap = _data_codewords(ver) * 8
    if len(bits) > cap:
        return None
    bits += [0] * min(4, cap - len(bits))          # 终止符（最多 4 个 0）
    while len(bits) % 8:                            # 补齐到字节边界
        bits.append(0)
    pad = 0xEC                                      # 交替填充 0xEC / 0x11
    while len(bits) < cap:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]


def _interleave(cw, ver):
    """按块拆分 → 各自算纠错 → 数据区与纠错区分别交织。"""
    ec, b1, d1, b2, d2 = _ECC_M[ver]
    blocks, k = [], 0
    for _ in range(b1):
        blocks.append(cw[k:k + d1])
        k += d1
    for _ in range(b2):
        blocks.append(cw[k:k + d2])
        k += d2
    ecs = [_rs_remainder(b, ec) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ec):
        for e in ecs:
            out.append(e[i])
    return out


def _mask_bit(m, r, c):
    if m == 0:
        return (r + c) % 2 == 0
    if m == 1:
        return r % 2 == 0
    if m == 2:
        return c % 3 == 0
    if m == 3:
        return (r + c) % 3 == 0
    if m == 4:
        return (r // 2 + c // 3) % 2 == 0
    if m == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if m == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _draw_format(mod, fun, size, mask):
    """写入格式信息（纠错等级 M 的两位是 00），两份副本 + 固定深色模块。"""
    data = mask                       # (0 << 3) | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    bits = ((data << 10) | rem) ^ 0x5412

    def bit(i):
        return bool((bits >> i) & 1)

    def setf(r, c, v):
        mod[r][c] = v
        fun[r][c] = True

    for i in range(6):
        setf(i, 8, bit(i))
    setf(7, 8, bit(6))
    setf(8, 8, bit(7))
    setf(8, 7, bit(8))
    for i in range(9, 15):
        setf(8, 14 - i, bit(i))
    for i in range(8):
        setf(8, size - 1 - i, bit(i))
    for i in range(8, 15):
        setf(size - 15 + i, 8, bit(i))
    setf(size - 8, 8, True)


def _place(mod, fun, codewords, size):
    """之字形填充数据区，跳过功能区。"""
    bits = []
    for cw in codewords:
        for i in range(7, -1, -1):
            bits.append((cw >> i) & 1)
    idx, right = 0, size - 1
    while right >= 1:
        if right == 6:                 # 跳过竖向定时图形所在列
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                upward = ((right + 1) & 2) == 0
                y = size - 1 - vert if upward else vert
                if not fun[y][x] and idx < len(bits):
                    mod[y][x] = bits[idx] == 1
                    idx += 1
        right -= 2


def _build(ver, codewords, mask, meta=False):
    """按给定掩码构建矩阵；meta=True 时同时返回功能区标记（测试/诊断用）。"""
    size = 17 + 4 * ver
    mod = [[False] * size for _ in range(size)]
    fun = [[False] * size for _ in range(size)]

    def setf(r, c, v):
        mod[r][c] = v
        fun[r][c] = True

    # 定时图形（先画，随后由定位图形覆盖两端的重叠部分——顺序反了会把
    # 定位图形的竖边/横边冲成浅色，整张码扫不出来）
    for i in range(size):
        setf(6, i, i % 2 == 0)
        setf(i, 6, i % 2 == 0)
    # 三个定位图形（含分隔符）
    for ox, oy in ((0, 0), (size - 7, 0), (0, size - 7)):
        for dy in range(-1, 8):
            for dx in range(-1, 8):
                x, y = ox + dx, oy + dy
                if 0 <= x < size and 0 <= y < size:
                    dark = ((0 <= dx <= 6 and dy in (0, 6))
                            or (0 <= dy <= 6 and dx in (0, 6))
                            or (2 <= dx <= 4 and 2 <= dy <= 4))
                    setf(y, x, dark)
    # 校正图形（跳过与定位图形重叠的三个角）
    pos = _ALIGN[ver]
    k = len(pos)
    for i in range(k):
        for j in range(k):
            if (i == 0 and j == 0) or (i == 0 and j == k - 1) or (i == k - 1 and j == 0):
                continue
            cx, cy = pos[j], pos[i]
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    setf(cy + dy, cx + dx, max(abs(dx), abs(dy)) != 1)
    # 先占位格式区（掩码稍后重写），保证数据填充跳过它们
    _draw_format(mod, fun, size, 0)
    # 版本信息（版本 ≥ 7）
    if ver >= 7:
        rem = ver
        for _ in range(12):
            rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = (ver << 12) | rem
        for i in range(18):
            b = bool((bits >> i) & 1)
            a = size - 11 + i % 3
            setf(i // 3, a, b)
            setf(a, i // 3, b)

    _place(mod, fun, codewords, size)
    for r in range(size):
        for c in range(size):
            if not fun[r][c] and _mask_bit(mask, r, c):
                mod[r][c] = not mod[r][c]
    _draw_format(mod, fun, size, mask)
    return (mod, fun) if meta else mod


def _penalty(mod):
    """ISO 18004 的四条罚分：连续同色 / 2x2 同色 / 类定位图形 / 深色比例。"""
    size = len(mod)
    score = 0
    # N1 行、列连续同色
    for line in (mod, list(map(list, zip(*mod)))):
        for row in line:
            run, prev = 1, row[0]
            for v in row[1:]:
                if v == prev:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + run - 5
                    run, prev = 1, v
            if run >= 5:
                score += 3 + run - 5
    # N2 2x2 同色块
    for r in range(size - 1):
        row, nxt = mod[r], mod[r + 1]
        for c in range(size - 1):
            if row[c] == row[c + 1] == nxt[c] == nxt[c + 1]:
                score += 3
    # N3 类定位图形（1:1:3:1:1 且一侧有 4 个浅色）
    # 边界处也要计分：两侧各补 4 个浅色再滑窗（否则贴边的图案永远漏算）
    pat1 = [True, False, True, True, True, False, True, False, False, False, False]
    pat2 = [False, False, False, False, True, False, True, True, True, False, True]
    for line in (mod, list(map(list, zip(*mod)))):
        for row in line:
            seq = [False] * 4 + list(row) + [False] * 4
            for i in range(len(seq) - 10):
                seg = seq[i:i + 11]
                if seg == pat1 or seg == pat2:
                    score += 40
    # N4 深色比例偏离 50%（按规范取最小整数 k）
    dark = sum(1 for row in mod for v in row if v)
    total = size * size
    k = (abs(dark * 20 - total * 10) + total - 1) // total - 1
    score += max(k, 0) * 10
    return score


def make(text):
    """文本 → 二维码矩阵（True = 深色）。超出容量抛 ValueError。"""
    raw = text.encode("utf-8")
    if len(raw) > MAX_BYTES:
        raise ValueError("内容 %d 字节，超过上限 %d" % (len(raw), MAX_BYTES))
    pick = None
    for ver in range(1, 11):
        cw = _encode_bits(raw, ver)
        if cw is not None:
            pick = (ver, cw)
            break
    if pick is None:
        raise ValueError("内容 %d 字节，超过上限 %d" % (len(raw), MAX_BYTES))
    ver, data = pick
    codewords = _interleave(data, ver)
    best, best_pen = None, None
    for m in range(8):
        mod = _build(ver, codewords, m)
        pen = _penalty(mod)
        if best_pen is None or pen < best_pen:
            best, best_pen = mod, pen
    return best


def version_of(text):
    """返回 (版本号, 边长方块数)，供测试与日志用。"""
    raw = text.encode("utf-8")
    for ver in range(1, 11):
        if _encode_bits(raw, ver) is not None:
            return ver, 17 + 4 * ver
    raise ValueError("内容过长")


def svg(text, scale=6, border=4):
    """二维码 → SVG 字符串（白底黑块，四周留 4 模块静区）。"""
    mod = make(text)
    n = len(mod)
    dim = n + 2 * border
    seg = []
    for r, row in enumerate(mod):
        c = 0
        while c < n:
            if row[c]:
                start = c
                while c < n and row[c]:
                    c += 1
                seg.append("M%d %dh%dv1h-%dz" % (start + border, r + border, c - start, c - start))
            else:
                c += 1
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d" shape-rendering="crispEdges" role="img" '
        'aria-label="扫码连接">'
        '<rect width="100%%" height="100%%" fill="#ffffff"/>'
        '<path d="%s" fill="#000000"/></svg>'
        % (dim, dim, dim * scale, dim * scale, "".join(seg))
    )


def block_lines(text, border=4):
    """二维码 → 终端行（半块字符，两行合一）。

    面向**深色背景**终端：浅色模块用亮块、深色模块留空，这样扫码看到的是
    「浅底深块」的正确极性。行高约为字宽两倍时比例最准（常见等宽字体如此）。
    """
    mod = make(text)
    n = len(mod)
    dim = n + 2 * border

    def dark_at(r, c):
        r -= border
        c -= border
        return 0 <= r < n and 0 <= c < n and mod[r][c]

    lines = []
    for r in range(0, dim, 2):
        buf = []
        for c in range(dim):
            top = not dark_at(r, c)              # True = 亮块
            bot = not dark_at(r + 1, c) if r + 1 < dim else True
            if top and bot:
                buf.append("\u2588")             # █
            elif top:
                buf.append("\u2580")             # ▀
            elif bot:
                buf.append("\u2584")             # ▄
            else:
                buf.append(" ")
        lines.append("".join(buf))
    return lines
