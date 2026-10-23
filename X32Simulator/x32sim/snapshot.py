# -*- coding: utf-8 -*-
"""x32sim/snapshot — 状态聚合: /api/state 快照 + 条件轮询指纹"""

import hashlib
import json
import math
import time

from .bridge import BRIDGE, BRIDGE_TARGET
from .cards import SHOW_CARDS
from .consts import NAME_COUNTS
from .fx import fx_state
from .logbus import EVENTS, LOG_LOCK, LOG_RING, LOG_SEQ
from .presets import AUTO_SNAPS, RECALL_STATE, SNAPSHOTS, preset_scope_text
from .profiles import console_listing, profile
from .quickkeys import KEY_CFG, QUICK_KEYS
from .runtime import DEF_UDP_PORT, DEF_WEB_PORT, ENV_WEB_PORT, PORT_CFG, UDP_PORT, WEB_PORT, ports_pending_restart
from .rider import rider_snapshot
from .state import METER_SUBSCRIBERS, STATE, compute_levels
from .timeline import SCHEDULES, SHOW_STATE

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

def snapshot_state(since_log=None):
    """聚合全量状态。since_log 给定时, log 字段只返回增量 (网页 250ms 轮询省带宽)。"""
    levels = compute_levels()
    with LOG_LOCK:
        ring = list(LOG_RING)
        seq = LOG_SEQ[0]
    if since_log is None or not isinstance(since_log, int):
        log_tail = ring[-40:]
    elif since_log >= seq:
        log_tail = []
    elif seq - since_log <= len(ring):
        log_tail = ring[len(ring) - (seq - since_log):]
    else:
        log_tail = ring[-40:]       # 落后太多, 直接给全量
    with STATE.lock:
        scene = STATE.scene
        names = {"main": STATE.names.get(("main", 0), "")}
        for g, cnt in NAME_COUNTS.items():
            names[g] = {str(i): STATE.names.get((g, i), "") for i in range(1, cnt + 1)}
    return {
        "ch": {str(i): STATE.ch[i] for i in range(1, 33)},
        "bus": {str(i): STATE.bus[i] for i in range(1, 17)},
        "dca": {str(i): STATE.dca[i] for i in range(1, 9)},
        "auxin": {str(i): STATE.auxin[i] for i in range(1, 7)},
        "usb": {str(i): STATE.usb[i] for i in range(1, 3)},
        "fxret": {str(i): STATE.fxret[i] for i in range(1, 9)},
        "mtx": {str(i): STATE.mtx[i] for i in range(1, 7)},
        "main": STATE.main,
        "meters": levels,
        "scene": scene,
        "fx": fx_state(),
        "console": {"id": profile().id, "label": profile().label,
                    "vendor": profile().vendor, "transport": profile().transport,
                    "ready": profile().ready, "verified": profile().verified,
                    "note": profile().note, "caps": list(profile().caps),
                    "counts": dict(profile().counts),
                    # ⚠️ pending 必须在这里下发：设置页在「控台未标定」时会读
                    #    c.pending.length 列出"还缺哪些地址映射"，少了它就会
                    #    抛 TypeError 把整个 render() 打断（表现为切到未标定控台后
                    #    界面卡在旧状态、不再刷新）。清单项里也有同一份。
                    "pending": profile().pending(),
                    "fxLib": len(profile().fx_algos),
                    "all": console_listing()},
        "ports": {"udp": UDP_PORT[0], "web": WEB_PORT[0],        # 当前**实际生效**的端口
                  "savedUdp": PORT_CFG["udp"], "savedWeb": PORT_CFG["web"],
                  "defUdp": DEF_UDP_PORT, "defWeb": DEF_WEB_PORT,
                  "envLocked": ENV_WEB_PORT[0] is not None,   # 线上部署: 网页端口由平台指定
                  "pending": ports_pending_restart()},
        "cards": SHOW_CARDS,
        "show": dict(SHOW_STATE),
        "events": list(EVENTS)[-6:],
        "snapshots": [{"id": s["id"], "name": s["name"], "time": s["time"],
                       "count": s.get("count", 0),
                       "scopeText": preset_scope_text(s.get("scope") or {})}
                      for s in SNAPSHOTS],
        "recall": dict(RECALL_STATE),
        # 自动增益/推子: 只带启用中的通道（通常 1-4 路），不把 32 路塞进状态包
        "rider": rider_snapshot(),
        "quickKeys": QUICK_KEYS,
        "keyCfg": dict(KEY_CFG),
        "names": names,
        "canUndo": len(AUTO_SNAPS) > 0,
        "subscribers": len(METER_SUBSCRIBERS),
        "log": log_tail,
        "logseq": seq,
        "bridge": {"on": BRIDGE["addr"] is not None,
                   "addr": ("%s:%d" % (BRIDGE["ip"], BRIDGE["port"])) if BRIDGE["addr"] else "",
                   "target": BRIDGE_TARGET[0],
                   "synced": BRIDGE["synced"],
                   "live": bool(BRIDGE["addr"] and time.time() - BRIDGE.get("last_recv", 0) < 8.0)},
        "schedules": [{"sid": s["sid"], "card": s["card_id"], "name": s["card_name"],
                       "runAt": s["run_at"], "mode": s["mode"]}
                      for s in list(SCHEDULES)],
    }


def _sanitize_snapshot(obj):
    """快照输出前的兜底消毒: 把任何残留的 NaN/inf 换成 None。

    入口层已经用 _finite 挡住了 NaN，这里是**第二道闸** —— 万一将来有新的
    写入路径漏了 _finite，宁可下发 null（前端显示 0/空），也不能让
    json.dumps 产出字面量 NaN 把前端 JSON.parse 崩掉（整个界面永久卡死）。
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_snapshot(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_snapshot(v) for v in obj]
    return obj


def _state_version(head):
    """状态内容指纹 (调用方已剔除 meters/log/logseq/ver)。

    用途: /api/state?v=N 条件轮询。网页 250ms 全量轮询在空台也要 15-21KB,
    实测 271-300MB/h (4G 场景不可接受)。命中"没变"时服务端只回 ~30 字节。
    哈希失败 (含不可序列化对象) 时返回 None —— 调用方必须恒返全量,
    **绝不能**把 None 当合法版本号 (否则下次 v=None 也匹配, 界面被冻结)。
    """
    try:
        raw = json.dumps(head, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def snapshot_state_safe(since_log=None, ver=None):
    """snapshot_state + NaN 消毒（HTTP 层只走这个入口）。

    ver 给定时进入**条件快照**模式 (网页专用, 旧客户端不传):
      - 剔除 meters (改由 /api/meters 单独取, 它每 tick 都变, 不能进指纹);
      - 按内容指纹比对, 未变更 → {"unchanged":true, log, logseq};
      - 有变更 → 全量 + ver 字段 (下次轮询带上)。
    """
    snap = _sanitize_snapshot(snapshot_state(since_log))
    if ver is None:
        return snap
    snap.pop("meters", None)
    head = {k: v for k, v in snap.items() if k not in ("log", "logseq", "ver")}
    cur = _state_version(head)
    if cur is not None and cur == ver:
        return {"unchanged": True, "log": snap.get("log") or [],
                "logseq": snap.get("logseq", 0)}
    if cur is None:
        return snap                  # 放弃条件模式, 恒返全量 (客户端会看到无 ver)
    snap["ver"] = cur
    return snap
