# -*- coding: utf-8 -*-
"""二维码编码器回归（纯标准库，不需要起服务、不依赖第三方库）。

验证思路：**不解回原文就不算对**。
1. 结构：尺寸公式、三个定位图形、定时图形、两份格式信息一致且 BCH 自洽
2. 纠错：每个 RS 块在 α^0..α^(ec-1) 处取值全为 0；翻转模块后必须变为非 0
   （前者说明码字有效，后者说明校验位确实覆盖了数据）
3. 往返：读格式信息 → 解掩码 → 还原码字 → 解交织 → 解析模式/长度/数据
   → 必须等于原始字符串
4. 可选交叉验证：装了 python-qrcode 时，要求存在某个掩码使矩阵与参考逐位一致
   （掩码选择是实现自由，两边选不同掩码不算错）

    python test_qr.py
"""
import random
import sys

sys.path.insert(0, ".")
from x32sim import qr  # noqa: E402

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + str(detail)) if detail and not ok else ""))


# ---------------------------------------------------------------- 解码工具（仅测试用）
def format_bits(mod):
    """读左上那份格式信息，返回 (15 位原始值, 纠错等级位, 掩码号, BCH 是否自洽)。"""
    def g(r, c):
        return 1 if mod[r][c] else 0

    seq = ([g(i, 8) for i in range(6)] + [g(7, 8), g(8, 8), g(8, 7)]
           + [g(8, 14 - i) for i in range(9, 15)])
    val = 0
    for i, b in enumerate(seq):
        val |= b << i
    raw = val ^ 0x5412
    data, rem = raw >> 10, raw & 0x3FF
    calc = data
    for _ in range(10):
        calc = (calc << 1) ^ ((calc >> 9) * 0x537)
    return val, data >> 3, data & 7, (calc & 0x3FF) == rem


def second_format_bits(mod):
    """读另一份格式信息（右上 + 左下）。"""
    size = len(mod)
    bits = 0
    for i in range(8):
        bits |= (1 if mod[8][size - 1 - i] else 0) << i
    for i in range(8, 15):
        bits |= (1 if mod[size - 15 + i][8] else 0) << i
    return bits


def total_codewords(ver):
    ec, b1, d1, b2, d2 = qr._ECC_M[ver]
    return b1 * d1 + b2 * d2 + ec * (b1 + b2)


def read_codewords(mod, ver, mask):
    """解掩码后按填充顺序读回码字（功能区布局与编码器同源）。"""
    size = len(mod)
    _, fun = qr._build(ver, [0] * total_codewords(ver), mask, meta=True)
    work = [[bool(v) for v in row] for row in mod]
    for r in range(size):
        for c in range(size):
            if not fun[r][c] and qr._mask_bit(mask, r, c):
                work[r][c] = not work[r][c]
    bits, right = [], size - 1
    while right >= 1:
        if right == 6:
            right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j
                up = ((right + 1) & 2) == 0
                y = size - 1 - vert if up else vert
                if not fun[y][x]:
                    bits.append(1 if work[y][x] else 0)
        right -= 2
    return [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8)]


def deinterleave(cw, ver):
    """交织后的码字 → [(数据块, 纠错块)]。"""
    ec, b1, d1, b2, d2 = qr._ECC_M[ver]
    nb, lens = b1 + b2, [d1] * b1 + [d2] * b2
    blocks = [[] for _ in range(nb)]
    k = 0
    for i in range(max(lens)):
        for b in range(nb):
            if i < lens[b]:
                blocks[b].append(cw[k])
                k += 1
    ecs = [[0] * ec for _ in range(nb)]
    for i in range(ec):
        for b in range(nb):
            ecs[b][i] = cw[k]
            k += 1
    return [(blocks[b], ecs[b]) for b in range(nb)]


def rs_first_bad(data, ecc):
    """返回第一个非零 syndrome 的下标；全零返回 -1（Horner 求值）。"""
    poly = list(data) + list(ecc)
    for i in range(len(ecc)):
        root = 1
        for _ in range(i):
            root = qr._gf_mul(root, 2)
        acc = 0
        for coef in poly:
            acc = qr._gf_mul(acc, root) ^ coef
        if acc != 0:
            return i
    return -1


def decode(text):
    """矩阵 → (文本, 说明)；失败时文本为 None。"""
    mod = qr.make(text)
    ver = (len(mod) - 17) // 4
    _, ecl, mask, bch_ok = format_bits(mod)
    if not bch_ok:
        return None, "格式信息 BCH 不自洽"
    if ecl != 0:
        return None, "纠错等级不是 M"
    payload = []
    for data, ecc in deinterleave(read_codewords(mod, ver, mask), ver):
        if rs_first_bad(data, ecc) != -1:
            return None, "RS 校验位不为零"
        payload.extend(data)
    bits = "".join(format(b, "08b") for b in payload)
    if int(bits[:4], 2) != 0b0100:
        return None, "模式不是字节模式"
    cci = 8 if ver <= 9 else 16
    n = int(bits[4:4 + cci], 2)
    body = bits[4 + cci:4 + cci + n * 8]
    try:
        return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8)).decode("utf-8"), "ok"
    except Exception as e:
        return None, "解码失败 %r" % e


URL = "http://192.168.1.5:8080/"

# ---------------------------------------------------------------- 1. 版本与尺寸
print("== 1. 版本选择与尺寸 ==")
check("1 字节 → 版本 1 / 21 模块", qr.version_of("x") == (1, 21), qr.version_of("x"))
check("29 字节 URL → 版本 3 / 29 模块", qr.version_of("http://192.168.100.200:28080/") == (3, 29),
      qr.version_of("http://192.168.100.200:28080/"))
check("边长公式 17+4v 逐档成立",
      all(len(qr.make("x" * k)) == qr.version_of("x" * k)[1] for k in (1, 20, 60, 120, 200)))
try:
    qr.make("A" * 214)
    check("超长内容抛 ValueError", False, "未抛异常")
except ValueError:
    check("超长内容抛 ValueError", True)

# ---------------------------------------------------------------- 2. 结构
print("\n== 2. 功能图形与格式信息 ==")
m = qr.make(URL)
n = len(m)


def finder_ok(mat, ox, oy):
    for dy in range(7):
        for dx in range(7):
            want = dy in (0, 6) or dx in (0, 6) or (2 <= dy <= 4 and 2 <= dx <= 4)
            if mat[oy + dy][ox + dx] != want:
                return False
    return True


check("左上定位图形正确", finder_ok(m, 0, 0))
check("右上定位图形正确", finder_ok(m, n - 7, 0))
check("左下定位图形正确", finder_ok(m, 0, n - 7))
check("竖向定时图形交替", all(m[i][6] == (i % 2 == 0) for i in range(8, n - 8)))
check("横向定时图形交替", all(m[6][i] == (i % 2 == 0) for i in range(8, n - 8)))
val, ecl, mask, bch = format_bits(m)
check("格式信息 BCH 自洽", bch)
check("纠错等级为 M", ecl == 0, ecl)
check("两份格式信息一致", second_format_bits(m) == val,
      "%x vs %x" % (second_format_bits(m), val))

# ---------------------------------------------------------------- 3. 纠错与往返
print("\n== 3. RS 校验与原文往返 ==")
samples = ["x", "http://192.168.1.5:8080/", "http://10.0.0.123:8080/",
           "http://172.16.31.200:28080/", "http://192.168.100.200:8080/?from=qr"]
for s in samples:
    got, why = decode(s)
    check("往返解回原文: %s" % s[:36], got == s, "%s / %s" % (got, why))

# 翻转模块后校验必须失败 —— 证明校验位真的覆盖到这些位置（而不是摆设）
random.seed(7)
mod0 = qr.make(URL)
ver0 = (len(mod0) - 17) // 4
_, _, mask0, _ = format_bits(mod0)
_, fun0 = qr._build(ver0, [0] * total_codewords(ver0), mask0, meta=True)
free = [(r, c) for r in range(len(mod0)) for c in range(len(mod0)) if not fun0[r][c]]
broken = 0
for _ in range(20):
    mm = [[bool(v) for v in row] for row in mod0]
    for r, c in random.sample(free, 3):
        mm[r][c] = not mm[r][c]
    ok_after = all(rs_first_bad(d, e) == -1
                   for d, e in deinterleave(read_codewords(mm, ver0, mask0), ver0))
    if ok_after:
        broken += 1
check("翻转 3 个模块后 RS 校验必失败（20 次）", broken == 0, "%d 次误判为有效" % broken)

# ---------------------------------------------------------------- 4. 输出形态
print("\n== 4. SVG 与终端形态 ==")
svg = qr.svg(URL)
check("SVG 含 viewBox", 'viewBox="0 0' in svg)
check("SVG 有白底与路径", 'fill="#ffffff"' in svg and "<path" in svg)
check("SVG 尺寸含静区", ('viewBox="0 0 %d %d"' % (n + 8, n + 8)) in svg, svg[:90])
lines = qr.block_lines(URL, border=4)
check("终端行数 = 高度的一半", len(lines) == (n + 8 + 1) // 2, len(lines))
check("终端每行等宽", len({len(x) for x in lines}) == 1, {len(x) for x in lines})
check("终端字符集合法", all(set(x) <= set("\u2580\u2584\u2588 ") for x in lines))
check("不同内容生成不同矩阵", qr.make("http://a/") != qr.make("http://b/"))

# ---------------------------------------------------------------- 5. 可选交叉验证
print("\n== 5. 交叉验证（装了 python-qrcode 才跑）==")
try:
    import qrcode  # noqa: E402
    from qrcode.constants import ERROR_CORRECT_M  # noqa: E402
    for s in samples:
        q = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=1, border=0)
        q.add_data(s, optimize=0)          # 关掉自动分段，对齐"单一字节段"
        q.make(fit=True)
        ref = [[bool(v) for v in row] for row in q.get_matrix()]
        cw = qr._interleave(qr._encode_bits(s.encode(), q.version), q.version)
        same_cls = any(qr._build(q.version, cw, mk) == ref for mk in range(8))
        check("与 python-qrcode 同版本同掩码逐位一致: %s" % s[:30], same_cls)
except ImportError:
    print("SKIP 未安装 python-qrcode（运行时不需要它）")

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
print("%d/%d 通过" % (sum(results), len(results)))
for i, name, detail in failed:
    print("失败项 #%d: %s%s" % (i, name, (" | " + str(detail)) if detail else ""))
sys.exit(0 if all(results) else 1)
