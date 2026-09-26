#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""X32 Remote v1.1.0 一键发布。

推送代码与标签 → 等云端编译 → 下载新 IPA → 重新打包 → 创建 GitHub Release
并上传附件。全程走 api.github.com（github.com 网页在本机网络下不可达）。

用法:
  export GITHUB_TOKEN=ghp_xxxx      # 需要 repo + workflow 两个 scope
  python tools/publish_release.py --dry-run     # 先看要做什么
  python tools/publish_release.py               # 正式发布

  --skip-build    跳过云端编译等待（复用 outputs/ 已有 IPA）
  --only-release  只创建 Release 并上传附件（代码与标签已推过）

⚠️ 为什么要强制重新编译：
   outputs/X32Remote-unsigned.ipa 是 462af1c 编译的，不含本版核心功能
   （桥接模式 + 时间轴调度器）。直接拿它发布，用户装到的是旧版本。
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent          # E:/AI/workbuddy/x32遥控
IOS = BASE / "X32Remote-ios"
IOS_TOOLS = IOS / "tools"
OUT = BASE / "outputs"

TAG = "v1.1.0"
RELEASE_NAME = "X32 Remote v1.1.0"
REPO_NAME = "X32Remote"

API = "https://api.github.com"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PY = sys.executable


# ---------------------------------------------------------------- 基础工具

def say(msg=""):
    print(msg, flush=True)


def step(n, total, title):
    say()
    say("=" * 64)
    say("  [%d/%d] %s" % (n, total, title))
    say("=" * 64)


def die(msg, code=1):
    say()
    say("✗ " + msg)
    sys.exit(code)


def git(*args, cwd=IOS):
    r = subprocess.run(["git"] + list(args), cwd=str(cwd),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def gh(method, path, token, payload=None):
    """GitHub REST 调用。返回 (状态码, 解析后的 body, 响应头)。"""
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "x32-release",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
    try:
        with OPENER.open(req, timeout=90) as r:
            raw = r.read().decode("utf-8")
            return r.status, (json.loads(raw) if raw else {}), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(raw)
        except Exception:
            body = {"raw": raw[:500]}
        return e.code, body, dict(e.headers)
    except Exception as e:
        return 0, {"error": repr(e)}, {}


def upload_asset(upload_url, token, filepath):
    """上传 Release 附件（走 uploads.github.com，非 api.github.com）。"""
    fp = pathlib.Path(filepath)
    url = upload_url.split("{")[0] + "?name=" + urllib.parse.quote(fp.name)
    data = fp.read_bytes()
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": "Bearer " + token,
        "Content-Type": "application/octet-stream",
        "Content-Length": str(len(data)),
        "User-Agent": "x32-release",
    })
    try:
        with OPENER.open(req, timeout=300) as r:
            return True, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return False, e.read().decode("utf-8", "replace")[:400]
    except Exception as e:
        return False, repr(e)


def call_sibling(script, args, token):
    """调用 X32Remote-ios/tools 下的兄弟脚本，token 走环境变量不进命令行。"""
    env = dict(os.environ)
    env["GITHUB_TOKEN"] = token
    r = subprocess.run([PY, str(IOS_TOOLS / script)] + args,
                       cwd=str(IOS_TOOLS), env=env)
    return r.returncode


def local_sha():
    return git("rev-parse", "HEAD")[1]


# ---------------------------------------------------------------- 各步骤

def check_token(token, dry):
    """校验身份与 scope。缺 workflow 就别推了 —— 会被 GitHub 直接拒。"""
    st, body, headers = gh("GET", "/user", token)
    if st != 200:
        die("Token 校验失败 (HTTP %s): %s" % (st, str(body)[:300]))
    owner = body["login"]

    scopes = ""
    # 经典 PAT 在 X-OAuth-Scopes 头里返回；细粒度 PAT 不返回该头
    for k, v in headers.items():
        if k.lower() == "x-oauth-scopes":
            scopes = v
    say("GitHub 身份: %s" % owner)
    if scopes:
        have = {s.strip() for s in scopes.split(",") if s.strip()}
        say("Token scope: %s" % ", ".join(sorted(have)))
        need = {"repo", "workflow"}
        lack = need - have
        if lack:
            die("Token 缺少 scope: %s\n"
                "  workflow —— 仓库里有 .github/workflows/，缺了推送会被直接拒绝\n"
                "  repo     —— 推送代码与创建 Release 都要\n"
                "经典 Token 补勾后值不变，无需重新生成。" % ", ".join(sorted(lack)))
        if "delete_repo" in have:
            say("⚠️  警告：该 Token 带 delete_repo 权限（能删光所有仓库），发布流程用不到。")
            say("    建议发布完成后到 GitHub 撤销重生成。")
    else:
        say("Token scope: （细粒度 Token 不回该头，跳过预检）")
        say("  请自行确认具备：Contents 读写、Workflows 读写、Administration 读写")
    return owner


def ensure_clean(dry):
    """确认工作树干净、标签就位。"""
    st, out, err = git("status", "--porcelain")
    if out:
        say("⚠️  工作树有未提交改动：")
        for line in out.splitlines()[:10]:
            say("    " + line)
        if not dry:
            die("请先提交或 stash，避免发布的内容与预期不符。")
    else:
        say("工作树: 干净 ✓")

    st, out, _ = git("tag", "-l", TAG)
    if not out:
        die("本地没有标签 %s。请先执行：\n"
            "  git -C X32Remote-ios tag -a %s -m '...'" % (TAG, TAG))
    say("标签 %s: 存在 ✓" % TAG)

    # 待推送提交
    st, out, _ = git("log", "--oneline", "origin/main..HEAD")
    n = len(out.splitlines()) if out else 0
    say("待推送提交: %d 个" % n)
    for line in (out.splitlines()[:8] if out else []):
        say("    " + line)
    return n


def push_all(token, dry):
    """推送分支与标签。先走 push_to_github.py，再单独推标签。"""
    say("→ 推送 main 分支…")
    if dry:
        say("    [dry-run] 跳过")
    else:
        rc = call_sibling("push_to_github.py",
                          ["--repo", REPO_NAME, "--token", token, "--public"], token)
        if rc != 0:
            die("分支推送失败（rc=%d）。若是 scope 问题，按上面提示补勾后重跑。" % rc)
        say("  分支推送完成 ✓")

    say("→ 推送标签 %s…" % TAG)
    if dry:
        say("    [dry-run] 跳过")
        return
    r = subprocess.run(
        ["git", "push", "origin", TAG],
        cwd=str(IOS),
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        say("  ⚠️ 标签推送失败：%s" % (r.stderr or "").strip()[:300])
        say("     若标签已存在（重跑场景）可忽略，稍后会继续建 Release。")
    else:
        say("  标签推送完成 ✓")


def build_and_fetch(token, owner, skip_build, dry):
    """等云端编译并按提交 SHA 精确下载新 IPA。"""
    if skip_build:
        say("→ 已跳过云端编译（--skip-build）")
        say("  ⚠️ 请确认 outputs/X32Remote-unsigned.ipa 确实是新编译的，")
        say("     旧包不含桥接模式与时间轴调度器！")
        return

    sha = local_sha()
    say("→ 等待提交 %s 对应的云端编译…" % sha[:8])
    say("  （首次约 4~6 分钟，脚本会轮询到结束）")
    if dry:
        say("    [dry-run] 跳过")
        return

    rc = call_sibling("fetch_ipa.py",
                      ["--repo", "%s/%s" % (owner, REPO_NAME), "--token", token,
                       "--wait", "--expect-sha", sha], token)
    if rc != 0:
        die("云端编译或下载失败。可先用下面这条看状态：\n"
            "  python X32Remote-ios/tools/fetch_ipa.py --repo %s/%s "
            "--status --token $GITHUB_TOKEN" % (owner, REPO_NAME))
    say("新 IPA 已下载 ✓")


def repack(dry):
    """用新 IPA 重新打包，否则 zip 里还是旧产物。"""
    say("→ 重新打包发布包…")
    if dry:
        say("    [dry-run] 跳过")
        return
    r = subprocess.run([PY, str(BASE / "tools" / "make_release.py")],
                       cwd=str(BASE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    say((r.stdout or "").strip())
    if r.returncode != 0:
        die("打包失败：%s" % (r.stderr or "").strip()[:400])
    # 校验和
    r2 = subprocess.run(["sha256sum",
                         "X32Remote-v1.1.0-full.zip", "X32Remote-v1.1.0-assets.zip",
                         "X32Remote-1.0.0.apk", "X32Remote-unsigned.ipa"],
                        cwd=str(OUT), capture_output=True, text=True)
    if r2.returncode == 0:
        (OUT / "SHA256SUMS.txt").write_text(r2.stdout, encoding="utf-8")
        say("  校验和已更新 ✓")


def create_release(token, owner, dry):
    """创建 Release（存在则复用）并上传附件。"""
    full = "%s/%s" % (owner, REPO_NAME)
    body_path = OUT / "RELEASE_BODY.md"
    if not body_path.exists():
        die("找不到发布正文 %s" % body_path)
    body = body_path.read_text(encoding="utf-8")

    assets = [
        OUT / "X32Remote-v1.1.0-assets.zip",
        OUT / "X32Remote-v1.1.0-full.zip",
        OUT / "X32Remote-1.0.0.apk",
        OUT / "X32Remote-unsigned.ipa",
        OUT / "SHA256SUMS.txt",
    ]
    for a in assets:
        if not a.exists():
            die("缺少附件 %s，请先跑 tools/make_release.py" % a)

    # 已存在就直接复用，避免重跑时报 422
    st, rel, _ = gh("GET", "/repos/%s/releases/tags/%s" % (full, TAG), token)
    if st == 200:
        say("→ Release %s 已存在，复用（id=%s）" % (TAG, rel.get("id")))
    else:
        say("→ 创建 Release %s…" % TAG)
        payload = {
            "tag_name": TAG,
            "name": RELEASE_NAME,
            "body": body,
            "draft": False,
            "prerelease": False,
        }
        if dry:
            say("    [dry-run] 不实际创建")
            return None
        st, rel, _ = gh("POST", "/repos/%s/releases" % full, token, payload)
        if st not in (200, 201):
            die("创建 Release 失败 (HTTP %s): %s" % (st, str(rel)[:400]))
        say("  创建成功 ✓")

    if dry:
        say("    [dry-run] 不实际上传")
        return rel

    # 上传附件前先看哪些已存在，已存在的跳过（重跑友好）
    st, existing, _ = gh("GET", "/repos/%s/releases/%s/assets" % (full, rel["id"]), token)
    have = {a["name"] for a in existing} if st == 200 and isinstance(existing, list) else set()

    upload_url = rel.get("upload_url")
    if not upload_url:
        die("Release 响应里没有 upload_url")

    for a in assets:
        if a.name in have:
            say("  · %-34s 已存在，跳过" % a.name)
            continue
        mb = a.stat().st_size / 1048576
        say("  · %-34s 上传中 (%.1f MB)…" % (a.name, mb))
        ok, res = upload_asset(upload_url, token, a)
        if ok:
            say("    ✓ 完成")
        else:
            say("    ✗ 失败：%s" % str(res)[:300])
    return rel


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="X32 Remote v1.1.0 一键发布")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""),
                    help="GitHub PAT（默认读 GITHUB_TOKEN）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作")
    ap.add_argument("--skip-build", action="store_true", help="跳过云端编译等待")
    ap.add_argument("--only-release", action="store_true", help="只建 Release 并上传附件")
    args = ap.parse_args()

    if not args.token:
        die("缺少 Token。请先：\n"
            "  export GITHUB_TOKEN=ghp_xxxxxxxx\n"
            "（GitHub → Settings → Developer settings → Personal access tokens，\n"
            " 经典 Token 需同时勾选 repo + workflow）")

    say("X32 Remote %s 发布" % TAG)
    if args.dry_run:
        say("模式: DRY-RUN（不做任何实际改动）")

    owner = check_token(args.token, args.dry_run)

    say()
    say("-" * 64)
    ensure_clean(args.dry_run)

    total = 1 if args.only_release else 4
    n = 0

    if not args.only_release:
        n += 1
        step(n, total, "推送代码与标签")
        push_all(args.token, args.dry_run)

        n += 1
        step(n, total, "云端编译并下载新 IPA")
        build_and_fetch(args.token, owner, args.skip_build, args.dry_run)

        n += 1
        step(n, total, "重新打包发布包")
        repack(args.dry_run)

    n += 1
    step(n, total, "创建 Release 并上传附件")
    rel = create_release(args.token, owner, args.dry_run)

    say()
    say("=" * 64)
    if args.dry_run:
        say("  DRY-RUN 结束 —— 未做任何改动")
        say("  确认无误后去掉 --dry-run 正式执行")
    else:
        say("  发布完成 🎉")
        if rel:
            say("  地址: %s" % rel.get("html_url", "(见 GitHub Releases)"))
        say()
        say("  附件:")
        for f in ["X32Remote-v1.1.0-assets.zip", "X32Remote-v1.1.0-full.zip",
                  "X32Remote-1.0.0.apk", "X32Remote-unsigned.ipa", "SHA256SUMS.txt"]:
            p = OUT / f
            if p.exists():
                say("    %-34s %.2f MB" % (f, p.stat().st_size / 1048576))
    say("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
