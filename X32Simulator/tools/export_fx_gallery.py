# -*- coding: utf-8 -*-
"""tools/export_fx_gallery.py — 效果器图鉴元数据与原生端资源导出。

效果器面板图是真机界面截图（X32 侧由 X32-Edit 4.4 离线模式逐算法渲染后
截取；WING 侧取自官方手册站的 FX 选择页），仅用于"识别这是哪个效果器"，
不参与任何参数运算。图片本身由 tools/collect_fx_shots 系列的采集脚本产出，
这里只负责把**元数据**与**资源**同步给三端，避免各端各自维护一份清单。

用法：
    python X32Simulator/tools/export_fx_gallery.py            # 生成 + 导出
    python X32Simulator/tools/export_fx_gallery.py --check     # 校验是否过期（回归用）
"""
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from x32sim.fxlib import FX_CAT_LABEL, FX_LABEL_EN, FX_SLOTS_HEAVY, X32_FX_ALGOS  # noqa: E402

WEB = os.path.join(BASE, "web", "fx")
JSON_OUT = os.path.join(WEB, "fxlib.json")
IOS_DIR = os.path.join(BASE, "..", "X32Remote-ios", "App", "Resources", "fx")
AND_DIR = os.path.join(os.environ.get("X32_ANDROID_DIR",
                                      os.path.join(BASE, "..", "..", "X32RemoteAndroid")),
                       "app", "src", "main", "assets", "fx")

WING_GROUPS = ("premium", "standard")
WING_LABEL = {
    "premium": "高级效果器（仅 WING 的 FX1-8 槽）",
    "standard": "标准效果器（WING 的 FX9-16 槽，X32 也有同族）",
}


def img_name(code):
    return code.replace("/", "_") + ".webp"


def x32_rows():
    out = []
    for code in X32_FX_ALGOS:                      # 顺序 = 枚举顺序
        d = X32_FX_ALGOS[code]
        out.append({
            "code": code,
            "cn": d.get("label", code),
            "en": FX_LABEL_EN.get(code, code),
            "cat": d.get("cat", "misc"),
            "catCn": FX_CAT_LABEL.get(d.get("cat", "misc"), d.get("cat", "misc")),
            "n": d.get("n", 0),
            "heavy": 1 if d.get("heavy") else 0,
            "img": "x32/" + img_name(code),
        })
    return out


def wing_rows():
    """WING 侧只有图片和名称（本工具不控 WING，纯参考图鉴）。"""
    out = {}
    for g in WING_GROUPS:
        d = os.path.join(WEB, "wing", g)
        rows = []
        for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            if not f.endswith(".webp"):
                continue
            slug = f[:-5]
            rows.append({"slug": slug,
                         "name": slug.replace("-", " ").upper(),
                         "img": "wing/%s/%s" % (g, f)})
        out[g] = {"label": WING_LABEL[g], "items": rows}
    return out


def build_payload():
    return {
        "v": 1,
        "note": "由 X32Simulator/tools/export_fx_gallery.py 生成, 禁止手改",
        "x32": {
            "label": "X32 / M32 效果库",
            "count": len(X32_FX_ALGOS),
            "heavySlots": list(FX_SLOTS_HEAVY),
            "items": x32_rows(),
        },
        "wing": wing_rows(),
    }


def dump(payload):
    return json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=False) + "\n"


def wanted(dest):
    """dest 下应该存在的相对路径集合（与 fxlib.json 里的 img 字段同构）。"""
    out = {"fxlib.json"}
    for root, _dirs, files in os.walk(WEB):
        rel = os.path.relpath(root, WEB)
        for f in files:
            if f.endswith(".webp"):
                out.add(f if rel == "." else "%s/%s" % (rel.replace("\\", "/"), f))
    return out


def sync_tree(dest):
    """把 web/fx 整棵树按 x32/ 、wing/<组>/ 的布局同步到原生端。

    原生端读资源用的是 fxlib.json 里的 img 字段（如 x32/HALL.webp），
    所以目录结构必须原样保留，各端拼 <root>/<img> 即可。
    """
    os.makedirs(dest, exist_ok=True)
    want = wanted(dest)
    # 清掉上一版遗留的平铺文件（早期版本把 61 张直接放根目录）
    for f in os.listdir(dest):
        p = os.path.join(dest, f)
        if os.path.isfile(p) and f.endswith((".webp", ".json")) and f not in want:
            os.remove(p)
    n = 0
    for rel in sorted(want):
        s = os.path.join(WEB, rel)
        if not os.path.isfile(s):
            continue
        d = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        n += 1
    return n


def main():
    check = "--check" in sys.argv
    body = dump(build_payload())

    if check:
        stale = []
        try:
            with open(JSON_OUT, encoding="utf-8") as f:
                if f.read() != body:
                    stale.append(JSON_OUT)
        except OSError:
            stale.append(JSON_OUT + " (缺失)")
        # 原生端资源只是拷贝，逐个比对文件名是否齐全
        for d in (IOS_DIR, AND_DIR):
            if not os.path.isdir(d):
                continue                      # 别的机器上可能没有这个消费者
            have = set()
            for root, _dirs, files in os.walk(d):
                rel = os.path.relpath(root, d)
                for f in files:
                    have.add(f if rel == "." else "%s/%s" % (rel.replace("\\", "/"), f))
            miss = sorted(wanted(d) - have)
            if miss:
                stale.append("%s (缺 %d: %s)" % (d, len(miss), ", ".join(miss[:3])))
        if stale:
            print("效果器图鉴导出物已过期: " + ", ".join(stale))
            print("跑 python X32Simulator/tools/export_fx_gallery.py 重新导出")
            return 1
        print("效果器图鉴导出物最新")
        return 0

    with open(JSON_OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    print("写出", JSON_OUT)

    for d in (IOS_DIR, AND_DIR):
        try:
            print("同步 %d 个资源到 %s" % (sync_tree(d), d))
        except OSError as e:
            print("跳过 %s (%s)" % (d, e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
