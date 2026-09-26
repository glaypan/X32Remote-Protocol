#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""创建 GitHub Release 并上传附件（幂等：已存在则跳过创建，逐个补传缺失附件）。

用法:
    python tools/create_release.py --tag v1.1.0 --title "X32 Remote v1.1.0" \
        --body outputs/RELEASE_BODY.md \
        --assets outputs/X32Remote-v1.1.0-assets.zip outputs/X32Remote-v1.1.0-full.zip \
                 outputs/X32Remote-1.0.0.apk outputs/X32Remote-unsigned.ipa \
                 outputs/SHA256SUMS.txt
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


def _req(method, url, token, data=None, headers=None, ctype="application/json"):
    h = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "x32release",
    }
    if headers:
        h.update(headers)
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        h["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="glaypan/X32Remote")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--body", required=True, help="Release 正文的 markdown 文件")
    ap.add_argument("--assets", nargs="*", default=[])
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--prerelease", action="store_true")
    args = ap.parse_args()

    if not args.token:
        print("缺少 Token。请设置 GITHUB_TOKEN 或传 --token")
        return 2

    title = args.title or args.tag
    body = open(args.body, "r", encoding="utf-8").read()

    # 1) 查是否已有该 tag 的 release
    st, rel = _req("GET", f"{API}/repos/{args.repo}/releases/tags/{args.tag}", args.token)
    if st == 200:
        print(f"Release {args.tag} 已存在 (id={rel['id']})，跳过创建")
    else:
        print(f"创建 Release {args.tag} …")
        st, rel = _req(
            "POST",
            f"{API}/repos/{args.repo}/releases",
            args.token,
            {
                "tag_name": args.tag,
                "name": title,
                "body": body,
                "draft": args.draft,
                "prerelease": args.prerelease,
            },
        )
        if st not in (200, 201):
            print(f"  ✗ 创建失败 HTTP {st}: {rel}")
            return 1
        print(f"  ✓ 已创建 id={rel['id']}")
        print(f"    页面: {rel['html_url']}")

    upload_url = rel["upload_url"].split("{")[0]
    existing = {a["name"]: a for a in rel.get("assets", [])}

    # 2) 逐个上传（已存在同名则先删再传，保证内容是最新的）
    for path in args.assets:
        if not os.path.isfile(path):
            print(f"  ! 跳过（文件不存在）: {path}")
            continue
        name = os.path.basename(path)
        size = os.path.getsize(path)
        if name in existing:
            aid = existing[name]["id"]
            print(f"  - {name} 已存在，删除旧附件后重传")
            dst, _ = _req("DELETE", f"{API}/repos/{args.repo}/releases/assets/{aid}", args.token)
            if dst not in (204, 200):
                print(f"    ! 删除失败 HTTP {dst}，尝试直接上传（可能报重名）")
        data = open(path, "rb").read()
        url = f"{upload_url}?name={name}"
        st, res = _req("POST", url, args.token, data, ctype="application/octet-stream")
        if st in (200, 201):
            print(f"  ✓ {name} ({size/1024/1024:.2f} MB) 上传成功")
        else:
            print(f"  ✗ {name} 上传失败 HTTP {st}: {res}")

    # 3) 复查最终附件列表
    st, rel = _req("GET", f"{API}/repos/{args.repo}/releases/tags/{args.tag}", args.token)
    print("\n最终附件:")
    for a in rel.get("assets", []):
        print(f"  - {a['name']}  {a['size']/1024/1024:.2f} MB  {a['browser_download_url']}")
    print(f"\nRelease 页面: {rel['html_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
