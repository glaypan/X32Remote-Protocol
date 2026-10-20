# -*- coding: utf-8 -*-
"""x32sim/httpapi — HTTP API (WebHandler: 全部路由)"""

import json
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler

from .actions import _clean_actions
from .bridge import BRIDGE_TARGET, bridge_start, bridge_stop, fx_pull_from_console
from .cards import SHOW_CARDS
from .consts import FX_COUNT, NAME_COUNTS, NOTE_BEATS, SET_KINDMAP
from .fx import FX_MODE_LABEL, FX_SYNC, FX_SYNC_LOCK, fx_set_param, fx_tap_apply, tap_register, tap_reset
from .logbus import EVENTS, EVT_SEQ, log
from .persist import CONFIG_PATH, ID_SEQ, clear_console, next_id, recall_last, save_user_config
from .presets import AUTO_SNAPS, SNAPSHOTS, _recall_stop_current, preset_scope_text, recall_snapshot, save_snapshot, start_recall
from .profiles import profile, set_profile
from .quickkeys import KEY_CFG, QUICK_KEYS, apply_key
from .runtime import DEF_UDP_PORT, DEF_WEB_PORT, ENV_WEB_PORT, LAN_IP, PORT_CFG, UDP_PORT, WEB_PORT, ports_pending_restart, valid_port
from .scan import scan_consoles
from .snapshot import snapshot_state_safe
from .state import STATE, compute_levels, set_name, set_value, target_exists
from .timeline import SCHEDULES, _show_stop_current, cancel_schedule, run_show, schedule_card
from .util import _finite
from .webassets import PWA_ICON, PWA_MANIFEST, PWA_SERVICE_WORKER, WEB_PAGE

# ------------------------------------------------------------
# 以下代码自 x32_simulator.py 逐字搬运 (B1 模块化), 仅头部导入为本工具生成
# ------------------------------------------------------------

class WebHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass  # 静默 HTTP 访问日志

    def _cfg_saved(self, what):
        """落盘自定义配置; 失败就发 500 说清楚"改动只在内存里", 返回 False。

        曾经的写法是失败只 log 一行、接口照样 ok:true —— 用户以为存住了,
        重启就没, 而且没有任何提示 (持久化"假成功")。
        """
        if save_user_config():
            return True
        self._send(500, "application/json", json.dumps(
            {"ok": False,
             "error": "%s失败: 无法写入配置文件 (%s) — 改动只在内存中, 重启后会丢失。"
                      "请检查所在目录是否可写、磁盘是否已满。" % (what, CONFIG_PATH[0])},
            ensure_ascii=False).encode("utf-8"))
        return False

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        # ⚠️ CSP: 本项目的 HTML 是内联 <script> + 内联 <style>，所以必须开
        #    'unsafe-inline'（换成外部文件就能收得更紧）。加上 frame-ancestors /
        #    object-src / base-uri 堵住"外链页面 iframe 套本页"和"改 base 标签
        #    劫持相对路径"这两类常见利用。它与前端 esc() 是两道独立的闸。
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                         "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                         "connect-src 'self'; frame-ancestors 'self'; "
                         "object-src 'none'; base-uri 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", WEB_PAGE.encode("utf-8"))
        elif path == "/api/state":
            since = None
            ver = None
            try:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query,
                                           keep_blank_values=True)
                if "logseq" in qs:
                    since = int(qs["logseq"][0])
                if "v" in qs:
                    ver = str(qs["v"][0])[:32]     # 空串 = 首轮: 返全量并带上 ver
            except (TypeError, ValueError):
                since = None
            # allow_nan=False 是**测试阶段就该炸**的开关：万一哪条新写入路径漏了
            # _finite，让它在服务端抛出来，而不是等线上前端 JSON.parse 崩、界面卡死
            try:
                body = json.dumps(snapshot_state_safe(since, ver),
                                  allow_nan=False).encode("utf-8")
            except ValueError as e:
                log("STATE 快照含非有限数 (%s)，已降级为空快照" % e)
                body = b"{}"
            self._send(200, "application/json; charset=utf-8", body)
        elif path == "/api/meters":
            # 电平单独取: 它每 tick 都变, 不能进 /api/state 的内容指纹,
            # 否则"没变更"永远命中不了。旧 /api/state 仍带 meters (App 兼容)。
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"meters": compute_levels()},
                                  allow_nan=False).encode("utf-8"))
        elif path == "/api/info":
            self._send(200, "application/json; charset=utf-8", json.dumps({
                "lanIp": LAN_IP[0], "udpPort": UDP_PORT[0], "webPort": WEB_PORT[0],
                "lanUrl": "http://%s:%d" % (LAN_IP[0], WEB_PORT[0]),
            }).encode("utf-8"))
        elif path == "/api/scan":
            # 局域网发现: 广播 /xinfo 找真台 (X32 官方发现指令), 无需手填 IP
            found = scan_consoles()
            log("SCAN 扫描完成: 发现 %d 台设备" % len(found))
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"ok": True, "found": found}).encode("utf-8"))
        elif path == "/api/export":
            # 配置导出: 卡片/控制键/命名/设置 一个 JSON 文件
            cfg = {"version": 1, "exportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                   "quickKeys": QUICK_KEYS, "cards": SHOW_CARDS, "keyCfg": KEY_CFG,
                   "idSeq": ID_SEQ[0],
                   "names": {"%s|%d" % k: v for k, v in STATE.names.items()}}
            body = json.dumps(cfg, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             "attachment; filename=\"x32_config_%s.json\""
                             % time.strftime("%Y%m%d_%H%M"))
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; sandbox")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
        elif path == "/manifest.webmanifest":
            self._send(200, "application/manifest+json; charset=utf-8",
                       PWA_MANIFEST.encode("utf-8"))
        elif path == "/icon.svg":
            self._send(200, "image/svg+xml; charset=utf-8",
                       PWA_ICON.encode("utf-8"))
        elif path == "/sw.js":
            self._send(200, "application/javascript; charset=utf-8",
                       PWA_SERVICE_WORKER.encode("utf-8"))
        else:
            self._send(404, "text/plain; charset=utf-8", "not found".encode())

    # 请求体上限: 没有它, 一个 Content-Length: 10GB 的请求就能让 read() 吃到爆内存。
    # 8 MB 对"一份配置导入"绰绰有余 (卡片/控制键/命名全在里面)。
    MAX_BODY = 8 * 1024 * 1024

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            n = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            n = 0
        if n < 0 or n > self.MAX_BODY:
            self._send(413, "text/plain; charset=utf-8", b"body too large")
            return
        try:
            raw = self.rfile.read(n) if n else b""
            data = json.loads(raw.decode("utf-8")) if raw else {}
            if not isinstance(data, dict):
                raise ValueError("body must be a JSON object")
        except Exception:
            self._send(400, "text/plain; charset=utf-8", b"bad json")
            return

        # ⚠️ 全局兜底: 任何一个端点里漏掉的 TypeError/KeyError/ValueError 都会
        #    顺着 BaseHTTPRequestHandler 冒出去 → 连接被重置（curl 退出码 52），
        #    用户看到的是"点了没反应"，服务端日志里什么都没有。
        #    宁可返回 400 让人看见, 也不要静默断链。
        try:
            self._dispatch(path, data)
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as e:
            log("API %s 参数异常: %r" % (path, e))
            try:
                self._send(400, "application/json",
                           json.dumps({"ok": False,
                                       "error": "bad request: %s" % e}).encode("utf-8"))
            except Exception:
                pass

    def _dispatch(self, path, data):
        if path == "/api/set":
            # kind 是**组名**（ch/bus/dca/main/auxin/usb/fxret/mtx），param 是 fader/on。
            # idx 用 target_exists 预检：越界时给明确的 400，而不是 KeyError 撞全局兜底。
            grp = data.get("kind")
            if not isinstance(grp, str):
                self._send(400, "application/json",
                           b'{"ok":false,"error":"bad kind"}')
                return
            k = SET_KINDMAP.get(grp, {}).get(data.get("param"))
            if not k:
                self._send(400, "application/json", b'{"ok":false,"error":"bad kind"}')
                return
            try:
                idx = int(data.get("idx", 0))
            except (TypeError, ValueError):
                self._send(400, "application/json",
                           b'{"ok":false,"error":"bad idx"}')
                return
            if not target_exists(k, idx):
                self._send(400, "application/json",
                           json.dumps({"ok": False,
                                       "error": "idx %d 超出「%s」的范围" % (
                                           idx, profile().label)}).encode("utf-8"))
                return
            v = data.get("value")
            # fader 类必须是数字：'abc'/null 会被 _clamp01 收敛成 0.0，
            # 于是"发了个垃圾值"表现成"推子归零"—— 比报错难查得多。
            if k.endswith("_fader") or k == "fx_mix":
                try:
                    float(v)
                except (TypeError, ValueError):
                    self._send(400, "application/json", json.dumps(
                        {"ok": False, "error": "推子值必须是数字"}).encode("utf-8"))
                    return
            set_value(k, idx, v)
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/setbatch":
            # 批量写入 (网页拖动 50ms 合并发送, 省请求数)
            sets = data.get("sets")
            if not isinstance(sets, list) or not sets or len(sets) > 128:
                self._send(400, "text/plain; charset=utf-8", b"bad sets")
                return
            n = 0
            for it in sets:
                if not isinstance(it, dict):
                    continue
                k = SET_KINDMAP.get(it.get("kind"), {}).get(it.get("param"))
                if not k:
                    continue
                try:
                    idx = int(it.get("idx", 0))
                except (TypeError, ValueError):
                    continue
                if not target_exists(k, idx):
                    continue      # 越界条目直接跳过，applied 计数会反映出来
                set_value(k, idx, it.get("value"))
                n += 1
            self._send(200, "application/json",
                       json.dumps({"ok": True, "applied": n}).encode())

        elif path == "/api/key":
            # 自定义控制键: 按步长调整目标推子
            r = apply_key(str(data.get("id", "")))
            if not r:
                self._send(404, "text/plain; charset=utf-8", b"no such key")
                return
            self._send(200, "application/json",
                       json.dumps({"ok": True, **r}).encode())

        elif path == "/api/keycfg":
            # 步长 / 渐变速度设置
            if "step_db" in data:
                KEY_CFG["step_db"] = min(max(_finite(data["step_db"], KEY_CFG["step_db"]), 0.5), 12.0)
            if "ramp" in data:
                KEY_CFG["ramp"] = min(max(_finite(data["ramp"], KEY_CFG["ramp"]), 0.0), 5.0)
            log("KEYCFG 步长 %.1f dB, 渐变 %.1f 秒" % (KEY_CFG["step_db"], KEY_CFG["ramp"]))
            if not self._cfg_saved("控制键设置"):
                return
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/fx":
            # 混响槽写入: {slot, field, value}  field ∈ type/on/mix/name/time/predelay/damp
            # 或 {action:"pull"} —— 从桥接的真台主动同步效果槽
            if str(data.get("action", "")) == "pull":
                ok, msg = fx_pull_from_console()
                if not ok:
                    self._send(400, "application/json",
                               json.dumps({"ok": False, "error": msg}).encode("utf-8"))
                    return
                self._send(200, "application/json",
                           json.dumps({"ok": True, "message": msg}).encode("utf-8"))
                return
            v, err = fx_set_param(data.get("slot"), data.get("field"), data.get("value"),
                                  source="manual")
            if err:
                self._send(400, "application/json",
                           json.dumps({"ok": False, "error": err}).encode("utf-8"))
                return
            if not self._cfg_saved("效果槽保存"):
                return
            self._send(200, "application/json",
                       json.dumps({"ok": True, "value": v}).encode("utf-8"))

        elif path == "/api/tap":
            # Tap 定拍: action='tap' 记一次点击 / 'reset' 清空 / 'apply' 按当前时值换算并写入
            act = str(data.get("action", "tap"))
            if act == "reset":
                tap_reset()
                self._send(200, "application/json", b'{"ok":true,"reset":true}')
                return
            if act == "tap":
                bpm, count, locked = tap_register()
                self._send(200, "application/json",
                           json.dumps({"ok": True, "bpm": round(bpm, 1), "count": count,
                                       "locked": locked}).encode("utf-8"))
                return
            if act == "apply":
                secs, written = fx_tap_apply()
                if isinstance(written, str):
                    self._send(400, "application/json",
                               json.dumps({"ok": False, "error": written}).encode("utf-8"))
                    return
                self._send(200, "application/json",
                           json.dumps({"ok": True, "seconds": round(secs, 3),
                                       "written": written}).encode("utf-8"))
                return
            self._send(400, "application/json", b'{"ok":false,"error":"bad action"}')

        elif path == "/api/fxsync":
            # 混响同步设置: {mode, group, followTap, note, dotted}
            with FX_SYNC_LOCK:
                mode = str(data.get("mode", FX_SYNC["mode"]))
                if mode in FX_MODE_LABEL:
                    FX_SYNC["mode"] = mode
                if isinstance(data.get("group"), list):
                    FX_SYNC["group"] = sorted({int(s) for s in data["group"]
                                               if isinstance(s, (int, float))
                                               and 1 <= int(s) <= FX_COUNT})
                if "followTap" in data:
                    FX_SYNC["followTap"] = bool(data["followTap"])
                if "note" in data and str(data["note"]) in NOTE_BEATS:
                    FX_SYNC["note"] = str(data["note"])
                if "dotted" in data:
                    FX_SYNC["dotted"] = bool(data["dotted"])
                snapshot = dict(FX_SYNC)
            if not self._cfg_saved("混响同步设置"):
                return
            log("REVERB 同步设置: %s · 联动组 %s · 跟随Tap %s · 时值 %s%s" % (
                FX_MODE_LABEL.get(snapshot["mode"], snapshot["mode"]),
                "/".join("FX%d" % s for s in snapshot["group"]) or "(空·全部混响槽)",
                "开" if snapshot["followTap"] else "关",
                snapshot["note"], "(附点)" if snapshot["dotted"] else ""))
            self._send(200, "application/json",
                       json.dumps({"ok": True, "sync": snapshot}).encode("utf-8"))

        elif path == "/api/ports":
            # 自由设置网络端口: {udp: 1-65535|null, web: 0-65535|null}
            # 传 null / "" = 清除该项自定义, 回到内置默认。端口是启动时绑定的 → 保存后需重启。
            errs = []
            new_udp, new_web = PORT_CFG["udp"], PORT_CFG["web"]
            if "udp" in data:
                raw = data.get("udp")
                if raw in (None, ""):
                    new_udp = None
                else:
                    new_udp = valid_port(raw)
                    if new_udp is None:
                        errs.append("OSC/UDP 端口必须是 1–65535 的整数")
            if "web" in data:
                raw = data.get("web")
                if raw in (None, ""):
                    new_web = None
                else:
                    new_web = valid_port(raw, allow_zero=True)
                    if new_web is None:
                        errs.append("网页端口必须是 0–65535 的整数（0 = 关闭网页）")
            eff_udp = new_udp if new_udp is not None else DEF_UDP_PORT
            eff_web = new_web if new_web is not None else DEF_WEB_PORT
            if ENV_WEB_PORT[0] is not None and "web" in data and new_web is not None:
                errs.append("本实例的网页端口由平台环境变量指定（线上部署），不能在此修改")
            if eff_web > 0 and eff_web == eff_udp:
                errs.append("OSC/UDP 端口与网页端口不能相同（当前都是 %d）" % eff_udp)
            if errs:
                self._send(400, "application/json",
                           json.dumps({"ok": False, "error": "；".join(errs)}).encode("utf-8"))
                return
            PORT_CFG["udp"], PORT_CFG["web"] = new_udp, new_web
            if not self._cfg_saved("端口设置"):
                return
            pending = ports_pending_restart()
            log("PORT 端口设置: OSC/UDP %s · 网页 %s → %s"
                % (eff_udp, (eff_web if eff_web > 0 else "已关闭"),
                   "重启后生效" if pending else "与当前一致, 无需重启"))
            self._send(200, "application/json", json.dumps({
                "ok": True, "restart": pending,
                "udp": eff_udp, "web": eff_web,
                "active": {"udp": UDP_PORT[0], "web": WEB_PORT[0]},
                "message": ("已保存, 重启模拟器后生效" if pending
                            else "已保存, 与当前生效端口一致"),
            }).encode("utf-8"))

        elif path == "/api/ctrlprofile":
            # 切换控台驱动: {id: "x32" | "m32" | "wing" | "yamaha" | "ah"}
            ok, msg, extra = set_profile(data.get("id"))
            if not ok:
                self._send(400, "application/json",
                           json.dumps({"ok": False, "error": msg}).encode("utf-8"))
                return
            p = profile()
            cleared = extra.get("fxCleared") or []
            fxn = "混响页已同步为「%s」的效果库（%d 种算法）" % (p.label, len(p.fx_algos))
            if cleared:
                fxn += "；FX%s 的算法不在新库中，已置空" % "/".join(str(x) for x in cleared)
            self._send(200, "application/json", json.dumps({
                "ok": True, "id": p.id, "label": p.label, "vendor": p.vendor,
                "ready": p.ready, "verified": p.verified,
                "fxCleared": cleared, "fxLib": extra.get("fxLib", 0),
                "fxSlots": extra.get("fxSlots", 0),
                "libCount": len(p.fx_algos),
                "fxLocked": (not p.ready) or ("fx" not in p.caps),
                "warning": "" if p.ready else
                "该控台驱动尚未标定：所有写入会被拒绝并记日志，卡片不会真正控制到台子",
                "fxNote": fxn,
            }).encode("utf-8"))

        elif path == "/api/console":
            # 数据读取与调用流程: 空台子 / 回传数据
            act = data.get("action")
            if act == "clear":
                clear_console()
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "recall_last":
                snap = recall_last()
                if not snap:
                    self._send(404, "text/plain; charset=utf-8", b"no snapshot")
                    return
                self._send(200, "application/json",
                           json.dumps({"ok": True, "id": snap["id"]}).encode())
            else:
                self._send(400, "text/plain; charset=utf-8", b"bad action")

        elif path == "/api/name":
            # 网页上改通道/输出名称 (与 OSC /ch/01/config/name 等价, 重启不丢)
            g = str(data.get("group", ""))
            try:
                idx = int(data.get("idx", 0))
            except (TypeError, ValueError):
                idx = 0
            cnt = NAME_COUNTS.get(g)
            ok = (g == "main" and idx == 0) or (cnt and 1 <= idx <= cnt)
            if not ok:
                self._send(400, "text/plain; charset=utf-8", b"bad target")
                return
            set_name(g, idx, str(data.get("name", "")))
            if not self._cfg_saved("名称保存"):
                return
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/undo":
            # 一键撤销: 回滚到最近一次卡片执行前的台面 (静音立即恢复, 推子 2 秒滑回)
            if not AUTO_SNAPS:
                self._send(404, "application/json", b'{"ok":false,"error":"no undo point"}')
                return
            snap = AUTO_SNAPS.pop()
            _show_stop_current()      # 正在跑的卡片先停
            start_recall(snap, 2.0)
            log("SHOW 撤销上一次执行: 回滚到 %s" % snap["name"])
            self._send(200, "application/json",
                       json.dumps({"ok": True, "name": snap["name"]}).encode())

        elif path == "/api/show":
            sid = data.get("id")
            if sid is None:
                _show_stop_current()
                log("SHOW 手动停止")
                self._send(200, "application/json", b'{"ok":true,"stopped":true}')
                return
            card = next((c for c in SHOW_CARDS if c["id"] == sid), None)
            if not card:
                self._send(404, "text/plain; charset=utf-8", b"no such card")
                return
            started = run_show(card)     # 内部带"停旧代 + 连点同一张卡忽略"
            if started:
                log("SHOW 启动卡片: %s" % card["name"])
            else:
                log("SHOW 忽略重复点击 — 「%s」已在运行" % card["name"])
            self._send(200, "application/json",
                       json.dumps({"ok": True, "already": not started}).encode("utf-8"))

        elif path == "/api/schedule":
            # 卡片定时触发: mode='delay' delay 秒后; mode='clock' time=HH:MM
            card = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not card:
                self._send(404, "application/json", b'{"ok":false,"error":"no such card"}')
                return
            mode = data.get("mode", "delay")
            try:
                if mode == "clock":
                    hh, mm = [int(x) for x in str(data.get("time", "20:00")).split(":")[:2]]
                    if not (0 <= hh < 24 and 0 <= mm < 60):
                        raise ValueError
                    schedule_card(card, "clock", clock=data.get("time"))
                else:
                    mode = "delay"
                    schedule_card(card, "delay", delay=float(data.get("delay", 10)))
            except (TypeError, ValueError):
                self._send(400, "application/json", b'{"ok":false,"error":"bad time"}')
                return
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/schedcancel":
            try:
                sid = int(data.get("sid", 0))
            except (TypeError, ValueError):
                self._send(400, "application/json",
                           b'{"ok":false,"error":"bad sid"}')
                return
            ok = cancel_schedule(sid)
            self._send(200, "application/json",
                       json.dumps({"ok": ok}).encode())

        elif path == "/api/bridge":
            # 桥接真台开关: addr='IP:PORT' 启动; addr='' 断开
            addr = str(data.get("addr", "")).strip()
            if not addr:
                bridge_stop()
                if BRIDGE_TARGET[0]:       # 用户主动断开 → 不再自动重连
                    BRIDGE_TARGET[0] = ""
                    if not self._cfg_saved("断开记忆"):
                        return
                self._send(200, "application/json", b'{"ok":true,"stopped":true}')
                return
            ok, msg = bridge_start(addr)
            if ok:
                ip, p = addr.rsplit(":", 1)
                BRIDGE_TARGET[0] = "%s:%d" % (ip, int(p))
                if not self._cfg_saved("记住真台地址"):
                    return
                log("BRIDGE 已记住真台地址 %s (下次启动自动重连)" % BRIDGE_TARGET[0])
            self._send(200 if ok else 400, "application/json",
                       json.dumps({"ok": ok, "msg": msg}).encode("utf-8"))

        elif path == "/api/import":
            # 配置导入: 覆盖卡片/控制键/命名/设置 (动作逐条清洗, 防脏数据)
            cfg = data.get("config")
            if not isinstance(cfg, dict):
                self._send(400, "application/json", b'{"ok":false,"error":"bad config"}')
                return
            cards = cfg.get("cards")
            keys = cfg.get("quickKeys")
            if not isinstance(cards, list) or not isinstance(keys, list):
                self._send(400, "application/json",
                           b'{"ok":false,"error":"config missing cards/quickKeys"}')
                return
            clean_cards = []
            import_dropped = 0
            for c in cards:
                if not isinstance(c, dict) or not str(c.get("name", "")).strip():
                    continue
                acts, why = _clean_actions(c.get("actions"))
                if acts is None:
                    acts = []
                import_dropped += len(why)
                clean_cards.append({"id": str(c.get("id") or next_id("c")),
                                    "name": str(c.get("name")).strip(),
                                    "desc": str(c.get("desc", "")).strip() or "导入的演出卡片",
                                    "actions": acts, "fades": [],
                                    "pin": 1 if c.get("pin") else 0,
                                    "next": str(c.get("next") or "")})
            clean_keys = []
            kindmap = {"ch_fader", "bus_fader", "dca_fader", "main_fader",
                       "auxin_fader", "usb_fader", "fxret_fader", "mtx_fader"}
            for k in keys:
                if (isinstance(k, dict) and k.get("kind") in kindmap
                        and str(k.get("name", "")).strip()):
                    try:
                        k_idx = int(k.get("idx", 1) or 1)
                        k_sign = int(k.get("sign", 1) or 1)
                    except (TypeError, ValueError):
                        continue          # 编号/方向不是数字 → 整条跳过, 不猜
                    clean_keys.append({"id": str(k.get("id") or next_id("k")),
                                       "name": str(k["name"]).strip(),
                                       "icon": str(k.get("icon", "⚡"))[:4] or "⚡",
                                       "kind": k["kind"],
                                       "idx": min(max(k_idx,
                                                      1 if k["kind"] != "main_fader" else 0), 32),
                                       "sign": -1 if k_sign < 0 else 1})
            keycfg = cfg.get("keyCfg") if isinstance(cfg.get("keyCfg"), dict) else {}
            # ⚠️ 先把所有可能抛异常的计算做完，**再**动内存。
            #    曾经的顺序是 QUICK_KEYS[:] = ... 在前、float(keycfg[...]) 在后，
            #    于是一个非法步长就会抛在中间：内存已是新配置但 save_user_config()
            #    没执行 → 重启后旧配置复活，内存与磁盘分叉，用户看到"导入成功但丢了"。
            new_step = _finite(keycfg.get("step_db"), KEY_CFG["step_db"])
            new_ramp = _finite(keycfg.get("ramp"), KEY_CFG["ramp"])
            new_step = min(max(new_step, 0.5), 12.0)
            new_ramp = min(max(new_ramp, 0.0), 5.0)
            QUICK_KEYS[:] = clean_keys
            SHOW_CARDS[:] = clean_cards
            KEY_CFG.update({"step_db": new_step, "ramp": new_ramp})
            if isinstance(cfg.get("idSeq"), int):
                ID_SEQ[0] = max(ID_SEQ[0], cfg["idSeq"])
            if isinstance(cfg.get("names"), dict):
                for kk, v in cfg["names"].items():
                    try:
                        g, i = kk.split("|")
                        STATE.names[(g, int(i))] = str(v)
                    except (ValueError, TypeError):
                        pass
            if not self._cfg_saved("配置导入"):
                return
            log("CONFIG 导入完成: %d 张卡片, %d 个控制键%s"
                % (len(clean_cards), len(clean_keys),
                   ("，丢弃 %d 个非法动作" % import_dropped) if import_dropped else ""))
            EVT_SEQ[0] += 1
            EVENTS.append({"id": EVT_SEQ[0], "time": time.strftime("%H:%M:%S"),
                           "text": "📦 配置导入完成 — %d 张卡片 / %d 个控制键%s" % (
                               len(clean_cards), len(clean_keys),
                               ("（丢弃 %d 个非法动作）" % import_dropped) if import_dropped else "")})
            self._send(200, "application/json",
                       json.dumps({"ok": True, "cards": len(clean_cards),
                                   "keys": len(clean_keys),
                                   "droppedActions": import_dropped}).encode())

        elif path == "/api/keyadd":
            # 网页上新增自定义控制键
            kindmap = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader",
                       "main": "main_fader", "auxin": "auxin_fader", "usb": "usb_fader",
                       "fxret": "fxret_fader", "mtx": "mtx_fader"}
            kind = kindmap.get(data.get("kind"))
            if not kind:
                self._send(400, "text/plain; charset=utf-8", "bad kind".encode())
                return
            try:
                idx = int(data.get("idx", 1))
            except (TypeError, ValueError):
                idx = 1
            idx = min(max(idx, 1 if kind != "main_fader" else 0), 32)
            QUICK_KEYS.append({
                "id": next_id("k"),
                "name": str(data.get("name", "")).strip() or "新按键",
                "icon": str(data.get("icon", "")).strip() or "⚡",
                "kind": kind, "idx": idx,
                "sign": -1 if int(data.get("sign", 1)) < 0 else 1,
            })
            if not self._cfg_saved("新增控制键"):
                return
            log("KEY 新增控制键: %s" % QUICK_KEYS[-1]["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/keydel":
            QUICK_KEYS[:] = [k for k in QUICK_KEYS if k["id"] != data.get("id")]
            if not self._cfg_saved("删除控制键"):
                return
            log("KEY 删除控制键: %s" % data.get("id"))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/keyedit":
            # 网页上编辑已有控制键
            target = next((k for k in QUICK_KEYS if k["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"key not found"}')
                return
            kindmap = {"ch": "ch_fader", "bus": "bus_fader", "dca": "dca_fader",
                       "main": "main_fader", "auxin": "auxin_fader", "usb": "usb_fader",
                       "fxret": "fxret_fader", "mtx": "mtx_fader"}
            kind = kindmap.get(data.get("kind"))
            if kind:
                target["kind"] = kind
            try:
                idx = int(data.get("idx", target["idx"]))
            except (TypeError, ValueError):
                idx = target["idx"]
            target["idx"] = min(max(idx, 1 if target["kind"] != "main_fader" else 0), 32)
            name = str(data.get("name", "")).strip()
            if name:
                target["name"] = name
            icon = str(data.get("icon", "")).strip()
            if icon:
                target["icon"] = icon
            try:
                sign = int(data.get("sign", target["sign"]))
            except (TypeError, ValueError):
                sign = target["sign"]
            target["sign"] = -1 if sign < 0 else 1
            if not self._cfg_saved("编辑控制键"):
                return
            log("KEY 编辑控制键: %s" % target["name"])
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/cardedit":
            # 网页上编辑已有演出卡片
            target = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"card not found"}')
                return
            card = data.get("card")
            if not isinstance(card, dict):
                # 少了 card 包裹 → 曾经会静默 {"ok":true} 而一个字段都没改 (假成功);
                # 平铺 payload 也会落到这里, 报错要指向真正的原因。
                self._send(400, "application/json", json.dumps(
                    {"ok": False,
                     "error": '卡片字段要包在 {"id":..., "card": {...}} 里'
                              ' ( received keys: %s )' % ",".join(sorted(data.keys())[:6])
                     }).encode("utf-8"))
                return
            new_name = str(card.get("name", "")).strip()
            if new_name:
                target["name"] = new_name
            if "desc" in card:
                target["desc"] = str(card.get("desc", "")).strip() or "自定义演出卡片"
            # ⚠️ actions 是**显式三元**：字段存在就整体替换（空数组 = 清空），
            #    字段不存在才不动。曾经的 `and card["actions"]` + `if actions:` 两道闸
            #    会造成两种假成功：混入非法动作时静默少存、传空数组时静默不删。
            dropped = []
            if "actions" in card:
                acts, dropped = _clean_actions(card.get("actions"))
                if acts is None:
                    self._send(400, "application/json", json.dumps(
                        {"ok": False, "error": "；".join(dropped)}).encode("utf-8"))
                    return
                target["actions"] = acts
                if dropped:
                    log("SHOW 编辑卡片 %s: 丢弃 %d 个动作 (%s)"
                        % (target["name"], len(dropped), "; ".join(dropped)))
            if "pin" in card:
                target["pin"] = 1 if card.get("pin") else 0
            if "next" in card:      # 执行完自动接续的下一张卡片 (空=不接续)
                nid = str(card.get("next") or "").strip()
                target["next"] = nid if nid and any(c["id"] == nid for c in SHOW_CARDS) else ""
            if not self._cfg_saved("编辑卡片"):
                return
            log("SHOW 编辑卡片: %s" % target["name"])
            self._send(200, "application/json", json.dumps(
                {"ok": True, "dropped": dropped}).encode("utf-8"))

        elif path == "/api/cardpin":
            # 置顶/取消置顶
            target = next((c for c in SHOW_CARDS if c["id"] == data.get("id")), None)
            if not target:
                self._send(404, "application/json", b'{"ok":false,"error":"card not found"}')
                return
            target["pin"] = 0 if target.get("pin") else 1
            if not self._cfg_saved("卡片置顶"):
                return
            log("SHOW %s: %s" % ("置顶卡片" if target.get("pin") else "取消置顶", target["name"]))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/cardadd":
            # 网页上新建演出卡片
            card = data.get("card")
            if not isinstance(card, dict):
                # 常见手误: 把字段平铺在顶层 (少了 card 包裹)。曾经的报错是
                # "actions 不是数组" —— 文不对题, 看着像动作格式错, 实际是包错了层。
                self._send(400, "application/json", json.dumps(
                    {"ok": False,
                     "error": '卡片字段要包在 {"card": {...}} 里 ( received keys: %s )'
                              % ",".join(sorted(data.keys())[:6])}).encode("utf-8"))
                return
            actions, dropped = _clean_actions(card.get("actions"))
            if actions is None:
                self._send(400, "application/json", json.dumps(
                    {"ok": False, "error": "；".join(dropped)}).encode("utf-8"))
                return
            if not actions:
                # 空动作的卡片跑起来什么都不做, 只会让人以为"卡片坏了"
                self._send(400, "application/json", json.dumps(
                    {"ok": False,
                     "error": "卡片至少要有一个有效动作"}).encode("utf-8"))
                return
            SHOW_CARDS.append({
                "id": next_id("c"),
                "name": str(card.get("name", "")).strip() or "🎬 新卡片",
                "desc": str(card.get("desc", "")).strip() or "自定义演出卡片",
                "actions": actions, "fades": [],
                "pin": 1 if card.get("pin") else 0,
                "next": "",
            })
            if not self._cfg_saved("新建卡片"):
                return
            log("SHOW 新建卡片: %s (%d 个动作%s)"
                % (SHOW_CARDS[-1]["name"], len(actions),
                   "，丢弃 %d 个: %s" % (len(dropped), "; ".join(dropped)) if dropped else ""))
            self._send(200, "application/json", json.dumps(
                {"ok": True, "id": SHOW_CARDS[-1]["id"],
                 "dropped": dropped}).encode())

        elif path == "/api/carddel":
            SHOW_CARDS[:] = [c for c in SHOW_CARDS if c["id"] != data.get("id")]
            for s in [x for x in list(SCHEDULES) if x["card_id"] == data.get("id")]:
                cancel_schedule(s["sid"])
            if not self._cfg_saved("删除卡片"):
                return
            log("SHOW 删除卡片: %s" % data.get("id"))
            self._send(200, "application/json", b'{"ok":true}')

        elif path == "/api/snapshot":
            act = data.get("action")
            if act == "save":
                try:
                    snap, saved = save_snapshot(data.get("name"), scope=data.get("scope"))
                except ValueError as e:
                    # 非法范围 (一个组/一个参数都不合法, 编号全越界...) → 明确拒。
                    # 曾经的写法是静默回落全量: "只想存 ch1-2" 变成存了 158 个值,
                    # 回归时整台被换掉 —— 比报难查得多。
                    self._send(400, "application/json", json.dumps(
                        {"ok": False, "error": str(e)}, ensure_ascii=False).encode("utf-8"))
                    return
                if snap["count"] <= 0:
                    # 空范围 = 一条永远不会生效的预设，属于"假成功"，直接拒
                    SNAPSHOTS[:] = [s for s in SNAPSHOTS if s["id"] != snap["id"]]
                    self._send(400, "application/json",
                               json.dumps({"ok": False, "error": "范围里没有可保存的参数"},
                                          ensure_ascii=False).encode("utf-8"))
                    return
                if not saved:
                    self._send(500, "application/json", json.dumps(
                        {"ok": False,
                         "error": "预设保存失败: 无法写入配置文件 (%s) — 预设只在内存中, "
                                  "重启后会丢失。请检查所在目录是否可写、磁盘是否已满。"
                                  % CONFIG_PATH[0]},
                        ensure_ascii=False).encode("utf-8"))
                    return
                self._send(200, "application/json",
                           json.dumps({"ok": True, "id": snap["id"],
                                       "count": snap["count"],
                                       "scopeText": preset_scope_text(snap["scope"])}).encode())
            elif act == "recall":
                try:
                    sid = int(data.get("id", 0))
                except (TypeError, ValueError):
                    self._send(400, "application/json",
                               b'{"ok":false,"error":"bad id"}')
                    return
                snap = recall_snapshot(sid, data.get("seconds", 3.0))
                if not snap:
                    self._send(404, "text/plain; charset=utf-8", b"no such snapshot")
                    return
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "delete":
                try:
                    did = int(data.get("id", 0))
                except (TypeError, ValueError):
                    self._send(400, "application/json",
                               b'{"ok":false,"error":"bad id"}')
                    return
                SNAPSHOTS[:] = [s for s in SNAPSHOTS if s["id"] != did]
                # ⚠️ 删除也必须落盘: 曾经只改内存 → 接口返 ok 但重启后预设复活,
                #    又是一次"假成功"。
                if not self._cfg_saved("删除预设"):
                    return
                self._send(200, "application/json", b'{"ok":true}')
            elif act == "stop":
                _recall_stop_current()
                self._send(200, "application/json", b'{"ok":true}')
            else:
                self._send(400, "text/plain; charset=utf-8", b"bad action")
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")
