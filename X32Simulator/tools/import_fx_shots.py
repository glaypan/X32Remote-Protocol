# -*- coding: utf-8 -*-
"""tools/import_fx_shots.py — 把采集到的截图变成 `web/fx/` 里的 WebP 资产。

两段来源，一个出口：

  A. X32：`collect_fx_shots.py` 的产物（`NN_panel.png`，480×134）
     → `web/fx/x32/<算法枚举名>.webp`（`/` 换成 `_`）
  B. WING：官方手册站的整页 FX 选择页截图（1280×800，从
     `https://wing-fx.com/images/fx/premium-all.png` 与 `standard-all.png` 下载）
     → 按 6 列 × 7 行网格裁切 → `web/fx/wing/<组>/<slug>.webp`

用法：
    # X32（--offset 是采集起始项在算法表里的下标，见下）
    python X32Simulator/tools/import_fx_shots.py x32 --shots outputs/fxshots --offset 31

    # WING（两个整页截图）
    python X32Simulator/tools/import_fx_shots.py wing \
        --premium outputs/fxsrc/wing_premium_all.png \
        --standard outputs/fxsrc/wing_standard_all.png

⚠️ `--offset` 不是随便填的：`collect_fx_shots.py` 从下拉框当前项开始按 Down
往下走，而 Qt 下拉框的 `Home` 不可靠（只到"可见区首项"），所以第 0 张
未必是算法表第 0 个。本次实测第 0 张 = 算法表第 31 个，故 offset=31。
**换机器/换版本重采后必须重新核对**（读名称条或人工看图），不能沿用 31。

做完这一步要接着跑：
    python X32Simulator/tools/export_fx_gallery.py     # 元数据 + 同步三端
    python X32Simulator/tools/build_fx_assets.py       # base64 内嵌
    python X32Simulator/tools/make_single.py           # 合成单文件
"""
import argparse
import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from x32sim.fxlib import X32_FX_ALGOS      # noqa: E402

WEB = os.path.join(BASE, "web", "fx")

# WING 选择页的网格（1280×800 整页，实测坐标）
WING_COLS = [(120, 291), (300, 471), (480, 651), (660, 831), (840, 1011), (1020, 1191)]
WING_ROWS = [(88, 179), (188, 279), (288, 379), (388, 479),
             (488, 579), (588, 679), (688, 779)]


def img_name(code):
    return code.replace("/", "_") + ".webp"


def do_x32(shots, offset, quality):
    codes = list(X32_FX_ALGOS)
    n = len(codes)
    files = sorted(f for f in os.listdir(shots) if f.endswith("_panel.png"))
    if len(files) < n:
        raise SystemExit("截图不足：%d < %d" % (len(files), n))
    out = os.path.join(WEB, "x32")
    os.makedirs(out, exist_ok=True)
    for i, code in enumerate(codes):
        src = os.path.join(shots, files[(i + offset) % n])
        im = Image.open(src).convert("RGB")
        im.save(os.path.join(out, img_name(code)), "WEBP", quality=quality, method=6)
    print("X32: %d 张 -> %s" % (n, out))


def do_wing(premium, standard, quality):
    for group, path in (("premium", premium), ("standard", standard)):
        if not path or not os.path.isfile(path):
            print("跳过 WING %s（没给图）" % group)
            continue
        page = Image.open(path).convert("RGB")
        out = os.path.join(WEB, "wing", group)
        os.makedirs(out, exist_ok=True)
        k = 0
        for (y0, y1) in WING_ROWS:
            for (x0, x1) in WING_COLS:
                tile = page.crop((x0, y0, x1, y1))
                # 空白格子（纯黑）跳过，编号靠行优先顺序
                if max(tile.convert("L").getdata()) < 8:
                    continue
                k += 1
                tile.save(os.path.join(out, "%s-%02d.webp" % (group, k)),
                          "WEBP", quality=quality, method=6)
        print("WING %s: %d 张 -> %s" % (group, k, out))
        print("  ⚠️ 文件名只是占位编号，需要按图上的名称改成真名"
              "（如 hall-reverb.webp）；改完重跑 export_fx_gallery.py")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["x32", "wing"])
    ap.add_argument("--shots", default="outputs/fxshots")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--premium", default=None)
    ap.add_argument("--standard", default=None)
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()
    if a.mode == "x32":
        do_x32(a.shots, a.offset, a.quality)
    else:
        do_wing(a.premium, a.standard, a.quality)
    return 0


if __name__ == "__main__":
    sys.exit(main())
