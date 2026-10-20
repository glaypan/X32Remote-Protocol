# -*- coding: utf-8 -*-
"""x32sim/persist — 自定义配置持久化 (原子写 / 坏文件备份 / 消毒)"""

import json
import os
import tempfile
import threading
import time

from .bridge import BRIDGE_TARGET
from .cards import SHOW_CARDS
from .consts import FX_COUNT, PRESET_DEFAULT_SCOPE
from .fx import FX_SYNC, FX_SYNC_LOCK, fx_algos
from .logbus import log
from .presets import RECALL_STOP, SNAPSHOTS, SNAP_ID, clean_preset_scope, recall_snapshot
from .profiles import CONSOLE_PROFILES, CTRL_PROFILE_ID
from .quickkeys import KEY_CFG, QUICK_KEYS, QUICK_KEY_KINDS, RAMP_STOP
from .runtime import PORT_CFG, valid_port
from .state import STATE, get_value, set_value, target_exists
from .timeline import _show_stop_current
from .util import _clamp01, _finite, curve_ratio

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

# ---- 自定义配置持久化: 控制键/演出卡片/步长存 JSON, 重启不丢 ----
# ⚠️ 可变容器: main() 可用 --config 改写。默认值按**仓库根目录**推:
#    包形态(开发)下 __file__ 在 x32sim/ 里, 配置放上一级; 合成单文件形态下
#    __file__ 就是 x32_simulator.py 本身, 其目录即根目录 —— 两种形态一致。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE) if os.path.basename(_HERE) == "x32sim" else _HERE
CONFIG_PATH = [os.path.join(_ROOT, "user_config.json")]
ID_SEQ = [1000]
CONFIG_SAVE_LOCK = threading.Lock()
CONFIG_BROKEN_BACKUP = [None]   # 上次加载时坏文件的备份路径 (None=上次加载正常)


def next_id(prefix):
    ID_SEQ[0] += 1
    return "%s%d" % (prefix, ID_SEQ[0])


def save_user_config():
    """把自定义配置**原子**写盘。返回 True=落盘成功, False=失败 (已记日志)。

    ⚠️ 曾经这里有三种"假成功", 全部堵上:
      1. 直接 open(目标, "w") 开写 → 写一半断电/磁盘满就是半个 JSON,
         下次启动 json.load 报错 → 预设/卡片静默全丢。
         现在: 同目录临时文件 + flush + fsync + os.replace 原子替换,
         任何时刻磁盘上都是一个完整文件。
      2. 失败只 log 一行, 接口照样返 ok:true → 用户以为存住了, 重启就没。
         现在返回 False, HTTP 层据此返 500 并说明"改动只在内存中"。
      3. 并发保存互相踩 (PermissionError 静默失败) → 全局保存锁 +
         每次独立临时文件名, 物理上不冲突。
    """
    with CONFIG_SAVE_LOCK:
        try:
            # list(...) 浅拷贝: 卡片/预设可能在别的线程里 append, 直接序列化原列表
            # 会抛 "dict changed size during iteration" (曾经偶发 PermissionError 的亲戚)
            text = json.dumps({"quickKeys": list(QUICK_KEYS), "cards": list(SHOW_CARDS),
                               "keyCfg": KEY_CFG, "idSeq": ID_SEQ[0],
                               "bridgeTarget": BRIDGE_TARGET[0],
                               "fxSync": dict(FX_SYNC),
                               "console": CTRL_PROFILE_ID[0],
                               "ports": {"udp": PORT_CFG["udp"], "web": PORT_CFG["web"]},
                               "fxSlots": {str(s): {"type": STATE.fx[s]["type"],
                                                    "name": STATE.fx[s]["name"],
                                                    "mix": STATE.fx[s]["mix"],
                                                    "par": {str(p): v for p, v in STATE.fx[s]["par"].items()}}
                                           for s in range(1, FX_COUNT + 1)},
                               "presets": list(SNAPSHOTS),
                               "names": {"%s|%d" % k: v for k, v in STATE.names.items()}},
                              ensure_ascii=False, indent=1, allow_nan=False)
        except Exception as e:
            # allow_nan=False: NaN 混进状态时在这里炸, 而不是写出一个 Python 专有
            # 字面量、别的工具还读不了的"假配置"
            log("配置序列化失败 (疑似 NaN 混进台面状态): %r" % e)
            return False
        folder = os.path.dirname(CONFIG_PATH[0]) or "."
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(prefix=".user_config-", suffix=".tmp", dir=folder)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            # Windows 上 replace 的目标可能被读进程占着 → 短暂重试, 别静默失败
            for attempt in range(4):
                try:
                    os.replace(tmp, CONFIG_PATH[0])
                    tmp = None
                    break
                except PermissionError:
                    if attempt == 3:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if CONFIG_BROKEN_BACKUP[0]:
                log("配置已用当前台面覆盖旧文件 (损坏的旧配置备份在 %s)"
                    % CONFIG_BROKEN_BACKUP[0])
                CONFIG_BROKEN_BACKUP[0] = None
            return True
        except Exception as e:
            log("配置保存失败: 无法写入 %s (%r) — 本次改动只在内存中, 重启后会丢失"
                % (CONFIG_PATH[0], e))
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return False


def load_user_config():
    try:
        with open(CONFIG_PATH[0], "r", encoding="utf-8") as f:
            d = json.load(f)
        # 自定义端口（全 None 表示没设过, 走内置默认）
        p = d.get("ports")
        if isinstance(p, dict):
            PORT_CFG["udp"] = valid_port(p.get("udp"))
            PORT_CFG["web"] = valid_port(p.get("web"), allow_zero=True)
        if isinstance(d.get("quickKeys"), list):
            # ⚠️ 控制键也要消毒：配置文件是用户可编辑的 JSON，一个 idx=99 的条目
            #    会让 /api/key 在 apply_key 里 KeyError（早年就是这么崩的）。
            #    kind 不在白名单、idx 不是数字 → 整条丢弃。
            clean_keys = []
            for k in d["quickKeys"]:
                if not isinstance(k, dict) or k.get("kind") not in QUICK_KEY_KINDS:
                    continue
                try:
                    k_idx = int(k.get("idx", 1) or 1)
                    k_sign = int(k.get("sign", 1) or 1)
                except (TypeError, ValueError):
                    continue
                if not target_exists(k["kind"], k_idx):
                    continue
                clean_keys.append({
                    "id": str(k.get("id") or next_id("k")),
                    "name": str(k.get("name", "")).strip() or "控制键",
                    "icon": str(k.get("icon", "⚡"))[:4] or "⚡",
                    "kind": k["kind"], "idx": k_idx,
                    "sign": -1 if k_sign < 0 else 1,
                })
            QUICK_KEYS[:] = clean_keys
            if len(clean_keys) != len(d["quickKeys"]):
                log("配置里的控制键有 %d 条不合法，已丢弃"
                    % (len(d["quickKeys"]) - len(clean_keys)))
        if isinstance(d.get("cards"), list):
            SHOW_CARDS[:] = d["cards"]
        # 预设（台面快照）: 以前只存在内存里重启即丢，现在跟配置一起落盘。
        # 配置文件可手改 → 必须逐条消毒，脏数据最多被丢弃，不能把回归变成乱写。
        if isinstance(d.get("presets"), list):
            loaded = []
            skipped_bad_scope = 0
            for s in d["presets"][:8]:
                if not isinstance(s, dict) or not isinstance(s.get("data"), dict):
                    continue
                # scope: 缺字段 = 旧格式全量预设 (合法); 有字段但清不出合法范围
                # = 脏数据 → **跳过这条预设**并记日志。曾经的写法是静默回落全量,
                # 一条坏 scope 就能让回归误改全台; 原文件我们不动, 用户修完能恢复。
                raw_scope = s.get("scope")
                if raw_scope is None:
                    sc = {"groups": list(PRESET_DEFAULT_SCOPE["groups"]),
                          "params": list(PRESET_DEFAULT_SCOPE["params"]), "indices": {}}
                else:
                    sc, ok, why = clean_preset_scope(raw_scope)
                    if not ok:
                        skipped_bad_scope += 1
                        log("预设「%s」的范围非法 (%s)，已跳过；原配置未改动，修复后可恢复"
                            % (s.get("name") or "?", why))
                        continue
                try:
                    sid = int(s.get("id") or 0)
                except (TypeError, ValueError):
                    sid = 0
                try:
                    scene_no = int(s.get("scene") or 1)
                except (TypeError, ValueError):
                    scene_no = 1
                try:
                    cnt = int(s.get("count") or 0)
                except (TypeError, ValueError):
                    cnt = 0
                loaded.append({"id": sid or (len(loaded) + 1),
                               "name": str(s.get("name") or "预设")[:20],
                               "time": str(s.get("time") or ""),
                               "scene": min(max(scene_no, 0), 99),
                               "scope": sc,
                               "data": s["data"], "count": max(0, cnt)})
            SNAPSHOTS[:] = loaded
            if SNAPSHOTS:
                SNAP_ID[0] = max([x["id"] for x in SNAPSHOTS] + [SNAP_ID[0]])
                log("已载入 %d 条预设%s" % (
                    len(SNAPSHOTS),
                    ("，%d 条因范围非法被跳过" % skipped_bad_scope) if skipped_bad_scope else ""))
        # 迁移旧结构: 内置/历史卡片用独立 fades 字段, 统一转成 fade_from 动作, 保证简介与动作一致
        migrated = False
        for c in SHOW_CARDS:
            if isinstance(c, dict) and c.get("fades"):
                for f in c["fades"]:
                    try:
                        c.setdefault("actions", []).append({
                            "kind": "fade_from", "ch": int(f.get("ch", 1)),
                            "frm": float(f.get("from", 0.0)), "to": float(f.get("to", 0.9)),
                            "duration": float(f.get("duration", 5.0)),
                            "curve": f.get("curve", "linear")})
                    except (TypeError, ValueError):
                        pass
                c["fades"] = []
                migrated = True
        if migrated:
            # ⚠️ 有预设因范围非法被跳过时**不做迁移保存**: 保存写的是内存状态,
            #     会把用户手改坏、还没来得及修的那条预设从文件里抹掉。
            #     旧结构留在内存里不影响运行, 用户修好配置后的下次启动再迁移。
            if skipped_bad_scope:
                log("检测到旧版 fades 结构; 因有预设被跳过, 本次不自动保存, "
                    "修复配置后的下次启动将自动迁移")
            else:
                save_user_config()
                log("已迁移旧版 fades 结构为 fade_from 动作")
        if isinstance(d.get("keyCfg"), dict):
            KEY_CFG.update(d["keyCfg"])
        if isinstance(d.get("names"), dict):
            for k, v in d["names"].items():
                try:
                    g, i = k.split("|")
                    STATE.names[(g, int(i))] = str(v)
                except (ValueError, TypeError):
                    pass
        if isinstance(d.get("idSeq"), int):
            ID_SEQ[0] = max(ID_SEQ[0], d["idSeq"])
        if isinstance(d.get("fxSync"), dict):
            with FX_SYNC_LOCK:
                for k in ("mode", "group", "followTap", "bpm", "note", "dotted"):
                    if k in d["fxSync"]:
                        FX_SYNC[k] = d["fxSync"][k]
        # ⚠️ 控台要先恢复: 下面恢复 fxSlots 时要用**该控台的效果库**做校验,
        #    否则会把别的台的算法当成合法的装进来。
        if isinstance(d.get("console"), str) and d["console"] in CONSOLE_PROFILES:
            CTRL_PROFILE_ID[0] = d["console"]
        if isinstance(d.get("fxSlots"), dict):
            with STATE.lock:
                for sk, sv in d["fxSlots"].items():
                    try:
                        s = int(sk)
                    except (TypeError, ValueError):
                        continue
                    if not (1 <= s <= FX_COUNT) or not isinstance(sv, dict):
                        continue
                    t = str(sv.get("type", "")).upper()
                    if t and t not in fx_algos():
                        continue
                    STATE.fx[s]["type"] = t
                    STATE.fx[s]["name"] = str(sv.get("name", ""))[:12]
                    try:
                        STATE.fx[s]["mix"] = _clamp01(sv.get("mix", 0.25), 0.25)
                    except (TypeError, ValueError):
                        pass
                    par = sv.get("par")
                    if isinstance(par, dict):
                        STATE.fx[s]["par"] = {}
                        for pk, pv in par.items():
                            try:
                                # ⚠️ _finite: 旧版配置可能已被 NaN 污染（Python 的
                                #    json.load 默认接受字面量 NaN），不清洗就带着毒进 STATE
                                STATE.fx[s]["par"][int(pk)] = _finite(pv, 0.0)
                            except (TypeError, ValueError):
                                pass
        if isinstance(d.get("bridgeTarget"), str) and d["bridgeTarget"].strip():
            BRIDGE_TARGET[0] = d["bridgeTarget"].strip()
        log("已加载自定义配置: %d 个控制键, %d 张演出卡片%s"
            % (len(QUICK_KEYS), len(SHOW_CARDS),
               (", 记住的真台地址 %s" % BRIDGE_TARGET[0]) if BRIDGE_TARGET[0] else ""))
    except FileNotFoundError:
        return True          # 没有配置文件 = 全新装机, 正常
    except Exception as e:
        # 坏文件 (写一半被杀 / 手改坏): 曾经只是 log 一行, 然后内存里的默认值
        # 会在下次保存时把用户数据覆盖掉 → 预设/卡片"静默全丢"。
        # 现在先把坏文件备份出来 (数据可救), 再明确告知, 绝不明不明白覆盖。
        backup = None
        try:
            backup = "%s.corrupt-%s" % (CONFIG_PATH[0], time.strftime("%Y%m%d-%H%M%S"))
            os.replace(CONFIG_PATH[0], backup)
        except OSError:
            backup = None
        CONFIG_BROKEN_BACKUP[0] = backup
        if backup:
            log("配置加载失败: %r — 文件已损坏, 备份到 %s; 本次用默认配置启动, "
                "修复后可把备份内容并回 user_config.json" % (e, backup))
        else:
            log("配置加载失败: %r — 文件已损坏且无法备份, 已保留原文件不动; "
                "本次用默认配置启动" % e)
        return False


def clear_console():
    """空台子:全部推子 1 秒同步拉底 + 全部取消静音,得到干净的空台。"""
    RAMP_STOP.set()
    RECALL_STOP.set()
    _show_stop_current()

    def worker():
        RAMP_STOP.clear()
        kinds = ([("ch_fader", i) for i in range(1, 33)] +
                 [("bus_fader", i) for i in range(1, 17)] +
                 [("dca_fader", i) for i in range(1, 9)] +
                 [(g + "_fader", i) for g, n in (("auxin", 6), ("usb", 2), ("fxret", 8), ("mtx", 6))
                  for i in range(1, n + 1)] +
                 [("main_fader", 0)])
        starts = [(k, i, get_value(k, i)) for k, i in kinds]
        steps = 20
        try:
            for s in range(1, steps + 1):
                if RAMP_STOP.is_set():
                    return
                time.sleep(0.05)
                ratio = curve_ratio(s / steps, "ease_out")
                for (k, i, a) in starts:
                    set_value(k, i, a * (1.0 - ratio))
        except Exception:
            pass
        for i in range(1, 33):
            set_value("ch_on", i, 1)
        for i in range(1, 17):
            set_value("bus_on", i, 1)
        for i in range(1, 9):
            set_value("dca_on", i, 1)
        set_value("main_on", 0, 1)
        log("CONSOLE 空台子完成: 全部推子拉底,全部取消静音")
    threading.Thread(target=worker, daemon=True).start()


def recall_last():
    """回传数据:把最近保存的预设平滑写回台面(读取→空台→回传 流程的最后一步)。"""
    if not SNAPSHOTS:
        return None
    return recall_snapshot(SNAPSHOTS[0]["id"], 3.0)
