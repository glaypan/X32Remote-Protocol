# -*- coding: utf-8 -*-
"""x32sim/version — 版本与设备身份常量 (xinfo 回包用)"""

# 模拟器自身版本。与共享协议规范的 spec-v1.12 对齐
# (v1.12 = 效果器图鉴: 真机界面截图随包 + /fx/ 资源路由)。
APP_VERSION = "1.8.0"

# /xinfo 回包的身份字段 (真台回 [ip, name, model, firmware]; 这里 name/model/firmware
# 是模拟器自称, 原先是 handle() 里的字面量, 收敛到这里统一管理)。
SIM_NAME = "X32Remote-Sim"
SIM_MODEL = "X32"
SIM_FIRMWARE = "3.06-sim"
