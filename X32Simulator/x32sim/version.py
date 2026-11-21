# -*- coding: utf-8 -*-
"""x32sim/version — 版本与设备身份常量 (xinfo 回包用)"""

# 模拟器自身版本。与共享协议规范的 spec-v1.11 对齐
# (v1.11 = 通道处理参数建模: 六种通道类型 × 均衡/门/压缩/插入/增益/声像/发送)。
APP_VERSION = "1.7.0"

# /xinfo 回包的身份字段 (真台回 [ip, name, model, firmware]; 这里 name/model/firmware
# 是模拟器自称, 原先是 handle() 里的字面量, 收敛到这里统一管理)。
SIM_NAME = "X32Remote-Sim"
SIM_MODEL = "X32"
SIM_FIRMWARE = "3.06-sim"
