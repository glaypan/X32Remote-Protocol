# -*- coding: utf-8 -*-
"""x32sim/logbus — 日志环 + 事件队列 (网页轮询弹提示用)"""

import collections
import threading
import time

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

LOG_LOCK = threading.Lock()
LOG_RING = collections.deque(maxlen=200)
LOG_SEQ = [0]        # 日志总条数(单调递增), 网页按 logseq 增量拉取


def log(text):
    with LOG_LOCK:
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), text)
        print(line, flush=True)
        LOG_RING.append(line)
        LOG_SEQ[0] += 1
EVT_SEQ = [0]
EVENTS = collections.deque(maxlen=20)   # 冲突/通知事件, 网页轮询弹提示
