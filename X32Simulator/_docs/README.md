# 协议原文存档

`x32sim/wingmap.py`（WING 通道处理参数表）与 `test_wing_params.py` 的**期望值全部来自
协议原文**，不是"看起来像"。这里存的是取数链路，不是给人读的文档。

| 文件 | 来源 | 是否入库 |
|---|---|---|
| `WingRemoteProtocols.md` | https://github.com/pmaillot/WingRemoteProtocols （官方《WING Remote Protocols》的 Markdown 版，GitBook 导出，946 KB） | ❌ 第三方文档，不随仓库分发。按需下载到本目录即可 |
| `parse_wing_doc.py` | 本项目 | ✅ 解析脚本 |
| `wing_raw_addrs.json` | 由上一行脚本生成 | ✅ 解析产物（ch 167 / auxin 109 / bus 112 / main 103 / mtx 103 条） |

## 重建地址原始表

```bash
cd X32Simulator/_docs
curl -L -o WingRemoteProtocols.md https://raw.githubusercontent.com/pmaillot/WingRemoteProtocols/main/WingRemoteProtocols.md
python parse_wing_doc.py            # → wing_raw_addrs.json
```

## 为什么需要解析而不是手抄

地址表是 HTML `<table>`，地址单元格里带脚注引用：

```html
<td><code>/ch/<i>N</i>/dyn/thr</code><a href="#fn6" class="footnote-ref">6</a></td>
```

不整体剥除脚注节点，`6` 会被吃进地址，得到 `/dyn/thr6` —— 一个真台上不存在的地址，
而且写出去**不会报错**（真台只是不理），排障时日志干净得像没事发生。
`parse_wing_doc.py` 里先用 `_FNREF` 正则把 `<a class="footnote-ref">…</a>` 整段删掉再取文本。

另外 `re.escape` **不保留 `%`**，所以带占位符的地址不能整串转正则，必须
`split("%d")` 分段转义再 join。
