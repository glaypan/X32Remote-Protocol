#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 运行查询 / 等待 / 下载产物（绕开 gh CLI 的引号与代理坑）。

本机没有可用的 gh（PortableGit 的 `gh.cmd` 包装器会把含 `|`/引号的参数
二次解析截断），所以 CI 这一段直接用 REST API，令牌复用 gh_api.py 的取法
（`GH_PAT` → git credential helper）。

用法:
    python tools/ci_runs.py list glaypan/X32Remote
    python tools/ci_runs.py wait glaypan/X32Remote            # 等到最新一次结束
    python tools/ci_runs.py wait glaypan/X32Remote --sha 9a550b1
    python tools/ci_runs.py download glaypan/X32Remote <run_id> outputs/ci
"""
import argparse
import json
import os
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gh_api import _open, api_get                      # noqa: E402

OK = ("success",)
BAD = ("failure", "cancelled", "timed_out", "action_required")


def runs(repo, limit=10):
    d = api_get("/repos/%s/actions/runs?per_page=%d" % (repo, limit))
    return (d or {}).get("workflow_runs", [])


def pick(repo, sha=None):
    rs = runs(repo)
    if sha:
        rs = [r for r in rs if r.get("head_sha", "").startswith(sha)]
    return rs[0] if rs else None


def cmd_list(repo):
    for r in runs(repo):
        print("%-11s %-22s %-9s %-9s %s" % (
            str(r["id"]), r.get("name", "")[:22],
            (r.get("head_sha") or "")[:9], r.get("status"), r.get("conclusion")))
    return 0


def cmd_wait(repo, sha=None, timeout=3600, interval=20):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = pick(repo, sha)
        if r:
            st, cc = r.get("status"), r.get("conclusion")
            if (st, cc) != last:
                print("[%4ds] %s run %s status=%s conclusion=%s"
                      % (time.time() - t0, r.get("name"), r["id"], st, cc), flush=True)
                last = (st, cc)
            if st == "completed":
                print("run id =", r["id"])
                return 0 if cc in OK else 1
        time.sleep(interval)
    print("等待超时")
    return 2


def fetch_blob(url, timeout=600):
    """取 actions 产物 / 日志这类"302 到 Azure SAS"的地址。

    ⚠️ 不能直接带 Authorization 跟跳转：SAS 链接自带签名，
    多带一个 token 头会让 Azure 报 403/404（本次踩过）。
    所以先拿 Location，再**不带任何头**去取。
    """
    import urllib.error
    import urllib.request
    from gh_api import PROXIES, TOKEN

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    last = None
    for proxy in PROXIES:
        h = {"http": proxy, "https": proxy} if proxy else {}
        try:
            op = urllib.request.build_opener(urllib.request.ProxyHandler(h), NoRedirect)
            req = urllib.request.Request(url, headers={
                "Authorization": "token " + TOKEN,
                "Accept": "application/vnd.github+json"})
            try:
                with op.open(req, timeout=60) as r:
                    return r.status, r.read()          # 没跳转，直接给了内容
            except urllib.error.HTTPError as e:
                if e.code not in (301, 302, 303, 307, 308):
                    return e.code, e.read()
                loc = e.headers.get("Location")
            if not loc:
                return 0, b""
            op2 = urllib.request.build_opener(urllib.request.ProxyHandler(h))
            with op2.open(urllib.request.Request(loc), timeout=timeout) as r2:
                return 200, r2.read()
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
    raise RuntimeError("blob fetch failed: %s" % last)


def cmd_download(repo, run_id, dest):
    d = api_get("/repos/%s/actions/runs/%s/artifacts" % (repo, run_id)) or {}
    arts = d.get("artifacts", [])
    if not arts:
        print("该 run 没有产物")
        return 1
    os.makedirs(dest, exist_ok=True)
    for a in arts:
        url = a["archive_download_url"]
        status, raw = fetch_blob(url)
        if status != 200 or not raw:
            print("下载失败 %s -> HTTP %s" % (a["name"], status))
            continue
        zp = os.path.join(dest, a["name"] + ".zip")
        with open(zp, "wb") as f:
            f.write(raw)
        print("下载 %-32s %6.1f MB" % (a["name"], len(raw) / 1048576))
        # 解到同名目录（里面通常又是一个 zip）
        out = os.path.join(dest, a["name"])
        os.makedirs(out, exist_ok=True)
        with zipfile.ZipFile(zp) as zf:
            zf.extractall(out)
        for fn in os.listdir(out):
            p = os.path.join(out, fn)
            if os.path.isfile(p):
                print("   -> %s  %.1f MB" % (p, os.path.getsize(p) / 1048576))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["list", "wait", "download"])
    ap.add_argument("repo")
    ap.add_argument("extra", nargs="?")
    ap.add_argument("extra2", nargs="?")
    ap.add_argument("--sha", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    a = ap.parse_args()
    if a.cmd == "list":
        return cmd_list(a.repo)
    if a.cmd == "wait":
        return cmd_wait(a.repo, a.sha, a.timeout)
    return cmd_download(a.repo, a.extra, a.extra2 or "outputs/ci")


if __name__ == "__main__":
    sys.exit(main())
