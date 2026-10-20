# -*- coding: utf-8 -*-
"""x32sim/version — 版本与设备身份常量 (xinfo 回包用)"""

# 模拟器自身版本。与共享协议规范的 spec-v1.6 对齐 (台面预设 + 场景全局/局部)。
APP_VERSION = "1.6.0"

# /xinfo 回包的身份字段 (真台回 [ip, name, model, firmware]; 这里 name/model/firmware
# 是模拟器自称, 原先是 handle() 里的字面量, 收敛到这里统一管理)。
SIM_NAME = "X32Remote-Sim"
SIM_MODEL = "X32"
SIM_FIRMWARE = "3.06-sim"
