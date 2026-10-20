# -*- coding: utf-8 -*-
"""x32sim/kinds — (kind, idx) 语义管道的命名规则 (纯函数, 零依赖)"""

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def fx_par_kind(pnum):
    """参数号 → kind。用 'fx_par_01' 这样既能在 (kind, idx) 管道里走, 又能反解参数号。"""
    return "fx_par_%02d" % int(pnum)


def fx_par_num(kind):
    """kind → 参数号; 不是 FX 参数则返回 None。"""
    if isinstance(kind, str) and kind.startswith("fx_par_"):
        try:
            return int(kind[7:])
        except ValueError:
            return None
    return None


def kind_group(kind):
    """'ch_fader' -> 'ch'"""
    return kind.rsplit("_", 1)[0]
