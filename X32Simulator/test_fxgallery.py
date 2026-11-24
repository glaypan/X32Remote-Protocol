# -*- coding: utf-8 -*-
"""效果器图鉴回归（真机截图 → 三端）。

要点：**图必须真的在包里、必须真能取到**，否则前端只是安静地不显示图片，
用户看到的是「图鉴空白」而不是报错。所以这里盯三件事：

1. 导出物不过期：web/fx/fxlib.json 与登记表一致（跑 --check），
   61 个算法一个不少、每条的 img 都真的有对应文件。
2. 内嵌资源自洽：x32sim/fxassets.py 的 FX_ASSETS 键集合 == web/fx 下的实际文件，
   且每张 webp 解 base64 后是合法 RIFF/WEBP（不是被 git 换行符毁掉的文本）。
3. HTTP 能取到：/fx/fxlib.json 与 /fx/x32/<code>.webp 返 200 + 正确 Content-Type +
   长缓存头；越界路径必须 404（别把整个包目录暴露成静态服务）。

另外校验网页端已接入（WEB_PAGE 里有图鉴入口与弹窗骨架），
以及 FX_LABEL_EN 覆盖全部算法（图鉴里的英文名就来自它）。

    python test_fxgallery.py            # 需要 127.0.0.1:28080 上已跑着模拟器
"""
import base64
import json
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, ".")
from x32sim import fxassets, webassets                     # noqa: E402
from x32sim.fxlib import FX_LABEL_EN, X32_FX_ALGOS          # noqa: E402

WEB = "http://127.0.0.1:%d" % int(os.environ.get("X32_TEST_WEB", "28080"))
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # 本机不走代理

results = []
failed = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    if not ok:
        failed.append((len(results), name, detail))
    print(("PASS " if ok else "FAIL ") + name + (("  | " + str(detail)) if detail and not ok else ""))


def fetch(path):
    try:
        r = OPENER.open(WEB + path, timeout=6)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), b""
    except Exception as e:
        return 0, {}, str(e).encode()


ROOT = os.path.dirname(os.path.abspath(__file__))
FXDIR = os.path.join(ROOT, "web", "fx")


def disk_files():
    out = set()
    for dirpath, _dirs, files in os.walk(FXDIR):
        rel = os.path.relpath(dirpath, FXDIR).replace("\\", "/")
        for f in files:
            if f.endswith((".webp", ".json")):
                out.add(f if rel == "." else "%s/%s" % (rel, f))
    return out


# ---------------------------------------------------------------- 1. 导出物
print("== 1. 导出物与登记表一致 ==")
r = subprocess.run([sys.executable, os.path.join("tools", "export_fx_gallery.py"), "--check"],
                   cwd=ROOT, capture_output=True, text=True,
                   encoding="utf-8", errors="replace")
check("export_fx_gallery.py --check 通过", r.returncode == 0,
      (r.stdout or r.stderr).strip()[:200])

with open(os.path.join(FXDIR, "fxlib.json"), encoding="utf-8") as f:
    lib = json.load(f)

check("X32 算法数 = 效果库 61", lib["x32"]["count"] == len(X32_FX_ALGOS) == 61,
      "%s / %s" % (lib["x32"]["count"], len(X32_FX_ALGOS)))
check("X32 条目数与 count 一致", len(lib["x32"]["items"]) == lib["x32"]["count"])

codes = {i["code"] for i in lib["x32"]["items"]}
check("X32 算法编码与 FX_ENUM 完全一致", codes == set(X32_FX_ALGOS),
      "缺 %s / 多 %s" % (sorted(set(X32_FX_ALGOS) - codes)[:3], sorted(codes - set(X32_FX_ALGOS))[:3]))
check("FX_LABEL_EN 覆盖全部算法", set(FX_LABEL_EN) == set(X32_FX_ALGOS),
      sorted(set(X32_FX_ALGOS) - set(FX_LABEL_EN))[:3])

# 每条都要有图，且文件名与 code 规范一致（/ 换成 _）
miss = [i["code"] for i in lib["x32"]["items"]
        if not os.path.isfile(os.path.join(FXDIR, i["img"]))]
check("61 张 X32 面板图文件齐全", not miss, miss[:5])
bad_name = [i["code"] for i in lib["x32"]["items"]
            if i["img"] != "x32/" + i["code"].replace("/", "_") + ".webp"]
check("X32 图片命名规则统一", not bad_name, bad_name[:5])

# WING 52 张：26 高级 + 26 标准
wing = lib["wing"]
check("WING 两个组都在", set(wing) >= {"premium", "standard"}, list(wing))
check("WING 高级 26 张", len(wing["premium"]["items"]) == 26, len(wing["premium"]["items"]))
check("WING 标准 26 张", len(wing["standard"]["items"]) == 26, len(wing["standard"]["items"]))
wmiss = [i["img"] for g in wing.values() for i in g["items"]
         if not os.path.isfile(os.path.join(FXDIR, i["img"]))]
check("52 张 WING 图文件齐全", not wmiss, wmiss[:5])

# ---------------------------------------------------------------- 2. 内嵌资源
print("\n== 2. 内嵌 base64 资源自洽 ==")
assets = fxassets.FX_ASSETS
check("FX_ASSETS 键集合 = web/fx 实际文件", set(assets) == disk_files(),
      "缺 %s / 多 %s" % (sorted(disk_files() - set(assets))[:3], sorted(set(assets) - disk_files())[:3]))

bad = []
for k, v in assets.items():
    if not k.endswith(".webp"):
        continue
    try:
        raw = base64.b64decode(v)
    except Exception:
        bad.append(k + " (base64 坏)")
        continue
    if not (raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"):
        bad.append(k + " (不是 WEBP)")
check("每张内嵌图都是合法 WEBP", not bad, bad[:3])

total = sum(len(v) for v in assets.values())
check("内嵌资源总量 < 1.2 MB（单文件分发红线）", total < 1_200_000, "%d 字节" % total)

# ---------------------------------------------------------------- 3. HTTP
print("\n== 3. /fx/ 路由 ==")
st, hdr, body = fetch("/fx/fxlib.json")
check("/fx/fxlib.json 200", st == 200, st)
check("fxlib.json Content-Type 是 JSON", "application/json" in hdr.get("Content-Type", ""),
      hdr.get("Content-Type"))
check("fxlib.json 内容与磁盘一致", body == open(os.path.join(FXDIR, "fxlib.json"), "rb").read())
check("fxlib.json 带长缓存", "max-age" in hdr.get("Cache-Control", ""), hdr.get("Cache-Control"))

st, hdr, body = fetch("/fx/x32/HALL.webp")
check("/fx/x32/HALL.webp 200", st == 200, st)
check("webp Content-Type 正确", hdr.get("Content-Type") == "image/webp", hdr.get("Content-Type"))
check("webp 字节与磁盘一致", body == open(os.path.join(FXDIR, "x32", "HALL.webp"), "rb").read())

st, hdr, _ = fetch("/fx/wing/premium/hall-reverb.webp")
check("WING 图可访问（子目录）", st == 200, st)

for bad_path in ("/fx/../../x32_simulator.py", "/fx/nope.webp", "/fx/"):
    st, _h, _b = fetch(bad_path)
    check("越界/不存在路径 404: %s" % bad_path, st == 404, st)

# ---------------------------------------------------------------- 4. 网页端接入
print("\n== 4. 网页端已接入 ==")
page = webassets.WEB_PAGE
check("网页含图鉴入口按钮", "fxGalBtn" in page)
check("网页含图鉴弹窗骨架", "fxGalModal" in page and "fxGalBody" in page)
check("网页按 /fx/fxlib.json 拉图鉴", "/fx/fxlib.json" in page)
check("网页卡片有面板图容器", "fxshot" in page)

# ---------------------------------------------------------------- 5. 真台名一致
# 用户是照着台子/X32-Edit 上的字找效果器的。界面上必须出现真台英文名
# （"Stereo Chorus"），只给中文（"立体声合唱"）等于让他自己翻译 —— 找不着。
print("\n== 5. 算法名 = 真台名 ==")
from x32sim.fx import fx_lib_meta                         # noqa: E402

meta = fx_lib_meta()["algos"]
check("API 下发条目数 = 61", len(meta) == 61, len(meta))
noen = [k for k, v in meta.items() if not (v.get("en") or "").strip()]
check("每个算法都带真台名 en", not noen, noen)
bad = [k for k, v in meta.items() if v.get("en") != FX_LABEL_EN.get(k)]
check("en 与 FX_LABEL_EN 逐条一致", not bad, bad)
check("en 无重名", len({v["en"] for v in meta.values()}) == len(meta))
check("中文 label 仍保留（作为辅助）",
      all((v.get("label") or "").strip() for v in meta.values()))

# 导出物（Android assets / iOS generated swift）必须带同一份 en
AND_JSON = os.path.join(os.path.dirname(os.path.abspath(".")),
                        "..", "X32RemoteAndroid", "app", "src", "main",
                        "assets", "fx_calibration.json")
if os.path.exists(AND_JSON):
    aj = json.load(open(AND_JSON, encoding="utf-8"))["algos"]
    check("Android 标定表带 en 且一致",
          all(aj.get(k, {}).get("en") == FX_LABEL_EN.get(k) for k in meta),
          [k for k in meta if aj.get(k, {}).get("en") != FX_LABEL_EN.get(k)][:5])
IOS_SWIFT = os.path.join(os.path.dirname(os.path.abspath(".")),
                         "X32Remote-ios", "Sources", "X32RemoteCore", "Model",
                         "FxCalibration+Generated.swift")
if os.path.exists(IOS_SWIFT):
    src = open(IOS_SWIFT, encoding="utf-8").read()
    check("iOS 生成物带 en 字段", '"en":' in src)
    check("iOS 生成物含 Stereo Chorus", "Stereo Chorus" in src)

# 网页：下拉用 en 拼显示名，不是只用中文 label
check("网页下拉用真台名拼显示", "lib[k].en ||" in page and "（' + zh + '）" in page)

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 60)
print("%d/%d 通过" % (sum(results), len(results)))
for i, name, detail in failed:
    print("失败项 #%d: %s%s" % (i, name, (" | " + str(detail)) if detail else ""))
sys.exit(0 if all(results) else 1)
