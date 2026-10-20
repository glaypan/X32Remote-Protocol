# -*- coding: utf-8 -*-
"""
X32 虚拟调音台模拟器 (Behringer X32/M32 OSC Server Simulator)
=============================================================

用途:在 Mac / Windows 电脑上模拟一台 X32 调音台,让 X32Remote iOS App
      在没有真机的情况下也能完整测试遥控功能。

用法:
    python3 x32_simulator.py                # 默认端口 10023 (仓库根目录的生成物)
    python3 -m x32sim                       # 开发形态 (跑 x32sim/ 包, 行为一致)

然后在 iOS App 设置里把"调音台 IP"填成这台电脑的局域网 IP 即可连接。

支持的 OSC 指令 (UDP :10023):
    /xinfo                          -> 返回 [ip, name, model, firmware]
    /ch/NN/mix/fader  [get/set f]   -> 通道 01-32 推子 (0.0-1.0)
    /ch/NN/mix/on     [get/set i]   -> 通道静音 (1=开 0=静音)
    /dca/NN/fader      [get/set f]  -> DCA 01-08 推子
    /dca/NN/on         [get/set i]  -> DCA 静音
    /main/st/mix/fader [get/set f]  -> Main LR 推子
    /main/st/mix/on    [get/set i]  -> Main LR 静音
    /meters/1          [订阅]       -> 每 150ms 推送 32 通道电平 blob
    /meters/1 i 0      [退订]       -> 停止推送
    支持 "*" 通配符查询 (如 /ch/*/mix/fader,以 Bundle 形式回包)
    支持接收 OSC Bundle 形式的指令

仅使用 Python 标准库,兼容 macOS 10.14.6 (python.org 的 Python 3) 与 Windows。

—— 双形态分发 (B1 模块化) ——
本仓库同时存在两种形态, 行为完全一致:
  开发形态  x32sim/ 包 (本目录) + web/ 三文件, 入口 python -m x32sim
  分发形态  x32_simulator.py (仓库根目录, 自包含单文件, 由 tools/make_single.py
            生成, **禁止手改**) —— 在线版/macOS App/协议仓都用它。
"""
