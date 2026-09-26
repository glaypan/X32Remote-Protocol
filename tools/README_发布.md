# 发布脚本 · X32 Remote v1.1.0

`tools/publish_release.py` 一条命令完成：推送代码与标签 → 等云端编译 → 下载新 IPA → 创建 GitHub Release 并上传附件。

## 前置：需要 GitHub Token

Token 必须同时具备这两个 scope：

| scope | 用途 |
|---|---|
| `repo` | 推送代码、创建 Release |
| `workflow` | 推送含 `.github/workflows/` 的提交（缺了会被直接拒绝） |

> ⚠️ 生成 Token 时**不要**勾 `delete_repo` —— 那个权限能删光你所有仓库，发布流程完全用不到。
> 建议用完即撤销，或改用有效期 7 天的 Token。

## 用法

```bash
cd "E:/AI/workbuddy/x32遥控"

# 令牌走环境变量，不落命令行、不进进程列表
export GITHUB_TOKEN=ghp_xxxxxxxx

# 第一步：只看现状，不做任何改动
python tools/publish_release.py --dry-run

# 第二步：完整发布（推送 + 编译 + 下载 + 建 Release + 上传附件）
python tools/publish_release.py
```

## 分步执行

```bash
--dry-run      只打印将要执行的动作与当前状态
--skip-build   跳过云端编译等待（复用 outputs/ 里已有的 IPA）
--only-release 只创建 Release 并上传附件（代码与标签已推过时用）
```

## 它会做的事

1. **推送代码**：`main` 上 5 个待推提交（`462af1c..dd2d290`）
2. **推送标签**：`v1.1.0`
3. **等云端编译**：按提交 SHA 精确等待那次运行（不是拿最近一次，避免拿到旧结果）
4. **下载新 IPA**：解包到 `outputs/X32Remote-unsigned.ipa`
5. **重新打包**：调用 `tools/make_release.py`，让 zip 里是**新编译的** IPA
6. **创建 Release**：标签 `v1.1.0`，正文取自 `outputs/RELEASE_BODY.md`
7. **上传附件**：`X32Remote-v1.1.0-assets.zip`、`X32Remote-v1.1.0-full.zip`、`X32Remote-1.0.0.apk`、`X32Remote-unsigned.ipa`、`SHA256SUMS.txt`

## ⚠️ 为什么必须先重新编译

`outputs/X32Remote-unsigned.ipa` 是 **`462af1c`（9 月 25 日 22:27）** 编译的，
**不含**桥接模式与时间轴调度器 —— 那是本版的核心功能。

直接把它当作 v1.1.0 发布，用户装上的将是没有新功能的旧版本。
所以本脚本强制走「推送 → 编译 → 下载 → 再打包」的顺序，
`--skip-build` 仅供 IPA 已确认是新版时复用。

## 校验产物

```bash
cd outputs
sha256sum -c SHA256SUMS.txt
```

## 相关文档

- 发布说明（Release 正文）：`outputs/RELEASE_BODY.md`
- 打包脚本：`tools/make_release.py`
- 云端编译链路：`X32Remote-ios/tools/ios_build.py`
- iOS 安装流程：`iOS-真机直装指南.md`
