#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带重试的 GitHub API 客户端 + Release 发布工具（绕开 shell/代理抖动）。

用法:
  python tools/gh_api.py whoami
  python tools/gh_api.py releases
  python tools/gh_api.py release <id>
  python tools/gh_api.py create <tag> <title> <body-file> <asset-file>...
"""
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = "glaypan/X32Remote-Protocol"
TOKEN = os.environ.get("GH_PAT", "")
if not TOKEN:
    out = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True, cwd=r"E:\AI\workbuddy\x32遥控").stdout
    for line in out.splitlines():
        if line.startswith("password="):
            TOKEN = line.split("=", 1)[1]

PROXIES = [
    None,                          # 直连
    "http://127.0.0.1:7897",       # gh.cmd 内置代理
    "http://127.0.0.1:9960",       # 环境变量代理
]


def _open(method, url, data=None, headers=None, timeout=60):
    """单次请求；成功返回 (status, body_bytes)。"""
    last = None
    for proxy in PROXIES:
        if proxy:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", "token " + TOKEN)
        req.add_header("User-Agent", "x32-release-tool")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()          # HTTP 错误是确定结果，直接返回
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
    raise RuntimeError("request failed on all routes: %s" % last)


def api(method, path, body=None, tries=10):
    """JSON API 调用，带重试。"""
    url = path if path.startswith("http") else "https://api.github.com" + path
    data = json.dumps(body).encode() if body is not None else None
    last = None
    for attempt in range(tries):
        try:
            status, raw = _open(method, url, data,
                                {"Accept": "application/vnd.github+json",
                                 "Content-Type": "application/json"})
            if not raw:
                raise IOError("empty body")
            return status, json.loads(raw.decode("utf-8"))
        except Exception as e:
            last = str(e)
        time.sleep(2 + attempt * 1.5)
    raise RuntimeError("API %s %s failed: %s" % (method, path, last))


def api_get(path):
    status, d = api("GET", path)
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError("GET %s -> %s" % (path, status))
    return d


def upload_asset(upload_url, filepath, tries=5):
    """上传单个附件，带重试。"""
    name = os.path.basename(filepath)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    url = upload_url.split("{")[0] + "?" + urllib.parse.urlencode({"name": name})
    raw = open(filepath, "rb").read()
    last = None
    for attempt in range(tries):
        try:
            status, body = _open("POST", url, raw,
                                 {"Content-Type": ctype}, timeout=600)
            if status in (200, 201):
                d = json.loads(body.decode("utf-8"))
                if d.get("state") == "uploaded":
                    return d
                last = "state=%s" % d.get("state")
            else:
                last = "HTTP %s: %s" % (status, body[:200])
        except Exception as e:
            last = str(e)
        time.sleep(3 + attempt * 2)
    raise RuntimeError("upload %s failed: %s" % (name, last))


def cmd_create(tag, title, body_file, files):
    body = open(body_file, encoding="utf-8").read()
    # 已存在就直接复用（幂等）
    rel = api_get("/repos/%s/releases/tags/%s" % (REPO, tag))
    if rel is None:
        status, rel = api("POST", "/repos/%s/releases" % REPO, {
            "tag_name": tag, "name": title, "body": body,
            "draft": False, "prerelease": False, "make_latest": "true",
        })
        if status not in (200, 201):
            raise RuntimeError("create release -> %s %s" % (status, rel))
        print("release created: id=%s" % rel["id"])
    else:
        print("release exists: id=%s" % rel["id"])
    # 补齐缺失附件
    have = {a["name"] for a in rel.get("assets", [])}
    for f in files:
        n = os.path.basename(f)
        if n in have:
            print("  skip (exists): %s" % n)
            continue
        d = upload_asset(rel["upload_url"], f)
        print("  uploaded: %-42s %10d bytes" % (d["name"], d["size"]))
    # 终态核对
    rel2 = api_get("/repos/%s/releases/%s" % (REPO, rel["id"]))
    print("final: tag=%s assets=%d" % (rel2["tag_name"], len(rel2["assets"])))
    for a in rel2["assets"]:
        print("  %-42s %10d" % (a["name"], a["size"]))
    return rel2


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "whoami"
    if cmd == "whoami":
        print(api_get("/user").get("login"))
    elif cmd == "releases":
        for d in api_get("/repos/%s/releases?per_page=10" % REPO):
            print("id=%s tag=%s draft=%s assets=%d" % (d["id"], d["tag_name"], d["draft"], len(d["assets"])))
    elif cmd == "release":
        rid = sys.argv[2]
        d = api_get("/repos/%s/releases/%s" % (REPO, rid))
        print("NOT FOUND" if d is None else
              "tag=%s draft=%s name=%r assets=%d" % (d["tag_name"], d["draft"], d["name"], len(d["assets"])))
        if d:
            for a in d["assets"]:
                print("  %-42s %10d %s" % (a["name"], a["size"], a["state"]))
    elif cmd == "create":
        tag, title, body_file = sys.argv[2], sys.argv[3], sys.argv[4]
        cmd_create(tag, title, body_file, sys.argv[5:])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
