#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 x32sim/params.py 的登记表导出为三端共用的数据文件:

  outputs/param_specs.json                         规范 JSON (人看/文档引用)
  <ios>/Sources/X32RemoteCore/Model/ParamSpecs+Generated.swift
  <android>/app/src/main/assets/param_specs.json

为什么必须生成而不是手写: 原生 App 直连真台走 **OSC**, 真台不回任何元信息
(中文名/量程/枚举/页签全靠 App 自己带)。手写一份 Swift 表 + 一份 Kotlin 表,
和 Python 那份必然三路漂移 —— 网页已经有 /api/paramspecs 下发, 原生端只能吃
同一份导出物, 否则"网页能调、App 调到别的值"这种 Bug 迟早出现。

导出内容:
  groups  各通道类型的**能力** (路数/是否有前置/门/动态/插入/几段 EQ/声像/发送目标数)
  specs   每组的行参数 (field/label/ty/lo/hi/step/dec/unit/log/枚举/默认/页签)
  sends   五组发送 (源上限/目标数/是否带取点/打包基址)

用法:
  python tools/export_param_specs.py            # 生成全部
  python tools/export_param_specs.py --check    # 只校验是否过期 (CI 防漂移)
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
sys.path.insert(0, BASE)

from x32sim.params import (GADDR, GROUP_PARAM_SPECS, GROUP_SIZE,  # noqa: E402
                          SEND_BASE, SEND_BUSES, SEND_GROUPS, SEND_SRC_MAX,
                          ui_specs)

IOS_OUT = os.path.join(BASE, "..", "X32Remote-ios", "Sources",
                       "X32RemoteCore", "Model", "ParamSpecs+Generated.swift")
# ⚠️ 真正的 Android 仓在**工作区之外**: E:\AI\workbuddy\X32RemoteAndroid
AND_ROOT = os.environ.get("X32_ANDROID_DIR",
                          os.path.join(BASE, "..", "..", "X32RemoteAndroid"))
AND_OUT = os.path.join(AND_ROOT, "app", "src", "main", "assets", "param_specs.json")
JSON_OUT = os.path.join(BASE, "outputs", "param_specs.json")

# 面板上要出现的通道类型 (顺序 = 网页/App 页签顺序)
PANEL_GROUPS = ("ch", "bus", "auxin", "fxret", "mtx", "main")
GROUP_LABEL = {"ch": "输入通道", "bus": "Bus", "auxin": "Aux In",
               "fxret": "FX 返回", "mtx": "Matrix", "main": "Main"}
# 面板标题里显示的短名 (列表里的 "Ch 01" 之类)
GROUP_SHORT = {"ch": "Ch", "bus": "Bus", "auxin": "Aux", "fxret": "FX",
               "mtx": "Mtx", "main": "Main"}


def capabilities(g):
    """一组的硬件能力 —— 由登记表**反推**, 不再手写一张能力表。

    有某个参数就说明这组支持该功能 (真台没有的能力, 登记表里也不会有那些行)。
    """
    fields = {r["f"] for r in ui_specs(g)}
    eq = sum(1 for f in fields if f.startswith("eq") and f.endswith("_f"))
    return {
        "label": GROUP_LABEL[g],
        "short": GROUP_SHORT[g],
        "size": GROUP_SIZE[g],
        "pre": 1 if "trim" in fields else 0,
        "gate": 1 if "gate_on" in fields else 0,
        "dyn": 1 if "dyn_on" in fields else 0,
        "ins": 1 if "ins_on" in fields else 0,
        "eq": eq,
        "pan": 1 if "pan" in fields else 0,
        "send": SEND_BUSES.get(g, 0),
        "tap": 1 if (g not in ("bus", "main") and SEND_BUSES.get(g)) else 0,
        "sendBase": SEND_BASE.get(g, 0),
        "sendMax": SEND_SRC_MAX.get(g, 0),
        # OSC 前缀（**真台拼写**：fxret → /fxrtn；main 带 /st，没有路号）。
        # 原生端的发送地址要自己拼 /<前缀>/<源>/mix/<目标>/level，前缀不能猜。
        "osc": GADDR.get(g, "/" + g),
    }


def build_payload():
    groups = {g: capabilities(g) for g in PANEL_GROUPS}
    # 行参数: ui_specs 与 GROUP_PARAM_SPECS 同序 (ui_specs 就是从它生成的),
    # 逐项 zip 补上 OSC 地址模板与语义 kind。
    # ⚠️ 地址模板必须**原样导出**, 不许 App 自己拼: 里面有一堆不能猜的坑 ——
    #    hpf_slope → /hpf/hpslope (字段名与地址段不同名)、
    #    fxret → /fxrtn (组名与地址前缀不同)、main 无路号 (/main/st/mix/pan)。
    specs = {}
    for g in PANEL_GROUPS:
        rows = []
        for sp, r in zip(GROUP_PARAM_SPECS[g], ui_specs(g)):
            row = dict(r)
            row["k"] = sp.kind
            row["a"] = sp.osc
            # ew: 枚举的线上表示 (value=字面值 / index=下标)。
            # dyn_ratio 本地存比值 4.0, 线上必须发下标 3 —— 漏了这个字段,
            # App 写 4.0 会被真台当成第 4 档 (5:1), 界面与台面悄悄错位。
            row["ew"] = sp.enum_wire
            rows.append(row)
        specs[g] = rows
    return {
        "v": 1,
        "note": "由 X32Simulator/tools/export_param_specs.py 生成, 禁止手改",
        "groups": groups,
        "specs": specs,
    }


def dump(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


SWIFT_HEAD = """// 由 X32Simulator/tools/export_param_specs.py 生成, **禁止手改**。
// 单一事实来源: x32sim/params.py (登记表)。改参数 → 改那边 → 重跑导出。
//
// 为什么内嵌而不走网络: App 直连真台走的是 **OSC**, 真台没有任何元信息接口,
// 参数中文名/量程/枚举/页签只能随 App 带。
import Foundation

public enum ParamSpecsGenerated {
    /// 与 outputs/param_specs.json 逐字节相同的一份数据
    public static let json = #\"\"\"
%s
\"\"\"#

    public static let payload: ParamSpecPayload = {
        guard let data = json.data(using: .utf8),
              let p = try? JSONDecoder().decode(ParamSpecPayload.self, from: data) else {
            assertionFailure("参数登记表内嵌 JSON 解析失败")
            return ParamSpecPayload(v: 1, note: "", groups: [:], specs: [:])
        }
        return p
    }()
}
"""


def write_swift(payload):
    body = dump(payload)
    os.makedirs(os.path.dirname(IOS_OUT), exist_ok=True)
    with open(IOS_OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(SWIFT_HEAD % body)
    return IOS_OUT


def write_json_file(payload, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(dump(payload))
        f.write("\n")
    return path


def main():
    check = "--check" in sys.argv
    payload = build_payload()
    body = dump(payload)

    if check:
        stale = []
        for path, expect in ((JSON_OUT, body),
                             (AND_OUT, body + "\n")):
            # Android 仓在工作区之外，别的机器/CI 上可能整个不存在 ——
            # 那是"没有这个消费者"，不是"导出物过期"，跳过而不是报错。
            if path == AND_OUT and not os.path.isdir(AND_ROOT):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    if f.read().strip() != expect.strip():
                        stale.append(path)
            except OSError:
                stale.append(path + " (缺失)")
        try:
            with open(IOS_OUT, encoding="utf-8") as f:
                if body not in f.read():
                    stale.append(IOS_OUT)
        except OSError:
            stale.append(IOS_OUT + " (缺失)")
        if stale:
            print("参数表导出物已过期, 请重跑 python tools/export_param_specs.py:")
            for s in stale:
                print("  -", s)
            return 1
        print("参数表导出物与登记表一致 (%d 字节)" % len(body))
        return 0

    print("写入:", write_json_file(payload, JSON_OUT))
    print("写入:", write_swift(payload))
    print("写入:", write_json_file(payload, AND_OUT))
    print("参数表: %d 组 / %d 项, %d 字节"
          % (len(payload["specs"]),
             sum(len(v) for v in payload["specs"].values()), len(body)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
